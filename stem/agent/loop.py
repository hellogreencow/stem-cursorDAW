"""Stem agent loop: Claude + tool registry against a Bridge."""
import os
import json
from dataclasses import dataclass

from anthropic import Anthropic

from ..bridge.base import Bridge
from ..tools.core import registry

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


@dataclass
class ToolContext:
    bridge: Bridge


class StemAgent:
    def __init__(self, bridge: Bridge, model: str = "claude-sonnet-4-6",
                 api_key: str = None):
        self.client = Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
        self.model = model
        self.ctx = ToolContext(bridge=bridge)
        self.messages: list = []

    def chat(self, user_message: str, on_event=None) -> str:
        """Run one user turn to completion (multi-step tool use). Returns the
        final assistant text. on_event(kind, payload) streams progress."""
        emit = on_event or (lambda kind, payload: None)
        self.messages.append({"role": "user", "content": user_message})

        while True:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=SYSTEM,
                tools=registry.definitions(),
                messages=self.messages,
            )
            self.messages.append({"role": "assistant",
                                  "content": response.content})

            if response.stop_reason != "tool_use":
                text = "".join(b.text for b in response.content
                               if b.type == "text")
                emit("text", text)
                return text

            results = []
            for block in response.content:
                if block.type == "text" and block.text.strip():
                    emit("text", block.text)
                if block.type == "tool_use":
                    emit("tool_call", {"name": block.name, "input": block.input})
                    result = registry.execute(block.name, block.input, self.ctx)
                    emit("tool_result", {"name": block.name, "result": result})
                    results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result),
                        "is_error": "error" in result,
                    })
            self.messages.append({"role": "user", "content": results})
