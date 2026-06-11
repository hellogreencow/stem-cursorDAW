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

OUTPUT_DIR = Path.home() / ".stem" / "generated"
ACE_STEP_DIR = os.environ.get("ACE_STEP_DIR", "")
SUNO_API_KEY = os.environ.get("SUNO_API_KEY", "")


class GenerationService:
    """Facade over ACE-Step (local) and Suno (API)."""

    def __init__(self):
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        self.ace_available = bool(ACE_STEP_DIR) and Path(ACE_STEP_DIR).exists()
        self.suno_available = bool(SUNO_API_KEY)

    def status(self) -> dict:
        return {"ace_step": self.ace_available, "suno": self.suno_available}

    def generate_sample(self, prompt: str, duration_seconds: float = 8.0,
                        backend: Optional[str] = None) -> str:
        """Generate audio from a text prompt; returns absolute WAV path.

        STUB until a backend is configured: raises with setup instructions
        rather than silently returning fake audio.
        """
        backend = backend or ("ace_step" if self.ace_available
                              else "suno" if self.suno_available else None)
        if backend == "ace_step":
            return self._generate_ace_step(prompt, duration_seconds)
        if backend == "suno":
            return self._generate_suno(prompt, duration_seconds)
        raise RuntimeError(
            "No generation backend configured. Set ACE_STEP_DIR to the "
            "ACE-Step checkout (e.g. ~/Desktop/ai-music-daw with its models/ "
            "and acestep-env/) or set SUNO_API_KEY.")

    def _generate_ace_step(self, prompt: str, duration: float) -> str:
        out = OUTPUT_DIR / f"ace_{uuid.uuid4().hex[:8]}.wav"
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


generation_service = GenerationService()
