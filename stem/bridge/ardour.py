"""ArdourBridge — talks to a live Ardour instance.

Two channels:
- File-based JSON RPC to the stem_bridge.lua command server (session edits,
  reads). Simple, dependency-free on the Ardour side, fine at agent speeds.
- OSC (python-osc) for transport, which Ardour supports natively without
  any script installed (Preferences > Control Surfaces > Open Sound Control).

Undo strategy: every Lua mutation is wrapped in Ardour's
begin/commit_reversible_command, so undo() maps to Ardour's real undo stack.
action_ids are sequence markers; undo(action_id) replays Session:undo() the
right number of times.
"""
import json
import os
import time
import uuid
from pathlib import Path
from typing import Optional

from .base import Bridge, MidiNote, TrackInfo, SessionOverview

try:
    from pythonosc.udp_client import SimpleUDPClient
    HAVE_OSC = True
except ImportError:
    HAVE_OSC = False

STEM_DIR = Path.home() / ".stem"
REQ = STEM_DIR / "request.json"
RESP = STEM_DIR / "response.json"


class ArdourBridge(Bridge):
    def __init__(self, osc_host: str = "127.0.0.1", osc_port: int = 3819,
                 rpc_timeout: float = 5.0):
        STEM_DIR.mkdir(exist_ok=True)
        self.rpc_timeout = rpc_timeout
        self.osc = SimpleUDPClient(osc_host, osc_port) if HAVE_OSC else None
        self._action_seq: list = []  # ordered action_ids for undo mapping

    # ---- RPC plumbing ----
    def _call(self, method: str, args: Optional[dict] = None) -> dict:
        req_id = uuid.uuid4().hex[:8]
        REQ.write_text(json.dumps({"id": req_id, "method": method,
                                   "args": args or {}}))
        deadline = time.time() + self.rpc_timeout
        while time.time() < deadline:
            if RESP.exists():
                try:
                    resp = json.loads(RESP.read_text())
                except json.JSONDecodeError:
                    time.sleep(0.05)
                    continue
                if resp.get("id") == req_id:
                    if "error" in resp:
                        raise RuntimeError(f"ardour bridge: {resp['error']}")
                    return resp.get("result", {})
            time.sleep(0.05)
        raise TimeoutError(
            f"no response from Ardour bridge for '{method}' — is the "
            "Stem Agent Bridge Lua script running? (Edit > Lua Scripts)")

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
        return SessionOverview(
            name=r.get("name", "?"), tempo=r.get("tempo", 120.0),
            meter=r.get("meter", "4/4"),
            sample_rate=r.get("sample_rate", 48000),
            tracks=tracks, markers=r.get("markers", []),
            playhead_seconds=r.get("playhead_seconds", 0.0))

    def get_midi_notes(self, track_id: str) -> list:
        r = self._call("get_midi_notes", {"track_id": track_id})
        return [MidiNote(pitch=n["pitch"], start_beat=n["start_beat"],
                         length_beats=n["length_beats"],
                         velocity=n.get("velocity", 100))
                for n in r.get("notes", [])]

    # ---- write ----
    def create_midi_track(self, name: str):
        r = self._call("create_midi_track", {"name": name})
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
        self._call("import_audio", {"track_id": track_id,
                                    "file_path": file_path,
                                    "position_seconds": position_seconds})
        return self._record_action()

    # ---- transport (OSC when available — lower latency, no script needed) ----
    def transport_play(self) -> None:
        if self.osc:
            self.osc.send_message("/transport_play", 1)
        else:
            self._call("transport_play")

    def transport_stop(self) -> None:
        if self.osc:
            self.osc.send_message("/transport_stop", 1)
        else:
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
