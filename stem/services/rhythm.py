"""Length-invariant rhythm substrate for the music jury.

Works in *beats and bars*, not absolute seconds. MIDI onsets are preferred
(exact grid); acoustic onsets can be fed as beat-times if tempo is known.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple


@dataclass
class Onset:
    beat: float
    pitch: Optional[int] = None
    velocity: int = 100
    role: str = "other"  # kick | snare | hat | bass | chord | lead | other
    track: str = ""


@dataclass
class BarGroove:
    bar: int
    onset_count: int
    density: float          # onsets per beat in the bar
    syncopation: float      # 0..1 weak/off-beat weight
    downbeat_hit: bool
    strong_beat_hits: int
    residual_mean: float    # mean |phase residual| in beats (0..0.5)
    kick_on_one: bool


@dataclass
class RhythmAnalysis:
    tempo_bpm: float
    beats_per_bar: int
    bars: int
    onset_count: int
    pulse_confidence: float
    meter_confidence: float
    downbeat_lock: float      # fraction of bars with near-beat-0 onset
    syncopation_mean: float
    density_mean: float
    density_cv: float         # coeff of variation across bars (form proxy)
    residual_mean: float
    kick_one_lock: float      # fraction of bars with kick near beat 0
    bar_grooves: List[BarGroove] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    def aggregate_floor(self, attr: str, worst_frac: float = 0.2) -> float:
        """Length-robust floor: mean of the worst fraction of bar values."""
        if not self.bar_grooves:
            return 0.0
        vals = []
        for g in self.bar_grooves:
            if attr == "syncopation":
                vals.append(g.syncopation)
            elif attr == "density":
                vals.append(g.density)
            elif attr == "downbeat":
                vals.append(1.0 if g.downbeat_hit else 0.0)
            elif attr == "residual":
                vals.append(g.residual_mean)
            else:
                raise ValueError(attr)
        vals.sort()
        n = max(1, int(len(vals) * worst_frac))
        return sum(vals[:n]) / n


def drum_role(pitch: int, channel: int = 0) -> str:
    if channel == 9 or pitch in (35, 36, 38, 40, 42, 44, 46, 49, 51):
        if pitch in (35, 36):
            return "kick"
        if pitch in (38, 40):
            return "snare"
        if pitch in (42, 44, 46):
            return "hat"
        return "hat"
    return "other"


def role_from_track(name: str, pitch: int, channel: int) -> str:
    low = (name or "").lower()
    if "drum" in low or channel == 9:
        return drum_role(pitch, channel)
    if "bass" in low:
        return "bass"
    if "chord" in low or "pad" in low:
        return "chord"
    if "lead" in low or "melody" in low:
        return "lead"
    return drum_role(pitch, channel) if channel == 9 else "other"


def onsets_from_notes(notes: Iterable, *, track: str = "",
                      default_channel: int = 0) -> List[Onset]:
    out: List[Onset] = []
    for n in notes:
        if isinstance(n, dict):
            pitch = int(n["pitch"])
            beat = float(n["start_beat"])
            vel = int(n.get("velocity", 100))
            ch = int(n.get("channel", default_channel))
        else:
            pitch = int(n.pitch)
            beat = float(n.start_beat)
            vel = int(getattr(n, "velocity", 100))
            ch = int(getattr(n, "channel", default_channel))
        out.append(Onset(
            beat=beat, pitch=pitch, velocity=vel,
            role=role_from_track(track, pitch, ch), track=track,
        ))
    return out


def onsets_from_bridge(bridge) -> List[Onset]:
    overview = bridge.get_session_overview()
    out: List[Onset] = []
    for t in overview.tracks:
        if t.kind != "midi":
            continue
        notes = bridge.get_midi_notes(t.track_id)
        out.extend(onsets_from_notes(notes, track=t.name))
    out.sort(key=lambda o: o.beat)
    return out


def _phase(beat: float, beats_per_bar: int) -> float:
    """Position within bar in [0, beats_per_bar)."""
    if beats_per_bar <= 0:
        return 0.0
    return beat % float(beats_per_bar)


def _near(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol or abs(a - b) >= (1.0 - tol)


def _beat_strength(phase: float, beats_per_bar: int) -> float:
    """1.0 on downbeat, 0.7 on strong beats, 0.35 on weak, 0.15 off-grid."""
    # Snap to nearest 16th
    step = 0.25
    snapped = round(phase / step) * step
    residual = abs(phase - snapped)
    if residual > 0.08:
        return 0.15
    # Integer beats
    if abs(snapped - round(snapped)) < 1e-6:
        beat_i = int(round(snapped)) % beats_per_bar
        if beat_i == 0:
            return 1.0
        if beats_per_bar == 4 and beat_i in (2,):
            return 0.7
        if beats_per_bar == 3 and beat_i in (0,):
            return 1.0
        return 0.55
    # Off-beat 8ths / 16ths
    return 0.3


def score_meter(onsets: Sequence[Onset], beats_per_bar: int,
                *, tol: float = 0.08) -> float:
    """Phase-coherence of onsets against a candidate meter (0..1)."""
    if not onsets:
        return 0.0
    weights = []
    role_bonus = 0.0
    role_n = 0
    for o in onsets:
        phase = _phase(o.beat, beats_per_bar)
        # Distance to nearest beat grid (quarters)
        nearest = round(phase)
        # also consider half-beats
        candidates = [nearest, round(phase * 2) / 2.0, round(phase * 4) / 4.0]
        dist = min(abs(phase - c) for c in candidates)
        # wrap
        dist = min(dist, abs(dist - beats_per_bar))
        hit = max(0.0, 1.0 - dist / max(tol * 3, 1e-6))
        # Downbeat bonus
        if abs(phase) <= tol or abs(phase - beats_per_bar) <= tol:
            hit = min(1.0, hit + 0.15)
        weights.append(hit * (0.5 + 0.5 * o.velocity / 127.0))
        # Role accents: snares on 2/4 favor 4/4; kick on 0 always helps
        if o.role == "snare" and beats_per_bar == 4:
            role_n += 1
            if abs(phase - 1.0) <= tol or abs(phase - 3.0) <= tol:
                role_bonus += 1.0
        elif o.role == "kick":
            role_n += 1
            if abs(phase) <= tol:
                role_bonus += 1.0
    base = sum(weights) / len(weights)
    if role_n:
        base = 0.85 * base + 0.15 * (role_bonus / role_n)
    # Mild prior for common meters (tie-break only)
    if beats_per_bar == 4:
        base += 0.02
    elif beats_per_bar == 3:
        base += 0.01
    return max(0.0, min(1.0, base))


def infer_meter(onsets: Sequence[Onset],
                candidates: Sequence[int] = (4, 3, 5, 6, 7)) -> Tuple[int, float]:
    best_m, best_s = 4, -1.0
    for m in candidates:
        s = score_meter(onsets, m)
        # Prefer denser common meters on near-ties
        if s > best_s + 0.015 or (abs(s - best_s) <= 0.015 and m == 4):
            best_m, best_s = m, s
    return best_m, max(0.0, min(1.0, best_s))


def analyze_rhythm(onsets: Sequence[Onset], *,
                   tempo_bpm: float = 120.0,
                   beats_per_bar: Optional[int] = None,
                   min_bars: int = 1) -> RhythmAnalysis:
    """Analyze groove in bar units. Length-invariant aggregates."""
    ons = sorted(onsets, key=lambda o: o.beat)
    if not ons:
        return RhythmAnalysis(
            tempo_bpm=tempo_bpm, beats_per_bar=beats_per_bar or 4, bars=0,
            onset_count=0, pulse_confidence=0.0, meter_confidence=0.0,
            downbeat_lock=0.0, syncopation_mean=0.0, density_mean=0.0,
            density_cv=0.0, residual_mean=0.0, kick_one_lock=0.0,
        )

    if beats_per_bar is None:
        beats_per_bar, meter_conf = infer_meter(ons)
    else:
        meter_conf = score_meter(ons, beats_per_bar)

    last_beat = ons[-1].beat
    bars = max(min_bars, int(last_beat // beats_per_bar) + 1)

    # Pulse confidence: regularity of inter-onset intervals vs beat grid
    ioi = [ons[i + 1].beat - ons[i].beat for i in range(len(ons) - 1)]
    ioi = [x for x in ioi if 0.05 < x < beats_per_bar * 2]
    if ioi:
        # Prefer intervals near 0.25/0.5/1.0
        def grid_fit(x: float) -> float:
            targets = (0.25, 0.5, 1.0, 1.5, 2.0)
            d = min(abs(x - t) for t in targets)
            return max(0.0, 1.0 - d / 0.25)
        pulse_conf = sum(grid_fit(x) for x in ioi) / len(ioi)
    else:
        pulse_conf = 0.0

    grooves: List[BarGroove] = []
    for b in range(bars):
        start = b * beats_per_bar
        end = start + beats_per_bar
        bar_ons = [o for o in ons if start <= o.beat < end]
        if not bar_ons:
            grooves.append(BarGroove(
                bar=b, onset_count=0, density=0.0, syncopation=0.0,
                downbeat_hit=False, strong_beat_hits=0, residual_mean=0.5,
                kick_on_one=False,
            ))
            continue

        sync_w = 0.0
        strength_w = 0.0
        residuals = []
        strong = 0
        downbeat = False
        kick_one = False
        for o in bar_ons:
            phase = o.beat - start
            strength = _beat_strength(phase, beats_per_bar)
            # syncopation: high when weak/off-beat
            sync_w += (1.0 - strength) * (0.5 + 0.5 * o.velocity / 127.0)
            strength_w += 0.5 + 0.5 * o.velocity / 127.0
            nearest = round(phase * 4) / 4.0
            residuals.append(abs(phase - nearest))
            if strength >= 0.7:
                strong += 1
            if phase <= 0.08 or abs(phase - beats_per_bar) <= 0.08:
                downbeat = True
                if o.role == "kick":
                    kick_one = True
            # kick slightly early/late still counts
            if o.role == "kick" and phase <= 0.12:
                kick_one = True
                downbeat = True

        sync = sync_w / max(strength_w, 1e-9)
        sync = max(0.0, min(1.0, sync))
        grooves.append(BarGroove(
            bar=b,
            onset_count=len(bar_ons),
            density=len(bar_ons) / float(beats_per_bar),
            syncopation=sync,
            downbeat_hit=downbeat,
            strong_beat_hits=strong,
            residual_mean=sum(residuals) / len(residuals),
            kick_on_one=kick_one,
        ))

    densities = [g.density for g in grooves]
    dens_mean = sum(densities) / len(densities)
    if dens_mean > 1e-9:
        var = sum((d - dens_mean) ** 2 for d in densities) / len(densities)
        dens_cv = (var ** 0.5) / dens_mean
    else:
        dens_cv = 0.0

    downbeat_lock = sum(1 for g in grooves if g.downbeat_hit) / len(grooves)
    kick_lock = sum(1 for g in grooves if g.kick_on_one) / len(grooves)
    sync_mean = sum(g.syncopation for g in grooves) / len(grooves)
    resid_mean = sum(g.residual_mean for g in grooves) / len(grooves)

    return RhythmAnalysis(
        tempo_bpm=float(tempo_bpm),
        beats_per_bar=int(beats_per_bar),
        bars=bars,
        onset_count=len(ons),
        pulse_confidence=round(pulse_conf, 4),
        meter_confidence=round(meter_conf, 4),
        downbeat_lock=round(downbeat_lock, 4),
        syncopation_mean=round(sync_mean, 4),
        density_mean=round(dens_mean, 4),
        density_cv=round(dens_cv, 4),
        residual_mean=round(resid_mean, 4),
        kick_one_lock=round(kick_lock, 4),
        bar_grooves=grooves,
    )


def make_grid_onsets(bars: int, beats_per_bar: int = 4, *,
                     pattern: str = "four_on_floor",
                     swing: float = 0.0) -> List[Onset]:
    """Synthetic onset trains for harness tests."""
    out: List[Onset] = []
    for b in range(bars):
        base = b * beats_per_bar
        if pattern == "four_on_floor":
            for beat in range(beats_per_bar):
                out.append(Onset(beat=base + beat, pitch=36, role="kick",
                                 velocity=110))
                # offbeat hats
                hat_t = base + beat + 0.5 + swing
                out.append(Onset(beat=hat_t, pitch=42, role="hat", velocity=80))
            # snare on 2 and 4
            for beat in (1, 3):
                if beat < beats_per_bar:
                    out.append(Onset(beat=base + beat, pitch=38, role="snare",
                                     velocity=100))
        elif pattern == "weak_only":
            # Evil twin: only off-beats
            for beat in range(beats_per_bar):
                out.append(Onset(beat=base + beat + 0.5, pitch=42, role="hat",
                                 velocity=90))
        elif pattern == "waltz":
            for beat in (0, 1, 2):
                role = "kick" if beat == 0 else "hat"
                pitch = 36 if beat == 0 else 42
                out.append(Onset(beat=base + beat, pitch=pitch, role=role,
                                 velocity=100 if beat == 0 else 70))
        else:
            raise ValueError(pattern)
    return out
