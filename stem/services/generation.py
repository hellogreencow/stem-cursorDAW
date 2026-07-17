"""Audio generation services.

Interface is final; backends are pluggable. ACE-Step wiring is adapted from
ai-music-daw (the 9.4GB model checkpoints live in the old project at
~/Desktop/ai-music-daw or the iCloud backup — point ACE_STEP_DIR there to
avoid re-downloading). Suno needs SUNO_API_KEY.

generate_sample(prompt) -> path to a WAV on disk, ready for
Bridge.import_audio() to drop onto a track.
"""
import os
import asyncio
import uuid
from pathlib import Path
from typing import Optional

from ..paths import generated_dir

ACE_STEP_DIR = os.environ.get("ACE_STEP_DIR", "")
SUNO_API_KEY = os.environ.get("SUNO_API_KEY", "")


class GenerationService:
    """Facade over ACE-Step (local) and Suno (API)."""

    def __init__(self):
        self.ace_available = bool(ACE_STEP_DIR) and Path(ACE_STEP_DIR).exists()
        self.suno_available = bool(SUNO_API_KEY)

    def status(self) -> dict:
        from .elevenlabs_music import elevenlabs_music
        return {"ace_step": self.ace_available, "suno": self.suno_available,
                "elevenlabs_music": elevenlabs_music.available()}

    def generate_sample(self, prompt: str, duration_seconds: float = 8.0,
                        backend: Optional[str] = None) -> str:
        """Generate audio from a text prompt; returns absolute WAV path.

        STUB until a backend is configured: raises with setup instructions
        rather than silently returning fake audio.
        """
        backend = backend or ("ace_step" if self.ace_available
                              else "suno" if self.suno_available
                              else "elevenlabs_music")
        if backend == "ace_step":
            return self._generate_ace_step(prompt, duration_seconds)
        if backend == "suno":
            return self._generate_suno(prompt, duration_seconds)
        if backend == "elevenlabs_music":
            return self._generate_elevenlabs(prompt, duration_seconds)
        raise RuntimeError(
            "No generation backend configured. Set ACE_STEP_DIR to the "
            "ACE-Step checkout (e.g. ~/Desktop/ai-music-daw with its models/ "
            "and acestep-env/), set SUNO_API_KEY, or set ELEVENLABS_API_KEY.")

    def _generate_ace_step(self, prompt: str, duration: float) -> str:
        out = generated_dir() / f"ace_{uuid.uuid4().hex[:8]}.wav"
        # Adapted pipeline from ai-music-daw/backend/services/ace_step_service.py:
        # shell out to the acestep CLI inside its own venv so torch deps stay
        # isolated from the agent process.
        ace = Path(ACE_STEP_DIR)
        cli = ace / "acestep-env" / "bin" / "acestep"
        if not cli.exists():
            raise RuntimeError(f"acestep CLI not found at {cli}")
        import subprocess
        subprocess.run(
            [str(cli), "--prompt", prompt, "--duration", str(duration),
             "--output", str(out)],
            check=True, timeout=600)
        if not out.exists():
            raise RuntimeError("ACE-Step reported success but produced no file")
        return str(out)

    def _generate_suno(self, prompt: str, duration: float) -> str:
        # Salvaged shape from ai-music-daw suno_service.py; needs a live key
        # to verify the current API contract.
        raise RuntimeError("Suno backend wired but unverified — needs "
                           "SUNO_API_KEY and a live test. See GAPS.md.")

    def _generate_elevenlabs(self, prompt: str, duration: float) -> str:
        from .elevenlabs_music import elevenlabs_music
        if not elevenlabs_music.available():
            raise RuntimeError(
                "No generation backend configured. Set ACE_STEP_DIR, "
                "SUNO_API_KEY, or ELEVENLABS_API_KEY.")
        return elevenlabs_music.generate(prompt, duration, instrumental=True)


generation_service = GenerationService()
