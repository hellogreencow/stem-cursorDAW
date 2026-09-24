"""Core tools: session read/write, MIDI, theory-backed music intelligence.

The model picks intent (key, style, mood); deterministic theory code picks
the actual pitches. The LLM never free-hands raw note numbers for harmonic
material unless explicitly placing user-specified notes.
"""
from typing import List, Optional
from pydantic import BaseModel, Field

from .registry import ToolRegistry
from .verification import diff, fingerprint, journal_for, verification_enabled
from ..bridge.base import MidiNote
from ..theory import (
    Scale, ScaleType, Chord, ChordType, ChordProgression,
    DrumPattern, PatternLibrary,
)
from ..theory import analysis

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
    return {
        "name": s.name, "tempo": s.tempo, "meter": s.meter,
        "playhead_seconds": s.playhead_seconds,
        "tracks": [{"track_id": t.track_id, "name": t.name, "kind": t.kind,
                    "muted": t.muted, "gain_db": t.gain_db}
                   for t in s.tracks],
        "markers": s.markers,
    }


class TrackRef(BaseModel):
    track_id: str = Field(description="Track id from get_session_overview")


@registry.register("get_midi_notes",
                   "Read all MIDI notes on a track.", TrackRef)
def get_midi_notes(args, ctx):
    notes = ctx.bridge.get_midi_notes(args.track_id)
    return {"notes": [{"pitch": n.pitch, "start_beat": n.start_beat,
                       "length_beats": n.length_beats, "velocity": n.velocity}
                      for n in notes]}


class MixerQuery(BaseModel):
    track_id: Optional[str] = Field(
        default=None, description="Limit to one track; omit for the whole mixer")


@registry.register(
    "get_mixer_state",
    "Read the mixer: level (gain in dB), pan, mute and solo state, and the "
    "plugin chain of every track. Use before any mix move so you know where "
    "the faders actually are instead of guessing.",
    MixerQuery)
def get_mixer_state(args, ctx):
    overview = ctx.bridge.get_session_overview()
    tracks = overview.tracks
    if args.track_id:
        tracks = [t for t in tracks if t.track_id == args.track_id]
        if not tracks:
            return {"error": f"no such track: {args.track_id}"}
    strips = [{
        "track_id": t.track_id,
        "name": t.name,
        "kind": t.kind,
        "gain_db": t.gain_db,
        "pan": t.pan,
        "muted": t.muted,
        "soloed": t.soloed,
        "plugins": list(t.plugins or []),
    } for t in tracks]
    return {
        "tracks": strips,
        "count": len(strips),
        "tempo": overview.tempo,
        "meter": overview.meter,
        # Honesty about the bridge's limits: the Bridge protocol carries no
        # sends/routing yet, so this tool reports what exists rather than
        # inventing a routing graph. PLAN.md lists both; see TOOLBOX_AUDIT.md.
        "not_reported": ["sends", "routing", "input/output connections",
                         "meter levels (live signal)"],
    }


@registry.register(
    "get_playhead",
    "Where the playhead is right now, in seconds AND in bars/beats. Use this "
    "when the user says 'here', 'at the playhead' or 'from where I am' so "
    "notes land where they are looking.",
    Empty)
def get_playhead(args, ctx):
    overview = ctx.bridge.get_session_overview()
    seconds = overview.playhead_seconds or 0.0
    tempo = overview.tempo or 120.0
    beats = seconds * tempo / 60.0
    beats_per_bar, note_value, meter_note = _parse_meter(overview.meter)
    bar = int(beats // beats_per_bar) + 1
    beat_in_bar = beats - (bar - 1) * beats_per_bar + 1
    return {
        "playhead_seconds": round(seconds, 6),
        "playhead_beats": round(beats, 6),
        "bar": bar,
        "beat_in_bar": round(beat_in_bar, 6),
        "tempo": tempo,
        "meter": overview.meter,
        "beats_per_bar": beats_per_bar,
        "note": meter_note,
    }


def _parse_meter(meter: str):
    """'4/4' -> (4.0, 4, note). Beats are quarter notes, which is what the
    note tools use; a meter on a different note value is reported, not
    silently reinterpreted."""
    try:
        numerator, denominator = str(meter).split("/")
        numerator, denominator = float(numerator), int(denominator)
    except Exception:
        return 4.0, 4, f"could not parse meter {meter!r}; assuming 4/4"
    if denominator == 4:
        return numerator, denominator, None
    # e.g. 6/8: quarter-note beats per bar = numerator * 4 / denominator
    return (numerator * 4.0 / denominator, denominator,
            f"{meter} expressed as {numerator * 4.0 / denominator:g} "
            "quarter-note beats per bar")


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


@registry.register(
    "undo",
    "Undo a previous action by action_id (or the last one) AND check it "
    "worked: Stem re-reads the session and reports whether it really came "
    "back to the state before that action. Trust the 'restored' field, not "
    "the fact that the call returned.",
    UndoArgs)
def undo(args, ctx):
    """Undo, then verify.

    The bridge's undo is a request, not a guarantee. On a live Ardour it maps
    to Editor:undo(1), which pops whatever Ardour last recorded — and a Stem
    mutation that never created an undo record (a fader move, a tempo write)
    is not on that stack at all, so the pop takes the *user's* previous edit
    instead. That failure is silent unless somebody looks, so this tool looks:
    it fingerprints the session before and after and compares the result with
    the fingerprint taken before the action being undone.
    """
    bridge = ctx.bridge
    journal = journal_for(bridge)
    entry = journal.get(args.action_id)
    watching = verification_enabled()
    scope = sorted(set((entry.before.get("notes", {}) if entry else {}))
                   | set((entry.after.get("notes", {}) if entry else {})))
    before_undo = fingerprint(bridge, scope) if watching else None

    ok = bridge.undo(args.action_id)
    result = {"undone": ok}
    if not ok:
        result["note"] = ("the bridge refused the undo — unknown action_id, "
                          "or nothing left on the undo stack")
        return result
    if not watching:
        return result

    after_undo = fingerprint(bridge, scope)
    moved = diff(before_undo, after_undo)
    result["changes"] = moved["details"]
    if not moved["changed"] and moved.get("checked"):
        result["restored"] = False
        result["warning"] = (
            "undo reported success but nothing in the session changed — the "
            "action was probably never on the DAW's undo stack")
        return result
    if entry is None:
        result["restored"] = None
        result["note"] = ("no fingerprint on file for that action, so Stem "
                          "cannot confirm what came back")
        return result

    drift = diff(entry.before, after_undo)
    result["restored"] = not drift["changed"]
    if drift["changed"]:
        result["unexpected"] = drift["details"]
        result["warning"] = (
            "the session did not come back to its state before that action — "
            "the undo may have reverted something else. Check the session "
            "before making more changes.")
    journal.forget_from(entry)
    return result


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
    "built-in audible one. Use when the user generated notes but hears "
    "nothing. NOTE: this one is NOT undoable from Stem — it replaces plugins "
    "on the track. Say so before using it on a track the user has already "
    "set up by hand.",
    Empty, mutates=True)
def make_tracks_audible(args, ctx):
    """Declared mutating on purpose.

    It swaps instrument plugins on existing tracks — a real, user-visible
    mutation — and the bridge gives back no action_id for it. Registering it
    as read-only (as it was) meant the registry never checked it and the
    result never told anyone the change could not be undone. It now returns
    an explicit undo verdict instead of a comfortable silence.
    """
    result = dict(ctx.bridge.fix_silent_instruments() or {})
    if "action_id" not in result:
        result.setdefault(
            "undo_note",
            "plugin swaps are not on Stem's undo journal; reverse this with "
            "Ardour's own undo (Ctrl/Cmd-Z) or by re-adding the old plugin")
    return result


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


# ============ ANALYSIS (deterministic — reads existing material) ============

class DetectKeyArgs(BaseModel):
    track_id: Optional[str] = Field(
        default=None,
        description="Analyse one track; omit to analyse every MIDI track in "
                    "the session together")


@registry.register(
    "detect_key",
    "Work out what key the existing material is in (Krumhansl-Schmuckler "
    "profile matching over the notes actually in the session — deterministic, "
    "no guessing). Call this before writing new parts so what you add is in "
    "key with what is already there. Returns confidence and the runners-up; "
    "low confidence means the material is ambiguous, say so rather than "
    "asserting a key.",
    DetectKeyArgs)
def detect_key(args, ctx):
    notes, tracks, skipped = _collect_notes(ctx, args.track_id)
    if not notes:
        return {"detected": False,
                "reason": "no MIDI notes found to analyse",
                "tracks_analysed": tracks, "tracks_skipped": skipped}
    result = analysis.detect_key(notes)
    result["tracks_analysed"] = tracks
    if skipped:
        result["tracks_skipped"] = skipped
    return result


class DetectChordsArgs(BaseModel):
    track_id: Optional[str] = Field(
        default=None,
        description="Analyse one track; omit to analyse every MIDI track")
    segment_beats: float = Field(
        default=4.0, gt=0,
        description="Length of each analysis window in beats — one bar of 4/4 "
                    "is 4.0, half-bar changes are 2.0")
    key: Optional[str] = Field(
        default=None,
        description="Root of the key for roman numerals, e.g. 'C', 'F#'. "
                    "Omitted: the key is detected from the same notes.")


@registry.register(
    "detect_chords",
    "Name the chords in existing material, window by window, with roman "
    "numerals in the detected (or given) key. Deterministic: chords are "
    "matched against the same chord vocabulary the write tools place, so "
    "anything named here can be re-voiced or extended exactly.",
    DetectChordsArgs)
def detect_chords(args, ctx):
    notes, tracks, skipped = _collect_notes(ctx, args.track_id)
    if not notes:
        return {"segments": [], "count": 0,
                "reason": "no MIDI notes found to analyse",
                "tracks_analysed": tracks}
    if args.key:
        tonic = _root_index(args.key)
        key_source = "given"
        key_name = NOTE_NAMES[tonic]
        key_scale = None
        key_confidence = None
    else:
        detected = analysis.detect_key(notes)
        tonic = detected.get("tonic_pitch_class")
        key_source = "detected"
        key_name = detected.get("key")
        key_scale = detected.get("scale")
        key_confidence = detected.get("confidence")
    segments = analysis.detect_chords(notes, args.segment_beats, tonic)
    return {
        "segments": segments,
        "count": len(segments),
        "progression": [s["chord"] for s in segments if s.get("chord")],
        "roman": [s.get("roman") for s in segments if s.get("roman")],
        "key": key_name,
        "key_scale": key_scale,
        "key_source": key_source,
        "key_confidence": key_confidence,
        "segment_beats": args.segment_beats,
        "tracks_analysed": tracks,
        "tracks_skipped": skipped or None,
    }


def _collect_notes(ctx, track_id: Optional[str]):
    """Notes from one track, or from every MIDI track in the session.

    Returns (notes, tracks_read, tracks_skipped). A track that cannot be read
    is reported in tracks_skipped with the reason instead of being dropped —
    an analysis over half the session that claims to cover all of it is worse
    than one that says what it missed.
    """
    if track_id:
        return list(ctx.bridge.get_midi_notes(track_id)), [track_id], []
    overview = ctx.bridge.get_session_overview()
    notes, read, skipped = [], [], []
    for track in overview.tracks:
        if track.kind != "midi":
            continue
        try:
            notes.extend(ctx.bridge.get_midi_notes(track.track_id))
            read.append(track.track_id)
        except Exception as e:
            skipped.append({"track_id": track.track_id, "reason": str(e)})
    return notes, read, skipped
