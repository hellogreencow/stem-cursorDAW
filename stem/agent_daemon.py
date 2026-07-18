"""Stem agent daemon — the brain behind the in-Ardour chat panel.

Runs continuously alongside Ardour. Watches ~/.stem/chat_request.json for a
user message from the C++ panel, runs the full agent (LLM + tools against the
live session), and writes the reply + a live activity log to
~/.stem/chat_response.json. The panel polls those files.

This keeps ALL agent logic in Python (proven, hot-editable) and lets the
compiled C++ panel stay a thin text box.

Run:  ./venv/bin/python -m stem.agent_daemon
"""
import json
import time
import traceback

from .bridge.ardour import ArdourBridge
from .agent.loop import StemAgent
from .paths import ensure_stem_home
from .tools import generation_tools  # noqa: F401 — registers extra tools
from .tools import produce_tools  # noqa: F401 — registers Stem produce/overlay
from .tools import sample_tools  # noqa: F401 — registers sample search/import
from .tools import plugin_tools  # noqa: F401 — registers plugin load/param
from .tools import proposal_tools  # noqa: F401 — registers selection/proposals
from .tools import memory_tools  # noqa: F401 — registers project memory
from .tools import task_tools  # noqa: F401 — registers autonomous tasks
from .tools import review_tools  # noqa: F401 — registers audio review


def _chat_paths():
    stem = ensure_stem_home()
    return stem / "chat_request.json", stem / "chat_response.json"


def write_response(state: str, reply: str = "", activity=None, req_id=""):
    _, resp = _chat_paths()
    resp.write_text(json.dumps({
        "id": req_id,
        "state": state,             # "thinking" | "done" | "error"
        "reply": reply,
        "activity": activity or [],
    }))


def main():
    req_path, _ = _chat_paths()
    bridge = ArdourBridge(rpc_timeout=15.0)
    agent = StemAgent(bridge)  # provider from stem config.json
    print("Stem daemon: waiting for Ardour + chat messages…")

    last_id = None
    while True:
        try:
            if not req_path.exists():
                time.sleep(0.2)
                continue
            try:
                req = json.loads(req_path.read_text())
            except (json.JSONDecodeError, OSError):
                time.sleep(0.1)
                continue
            rid = req.get("id")
            if rid == last_id:
                time.sleep(0.2)
                continue
            last_id = rid
            msg = req.get("message", "").strip()
            if not msg:
                continue

            print(f"\n[user] {msg}")
            activity = []

            def on_event(kind, payload):
                if kind == "tool_call":
                    line = f"⚙ {payload['name']}"
                    activity.append(line)
                    print(" ", line, payload["input"])
                    write_response("thinking", activity=activity, req_id=rid)
                elif kind == "tool_result":
                    r = payload["result"]
                    ok = "✗" if "error" in r else "✓"
                    activity.append(f"{ok} {payload['name']}")
                    write_response("thinking", activity=activity, req_id=rid)

            write_response("thinking", activity=["thinking…"], req_id=rid)
            reply = agent.chat(msg, on_event)
            print(f"[stem] {reply}")
            write_response("done", reply=reply, activity=activity, req_id=rid)

        except Exception as e:
            traceback.print_exc()
            write_response("error", reply=f"Error: {e}", req_id=last_id or "")
            time.sleep(0.5)


if __name__ == "__main__":
    main()
