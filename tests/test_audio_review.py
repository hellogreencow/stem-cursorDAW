"""Listen/review harness tests (no network)."""
from pathlib import Path

import stem.tools.review_tools  # noqa: F401
from stem.services.audio_review import improve_prompt, review_wav
from stem.tools.core import registry


FIXTURE = (Path(__file__).resolve().parent / "fixtures" / "samples_lib"
           / "warm_pad_dm.wav")


def test_review_wav_on_fixture():
    review = review_wav(FIXTURE, expect_vocals=False, min_duration=0.2)
    assert review.duration_seconds > 0
    assert review.sample_rate > 0
    assert 0 <= review.overall <= 100
    assert review.verdict in {"pass", "revise", "fail"}


def test_improve_prompt_adds_hints():
    review = review_wav(FIXTURE, expect_vocals=True, min_duration=20)
    prompt = improve_prompt("a house song with vocals", review)
    assert "house song" in prompt
    if review.verdict != "pass":
        assert len(prompt) > len("a house song with vocals")


def test_review_audio_tool(mock_ctx):
    r = registry.execute("review_audio", {
        "path": str(FIXTURE),
        "expect_vocals": False,
        "min_duration": 0.2,
    }, mock_ctx)
    assert "overall" in r
    assert r["path"]


def test_improve_song_prompt_tool(mock_ctx):
    review = review_wav(FIXTURE, expect_vocals=True, min_duration=20)
    r = registry.execute("improve_song_prompt", {
        "prompt": "a house song with vocals",
        "review_overall": review.overall,
        "review_verdict": review.verdict,
        "review_hints": review.improve_prompt_hints,
    }, mock_ctx)
    assert "prompt" in r
    assert "house song" in r["prompt"]
