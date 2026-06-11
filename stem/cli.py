"""Stem CLI — minimal chat shell. The interface is not the product.

Usage:
    python -m stem.cli                      # auto: Ardour if reachable, else mock
    python -m stem.cli --mock              # force in-memory session
    python -m stem.cli --ardour            # require live Ardour bridge
    python -m stem.cli --provider openrouter --model meta-llama/llama-3.3-70b
    python -m stem.cli --provider openai
    python -m stem.cli --provider custom --base-url http://localhost:11434/v1 --model llama3

Provider/key resolution: flags > STEM_PROVIDER/STEM_MODEL/STEM_BASE_URL +
provider key env vars > ~/.stem/config.json. See stem/agent/providers.py.
"""
import sys

from .bridge.mock import MockBridge
from .bridge.ardour import ArdourBridge
from .agent.loop import StemAgent
from .tools import generation_tools  # noqa: F401 (registers tools)


def pick_bridge(argv):
    if "--mock" in argv:
        print("• bridge: mock (in-memory session)")
        return MockBridge()
    ardour = ArdourBridge()
    if ardour.connected():
        print("• bridge: live Ardour")
        return ardour
    if "--ardour" in argv:
        print("✗ Ardour bridge unreachable. Is Ardour running with the "
              "Stem Agent Bridge Lua script active? (see README)")
        sys.exit(1)
    print("• bridge: mock (Ardour not reachable — start it with the bridge "
          "script for live control)")
    return MockBridge()


def flag_value(argv, name):
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


def main():
    argv = sys.argv[1:]
    bridge = pick_bridge(argv)
    agent = StemAgent(
        bridge,
        provider=None,
        **{k: v for k, v in {
            "provider": flag_value(argv, "--provider"),
            "model": flag_value(argv, "--model"),
            "api_key": flag_value(argv, "--api-key"),
            "base_url": flag_value(argv, "--base-url"),
        }.items() if v},
    )

    def on_event(kind, payload):
        if kind == "tool_call":
            print(f"  ⚙ {payload['name']}({payload['input']})")
        elif kind == "tool_result":
            r = payload["result"]
            mark = "✗" if "error" in r else "✓"
            print(f"  {mark} {r}")

    print("Stem — AI producer agent. Type a command ('quit' to exit).\n")
    while True:
        try:
            user = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user or user.lower() in ("quit", "exit"):
            break
        try:
            reply = agent.chat(user, on_event)
            print(f"\nstem> {reply}\n")
        except Exception as e:
            print(f"\n✗ {e}\n")


if __name__ == "__main__":
    main()
