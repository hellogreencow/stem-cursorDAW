"""Music Greats Jury — criteria oracles over rhythm + session (+ optional hygiene).

Minimalist critics (v1):
  pulse_locke   — dance-floor pulse / downbeat lock
  harmony_bach  — diatonic / chord-tone hygiene on MIDI
  form_abbey    — arrangement economy / density arc / markers
  intent_fit    — asked key/tempo/style vs measured
  mix_hygiene   — existing audio_review (optional WAV)
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .rhythm import (
    Onset, RhythmAnalysis, analyze_rhythm, onsets_from_bridge, onsets_from_notes,
)


NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLAT_EQUIV = {"DB": "C#", "EB": "D#", "GB": "F#", "AB": "G#", "BB": "A#"}
MAJOR = {0, 2, 4, 5, 7, 9, 11}
MINOR = {0, 2, 3, 5, 7, 8, 10}


STYLE_WEIGHTS: Dict[str, Dict[str, float]] = {
    "house": {
        "pulse_locke": 0.35, "harmony_bach": 0.15, "form_abbey": 0.20,
        "intent_fit": 0.15, "mix_hygiene": 0.15,
    },
    "party": {
        "pulse_locke": 0.30, "harmony_bach": 0.15, "form_abbey": 0.20,
        "intent_fit": 0.20, "mix_hygiene": 0.15,
    },
    "pop": {
        "pulse_locke": 0.20, "harmony_bach": 0.25, "form_abbey": 0.25,
        "intent_fit": 0.15, "mix_hygiene": 0.15,
    },
    "emotional": {
        "pulse_locke": 0.10, "harmony_bach": 0.30, "form_abbey": 0.25,
        "intent_fit": 0.20, "mix_hygiene": 0.15,
    },
    "hip_hop": {
        "pulse_locke": 0.25, "harmony_bach": 0.15, "form_abbey": 0.25,
        "intent_fit": 0.20, "mix_hygiene": 0.15,
    },
    "default": {
        "pulse_locke": 0.25, "harmony_bach": 0.25, "form_abbey": 0.20,
        "intent_fit": 0.15, "mix_hygiene": 0.15,
    },
}


@dataclass
class CriticResult:
    id: str
    score: float              # 0..1
    blocking: bool = False
    findings: List[str] = field(default_factory=list)
    evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class JuryReport:
    style: str
    verdict: str              # pass | revise | fail
    overall: float            # 0..100
    rhythm: Dict[str, Any]
    critics: List[CriticResult] = field(default_factory=list)
    weights: Dict[str, float] = field(default_factory=dict)
    improve_hints: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "style": self.style,
            "verdict": self.verdict,
            "overall": self.overall,
            "rhythm": self.rhythm,
            "critics": [c.to_dict() for c in self.critics],
            "weights": self.weights,
            "improve_hints": self.improve_hints,
        }


def _pc(key: str) -> int:
    r = key.strip().upper().replace("♯", "#").replace("♭", "B")
    r = FLAT_EQUIV.get(r, r)
    if r not in NOTE_NAMES:
        raise ValueError(f"unknown key: {key}")
    return NOTE_NAMES.index(r)


def _scale(key: str, minor: bool) -> set:
    root = _pc(key)
    intervals = MINOR if minor else MAJOR
    return {(root + i) % 12 for i in intervals}


def resolve_style(style: str) -> str:
    s = (style or "default").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "tech_house": "house", "deep_house": "house", "edm": "house",
        "anthem": "party", "trap": "hip_hop", "ballad": "emotional",
        "rnb": "emotional",
    }
    s = aliases.get(s, s)
    return s if s in STYLE_WEIGHTS else "default"


def critic_pulse_locke(rhythm: RhythmAnalysis, style: str) -> CriticResult:
    """Dance-floor ruthlessness: downbeats, kick-on-one, pulse confidence."""
    findings = []
    # House/party want kick lock; emotional less so
    want_kick = style in ("house", "party", "pop", "hip_hop", "default")
    down = rhythm.downbeat_lock
    pulse = rhythm.pulse_confidence
    kick = rhythm.kick_one_lock if want_kick else max(rhythm.kick_one_lock, 0.5)
    meter = rhythm.meter_confidence

    score = 0.35 * down + 0.25 * pulse + 0.25 * kick + 0.15 * meter
    # Length-robust: worst 20% of bars must still have some downbeat presence
    floor = rhythm.aggregate_floor("downbeat", 0.2)
    score = 0.8 * score + 0.2 * floor

    blocking = False
    if want_kick and down < 0.35:
        findings.append(f"weak downbeat lock ({down:.0%})")
        blocking = style in ("house", "party")
    if want_kick and kick < 0.25:
        findings.append(f"kick rarely on beat one ({kick:.0%})")
    if pulse < 0.35:
        findings.append(f"unstable pulse (confidence {pulse:.2f})")
    if rhythm.bars < 4:
        findings.append(f"too short musically ({rhythm.bars} bars)")
        score *= 0.7

    return CriticResult(
        id="pulse_locke",
        score=max(0.0, min(1.0, score)),
        blocking=blocking and score < 0.35,
        findings=findings,
        evidence={
            "downbeat_lock": rhythm.downbeat_lock,
            "kick_one_lock": rhythm.kick_one_lock,
            "pulse_confidence": rhythm.pulse_confidence,
            "meter_confidence": rhythm.meter_confidence,
            "bars": rhythm.bars,
        },
    )


def critic_harmony_bach(notes: Sequence, key: str, is_minor: bool) -> CriticResult:
    findings = []
    if not notes:
        return CriticResult(
            id="harmony_bach", score=0.0, blocking=True,
            findings=["no MIDI notes to judge harmony"],
        )
    allowed = _scale(key, is_minor)
    # Allow a few chromatic approach tones
    pitches = []
    for n in notes:
        p = int(n["pitch"] if isinstance(n, dict) else n.pitch)
        ch = int(n.get("channel", 0) if isinstance(n, dict)
                 else getattr(n, "channel", 0))
        if ch == 9:
            continue  # drums out of harmony
        pitches.append(p)
    if not pitches:
        return CriticResult(
            id="harmony_bach", score=0.6, findings=["only drum notes present"],
            evidence={"melodic_notes": 0},
        )
    bad = [p for p in pitches if (p % 12) not in allowed]
    ratio_good = 1.0 - (len(bad) / len(pitches))
    score = ratio_good
    if ratio_good < 0.85:
        findings.append(
            f"{len(bad)}/{len(pitches)} non-diatonic pitches for "
            f"{key} {'minor' if is_minor else 'major'}"
        )
    # Range sanity
    span = max(pitches) - min(pitches)
    if span > 48:
        findings.append(f"extreme pitch span ({span} semitones)")
        score *= 0.9
    return CriticResult(
        id="harmony_bach",
        score=max(0.0, min(1.0, score)),
        blocking=ratio_good < 0.5,
        findings=findings,
        evidence={
            "melodic_notes": len(pitches),
            "non_diatonic": len(bad),
            "diatonic_ratio": round(ratio_good, 4),
        },
    )


def critic_form_abbey(rhythm: RhythmAnalysis, markers: Sequence,
                      track_count: int) -> CriticResult:
    findings = []
    # Prefer some density variation across bars (not flat wallpaper),
    # but not chaos. Sweet spot CV ~ 0.15–0.8
    cv = rhythm.density_cv
    if rhythm.bars <= 4:
        cv_score = 0.55  # short loops: neutral
    elif 0.1 <= cv <= 0.9:
        cv_score = 1.0
    elif cv < 0.1:
        cv_score = 0.45
        findings.append("flat density across bars — little arrangement arc")
    else:
        cv_score = 0.55
        findings.append(f"erratic density CV ({cv:.2f})")

    marker_score = 0.4
    if markers:
        marker_score = min(1.0, 0.4 + 0.2 * len(markers))
    else:
        if rhythm.bars >= 8:
            findings.append("no section markers on a longer piece")

    track_score = 0.3
    if track_count >= 3:
        track_score = 1.0
    elif track_count == 2:
        track_score = 0.7
        findings.append("thin arrangement (fewer than 3 tracks)")
    else:
        findings.append("almost no arrangement layers")

    bar_score = min(1.0, rhythm.bars / 8.0)
    if rhythm.bars < 4:
        findings.append("under-developed form (< 4 bars)")

    score = 0.35 * cv_score + 0.25 * marker_score + 0.25 * track_score + 0.15 * bar_score
    return CriticResult(
        id="form_abbey",
        score=max(0.0, min(1.0, score)),
        findings=findings,
        evidence={
            "density_cv": rhythm.density_cv,
            "markers": len(list(markers or [])),
            "tracks": track_count,
            "bars": rhythm.bars,
        },
    )


def critic_intent_fit(*, style: str, key: str, is_minor: bool,
                      expected_tempo: Optional[float],
                      measured_tempo: float,
                      rhythm: RhythmAnalysis,
                      produced_meta: Optional[dict] = None) -> CriticResult:
    findings = []
    score = 1.0
    meta = produced_meta or {}

    if expected_tempo is not None:
        err = abs(measured_tempo - expected_tempo) / max(expected_tempo, 1.0)
        if err > 0.08:
            findings.append(
                f"tempo drift: wanted {expected_tempo:g}, got {measured_tempo:g}"
            )
            score *= max(0.2, 1.0 - err)

    if meta.get("key") and str(meta["key"]).upper() != str(key).upper():
        findings.append(f"key mismatch: intent {key}, session meta {meta['key']}")
        score *= 0.7

    # Style-specific rhythm expectations
    if style in ("house", "party") and rhythm.kick_one_lock < 0.4:
        findings.append("style intent wants stronger four-on-floor kick lock")
        score *= 0.75
    if style == "emotional" and rhythm.density_mean > 6.0:
        findings.append("emotional intent but very dense groove")
        score *= 0.85

    if rhythm.beats_per_bar not in (3, 4, 5, 6):
        findings.append(f"unusual meter {rhythm.beats_per_bar}/4 vs intent")
        score *= 0.8

    return CriticResult(
        id="intent_fit",
        score=max(0.0, min(1.0, score)),
        findings=findings,
        evidence={
            "expected_tempo": expected_tempo,
            "measured_tempo": measured_tempo,
            "key": key,
            "is_minor": is_minor,
            "style": style,
        },
    )


def critic_mix_hygiene(wav_path: Optional[str], *,
                       expect_vocals: bool = False) -> CriticResult:
    if not wav_path:
        return CriticResult(
            id="mix_hygiene",
            score=0.7,  # neutral when no WAV — don't punish MIDI-only judges
            findings=["no WAV provided — hygiene not scored"],
            evidence={"skipped": True},
        )
    from .audio_review import review_wav
    review = review_wav(wav_path, expect_vocals=expect_vocals, min_duration=4.0)
    score = max(0.0, min(1.0, review.overall / 100.0))
    return CriticResult(
        id="mix_hygiene",
        score=score,
        blocking=review.verdict == "fail",
        findings=list(review.findings),
        evidence={
            "overall": review.overall,
            "verdict": review.verdict,
            "path": review.path,
        },
    )


def _all_notes(bridge) -> list:
    notes = []
    overview = bridge.get_session_overview()
    for t in overview.tracks:
        if t.kind != "midi":
            continue
        notes.extend(bridge.get_midi_notes(t.track_id))
    return notes


def judge_onsets(onsets: Sequence[Onset], *,
                 style: str = "default",
                 key: str = "C",
                 is_minor: bool = False,
                 tempo_bpm: float = 120.0,
                 beats_per_bar: Optional[int] = None,
                 expected_tempo: Optional[float] = None,
                 markers: Optional[Sequence] = None,
                 track_count: int = 0,
                 notes: Optional[Sequence] = None,
                 wav_path: Optional[str] = None,
                 expect_vocals: bool = False,
                 produced_meta: Optional[dict] = None) -> JuryReport:
    style = resolve_style(style)
    weights = dict(STYLE_WEIGHTS[style])
    rhythm = analyze_rhythm(
        onsets, tempo_bpm=tempo_bpm, beats_per_bar=beats_per_bar)

    note_list = list(notes) if notes is not None else [
        {"pitch": o.pitch or 60, "start_beat": o.beat, "channel": 9 if o.role in
         ("kick", "snare", "hat") else 0}
        for o in onsets
    ]

    critics = [
        critic_pulse_locke(rhythm, style),
        critic_harmony_bach(note_list, key, is_minor),
        critic_form_abbey(rhythm, markers or [], track_count),
        critic_intent_fit(
            style=style, key=key, is_minor=is_minor,
            expected_tempo=expected_tempo, measured_tempo=tempo_bpm,
            rhythm=rhythm, produced_meta=produced_meta,
        ),
        critic_mix_hygiene(wav_path, expect_vocals=expect_vocals),
    ]

    # Drop mix weight into others if skipped
    mix = next(c for c in critics if c.id == "mix_hygiene")
    w = dict(weights)
    if mix.evidence.get("skipped"):
        freed = w.pop("mix_hygiene", 0.0)
        for k in list(w):
            w[k] += freed / max(1, len(w))

    overall = 100.0 * sum(w.get(c.id, 0.0) * c.score for c in critics)
    blocking = any(c.blocking for c in critics)
    hints: List[str] = []
    for c in critics:
        for f in c.findings:
            hints.append(f"[{c.id}] {f}")

    if blocking or overall < 45:
        verdict = "fail"
    elif overall < 70:
        verdict = "revise"
    else:
        verdict = "pass"

    # Rhythm summary without full bar dump (keep JSON small)
    rhythm_summary = {
        k: getattr(rhythm, k)
        for k in (
            "tempo_bpm", "beats_per_bar", "bars", "onset_count",
            "pulse_confidence", "meter_confidence", "downbeat_lock",
            "syncopation_mean", "density_mean", "density_cv",
            "residual_mean", "kick_one_lock",
        )
    }

    return JuryReport(
        style=style,
        verdict=verdict,
        overall=round(overall, 1),
        rhythm=rhythm_summary,
        critics=critics,
        weights={k: round(v, 4) for k, v in w.items()},
        improve_hints=hints[:12],
    )


def judge_bridge(bridge, *,
                 style: str = "default",
                 key: str = "C",
                 is_minor: bool = False,
                 expected_tempo: Optional[float] = None,
                 beats_per_bar: Optional[int] = None,
                 wav_path: Optional[str] = None,
                 expect_vocals: bool = False,
                 produced_meta: Optional[dict] = None) -> JuryReport:
    overview = bridge.get_session_overview()
    onsets = onsets_from_bridge(bridge)
    notes = _all_notes(bridge)
    tracks = overview.tracks
    return judge_onsets(
        onsets,
        style=style,
        key=key,
        is_minor=is_minor,
        tempo_bpm=float(overview.tempo),
        beats_per_bar=beats_per_bar,
        expected_tempo=expected_tempo if expected_tempo is not None
        else float(overview.tempo),
        markers=overview.markers,
        track_count=len(tracks),
        notes=notes,
        wav_path=wav_path,
        expect_vocals=expect_vocals,
        produced_meta=produced_meta,
    )


def write_jury_json(report: JuryReport, path: str | Path) -> Path:
    import json
    path = Path(path)
    path.write_text(json.dumps(report.to_dict(), indent=2))
    return path
