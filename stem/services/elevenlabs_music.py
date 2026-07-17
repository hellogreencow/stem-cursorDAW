"""ElevenLabs Music — real, official API for AI songs with sung vocals.

The one music+vocals API with a stable public contract and full commercial
licensing (Suno/Udio have no official API). Generates a produced song from a
text prompt; we save the audio so the bridge can drop it on a track.

Key resolution: elevenlabs_api_key in ~/.stem/config.json, or the
ELEVENLABS_API_KEY env var.
"""
import io
import json
import os
import subprocess
import uuid
import zipfile
from pathlib import Path
from typing import List, Optional

import httpx

from ..paths import config_path, generated_dir

API_URL = "https://api.elevenlabs.io/v1/music"
STEM_URL = "https://api.elevenlabs.io/v1/music/stem-separation"


def _api_key() -> Optional[str]:
    key = os.environ.get("ELEVENLABS_API_KEY")
    if key:
        return key
    cfg = config_path()
    if cfg.exists():
        try:
            return json.loads(cfg.read_text()).get("elevenlabs_api_key")
        except json.JSONDecodeError:
            return None
    return None


class ElevenLabsMusic:
    def __init__(self):
        pass

    def available(self) -> bool:
        return bool(_api_key())

    def _require_key(self) -> str:
        key = _api_key()
        if not key:
            raise RuntimeError(
                "No ElevenLabs key. Set ELEVENLABS_API_KEY or add "
                "'elevenlabs_api_key' to ~/.stem/config.json.")
        return key

    def _decode_to_wav(self, encoded: Path, out: Path) -> str:
        """Ardour's importer can make a zero-length region from compressed
        audio. Decode to PCM WAV so import is deterministic."""
        if encoded.stat().st_size < 1000:
            raise RuntimeError("ElevenLabs returned an unexpectedly tiny file")
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(encoded),
             "-c:a", "pcm_s24le", str(out)],
            check=True, timeout=120)
        if not out.exists() or out.stat().st_size < 1000:
            raise RuntimeError("Failed to decode generated audio to WAV")
        return str(out)

    def _compose(self, body: dict) -> str:
        """POST a /v1/music body (prompt OR composition_plan) and return the
        decoded WAV path."""
        key = self._require_key()
        headers = {"Content-Type": "application/json", "xi-api-key": key}
        base = generated_dir() / f"eleven_{uuid.uuid4().hex[:8]}"
        encoded = base.with_suffix(".mp3")
        out = base.with_suffix(".wav")
        with httpx.Client(timeout=300.0) as client:
            resp = client.post(API_URL, headers=headers, json=body)
            if resp.status_code != 200:
                raise RuntimeError(
                    f"ElevenLabs Music API {resp.status_code}: {resp.text[:300]}")
            encoded.write_bytes(resp.content)
        return self._decode_to_wav(encoded, out)

    def generate(self, prompt: str, length_seconds: float = 20.0,
                 instrumental: bool = False, model: str = "music_v2") -> str:
        """Generate a song (with sung vocals unless instrumental). Returns the
        path to the saved WAV. Raises with a clear message on failure."""
        length_ms = int(max(3.0, min(length_seconds, 600.0)) * 1000)
        return self._compose({
            "prompt": prompt,
            "music_length_ms": length_ms,
            "model_id": model,
            "force_instrumental": instrumental,
        })

    def generate_from_plan(self, composition_plan: List[dict],
                           model: str = "music_v2") -> str:
        """Generate from a composition plan: an ordered list of section chunks,
        each {text, duration_ms, positive_styles[], negative_styles[],
        context_adherence}. This is how you get line-/section-level control over
        lyrics, timing, and style. `text` holds the section tag + lyric lines,
        e.g. '[Verse 1]\\nFirst line\\nSecond line'."""
        if not composition_plan:
            raise RuntimeError("composition_plan is empty")
        return self._compose({
            "composition_plan": {"chunks": composition_plan},
            "model_id": model,
        })

    def separate_stems(self, audio_path: str,
                       variation: str = "two_stems_v1") -> dict:
        """Split an audio file into stems. variation 'two_stems_v1' = vocals +
        accompaniment; 'six_stems_v1' = vocals/drums/bass/etc. Returns a dict of
        {stem_name: wav_path}. Works on ANY audio file, not just our own."""
        key = self._require_key()
        src = Path(audio_path)
        if not src.exists():
            raise RuntimeError(f"audio file not found: {audio_path}")
        headers = {"xi-api-key": key}
        files = {"file": (src.name, src.read_bytes(), "audio/wav")}
        data = {"stem_variation_id": variation}
        with httpx.Client(timeout=600.0) as client:
            resp = client.post(STEM_URL, headers=headers, files=files,
                               data=data)
            if resp.status_code != 200:
                raise RuntimeError(
                    f"ElevenLabs stem-separation {resp.status_code}: "
                    f"{resp.text[:300]}")
            payload = resp.content
        stems: dict = {}
        prefix = src.stem
        out_dir = generated_dir()
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            for entry in zf.namelist():
                if entry.endswith("/"):
                    continue
                raw = zf.read(entry)
                name = Path(entry).stem  # e.g. "vocals", "accompaniment"
                enc = out_dir / f"{prefix}_{name}{Path(entry).suffix}"
                enc.write_bytes(raw)
                wav = out_dir / f"{prefix}_{name}.wav"
                try:
                    self._decode_to_wav(enc, wav)
                    stems[name] = str(wav)
                except Exception:
                    stems[name] = str(enc)  # fall back to raw stem file
        if not stems:
            raise RuntimeError("stem separation returned no audio")
        return stems

    def isolate_vocals(self, audio_path: str) -> str:
        """Return the path to JUST the vocal stem of an audio file."""
        stems = self.separate_stems(audio_path, variation="two_stems_v1")
        for name, path in stems.items():
            if "vocal" in name.lower():
                return path
        # some variations label the kept stem differently; if there are exactly
        # two, the non-accompaniment one is the vocal.
        non_backing = [p for n, p in stems.items()
                       if not any(k in n.lower() for k in
                                  ("accomp", "instrument", "backing", "music"))]
        if len(non_backing) == 1:
            return non_backing[0]
        raise RuntimeError(
            f"could not identify a vocal stem among: {sorted(stems)}")


elevenlabs_music = ElevenLabsMusic()
