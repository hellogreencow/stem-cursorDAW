"""M4.2 autonomous tasks: preview gate, arrange, rough mix, checklists."""
import stem.tools.task_tools  # noqa: F401
from stem.tools.core import registry


def run(ctx, tool, **kwargs):
    return registry.execute(tool, kwargs, ctx)


def test_list_tasks(mock_ctx):
    r = run(mock_ctx, "list_tasks")
    names = {t["task"] for t in r["tasks"]}
    assert "arrange_loop_to_song" in names
    assert "rough_mix" in names
    assert all(t["destructive"] for t in r["tasks"])


def test_run_task_requires_confirm(mock_ctx):
    preview = run(mock_ctx, "run_task", task="arrange_loop_to_song",
                  key="F", tempo=124)
    assert preview.get("needs_confirm") is True
    assert preview.get("plan")
    # Session untouched
    assert run(mock_ctx, "get_session_overview")["tracks"] == []


def test_arrange_loop_to_song_with_confirm(mock_ctx):
    result = run(mock_ctx, "run_task", task="arrange_loop_to_song",
                 confirm=True, key="C", tempo=120)
    assert result.get("ok") is True, result
    assert result["checklist"]["markers_present"]
    assert result["checklist"]["notes_present"]
    assert result["steps_used"] <= result["max_steps"]

    overview = run(mock_ctx, "get_session_overview")
    assert overview["tempo"] == 120
    assert len(overview["tracks"]) >= 3
    names = {m["name"] for m in overview["markers"]}
    assert {"intro", "verse", "chorus"} <= names


def test_rough_mix_needs_tracks_then_succeeds(mock_ctx):
    empty = run(mock_ctx, "run_task", task="rough_mix", confirm=True)
    assert empty.get("ok") is False

    run(mock_ctx, "create_midi_track", name="Drums",
        instrument_id="mock.synth")
    run(mock_ctx, "create_midi_track", name="Bass",
        instrument_id="mock.synth")
    run(mock_ctx, "create_midi_track", name="Chords",
        instrument_id="mock.piano")

    mixed = run(mock_ctx, "run_task", task="rough_mix", confirm=True)
    assert mixed.get("ok") is True, mixed
    assert mixed["checklist"]["gains_applied"]
    assert mixed["checklist"]["unmuted"]
    drums = next(t for t in mock_ctx.bridge.tracks.values()
                 if t.name == "Drums")
    assert drums.muted is False
    assert drums.gain_db == -1.0


def test_unknown_task(mock_ctx):
    r = run(mock_ctx, "run_task", task="summon_demon", confirm=True)
    assert "error" in r
