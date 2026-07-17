"""Local sample library index + token search (M2 MVP).

D008: Metadata/path/tag scoring first — no embedding model required.
Index file lives at STEM_HOME/sample_index.json by default.
"""
from __future__ import annotations

import hashlib
import json
import re
import wave
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional

from ..paths import stem_home

AUDIO_EXTS = {".wav", ".wave", ".aif", ".aiff", ".flac", ".mp3", ".ogg"}


@dataclass
class SampleEntry:
    id: str
    path: str
    name: str
    tags: List[str] = field(default_factory=list)
    duration_seconds: Optional[float] = None
    sample_rate: Optional[int] = None


def _tokenize(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", text.lower()) if t}


def _wav_meta(path: Path) -> tuple[Optional[float], Optional[int]]:
    if path.suffix.lower() not in {".wav", ".wave"}:
        return None, None
    try:
        with wave.open(str(path), "rb") as w:
            rate = w.getframerate()
            frames = w.getnframes()
            dur = frames / float(rate) if rate else None
            return dur, rate
    except Exception:
        return None, None


def _tags_from_path(path: Path, root: Path) -> list[str]:
    rel = path.relative_to(root) if root in path.parents or path.parent == root else path
    parts = list(rel.parts[:-1]) + [path.stem]
    tags = []
    for part in parts:
        tags.extend(t for t in _tokenize(part) if t)
    # de-dupe preserving order
    seen = set()
    out = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


class SampleIndex:
    def __init__(self, index_path: Optional[Path] = None):
        self.index_path = Path(index_path) if index_path else stem_home() / "sample_index.json"
        self.entries: list[SampleEntry] = []

    def load(self) -> "SampleIndex":
        if not self.index_path.exists():
            self.entries = []
            return self
        data = json.loads(self.index_path.read_text())
        self.entries = [SampleEntry(**row) for row in data.get("entries", [])]
        return self

    def save(self) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "entries": [asdict(e) for e in self.entries],
        }
        self.index_path.write_text(json.dumps(payload, indent=2))

    def build(self, root: Path) -> int:
        root = Path(root).expanduser().resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"sample root not found: {root}")
        entries = []
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in AUDIO_EXTS:
                continue
            dur, rate = _wav_meta(path)
            entry_id = "smp_" + hashlib.sha1(str(path).encode()).hexdigest()[:12]
            entries.append(SampleEntry(
                id=entry_id,
                path=str(path),
                name=path.stem,
                tags=_tags_from_path(path, root),
                duration_seconds=dur,
                sample_rate=rate,
            ))
        self.entries = entries
        self.save()
        return len(entries)

    def search(self, query: str, limit: int = 10) -> list[dict]:
        q_tokens = _tokenize(query)
        if not q_tokens:
            return []
        scored = []
        for entry in self.entries:
            hay = _tokenize(entry.name) | set(entry.tags) | _tokenize(entry.path)
            overlap = q_tokens & hay
            if not overlap:
                continue
            # Prefer denser overlap and shorter names (more specific hits).
            score = len(overlap) * 10 + sum(3 for t in overlap if t in _tokenize(entry.name))
            score -= min(len(entry.name), 20) * 0.01
            scored.append((score, entry))
        scored.sort(key=lambda pair: (-pair[0], pair[1].name))
        out = []
        for score, entry in scored[: max(1, min(limit, 50))]:
            row = asdict(entry)
            row["score"] = round(score, 3)
            out.append(row)
        return out

    def get(self, sample_id: str) -> Optional[SampleEntry]:
        for entry in self.entries:
            if entry.id == sample_id:
                return entry
        return None


def default_index() -> SampleIndex:
    return SampleIndex().load()
