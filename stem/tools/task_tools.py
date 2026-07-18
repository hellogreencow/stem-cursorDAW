"""Autonomous task tools (M4.2) — confirm-gated."""
from typing import Optional

from pydantic import BaseModel, Field

from .core import registry
from ..agent.tasks import execute_task, list_tasks


class Empty(BaseModel):
    pass


@registry.register(
    "list_tasks",
    "List autonomous multi-step task modes (arrange, rough mix, …) and whether "
    "they are destructive / need confirm.",
    Empty)
def list_tasks_tool(args, ctx):
    return {"tasks": list_tasks()}


class RunTask(BaseModel):
    task: str = Field(
        description="Task id from list_tasks, e.g. arrange_loop_to_song "
                    "or rough_mix")
    confirm: bool = Field(
        default=False,
        description="Must be true to execute. False/omit returns a plan "
                    "preview only (required for destructive tasks).")
    key: str = Field(default="C", description="Tonal center for arrange tasks")
    is_minor: bool = Field(default=False)
    tempo: float = Field(default=120.0, gt=20, lt=400)


@registry.register(
    "run_task",
    "Run (or preview) an autonomous multi-step production task. "
    "Destructive tasks ALWAYS require confirm=true. Without confirm, returns "
    "needs_confirm + plan. Uses only an allow-listed tool subset with a step cap. "
    "Nested tool calls carry their own undo action_ids.",
    RunTask)
def run_task(args, ctx):
    return execute_task(
        args.task, ctx,
        confirm=args.confirm,
        key=args.key,
        tempo=args.tempo,
        is_minor=args.is_minor,
    )
