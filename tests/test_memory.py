"""M4.1 project memory: persistence, redaction, second-session defaults."""
import json

import stem.tools.memory_tools  # noqa: F401
from stem.agent.loop import StemAgent, ToolContext
from stem.agent.providers import BaseProvider, CompletionResult, ToolCall
from stem.bridge.mock import MockBridge
from stem.paths import stem_home
from stem.services.memory import (
    format_memory_block, load_memory, memory_path, redact, update_memory,
)
from stem.tools.core import registry


def run(ctx, tool, **kwargs):
    return registry.execute(tool, kwargs, ctx)


def test_update_and_recall_via_tools(mock_ctx):
    mock_ctx.project_id = "prod-a"
    r = run(mock_ctx, "update_memory", preferred_key="F", preferred_tempo=95,
            preferred_genre="house", add_note="keep kicks dry")
    assert r["ok"]
    recalled = run(mock_ctx, "recall_memory")
    assert recalled["preferred_key"] == "F"
    assert recalled["preferred_tempo"] == 95
    assert "keep kicks dry" in recalled["notes"]


def test_memory_redacts_secrets_on_disk():
    mem = update_memory(
        "secure",
        add_note="use key sk-ant-SECRET123 for nothing",
        add_sample="/Samples/kick.wav",
    )
    # Direct field injection attempt
    raw = {
        "project_id": "secure",
        "api_key": "sk-leak",
        "notes": mem.notes,
        "preferred_key": "C",
    }
    scrubbed = redact(raw)
    assert "api_key" not in scrubbed
    assert "sk-ant-" not in json.dumps(scrubbed)

    path = memory_path("secure")
    on_disk = json.loads(path.read_text())
    assert "api_key" not in on_disk
    blob = path.read_text()
    assert "sk-ant-SECRET123" not in blob
    assert "[redacted]" in blob


def test_second_session_system_prompt_includes_memory():
    update_memory("session-2", preferred_tempo=88, preferred_key="D")

    class Capture(BaseProvider):
        def __init__(self):
            self.system = None

        def complete(self, system, messages, tools):
            self.system = system
            return CompletionResult(text="ok using your defaults")

    provider = Capture()
    agent = StemAgent(MockBridge(), provider=provider, project_id="session-2")
    reply = agent.chat("continue")
    assert "ok" in reply
    assert "preferred tempo: 88" in provider.system
    assert "preferred key: D" in provider.system
    assert format_memory_block(load_memory("session-2"))


def test_scripted_agent_uses_memory_tempo():
    update_memory("dogfood", preferred_tempo=102)

    class Scripted(BaseProvider):
        def __init__(self):
            self.systems = []

        def complete(self, system, messages, tools):
            self.systems.append(system)
            if len(self.systems) == 1:
                return CompletionResult(text="", tool_calls=[
                    ToolCall(id="1", name="recall_memory", input={}),
                ])
            if len(self.systems) == 2:
                return CompletionResult(text="", tool_calls=[
                    ToolCall(id="2", name="set_tempo", input={"bpm": 102}),
                ])
            return CompletionResult(text="Set 102 BPM from memory.")

    bridge = MockBridge()
    provider = Scripted()
    agent = StemAgent(bridge, provider=provider, project_id="dogfood")
    reply = agent.chat("use my tempo")
    assert bridge.tempo == 102
    assert "102" in reply
    assert any("102" in s for s in provider.systems)
