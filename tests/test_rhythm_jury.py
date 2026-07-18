"""Rhythm substrate + Music Greats Jury tests."""
from pathlib import Path

from stem.agent.loop import ToolContext
from stem.bridge.mock import MockBridge
from stem.services.jury import judge_bridge, judge_onsets, resolve_style
from stem.services.rhythm import (
    Onset, analyze_rhythm, infer_meter, make_grid_onsets, onsets_from_bridge,
)
from stem.tools.core import registry
from stem.tools import produce_tools  # noqa: F401
from stem.tools import jury_tools  # noqa: F401


def test_four_on_floor_recovers_4_4_and_strong_downbeats():
    ons = make_grid_onsets(8, 4, pattern="four_on_floor")
    meter, conf = infer_meter(ons)
    assert meter == 4
    assert conf > 0.5
    r = analyze_rhythm(ons, tempo_bpm=124, beats_per_bar=4)
    assert r.bars == 8
    assert r.downbeat_lock >= 0.9
    assert r.kick_one_lock >= 0.9
    assert r.pulse_confidence > 0.5


def test_waltz_prefers_3_4():
    ons = make_grid_onsets(8, 3, pattern="waltz")
    meter, conf = infer_meter(ons, candidates=(3, 4, 5))
    assert meter == 3
    assert conf > 0.45


def test_length_invariance_4_vs_64_bars():
    # Lock meter so this tests groove aggregates, not inference edge cases.
    short = analyze_rhythm(
        make_grid_onsets(4, pattern="four_on_floor"), tempo_bpm=120,
        beats_per_bar=4)
    long = analyze_rhythm(
        make_grid_onsets(64, pattern="four_on_floor"), tempo_bpm=120,
        beats_per_bar=4)
    assert short.beats_per_bar == long.beats_per_bar == 4
    assert abs(short.downbeat_lock - long.downbeat_lock) < 0.05
    assert abs(short.syncopation_mean - long.syncopation_mean) < 0.05
    assert abs(short.density_mean - long.density_mean) < 0.05
    assert abs(short.kick_one_lock - long.kick_one_lock) < 0.05
    assert long.bars == 64
    assert short.bars == 4


def test_evil_twin_weak_only_fails_pulse_locke():
    good = judge_onsets(
        make_grid_onsets(8, pattern="four_on_floor"),
        style="house", key="C", tempo_bpm=124, track_count=3,
    )
    bad = judge_onsets(
        make_grid_onsets(8, pattern="weak_only"),
        style="house", key="C", tempo_bpm=124, track_count=3,
    )
    pulse_good = next(c for c in good.critics if c.id == "pulse_locke")
    pulse_bad = next(c for c in bad.critics if c.id == "pulse_locke")
    assert pulse_good.score > 0.7
    assert pulse_bad.score < 0.45
    assert bad.verdict in {"revise", "fail"}
    assert good.overall > bad.overall


def test_shifted_onsets_hurt_downbeat_lock():
    ons = make_grid_onsets(8, pattern="four_on_floor")
    shifted = [Onset(beat=o.beat + 0.5, pitch=o.pitch, role=o.role,
                     velocity=o.velocity) for o in ons]
    r0 = analyze_rhythm(ons, tempo_bpm=120, beats_per_bar=4)
    r1 = analyze_rhythm(shifted, tempo_bpm=120, beats_per_bar=4)
    assert r0.downbeat_lock > r1.downbeat_lock
    assert r0.kick_one_lock > r1.kick_one_lock


def test_produce_instrumental_passes_jury_house_profile():
    ctx = ToolContext(bridge=MockBridge())
    produced = registry.execute("produce_instrumental", {
        "style": "house",
        "key": "F",
        "is_minor": True,
        "tempo": 124,
        "bars": 12,
        "include_lead": True,
    }, ctx)
    assert produced.get("ok")
    report = judge_bridge(
        ctx.bridge, style="house", key="F", is_minor=True,
        expected_tempo=124,
    )
    assert report.rhythm["bars"] >= 8
    pulse = next(c for c in report.critics if c.id == "pulse_locke")
    harm = next(c for c in report.critics if c.id == "harmony_bach")
    assert pulse.score > 0.5
    assert harm.score > 0.7
    assert report.verdict in {"pass", "revise"}


def test_judge_session_tool_writes_json(tmp_path):
    ctx = ToolContext(bridge=MockBridge())
    registry.execute("produce_instrumental", {
        "style": "party", "key": "A", "is_minor": True, "bars": 8,
    }, ctx)
    out = tmp_path / "jury.json"
    r = registry.execute("judge_session", {
        "style": "party",
        "key": "A",
        "is_minor": True,
        "write_json": str(out),
    }, ctx)
    assert "error" not in r
    assert out.exists()
    assert r["verdict"] in {"pass", "revise", "fail"}
    assert "pulse_locke" in {c["id"] for c in r["critics"]}


def test_resolve_style_aliases():
    assert resolve_style("deep-house") == "house"
    assert resolve_style("anthem") == "party"
    assert resolve_style("nope") == "default"


def test_onsets_from_bridge_reads_midi():
    ctx = ToolContext(bridge=MockBridge())
    registry.execute("produce_instrumental", {
        "style": "pop", "key": "C", "bars": 4,
    }, ctx)
    ons = onsets_from_bridge(ctx.bridge)
    assert len(ons) > 10
    assert any(o.role in ("kick", "bass", "chord") for o in ons)
