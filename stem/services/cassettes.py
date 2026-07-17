"""HTTP-generation cassettes (decoded WAV), keyed by request body hash.

STEM_CASSETTE_DIR  — directory of *.wav cassettes
STEM_CASSETTE_MODE  — replay | record | off
  default: replay when STEM_CASSETTE_DIR is set, else off
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Optional


def cassette_mode() -> str:
    explicit = os.environ.get("STEM_CASSETTE_MODE")
    if explicit:
        return explicit.strip().lower()
    if os.environ.get("STEM_CASSETTE_DIR"):
        return "replay"
    return "off"


def cassette_key(body: dict) -> str:
    payload = json.dumps(body, sort_keys=True, default=str)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def cassette_path(body: dict) -> Optional[Path]:
    root = os.environ.get("STEM_CASSETTE_DIR")
    if not root:
        return None
    return Path(root) / f"{cassette_key(body)}.wav"
