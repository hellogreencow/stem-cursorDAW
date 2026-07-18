"""Resolves Stem's on-disk home directory.

Default: ~/.stem
Override: STEM_HOME (absolute or relative path)

Decision: Python-side isolation for tests and alternate profiles without
forcing Lua/Ardour to move. Live bridge Lua still reads $HOME/.stem unless
separately installed there — see DECISIONS.md.
"""
from __future__ import annotations

import os
from pathlib import Path


def stem_home() -> Path:
    override = os.environ.get("STEM_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".stem").resolve()


def config_path() -> Path:
    return stem_home() / "config.json"


def generated_dir() -> Path:
    path = stem_home() / "generated"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_stem_home() -> Path:
    path = stem_home()
    path.mkdir(parents=True, exist_ok=True)
    return path
