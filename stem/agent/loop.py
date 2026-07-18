"""Stem agent loop: any LLM provider + tool registry against a Bridge.

Provider-agnostic: the loop speaks the neutral message format from
providers.py; Anthropic/OpenAI/OpenRouter/custom endpoints are adapters.
"""
import json
from dataclasses import dataclass
from typing import Optional

from ..bridge.base import Bridge
from ..services.memory import format_memory_block, load_memory
from ..tools.core import registry
from .providers import make_provider, BaseProvider

SYSTEM = """You are Stem, an AI music production agent operating a real DAW \
(Ardour) session on behalf of a producer.

Rules:
- Always read the current session (get_session_overview) before mutating the \
DAW, and target tracks by their track_id.
- Call get_selection when the user's focus matters; prefer editing the \
selection over inventing a new target.
- For harmonic/rhythmic material (chords, progressions, basslines, drums), \
use the theory tools — never free-hand MIDI pitches for chords or scales. \
Use insert_midi_notes only for explicit user-specified notes or melodies. \
For reviewable edits, prefer propose_midi_notes then accept_proposal.
- Before creating a MIDI track, call list_instruments and choose the best \
installed instrument for that role. Consider the full palette: acoustic and \
electric pianos, organs, guitars, basses, orchestral instruments, drums, \
samplers, and synthesizers. Reuse the same plugin only when musically apt.
- Every mutation returns an action_id. After completing a request, tell the \
user what you did and that it can be undone.
- If a tool errors, diagnose and adapt; do not silently claim success.
- Be fast and decisive: pick sensible musical defaults (key, voicing, \
velocity) from context and project memory instead of asking, unless the \
choice is truly fundamental to the user's intent.
- For multi-step jobs (arrange a song, rough mix), use list_tasks / run_task. \
Never pass confirm=true until the user explicitly agrees to the plan preview.
- Keep replies short — producers want results, not essays."""

MAX_STEPS = 25  # safety valve against tool-call loops


@dataclass
class ToolContext:
    bridge: Bridge
    project_id: str = "default"


class StemAgent:
    def __init__(self, bridge: Bridge, provider: Optional[BaseProvider] = None,
                 project_id: str = "default", **provider_kwargs):
        """provider_kwargs: provider= name, model=, api_key=, base_url= —
        see providers.load_config for env/config-file resolution."""
        self.provider = provider or make_provider(**provider_kwargs)
        self.project_id = project_id or "default"
        self.ctx = ToolContext(bridge=bridge, project_id=self.project_id)
        self.messages: list = []   # neutral format

    def _system(self) -> str:
        mem = load_memory(self.project_id)
        block = format_memory_block(mem)
        if not block:
            return SYSTEM
        return SYSTEM + "\n\n" + block

    def reset(self):
        """Clear conversation history for a fresh chat."""
        self.messages = []

    def _trim_history(self):
        self.messages = self.messages[-80:]
        while self.messages and self.messages[0]["role"] != "user":
            self.messages.pop(0)

    def load_history(self, turns):
        """Replace visible conversation history from the native panel.

        History files only contain user/assistant transcript text, not prior
        tool calls. Keep the provider context valid by dropping malformed turns,
        discarding leading assistant-only content, and coalescing duplicate
        adjacent roles.
        """
        messages = []
        for turn in turns or []:
            role = turn.get("role") if isinstance(turn, dict) else None
            content = turn.get("content") if isinstance(turn, dict) else None
            if role not in {"user", "assistant"} or not isinstance(content, str):
                continue
            content = content.strip()
            if not content:
                continue
            if not messages and role != "user":
                continue
            if messages and messages[-1]["role"] == role:
                messages[-1]["content"] += "\n\n" + content
            else:
                messages.append({"role": role, "content": content})
        self.messages = messages
        self._trim_history()

    def record_turn(self, user_message: str, assistant_text: str):
        """Record a completed visible turn that bypassed provider.chat.

        Deterministic local fallbacks still need to become part of the running
        conversation so follow-up prompts and restored history match the panel.
        """
        user_message = (user_message or "").strip()
        assistant_text = (assistant_text or "").strip()
        if user_message:
            if not (self.messages
                    and self.messages[-1].get("role") == "user"
                    and self.messages[-1].get("content") == user_message):
                self.messages.append({"role": "user", "content": user_message})
        if assistant_text:
            self.messages.append({"role": "assistant", "content": assistant_text})
        self._trim_history()

    def chat(self, user_message: str, on_event=None) -> str:
        """Run one user turn to completion (multi-step tool use). Returns the
        final assistant text. on_event(kind, payload) streams progress."""
        emit = on_event or (lambda kind, payload: None)
        self.messages.append({"role": "user", "content": user_message})

        for _ in range(MAX_STEPS):
            result = self.provider.complete(self._system(), self.messages,
                                            registry.definitions())
            self.messages.append({
                "role": "assistant",
                "content": result.text,
                "tool_calls": [{"id": tc.id, "name": tc.name,
                                "input": tc.input}
                               for tc in result.tool_calls],
            })
            if result.text.strip():
                emit("text", result.text)

            if not result.wants_tools:
                return result.text

            for tc in result.tool_calls:
                emit("tool_call", {"name": tc.name, "input": tc.input})
                tool_result = registry.execute(tc.name, tc.input, self.ctx)
                emit("tool_result", {"name": tc.name, "result": tool_result})
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(tool_result),
                    "is_error": "error" in tool_result,
                })

        return "(stopped: exceeded maximum tool steps for one turn)"
