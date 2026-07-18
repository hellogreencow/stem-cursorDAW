"""M2.3 plugin load/param tools against MockBridge."""
import stem.tools.plugin_tools  # noqa: F401
from stem.tools.core import registry


def run(ctx, tool, **kwargs):
    return registry.execute(tool, kwargs, ctx)


def test_list_plugins_filters_effects(mock_ctx):
    all_p = run(mock_ctx, "list_plugins")
    assert all_p["count"] >= 3
    effects = run(mock_ctx, "list_plugins", kind="effect")
    assert effects["count"] == 1
    assert effects["plugins"][0]["id"] == "mock.reverb"
    q = run(mock_ctx, "list_plugins", query="reverb")
    assert q["count"] == 1


def test_load_plugin_set_param_and_undo(mock_ctx):
    track = run(mock_ctx, "create_midi_track", name="FX")["track_id"]
    loaded = run(mock_ctx, "load_plugin", track_id=track,
                 plugin_id="mock.reverb")
    assert "action_id" in loaded
    assert any(p["id"] == "mock.reverb"
               for p in mock_ctx.bridge.tracks[track].plugins)

    params = run(mock_ctx, "get_plugin_params", track_id=track,
                 plugin_index=0)
    # create_midi_track has no instrument → reverb is index 0
    assert params["plugin_id"] == "mock.reverb"
    mix = next(p for p in params["params"] if p["id"] == "mix")
    assert mix["value"] == 0.3

    set_r = run(mock_ctx, "set_plugin_param", track_id=track,
                param_id="mix", value=0.9, plugin_index=0)
    assert "action_id" in set_r
    after = run(mock_ctx, "get_plugin_params", track_id=track,
                plugin_index=0)
    assert next(p["value"] for p in after["params"] if p["id"] == "mix") == 0.9

    run(mock_ctx, "undo", action_id=set_r["action_id"])
    reverted = run(mock_ctx, "get_plugin_params", track_id=track,
                   plugin_index=0)
    assert next(p["value"] for p in reverted["params"]
                if p["id"] == "mix") == 0.3


def test_set_plugin_param_clamps(mock_ctx):
    track = run(mock_ctx, "create_midi_track", name="t",
                instrument_id="mock.synth")["track_id"]
    run(mock_ctx, "set_plugin_param", track_id=track, param_id="cutoff",
        value=5.0, plugin_index=0)
    params = run(mock_ctx, "get_plugin_params", track_id=track,
                 plugin_index=0)
    assert next(p["value"] for p in params["params"]
                if p["id"] == "cutoff") == 1.0
