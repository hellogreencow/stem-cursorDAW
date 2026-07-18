"""LLM provider abstraction.

The agent loop speaks a neutral message format; each provider adapts it.

Neutral format:
  {"role": "user", "content": str}
  {"role": "assistant", "content": str, "tool_calls": [{"id","name","input"}]}
  {"role": "tool", "tool_call_id": str, "content": str, "is_error": bool}

Config resolution (first hit wins):
  1. explicit kwargs
  2. environment: STEM_PROVIDER, STEM_MODEL, STEM_BASE_URL, and the
     provider's standard key var (ANTHROPIC_API_KEY / OPENAI_API_KEY /
     OPENROUTER_API_KEY / STEM_API_KEY)
  3. ~/.stem/config.json  e.g. {"provider": "openrouter",
     "model": "anthropic/claude-sonnet-4.6", "api_key": "sk-or-..."}

Providers:
  anthropic   — native Messages API
  openai      — Chat Completions
  openrouter  — Chat Completions at openrouter.ai (any model they host)
  custom      — any OpenAI-compatible endpoint via base_url (Ollama, Groq...)
"""
import os
import json
from dataclasses import dataclass, field
from typing import Optional

from ..paths import config_path

DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-4o",
    "openrouter": "anthropic/claude-sonnet-4.6",
    "custom": None,  # must be specified
}

KEY_ENV_VARS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "custom": "STEM_API_KEY",
}

BASE_URLS = {
    "openrouter": "https://openrouter.ai/api/v1",
}


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class CompletionResult:
    text: str
    tool_calls: list = field(default_factory=list)  # list[ToolCall]

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


def load_config(provider: Optional[str] = None, model: Optional[str] = None,
                api_key: Optional[str] = None,
                base_url: Optional[str] = None) -> dict:
    file_cfg = {}
    cfg_path = config_path()
    if cfg_path.exists():
        try:
            file_cfg = json.loads(cfg_path.read_text())
        except json.JSONDecodeError:
            raise RuntimeError(f"invalid JSON in {cfg_path}")

    explicit_provider = provider is not None
    env_provider = os.environ.get("STEM_PROVIDER")
    file_provider = file_cfg.get("provider")
    provider = (provider or env_provider or file_provider or "anthropic").lower()
    if provider not in DEFAULT_MODELS:
        raise ValueError(f"unknown provider '{provider}' — choose from: "
                         f"{', '.join(DEFAULT_MODELS)}")

    model = (model or os.environ.get("STEM_MODEL")
             or file_cfg.get("model") or DEFAULT_MODELS[provider])
    if not model:
        raise ValueError(f"provider '{provider}' requires an explicit model "
                         "(STEM_MODEL or config.json)")

    provider_from_config = (
        not explicit_provider and not env_provider and file_provider == provider)
    if api_key:
        resolved_key = api_key
    elif provider_from_config:
        resolved_key = (file_cfg.get("api_key")
                        or os.environ.get(KEY_ENV_VARS[provider])
                        or os.environ.get("STEM_API_KEY"))
    else:
        resolved_key = (os.environ.get(KEY_ENV_VARS[provider])
                        or os.environ.get("STEM_API_KEY")
                        or file_cfg.get("api_key"))

    if base_url:
        resolved_base_url = base_url
    elif provider_from_config:
        resolved_base_url = (file_cfg.get("base_url")
                             or os.environ.get("STEM_BASE_URL")
                             or BASE_URLS.get(provider))
    else:
        resolved_base_url = (os.environ.get("STEM_BASE_URL")
                             or file_cfg.get("base_url")
                             or BASE_URLS.get(provider))
    if provider == "custom" and not resolved_base_url:
        raise ValueError("provider 'custom' requires STEM_BASE_URL")

    return {"provider": provider, "model": model,
            "api_key": resolved_key, "base_url": resolved_base_url}


class BaseProvider:
    def complete(self, system: str, messages: list,
                 tools: list) -> CompletionResult:
        raise NotImplementedError


class AnthropicProvider(BaseProvider):
    def __init__(self, model: str, api_key: Optional[str],
                 base_url: Optional[str] = None):
        from anthropic import Anthropic
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = Anthropic(**kwargs)
        self.model = model

    @staticmethod
    def _to_native(messages: list) -> list:
        out = []
        pending_results = []

        def flush_results():
            nonlocal pending_results
            if pending_results:
                out.append({"role": "user", "content": pending_results})
                pending_results = []

        for m in messages:
            if m["role"] == "user":
                flush_results()
                out.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                flush_results()
                content = []
                if m.get("content"):
                    content.append({"type": "text", "text": m["content"]})
                for tc in m.get("tool_calls", []):
                    content.append({"type": "tool_use", "id": tc["id"],
                                    "name": tc["name"], "input": tc["input"]})
                out.append({"role": "assistant", "content": content})
            elif m["role"] == "tool":
                pending_results.append({
                    "type": "tool_result",
                    "tool_use_id": m["tool_call_id"],
                    "content": m["content"],
                    "is_error": m.get("is_error", False),
                })
        flush_results()
        return out

    def complete(self, system, messages, tools) -> CompletionResult:
        resp = self.client.messages.create(
            model=self.model, max_tokens=4096, system=system,
            tools=tools, messages=self._to_native(messages))
        text = "".join(b.text for b in resp.content if b.type == "text")
        calls = [ToolCall(id=b.id, name=b.name, input=b.input)
                 for b in resp.content if b.type == "tool_use"]
        return CompletionResult(text=text, tool_calls=calls)


class OpenAICompatProvider(BaseProvider):
    """OpenAI, OpenRouter, and any OpenAI-compatible endpoint."""

    def __init__(self, model: str, api_key: Optional[str],
                 base_url: Optional[str] = None):
        from openai import OpenAI
        kwargs = {"api_key": api_key or "unused"}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = OpenAI(**kwargs)
        self.model = model

    @staticmethod
    def _to_native(system: str, messages: list) -> list:
        out = [{"role": "system", "content": system}]
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                msg = {"role": "assistant",
                       "content": m.get("content") or None}
                if m.get("tool_calls"):
                    msg["tool_calls"] = [
                        {"id": tc["id"], "type": "function",
                         "function": {"name": tc["name"],
                                      "arguments": json.dumps(tc["input"])}}
                        for tc in m["tool_calls"]]
                out.append(msg)
            elif m["role"] == "tool":
                out.append({"role": "tool",
                            "tool_call_id": m["tool_call_id"],
                            "content": m["content"]})
        return out

    @staticmethod
    def _tools_to_native(tools: list) -> list:
        # convert Anthropic-style defs (our registry format) to OpenAI format
        return [{"type": "function",
                 "function": {"name": t["name"],
                              "description": t["description"],
                              "parameters": t["input_schema"]}}
                for t in tools]

    def complete(self, system, messages, tools) -> CompletionResult:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=self._to_native(system, messages),
            tools=self._tools_to_native(tools),
            max_tokens=4096)
        choice = resp.choices[0].message
        calls = []
        for tc in (choice.tool_calls or []):
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_malformed_arguments": tc.function.arguments}
            calls.append(ToolCall(id=tc.id, name=tc.function.name, input=args))
        return CompletionResult(text=choice.content or "", tool_calls=calls)


def make_provider(provider: Optional[str] = None, model: Optional[str] = None,
                  api_key: Optional[str] = None,
                  base_url: Optional[str] = None) -> BaseProvider:
    cfg = load_config(provider, model, api_key, base_url)
    if cfg["provider"] == "anthropic":
        return AnthropicProvider(cfg["model"], cfg["api_key"], cfg["base_url"])
    return OpenAICompatProvider(cfg["model"], cfg["api_key"], cfg["base_url"])
