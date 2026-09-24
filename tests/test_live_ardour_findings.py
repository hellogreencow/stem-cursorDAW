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
