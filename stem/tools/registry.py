"""Tool registry: every tool = pydantic input schema + handler.

The registry produces Anthropic tool-use definitions for the agent loop and
enforces validation before any bridge call. Every mutating tool returns the
action_id so the agent (and user) can undo it.
"""
from typing import Callable, Type
from pydantic import BaseModel


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

    def execute(self, name: str, raw_input: dict, ctx) -> dict:
        if name not in self._tools:
            return {"error": f"unknown tool: {name}"}
        tool = self._tools[name]
        try:
            args = tool.schema(**raw_input)   # validation gate
        except Exception as e:
            return {"error": f"invalid input: {e}"}
        try:
            result = tool.handler(args, ctx)
            if tool.mutates and "action_id" not in result:
                result["warning"] = "mutating tool returned no action_id"
            return result
        except Exception as e:
            return {"error": str(e)}
