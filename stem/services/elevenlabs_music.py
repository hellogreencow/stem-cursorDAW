"""ElevenLabs Music — real, official API for AI songs with sung vocals.

The one music+vocals API with a stable public contract and full commercial
licensing (Suno/Udio have no official API). Generates a produced song from a
text prompt; we save the audio so the bridge can drop it on a track.

Key resolution: elevenlabs_api_key in ~/.stem/config.json, or the
ELEVENLABS_API_KEY env var.
"""
import json
import os
import uuid
from pathlib import Path
from typing import Optional

import httpx

CONFIG = Path.home() / ".stem" / "config.json"
OUTPUT_DIR = Path.home() / ".stem" / "generated"
API_URL = "https://api.elevenlabs.io/v1/music"


def _api_key() -> Optional[str]:
    key = os.environ.get("ELEVENLABS_API_KEY")
    if key:
        return key
    if CONFIG.exists():
        try:
            return json.loads(CONFIG.read_text()).get("elevenlabs_api_key")
        except json.JSONDecodeError:
            return None
    return None


class ElevenLabsMusic:
    def __init__(self):
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    def available(self) -> bool:
        return bool(_api_key())

    def generate(self, prompt: str, length_seconds: float = 20.0,
                 instrumental: bool = False, model: str = "music_v2") -> str:
        """Generate a song (with sung vocals unless instrumental). Returns the
        path to the saved audio file. Raises with a clear message on failure."""
        key = _api_key()
        if not key:
            raise RuntimeError(
                "No ElevenLabs key. Set ELEVENLABS_API_KEY or add "
                "'elevenlabs_api_key' to ~/.stem/config.json.")

        length_ms = int(max(3.0, min(length_seconds, 600.0)) * 1000)
        body = {
            "prompt": prompt,
            "music_length_ms": length_ms,
            "model_id": model,
            "force_instrumental": instrumental,
        }
        headers = {"Content-Type": "application/json", "xi-api-key": key}

        out = OUTPUT_DIR / f"eleven_{uuid.uuid4().hex[:8]}.mp3"
        # generation can take a while for longer clips
        with httpx.Client(timeout=300.0) as client:
            resp = client.post(API_URL, headers=headers, json=body)
            if resp.status_code != 200:
                detail = resp.text[:300]
                raise RuntimeError(
                    f"ElevenLabs Music API {resp.status_code}: {detail}")
            out.write_bytes(resp.content)

        if out.stat().st_size < 1000:
            raise RuntimeError("ElevenLabs returned an unexpectedly tiny file")
        return str(out)


elevenlabs_music = ElevenLabsMusic()
