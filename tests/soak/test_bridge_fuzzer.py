"""H3: deterministic MockBridge tool soak / fuzzer (D012)."""
from __future__ import annotations

import json
import random

import pytest

import stem.tools.generation_tools  # noqa: F401
import stem.tools.plugin_tools  # noqa: F401
import stem.tools.proposal_tools  # noqa: F401
import stem.tools.sample_tools  # noqa: F401
from stem.agent.loop import ToolContext
from stem.bridge.mock import MockBridge
from stem.tools.core import registry
from tests.harness.asserts_music import session_fingerprint


SEED = 42
STEPS = 80


def _run(ctx, tool, **kwargs):
    return registry.execute(tool, kwargs, ctx)


def _midi_tracks(ctx):
    return [t.track_id for t in ctx.bridge.tracks.values() if t.kind == "midi"]


def _pick_op(rng: random.Random, ctx) -> tuple[str, dict]:
    """Return a likely-valid (tool, args) pair."""
    tracks = _midi_tracks(ctx)
    ops = ["create", "tempo", "overview"]
    if tracks:
        ops.extend([
            "notes", "drums", "chords", "bass", "gain", "mute", "marker",
            "plugin", "param", "propose", "play", "stop", "locate", "undo",
        ])
    op = rng.choice(ops)
    if op == "create":
        return "create_midi_track", {
            "name": f"T{rng.randint(0, 999)}",
            "instrument_id": rng.choice(["mock.piano", "mock.synth"]),
        }
    if op == "tempo":
        return "set_tempo", {"bpm": rng.uniform(60, 180)}
    if op == "overview":
        return "get_session_overview", {}
    tid = rng.choice(tracks)
    if op == "notes":
        n = rng.randint(1, 4)
        notes = [{
            "pitch": rng.randint(36, 84),
            "start_beat": float(rng.randint(0, 8)),
            "length_beats": rng.choice([0.25, 0.5, 1.0]),
            "velocity": rng.randint(40, 120),
        } for _ in range(n)]
        return "insert_midi_notes", {"track_id": tid, "notes": notes}
    if op == "drums":
        return "insert_drum_pattern", {
            "track_id": tid,
            "style": rng.choice(["four_on_floor", "trap", "hip_hop"]),
            "bars": rng.randint(1, 4),
        }
    if op == "chords":
        return "insert_chord_progression", {
            "track_id": tid,
            "key": rng.choice(["C", "F", "G", "A"]),
            "progression": rng.choice(
                ["I_V_vi_IV", "I_IV_V", "Andalusian"]),
        }
    if op == "bass":
        return "insert_bassline", {
            "track_id": tid,
            "key": "C",
            "progression": "I_V_vi_IV",
            "style": rng.choice(["pulse", "driving"]),
        }
    if op == "gain":
        return "set_track_gain", {
            "track_id": tid, "gain_db": rng.uniform(-24, 6),
        }
    if op == "mute":
        return "set_track_mute", {
            "track_id": tid, "muted": rng.choice([True, False]),
        }
    if op == "marker":
        return "add_marker", {
            "name": f"m{rng.randint(0, 50)}",
            "position_seconds": rng.uniform(0, 30),
        }
    if op == "plugin":
        return "load_plugin", {
            "track_id": tid, "plugin_id": "mock.reverb",
        }
    if op == "param":
        plugins = ctx.bridge.tracks[tid].plugins
        if not plugins:
            return "load_plugin", {
                "track_id": tid, "plugin_id": "mock.reverb",
            }
        pid = plugins[0]["id"]
        param = {
            "mock.reverb": "mix",
            "mock.synth": "cutoff",
            "mock.piano": "level",
        }.get(pid, "mix")
        return "set_plugin_param", {
            "track_id": tid, "plugin_index": 0,
            "param_id": param,
            "value": rng.random(),
        }
    if op == "propose":
        return "propose_midi_notes", {
            "track_id": tid,
            "notes": [{"pitch": 60, "start_beat": 0.0, "length_beats": 1.0}],
            "summary": "fuzz",
        }
    if op == "play":
        return "transport_play", {}
    if op == "stop":
        return "transport_stop", {}
    if op == "locate":
        return "set_selection", {
            "track_ids": [tid],
            "start_seconds": 0.0,
            "end_seconds": rng.uniform(1, 8),
        }
    # undo
    return "undo", {}


def _assert_invariants(ctx, seed: int, step: int):
    overview = _run(ctx, "get_session_overview")
    assert "error" not in overview, (seed, step, overview)
    # JSON serializable
    json.dumps(overview)
    fp = session_fingerprint(ctx.bridge)
    json.dumps(fp)
    for t in ctx.bridge.tracks.values():
        if t.kind != "midi":
            continue
        for n in ctx.bridge.notes.get(t.track_id, []):
            assert 0 <= n.pitch <= 127, (seed, step, n.pitch)
            assert n.length_beats > 0, (seed, step, n)
            assert n.start_beat >= 0, (seed, step, n)


def test_bridge_fuzzer_invariants():
    """PR-gated soak (D012): 80 steps, fixed seed."""
    _run_fuzzer(STEPS)


@pytest.mark.nightly
@pytest.mark.slow
def test_bridge_fuzzer_long():
    _run_fuzzer(250)


def _run_fuzzer(steps: int):
    rng = random.Random(SEED)
    ctx = ToolContext(bridge=MockBridge())
    _run(ctx, "create_midi_track", name="seed", instrument_id="mock.piano")

    for step in range(steps):
        tool, args = _pick_op(rng, ctx)
        result = _run(ctx, tool, **args)
        assert isinstance(result, dict), (SEED, step, tool, result)
        try:
            _assert_invariants(ctx, SEED, step)
        except AssertionError:
            print(f"FUZZ_FAIL seed={SEED} step={step} tool={tool} args={args} "
                  f"result={result}")
            raise

    for _ in range(len(ctx.bridge._undo_stack) + 5):
        _run(ctx, "undo")
    _assert_invariants(ctx, SEED, steps)
