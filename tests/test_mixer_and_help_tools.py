"""Coverage for mixer, diagnostics, theory lookup, and ardour_help."""
from stem.tools.core import registry
import stem.tools.ardour_tools  # noqa: F401


def run(ctx, tool, **kwargs):
    return registry.execute(tool, kwargs, ctx)


def test_set_track_gain_and_mute(mock_ctx):
    track_id = run(mock_ctx, "create_midi_track", name="Mix")["track_id"]
    g = run(mock_ctx, "set_track_gain", track_id=track_id, gain_db=-6.0)
    assert "action_id" in g
    assert mock_ctx.bridge.tracks[track_id].gain_db == -6.0
    m = run(mock_ctx, "set_track_mute", track_id=track_id, muted=True)
    assert "action_id" in m
    assert mock_ctx.bridge.tracks[track_id].muted is True


def test_get_scale_notes():
    from stem.agent.loop import ToolContext
    from stem.bridge.mock import MockBridge
    ctx = ToolContext(bridge=MockBridge())
    r = run(ctx, "get_scale_notes", root="C", scale_type="major")
    assert "error" not in r
    assert len(r["notes"]) == 7


def test_diagnose_audio_and_make_tracks_audible_on_mock(mock_ctx):
    d = run(mock_ctx, "diagnose_audio")
    assert d.get("supported") is False
    f = run(mock_ctx, "make_tracks_audible")
    assert f.get("ok") is False


def test_ardour_help_returns_structure():
    from stem.agent.loop import ToolContext
    from stem.bridge.mock import MockBridge
    ctx = ToolContext(bridge=MockBridge())
    r = run(ctx, "ardour_help", question="how do I change tempo")
    assert "found" in r
