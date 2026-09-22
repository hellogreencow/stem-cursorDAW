"""Deterministic analysis of existing material: key and chords.

PLAN.md lists ``detect_key`` / ``detect_chords`` under "music intelligence
(deterministic — the theory engine, so no hallucinated notes)". This module is
that engine half: pure functions over notes, no model in the loop, same answer
every time.

Key detection is Krumhansl-Schmuckler: build a duration-weighted pitch-class
profile from the notes, correlate it against the Krumhansl-Kessler major and
minor key profiles in all 24 rotations, rank the results. Chord detection
segments the notes into windows and matches each window's pitch-class set
against the chord vocabulary already in notes.py, so a chord this engine can
name is a chord the write-side tools can place.
"""
import math
from typing import List, Optional

from .notes import Chord, ChordType, Note

# Krumhansl & Kessler (1982) key profiles: perceived stability of each scale
# degree, major and minor. Standard reference values.
MAJOR_PROFILE = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09,
                 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
MINOR_PROFILE = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53,
                 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]

NOTE_NAMES = Note.NOTE_NAMES

#: semitone offset from the tonic -> roman numeral (works for any mode)
DEGREE_NUMERALS = {0: "I", 1: "bII", 2: "II", 3: "bIII", 4: "III", 5: "IV",
                   6: "#IV", 7: "V", 8: "bVI", 9: "VI", 10: "bVII", 11: "VII"}

MINOR_QUALITIES = {ChordType.MINOR, ChordType.MINOR_7, ChordType.MINOR_9,
                   ChordType.MINOR_SIX, ChordType.DIMINISHED,
                   ChordType.DIMINISHED_7, ChordType.HALF_DIMINISHED}

#: chord templates, simplest first — a tie goes to the plainer chord
CHORD_TEMPLATES = sorted(Chord.INTERVALS.items(), key=lambda kv: len(kv[1]))


class NoteEvent:
    """One sounding note, in beats. Adapter over the two note shapes in the
    codebase: bridge.base.MidiNote (pitch/start_beat/length_beats) and
    theory.notes.Note (midi_note/start_time/duration)."""

    __slots__ = ("pitch", "start", "length", "velocity")

    def __init__(self, pitch: int, start: float, length: float,
                 velocity: int = 100):
        self.pitch = int(pitch)
        self.start = float(start)
        self.length = max(0.0, float(length))
        self.velocity = int(velocity)

    @property
    def end(self) -> float:
        return self.start + self.length

    @property
    def pitch_class(self) -> int:
        return self.pitch % 12


def as_events(notes) -> List[NoteEvent]:
    """Normalise any supported note representation into NoteEvents."""
    events = []
    for n in notes or []:
        if isinstance(n, NoteEvent):
            events.append(n)
        elif isinstance(n, dict):
            events.append(NoteEvent(
                n.get("pitch", n.get("midi_note")),
                n.get("start_beat", n.get("start", 0.0)),
                n.get("length_beats", n.get("duration", 1.0)),
                n.get("velocity", 100)))
        elif hasattr(n, "start_beat"):
            events.append(NoteEvent(n.pitch, n.start_beat, n.length_beats,
                                    getattr(n, "velocity", 100)))
        elif hasattr(n, "midi_note"):
            events.append(NoteEvent(n.midi_note, getattr(n, "start_time", 0.0),
                                    getattr(n, "duration", 1.0),
                                    getattr(n, "velocity", 100)))
        else:
            raise TypeError(f"unsupported note object: {n!r}")
    return events


def pitch_class_profile(notes) -> List[float]:
    """Duration-weighted weight per pitch class (a note held twice as long
    counts twice; velocity is deliberately ignored so a quiet pedal tone
    still counts)."""
    profile = [0.0] * 12
    for e in as_events(notes):
        profile[e.pitch_class] += max(e.length, 1e-6)
    return profile


def _correlation(a: List[float], b: List[float]) -> float:
    n = len(a)
    mean_a, mean_b = sum(a) / n, sum(b) / n
    da = [x - mean_a for x in a]
    db = [y - mean_b for y in b]
    denom = math.sqrt(sum(x * x for x in da) * sum(y * y for y in db))
    if denom == 0:
        return 0.0
    return sum(x * y for x, y in zip(da, db)) / denom


def detect_key(notes, top: int = 3) -> dict:
    """Best-fitting key for this material, with the runners-up.

    ``confidence`` is 0..1 and combines how well the winner fits (correlation)
    with how far clear of the runner-up it is (margin). It is a fit score, not
    a probability: two keys a semitone apart can both fit real music.
    """
    events = as_events(notes)
    if not events:
        return {"detected": False, "reason": "no notes to analyse",
                "notes_analysed": 0}
    profile = pitch_class_profile(events)
    ranked = []
    for tonic in range(12):
        for scale_name, base in (("major", MAJOR_PROFILE),
                                 ("minor", MINOR_PROFILE)):
            rotated = [base[(i - tonic) % 12] for i in range(12)]
            ranked.append({
                "key": NOTE_NAMES[tonic],
                "tonic_pitch_class": tonic,
                "scale": scale_name,
                "correlation": round(_correlation(profile, rotated), 4),
            })
    ranked.sort(key=lambda r: r["correlation"], reverse=True)
    best, second = ranked[0], ranked[1]
    margin = best["correlation"] - second["correlation"]
    fit = max(0.0, min(1.0, best["correlation"]))
    confidence = round(fit * (0.5 + 0.5 * min(margin / 0.10, 1.0)), 3)
    distinct = sum(1 for w in profile if w > 0)
    return {
        "detected": True,
        "key": best["key"],
        "scale": best["scale"],
        "tonic_pitch_class": best["tonic_pitch_class"],
        "correlation": best["correlation"],
        "margin": round(margin, 4),
        "confidence": confidence,
        "alternates": ranked[1:1 + max(0, top - 1)],
        "notes_analysed": len(events),
        "distinct_pitch_classes": distinct,
        "caveat": ("fewer than 4 distinct pitch classes — too little material "
                   "to be sure") if distinct < 4 else None,
    }


def name_pitch_classes(pitch_classes, bass_pitch_class: Optional[int] = None) -> dict:
    """Name a set of pitch classes as the closest chord in the vocabulary.

    Scoring is Jaccard overlap between the template and the sounding pitch
    classes (1.0 = exact), with small bonuses for a root that is actually
    sounding and for a root that is in the bass. Returns the missing and extra
    pitch classes so the caller can see *why* a match is partial.
    """
    pcs = {int(p) % 12 for p in pitch_classes}
    if not pcs:
        return {"named": False, "reason": "no pitches"}
    best = None
    for root in range(12):
        for chord_type, intervals in CHORD_TEMPLATES:
            template = {(root + i) % 12 for i in intervals}
            overlap = len(template & pcs)
            union = len(template | pcs)
            score = overlap / union if union else 0.0
            score += 0.02 if root in pcs else -0.05
            if bass_pitch_class is not None and root == bass_pitch_class % 12:
                score += 0.03
            candidate = (round(score, 6), -len(template), root, chord_type,
                         template)
            if best is None or candidate[:2] > best[:2]:
                best = candidate
    score, _, root, chord_type, template = best
    chord = Chord(root, chord_type)
    return {
        "named": True,
        "chord": chord.get_name(),
        "root": NOTE_NAMES[root],
        "root_pitch_class": root,
        "type": chord_type.value,
        "confidence": round(max(0.0, min(1.0, score)), 3),
        "exact": template == pcs,
        "missing": sorted(NOTE_NAMES[p] for p in template - pcs),
        "extra": sorted(NOTE_NAMES[p] for p in pcs - template),
        "inversion_bass": (NOTE_NAMES[bass_pitch_class % 12]
                           if bass_pitch_class is not None
                           and bass_pitch_class % 12 != root else None),
    }


def roman_numeral(root_pitch_class: int, chord_type: ChordType,
                  tonic_pitch_class: int) -> str:
    """Roman numeral of a chord relative to a tonic, minor in lower case."""
    numeral = DEGREE_NUMERALS[(root_pitch_class - tonic_pitch_class) % 12]
    if chord_type in MINOR_QUALITIES:
        numeral = numeral.lower()
    if chord_type in (ChordType.DIMINISHED, ChordType.DIMINISHED_7,
                      ChordType.HALF_DIMINISHED):
        numeral += "o"
    return numeral


def detect_chords(notes, segment_beats: float = 4.0,
                  tonic_pitch_class: Optional[int] = None) -> List[dict]:
    """Split the material into fixed windows and name each window's chord.

    Windows are ``segment_beats`` long, starting at the first onset. A note
    counts towards a window if it sounds during it; weighting is by how much
    of the note lies inside the window, so a held pad does not drown the
    chord that changes under it.
    """
    events = as_events(notes)
    if not events:
        return []
    if segment_beats <= 0:
        raise ValueError("segment_beats must be > 0")
    start = min(e.start for e in events)
    finish = max(e.end for e in events)
    segments = []
    window_start = start
    while window_start < finish - 1e-9:
        window_end = window_start + segment_beats
        sounding = [e for e in events
                    if e.end > window_start + 1e-9 and e.start < window_end - 1e-9]
        if not sounding:
            segments.append({"start_beat": round(window_start, 6),
                             "end_beat": round(window_end, 6),
                             "chord": None, "note_count": 0, "rest": True})
            window_start = window_end
            continue
        pcs = {e.pitch_class for e in sounding}
        bass = min(sounding, key=lambda e: e.pitch).pitch_class
        named = name_pitch_classes(pcs, bass)
        segment = {
            "start_beat": round(window_start, 6),
            "end_beat": round(window_end, 6),
            "note_count": len(sounding),
            "rest": False,
            "chord": named.get("chord"),
            "root": named.get("root"),
            "type": named.get("type"),
            "confidence": named.get("confidence"),
            "exact": named.get("exact"),
            "missing": named.get("missing"),
            "extra": named.get("extra"),
            "bass": NOTE_NAMES[bass],
        }
        if tonic_pitch_class is not None and named.get("named"):
            segment["roman"] = roman_numeral(
                named["root_pitch_class"], ChordType(named["type"]),
                tonic_pitch_class)
        segments.append(segment)
        window_start = window_end
    return segments
