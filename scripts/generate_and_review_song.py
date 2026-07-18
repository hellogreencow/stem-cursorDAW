#!/usr/bin/env python3
"""Generate a vocal song with ElevenLabs, review it, regenerate if needed.

Requires ELEVENLABS_API_KEY in the environment (never commit the key).

Usage:
  ELEVENLABS_API_KEY=... python scripts/generate_and_review_song.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stem.paths import generated_dir
from stem.services.audio_review import improve_prompt, review_wav, write_review_json
from stem.services.elevenlabs_music import elevenlabs_music

OUT_DIR = ROOT / "examples" / "dogfood"
DEFAULT_PROMPT = (
    "Atmospheric deep-house track in F minor at 124 BPM, warm analog bass, "
    "crisp four-on-the-floor kick, soft pads, and a clear intimate female lead "
    "vocal singing memorable English lyrics about late-night city lights and "
    "finding resolve. Verse then chorus, polished modern mix, vocals upfront, "
    "no distorted clipping."
)


def main(argv: list[str]) -> int:
    if not elevenlabs_music.available():
        print("ELEVENLABS_API_KEY not available", file=sys.stderr)
        return 2

    length = float(os.environ.get("STEM_SONG_SECONDS", "45"))
    max_attempts = int(os.environ.get("STEM_SONG_ATTEMPTS", "2"))
    prompt = os.environ.get("STEM_SONG_PROMPT", DEFAULT_PROMPT)
    out_wav = OUT_DIR / "stem_vocal_house.wav"
    out_review = OUT_DIR / "stem_vocal_house.review.json"
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    history = []
    best = None
    best_path = None

    for attempt in range(1, max_attempts + 1):
        print(f"\n=== attempt {attempt}/{max_attempts} ===")
        print(f"prompt: {prompt[:180]}...")
        path = elevenlabs_music.generate(
            prompt, length_seconds=length, instrumental=False)
        review = review_wav(path, expect_vocals=True, min_duration=min(20.0, length * 0.5))
        history.append({"attempt": attempt, "prompt": prompt, "review": review.to_dict(),
                        "source": path})
        print(json.dumps({
            "verdict": review.verdict,
            "overall": review.overall,
            "findings": review.findings,
            "hints": review.improve_prompt_hints,
            "duration": review.duration_seconds,
            "rms": review.rms,
        }, indent=2))

        if best is None or review.overall > best.overall:
            best = review
            best_path = path

        if review.verdict == "pass":
            best = review
            best_path = path
            break

        prompt = improve_prompt(DEFAULT_PROMPT, review)

    assert best is not None and best_path is not None
    shutil.copyfile(best_path, out_wav)
    write_review_json(best, out_review)
    (OUT_DIR / "stem_vocal_house.txt").write_text(
        "Stem vocal dogfood (ElevenLabs Music)\n"
        f"length_target_s={length}\n"
        f"attempts={len(history)}\n"
        f"verdict={best.verdict} overall={best.overall}\n"
        f"findings={best.findings}\n"
        f"output={out_wav.name}\n"
        "Rebuild: ELEVENLABS_API_KEY=… python scripts/generate_and_review_song.py\n"
        "NOTE: API key must never be committed.\n"
    )
    (OUT_DIR / "stem_vocal_house.history.json").write_text(
        json.dumps(history, indent=2)[:200_000]
    )
    print(f"\nSaved {out_wav} ({out_wav.stat().st_size} bytes)")
    print(f"Review {out_review}")
    return 0 if best.verdict in {"pass", "revise"} else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
