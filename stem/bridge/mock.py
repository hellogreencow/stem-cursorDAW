"""In-memory Ardour session emulator.

Lets the whole agent stack (tools, validation, undo, CLI) run end-to-end
with no Ardour installed. Mirrors the semantics the real bridge must honor,
including per-action undo.
"""
import uuid
import copy
from typing import Optional

from .base import Bridge, MidiNote, TrackInfo, SessionOverview

# Mock plugin catalog: instruments + one effect with controllable params.
MOCK_PLUGIN_CATALOG = [
    {"id": "mock.piano", "name": "Studio Piano", "creator": "Stem",
     "category": "Piano", "type": "Mock", "kind": "instrument",
     "presets": [],
     "params": [
         {"id": "level", "name": "Level", "min": 0.0, "max": 1.0, "default": 0.8},
     ]},
    {"id": "mock.synth", "name": "Poly Synth", "creator": "Stem",
     "category": "Synth", "type": "Mock", "kind": "instrument",
     "presets": [],
     "params": [
         {"id": "cutoff", "name": "Cutoff", "min": 0.0, "max": 1.0, "default": 0.5},
         {"id": "resonance", "name": "Resonance", "min": 0.0, "max": 1.0,
          "default": 0.1},
     ]},
    {"id": "mock.reverb", "name": "Hall Reverb", "creator": "Stem",
     "category": "Reverb", "type": "Mock", "kind": "effect",
     "presets": [],
     "params": [
         {"id": "mix", "name": "Mix", "min": 0.0, "max": 1.0, "default": 0.3},
         {"id": "decay", "name": "Decay", "min": 0.1, "max": 10.0, "default": 2.5},
     ]},
]


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
        self._catalog = {p["id"]: p for p in MOCK_PLUGIN_CATALOG}

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
            {k: v for k, v in p.items() if k != "params"}
            for p in MOCK_PLUGIN_CATALOG if p["kind"] == "instrument"
        ]

    def list_plugins(self, kind: Optional[str] = None) -> list:
        out = []
        for p in MOCK_PLUGIN_CATALOG:
            if kind and p["kind"] != kind:
                continue
            out.append({k: v for k, v in p.items() if k != "params"})
        return out

    def _default_param_values(self, plugin_id: str) -> dict:
        spec = self._catalog.get(plugin_id)
        if not spec:
            return {}
        return {p["id"]: p["default"] for p in spec.get("params", [])}

    def load_plugin(self, track_id: str, plugin_id: str,
                    position: Optional[int] = None) -> str:
        if track_id not in self.tracks:
            raise KeyError(f"no such track: {track_id}")
        if plugin_id not in self._catalog:
            raise ValueError(f"unknown plugin: {plugin_id}")
        action_id = self._checkpoint()
        instance = {
            "id": plugin_id,
            "preset": None,
            "params": self._default_param_values(plugin_id),
        }
        plugins = self.tracks[track_id].plugins
        if position is None or position < 0 or position >= len(plugins):
            plugins.append(instance)
        else:
            plugins.insert(position, instance)
        return action_id

    def get_plugin_params(self, track_id: str,
                          plugin_index: int = 0) -> dict:
        if track_id not in self.tracks:
            raise KeyError(f"no such track: {track_id}")
        plugins = self.tracks[track_id].plugins
        if plugin_index < 0 or plugin_index >= len(plugins):
            raise IndexError(f"no plugin at index {plugin_index}")
        inst = plugins[plugin_index]
        spec = self._catalog.get(inst["id"], {})
        values = inst.setdefault("params", self._default_param_values(inst["id"]))
        params = []
        for p in spec.get("params", []):
            params.append({
                "id": p["id"], "name": p["name"],
                "min": p["min"], "max": p["max"],
                "value": values.get(p["id"], p["default"]),
            })
        return {"track_id": track_id, "plugin_index": plugin_index,
                "plugin_id": inst["id"], "params": params}

    def set_plugin_param(self, track_id: str, param_id: str, value: float,
                         plugin_index: int = 0) -> str:
        info = self.get_plugin_params(track_id, plugin_index)
        match = next((p for p in info["params"] if p["id"] == param_id), None)
        if not match:
            raise ValueError(f"unknown param: {param_id}")
        clamped = max(match["min"], min(match["max"], float(value)))
        action_id = self._checkpoint()
        inst = self.tracks[track_id].plugins[plugin_index]
        inst.setdefault("params", {})[param_id] = clamped
        return action_id

    def create_midi_track(self, name: str, instrument_id: Optional[str] = None,
                          preset: Optional[str] = None):
        action_id = self._checkpoint()
        track_id = f"trk_{uuid.uuid4().hex[:6]}"
        plugins = []
        if instrument_id:
            plugins.append({
                "id": instrument_id, "preset": preset,
                "params": self._default_param_values(instrument_id),
            })
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
