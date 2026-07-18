"""Audio listen/review tools for generated material."""
import json
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from .core import registry
from ..services.audio_review import AudioReview, improve_prompt, review_wav


class ReviewAudio(BaseModel):
    path: str = Field(description="Absolute path to a WAV file to review")
    expect_vocals: bool = Field(default=True)
    min_duration: float = Field(default=12.0, gt=0, le=600)


@registry.register(
    "review_audio",
    "Mix hygiene only: loudness, dynamics, silence, midband presence on a WAV. "
    "For musical judgment (rhythm, harmony, form), prefer judge_session. "
    "Use both after produce_instrumental / overlay_vocals when a render exists.",
    ReviewAudio)
def review_audio(args, ctx):
    review = review_wav(
        args.path,
        expect_vocals=args.expect_vocals,
        min_duration=args.min_duration,
    )
    return review.to_dict()


class ImproveSongPrompt(BaseModel):
    prompt: str
    review_path: Optional[str] = Field(
        default=None,
        description="Optional path to a prior review JSON; else pass findings via "
                    "review_overall / review_hints")
    review_overall: Optional[float] = None
    review_hints: Optional[list] = None
    review_verdict: Optional[str] = None


@registry.register(
    "improve_song_prompt",
    "Rewrite a vocal/overlay prompt using audio-review hints so the next "
    "overlay_vocals call is more likely to pass the listen harness.",
    ImproveSongPrompt)
def improve_song_prompt(args, ctx):
    if args.review_path:
        p = Path(args.review_path)
        if p.suffix.lower() == ".json":
            data = json.loads(p.read_text())
            review = AudioReview(**{k: data[k] for k in AudioReview.__dataclass_fields__
                                    if k in data})
        else:
            review = review_wav(p)
    else:
        review = AudioReview(
            path="",
            duration_seconds=0,
            sample_rate=0,
            channels=0,
            peak=0,
            rms=0,
            crest_db=0,
            silence_ratio=0,
            loudness_score=0,
            dynamics_score=0,
            presence_score=0,
            overall=float(args.review_overall or 0),
            verdict=args.review_verdict or "revise",
            findings=[],
            improve_prompt_hints=list(args.review_hints or []),
        )
    return {"prompt": improve_prompt(args.prompt, review)}
