"""Provider abstraction tests: config resolution, message translation, and
the agent loop driven by a scripted fake provider (no network)."""
import json
import pytest

from stem.agent import providers
from stem.agent.providers import (
    load_config, AnthropicProvider, OpenAICompatProvider,
    CompletionResult, ToolCall, BaseProvider,
)
from stem.agent.loop import StemAgent
from stem.bridge.mock import MockBridge


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch, tmp_path):
    monkeypatch.setattr(providers, "CONFIG_PATH", tmp_path / "config.json")


# ---- config resolution ----

def test_default_provider_is_anthropic(monkeypatch):
    for var in ("STEM_PROVIDER", "STEM_MODEL", "STEM_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    cfg = load_config()
    assert cfg["provider"] == "anthropic"
    assert cfg["model"].startswith("claude")


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("STEM_PROVIDER", "openrouter")
    monkeypatch.setenv("STEM_MODEL", "meta-llama/llama-3.3-70b-instruct")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    cfg = load_config()
    assert cfg["provider"] == "openrouter"
    assert cfg["base_url"] == "https://openrouter.ai/api/v1"
    assert cfg["api_key"] == "sk-or-test"


def test_config_key_wins_when_provider_comes_from_config(monkeypatch):
    providers.CONFIG_PATH.write_text(json.dumps({
        "provider": "openrouter",
        "model": "anthropic/claude-sonnet-4.6",
        "api_key": "sk-or-good-config",
    }))
    monkeypatch.delenv("STEM_PROVIDER", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-stale-env")

    cfg = load_config()

    assert cfg["provider"] == "openrouter"
    assert cfg["api_key"] == "sk-or-good-config"


def test_explicit_args_beat_env(monkeypatch):
    monkeypatch.setenv("STEM_PROVIDER", "openai")
    cfg = load_config(provider="anthropic", model="claude-haiku-4-5-20251001")
    assert cfg["provider"] == "anthropic"
    assert cfg["model"] == "claude-haiku-4-5-20251001"


def test_unknown_provider_rejected():
    with pytest.raises(ValueError, match="unknown provider"):
        load_config(provider="grok-on-a-toaster")


def test_custom_requires_base_url(monkeypatch):
    monkeypatch.delenv("STEM_BASE_URL", raising=False)
    with pytest.raises(ValueError, match="base_url|BASE_URL"):
        load_config(provider="custom", model="llama3")


# ---- message format translation ----

NEUTRAL = [
    {"role": "user", "content": "make a beat"},
    {"role": "assistant", "content": "on it",
     "tool_calls": [{"id": "t1", "name": "create_midi_track",
                     "input": {"name": "Drums"}}]},
    {"role": "tool", "tool_call_id": "t1",
     "content": '{"track_id": "trk_1"}', "is_error": False},
]


def test_anthropic_translation():
    native = AnthropicProvider._to_native(NEUTRAL)
    assert native[0] == {"role": "user", "content": "make a beat"}
    assert native[1]["role"] == "assistant"
    types = [b["type"] for b in native[1]["content"]]
    assert types == ["text", "tool_use"]
    # tool result becomes a user message with tool_result blocks
    assert native[2]["role"] == "user"
    assert native[2]["content"][0]["type"] == "tool_result"
    assert native[2]["content"][0]["tool_use_id"] == "t1"


def test_openai_translation():
    native = OpenAICompatProvider._to_native("SYS", NEUTRAL)
    assert native[0] == {"role": "system", "content": "SYS"}
    assert native[2]["role"] == "assistant"
    tc = native[2]["tool_calls"][0]
    assert tc["function"]["name"] == "create_midi_track"
    assert json.loads(tc["function"]["arguments"]) == {"name": "Drums"}
    assert native[3] == {"role": "tool", "tool_call_id": "t1",
                         "content": '{"track_id": "trk_1"}'}


def test_openai_tool_def_translation():
    defs = OpenAICompatProvider._tools_to_native([
        {"name": "x", "description": "d",
         "input_schema": {"type": "object", "properties": {}}}])
    assert defs[0]["type"] == "function"
    assert defs[0]["function"]["parameters"] == {"type": "object",
                                                 "properties": {}}


# ---- agent loop with scripted provider (provider-agnostic behavior) ----

class ScriptedProvider(BaseProvider):
    """Replays a fixed script of completions; records what it was sent."""

    def __init__(self, script):
        self.script = list(script)
        self.seen = []

    def complete(self, system, messages, tools):
        self.seen.append([dict(m) for m in messages])
        return self.script.pop(0)


def test_agent_loop_executes_tools_and_finishes():
    bridge = MockBridge()
    provider = ScriptedProvider([
        CompletionResult(text="", tool_calls=[
            ToolCall(id="a", name="create_midi_track",
                     input={"name": "Chords"})]),
        CompletionResult(text="", tool_calls=[
            ToolCall(id="b", name="set_tempo", input={"bpm": 90})]),
        CompletionResult(text="Done — track created at 90bpm."),
    ])
    agent = StemAgent(bridge, provider=provider)
    reply = agent.chat("set up a chord track at 90")

    assert reply == "Done — track created at 90bpm."
    assert bridge.tempo == 90
    assert len(bridge.tracks) == 1
    # tool results were fed back into the conversation
    last_sent = provider.seen[-1]
    tool_msgs = [m for m in last_sent if m["role"] == "tool"]
    assert len(tool_msgs) == 2
    assert not any(m.get("is_error") for m in tool_msgs)


def test_agent_loop_surfaces_tool_errors_to_model():
    provider = ScriptedProvider([
        CompletionResult(text="", tool_calls=[
            ToolCall(id="a", name="set_tempo", input={"bpm": -10})]),
        CompletionResult(text="That tempo is invalid; nothing was changed."),
    ])
    agent = StemAgent(MockBridge(), provider=provider)
    agent.chat("tempo -10")
    err_msgs = [m for m in provider.seen[-1]
                if m["role"] == "tool" and m["is_error"]]
    assert len(err_msgs) == 1


def test_agent_loop_step_cap():
    looping = CompletionResult(text="", tool_calls=[
        ToolCall(id="x", name="get_session_overview", input={})])
    provider = ScriptedProvider([looping] * 100)
    agent = StemAgent(MockBridge(), provider=provider)
    reply = agent.chat("loop forever")
    assert "maximum tool steps" in reply
