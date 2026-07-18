"""Project memory tools (M4.1)."""
from typing import Optional

from pydantic import BaseModel, Field

from .core import registry
from ..services.memory import load_memory, update_memory


class Empty(BaseModel):
    pass


@registry.register(
    "recall_memory",
    "Recall durable producer preferences for this project (key, tempo, genre, "
    "recent samples/plugins, notes). Use when choosing defaults.",
    Empty)
def recall_memory(args, ctx):
    project_id = getattr(ctx, "project_id", "default")
    mem = load_memory(project_id)
    return {
        "project_id": mem.project_id,
        "preferred_key": mem.preferred_key,
        "preferred_tempo": mem.preferred_tempo,
        "preferred_genre": mem.preferred_genre,
        "recent_samples": mem.recent_samples,
        "recent_plugins": mem.recent_plugins,
        "notes": mem.notes,
    }


class UpdateMemory(BaseModel):
    preferred_key: Optional[str] = None
    preferred_tempo: Optional[float] = Field(default=None, gt=20, lt=400)
    preferred_genre: Optional[str] = None
    add_sample: Optional[str] = None
    add_plugin: Optional[str] = None
    add_note: Optional[str] = Field(
        default=None, description="Short production note to remember")


@registry.register(
    "update_memory",
    "Update durable project memory (preferences/notes). Does not mutate the "
    "DAW session. Secrets/API keys are rejected/redacted.",
    UpdateMemory)
def update_memory_tool(args, ctx):
    project_id = getattr(ctx, "project_id", "default")
    mem = update_memory(
        project_id,
        preferred_key=args.preferred_key,
        preferred_tempo=args.preferred_tempo,
        preferred_genre=args.preferred_genre,
        add_sample=args.add_sample,
        add_plugin=args.add_plugin,
        add_note=args.add_note,
    )
    return {
        "ok": True,
        "project_id": mem.project_id,
        "preferred_key": mem.preferred_key,
        "preferred_tempo": mem.preferred_tempo,
        "preferred_genre": mem.preferred_genre,
        "notes": mem.notes,
    }
