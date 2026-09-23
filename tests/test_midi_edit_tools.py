"""MIDI editing on top of replace_midi_notes, against MockBridge.

Every tool here promises three things, and every test checks at least one:
it changes exactly the notes it says, it is ONE undo step, and undo puts the
notes back exactly — not approximately. A tool that fails validation must
leave nothing behind: no changed note, no undo entry, no journal entry.
"""
import pytest

from stem.agent.loop import SYSTEM, ToolContext
from stem.bridge.base import MidiNote
from stem.bridge.mock import MockBridge
from stem.tools.core import registry
from stem.tools import midi_edit
from stem.tools.verification import journal_for


@pytest.fixture
def ctx():
    return ToolContext(bridge=MockBridge())


def run(ctx, tool, **kwargs):
    return registry.execute(tool, kwargs, ctx)


def rows(ctx, track_id):
    return sorted((n.start_beat, n.pitch, n.length_beats, n.velocity, n.channel)
                  for n in ctx.bridge.get_midi_notes(track_id))


def track_with(ctx, notes, name="Keys"):
    track_id = run(ctx, "create_midi_track", name=name)["track_id"]
    r = run(ctx, "insert_midi_notes", track_id=track_id, notes=[
        {"pitch": p, "start_beat": s, "length_beats": l, "velocity": v}
        for (p, s, l, v) in notes])
    assert "error" not in r
    return track_id


MELODY = [(60, 0.0, 1.0, 100), (62, 1.0, 1.0, 100), (64, 2.0, 1.0, 100),
          (65, 3.0, 1.0, 100)]


def assert_one_step_and_undo(ctx, track_id, result, before_rows):
    """The shared contract: one undo entry, verified, undo is exact."""
    assert "error" not in result, result
    assert result["verified"]["checked"] is True
    assert result["verified"]["changed"] is True
    assert result["undo"]["available"] is True
    assert ctx.bridge._undo_stack[-1][0] == result["action_id"]
    assert rows(ctx, track_id) != before_rows
    undo = run(ctx, "undo", action_id=result["action_id"])
    assert undo["undone"] is True and undo["restored"] is True
    assert rows(ctx, track_id) == before_rows


def assert_nothing_happened(ctx, track_id, result, before_rows, stack_len,
                            journal_len):
    assert "error" in result, result
    assert rows(ctx, track_id) == before_rows
    assert len(ctx.bridge._undo_stack) == stack_len
    assert len(journal_for(ctx.bridge).entries) == journal_len


def snapshot(ctx, track_id):
    return (rows(ctx, track_id), len(ctx.bridge._undo_stack),
            len(journal_for(ctx.bridge).entries))


# ============ the bridge primitive ============

def test_replace_removes_only_the_range_and_writes_absolute_positions():
    b = MockBridge()
    t, _ = b.create_midi_track("K")
    b.insert_midi_notes(t, [MidiNote(60, 0, 1), MidiNote(62, 1, 1),
                            MidiNote(64, 2, 1)])
    depth = len(b._undo_stack)
    aid = b.replace_midi_notes(t, [MidiNote(70, 1.5, 0.5)], 1.0, 2.0)
    assert len(b._undo_stack) == depth + 1 and b._undo_stack[-1][0] == aid
    got = sorted((n.start_beat, n.pitch) for n in b.get_midi_notes(t))
    assert got == [(0, 60), (1.5, 70), (2, 64)]     # 1.5 is absolute, not 1+1.5


def test_replace_open_end_and_new_notes_outside_the_range():
    b = MockBridge()
    t, _ = b.create_midi_track("K")
    b.insert_midi_notes(t, [MidiNote(60, 0, 1), MidiNote(62, 3.97, 1)])
    b.replace_midi_notes(t, [MidiNote(62, 4.0, 1)], 3.0)          # end None
    assert sorted((n.start_beat, n.pitch) for n in b.get_midi_notes(t)) == \
        [(0, 60), (4.0, 62)]


def test_replace_accepts_the_lua_dict_shape():
    b = MockBridge()
    t, _ = b.create_midi_track("K")
    b.replace_midi_notes(t, [{"pitch": 61, "start_beat": 0.5,
                              "length_beats": 1, "velocity": 90,
                              "channel": 9}])
    [n] = b.get_midi_notes(t)
    assert (n.pitch, n.velocity, n.channel) == (61, 90, 9)


@pytest.mark.parametrize("call", [
    lambda b, t, a: b.replace_midi_notes("nope", []),
    lambda b, t, a: b.replace_midi_notes(a, []),
    lambda b, t, a: b.replace_midi_notes(t, [], 2.0, 2.0),
    lambda b, t, a: b.replace_midi_notes(t, [], -1.0),
    lambda b, t, a: b.replace_midi_notes(t, [MidiNote(128, 0, 1)]),
    lambda b, t, a: b.replace_midi_notes(t, [MidiNote(60, 0, 0)]),
    lambda b, t, a: b.replace_midi_notes(t, [MidiNote(60, -1, 1)]),
    lambda b, t, a: b.replace_midi_notes(t, [MidiNote(60, 0, 1, velocity=0)]),
])
def test_replace_validation_failure_leaves_no_checkpoint(call):
    b = MockBridge()
    t, _ = b.create_midi_track("K")
    b.insert_midi_notes(t, [MidiNote(60, 0, 1)])
    a = b.import_audio("", "/tmp/x.wav")
    depth, before = len(b._undo_stack), list(b.get_midi_notes(t))
    with pytest.raises((KeyError, ValueError)):
        call(b, t, a and next(k for k, v in b.tracks.items() if v.kind == "audio"))
    assert len(b._undo_stack) == depth
    assert b.get_midi_notes(t) == before


def test_replace_window_cuts_in_gaps_not_on_notes():
    notes = [MidiNote(60, 0, 1), MidiNote(62, 1, 1), MidiNote(64, 2, 1),
             MidiNote(65, 3, 1)]
    assert midi_edit.replace_window(notes, {1}) == (0.5, 1.5)
    assert midi_edit.replace_window(notes, {0}) == (0.0, 0.5)
    assert midi_edit.replace_window(notes, {3}) == (2.5, None)
    assert midi_edit.replace_window(notes, {1, 2}) == (0.5, 2.5)


# ============ edit_midi_notes ============

def test_edit_changes_each_field_of_one_note(ctx):
    t = track_with(ctx, MELODY)
    before = rows(ctx, t)
    r = run(ctx, "edit_midi_notes", track_id=t, edits=[
        {"pitch": 62, "start_beat": 1.0, "new_pitch": 63, "new_velocity": 70,
         "new_start_beat": 1.5, "new_length_beats": 0.5}])
    assert r["notes_changed"] == 1
    assert (1.5, 63, 0.5, 70, 0) in rows(ctx, t)
    assert (1.0, 62, 1.0, 100, 0) not in rows(ctx, t)
    assert len(rows(ctx, t)) == 4          # neighbours untouched, no dupes
    assert_one_step_and_undo(ctx, t, r, before)


def test_edit_delete_and_change_in_one_step(ctx):
    t = track_with(ctx, MELODY)
    before = rows(ctx, t)
    depth = len(ctx.bridge._undo_stack)
    r = run(ctx, "edit_midi_notes", track_id=t, edits=[
        {"pitch": 60, "start_beat": 0, "delete": True},
        {"pitch": 65, "start_beat": 3, "new_velocity": 40}])
    assert len(ctx.bridge._undo_stack) == depth + 1
    assert r["notes_deleted"] == 1 and r["notes_changed"] == 1
    assert [row[1] for row in rows(ctx, t)] == [62, 64, 65]
    assert_one_step_and_undo(ctx, t, r, before)


def test_edit_touches_only_one_of_two_stacked_identical_notes(ctx):
    t = track_with(ctx, [(60, 0, 1, 100), (60, 0, 1, 100), (64, 1, 1, 100)])
    before = rows(ctx, t)
    r = run(ctx, "edit_midi_notes", track_id=t, edits=[
        {"pitch": 60, "start_beat": 0, "new_velocity": 50}])
    assert sorted(v for (_, p, _, v, _) in rows(ctx, t) if p == 60) == [50, 100]
    assert_one_step_and_undo(ctx, t, r, before)


def test_edit_bulk_velocity_on_a_range(ctx):
    t = track_with(ctx, MELODY)
    before = rows(ctx, t)
    r = run(ctx, "edit_midi_notes", track_id=t,
            select={"start_beat": 1, "end_beat": 3},
            change={"velocity_delta": -30})
    assert [v for (*_, v, _) in rows(ctx, t)] == [100, 70, 70, 100]
    assert_one_step_and_undo(ctx, t, r, before)


def test_edit_bulk_delete_and_shift(ctx):
    t = track_with(ctx, MELODY)
    before = rows(ctx, t)
    r = run(ctx, "edit_midi_notes", track_id=t, select={"start_beat": 2},
            change={"delete": True})
    assert [row[1] for row in rows(ctx, t)] == [60, 62]
    assert_one_step_and_undo(ctx, t, r, before)
    r = run(ctx, "edit_midi_notes", track_id=t,
            select={"pitch_min": 64}, change={"shift_beats": 4})
    assert [(s, p) for (s, p, *_) in rows(ctx, t)] == \
        [(0, 60), (1, 62), (6, 64), (7, 65)]
    assert_one_step_and_undo(ctx, t, r, before)


@pytest.mark.parametrize("kwargs", [
    {"edits": [{"pitch": 61, "start_beat": 0, "delete": True}]},  # no such note
    {"select": {"start_beat": 10}, "change": {"delete": True}},   # empty sel
    {"select": {}, "change": {"shift_beats": -1}},               # before beat 0
])
def test_edit_that_cannot_apply_changes_nothing(ctx, kwargs):
    t = track_with(ctx, MELODY)
    snap = snapshot(ctx, t)
    r = run(ctx, "edit_midi_notes", track_id=t, **kwargs)
    assert_nothing_happened(ctx, t, r, *snap)


@pytest.mark.parametrize("kwargs", [
    {"edits": [{"pitch": 60, "start_beat": 0}]},                     # no change
    {"edits": [{"pitch": 60, "start_beat": 0, "delete": True,
                "new_velocity": 3}]},                                # both
    {"edits": [{"pitch": 60, "start_beat": 0, "new_pitch": 128}]},   # range
    {"select": {"start_beat": 0}},                                   # no change
    {"select": {"start_beat": 3, "end_beat": 1}, "change": {"delete": True}},
    {"edits": [{"pitch": 60, "start_beat": 0, "delete": True}],
     "select": {}, "change": {"delete": True}},                      # two modes
    {},
])
def test_edit_invalid_input_is_refused_at_the_gate(ctx, kwargs):
    t = track_with(ctx, MELODY)
    snap = snapshot(ctx, t)
    r = run(ctx, "edit_midi_notes", track_id=t, **kwargs)
    assert r["error"].startswith("invalid input")
    assert_nothing_happened(ctx, t, r, *snap)


def test_edit_refuses_audio_tracks_and_unknown_tracks(ctx):
    ctx.bridge.import_audio("", "/tmp/a.wav")
    audio = next(k for k, v in ctx.bridge.tracks.items() if v.kind == "audio")
    depth = len(ctx.bridge._undo_stack)
    for tid in (audio, "trk_missing"):
        r = run(ctx, "edit_midi_notes", track_id=tid,
                select={}, change={"delete": True})
        assert "error" in r
    assert len(ctx.bridge._undo_stack) == depth


def test_an_edit_that_matches_already_is_a_noop_not_an_undo_entry(ctx):
    t = track_with(ctx, MELODY)
    snap = snapshot(ctx, t)
    r = run(ctx, "edit_midi_notes", track_id=t, edits=[
        {"pitch": 60, "start_beat": 0, "new_velocity": 100}])
    assert r["noop"] is True and "action_id" not in r
    assert r["undo"]["available"] is False
    assert r["verified"]["changed"] is False
    assert snapshot(ctx, t) == snap


# ============ quantize ============

SLOPPY = [(60, 0.03, 0.5, 100), (62, 0.49, 0.5, 100), (64, 1.02, 0.5, 100),
          (65, 1.46, 0.5, 100)]


def test_quantize_full_strength(ctx):
    t = track_with(ctx, SLOPPY)
    before = rows(ctx, t)
    r = run(ctx, "quantize", track_id=t, grid=0.5)
    assert [s for (s, *_) in rows(ctx, t)] == [0.0, 0.5, 1.0, 1.5]
    assert r["notes_changed"] == 4
    assert_one_step_and_undo(ctx, t, r, before)


def test_quantize_half_strength_moves_halfway(ctx):
    t = track_with(ctx, SLOPPY)
    before = rows(ctx, t)
    r = run(ctx, "quantize", track_id=t, grid=0.5, strength=0.5)
    assert [s for (s, *_) in rows(ctx, t)] == pytest.approx(
        [0.015, 0.495, 1.01, 1.48])
    assert_one_step_and_undo(ctx, t, r, before)


def test_quantize_swing_delays_the_offbeats(ctx):
    t = track_with(ctx, [(60, 0, 0.25, 100), (60, 0.5, 0.25, 100),
                         (60, 1.0, 0.25, 100), (60, 1.5, 0.25, 100)])
    before = rows(ctx, t)
    r = run(ctx, "quantize", track_id=t, grid=0.5, swing=0.33)
    assert [s for (s, *_) in rows(ctx, t)] == pytest.approx(
        [0.0, 0.665, 1.0, 1.665])
    assert_one_step_and_undo(ctx, t, r, before)


def test_quantize_only_the_range_and_leaves_the_rest_exact(ctx):
    t = track_with(ctx, SLOPPY)
    before = rows(ctx, t)
    r = run(ctx, "quantize", track_id=t, grid=0.5, start_beat=1.0)
    starts = [s for (s, *_) in rows(ctx, t)]
    assert starts == [0.03, 0.49, 1.0, 1.5]
    assert r["notes_considered"] == 2
    assert_one_step_and_undo(ctx, t, r, before)


def test_quantize_length_snaps_ends(ctx):
    t = track_with(ctx, [(60, 0.02, 0.9, 100)])
    before = rows(ctx, t)
    r = run(ctx, "quantize", track_id=t, grid=0.25, quantize_length=True)
    assert rows(ctx, t) == [(0.0, 60, 1.0, 100, 0)]
    assert_one_step_and_undo(ctx, t, r, before)


def test_quantizing_quantized_notes_is_a_noop(ctx):
    t = track_with(ctx, MELODY)
    snap = snapshot(ctx, t)
    r = run(ctx, "quantize", track_id=t, grid=0.25)
    assert r["noop"] is True
    assert r["undo"] == {"available": False, "reason": registry_noop_reason()}
    assert snapshot(ctx, t) == snap


def registry_noop_reason():
    from stem.tools.registry import NOOP_REASON
    return NOOP_REASON


@pytest.mark.parametrize("kwargs", [
    {"grid": 0}, {"strength": 1.5}, {"swing": 0.9},
    {"start_beat": 4, "end_beat": 2},
])
def test_quantize_invalid_input_leaves_no_undo_entry(ctx, kwargs):
    t = track_with(ctx, SLOPPY)
    snap = snapshot(ctx, t)
    r = run(ctx, "quantize", track_id=t, **kwargs)
    assert_nothing_happened(ctx, t, r, *snap)


def test_quantize_reports_collisions(ctx):
    t = track_with(ctx, [(60, 0.0, 0.25, 100), (60, 0.05, 0.25, 100)])
    r = run(ctx, "quantize", track_id=t, grid=0.5)
    assert r["collisions"] == 1


# ============ transpose ============

def test_transpose_semitones(ctx):
    t = track_with(ctx, MELODY)
    before = rows(ctx, t)
    r = run(ctx, "transpose", track_id=t, semitones=12)
    assert [p for (_, p, *_) in rows(ctx, t)] == [72, 74, 76, 77]
    assert_one_step_and_undo(ctx, t, r, before)


def test_transpose_diatonic_in_a_given_key(ctx):
    # C major up a third: C->E, D->F, E->G, F->A (not a fixed 4 semitones)
    t = track_with(ctx, MELODY)
    before = rows(ctx, t)
    r = run(ctx, "transpose", track_id=t, scale_steps=2, key="C",
            scale="major")
    assert [p for (_, p, *_) in rows(ctx, t)] == [64, 65, 67, 69]
    assert r["key"] == "C major"
    assert_one_step_and_undo(ctx, t, r, before)


def test_diatonic_shift_wraps_octaves_and_keeps_alterations():
    major = [0, 2, 4, 5, 7, 9, 11]
    assert midi_edit.diatonic_shift(71, 1, 0, major) == 72     # B -> C above
    assert midi_edit.diatonic_shift(60, -1, 0, major) == 59    # C -> B below
    assert midi_edit.diatonic_shift(60, 7, 0, major) == 72     # up an octave
    assert midi_edit.diatonic_shift(61, 1, 0, major) == 63     # C# -> D#
    minor = [0, 2, 3, 5, 7, 8, 10]
    assert midi_edit.diatonic_shift(57, 2, 9, minor) == 60     # A minor: A->C


def test_transpose_diatonic_detects_the_key_when_not_given(ctx):
    # A natural minor, weighted to A: detection must land on A minor
    notes = [(57, 0, 2, 100), (60, 2, 1, 100), (64, 3, 1, 100),
             (62, 4, 1, 100), (59, 5, 1, 100), (57, 6, 2, 100),
             (65, 8, 1, 100), (67, 9, 1, 100), (57, 10, 2, 100)]
    t = track_with(ctx, notes)
    before = rows(ctx, t)
    r = run(ctx, "transpose", track_id=t, scale_steps=1)
    assert r["key_detected"] is True
    assert r["key"] in ("A minor", "C major")      # relative keys: same notes
    # every note stays in the A-minor/C-major collection
    assert all(p % 12 in {0, 2, 4, 5, 7, 9, 11} for (_, p, *_) in rows(ctx, t))
    assert_one_step_and_undo(ctx, t, r, before)


def test_transpose_out_of_range_refuses_the_whole_edit(ctx):
    t = track_with(ctx, [(120, 0, 1, 100), (60, 1, 1, 100)])
    snap = snapshot(ctx, t)
    r = run(ctx, "transpose", track_id=t, semitones=12)
    assert "MIDI range" in r["error"]
    assert_nothing_happened(ctx, t, r, *snap)


@pytest.mark.parametrize("kwargs", [
    {}, {"semitones": 2, "scale_steps": 1}, {"semitones": 2, "key": "C"},
    {"scale_steps": 1, "scale": "klingon", "key": "C"},
    {"scale_steps": 1, "scale": "minor"},
])
def test_transpose_invalid_input(ctx, kwargs):
    t = track_with(ctx, MELODY)
    snap = snapshot(ctx, t)
    r = run(ctx, "transpose", track_id=t, **kwargs)
    assert_nothing_happened(ctx, t, r, *snap)


def test_transpose_range_and_pitch_filter(ctx):
    t = track_with(ctx, MELODY)
    before = rows(ctx, t)
    r = run(ctx, "transpose", track_id=t, semitones=-12, pitch_max=62)
    assert [p for (_, p, *_) in rows(ctx, t)] == [48, 50, 64, 65]
    assert_one_step_and_undo(ctx, t, r, before)


# ============ humanize ============

GRID = [(60, float(i) * 0.5, 0.5, 100) for i in range(16)]


def test_humanize_is_bounded_seeded_and_one_step(ctx):
    t = track_with(ctx, GRID)
    before = rows(ctx, t)
    r = run(ctx, "humanize", track_id=t, timing_beats=0.03, velocity=10,
            seed=7)
    after = rows(ctx, t)
    for (s0, _, _, v0, _), (s1, _, _, v1, _) in zip(before, after):
        assert abs(s1 - s0) <= 0.03 + 1e-9 and abs(v1 - v0) <= 10
    assert r["seed"] == 7
    assert_one_step_and_undo(ctx, t, r, before)
    r2 = run(ctx, "humanize", track_id=t, timing_beats=0.03, velocity=10,
             seed=7)
    assert rows(ctx, t) == after                       # same seed, same result
    run(ctx, "undo", action_id=r2["action_id"])


def test_humanize_invalid_input(ctx):
    t = track_with(ctx, GRID)
    snap = snapshot(ctx, t)
    for kwargs in ({"timing_beats": 0, "velocity": 0}, {"timing_beats": 1}):
        r = run(ctx, "humanize", track_id=t, **kwargs)
        assert_nothing_happened(ctx, t, r, *snap)


# ============ duplicate_notes ============

def test_duplicate_repeats_a_bar_after_itself(ctx):
    t = track_with(ctx, MELODY)
    before = rows(ctx, t)
    r = run(ctx, "duplicate_notes", track_id=t, start_beat=0, end_beat=4,
            times=2)
    assert len(rows(ctx, t)) == 12
    assert [p for (s, p, *_) in rows(ctx, t) if s >= 8] == [60, 62, 64, 65]
    assert_one_step_and_undo(ctx, t, r, before)


def test_duplicate_replace_existing_clears_the_destination(ctx):
    t = track_with(ctx, MELODY + [(40, 4.5, 1, 100), (41, 9, 1, 100)])
    before = rows(ctx, t)
    r = run(ctx, "duplicate_notes", track_id=t, start_beat=0, end_beat=4,
            replace_existing=True)
    got = rows(ctx, t)
    assert (4.5, 40, 1, 100, 0) not in got        # cleared
    assert (9, 41, 1, 100, 0) in got              # outside destination: kept
    assert r["cleared"] == 1
    assert_one_step_and_undo(ctx, t, r, before)


@pytest.mark.parametrize("kwargs", [
    {"start_beat": 0, "end_beat": 4, "destination_beat": 2},    # overlap
    {"start_beat": 4, "end_beat": 4},
    {"start_beat": 0, "end_beat": 4, "times": 0},
])
def test_duplicate_invalid_input(ctx, kwargs):
    t = track_with(ctx, MELODY)
    snap = snapshot(ctx, t)
    r = run(ctx, "duplicate_notes", track_id=t, **kwargs)
    assert_nothing_happened(ctx, t, r, *snap)


def test_duplicate_empty_source_is_an_error_not_an_entry(ctx):
    t = track_with(ctx, MELODY)
    snap = snapshot(ctx, t)
    r = run(ctx, "duplicate_notes", track_id=t, start_beat=20, end_beat=24)
    assert_nothing_happened(ctx, t, r, *snap)


# ============ add_instrument ============

def test_add_instrument_is_one_step_and_undo_removes_it(ctx):
    t = run(ctx, "create_midi_track", name="Bare")["track_id"]
    r = run(ctx, "add_instrument", track_id=t)
    assert r["added"] is True and r["undo"]["available"] is True
    assert "Stem's undo" in r["undo_note"]
    assert ctx.bridge.tracks[t].plugins
    u = run(ctx, "undo", action_id=r["action_id"])
    assert u["restored"] is True and ctx.bridge.tracks[t].plugins == []


def test_add_instrument_on_a_track_that_has_one_is_a_noop(ctx):
    t = run(ctx, "create_midi_track", name="P",
            instrument_id="mock.piano")["track_id"]
    depth = len(ctx.bridge._undo_stack)
    r = run(ctx, "add_instrument", track_id=t)
    assert r["noop"] is True and r["undo"]["available"] is False
    assert len(ctx.bridge._undo_stack) == depth


def test_add_instrument_refuses_audio_and_missing_tracks(ctx):
    ctx.bridge.import_audio("", "/tmp/a.wav")
    audio = next(k for k, v in ctx.bridge.tracks.items() if v.kind == "audio")
    depth = len(ctx.bridge._undo_stack)
    for tid in (audio, "trk_missing"):
        assert "error" in run(ctx, "add_instrument", track_id=tid)
    assert len(ctx.bridge._undo_stack) == depth


# ============ every new tool is one step, undo is exact ============

@pytest.mark.parametrize("tool,kwargs", [
    ("edit_midi_notes", {"select": {}, "change": {"velocity": 64}}),
    ("quantize", {"grid": 0.5}),
    ("transpose", {"semitones": 3}),
    ("humanize", {"seed": 1}),
    ("duplicate_notes", {"start_beat": 0, "end_beat": 2}),
])
def test_undo_after_other_edits_restores_exactly_the_prior_notes(ctx, tool,
                                                                 kwargs):
    """Undo of the newest edit restores the state between edits, and never
    touches the earlier ones."""
    t = track_with(ctx, SLOPPY)
    run(ctx, "set_tempo", bpm=97)
    first = run(ctx, "edit_midi_notes", track_id=t, edits=[
        {"pitch": 60, "start_beat": 0.03, "new_velocity": 90}])
    middle = rows(ctx, t)
    depth = len(ctx.bridge._undo_stack)
    r = run(ctx, tool, track_id=t, **kwargs)
    assert len(ctx.bridge._undo_stack) == depth + 1
    u = run(ctx, "undo")
    assert u["restored"] is True
    assert rows(ctx, t) == middle and ctx.bridge.tempo == 97
    assert journal_for(ctx.bridge).get(None).action_id == first["action_id"]


def test_system_prompt_tells_the_agent_to_read_verification():
    assert "verified" in SYSTEM and "restored" in SYSTEM
    assert "undo.available" in SYSTEM
