"""Per-project producer memory (M4.1).

Stored at STEM_HOME/memory/<project_id>.json. Injected into the agent system
prompt and readable/writable via tools.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, List, Optional

from ..paths import stem_home

SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|secret|token|password|authorization|credential)", re.I)
SECRET_VAL_RE = re.compile(r"\b(sk-|sk-ant-|or-|ghp_|xox[baprs]-)\S+", re.I)
MAX_NOTES = 12
MAX_NOTE_LEN = 200
MAX_LIST = 20


@dataclass
class ProjectMemory:
    project_id: str = "default"
    preferred_key: Optional[str] = None
    preferred_tempo: Optional[float] = None
    preferred_genre: Optional[str] = None
    recent_samples: List[str] = field(default_factory=list)
    recent_plugins: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


def memory_dir() -> Path:
    path = stem_home() / "memory"
    path.mkdir(parents=True, exist_ok=True)
    return path


def memory_path(project_id: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", project_id.strip()) or "default"
    return memory_dir() / f"{safe}.json"


def _scrub_string(value: str) -> str:
    return SECRET_VAL_RE.sub("[redacted]", value)


def redact(obj: Any) -> Any:
    """Drop secret-like keys and scrub token-shaped values."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if SECRET_KEY_RE.search(str(k)):
                continue
            out[k] = redact(v)
        return out
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    if isinstance(obj, str):
        return _scrub_string(obj)
    return obj


def load_memory(project_id: str = "default") -> ProjectMemory:
    path = memory_path(project_id)
    if not path.exists():
        return ProjectMemory(project_id=project_id)
    try:
        raw = redact(json.loads(path.read_text()))
    except (json.JSONDecodeError, OSError):
        return ProjectMemory(project_id=project_id)
    known = {f.name for f in ProjectMemory.__dataclass_fields__.values()}
    data = {k: v for k, v in raw.items() if k in known}
    data["project_id"] = project_id
    mem = ProjectMemory(**data)
    mem.recent_samples = list(mem.recent_samples)[:MAX_LIST]
    mem.recent_plugins = list(mem.recent_plugins)[:MAX_LIST]
    mem.notes = [n[:MAX_NOTE_LEN] for n in mem.notes][:MAX_NOTES]
    return mem


def save_memory(mem: ProjectMemory) -> Path:
    path = memory_path(mem.project_id)
    payload = redact(asdict(mem))
    path.write_text(json.dumps(payload, indent=2))
    return path


def update_memory(project_id: str, **fields) -> ProjectMemory:
    mem = load_memory(project_id)
    if "preferred_key" in fields and fields["preferred_key"] is not None:
        mem.preferred_key = str(fields["preferred_key"])[:32]
    if "preferred_tempo" in fields and fields["preferred_tempo"] is not None:
        tempo = float(fields["preferred_tempo"])
        if 20 <= tempo <= 400:
            mem.preferred_tempo = tempo
    if "preferred_genre" in fields and fields["preferred_genre"] is not None:
        mem.preferred_genre = str(fields["preferred_genre"])[:64]
    if fields.get("add_sample"):
        s = _scrub_string(str(fields["add_sample"]))[:200]
        mem.recent_samples = [s] + [x for x in mem.recent_samples if x != s]
        mem.recent_samples = mem.recent_samples[:MAX_LIST]
    if fields.get("add_plugin"):
        s = _scrub_string(str(fields["add_plugin"]))[:120]
        mem.recent_plugins = [s] + [x for x in mem.recent_plugins if x != s]
        mem.recent_plugins = mem.recent_plugins[:MAX_LIST]
    if fields.get("add_note"):
        note = _scrub_string(str(fields["add_note"]))[:MAX_NOTE_LEN]
        if note:
            mem.notes = [note] + mem.notes
            mem.notes = mem.notes[:MAX_NOTES]
    save_memory(mem)
    return mem


def format_memory_block(mem: ProjectMemory) -> str:
    """Short ambient context for the system prompt."""
    lines = [f"Project memory ({mem.project_id}):"]
    if mem.preferred_key:
        lines.append(f"- preferred key: {mem.preferred_key}")
    if mem.preferred_tempo is not None:
        lines.append(f"- preferred tempo: {mem.preferred_tempo:g} BPM")
    if mem.preferred_genre:
        lines.append(f"- preferred genre: {mem.preferred_genre}")
    if mem.recent_plugins:
        lines.append("- recent plugins: " + ", ".join(mem.recent_plugins[:5]))
    if mem.recent_samples:
        lines.append("- recent samples: " + ", ".join(mem.recent_samples[:5]))
    if mem.notes:
        lines.append("- notes: " + " | ".join(mem.notes[:4]))
    if len(lines) == 1:
        return ""
    lines.append(
        "Honor these preferences as defaults unless the user overrides them.")
    return "\n".join(lines)
