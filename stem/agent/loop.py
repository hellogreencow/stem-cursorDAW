"""Stem agent loop: any LLM provider + tool registry against a Bridge.

Provider-agnostic: the loop speaks the neutral message format from
providers.py; Anthropic/OpenAI/OpenRouter/custom endpoints are adapters.
"""
import json
from dataclasses import dataclass
from typing import Optional

from ..bridge.base import Bridge
from ..tools.core import registry
from .providers import make_provider, BaseProvider

SYSTEM = """You are Stem, an AI music production agent operating a real DAW \
(Ardour) session on behalf of a producer.

Rules:
- Always read the session (get_session_overview) before your first mutation \
in a conversation, and target tracks by their track_id.
- For harmonic/rhythmic material (chords, progressions, basslines, drums), \
use the theory tools — never free-hand MIDI pitches for chords or scales. \
Use insert_midi_notes only for explicit user-specified notes or melodies.
- Every mutation returns an action_id. After completing a request, tell the \
user what you did and that it can be undone.
- If a tool errors, diagnose and adapt; do not silently claim success.
- Be fast and decisive: pick sensible musical defaults (key, voicing, \
velocity) from context instead of asking, unless the choice is truly \
fundamental to the user's intent.
- Keep replies short — producers want results, not essays."""

MAX_STEPS = 25  # safety valve against tool-call loops


@dataclass
class ToolContext:
    bridge: Bridge


class StemAgent:
    def __init__(self, bridge: Bridge, provider: Optional[BaseProvider] = None,
                 **provider_kwargs):
        """provider_kwargs: provider= name, model=, api_key=, base_url= —
        see providers.load_config for env/config-file resolution."""
        self.provider = provider or make_provider(**provider_kwargs)
        self.ctx = ToolContext(bridge=bridge)
        self.messages: list = []   # neutral format

    def chat(self, user_message: str, on_event=None) -> str:
        """Run one user turn to completion (multi-step tool use). Returns the
        final assistant text. on_event(kind, payload) streams progress."""
        emit = on_event or (lambda kind, payload: None)
        self.messages.append({"role": "user", "content": user_message})

        for _ in range(MAX_STEPS):
            result = self.provider.complete(SYSTEM, self.messages,
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
