"""Findings from running the bridge in REAL Ardour (8.12 and 9.8).

Each test here pins one behaviour measured in a live, stock Ardour session
(Debian's 8.12 and 9.8 packages, headless under Xvfb, dummy audio backend,
bridge loaded by stem_bridge.lua as an EditorHook). The mock in
tests/lua/mock_ardour.lua was changed to behave the same way, so these tests
fail against the pre-fix bridge (STEM_BRIDGE_IMPL_UNDER_TEST) and pass now.
See LIVE_ARDOUR.md in the PR for the handler-by-handler record.
"""

from pathlib import Path

from .luaharness import call, call_bridge, requires_lua, run_script, user

BRIDGE = Path(__file__).resolve().parent.parent / "stem" / "bridge" / "bridge_impl.lua"

N1 = {"pitch": 60, "start_beat": 0.0, "length_beats": 1.0, "velocity": 100}

USER_REGION = [
    user("draw_region", track="Chords", samples=480000),
    user("add_note", track="Chords", pitch=48, start_beat=3.0, length_beats=1.0, velocity=70),
]


# ----------------------------------------------------------------------
# 1. collected_undo_commands() is a boolean, not a count
# ----------------------------------------------------------------------
# Live, 8.12 and 9.8: type(Session:collected_undo_commands()) == "boolean";
# false with nothing open or an empty open command, true once it holds a diff.
# The guard tested `type(n) == "number" and n > 0`, so it never fired: with a
# user command left open holding a region mute, insert_midi_notes went ahead
# and the user's pending command did not survive (collected went true->false).

def test_open_command_guard_reads_a_boolean():
    src = BRIDGE.read_text()
    body = src[src.index("local function assert_no_open_command"):]
    body = body[:body.index("\nend\n")]
    assert "n == true" in body


@requires_lua
def test_insert_refuses_while_a_user_command_holds_changes(tmp_path):
    steps, _ = run_script(tmp_path, USER_REGION + [
        user("open_dangling_command"),
        call("ping"),
        call("insert_midi_notes", {"track_id": "Chords", "notes": [dict(N1, start_beat=2.0)]}),
    ])
    before, failed = steps[-2], steps[-1]
    assert failed.error and "unfinished edit" in failed.error
    assert failed.state["history"]["open"] == "dangling (another script)"
    assert failed.state["history"]["warnings"] == []
    assert failed.track("Chords")["regions"] == before.track("Chords")["regions"]
    assert failed.state["journal_depth"] == 0


# ----------------------------------------------------------------------
# 2. MIDI region length is beat time; an audio-time set_length is ignored
# ----------------------------------------------------------------------
# Live, 8.12 (user-drawn region) and 9.8 (region made by the bridge): the
# region length reads "b9600@b0"; region:set_length(Temporal.timecnt_t(456000))
# returned without error and left it at b9600, so a note at beat 16 was written
# past the region end (invisible, inaudible). timecnt_t.from_ticks(...) works.

@requires_lua
def test_notes_past_the_region_end_really_grow_the_region(tmp_path):
    steps, _ = run_script(tmp_path, USER_REGION + [
        call("ping"),
        call("insert_midi_notes", {"track_id": "Chords", "notes": [dict(N1, start_beat=40.0)]}),
    ])
    grown = steps[-1]
    assert grown.error is None, grown.error
    region = grown.track("Chords")["regions"][0]
    # the note ends at beat 41: 41 * 24000 samples at 120 bpm / 48 kHz
    assert region["length"] >= 41 * 24000


@requires_lua
def test_undo_shrinks_the_grown_region_back(tmp_path):
    steps, _ = run_script(tmp_path, USER_REGION + [
        call("ping"),
        call("insert_midi_notes", {"track_id": "Chords", "notes": [dict(N1, start_beat=40.0)]}),
        call("undo"),
    ])
    before, undone = steps[-3], steps[-1]
    assert undone.result["undone"] is True, undone.result
    assert undone.track("Chords")["regions"] == before.track("Chords")["regions"]
    assert undone.track("Chords")["regions"][0]["length"] == 480000


# ----------------------------------------------------------------------
# 3. new_midi_track's route-group argument: nil crashes Ardour 9
# ----------------------------------------------------------------------
# Live, 9.8: create_midi_track passing nil for the (9.x) shared_ptr<RouteGroup>
# killed Ardour: "ArdourGUI[41973]: segfault at 0 ... error 4 in
# libardour.so.3.0.0". 9.8's own scripts pass ARDOUR.RouteGroup (); on 8.12 that
# is not callable and nil is right. The mock raises where Ardour 9 crashed.

@requires_lua
def test_create_midi_track_on_ardour_9_passes_a_nil_route_group_object(tmp_path):
    res = call_bridge(tmp_path, "create_midi_track", {"name": "Keys"}, ardour="9")
    assert res.error is None, res.error
    assert res.result["track_id"] == "Keys"
    assert any(c.startswith("new_midi_track name=Keys") for c in res.calls)


@requires_lua
def test_create_midi_track_on_ardour_8_still_passes_nil(tmp_path):
    res = call_bridge(tmp_path, "create_midi_track", {"name": "Keys"}, ardour="8")
    assert res.error is None, res.error
    assert res.result["track_id"] == "Keys"
