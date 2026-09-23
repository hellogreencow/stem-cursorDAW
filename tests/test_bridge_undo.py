"""Undo, made real: behavioural tests of the bridge's undo model.

Background (Ardour source, cited in bridge_impl.lua's UNDO block):
  * Lua cannot see Ardour's undo history, and Editor:undo(1) pops whatever is
    on top. The old handlers.undo called it blindly while most mutations put
    nothing on the stack — so "undo" undid the USER's own previous edit.
  * Controllable writes, track creation/removal and processor changes are
    never on Ardour's undo stack at all.
  * Reversible commands cannot nest: begin inside an open command aborts both.

The bridge now records one journal entry per Stem mutation with its exact
inverse, and handlers.undo applies that inverse (never Editor:undo).

These tests run the real bridge_impl.lua through a SEQUENCE of steps in one
Lua state against tests/lua/mock_ardour.lua, which models Ardour's history
semantics, controllables, playlists, regions, MIDI models, locations and the
tempo map, plus the user's own GUI edits. Point STEM_BRIDGE_IMPL_UNDER_TEST at
another copy of the bridge to run them against it (that is how the pre-fix
failure count in LUA_UNDO.md was measured).

Nothing here ran against a live Ardour.
"""

import pytest

from .luaharness import FLUSH, RELOAD, call, requires_lua, run_script, user

pytestmark = requires_lua

N1 = {"pitch": 60, "start_beat": 0.0, "length_beats": 1.0, "velocity": 100}
N2 = {"pitch": 64, "start_beat": 1.0, "length_beats": 1.0, "velocity": 90, "channel": 2}
N3 = {"pitch": 67, "start_beat": 2.5, "length_beats": 0.5, "velocity": 80}


def world(state):
    """The session as a user would judge it: tracks, notes, gain, mute,
    instruments, tempo, markers. Not the undo history (a Stem undo adds a
    compensating command to it, by design) and not the journal."""
    return {"routes": state["routes"], "tempo": state["tempo"],
            "markers": state["markers"]}


def ticks(beats):
    return round(beats * 1920)


def assert_clean(step, why=""):
    h = step.state["history"]
    assert h["open"] == "", f"a reversible command was left open ({h['open']}) {why}"
    assert h["warnings"] == [], f"Ardour would have logged: {h['warnings']} {why}"


# a user-drawn region with one user note on Chords, both on Ardour's stack
USER_SETUP = [
    user("draw_region", track="Chords", samples=480000),
    user("add_note", track="Chords", pitch=48, start_beat=3.0, length_beats=1.0, velocity=70),
]

# every mutating handler the Python side records an action for, plus the two
# processor handlers when journaling is requested
MUTATIONS = {
    "create_midi_track": ([], call("create_midi_track", {"name": "Keys"}), {}),
    "insert_notes_new_region": ([], call("insert_midi_notes",
                                         {"track_id": "Drums", "notes": [N1, N2]}), {}),
    "insert_notes_existing_region": (USER_SETUP, call("insert_midi_notes",
                                     {"track_id": "Chords", "notes": [N1, N2, N3]}), {}),
    "insert_notes_grows_region": (USER_SETUP, call("insert_midi_notes",
                                  {"track_id": "Chords",
                                   "notes": [dict(N1, start_beat=40.0)]}), {}),
    "insert_notes_fork_region": ([], call("insert_midi_notes",
                                          {"track_id": "Drums", "notes": [N1]}), {"fork": True}),
    "replace_notes": (USER_SETUP + [call("insert_midi_notes",
                                         {"track_id": "Chords", "notes": [N1, N2, N3]})],
                      call("replace_midi_notes",
                           {"track_id": "Chords", "notes": [dict(N1, pitch=62)],
                            "start_beat": 0.0, "end_beat": 2.0}), {}),
    "set_tempo_stock": ([], call("set_tempo", {"bpm": 90}), {}),
    "set_tempo_fork": ([], call("set_tempo", {"bpm": 90}), {"fork": True}),
    "set_track_gain": ([], call("set_track_gain", {"track_id": "Chords", "gain_db": -6.0}), {}),
    "set_track_gain_rt_queued": ([], call("set_track_gain",
                                          {"track_id": "Chords", "gain_db": -6.0}), {"rt_queue": True}),
    "set_track_mute": ([], call("set_track_mute", {"track_id": "Drums", "muted": True}), {}),
    "add_marker": ([], call("add_marker", {"position_seconds": 4, "name": "Drop"}), {}),
    "import_audio_stock": ([], call("import_audio", {"file_path": "/tmp/x.wav"}), {}),
    "import_audio_fork": ([], call("import_audio", {"file_path": "/tmp/x.wav"}), {"fork": True}),
    "add_instrument_journaled": ([], call("add_instrument",
                                          {"track_id": "Chords", "journal": True}), {}),
    "fix_silent_instruments_journaled": ([], call("fix_silent_instruments",
                                                  {"journal": True}), {}),
}


# ======================================================================
# (a) every mutation followed by undo restores the prior state
# ======================================================================

@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_mutation_then_undo_restores_the_prior_state(tmp_path, name):
    setup, mutation, profile = MUTATIONS[name]
    steps, _ = run_script(tmp_path, setup + [call("ping"), mutation, FLUSH,
                                             call("undo"), FLUSH], **profile)
    before, done, undone = steps[len(setup)], steps[len(setup) + 1], steps[-1]
    assert done.error is None and done.result is not None, done.response
    landed = steps[len(setup) + 2]   # after queued RT writes are delivered
    assert world(landed.state) != world(before.state), \
        f"{name} changed nothing, so this test proves nothing"
    assert world(undone.state) == world(before.state), (
        f"undo after {name} did not restore the prior state")
    assert_clean(undone, f"after undoing {name}")
    assert steps[-2].result.get("ok") is True, steps[-2].response
    assert done.result.get("action_id"), f"{name} returned no action id: {done.result}"


def test_every_mutation_in_sequence_unwinds_one_step_at_a_time(tmp_path):
    """Seven different Stem actions, then seven undos: each undo restores
    exactly the state before its own action, newest first."""
    actions = [
        call("create_midi_track", {"name": "Keys"}),
        call("insert_midi_notes", {"track_id": "Keys", "notes": [N1, N2]}),
        call("set_tempo", {"bpm": 97}),
        call("set_track_gain", {"track_id": "Keys", "gain_db": -3.0}),
        call("set_track_mute", {"track_id": "Drums", "muted": True}),
        call("add_marker", {"position_seconds": 2, "name": "Verse"}),
        call("replace_midi_notes", {"track_id": "Keys", "notes": [N3], "start_beat": 0.0}),
    ]
    steps, _ = run_script(tmp_path, [call("ping")] + actions + [call("undo")] * len(actions))
    snapshots = [world(s.state) for s in steps[:len(actions) + 1]]
    for k in range(len(actions)):
        after_undo = world(steps[len(actions) + 1 + k].state)
        assert after_undo == snapshots[len(actions) - 1 - k], f"undo #{k + 1} restored the wrong state"
    assert steps[-1].state["journal_depth"] == 0
    assert_clean(steps[-1])


# ======================================================================
# (b) undo after a Stem mutation never touches the user's own edits
# ======================================================================

@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_undo_never_pops_a_pre_existing_user_edit(tmp_path, name):
    """The user drew a region and a note (both on Ardour's undo stack), then
    Stem acted, then Stem undid. The user's note and their two history
    entries must be exactly where they were."""
    setup, mutation, profile = MUTATIONS[name]
    user_edits = [user("draw_region", track="Drums", samples=480000),
                  user("add_note", track="Drums", pitch=36, start_beat=0.0,
                       length_beats=0.5, velocity=110)]
    if mutation["json"].find('"Drums"') != -1 or name.startswith("fix_silent"):
        # keep the user's edit on a track the mutation does not touch
        user_edits = [user("draw_region", track="Chords", samples=480000),
                      user("add_note", track="Chords", pitch=36, start_beat=0.0,
                           length_beats=0.5, velocity=110)]
        if name.startswith("fix_silent") or name.startswith("insert_notes_exist") \
                or name == "replace_notes":
            user_edits = [user("add_marker", samples=12345)]
    steps, _ = run_script(tmp_path, setup + user_edits + [call("ping"), mutation,
                                                          FLUSH, call("undo"), FLUSH], **profile)
    before = steps[len(setup) + len(user_edits)]
    undone = steps[-1]
    assert world(undone.state) == world(before.state)
    hist_before = before.state["history"]["undo"]
    hist_after = undone.state["history"]["undo"]
    assert hist_after[:len(hist_before)] == hist_before, (
        f"Stem's undo removed the user's own history entries: "
        f"{hist_before} -> {hist_after}")
    assert undone.state["history"]["redo"] == [], "Stem's undo popped something off Ardour's stack"
    assert_clean(undone)


def test_undo_with_nothing_of_stems_left_leaves_the_user_alone(tmp_path):
    """The exact old bug: no Stem action on record, the user has edits on the
    stack, "undo" arrives. It must refuse and touch nothing."""
    steps, calls = run_script(tmp_path, USER_SETUP + [call("undo")])
    before, undone = steps[-2], steps[-1]
    assert undone.result.get("ok") is False
    assert "nothing" in undone.result.get("error", "").lower()
    assert world(undone.state) == world(before.state), "undo removed the user's own note"
    assert undone.state["history"] == before.state["history"]
    assert not any(c.startswith("Editor:undo") for c in calls)


def test_user_edit_made_after_stem_survives_stems_undo(tmp_path):
    """Stem sets the tempo (nothing on Ardour's stack for that on stock
    Ardour), then the user adds a note. The old Editor:undo(1) removed the
    user's note and left the tempo; the right answer is the reverse."""
    steps, _ = run_script(tmp_path, [
        user("draw_region", track="Chords"),
        call("set_tempo", {"bpm": 90}),
        user("add_note", track="Chords", pitch=50, start_beat=1.0, length_beats=1.0),
        call("undo"),
    ])
    undone = steps[-1]
    assert undone.state["tempo"] == 120
    assert [n["pitch"] for n in undone.notes("Chords")] == [50], "the user's note was undone"


def test_undo_refuses_rather_than_clobber_a_control_the_user_moved(tmp_path):
    steps, _ = run_script(tmp_path, [
        call("set_track_gain", {"track_id": "Chords", "gain_db": -6.0}),
        user("set_gain", track="Chords", value=0.25),
        call("undo"),
    ])
    undone = steps[-1]
    assert undone.result.get("ok") is False
    assert "changed since" in undone.result["error"]
    assert undone.track("Chords")["gain"] == 0.25, "undo overwrote the user's fader move"
    assert undone.state["journal_depth"] == 0, "a refused entry is dropped (stays aligned with Python)"


def test_undo_refuses_when_the_user_edited_stems_notes(tmp_path):
    steps, _ = run_script(tmp_path, USER_SETUP + [
        call("insert_midi_notes", {"track_id": "Chords", "notes": [N1, N2]}),
        user("delete_note", track="Chords", pitch=64, start_beat=1.0),
        call("undo"),
    ])
    before_undo, undone = steps[-2], steps[-1]
    assert undone.result.get("ok") is False
    assert world(undone.state) == world(before_undo.state)
    assert_clean(undone)


def test_undo_of_a_track_that_gained_user_content_refuses(tmp_path):
    steps, _ = run_script(tmp_path, [
        call("create_midi_track", {"name": "Keys"}),
        user("draw_region", track="Keys"),
        call("undo"),
    ])
    undone = steps[-1]
    assert undone.result.get("ok") is False
    assert undone.track("Keys") is not None, "undo deleted a track holding the user's region"


def test_undo_never_calls_editor_undo(tmp_path):
    _, calls = run_script(tmp_path, [
        call(m["method"], __import__("json").loads(m["json"]))
        for _, m, p in MUTATIONS.values() if not p
    ] + [call("undo")] * 20)
    assert not any(c.startswith("Editor:undo") for c in calls)


# ======================================================================
# (c) a failing mutation leaves no open command and no journal entry
# ======================================================================

FAILURES = {
    "gain_missing_track": ([], call("set_track_gain", {"track_id": "nope", "gain_db": -3}), {}),
    "gain_not_a_number": ([], call("set_track_gain", {"track_id": "Chords", "gain_db": "loud"}), {}),
    "mute_missing_track": ([], call("set_track_mute", {"track_id": "nope", "muted": True}), {}),
    "tempo_negative": ([], call("set_tempo", {"bpm": -1}), {}),
    "tempo_write_fails": ([], call("set_tempo", {"bpm": 90}), {"set_tempo_fails": True}),
    "insert_bad_pitch_on_empty_track": ([], call("insert_midi_notes", {
        "track_id": "Drums", "notes": [N1, dict(N2, pitch=200)]}), {}),
    "insert_diff_fails_after_region_created": ([], call("insert_midi_notes", {
        "track_id": "Drums", "notes": [N1]}), {"diff_apply_fails": True}),
    "insert_diff_fails_after_region_grown": (USER_SETUP, call("insert_midi_notes", {
        "track_id": "Chords", "notes": [dict(N1, start_beat=40.0)]}), {"diff_apply_fails": True}),
    "insert_on_ardour8": ([], call("insert_midi_notes", {
        "track_id": "Chords", "notes": [N1]}), {"ardour": "8"}),
    "replace_end_before_start": (USER_SETUP, call("replace_midi_notes", {
        "track_id": "Chords", "notes": [N1], "start_beat": 4.0, "end_beat": 2.0}), {}),
    "replace_bad_note": (USER_SETUP, call("replace_midi_notes", {
        "track_id": "Chords", "notes": [dict(N1, length_beats=0)]}), {}),
    "replace_diff_fails": (USER_SETUP, call("replace_midi_notes", {
        "track_id": "Chords", "notes": [dict(N1, start_beat=40.0)]}), {"diff_apply_fails": True}),
    "marker_throws": ([], call("add_marker", {"position_seconds": 4, "name": "x"}),
                      {"marker_fails": True}),
    "marker_already_there": ([user("add_marker", samples=4 * 48000)],
                             call("add_marker", {"position_seconds": 4, "name": "x"}), {}),
    "import_no_path": ([], call("import_audio", {}), {}),
    "delete_missing_track": ([], call("delete_track", {"track_id": "nope"}), {}),
}


@pytest.mark.parametrize("name", sorted(FAILURES))
def test_failed_mutation_leaves_nothing_behind(tmp_path, name):
    setup, mutation, profile = FAILURES[name]
    steps, _ = run_script(tmp_path, setup + [call("ping"), mutation], **profile)
    before, failed = steps[-2], steps[-1]
    # a dispatch-level error, so ArdourBridge._call raises and records no
    # action id — the Python sequence and the journal stay one-to-one
    assert failed.error, (
        f"{name} did not fail at dispatch level; ArdourBridge would have "
        f"recorded an undo action for a mutation that did not happen: {failed.response}")
    assert failed.state["journal_depth"] == before.state["journal_depth"]
    assert world(failed.state) == world(before.state), f"{name} left a partial change"
    assert failed.state["history"]["open"] == ""
    assert failed.state["history"]["warnings"] == []


@pytest.mark.parametrize("mutation", [
    call("insert_midi_notes", {"track_id": "Drums", "notes": [N1]}),
    call("replace_midi_notes", {"track_id": "Chords", "notes": [N1]}),
    call("add_marker", {"position_seconds": 3}),
    call("set_tempo", {"bpm": 90}),
], ids=lambda m: m["method"])
def test_mutation_refuses_while_someone_elses_command_is_open(tmp_path, mutation):
    """Beginning a command inside an open one makes Ardour abort BOTH
    (history_owner.cc:68). Stem must not destroy another script's pending
    edit: it refuses up front and leaves that command open and intact."""
    steps, _ = run_script(tmp_path, USER_SETUP + [user("open_dangling_command"),
                                                  call("ping"), mutation])
    before, failed = steps[-2], steps[-1]
    assert failed.error and "unfinished edit" in failed.error
    assert failed.state["history"]["open"] == "dangling (another script)"
    assert failed.state["history"]["warnings"] == []
    assert world(failed.state) == world(before.state)
    assert failed.state["journal_depth"] == 0


def test_tempo_change_is_refused_when_the_current_tempo_cannot_be_read(tmp_path):
    """No readable prior tempo = no exact inverse = no change."""
    steps, _ = run_script(tmp_path, [call("set_tempo", {"bpm": 90})], qpm_fails=True)
    assert steps[-1].error and "could not be undone" in steps[-1].error
    assert steps[-1].state["tempo"] == 120
    assert steps[-1].state["journal_depth"] == 0


def test_failed_mutation_does_not_misalign_the_next_undo(tmp_path):
    """Python records only successful calls. After a failure, one undo must
    undo the last SUCCESSFUL Stem action."""
    steps, _ = run_script(tmp_path, [
        call("set_track_mute", {"track_id": "Drums", "muted": True}),
        call("set_track_gain", {"track_id": "nope", "gain_db": -3}),
        call("undo"),
    ])
    assert steps[-1].result.get("ok") is True
    assert steps[-1].track("Drums")["mute"] == 0


# ======================================================================
# (d) replace_midi_notes is a single undo step
# ======================================================================

def test_replace_midi_notes_is_one_ardour_command_and_one_undo_step(tmp_path):
    steps, _ = run_script(tmp_path, USER_SETUP + [
        call("insert_midi_notes", {"track_id": "Chords", "notes": [N1, N2, N3]}),
        call("ping"),
        call("replace_midi_notes", {"track_id": "Chords",
                                    "notes": [dict(N1, pitch=61), dict(N2, pitch=65)],
                                    "start_beat": 0.0, "end_beat": 2.0}),
        call("undo"),
    ])
    before, replaced, undone = steps[-3], steps[-2], steps[-1]
    assert replaced.result["removed"] == 2 and replaced.result["added"] == 2
    new_entries = replaced.state["history"]["undo"][len(before.state["history"]["undo"]):]
    assert new_entries == ["Stem: replace notes"], (
        f"replace should be exactly one Ardour command, got {new_entries}")
    assert replaced.state["journal_depth"] == before.state["journal_depth"] + 1
    assert undone.notes("Chords") == before.notes("Chords"), "one undo did not restore the exact notes"
    assert undone.state["journal_depth"] == before.state["journal_depth"]


def test_replace_that_grows_the_region_is_still_one_stem_undo_step(tmp_path):
    steps, _ = run_script(tmp_path, USER_SETUP + [
        call("ping"),
        call("replace_midi_notes", {"track_id": "Chords",
                                    "notes": [dict(N1, start_beat=60.0)], "start_beat": 0.0}),
        call("undo"),
    ])
    before, undone = steps[-3], steps[-1]
    assert world(undone.state) == world(before.state)


def test_replace_and_its_undo_round_trip_twice(tmp_path):
    """Undo re-adds the model's own removed NotePtrs; doing it twice must
    still be exact (no duplicated or lost notes)."""
    edit = call("replace_midi_notes", {"track_id": "Chords", "notes": [N3], "start_beat": 0.0})
    steps, _ = run_script(tmp_path, USER_SETUP + [
        call("insert_midi_notes", {"track_id": "Chords", "notes": [N1, N2]}),
        call("ping"), edit, call("undo"), edit, call("undo")])
    assert steps[-1].notes("Chords") == steps[-5].notes("Chords")
    assert steps[-3].notes("Chords") == steps[-5].notes("Chords")


# ======================================================================
# replace_midi_notes contract (the Python side builds against these five)
# ======================================================================

def _chords_with(notes):
    return USER_SETUP[:1] + [call("insert_midi_notes", {"track_id": "Chords", "notes": notes})]


def test_contract_1_new_notes_are_absolute_not_offsets(tmp_path):
    steps, _ = run_script(tmp_path, _chords_with([N1]) + [
        call("replace_midi_notes", {"track_id": "Chords", "start_beat": 4.0, "end_beat": 8.0,
                                    "notes": [dict(N1, start_beat=5.0)]})])
    starts = sorted(n["start"] for n in steps[-1].notes("Chords"))
    assert starts == [0, ticks(5.0)], f"a note sent at beat 5 landed at {starts}"


def test_contract_2_range_selects_by_start_and_missing_end_means_to_the_end(tmp_path):
    notes = [dict(N1, start_beat=0.5, length_beats=2.0),   # starts before: kept, though it overlaps
             dict(N1, pitch=62, start_beat=1.0),           # in range
             dict(N1, pitch=63, start_beat=3.5, length_beats=4.0),  # starts in range, ends past it
             dict(N1, pitch=64, start_beat=4.0),           # at end_beat: kept (half-open)
             dict(N1, pitch=65, start_beat=30.0)]
    bounded, _ = run_script(tmp_path / "a", _chords_with(notes) + [
        call("replace_midi_notes", {"track_id": "Chords", "notes": [],
                                    "start_beat": 1.0, "end_beat": 4.0})])
    assert sorted(n["pitch"] for n in bounded[-1].notes("Chords")) == [60, 64, 65]
    assert bounded[-1].result["removed"] == 2

    open_ended, _ = run_script(tmp_path / "b", _chords_with(notes) + [
        call("replace_midi_notes", {"track_id": "Chords", "notes": [], "start_beat": 1.0})])
    assert sorted(n["pitch"] for n in open_ended[-1].notes("Chords")) == [60]

    explicit_null, _ = run_script(tmp_path / "c", _chords_with(notes) + [
        call("replace_midi_notes", {"track_id": "Chords", "notes": [], "start_beat": 1.0,
                                    "end_beat": None})])
    assert sorted(n["pitch"] for n in explicit_null[-1].notes("Chords")) == [60]

    default_start, _ = run_script(tmp_path / "d", _chords_with(notes) + [
        call("replace_midi_notes", {"track_id": "Chords", "notes": [], "end_beat": 1.0})])
    assert sorted(n["pitch"] for n in default_start[-1].notes("Chords")) == [62, 63, 64, 65]


def test_contract_3_new_notes_outside_the_range_go_in_where_they_say(tmp_path):
    """quantize moves a 3.97 note to 4.0: the range is [0, 4) but the new
    note belongs at 4.0 and must land there."""
    steps, _ = run_script(tmp_path, _chords_with([dict(N1, start_beat=3.97)]) + [
        call("replace_midi_notes", {"track_id": "Chords", "start_beat": 0.0, "end_beat": 4.0,
                                    "notes": [dict(N1, start_beat=4.0)]})])
    assert [n["start"] for n in steps[-1].notes("Chords")] == [ticks(4.0)]


def test_contract_4_note_shape_and_channel_round_trip(tmp_path):
    steps, _ = run_script(tmp_path, _chords_with([]) + [
        call("replace_midi_notes", {"track_id": "Chords", "notes": [
            {"pitch": 70, "start_beat": 1.25, "length_beats": 0.75, "velocity": 33, "channel": 9},
            {"pitch": 71, "start_beat": 2.0, "length_beats": 1.0}]}),   # defaults: vel 100, ch 0
        call("get_midi_notes", {"track_id": "Chords"})])
    got = sorted(steps[-1].result["notes"], key=lambda n: n["pitch"])
    assert got == [
        {"pitch": 70, "start_beat": 1.25, "length_beats": 0.75, "velocity": 33, "channel": 9},
        {"pitch": 71, "start_beat": 2.0, "length_beats": 1.0, "velocity": 100, "channel": 0},
    ]


def test_get_midi_notes_reports_channel_so_read_modify_write_keeps_it(tmp_path):
    steps, _ = run_script(tmp_path, _chords_with([N2]) + [
        call("get_midi_notes", {"track_id": "Chords"})])
    notes = steps[-1].result["notes"]
    assert notes[0]["channel"] == 2
    # feed the read straight back in: nothing may change
    steps2, _ = run_script(tmp_path / "rmw", _chords_with([N2]) + [
        call("replace_midi_notes", {"track_id": "Chords", "notes": notes})])
    assert steps2[-1].notes("Chords") == steps[-2].notes("Chords")


def test_contract_5_returns_an_action_id_and_is_one_undo_step(tmp_path):
    steps, _ = run_script(tmp_path, _chords_with([N1, N2]) + [
        call("replace_midi_notes", {"track_id": "Chords", "notes": [N3]}),
        call("get_undo_journal"), call("undo")])
    action = steps[-3].result["action_id"]
    assert isinstance(action, str) and action
    assert steps[-2].result["entries"][-1]["action_id"] == action
    assert steps[-1].result["action_id"] == action
    assert steps[-1].notes("Chords") == steps[-4].notes("Chords")


def test_replace_on_a_track_with_no_region_and_no_notes_is_a_noop_step(tmp_path):
    steps, _ = run_script(tmp_path, [call("ping"),
        call("replace_midi_notes", {"track_id": "Drums", "notes": []}), call("undo")])
    assert steps[-2].result["action_id"]
    assert world(steps[-1].state) == world(steps[0].state)
    assert steps[-1].result["ok"] is True


# ======================================================================
# the journal itself
# ======================================================================

def test_journal_survives_the_loaders_periodic_reload(tmp_path):
    """stem_bridge.lua re-runs the chunk every 10 s. The journal must live
    through that, or undo would forget everything every ten seconds."""
    steps, _ = run_script(tmp_path, [
        call("set_track_mute", {"track_id": "Drums", "muted": True}),
        RELOAD, RELOAD,
        call("undo")])
    assert steps[-1].result.get("ok") is True
    assert steps[-1].track("Drums")["mute"] == 0


def test_undo_by_action_id_refuses_anything_but_the_newest(tmp_path):
    steps, _ = run_script(tmp_path, [
        call("set_track_mute", {"track_id": "Drums", "muted": True}),
        call("set_track_gain", {"track_id": "Drums", "gain_db": -3}),
    ])
    first = steps[0].result["action_id"]
    second = steps[1].result["action_id"]
    steps, _ = run_script(tmp_path / "x", [
        call("set_track_mute", {"track_id": "Drums", "muted": True}),
        call("set_track_gain", {"track_id": "Drums", "gain_db": -3}),
        call("undo", {"action_id": first}),
        call("undo", {"action_id": second}),
    ])
    assert steps[2].result["ok"] is False and steps[2].state["journal_depth"] == 2
    assert steps[3].result["ok"] is True and steps[3].state["journal_depth"] == 1


def test_stock_tempo_path_no_longer_opens_an_empty_command(tmp_path):
    """The begin/abort_empty pair the old set_tempo put around update()
    recorded nothing: libtemporal has no access to session history."""
    steps, calls = run_script(tmp_path, [call("set_tempo", {"bpm": 90})])
    assert not any(c.startswith("BEGIN:") for c in calls)
    assert steps[-1].result["on_ardour_undo_stack"] is False
    assert steps[-1].result["previous_bpm"] == 120


def test_fork_tempo_is_on_ardours_stack_and_undo_does_not_pop_it(tmp_path):
    steps, _ = run_script(tmp_path, [call("set_tempo", {"bpm": 90}), call("undo")],
                          fork=True)
    assert steps[0].result["on_ardour_undo_stack"] is True
    assert steps[0].state["history"]["undo"] == ["Set Tempo"]
    assert steps[1].state["tempo"] == 120
    # compensating command, nothing popped: Ardour's history stays truthful
    assert steps[1].state["history"]["undo"] == ["Set Tempo", "Set Tempo"]
    assert steps[1].state["history"]["redo"] == []


def test_stem_commands_are_never_nested(tmp_path):
    """Every scripted mutation, back to back: Ardour would log a warning (and
    drop both commands) on any nested begin."""
    seq = [s for setup, m, p in MUTATIONS.values() if not p for s in setup + [m]]
    steps, calls = run_script(tmp_path, seq + [call("undo")] * len(seq))
    assert steps[-1].state["history"]["warnings"] == []
    assert "NESTED_BEGIN_ABORTED_BOTH" not in calls
    assert_clean(steps[-1])
