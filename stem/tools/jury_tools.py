"""Music Greats Jury tools — rhythm-aware session judgment."""
from typing import Optional

from pydantic import BaseModel, Field

from .core import registry
from ..services.jury import judge_bridge, write_jury_json


class JudgeSession(BaseModel):
    style: str = Field(
        default="default",
        description="Style profile for critic weights: house, party, pop, "
                    "emotional, hip_hop, default")
    key: str = Field(default="C")
    is_minor: bool = Field(default=False)
    expected_tempo: Optional[float] = Field(
        default=None, gt=20, lt=400,
        description="Intent tempo; defaults to session tempo")
    beats_per_bar: Optional[int] = Field(
        default=None, ge=2, le=12,
        description="Force meter; omit to infer from onsets")
    wav_path: Optional[str] = Field(
        default=None,
        description="Optional mix/render WAV for mix_hygiene critic")
    expect_vocals: bool = Field(default=False)
    write_json: Optional[str] = Field(
        default=None,
        description="If set, write full jury report JSON to this path")


@registry.register(
    "judge_session",
    "Run the Music Greats Jury on the current Stem session: rhythm substrate "
    "(pulse/meter/groove, length-invariant) plus critics pulse_locke, "
    "harmony_bach, form_abbey, intent_fit, and optional mix_hygiene on a WAV. "
    "Prefer this over review_audio alone when judging whether a Stem-produced "
    "track is musically coherent. Returns verdict pass|revise|fail.",
    JudgeSession)
def judge_session(args, ctx):
    report = judge_bridge(
        ctx.bridge,
        style=args.style,
        key=args.key,
        is_minor=args.is_minor,
        expected_tempo=args.expected_tempo,
        beats_per_bar=args.beats_per_bar,
        wav_path=args.wav_path,
        expect_vocals=args.expect_vocals,
    )
    if args.write_json:
        write_jury_json(report, args.write_json)
        out = report.to_dict()
        out["written"] = args.write_json
        return out
    return report.to_dict()
