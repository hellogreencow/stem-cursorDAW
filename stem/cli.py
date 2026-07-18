"""Stem CLI — minimal chat shell. The interface is not the product.

Usage:
    python -m stem.cli                      # auto: Ardour if reachable, else mock
    python -m stem.cli --mock              # force in-memory session
    python -m stem.cli --ardour            # require live Ardour bridge
    python -m stem.cli --script song.stem  # run StemScript against the session
    python -m stem.cli --ui                # open the Apple-inspired command UI
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
from .agent.local_fallback import handle_local_intent
from .language import execute_stemscript, StemScriptError
from .tools import generation_tools  # noqa: F401 (registers tools)
from .tools import produce_tools  # noqa: F401 (registers Stem produce/overlay)
from .tools import sample_tools  # noqa: F401 (registers sample search/import)
from .tools import plugin_tools  # noqa: F401 (registers plugin load/param)
from .tools import proposal_tools  # noqa: F401 (registers selection/proposals)
from .tools import memory_tools  # noqa: F401 (registers project memory)
from .tools import task_tools  # noqa: F401 (registers autonomous tasks)
from .tools import review_tools  # noqa: F401 (registers audio review)
from .tools import jury_tools  # noqa: F401 (registers music jury)


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


def _clean_argv(argv):
    flags_with_values = {
        "--provider", "--model", "--api-key", "--base-url", "--script",
    }
    cleaned = []
    skip = False
    for i, item in enumerate(argv):
        if skip:
            skip = False
            continue
        if item in flags_with_values:
            skip = i + 1 < len(argv)
            continue
        cleaned.append(item)
    return cleaned


def launch_ui(argv):
    from . import webserver
    sys.argv = [sys.argv[0]] + [
        a for a in argv if a not in ("--ui", "--ardour")
    ]
    webserver.main()


def main():
    argv = sys.argv[1:]
    if "--ui" in argv:
        launch_ui(argv)
        return

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

    script_path = flag_value(argv, "--script")
    if script_path:
        with open(script_path, "r", encoding="utf-8") as fh:
            script = fh.read()
        try:
            print(execute_stemscript(script, agent.ctx, on_event))
        except StemScriptError as e:
            print(f"✗ StemScript error: {e}")
            sys.exit(1)
        return

    print("Stem — AI producer agent. Type a command ('quit' to exit).\n")
    while True:
        try:
            user = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user or user.lower() in ("quit", "exit"):
            break
        try:
            reply = handle_local_intent(user, agent.ctx, on_event)
            if reply is None:
                reply = agent.chat(user, on_event)
            print(f"\nstem> {reply}\n")
        except Exception as e:
            print(f"\n✗ {e}\n")


if __name__ == "__main__":
    main()
