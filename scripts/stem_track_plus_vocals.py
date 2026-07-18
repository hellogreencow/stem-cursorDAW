#!/usr/bin/env python3
"""Correct dogfood pipeline: Stem builds the track, ElevenLabs adds vocals only.

1) StemScript + arrange_loop_to_song → MockBridge MIDI
2) Offline synth render → instrumental WAV
3) ElevenLabs generate + isolate_vocals → vocal stem only
4) Mix instrumental + vocals → final WAV
5) review_audio gate

Requires ELEVENLABS_API_KEY for step 3 only. Never commit the key.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import struct
import subprocess
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stem.bridge.mock import MockBridge
from stem.services.audio_review import review_wav, write_review_json
from stem.services.elevenlabs_music import elevenlabs_music
from stem.tools.generation_tools import _provider_safe_vocal_prompt

OUT_DIR = ROOT / "examples" / "dogfood"
DURATION = float(os.environ.get("STEM_SONG_SECONDS", "32"))
LYRICS = os.environ.get(
    "STEM_SONG_LYRICS",
    "City lights keep calling my name\n"
    "I walk through the neon and the rain\n"
    "Hold the night until it breaks\n"
    "Find my resolve in the bass",
)


def _load_render_mod():
    spec = importlib.util.spec_from_file_location(
        "render_mock_song", ROOT / "scripts" / "render_mock_song.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _read_wav(path: Path):
    with wave.open(str(path), "rb") as w:
        ch = w.getnchannels()
        sw = w.getsampwidth()
        rate = w.getframerate()
        raw = w.readframes(w.getnframes())
    if sw != 2:
        tmp = path.with_suffix(".s16.wav")
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(path),
             "-c:a", "pcm_s16le", str(tmp)],
            check=True, timeout=120)
        return _read_wav(tmp)
    samples = struct.unpack("<" + "h" * (len(raw) // 2), raw)
    frames = []
    for i in range(0, len(samples), ch):
        frame = samples[i:i + ch]
        frames.append(tuple(s / 32768.0 for s in frame))
    return rate, ch, frames


def mix_wavs(instrumental: Path, vocals: Path, out: Path,
             vocal_gain: float = 0.9, bed_gain: float = 0.72) -> Path:
    ir, ich, iframes = _read_wav(instrumental)
    vr, vch, vframes = _read_wav(vocals)
    if vr != ir:
        tmp = vocals.with_name(vocals.stem + f"_{ir}hz.wav")
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(vocals),
             "-ar", str(ir), "-c:a", "pcm_s16le", str(tmp)],
            check=True, timeout=120)
        vr, vch, vframes = _read_wav(tmp)

    n = max(len(iframes), len(vframes))
    mixed_l, mixed_r = [], []
    for i in range(n):
        ib = iframes[i] if i < len(iframes) else (0.0, 0.0)
        vb = vframes[i] if i < len(vframes) else (0.0, 0.0)
        il = ib[0]
        iright = ib[1] if len(ib) > 1 else il
        vl = vb[0]
        vright = vb[1] if len(vb) > 1 else vl
        mixed_l.append(il * bed_gain + vl * vocal_gain)
        mixed_r.append(iright * bed_gain + vright * vocal_gain)

    peak = max(1e-9, max(abs(x) for x in mixed_l + mixed_r))
    scale = 0.9 / peak
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "w") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(ir)
        buf = bytearray()
        for l, r in zip(mixed_l, mixed_r):
            buf += struct.pack(
                "<hh",
                int(max(-32767, min(32767, l * scale * 32767))),
                int(max(-32767, min(32767, r * scale * 32767))),
            )
        w.writeframes(buf)
    return out


def main() -> int:
    if not elevenlabs_music.available():
        print("ELEVENLABS_API_KEY required for vocal stem only", file=sys.stderr)
        return 2

    render_mod = _load_render_mod()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    instrumental = OUT_DIR / "stem_instrumental_house.wav"
    vocals_out = OUT_DIR / "stem_vocals_only.wav"
    final = OUT_DIR / "stem_vocal_house.wav"

    print("=== 1) Stem instrumental (MIDI → synth) ===")
    bridge = MockBridge(name="stem-plus-vocals", tempo=124)
    summary = render_mod.build_song(bridge)
    render_mod.render(bridge, instrumental, seconds=DURATION)
    print(summary)
    print(f"instrumental: {instrumental} ({instrumental.stat().st_size} bytes)")

    print("=== 2) ElevenLabs VOCALS ONLY (isolate stem) ===")
    vocal_prompt = _provider_safe_vocal_prompt(
        "intimate female lead vocal, deep house, emotional, clear diction",
        LYRICS,
    )
    full = elevenlabs_music.generate(
        vocal_prompt, length_seconds=min(DURATION, 40.0), instrumental=False)
    vocal_path = elevenlabs_music.isolate_vocals(full)
    shutil.copyfile(vocal_path, vocals_out)
    print(f"vocals: {vocals_out} ({vocals_out.stat().st_size} bytes)")

    print("=== 3) Mix Stem bed + vocal stem ===")
    mix_wavs(instrumental, vocals_out, final)
    review = review_wav(
        final, expect_vocals=True, min_duration=min(16.0, DURATION * 0.5))
    # Keep review path relative for the repo artifact
    data = review.to_dict()
    data["path"] = "examples/dogfood/stem_vocal_house.wav"
    (OUT_DIR / "stem_vocal_house.review.json").write_text(json.dumps(data, indent=2))

    meta = {
        "pipeline": "stem_instrumental + elevenlabs_vocals_only",
        "not": "full elevenlabs song",
        "instrumental": instrumental.name,
        "vocals": vocals_out.name,
        "final": final.name,
        "stem_summary": summary,
        "review": data,
    }
    (OUT_DIR / "stem_vocal_house.pipeline.json").write_text(json.dumps(meta, indent=2))
    (OUT_DIR / "stem_vocal_house.txt").write_text(
        "CORRECT PIPELINE: Stem track + ElevenLabs vocals only\n"
        f"1) Stem MIDI/arrange → {instrumental.name}\n"
        f"2) ElevenLabs isolate_vocals → {vocals_out.name}\n"
        f"3) Mix → {final.name}\n"
        f"review: verdict={review.verdict} overall={review.overall}\n"
        "Rebuild: ELEVENLABS_API_KEY=… python scripts/stem_track_plus_vocals.py\n"
    )
    print(json.dumps({
        "pipeline": meta["pipeline"],
        "verdict": review.verdict,
        "overall": review.overall,
        "findings": review.findings,
        "final_bytes": final.stat().st_size,
    }, indent=2))
    return 0 if review.verdict in {"pass", "revise"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
