"""The read-side tools PLAN.md asks for: mixer state and playhead position,
plus the toolbox-assembly check that keeps every entry point in step.
"""
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

from stem.agent.loop import ToolContext
from stem.bridge.mock import MockBridge
from stem.tools.core import registry, _parse_meter


@pytest.fixture
def ctx():
    return ToolContext(bridge=MockBridge())


def run(ctx, tool, **kwargs):
    return registry.execute(tool, kwargs, ctx)


def test_mixer_state_lists_every_strip(ctx):
    drums = run(ctx, "create_midi_track", name="Drums",
                instrument_id="mock.piano")["track_id"]
    bass = run(ctx, "create_midi_track", name="Bass")["track_id"]
    run(ctx, "set_track_gain", track_id=drums, gain_db=-3.5)
    run(ctx, "set_track_mute", track_id=bass, muted=True)

    state = run(ctx, "get_mixer_state")

    assert state["count"] == 2
    strips = {s["track_id"]: s for s in state["tracks"]}
    assert strips[drums]["gain_db"] == -3.5
    assert strips[drums]["plugins"][0]["id"] == "mock.piano"
    assert strips[bass]["muted"] is True
    assert strips[bass]["pan"] == 0.0


def test_mixer_state_can_focus_one_track(ctx):
    run(ctx, "create_midi_track", name="Drums")
    bass = run(ctx, "create_midi_track", name="Bass")["track_id"]

    state = run(ctx, "get_mixer_state", track_id=bass)

    assert state["count"] == 1 and state["tracks"][0]["name"] == "Bass"


def test_mixer_state_rejects_an_unknown_track(ctx):
    assert "error" in run(ctx, "get_mixer_state", track_id="nope")


def test_mixer_state_names_what_this_bridge_cannot_report(ctx):
    # PLAN.md asks for sends and routing; the Bridge protocol does not carry
    # them yet. Saying so beats returning an empty list that reads as "none".
    assert "sends" in run(ctx, "get_mixer_state")["not_reported"]


def test_mixer_state_is_read_only():
    assert registry.get("get_mixer_state").mutates is False


def test_playhead_converts_seconds_to_bars_and_beats(ctx):
    ctx.bridge.locate(4.0)                      # 4 s at 120 bpm = 8 beats

    where = run(ctx, "get_playhead")

    assert where["playhead_beats"] == 8.0
    assert where["bar"] == 3 and where["beat_in_bar"] == 1.0
    assert where["beats_per_bar"] == 4.0


def test_playhead_follows_the_session_tempo(ctx):
    run(ctx, "set_tempo", bpm=90)
    ctx.bridge.locate(4.0)                      # 4 s at 90 bpm = 6 beats

    where = run(ctx, "get_playhead")

    assert where["playhead_beats"] == 6.0
    assert where["bar"] == 2 and where["beat_in_bar"] == 3.0


def test_playhead_at_session_start(ctx):
    where = run(ctx, "get_playhead")
    assert where["bar"] == 1 and where["beat_in_bar"] == 1.0


def test_odd_meters_are_converted_not_faked():
    assert _parse_meter("3/4")[0] == 3.0
    assert _parse_meter("6/8")[0] == 3.0        # 6 eighths = 3 quarter beats
    assert "6/8" in _parse_meter("6/8")[2]
    assert "assuming 4/4" in _parse_meter("garbage")[2]


def test_playhead_reports_an_unparsable_meter(ctx):
    ctx.bridge.meter = "weird"
    where = run(ctx, "get_playhead")
    assert "could not parse" in where["note"]


def test_every_entry_point_gets_the_same_toolbox():
    """ardour_help used to be registered only by webserver.py, so the CLI and
    the daemon shipped a toolbox one tool short. Checked in a fresh
    interpreter because this one has already imported everything."""
    import subprocess
    import sys

    probe = subprocess.run(
        [sys.executable, "-c",
         "from stem.tools.core import registry;"
         "print('ardour_help' in registry.names())"],
        capture_output=True, text=True, cwd=str(REPO_ROOT))

    assert probe.stdout.strip() == "True", probe.stderr
