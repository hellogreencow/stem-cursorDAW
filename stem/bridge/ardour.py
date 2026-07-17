"""ArdourBridge — talks to a live Ardour instance.

File-based JSON RPC talks to the stem_bridge.lua command server for session
edits, reads, and transport. Keeping transport on the same acknowledged channel
avoids silent OSC no-ops when Ardour's OSC control surface is disabled.

Undo strategy: every Lua mutation is wrapped in Ardour's
begin/commit_reversible_command, so undo() maps to Ardour's real undo stack.
action_ids are sequence markers; undo(action_id) replays Session:undo() the
right number of times.
"""
import json
import fcntl
import math
import subprocess
import time
import uuid
from pathlib import Path
from typing import Optional

from ..paths import ensure_stem_home, generated_dir
from .base import Bridge, MidiNote, TrackInfo, SessionOverview

# Tempo/sample_rate from Lua have historically returned inf / int64-max when
# the wrong Temporal API is used. Quarantine instead of trusting.
_TEMPO_MIN, _TEMPO_MAX = 20.0, 400.0
_RATE_MIN, _RATE_MAX = 8000, 384000


def _sanitize_tempo(raw) -> tuple:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 120.0, False
    if not math.isfinite(value) or not (_TEMPO_MIN <= value <= _TEMPO_MAX):
        return 120.0, False
    return value, True


def _sanitize_sample_rate(raw) -> tuple:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 48000, False
    if value < _RATE_MIN or value > _RATE_MAX:
        return 48000, False
    return value, True


GENERAL_MIDI_PROGRAMS = (
    "Acoustic Grand Piano", "Bright Acoustic Piano", "Electric Grand Piano",
    "Honky-tonk Piano", "Electric Piano 1", "Electric Piano 2",
    "Harpsichord", "Clavinet", "Celesta", "Glockenspiel", "Music Box",
    "Vibraphone", "Marimba", "Xylophone", "Tubular Bells", "Dulcimer",
    "Drawbar Organ", "Percussive Organ", "Rock Organ", "Church Organ",
    "Reed Organ", "Accordion", "Harmonica", "Tango Accordion",
    "Acoustic Guitar (nylon)", "Acoustic Guitar (steel)",
    "Electric Guitar (jazz)", "Electric Guitar (clean)",
    "Electric Guitar (muted)", "Overdriven Guitar", "Distortion Guitar",
    "Guitar Harmonics", "Acoustic Bass", "Electric Bass (finger)",
    "Electric Bass (pick)", "Fretless Bass", "Slap Bass 1", "Slap Bass 2",
    "Synth Bass 1", "Synth Bass 2", "Violin", "Viola", "Cello",
    "Contrabass", "Tremolo Strings", "Pizzicato Strings", "Orchestral Harp",
    "Timpani", "String Ensemble 1", "String Ensemble 2", "Synth Strings 1",
    "Synth Strings 2", "Choir Aahs", "Voice Oohs", "Synth Voice",
    "Orchestra Hit", "Trumpet", "Trombone", "Tuba", "Muted Trumpet",
    "French Horn", "Brass Section", "Synth Brass 1", "Synth Brass 2",
    "Soprano Sax", "Alto Sax", "Tenor Sax", "Baritone Sax", "Oboe",
    "English Horn", "Bassoon", "Clarinet", "Piccolo", "Flute", "Recorder",
    "Pan Flute", "Blown Bottle", "Shakuhachi", "Whistle", "Ocarina",
    "Lead 1 (square)", "Lead 2 (sawtooth)", "Lead 3 (calliope)",
    "Lead 4 (chiff)", "Lead 5 (charang)", "Lead 6 (voice)",
    "Lead 7 (fifths)", "Lead 8 (bass + lead)", "Pad 1 (new age)",
    "Pad 2 (warm)", "Pad 3 (polysynth)", "Pad 4 (choir)", "Pad 5 (bowed)",
    "Pad 6 (metallic)", "Pad 7 (halo)", "Pad 8 (sweep)", "FX 1 (rain)",
    "FX 2 (soundtrack)", "FX 3 (crystal)", "FX 4 (atmosphere)",
    "FX 5 (brightness)", "FX 6 (goblins)", "FX 7 (echoes)",
    "FX 8 (sci-fi)", "Sitar", "Banjo", "Shamisen", "Koto", "Kalimba",
    "Bag Pipe", "Fiddle", "Shanai", "Tinkle Bell", "Agogo", "Steel Drums",
    "Woodblock", "Taiko Drum", "Melodic Tom", "Synth Drum",
    "Reverse Cymbal", "Guitar Fret Noise", "Breath Noise", "Seashore",
    "Bird Tweet", "Telephone Ring", "Helicopter", "Applause", "Gunshot",
)


class ArdourBridge(Bridge):
    def __init__(self, osc_host: str = "127.0.0.1", osc_port: int = 3819,
                 rpc_timeout: float = 5.0):
        ensure_stem_home()
        self.rpc_timeout = rpc_timeout
        self._action_seq: list = []  # ordered action_ids for undo mapping

    # ---- RPC plumbing ----
    def _call(self, method: str, args: Optional[dict] = None) -> dict:
        # The transport is a single request/response mailbox. Lock it across
        # threads and processes so web refresh, chat jobs, and CLI diagnostics
        # cannot overwrite one another's request.json.
        # Paths resolved per call so STEM_HOME changes are honored.
        stem_dir = ensure_stem_home()
        req = stem_dir / "request.json"
        resp_path = stem_dir / "response.json"
        rpc_lock = stem_dir / "rpc.lock"
        with rpc_lock.open("a+") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                req_id = uuid.uuid4().hex[:8]
                try:
                    resp_path.unlink()
                except FileNotFoundError:
                    pass
                req.write_text(json.dumps({"id": req_id, "method": method,
                                           "args": args or {}}))
                deadline = time.time() + self.rpc_timeout
                while time.time() < deadline:
                    if resp_path.exists():
                        try:
                            resp = json.loads(resp_path.read_text())
                        except json.JSONDecodeError:
                            time.sleep(0.05)
                            continue
                        if resp.get("id") == req_id:
                            if "error" in resp:
                                raise RuntimeError(
                                    f"ardour bridge: {resp['error']}")
                            return resp.get("result", {})
                    time.sleep(0.05)
                raise TimeoutError(
                    f"no response from Ardour bridge for '{method}' — is the "
                    "Stem Agent Bridge Lua script running? "
                    "(Edit > Lua Scripts)")
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _record_action(self) -> str:
        action_id = uuid.uuid4().hex[:8]
        self._action_seq.append(action_id)
        return action_id

    def connected(self) -> bool:
        try:
            return bool(self._call("ping").get("pong"))
        except (TimeoutError, RuntimeError):
            return False

    # ---- read ----
    def get_session_overview(self) -> SessionOverview:
        r = self._call("get_session_overview")
        tracks = [TrackInfo(track_id=t["track_id"], name=t["name"],
                            kind=t.get("kind", "audio"),
                            muted=t.get("muted", False),
                            gain_db=t.get("gain_db", 0.0))
                  for t in r.get("tracks", [])]
        tempo, tempo_ok = _sanitize_tempo(r.get("tempo", 120.0))
        rate, rate_ok = _sanitize_sample_rate(r.get("sample_rate", 48000))
        untrusted = []
        if not tempo_ok:
            untrusted.append("tempo")
        if not rate_ok:
            untrusted.append("sample_rate")
        return SessionOverview(
            name=r.get("name", "?"), tempo=tempo,
            meter=r.get("meter", "4/4"),
            sample_rate=rate,
            tracks=tracks, markers=r.get("markers", []),
            playhead_seconds=r.get("playhead_seconds", 0.0),
            untrusted_fields=untrusted)

    def get_midi_notes(self, track_id: str) -> list:
        r = self._call("get_midi_notes", {"track_id": track_id})
        return [MidiNote(pitch=n["pitch"], start_beat=n["start_beat"],
                         length_beats=n["length_beats"],
                         velocity=n.get("velocity", 100))
                for n in r.get("notes", [])]

    # ---- write ----
    def list_instruments(self) -> list:
        instruments = self._call("list_instruments").get("instruments", [])
        has_gm = any(i.get("id") == "urn:ardour:a-fluidsynth"
                     for i in instruments)
        if has_gm:
            instruments.extend({
                "id": f"gm:{program}", "name": name,
                "creator": "General MIDI / ACE Fluid Synth",
                "category": "General MIDI", "type": "GM", "presets": [],
            } for program, name in enumerate(GENERAL_MIDI_PROGRAMS))
            instruments.append({
                "id": "gm:drums", "name": "General MIDI Drum Kit",
                "creator": "General MIDI / ACE Fluid Synth",
                "category": "Drums & Percussion", "type": "GM",
                "presets": [],
            })
        return instruments

    def create_midi_track(self, name: str, instrument_id: Optional[str] = None,
                          preset: Optional[str] = None):
        args = {"name": name}
        if instrument_id:
            args["instrument_id"] = instrument_id
        if preset:
            args["preset"] = preset
        r = self._call("create_midi_track", args)
        return r["track_id"], self._record_action()

    def insert_midi_notes(self, track_id: str, notes: list,
                          start_beat: float = 0.0) -> str:
        payload = [{"pitch": n.pitch, "start_beat": n.start_beat + start_beat,
                    "length_beats": n.length_beats, "velocity": n.velocity,
                    "channel": n.channel} for n in notes]
        self._call("insert_midi_notes", {"track_id": track_id, "notes": payload})
        return self._record_action()

    def set_tempo(self, bpm: float) -> str:
        self._call("set_tempo", {"bpm": bpm})
        return self._record_action()

    def set_track_gain(self, track_id: str, gain_db: float) -> str:
        self._call("set_track_gain", {"track_id": track_id, "gain_db": gain_db})
        return self._record_action()

    def set_track_mute(self, track_id: str, muted: bool) -> str:
        self._call("set_track_mute", {"track_id": track_id, "muted": muted})
        return self._record_action()

    def add_marker(self, name: str, position_seconds: float) -> str:
        self._call("add_marker", {"name": name,
                                  "position_seconds": position_seconds})
        return self._record_action()

    def import_audio(self, track_id: str, file_path: str,
                     position_seconds: float = 0.0) -> str:
        file_path = self._audio_for_session_rate(file_path)
        result = self._call("import_audio", {
            "track_id": track_id, "file_path": file_path,
            "position_seconds": position_seconds})
        imported_track = result.get("track_id")
        if not imported_track:
            raise RuntimeError("Ardour did not create an audio track")
        deadline = time.time() + self.rpc_timeout
        while time.time() < deadline:
            content = self._call("get_track_content",
                                 {"track_id": imported_track})
            regions = content.get("regions", [])
            if any(r.get("length_samples", 0) > 0 for r in regions):
                return self._record_action()
            time.sleep(0.1)
        raise RuntimeError(
            f"Ardour created '{imported_track}' but its audio region is empty")

    def _target_sample_rate(self) -> Optional[int]:
        try:
            rate = int(self._call("get_session_overview").get("sample_rate", 0))
            return rate if rate > 0 else None
        except Exception:
            return None

    def _audio_sample_rate(self, file_path: str) -> Optional[int]:
        try:
            p = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "a:0",
                 "-show_entries", "stream=sample_rate", "-of",
                 "default=noprint_wrappers=1:nokey=1", file_path],
                check=True, capture_output=True, text=True, timeout=30)
            return int(p.stdout.strip())
        except Exception:
            return None

    def _resample_audio(self, file_path: str, sample_rate: int) -> str:
        src = Path(file_path)
        out_dir = generated_dir()
        out = out_dir / f"{src.stem}_{sample_rate}hz_{uuid.uuid4().hex[:8]}.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
             "-ar", str(sample_rate), "-c:a", "pcm_s24le", str(out)],
            check=True, timeout=180)
        if not out.exists() or out.stat().st_size < 1000:
            raise RuntimeError("resampled audio file was not created")
        return str(out)

    def _audio_for_session_rate(self, file_path: str) -> str:
        target = self._target_sample_rate()
        source = self._audio_sample_rate(file_path)
        if not target or not source or target == source:
            return file_path
        return self._resample_audio(file_path, target)

    # ---- transport ----
    def transport_play(self) -> None:
        self._call("transport_play")

    def transport_stop(self) -> None:
        self._call("transport_stop")

    def locate(self, seconds: float) -> None:
        self._call("locate", {"seconds": seconds})

    # ---- undo ----
    def undo(self, action_id: Optional[str] = None) -> bool:
        if action_id is None:
            if not self._action_seq:
                return False
            self._action_seq.pop()
            self._call("undo")
            return True
        if action_id not in self._action_seq:
            return False
        # undo everything back to and including action_id
        idx = self._action_seq.index(action_id)
        n = len(self._action_seq) - idx
        for _ in range(n):
            self._call("undo")
        del self._action_seq[idx:]
        return True

    def save_session(self) -> None:
        self._call("save_session")

    # ---- audio health ----
    def diagnose_audio(self, track_id: Optional[str] = None) -> dict:
        args = {"track_id": track_id} if track_id else {}
        return self._call("diagnose_audio", args)

    def fix_silent_instruments(self) -> dict:
        return self._call("fix_silent_instruments", {})
