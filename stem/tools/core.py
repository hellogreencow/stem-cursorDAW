"""Core tools: session read/write, MIDI, theory-backed music intelligence.

The model picks intent (key, style, mood); deterministic theory code picks
the actual pitches. The LLM never free-hands raw note numbers for harmonic
material unless explicitly placing user-specified notes.
"""
from typing import List, Optional
from pydantic import BaseModel, Field

from .registry import ToolRegistry
from ..bridge.base import MidiNote
from ..theory import (
    Scale, ScaleType, Chord, ChordType, ChordProgression,
    DrumPattern, PatternLibrary,
)

registry = ToolRegistry()

NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
FLAT_EQUIV = {'DB': 'C#', 'EB': 'D#', 'GB': 'F#', 'AB': 'G#', 'BB': 'A#'}


def _root_index(root: str) -> int:
    r = root.strip().upper().replace('♯', '#').replace('♭', 'B')
    r = FLAT_EQUIV.get(r, r)
    if r not in NOTE_NAMES:
        raise ValueError(f"unknown root note: {root}")
    return NOTE_NAMES.index(r)


# ============ READ ============

class Empty(BaseModel):
    pass


@registry.register("get_session_overview",
                   "Read the current DAW session: tempo, tracks, markers, playhead.",
                   Empty)
def get_session_overview(args, ctx):
    s = ctx.bridge.get_session_overview()
    out = {
        "name": s.name, "tempo": s.tempo, "meter": s.meter,
        "playhead_seconds": s.playhead_seconds,
        "tracks": [{"track_id": t.track_id, "name": t.name, "kind": t.kind,
                    "muted": t.muted, "gain_db": t.gain_db}
                   for t in s.tracks],
        "markers": s.markers,
    }
    untrusted = getattr(s, "untrusted_fields", None) or []
    if untrusted:
        out["untrusted_fields"] = list(untrusted)
    return out


class TrackRef(BaseModel):
    track_id: str = Field(description="Track id from get_session_overview")


@registry.register("get_midi_notes",
                   "Read all MIDI notes on a track.", TrackRef)
def get_midi_notes(args, ctx):
    notes = ctx.bridge.get_midi_notes(args.track_id)
    return {"notes": [{"pitch": n.pitch, "start_beat": n.start_beat,
                       "length_beats": n.length_beats, "velocity": n.velocity}
                      for n in notes]}


# ============ WRITE ============

class CreateTrack(BaseModel):
    name: str = Field(description="Track name, e.g. 'Chords' or 'Drums'")
    instrument_id: Optional[str] = Field(
        default=None,
        description="Installed instrument id from list_instruments. Choose one "
                    "that fits the requested role; omit only for the GM fallback.")
    preset: Optional[str] = Field(
        default=None,
        description="Optional preset name advertised by list_instruments")


class InstrumentSearch(BaseModel):
    query: Optional[str] = Field(
        default=None,
        description="Optional case-insensitive filter such as piano, bass, "
                    "drums, strings, synth, or a plugin maker")


@registry.register(
    "list_instruments",
    "List every instrument plugin currently installed and scanned by Ardour, "
    "including available presets. Use this before creating an instrument track "
    "so you can choose the best sound for the musical role.",
    InstrumentSearch)
def list_instruments(args, ctx):
    instruments = ctx.bridge.list_instruments()
    if args.query:
        q = args.query.casefold()
        instruments = [
            i for i in instruments
            if q in " ".join(str(i.get(k, "")) for k in
                             ("name", "creator", "category", "type")).casefold()
        ]
    return {"instruments": instruments, "count": len(instruments)}


@registry.register("create_midi_track",
                   "Create a new audible MIDI instrument track. Select an "
                   "installed instrument with list_instruments first. Returns "
                   "track_id and action_id (undoable).",
                   CreateTrack, mutates=True)
def create_midi_track(args, ctx):
    track_id, action_id = ctx.bridge.create_midi_track(
        args.name, args.instrument_id, args.preset)
    return {"track_id": track_id, "action_id": action_id,
            "instrument_id": args.instrument_id, "preset": args.preset}


class NoteSpec(BaseModel):
    pitch: int = Field(ge=0, le=127)
    start_beat: float = Field(ge=0)
    length_beats: float = Field(gt=0)
    velocity: int = Field(default=100, ge=1, le=127)


class InsertNotes(BaseModel):
    track_id: str
    notes: List[NoteSpec]
    start_beat: float = Field(default=0.0, ge=0,
                              description="Offset added to every note")


@registry.register("insert_midi_notes",
                   "Insert explicit MIDI notes on a track (undoable). For chords/"
                   "progressions/patterns prefer the theory tools instead.",
                   InsertNotes, mutates=True)
def insert_midi_notes(args, ctx):
    notes = [MidiNote(pitch=n.pitch, start_beat=n.start_beat,
                      length_beats=n.length_beats, velocity=n.velocity)
             for n in args.notes]
    action_id = ctx.bridge.insert_midi_notes(args.track_id, notes,
                                             args.start_beat)
    # verify
    after = ctx.bridge.get_midi_notes(args.track_id)
    return {"action_id": action_id, "inserted": len(notes),
            "track_note_count": len(after)}


class SetTempo(BaseModel):
    bpm: float = Field(gt=20, lt=400)


@registry.register("set_tempo", "Set session tempo in BPM (undoable).",
                   SetTempo, mutates=True)
def set_tempo(args, ctx):
    return {"action_id": ctx.bridge.set_tempo(args.bpm), "bpm": args.bpm}


class SetGain(BaseModel):
    track_id: str
    gain_db: float = Field(ge=-60, le=12)


@registry.register("set_track_gain", "Set track gain in dB (undoable).",
                   SetGain, mutates=True)
def set_track_gain(args, ctx):
    return {"action_id": ctx.bridge.set_track_gain(args.track_id, args.gain_db)}


class SetMute(BaseModel):
    track_id: str
    muted: bool


@registry.register("set_track_mute", "Mute or unmute a track (undoable).",
                   SetMute, mutates=True)
def set_track_mute(args, ctx):
    return {"action_id": ctx.bridge.set_track_mute(args.track_id, args.muted)}


class AddMarker(BaseModel):
    name: str
    position_seconds: float = Field(ge=0)


@registry.register("add_marker", "Add a location marker (undoable).",
                   AddMarker, mutates=True)
def add_marker(args, ctx):
    return {"action_id": ctx.bridge.add_marker(args.name, args.position_seconds)}


# ============ TRANSPORT ============

@registry.register("transport_play", "Start playback.", Empty)
def transport_play(args, ctx):
    ctx.bridge.transport_play()
    return {"playing": True}


@registry.register("transport_stop", "Stop playback.", Empty)
def transport_stop(args, ctx):
    ctx.bridge.transport_stop()
    return {"playing": False}


# ============ UNDO ============

class UndoArgs(BaseModel):
    action_id: Optional[str] = Field(
        default=None, description="Undo back to this action; None = last action")


@registry.register("undo", "Undo a previous action by action_id (or the last one).",
                   UndoArgs)
def undo(args, ctx):
    ok = ctx.bridge.undo(args.action_id)
    return {"undone": ok}


# ============ AUDIO HEALTH ============

@registry.register(
    "diagnose_audio",
    "Diagnose why playback may be silent: checks the audio engine, master bus "
    "routing to hardware, transport, and whether tracks have a working "
    "instrument. Call this FIRST whenever the user says they can't hear "
    "anything, then explain the most likely cause.",
    Empty)
def diagnose_audio(args, ctx):
    return ctx.bridge.diagnose_audio()


@registry.register(
    "make_tracks_audible",
    "Fix silent MIDI tracks so they produce sound on play. MIDI needs an "
    "instrument; a MIDI track with no synth (or a bare a-fluidsynth that has "
    "no soundfont loaded) is silent. This adds or replaces the synth with the "
    "built-in audible one. Use when the user generated notes but hears nothing.",
    Empty)
def make_tracks_audible(args, ctx):
    return ctx.bridge.fix_silent_instruments()


# ============ MUSIC INTELLIGENCE (deterministic theory) ============

class ProgressionArgs(BaseModel):
    track_id: str
    key: str = Field(description="Root note, e.g. 'C', 'F#', 'Bb'")
    progression: str = Field(
        description=f"One of: {', '.join(ChordProgression.COMMON_PROGRESSIONS.keys())}")
    octave: int = Field(default=4, ge=1, le=7)
    beats_per_chord: float = Field(default=4.0, gt=0)
    start_beat: float = Field(default=0.0, ge=0)
    velocity: int = Field(default=90, ge=1, le=127)


@registry.register(
    "insert_chord_progression",
    "Place a chord progression on a MIDI track using real music theory — "
    "correct voicings in key, no guessed notes. Undoable.",
    ProgressionArgs, mutates=True)
def insert_chord_progression(args, ctx):
    prog = ChordProgression.from_progression_name(args.progression,
                                                  _root_index(args.key))
    notes = []
    beat = args.start_beat
    chord_names = []
    for chord, _duration in prog.chords:
        chord.octave = args.octave
        chord_names.append(chord.get_name())
        for n in chord.get_notes():
            notes.append(MidiNote(pitch=n.midi_note, start_beat=beat,
                                  length_beats=args.beats_per_chord,
                                  velocity=args.velocity))
        beat += args.beats_per_chord
    action_id = ctx.bridge.insert_midi_notes(args.track_id, notes)
    after = ctx.bridge.get_midi_notes(args.track_id)
    return {"action_id": action_id, "chords": chord_names,
            "notes_inserted": len(notes), "track_note_count": len(after),
            "total_beats": beat - args.start_beat}


class ChordArgs(BaseModel):
    track_id: str
    root: str
    chord_type: str = Field(
        description=f"One of: {', '.join(c.value for c in ChordType)}")
    octave: int = Field(default=4, ge=1, le=7)
    start_beat: float = Field(default=0.0, ge=0)
    length_beats: float = Field(default=4.0, gt=0)
    velocity: int = Field(default=90, ge=1, le=127)


@registry.register("insert_chord",
                   "Place a single chord (correct voicing) on a track. Undoable.",
                   ChordArgs, mutates=True)
def insert_chord(args, ctx):
    chord = Chord(_root_index(args.root), ChordType(args.chord_type), args.octave)
    notes = [MidiNote(pitch=n.midi_note, start_beat=args.start_beat,
                      length_beats=args.length_beats, velocity=args.velocity)
             for n in chord.get_notes()]
    action_id = ctx.bridge.insert_midi_notes(args.track_id, notes)
    return {"action_id": action_id, "chord": chord.get_name(),
            "pitches": [n.pitch for n in notes]}


class ScaleQuery(BaseModel):
    root: str
    scale_type: str = Field(
        description=f"One of: {', '.join(s.value for s in ScaleType)}")
    octave: int = Field(default=4, ge=0, le=8)


@registry.register("get_scale_notes",
                   "Look up the notes of a scale (read-only theory reference).",
                   ScaleQuery)
def get_scale_notes(args, ctx):
    scale = Scale(_root_index(args.root), ScaleType(args.scale_type))
    notes = scale.get_notes(args.octave)
    return {"notes": [{"name": n.name, "pitch": n.midi_note} for n in notes]}


class DrumArgs(BaseModel):
    track_id: str
    style: str = Field(
        description=f"One of: {', '.join(DrumPattern.PATTERNS.keys())}")
    bars: int = Field(default=4, ge=1, le=64)
    start_beat: float = Field(default=0.0, ge=0)


@registry.register("insert_drum_pattern",
                   "Place a drum pattern (GM drum mapping) on a MIDI track. Undoable.",
                   DrumArgs, mutates=True)
def insert_drum_pattern(args, ctx):
    overview = ctx.bridge.get_session_overview()
    pattern = DrumPattern.from_preset(args.style, overview.tempo)
    theory_notes = pattern.to_midi_notes(bars=args.bars)
    sec_per_beat = 60.0 / overview.tempo
    notes = [MidiNote(pitch=n.midi_note,
                      start_beat=n.start_time / sec_per_beat,
                      length_beats=max(n.duration / sec_per_beat, 0.1),
                      velocity=n.velocity, channel=9)
             for n in theory_notes]
    action_id = ctx.bridge.insert_midi_notes(args.track_id, notes,
                                             args.start_beat)
    return {"action_id": action_id, "style": args.style,
            "notes_inserted": len(notes), "bars": args.bars}


class BasslineArgs(BaseModel):
    track_id: str
    key: str
    progression: str = Field(
        description=f"One of: {', '.join(ChordProgression.COMMON_PROGRESSIONS.keys())}")
    style: str = Field(default="pulse",
                       description="pulse | walking | syncopated | bounce | driving")
    octave: int = Field(default=2, ge=0, le=4)
    beats_per_chord: float = Field(default=4.0, gt=0)
    start_beat: float = Field(default=0.0, ge=0)


@registry.register("insert_bassline",
                   "Generate a bassline following a chord progression. Undoable.",
                   BasslineArgs, mutates=True)
def insert_bassline(args, ctx):
    prog = ChordProgression.from_progression_name(args.progression,
                                                  _root_index(args.key))
    notes = []
    beat = args.start_beat
    for chord, _duration in prog.chords:
        root_pitch = (args.octave + 1) * 12 + chord.root_note
        if args.style == "pulse":
            steps = [(i * 1.0, 0.9) for i in range(int(args.beats_per_chord))]
        elif args.style == "walking":
            steps = [(i * 1.0, 1.0) for i in range(int(args.beats_per_chord))]
        elif args.style == "syncopated":
            steps = [(0.0, 1.0), (1.5, 0.5), (2.5, 0.5), (3.0, 1.0)]
        elif args.style == "bounce":
            steps = [(0.0, 0.5), (0.5, 0.5), (2.0, 0.5), (2.5, 0.5)]
        else:  # driving
            steps = [(i * 0.5, 0.45) for i in range(int(args.beats_per_chord * 2))]
        for offset, length in steps:
            if offset < args.beats_per_chord:
                notes.append(MidiNote(pitch=root_pitch, start_beat=beat + offset,
                                      length_beats=length, velocity=100))
        beat += args.beats_per_chord
    action_id = ctx.bridge.insert_midi_notes(args.track_id, notes)
    return {"action_id": action_id, "notes_inserted": len(notes)}
