"""Tool registry: every tool = pydantic input schema + handler.

The registry produces Anthropic tool-use definitions for the agent loop and
enforces validation before any bridge call. Every mutating tool returns the
action_id so the agent (and user) can undo it.

The registry also *checks the work*: around every mutating handler it takes a
before/after fingerprint of the session (stem/tools/verification.py), records
the pair in the undo journal, and attaches a ``verified`` block to the result.
That block is how the agent learns three things it used to have to assume —
whether the mutation actually landed, what else moved, and whether Stem can
undo it. A mutating tool that comes back without an action_id is reported as
not-undoable in the result itself rather than left as a silent warning.
"""
from typing import Callable, Type
from pydantic import BaseModel

from .verification import (
    diff, fingerprint, journal_for, scope_from_input, verification_enabled,
)

NO_ACTION_ID_WARNING = "mutating tool returned no action_id"
NO_UNDO_REASON = (
    "this tool reported no action_id, so Stem cannot undo it from here — "
    "the change is only reversible with Ardour's own undo")


class Tool:
    def __init__(self, name: str, description: str,
                 schema: Type[BaseModel], handler: Callable, mutates: bool):
        self.name = name
        self.description = description
        self.schema = schema
        self.handler = handler
        self.mutates = mutates

    def anthropic_def(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.schema.model_json_schema(),
        }


class ToolRegistry:
    def __init__(self):
        self._tools: dict = {}

    def register(self, name: str, description: str, schema: Type[BaseModel],
                 mutates: bool = False):
        def deco(fn):
            self._tools[name] = Tool(name, description, schema, fn, mutates)
            return fn
        return deco

    def definitions(self) -> list:
        return [t.anthropic_def() for t in self._tools.values()]

    def get(self, name: str):
        return self._tools.get(name)

    def names(self) -> list:
        return list(self._tools)

    def mutating_names(self) -> list:
        return [n for n, t in self._tools.items() if t.mutates]

    def execute(self, name: str, raw_input: dict, ctx) -> dict:
        if name not in self._tools:
            return {"error": f"unknown tool: {name}"}
        tool = self._tools[name]
        try:
            args = tool.schema(**raw_input)   # validation gate
        except Exception as e:
            return {"error": f"invalid input: {e}"}

        bridge = getattr(ctx, "bridge", None)
        watching = bool(tool.mutates and bridge is not None
                        and verification_enabled())
        scope = scope_from_input(raw_input) if watching else []
        before = fingerprint(bridge, scope) if watching else None

        try:
            result = tool.handler(args, ctx)
        except Exception as e:
            failure = {"error": str(e)}
            if watching:
                self._attach_partial_mutation(failure, bridge, name, before,
                                              scope)
            return failure

        if not isinstance(result, dict):
            result = {"value": result}

        if "error" in result:
            # The handler reported failure itself. It may still have changed
            # the session before giving up — say so instead of hiding it.
            if watching:
                self._attach_partial_mutation(result, bridge, name, before,
                                              scope)
            return result

        if not watching:
            if tool.mutates and "action_id" not in result:
                result["warning"] = NO_ACTION_ID_WARNING
            return result

        after = fingerprint(bridge, scope)
        changes = diff(before, after)
        action_id = result.get("action_id")
        journal_for(bridge).record(action_id, name, before, after, changes)

        result["verified"] = {
            "checked": changes.get("checked", False),
            "changed": changes["changed"],
            "changes": changes["details"],
        }
        if changes.get("checked") and not changes["changed"]:
            result["verified"]["note"] = (
                "the tool reported success but nothing measurable changed in "
                "the session")
        if not changes.get("checked"):
            result["verified"]["note"] = (
                "could not verify: the session could not be read back "
                + "; ".join(after.get("unreadable", []) or
                            before.get("unreadable", [])))

        if action_id:
            result["undo"] = {"available": True, "action_id": action_id}
        else:
            result["warning"] = NO_ACTION_ID_WARNING
            result["undo"] = {"available": False, "reason": NO_UNDO_REASON}
        return result

    def _attach_partial_mutation(self, payload: dict, bridge, name: str,
                                 before, scope) -> None:
        """A failed tool that already moved the session is the dangerous case:
        record it so undo has something to aim at, and tell the caller."""
        after = fingerprint(bridge, scope)
        changes = diff(before, after)
        if not changes["changed"]:
            return
        journal_for(bridge).record(payload.get("action_id"), name, before,
                                   after, changes)
        payload["partial_mutation"] = changes["details"]
        if not payload.get("action_id"):
            payload["undo"] = {
                "available": False,
                "reason": "the tool failed after changing the session and "
                          "returned no action_id"}
