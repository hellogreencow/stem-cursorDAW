#!/usr/bin/env python3
"""Build a Stem song on MockBridge and render a stereo WAV (no API keys).

Usage:
  python scripts/render_mock_song.py [out.wav]

Uses StemScript + arrange_loop_to_song so the artifact matches the agent stack.
"""
from __future__ import annotations

import math
import struct
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stem.agent.loop import ToolContext
from stem.agent.tasks import execute_task
from stem.bridge.mock import MockBridge
from stem.language import execute_stemscript

SR = 44100


def _env(t: float, attack: float, release: float, dur: float) -> float:
    if t < 0 or t > dur:
        return 0.0
    if t < attack:
        return t / max(attack, 1e-4)
    if t > dur - release:
        return max(0.0, (dur - t) / max(release, 1e-4))
    return 1.0


def _midi_to_hz(pitch: int) -> float:
    return 440.0 * (2.0 ** ((pitch - 69) / 12.0))


def _noise(idx: int) -> float:
    return ((idx * 1103515245 + 12345) & 0x7FFFFFFF) / 0x7FFFFFFF - 0.5


def render(bridge: MockBridge, path: Path, seconds: float = 24.0) -> Path:
    n = int(SR * seconds)
    left = [0.0] * n
    right = [0.0] * n
    beat_sec = 60.0 / bridge.tempo

    for track in bridge.tracks.values():
        if track.kind != "midi":
            continue
        name = track.name.lower()
        notes = bridge.notes.get(track.track_id, [])
        if "drum" in name:
            gain, pan = 0.55, 0.0
        elif "bass" in name:
            gain, pan = 0.48, -0.2
        elif "chord" in name:
            gain, pan = 0.30, 0.15
        elif "lead" in name:
            gain, pan = 0.38, 0.3
        else:
            gain, pan = 0.32, 0.0
        l_amt = 0.5 * (1.0 - pan)
        r_amt = 0.5 * (1.0 + pan)

        for note in notes:
            start = note.start_beat * beat_sec
            dur = max(0.04, note.length_beats * beat_sec)
            vel = max(1, min(127, note.velocity)) / 127.0
            freq = _midi_to_hz(note.pitch)
            is_drum = "drum" in name or note.channel == 9
            i0 = int(start * SR)
            length = int(dur * SR)
            for i in range(length):
                idx = i0 + i
                if idx >= n:
                    break
                t = i / SR
                if is_drum:
                    if note.pitch <= 40:  # kick
                        f = 60.0 * (1.0 - 0.7 * min(1.0, t / dur))
                        sample = math.sin(2 * math.pi * f * t)
                        sample *= _env(t, 0.002, 0.12, dur) * math.exp(-6.0 * t)
                    elif note.pitch <= 45:  # snare
                        sample = (0.35 * math.sin(2 * math.pi * 180 * t)
                                  + 0.65 * _noise(idx))
                        sample *= _env(t, 0.001, 0.08, dur) * math.exp(-10.0 * t)
                    else:  # hat
                        sample = _noise(idx) * _env(
                            t, 0.0005, 0.03, min(dur, 0.08))
                        sample *= math.exp(-40.0 * t)
                elif "bass" in name:
                    sample = math.sin(2 * math.pi * freq * t) * math.exp(-1.8 * t)
                    sample *= _env(t, 0.005, 0.06, dur)
                else:
                    phase = 2 * math.pi * freq * t
                    sample = 0.65 * math.sin(phase) + 0.25 * math.sin(2 * phase)
                    sample *= _env(t, 0.01, 0.1, dur)

                sample *= gain * vel
                left[idx] += sample * (2.0 * l_amt)
                right[idx] += sample * (2.0 * r_amt)

    peak = max(1e-9, max(abs(x) for x in left), max(abs(x) for x in right))
    scale = 0.9 / peak
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "w") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        frames = bytearray()
        for i in range(n):
            lv = int(max(-32767, min(32767, left[i] * scale * 32767)))
            rv = int(max(-32767, min(32767, right[i] * scale * 32767)))
            frames += struct.pack("<hh", lv, rv)
        w.writeframes(frames)
    return path


def build_song(bridge: MockBridge) -> str:
    script = (ROOT / "examples/dogfood/house_beat.stem").read_text()
    ctx = ToolContext(bridge=bridge)
    msg = execute_stemscript(script, ctx)
    result = execute_task(
        "arrange_loop_to_song", ctx,
        confirm=True, key="F", tempo=124, is_minor=True,
    )
    return f"{msg}\narrange_ok={result.get('ok')} checklist={result.get('checklist')}"


def main(argv: list[str]) -> int:
    out = Path(argv[1]) if len(argv) > 1 else (
        ROOT / "examples/dogfood/stem_f_minor_house.wav"
    )
    bridge = MockBridge(name="stem-dogfood", tempo=124)
    summary = build_song(bridge)
    render(bridge, out, seconds=24.0)
    meta = out.with_suffix(".txt")
    meta.write_text(
        "Stem offline dogfood render\n"
        "Source: examples/dogfood/house_beat.stem + arrange_loop_to_song\n"
        "Backend: theory/tools → simple synth WAV (no ElevenLabs/ACE/Suno)\n"
        f"tempo={bridge.tempo} tracks={len(bridge.tracks)}\n"
        f"{summary}\n"
        f"file={out.name}\n"
        "Rebuild: python scripts/render_mock_song.py\n"
    )
    print(meta.read_text())
    print(f"Wrote {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
