#!/usr/bin/env python3
"""Make a song now: Stem produce_instrumental → synth → overlay vocals → mix.

Stem owns the bed. ElevenLabs supplies an isolated vocal stem only.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stem.agent.loop import ToolContext
from stem.bridge.mock import MockBridge
from stem.services.audio_review import review_wav
from stem.services.elevenlabs_music import elevenlabs_music
from stem.services.jury import judge_bridge, write_jury_json
from stem.tools.core import registry
from stem.tools import generation_tools  # noqa: F401
from stem.tools import produce_tools  # noqa: F401
from stem.tools import jury_tools  # noqa: F401
from stem.tools.generation_tools import _provider_safe_vocal_prompt

OUT_DIR = ROOT / "examples" / "dogfood"
DURATION = float(os.environ.get("STEM_SONG_SECONDS", "36"))
LYRICS = os.environ.get(
    "STEM_SONG_LYRICS",
    "We build the night from the ground up\n"
    "Every chord is a choice we own\n"
    "Lay the vocal when the bed feels right\n"
    "This is our song, not a borrowed tone",
)


def _load_render_mod():
    spec = importlib.util.spec_from_file_location(
        "render_mock_song", ROOT / "scripts" / "render_mock_song.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _load_mix_mod():
    spec = importlib.util.spec_from_file_location(
        "stem_track_plus_vocals", ROOT / "scripts" / "stem_track_plus_vocals.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    if not elevenlabs_music.available():
        print("ELEVENLABS_API_KEY required for vocal overlay only", file=sys.stderr)
        return 2

    render_mod = _load_render_mod()
    mix_mod = _load_mix_mod()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    instrumental = OUT_DIR / "stem_now_instrumental.wav"
    vocals_out = OUT_DIR / "stem_now_vocals.wav"
    final = OUT_DIR / "stem_now_song.wav"

    print("=== 1) Stem produce_instrumental ===")
    bridge = MockBridge(name="stem-now", tempo=122)
    ctx = ToolContext(bridge=bridge)
    produced = registry.execute("produce_instrumental", {
        "style": "party",
        "key": "A",
        "is_minor": True,
        "tempo": 122,
        "progression": "Andalusian",
        "bars": 16,
        "drum_style": "four_on_floor",
        "bass_style": "driving",
        "include_lead": True,
        "prompt": "make me a song — anthem energy, dark minor lift",
    }, ctx)
    if "error" in produced:
        print(produced, file=sys.stderr)
        return 1
    analysis = registry.execute("analyze_instrumental", {}, ctx)
    print(json.dumps({
        "producer": produced.get("producer"),
        "key": produced.get("key"),
        "tempo": produced.get("tempo"),
        "progression": produced.get("progression"),
        "bars": produced.get("bars"),
        "tracks": produced.get("tracks"),
        "ready_for_vocals": analysis.get("ready_for_vocals"),
    }, indent=2))

    print("=== 2) Jury on Stem session (pre-render) ===")
    jury = judge_bridge(
        bridge, style="party", key="A", is_minor=True, expected_tempo=122)
    write_jury_json(jury, OUT_DIR / "stem_now_song.jury.json")
    print(json.dumps({
        "verdict": jury.verdict, "overall": jury.overall,
        "rhythm": jury.rhythm,
        "critics": {c.id: round(c.score, 3) for c in jury.critics},
    }, indent=2))

    print("=== 3) Render Stem bed ===")
    render_mod.render(bridge, instrumental, seconds=DURATION)
    print(f"instrumental: {instrumental} ({instrumental.stat().st_size} bytes)")

    print("=== 4) Vocal overlay (isolated stem only — one API call) ===")
    # Same path as overlay_vocals tool: generate vocal-forward → isolate stem.
    # Done here once so we do not pay for a second generation.
    if not analysis.get("ready_for_vocals"):
        print("Instrumental not ready for vocals", file=sys.stderr)
        return 1
    vocal_prompt = _provider_safe_vocal_prompt(
        "powerful anthem lead vocal, emotional, clear diction, minor key lift",
        LYRICS,
    )
    full = elevenlabs_music.generate(
        vocal_prompt, length_seconds=min(DURATION, 40.0), instrumental=False)
    vocal_path = elevenlabs_music.isolate_vocals(full)
    shutil.copyfile(vocal_path, vocals_out)
    bridge.import_audio("", str(vocals_out), 0.0)
    print(f"vocals: {vocals_out} ({vocals_out.stat().st_size} bytes)")

    print("=== 5) Mix Stem bed + vocal overlay ===")
    mix_mod.mix_wavs(instrumental, vocals_out, final)
    review = review_wav(
        final, expect_vocals=True, min_duration=min(16.0, DURATION * 0.5))
    data = review.to_dict()
    data["path"] = "examples/dogfood/stem_now_song.wav"
    (OUT_DIR / "stem_now_song.review.json").write_text(json.dumps(data, indent=2))

    # Re-judge with mix hygiene on the final WAV
    jury_final = judge_bridge(
        bridge, style="party", key="A", is_minor=True, expected_tempo=122,
        wav_path=str(final), expect_vocals=True,
    )
    write_jury_json(jury_final, OUT_DIR / "stem_now_song.jury.json")

    meta = {
        "pipeline": "produce_instrumental + judge_session + overlay_vocals",
        "not": "full elevenlabs song",
        "instrumental": instrumental.name,
        "vocals": vocals_out.name,
        "final": final.name,
        "produce": {
            "key": produced.get("key"),
            "is_minor": produced.get("is_minor"),
            "tempo": produced.get("tempo"),
            "progression": produced.get("progression"),
            "bars": produced.get("bars"),
        },
        "jury": jury_final.to_dict(),
        "review": data,
    }
    (OUT_DIR / "stem_now_song.pipeline.json").write_text(json.dumps(meta, indent=2))
    (OUT_DIR / "stem_now_song.txt").write_text(
        "SONG NOW — Stem produce + vocal overlay\n"
        f"1) produce_instrumental A minor Andalusian @ 122 → {instrumental.name}\n"
        f"2) overlay_vocals (isolated) → {vocals_out.name}\n"
        f"3) Mix → {final.name}\n"
        f"review: verdict={review.verdict} overall={review.overall}\n"
        "Rebuild: ELEVENLABS_API_KEY=… python scripts/make_song_now.py\n"
    )
    print(json.dumps({
        "pipeline": meta["pipeline"],
        "verdict": review.verdict,
        "overall": review.overall,
        "findings": review.findings,
        "final": str(final),
        "final_bytes": final.stat().st_size,
    }, indent=2))
    return 0 if review.verdict in {"pass", "revise"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
