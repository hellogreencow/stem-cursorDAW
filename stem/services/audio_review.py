"""Listen/review harness for generated songs (M4.3 / dogfood loop).

Analyzes a WAV and returns scored findings the agent (or CI) can use to
decide whether to regenerate with a tighter prompt.
"""
from __future__ import annotations

import json
import math
import struct
import wave
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class AudioReview:
    path: str
    duration_seconds: float
    sample_rate: int
    channels: int
    peak: float
    rms: float
    crest_db: float
    silence_ratio: float
    loudness_score: float          # 0..1
    dynamics_score: float          # 0..1
    presence_score: float          # midband energy proxy for vocals/mix body
    overall: float                 # 0..100
    verdict: str                   # pass | revise | fail
    findings: List[str] = field(default_factory=list)
    improve_prompt_hints: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _read_wav_mono(path: Path) -> tuple[list[float], int, int]:
    with wave.open(str(path), "rb") as w:
        channels = w.getnchannels()
        width = w.getsampwidth()
        rate = w.getframerate()
        n = w.getnframes()
        raw = w.readframes(n)
    if width == 2:
        fmt = "<" + "h" * (len(raw) // 2)
        samples = struct.unpack(fmt, raw)
        scale = 32768.0
    elif width == 3:
        # 24-bit little-endian packed
        samples = []
        for i in range(0, len(raw), 3):
            b = raw[i:i + 3]
            if len(b) < 3:
                break
            val = int.from_bytes(b, "little", signed=True)
            samples.append(val)
        scale = 8388608.0
    elif width == 4:
        fmt = "<" + "i" * (len(raw) // 4)
        samples = struct.unpack(fmt, raw)
        scale = 2147483648.0
    else:
        raise RuntimeError(f"unsupported sample width: {width}")

    if channels > 1:
        mono = []
        for i in range(0, len(samples), channels):
            frame = samples[i:i + channels]
            mono.append(sum(frame) / len(frame) / scale)
    else:
        mono = [s / scale for s in samples]
    return mono, rate, channels


def _rms(xs: list[float]) -> float:
    if not xs:
        return 0.0
    return math.sqrt(sum(x * x for x in xs) / len(xs))


def _band_energy(mono: list[float], rate: int, lo: float, hi: float,
                 frame: int = 2048) -> float:
    """Very cheap DFT-ish band energy via Goertzel-ish block RMS of filtered
    difference — approximate mid presence without numpy dependency."""
    # Simple one-pole band emphasis: highpass then lowpass, measure RMS.
    # Not audiophile — good enough as a vocal/body presence proxy.
    if len(mono) < frame:
        return 0.0
    rc_hi = 1.0 / (2 * math.pi * lo)
    dt = 1.0 / rate
    alpha_hp = rc_hi / (rc_hi + dt)
    rc_lo = 1.0 / (2 * math.pi * hi)
    alpha_lp = dt / (rc_lo + dt)
    prev = mono[0]
    hp = 0.0
    lp = 0.0
    acc = 0.0
    count = 0
    for x in mono[:: max(1, len(mono) // 200_000)]:  # downsample for speed
        hp = alpha_hp * (hp + x - prev)
        prev = x
        lp = lp + alpha_lp * (hp - lp)
        acc += lp * lp
        count += 1
    return math.sqrt(acc / max(1, count))


def review_wav(path: str | Path,
               *,
               expect_vocals: bool = True,
               min_duration: float = 12.0,
               target_rms: float = 0.12) -> AudioReview:
    path = Path(path)
    mono, rate, channels = _read_wav_mono(path)
    duration = len(mono) / float(rate)
    peak = max((abs(x) for x in mono), default=0.0)
    rms = _rms(mono)
    crest_db = 20 * math.log10(peak / max(rms, 1e-9)) if peak > 0 else 0.0
    silence_eps = 0.01
    silence_ratio = sum(1 for x in mono if abs(x) < silence_eps) / max(1, len(mono))

    # Scores 0..1
    loudness_score = max(0.0, min(1.0, rms / target_rms))
    if peak > 0.99:
        loudness_score *= 0.7  # clip risk
    dynamics_score = max(0.0, min(1.0, (crest_db - 4.0) / 12.0))
    mid = _band_energy(mono, rate, 200.0, 4000.0)
    presence_score = max(0.0, min(1.0, mid / max(rms, 1e-6) * 0.8))

    findings: list[str] = []
    hints: list[str] = []

    if duration < min_duration:
        findings.append(f"short duration ({duration:.1f}s < {min_duration}s)")
        hints.append("request a longer clip (45–60s) with clear verse/chorus")
    if rms < 0.04:
        findings.append(f"too quiet (rms={rms:.3f})")
        hints.append("ask for a louder, fuller mix with stronger drums and vocal")
    if peak > 0.99:
        findings.append("possible clipping (peak≈1.0)")
        hints.append("ask for controlled dynamics, no distortion")
    if silence_ratio > 0.35:
        findings.append(f"high silence ratio ({silence_ratio:.0%})")
        hints.append("tighten structure; less empty intro; keep groove continuous")
    if expect_vocals and presence_score < 0.25:
        findings.append("weak midband presence — vocals may be buried or missing")
        hints.append(
            "emphasize lead vocal upfront, intimate dry vocal, lyrics clearly sung"
        )
    if crest_db < 4.5:
        findings.append(f"over-compressed (crest {crest_db:.1f} dB)")
        hints.append("allow more dynamics; less brickwall limiting")
    if crest_db > 18 and rms < 0.08:
        findings.append("very peaky / thin")
        hints.append("add body: bass, pads, thicker vocal doubles")

    overall = 100.0 * (
        0.35 * loudness_score + 0.25 * dynamics_score + 0.40 * presence_score
    )
    if duration < min_duration:
        overall *= 0.7
    if silence_ratio > 0.35:
        overall *= 0.8

    if overall >= 70 and not any("clipping" in f for f in findings):
        verdict = "pass"
    elif overall >= 45:
        verdict = "revise"
    else:
        verdict = "fail"

    if verdict != "pass" and not hints:
        hints.append("regenerate with clearer genre, tempo, vocal style, and lyrics")

    return AudioReview(
        path=str(path.resolve()),
        duration_seconds=round(duration, 3),
        sample_rate=rate,
        channels=channels,
        peak=round(peak, 4),
        rms=round(rms, 4),
        crest_db=round(crest_db, 2),
        silence_ratio=round(silence_ratio, 4),
        loudness_score=round(loudness_score, 3),
        dynamics_score=round(dynamics_score, 3),
        presence_score=round(presence_score, 3),
        overall=round(overall, 1),
        verdict=verdict,
        findings=findings,
        improve_prompt_hints=hints,
    )


def improve_prompt(base_prompt: str, review: AudioReview) -> str:
    """Augment a generation prompt using review hints."""
    if review.verdict == "pass" or not review.improve_prompt_hints:
        return base_prompt
    extras = "; ".join(review.improve_prompt_hints[:3])
    return (
        f"{base_prompt.rstrip('.')}. Mix notes: {extras}. "
        "Keep a clear lead vocal in the foreground with intelligible lyrics."
    )


def write_review_json(review: AudioReview, out: Path) -> Path:
    out.write_text(json.dumps(review.to_dict(), indent=2))
    return out
