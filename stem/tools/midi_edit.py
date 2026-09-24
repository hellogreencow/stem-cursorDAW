"""MIDI editing tools: change, delete, quantize, transpose, humanize, copy.

Every tool here is a read-modify-write on ONE track: read the notes, work out
the new notes in Python, and hand the result to the bridge as a single
``replace_midi_notes`` call (or ``insert_midi_notes`` when nothing needs to
go). One bridge call means one undo step, and the registry fingerprints the
track before and after, so the ``verified`` block says whether the notes
really changed.

Nothing touches the bridge until every check has passed: an edit that names
a note that isn't there, or would push a pitch past 127, fails with nothing
changed and nothing on the undo stack. An edit that would change nothing
(quantizing notes already on the grid) makes no bridge call at all and says
so, rather than leaving an empty undo step behind.

Why the replace range is not simply the user's range: replace_midi_notes
removes every note whose start falls in [start, end). If the range edge sits
exactly on a note, float-to-tick rounding on the Ardour side decides whether
that note goes — and a note we meant to keep would be silently deleted, or
one we meant to move would be doubled. So the range sent to the bridge is
cut at the midpoint of the gaps around the notes being rewritten, never on a
note, and any untouched note inside it is written back exactly as it was.
"""
import random
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator

from .core import registry, _root_index
from ..bridge.base import MidiNote
from ..theory import Scale, ScaleType
from ..theory import analysis

#: two positions closer than this are the same position (Ardour's grid is
#: 1/1920 of a beat; this is about two ticks)
MATCH_TOL = 1e-3
#: positions are stored rounded to this many decimals so fingerprints are stable
PLACES = 6


# ============ shared machinery ============

def _clone(n: MidiNote, **changes) -> MidiNote:
    fields = dict(pitch=n.pitch, start_beat=n.start_beat,
                  length_beats=n.length_beats, velocity=n.velocity,
                  channel=getattr(n, "channel", 0))
    fields.update(changes)
    fields["start_beat"] = round(float(fields["start_beat"]), PLACES)
    fields["length_beats"] = round(float(fields["length_beats"]), PLACES)
    return MidiNote(**fields)


def _same(a: MidiNote, b: MidiNote) -> bool:
    return (a.pitch == b.pitch and a.velocity == b.velocity
            and getattr(a, "channel", 0) == getattr(b, "channel", 0)
            and abs(a.start_beat - b.start_beat) < 1e-9
            and abs(a.length_beats - b.length_beats) < 1e-9)


def _read_track(ctx, track_id: str):
    """(notes, error). Checks the track exists and is MIDI before anything."""
    overview = ctx.bridge.get_session_overview()
    track = next((t for t in overview.tracks if t.track_id == track_id), None)
    if track is None:
        return None, f"no such track: {track_id}"
    if track.kind != "midi":
        return None, f"track {track_id} is an {track.kind} track, not MIDI"
    return list(ctx.bridge.get_midi_notes(track_id)), None


def _in_range(n: MidiNote, start_beat: float, end_beat: Optional[float]) -> bool:
    return (n.start_beat >= start_beat - MATCH_TOL / 2
            and (end_beat is None or n.start_beat < end_beat - MATCH_TOL / 2))


def replace_window(current: list, remove_idx) -> tuple:
    """The [lo, hi) to send to replace_midi_notes so that it removes the
    notes in remove_idx, with both edges in a gap between notes."""
    starts = [current[i].start_beat for i in remove_idx]
    first, last = min(starts), max(starts)
    below = [n.start_beat for n in current if n.start_beat < first - MATCH_TOL]
    above = [n.start_beat for n in current if n.start_beat > last + MATCH_TOL]
    lo = (max(below) + first) / 2 if below else 0.0
    hi = (last + min(above)) / 2 if above else None
    return round(lo, PLACES), (round(hi, PLACES) if hi is not None else None)


def rewrite(ctx, track_id: str, current: list, remove_idx, add: list) -> dict:
    """Remove current[i] for i in remove_idx and add ``add``, as ONE bridge
    call. Returns the action_id plus what was sent, or a noop marker."""
    remove_idx = set(remove_idx)
    if not remove_idx and not add:
        return {"noop": True}
    if not remove_idx:
        action_id = ctx.bridge.insert_midi_notes(track_id, add, 0.0)
        return {"action_id": action_id, "removed": 0, "added": len(add)}
    lo, hi = replace_window(current, remove_idx)
    keep = [n for i, n in enumerate(current)
            if i not in remove_idx and n.start_beat >= lo
            and (hi is None or n.start_beat < hi)]
    action_id = ctx.bridge.replace_midi_notes(track_id, keep + add, lo, hi)
    return {"action_id": action_id, "removed": len(remove_idx),
            "added": len(add)}


def _apply(ctx, track_id: str, current: list, new_by_index: dict,
           deleted=()) -> dict:
    """new_by_index: {i: replacement MidiNote}; deleted: indexes to drop.
    Notes whose replacement equals the original are left alone."""
    changed = {i: n for i, n in new_by_index.items()
               if not _same(current[i], n)}
    remove = set(changed) | set(deleted)
    out = rewrite(ctx, track_id, current, remove, list(changed.values()))
    out["notes_changed"] = len(changed)
    out["notes_deleted"] = len(set(deleted))
    if out.get("noop"):
        out["note"] = "nothing to change — every selected note already matches"
    return out


def _range_error(start_beat, end_beat):
    if end_beat is not None and end_beat <= start_beat:
        raise ValueError(f"end_beat ({end_beat}) must be after start_beat "
                         f"({start_beat})")


class BeatRange(BaseModel):
    start_beat: float = Field(default=0.0, ge=0,
                              description="Only notes starting at or after this beat")
    end_beat: Optional[float] = Field(
        default=None, description="...and before this beat; omit for the end "
                                  "of the track")
    pitch_min: int = Field(default=0, ge=0, le=127)
    pitch_max: int = Field(default=127, ge=0, le=127)

    @model_validator(mode="after")
    def _check_range(self):
        _range_error(self.start_beat, self.end_beat)
        if self.pitch_min > self.pitch_max:
            raise ValueError("pitch_min is above pitch_max")
        return self

    def selects(self, n: MidiNote) -> bool:
        return (_in_range(n, self.start_beat, self.end_beat)
                and self.pitch_min <= n.pitch <= self.pitch_max)


def _selected(current, rng: BeatRange) -> list:
    return [i for i, n in enumerate(current) if rng.selects(n)]


# ============ edit_midi_notes ============

class NoteEdit(BaseModel):
    pitch: int = Field(ge=0, le=127, description="The note to edit, as read "
                                                 "by get_midi_notes")
    start_beat: float = Field(ge=0, description="...and its start beat")
    delete: bool = False
    new_pitch: Optional[int] = Field(default=None, ge=0, le=127)
    new_start_beat: Optional[float] = Field(default=None, ge=0)
    new_length_beats: Optional[float] = Field(default=None, gt=0)
    new_velocity: Optional[int] = Field(default=None, ge=1, le=127)

    @model_validator(mode="after")
    def _one_thing(self):
        wants_change = any(v is not None for v in (
            self.new_pitch, self.new_start_beat, self.new_length_beats,
            self.new_velocity))
        if self.delete and wants_change:
            raise ValueError("an edit either deletes a note or changes it, "
                             "not both")
        if not self.delete and not wants_change:
            raise ValueError("edit changes nothing: set delete or a new_* field")
        return self


class BulkChange(BaseModel):
    delete: bool = False
    shift_beats: float = Field(default=0.0, ge=-1024, le=1024,
                               description="Move selected notes by this many beats")
    velocity: Optional[int] = Field(default=None, ge=1, le=127,
                                    description="Set every selected velocity")
    velocity_delta: int = Field(default=0, ge=-126, le=126,
                                description="Add to velocity (clamped to 1-127)")
    length_beats: Optional[float] = Field(default=None, gt=0)
    length_scale: Optional[float] = Field(default=None, gt=0, le=16)

    @model_validator(mode="after")
    def _one_thing(self):
        wants_change = (self.shift_beats != 0 or self.velocity is not None
                        or self.velocity_delta != 0
                        or self.length_beats is not None
                        or self.length_scale is not None)
        if self.delete and wants_change:
            raise ValueError("change either deletes the notes or edits them, "
                             "not both")
        if not self.delete and not wants_change:
            raise ValueError("change does nothing")
        if self.velocity is not None and self.velocity_delta:
            raise ValueError("give velocity or velocity_delta, not both")
        if self.length_beats is not None and self.length_scale is not None:
            raise ValueError("give length_beats or length_scale, not both")
        return self


class EditNotes(BaseModel):
    track_id: str
    edits: List[NoteEdit] = Field(
        default_factory=list,
        description="Per-note edits. Each names one existing note by pitch + "
                    "start_beat (from get_midi_notes) and deletes it or sets "
                    "new values.")
    select: Optional[BeatRange] = Field(
        default=None, description="Bulk mode: which notes (beat range and "
                                  "pitch range). Use with change.")
    change: Optional[BulkChange] = None

    @model_validator(mode="after")
    def _one_mode(self):
        if self.edits and (self.select or self.change):
            raise ValueError("use either edits (per-note) or select + change "
                             "(bulk), not both")
        if not self.edits and not (self.select and self.change):
            raise ValueError("nothing to do: give edits, or select + change")
        return self


@registry.register(
    "edit_midi_notes",
    "Change or delete existing MIDI notes — pitch, start, length, velocity — "
    "as ONE undoable step. Per-note: read get_midi_notes, then name each note "
    "by pitch + start_beat. Bulk: select a beat/pitch range and apply one "
    "change (delete, shift_beats, velocity, velocity_delta, length). "
    "Nothing changes unless every edit is valid.",
    EditNotes, mutates=True)
def edit_midi_notes(args, ctx):
    current, err = _read_track(ctx, args.track_id)
    if err:
        return {"error": err}

    new_by_index, deleted = {}, set()
    if args.edits:
        taken = set()
        for k, e in enumerate(args.edits):
            idx = next((i for i, n in enumerate(current)
                        if i not in taken and n.pitch == e.pitch
                        and abs(n.start_beat - e.start_beat) < MATCH_TOL), None)
            if idx is None:
                return {"error": f"edit {k}: no note with pitch {e.pitch} at "
                                 f"beat {e.start_beat} on {args.track_id} "
                                 "(read get_midi_notes first)"}
            taken.add(idx)
            if e.delete:
                deleted.add(idx)
                continue
            n = current[idx]
            new_by_index[idx] = _clone(
                n,
                pitch=e.new_pitch if e.new_pitch is not None else n.pitch,
                start_beat=(e.new_start_beat if e.new_start_beat is not None
                            else n.start_beat),
                length_beats=(e.new_length_beats if e.new_length_beats
                              is not None else n.length_beats),
                velocity=(e.new_velocity if e.new_velocity is not None
                          else n.velocity))
    else:
        picked = _selected(current, args.select)
        if not picked:
            return {"error": "no notes in that selection on "
                             f"{args.track_id}"}
        c = args.change
        if c.delete:
            deleted = set(picked)
        else:
            for i in picked:
                n = current[i]
                start = n.start_beat + c.shift_beats
                if start < 0:
                    return {"error": f"shift_beats {c.shift_beats} would move "
                                     f"the note at beat {n.start_beat} before "
                                     "beat 0"}
                if c.velocity is not None:
                    vel = c.velocity
                else:
                    vel = max(1, min(127, n.velocity + c.velocity_delta))
                if c.length_beats is not None:
                    length = c.length_beats
                elif c.length_scale is not None:
                    length = n.length_beats * c.length_scale
                else:
                    length = n.length_beats
                new_by_index[i] = _clone(n, start_beat=start, velocity=vel,
                                         length_beats=length)

    out = _apply(ctx, args.track_id, current, new_by_index, deleted)
    out["track_id"] = args.track_id
    return out


# ============ quantize ============

class QuantizeArgs(BeatRange):
    track_id: str
    grid: float = Field(default=0.25, gt=0, le=4,
                        description="Grid step in beats (quarter notes): 1 = "
                                    "1/4, 0.5 = 1/8, 0.25 = 1/16, 0.3333 = "
                                    "1/8 triplet")
    strength: float = Field(default=1.0, ge=0, le=1,
                            description="1 = snap fully, 0.5 = halfway there")
    swing: float = Field(default=0.0, ge=0, le=0.5,
                         description="Delay every second grid slot by this "
                                     "fraction of a grid step (0 = straight, "
                                     "~0.33 = triplet shuffle)")
    quantize_length: bool = Field(default=False,
                                  description="Also snap note ends to the grid")


def _grid_target(pos: float, grid: float, swing: float) -> float:
    slot = round(pos / grid)
    target = slot * grid
    if swing and slot % 2 == 1:
        target += swing * grid
    return target


@registry.register(
    "quantize",
    "Snap note starts (optionally ends) to a grid, with strength and swing, "
    "on a track or a beat range. ONE undoable step.",
    QuantizeArgs, mutates=True)
def quantize(args, ctx):
    current, err = _read_track(ctx, args.track_id)
    if err:
        return {"error": err}
    picked = _selected(current, args)
    if not picked:
        return {"error": f"no notes in that range on {args.track_id}"}

    new_by_index = {}
    for i in picked:
        n = current[i]
        start = n.start_beat + args.strength * (
            _grid_target(n.start_beat, args.grid, args.swing) - n.start_beat)
        start = max(0.0, start)
        length = n.length_beats
        if args.quantize_length:
            end = n.start_beat + n.length_beats
            end = end + args.strength * (
                round(end / args.grid) * args.grid - end)
            length = end - start
            if length < MATCH_TOL:          # snapped to nothing: keep it audible
                length = min(n.length_beats, args.grid)
        new_by_index[i] = _clone(n, start_beat=start, length_beats=length)

    out = _apply(ctx, args.track_id, current, new_by_index)
    moved = list(new_by_index.values())
    keys = [(n.pitch, round(n.start_beat, 4)) for n in moved]
    collisions = len(keys) - len(set(keys))
    out.update({"track_id": args.track_id, "notes_considered": len(picked),
                "grid": args.grid, "strength": args.strength,
                "swing": args.swing})
    if collisions:
        out["collisions"] = collisions
        out["collision_note"] = ("that many notes now sit on the same pitch "
                                 "and beat as another note — they stack, "
                                 "nothing was merged")
    return out


# ============ transpose ============

SCALE_NAMES = [s.value for s in ScaleType]


class TransposeArgs(BeatRange):
    track_id: str
    semitones: int = Field(default=0, ge=-48, le=48,
                           description="Chromatic shift, e.g. 12 = up an octave")
    scale_steps: int = Field(default=0, ge=-21, le=21,
                             description="Diatonic shift in scale degrees, "
                                         "e.g. 2 = up a third in key")
    key: Optional[str] = Field(default=None,
                               description="Key root for scale_steps, e.g. 'A'. "
                                           "Omit to detect it from the track.")
    scale: Optional[str] = Field(default=None,
                                 description="Scale for scale_steps: "
                                             + ", ".join(SCALE_NAMES)
                                             + ". Omit with key to detect.")

    @model_validator(mode="after")
    def _one_kind(self):
        if bool(self.semitones) == bool(self.scale_steps):
            raise ValueError("give exactly one of semitones or scale_steps "
                             "(non-zero)")
        if self.semitones and (self.key or self.scale):
            raise ValueError("key/scale only apply to scale_steps")
        if self.scale is not None and self.scale not in SCALE_NAMES:
            raise ValueError(f"unknown scale {self.scale!r}; one of "
                             + ", ".join(SCALE_NAMES))
        if self.scale and not self.key:
            raise ValueError("a scale needs a key root")
        return self


def diatonic_shift(pitch: int, steps: int, root: int, intervals: list) -> int:
    """Move a pitch by scale degrees. A note outside the scale moves with
    the degree just below it and keeps its alteration (C# in C major, up a
    step, becomes D#)."""
    rel = (pitch - root) % 12
    base = pitch - rel
    idx = max(i for i, iv in enumerate(intervals) if iv <= rel)
    alteration = rel - intervals[idx]
    octave, degree = divmod(idx + steps, len(intervals))
    return base + 12 * octave + intervals[degree] + alteration


@registry.register(
    "transpose",
    "Transpose notes on a track (or a beat/pitch range): by semitones, or "
    "diatonically by scale_steps within a key (detected from the track if "
    "you don't give one). ONE undoable step; refuses if any note would leave "
    "0-127.",
    TransposeArgs, mutates=True)
def transpose(args, ctx):
    current, err = _read_track(ctx, args.track_id)
    if err:
        return {"error": err}
    picked = _selected(current, args)
    if not picked:
        return {"error": f"no notes in that range on {args.track_id}"}

    out_extra = {}
    if args.scale_steps:
        if args.key:
            try:
                root = _root_index(args.key)
            except ValueError as e:
                return {"error": str(e)}
            scale_name = args.scale or "major"
            out_extra["key"] = f"{args.key} {scale_name}"
        else:
            found = analysis.detect_key(current)
            if not found.get("detected"):
                return {"error": "couldn't detect a key from this track — "
                                 "give key (and scale)"}
            root, scale_name = found["tonic_pitch_class"], found["scale"]
            out_extra["key"] = f"{found['key']} {scale_name}"
            out_extra["key_detected"] = True
            out_extra["key_confidence"] = found.get("confidence")
            if found.get("caveat"):
                out_extra["key_caveat"] = found["caveat"]
        intervals = Scale(root, ScaleType(scale_name)).intervals

        def shift(p):
            return diatonic_shift(p, args.scale_steps, root, intervals)
    else:
        def shift(p):
            return p + args.semitones

    new_by_index, outside = {}, []
    for i in picked:
        n = current[i]
        p = shift(n.pitch)
        if not 0 <= p <= 127:
            outside.append(f"{n.pitch}@{n.start_beat:g}")
            continue
        new_by_index[i] = _clone(n, pitch=p)
    if outside:
        return {"error": f"{len(outside)} note(s) would leave MIDI range "
                         f"0-127 ({', '.join(outside[:5])}); nothing changed"}

    out = _apply(ctx, args.track_id, current, new_by_index)
    out.update(out_extra)
    out["track_id"] = args.track_id
    return out


# ============ humanize ============

class HumanizeArgs(BeatRange):
    track_id: str
    timing_beats: float = Field(default=0.02, ge=0, le=0.25,
                                description="Most a start may drift, in beats "
                                            "(0.02 ≈ 10 ms at 120 bpm)")
    velocity: int = Field(default=8, ge=0, le=40,
                          description="Most a velocity may drift, +/-")
    seed: Optional[int] = Field(default=None,
                                description="Same seed, same result")

    @model_validator(mode="after")
    def _does_something(self):
        if not self.timing_beats and not self.velocity:
            raise ValueError("timing_beats and velocity are both 0 — "
                             "nothing to humanize")
        return self


@registry.register(
    "humanize",
    "Loosen robotic MIDI: small random drift on note starts and velocities, "
    "on a track or beat range. ONE undoable step; the seed is returned so "
    "the exact result can be recreated.",
    HumanizeArgs, mutates=True)
def humanize(args, ctx):
    current, err = _read_track(ctx, args.track_id)
    if err:
        return {"error": err}
    picked = _selected(current, args)
    if not picked:
        return {"error": f"no notes in that range on {args.track_id}"}
    seed = args.seed if args.seed is not None else random.randrange(2 ** 31)
    rng = random.Random(seed)

    def drift(limit):
        # bell-shaped, most notes close to where they were, none past limit
        return max(-limit, min(limit, rng.gauss(0.0, limit / 2.0)))

    new_by_index = {}
    for i in sorted(picked, key=lambda i: (current[i].start_beat,
                                           current[i].pitch)):
        n = current[i]
        start = max(0.0, n.start_beat + drift(args.timing_beats))
        vel = int(round(n.velocity + drift(args.velocity)))
        new_by_index[i] = _clone(n, start_beat=start,
                                 velocity=max(1, min(127, vel)))

    out = _apply(ctx, args.track_id, current, new_by_index)
    out.update({"track_id": args.track_id, "seed": seed})
    return out


# ============ duplicate_notes ============

class DuplicateArgs(BaseModel):
    track_id: str
    start_beat: float = Field(ge=0, description="Copy notes starting here...")
    end_beat: float = Field(gt=0, description="...up to (not including) here")
    destination_beat: Optional[float] = Field(
        default=None, ge=0,
        description="Where the first copy starts; omit to place it right "
                    "after the source")
    times: int = Field(default=1, ge=1, le=64)
    replace_existing: bool = Field(
        default=False,
        description="Clear notes already in the destination first; otherwise "
                    "copies are layered over them")

    @model_validator(mode="after")
    def _check(self):
        _range_error(self.start_beat, self.end_beat)
        span = self.end_beat - self.start_beat
        dest = self.end_beat if self.destination_beat is None \
            else self.destination_beat
        if dest < self.end_beat and dest + span * self.times > self.start_beat:
            raise ValueError("destination overlaps the source range")
        return self


@registry.register(
    "duplicate_notes",
    "Copy the notes in a beat range to later (or earlier) on the SAME track, "
    "one or more times — e.g. repeat bars 1-4 into 5-8. Works on notes, not "
    "Ardour regions. ONE undoable step.",
    DuplicateArgs, mutates=True)
def duplicate_notes(args, ctx):
    current, err = _read_track(ctx, args.track_id)
    if err:
        return {"error": err}
    span = args.end_beat - args.start_beat
    dest = args.end_beat if args.destination_beat is None \
        else args.destination_beat
    source = [n for n in current
              if _in_range(n, args.start_beat, args.end_beat)]
    if not source:
        return {"error": f"no notes between beat {args.start_beat:g} and "
                         f"{args.end_beat:g} on {args.track_id}"}
    copies = []
    for k in range(args.times):
        offset = dest - args.start_beat + k * span
        copies.extend(_clone(n, start_beat=n.start_beat + offset)
                      for n in source)
    clear = []
    if args.replace_existing:
        clear = [i for i, n in enumerate(current)
                 if _in_range(n, dest, dest + span * args.times)]
    out = rewrite(ctx, args.track_id, current, clear, copies)
    out.update({"track_id": args.track_id, "copied": len(source),
                "times": args.times, "destination_beat": dest,
                "cleared": len(clear)})
    return out


# ============ add_instrument ============

class InstrumentTrack(BaseModel):
    track_id: str = Field(description="MIDI track id from get_session_overview")


@registry.register(
    "add_instrument",
    "Give ONE MIDI track an audible instrument: adds the built-in synth if "
    "the track has none, or replaces a silent a-fluidsynth. It does not load "
    "a plugin of your choice — to pick the sound, create the track with an "
    "instrument_id instead. Undoable through Stem's undo only when the result "
    "says so (Ardour's own Ctrl-Z never covers plugin changes).",
    InstrumentTrack, mutates=True)
def add_instrument(args, ctx):
    overview = ctx.bridge.get_session_overview()
    track = next((t for t in overview.tracks if t.track_id == args.track_id),
                 None)
    if track is None:
        return {"error": f"no such track: {args.track_id}"}
    if track.kind != "midi":
        return {"error": f"track {args.track_id} is not a MIDI track"}
    result = dict(ctx.bridge.add_instrument(args.track_id) or {})
    if result.get("ok") is False:
        return {"error": result.get("note", "the bridge can't add instruments")}
    if result.get("already") and not result.get("action_id"):
        result["noop"] = True
        result["note"] = "the track already has a working instrument"
        return result
    if result.get("action_id"):
        result["undo_note"] = PLUGIN_UNDO_JOURNALED
    else:
        result["undo_note"] = PLUGIN_UNDO_NONE
    return result


#: from Ardour source (8.12 and 9.8): Route::add_processor_by_index and
#: Route::replace_processor (libs/ardour/route.cc) add no undo command, and
#: gtk2_ardour/processor_box.cc has no begin_reversible_command at all — so a
#: plugin change is never on Ardour's undo history, whoever makes it.
PLUGIN_UNDO_JOURNALED = (
    "undoable with Stem's undo (the bridge journaled the previous plugin "
    "chain). Ardour's own Ctrl-Z will not reverse it — Ardour keeps no undo "
    "record for plugin changes.")
PLUGIN_UNDO_NONE = (
    "not undoable: this bridge did not journal the change, and Ardour keeps "
    "no undo record for plugin changes. Remove the plugin by hand to reverse "
    "it.")
