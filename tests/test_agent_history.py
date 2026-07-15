from stem.agent.loop import StemAgent
from stem.agent.providers import CompletionResult
from stem.bridge.mock import MockBridge


class NoopProvider:
    def complete(self, system, messages, tools):
        return CompletionResult(text="ok")


def test_agent_load_history_normalizes_visible_turns():
    agent = StemAgent(MockBridge(), provider=NoopProvider())

    agent.load_history([
        {"role": "assistant", "content": "leading reply"},
        {"role": "user", "content": "make drums"},
        {"role": "user", "content": "and bass"},
        {"role": "assistant", "content": "done"},
        {"role": "tool", "content": "ignored"},
        {"role": "assistant", "content": ""},
    ])

    assert agent.messages == [
        {"role": "user", "content": "make drums\n\nand bass"},
        {"role": "assistant", "content": "done"},
    ]


def test_agent_load_history_cap_keeps_user_first():
    agent = StemAgent(MockBridge(), provider=NoopProvider())
    turns = []
    for i in range(45):
        turns.append({"role": "user", "content": f"user {i}"})
        turns.append({"role": "assistant", "content": f"assistant {i}"})

    agent.load_history(turns)

    assert len(agent.messages) <= 80
    assert agent.messages[0]["role"] == "user"


def test_agent_record_turn_avoids_duplicate_pending_user():
    agent = StemAgent(MockBridge(), provider=NoopProvider())
    agent.messages = [{"role": "user", "content": "set tempo"}]

    agent.record_turn("set tempo", "Tempo set.")

    assert agent.messages == [
        {"role": "user", "content": "set tempo"},
        {"role": "assistant", "content": "Tempo set."},
    ]
