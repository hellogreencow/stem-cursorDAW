"""Bridge protocol: everything the agent can do to a DAW session.

Implementations: MockBridge (in-memory, for tests/dev) and ArdourBridge
(OSC + Lua command server against a live Ardour instance). Tools depend
only on this interface, so swapping bridges requires zero tool changes.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MidiNote:
    pitch: int          # MIDI note number 0-127
    start_beat: float   # position in beats from region start
    length_beats: float
    velocity: int = 100
    channel: int = 0


@dataclass
class TrackInfo:
    track_id: str
    name: str
    kind: str           # "midi" | "audio"
    muted: bool = False
    soloed: bool = False
    gain_db: float = 0.0
    pan: float = 0.0
    plugins: list = field(default_factory=list)


@dataclass
class SessionOverview:
    name: str
    tempo: float
    meter: str
    sample_rate: int
    tracks: list            # list[TrackInfo]
    markers: list
    playhead_seconds: float
    snapshot_id: Optional[str] = None


class Bridge(ABC):
    """All mutations MUST be reversible: every mutating method returns an
    action_id that undo(action_id) can revert. No silent mutations."""

    @abstractmethod
    def connected(self) -> bool: ...

    # ---- read ----
    @abstractmethod
    def get_session_overview(self) -> SessionOverview: ...

    @abstractmethod
    def get_midi_notes(self, track_id: str) -> list: ...

    # ---- write (all return action_id) ----
    @abstractmethod
    def create_midi_track(self, name: str) -> tuple:
        """returns (track_id, action_id)"""

    @abstractmethod
    def insert_midi_notes(self, track_id: str, notes: list,
                          start_beat: float = 0.0) -> str: ...

    @abstractmethod
    def set_tempo(self, bpm: float) -> str: ...

    @abstractmethod
    def set_track_gain(self, track_id: str, gain_db: float) -> str: ...

    @abstractmethod
    def set_track_mute(self, track_id: str, muted: bool) -> str: ...

    @abstractmethod
    def add_marker(self, name: str, position_seconds: float) -> str: ...

    @abstractmethod
    def import_audio(self, track_id: str, file_path: str,
                     position_seconds: float = 0.0) -> str: ...

    # ---- transport ----
    @abstractmethod
    def transport_play(self) -> None: ...

    @abstractmethod
    def transport_stop(self) -> None: ...

    @abstractmethod
    def locate(self, seconds: float) -> None: ...

    # ---- undo ----
    @abstractmethod
    def undo(self, action_id: Optional[str] = None) -> bool:
        """Revert a specific action (or the last one if None)."""

    @abstractmethod
    def save_session(self) -> None: ...
