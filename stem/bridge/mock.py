"""In-memory Ardour session emulator.

Lets the whole agent stack (tools, validation, undo, CLI) run end-to-end
with no Ardour installed. Mirrors the semantics the real bridge must honor,
including per-action undo.
"""
import uuid
import copy
from typing import Optional

from .base import Bridge, MidiNote, TrackInfo, SessionOverview


class MockBridge(Bridge):
    def __init__(self, name: str = "mock-session", tempo: float = 120.0):
        self.name = name
        self.tempo = tempo
        self.meter = "4/4"
        self.sample_rate = 48000
        self.playhead = 0.0
        self.playing = False
        self.tracks: dict = {}          # track_id -> TrackInfo
        self.notes: dict = {}           # track_id -> list[MidiNote]
        self.audio: dict = {}           # track_id -> list[(path, pos)]
        self.markers: list = []
        self._undo_stack: list = []     # (action_id, snapshot)

    def connected(self) -> bool:
        return True

    # ---- snapshots for undo ----
    def _checkpoint(self) -> str:
        action_id = uuid.uuid4().hex[:8]
        state = copy.deepcopy({
            "tempo": self.tempo, "meter": self.meter,
            "tracks": self.tracks, "notes": self.notes,
            "audio": self.audio, "markers": self.markers,
        })
        self._undo_stack.append((action_id, state))
        return action_id

    def undo(self, action_id: Optional[str] = None) -> bool:
        if not self._undo_stack:
            return False
        if action_id is None:
            _, state = self._undo_stack.pop()
        else:
            idx = next((i for i, (a, _) in enumerate(self._undo_stack)
                        if a == action_id), None)
            if idx is None:
                return False
            _, state = self._undo_stack[idx]
            del self._undo_stack[idx:]
        self.tempo = state["tempo"]
        self.meter = state["meter"]
        self.tracks = state["tracks"]
        self.notes = state["notes"]
        self.audio = state["audio"]
        self.markers = state["markers"]
        return True

    # ---- read ----
    def get_session_overview(self) -> SessionOverview:
        return SessionOverview(
            name=self.name, tempo=self.tempo, meter=self.meter,
            sample_rate=self.sample_rate,
            tracks=list(self.tracks.values()),
            markers=list(self.markers),
            playhead_seconds=self.playhead,
        )

    def get_midi_notes(self, track_id: str) -> list:
        if track_id not in self.tracks:
            raise KeyError(f"no such track: {track_id}")
        return list(self.notes.get(track_id, []))

    # ---- write ----
    def list_instruments(self) -> list:
        return [
            {"id": "mock.piano", "name": "Studio Piano", "creator": "Stem",
             "category": "Piano", "type": "Mock", "presets": []},
            {"id": "mock.synth", "name": "Poly Synth", "creator": "Stem",
             "category": "Synth", "type": "Mock", "presets": []},
        ]

    def create_midi_track(self, name: str, instrument_id: Optional[str] = None,
                          preset: Optional[str] = None):
        action_id = self._checkpoint()
        track_id = f"trk_{uuid.uuid4().hex[:6]}"
        plugins = []
        if instrument_id:
            plugins.append({"id": instrument_id, "preset": preset})
        self.tracks[track_id] = TrackInfo(
            track_id=track_id, name=name, kind="midi", plugins=plugins)
        self.notes[track_id] = []
        return track_id, action_id

    def insert_midi_notes(self, track_id: str, notes: list,
                          start_beat: float = 0.0) -> str:
        if track_id not in self.tracks:
            raise KeyError(f"no such track: {track_id}")
        if self.tracks[track_id].kind != "midi":
            raise ValueError(f"track {track_id} is not a MIDI track")
        action_id = self._checkpoint()
        for n in notes:
            shifted = MidiNote(pitch=n.pitch, start_beat=n.start_beat + start_beat,
                               length_beats=n.length_beats, velocity=n.velocity,
                               channel=n.channel)
            self.notes[track_id].append(shifted)
        return action_id

    def set_tempo(self, bpm: float) -> str:
        action_id = self._checkpoint()
        self.tempo = bpm
        return action_id

    def set_track_gain(self, track_id: str, gain_db: float) -> str:
        if track_id not in self.tracks:
            raise KeyError(f"no such track: {track_id}")
        action_id = self._checkpoint()
        self.tracks[track_id].gain_db = gain_db
        return action_id

    def set_track_mute(self, track_id: str, muted: bool) -> str:
        if track_id not in self.tracks:
            raise KeyError(f"no such track: {track_id}")
        action_id = self._checkpoint()
        self.tracks[track_id].muted = muted
        return action_id

    def add_marker(self, name: str, position_seconds: float) -> str:
        action_id = self._checkpoint()
        self.markers.append({"name": name, "position": position_seconds})
        return action_id

    def import_audio(self, track_id: str, file_path: str,
                     position_seconds: float = 0.0) -> str:
        action_id = self._checkpoint()
        if not track_id:
            track_id = f"aud_{uuid.uuid4().hex[:6]}"
            self.tracks[track_id] = TrackInfo(
                track_id=track_id, name=file_path.rsplit("/", 1)[-1],
                kind="audio")
            self.audio[track_id] = []
        if track_id not in self.tracks:
            raise KeyError(f"no such track: {track_id}")
        self.audio.setdefault(track_id, []).append((file_path, position_seconds))
        return action_id

    # ---- transport ----
    def transport_play(self) -> None:
        self.playing = True

    def transport_stop(self) -> None:
        self.playing = False

    def locate(self, seconds: float) -> None:
        self.playhead = seconds

    def save_session(self) -> None:
        pass
