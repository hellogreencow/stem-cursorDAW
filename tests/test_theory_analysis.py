"""Key and chord detection: the deterministic analysis half of PLAN.md's
"music intelligence" section, tested by round-tripping material this repo's
own write tools place.
"""
import pytest

from stem.agent.loop import ToolContext
from stem.bridge.base import MidiNote
from stem.bridge.mock import MockBridge
from stem.theory import analysis
from stem.theory.notes import Chord, ChordProgression, ChordType, Note
from stem.tools.core import registry


@pytest.fixture
def ctx():
    return ToolContext(bridge=MockBridge())


def run(ctx, tool, **kwargs):
    return registry.execute(tool, kwargs, ctx)


def notes_for(progression: str, key: int, octave: int = 4, beats: float = 4.0):
    prog = ChordProgression.from_progression_name(progression, key)
    out, beat = [], 0.0
    for chord, _ in prog.chords:
        chord.octave = octave
        for n in chord.get_notes():
            out.append(MidiNote(pitch=n.midi_note, start_beat=beat,
                                length_beats=beats, velocity=90))
        beat += beats
    return out


# ---- chord naming ----

@pytest.mark.parametrize("pcs, expected", [
    ({0, 4, 7}, "C"),
    ({9, 0, 4}, "Am"),
    ({0, 3, 6}, "Cdim"),
    ({0, 4, 8}, "Caug"),
    ({0, 4, 7, 11}, "Cmaj7"),
    ({7, 11, 2, 5}, "G7"),
    ({0, 5, 7}, "Csus4"),
])
def test_named_chords_are_exact(pcs, expected):
    named = analysis.name_pitch_classes(pcs)
    assert named["chord"] == expected
    assert named["exact"] is True
    assert named["confidence"] == 1.0


def test_ambiguous_sets_are_settled_by_the_bass():
    """{A C E G} is Am7 and C6 at once — the bass decides, and the tool says
    which reading it took rather than pretending there is only one."""
    assert analysis.name_pitch_classes({9, 0, 4, 7}, 9)["chord"] == "Am7"
    assert analysis.name_pitch_classes({9, 0, 4, 7}, 0)["chord"] == "C6"


def test_inversion_reports_the_bass_note():
    named = analysis.name_pitch_classes({0, 4, 7}, bass_pitch_class=4)
    assert named["chord"] == "C"
    assert named["inversion_bass"] == "E"


def test_partial_chord_says_what_is_missing():
    named = analysis.name_pitch_classes({0, 7})      # no third
    assert named["exact"] is False
    assert named["confidence"] < 1.0
    assert named["missing"]


# ---- key detection ----

def test_detects_c_major_from_a_progression():
    result = analysis.detect_key(notes_for("I_V_vi_IV", 0))
    assert (result["key"], result["scale"]) == ("C", "major")
    assert result["confidence"] > 0.7
    assert result["notes_analysed"] == 12


def test_detects_a_minor():
    result = analysis.detect_key(notes_for("i_VII_VI_V", 9, octave=3))
    assert (result["key"], result["scale"]) == ("A", "minor")


def test_detects_f_sharp_major():
    result = analysis.detect_key(notes_for("I_IV_V", 6))
    assert result["key"] == "F#"


def test_thin_material_carries_a_caveat():
    result = analysis.detect_key([MidiNote(pitch=60, start_beat=0,
                                           length_beats=1)])
    assert result["detected"] is True
    assert result["caveat"]


def test_no_notes_is_not_a_guess():
    assert analysis.detect_key([])["detected"] is False


def test_duration_weighting_beats_note_count():
    """One long tonic pedal should not be outvoted by passing notes."""
    profile = analysis.pitch_class_profile([
        MidiNote(pitch=60, start_beat=0, length_beats=16),
        MidiNote(pitch=61, start_beat=0, length_beats=0.25),
    ])
    assert profile[0] > profile[1] * 10


# ---- segmentation and roman numerals ----

def test_chord_segments_match_what_was_written():
    segments = analysis.detect_chords(notes_for("I_V_vi_IV", 0), 4.0, 0)
    assert [s["chord"] for s in segments] == ["C", "G", "Am", "F"]
    assert [s["roman"] for s in segments] == ["I", "V", "vi", "IV"]
    assert all(s["exact"] for s in segments)


def test_minor_key_roman_numerals_use_flat_degrees():
    segments = analysis.detect_chords(notes_for("i_VII_VI_V", 9, octave=3),
                                      4.0, 9)
    assert [s["roman"] for s in segments] == ["i", "bVII", "bVI", "V"]


def test_rests_are_reported_as_rests():
    notes = [MidiNote(pitch=60, start_beat=0, length_beats=1),
             MidiNote(pitch=64, start_beat=0, length_beats=1),
             MidiNote(pitch=67, start_beat=0, length_beats=1),
             MidiNote(pitch=62, start_beat=8, length_beats=1)]
    segments = analysis.detect_chords(notes, 4.0)
    assert len(segments) == 3
    assert segments[1]["rest"] is True and segments[1]["chord"] is None


def test_segment_beats_must_be_positive():
    with pytest.raises(ValueError):
        analysis.detect_chords(notes_for("I_IV_V", 0), 0)


def test_theory_note_objects_are_accepted_too():
    chord = Chord(0, ChordType.MAJOR, 4)
    events = analysis.as_events(chord.get_notes())
    assert [e.pitch for e in events] == [n.midi_note for n in chord.get_notes()]
    assert isinstance(chord.get_notes()[0], Note)


# ---- the tools, through the registry, on a live mock session ----

def test_detect_key_round_trips_through_the_write_tools(ctx):
    track_id = run(ctx, "create_midi_track", name="Chords")["track_id"]
    run(ctx, "insert_chord_progression", track_id=track_id, key="F",
        progression="I_IV_V")

    result = run(ctx, "detect_key", track_id=track_id)

    assert result["key"] == "F" and result["scale"] == "major"
    assert result["tracks_analysed"] == [track_id]


def test_detect_chords_names_what_the_agent_just_placed(ctx):
    track_id = run(ctx, "create_midi_track", name="Chords")["track_id"]
    run(ctx, "insert_chord_progression", track_id=track_id, key="C",
        progression="I_V_vi_IV")

    result = run(ctx, "detect_chords", track_id=track_id, segment_beats=4.0)

    assert result["progression"] == ["C", "G", "Am", "F"]
    assert result["roman"] == ["I", "V", "vi", "IV"]
    assert result["key"] == "C" and result["key_source"] == "detected"


def test_detect_chords_accepts_a_given_key(ctx):
    track_id = run(ctx, "create_midi_track", name="Chords")["track_id"]
    run(ctx, "insert_chord_progression", track_id=track_id, key="C",
        progression="I_V_vi_IV")

    result = run(ctx, "detect_chords", track_id=track_id, key="A")

    assert result["key_source"] == "given"
    assert result["roman"][0] == "bIII"      # C is bIII of A


def test_analysis_covers_every_midi_track_when_no_track_given(ctx):
    first = run(ctx, "create_midi_track", name="Chords")["track_id"]
    second = run(ctx, "create_midi_track", name="Bass")["track_id"]
    run(ctx, "insert_chord_progression", track_id=first, key="C",
        progression="I_V_vi_IV")
    run(ctx, "insert_bassline", track_id=second, key="C",
        progression="I_V_vi_IV")

    result = run(ctx, "detect_key")

    assert sorted(result["tracks_analysed"]) == sorted([first, second])
    assert result["key"] == "C"


def test_analysis_on_an_empty_session_does_not_invent_a_key(ctx):
    result = run(ctx, "detect_key")
    assert result["detected"] is False
    assert "no MIDI notes" in result["reason"]


def test_analysis_tools_are_read_only():
    assert registry.get("detect_key").mutates is False
    assert registry.get("detect_chords").mutates is False
