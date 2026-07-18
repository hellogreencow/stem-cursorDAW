#!/usr/bin/env python3
"""Deprecated entrypoint — use stem_track_plus_vocals.py.

Oli's intended pipeline is Stem instrumental + ElevenLabs vocals only.
This wrapper forwards to that script so old docs/commands stay honest.
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

print(
    "NOTE: forwarding to scripts/stem_track_plus_vocals.py "
    "(Stem bed + vocals only; not a full ElevenLabs song).",
    file=sys.stderr,
)
target = Path(__file__).resolve().parent / "stem_track_plus_vocals.py"
sys.argv[0] = str(target)
runpy.run_path(str(target), run_name="__main__")
