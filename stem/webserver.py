"""Stem local agent server.

In Ardour, this runs as a private loopback API for the native Stem panel. The
standalone web page is still available for development, but the shipped app
path is the GTK panel baked into Ardour.
"""
import argparse
import json
import os
import threading
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .bridge.ardour import ArdourBridge
from .bridge.mock import MockBridge
from .agent.loop import StemAgent
from .agent.local_fallback import handle_local_intent
from .paths import ensure_stem_home
from .tools import generation_tools  # noqa: F401 — registers extra tools
from .tools import ardour_tools  # noqa: F401 — registers ardour_help
from .tools import sample_tools  # noqa: F401 — registers sample search/import
from .tools import plugin_tools  # noqa: F401 — registers plugin load/param
from .tools import proposal_tools  # noqa: F401 — registers selection/proposals
from .tools import memory_tools  # noqa: F401 — registers project memory
from .tools import task_tools  # noqa: F401 — registers autonomous tasks
from .tools import review_tools  # noqa: F401 — registers audio review
from .tools.core import registry

WEB_DIR = Path(__file__).parent / "web"
HOST, PORT = "127.0.0.1", 8765

# ---- shared agent state ----
_agent = None
_bridge = None
_force_mock = False
_jobs = {}          # job_id -> {state, activity:[], reply}
_jobs_lock = threading.Lock()


class StemHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def _install_bridge_impl():
    """Keep Ardour's hot-reloaded bridge implementation in sync with Stem."""
    stem_dir = ensure_stem_home()
    src = Path(__file__).parent / "bridge" / "bridge_impl.lua"
    dst = stem_dir / "bridge_impl.lua"
    body = src.read_text()
    if not dst.exists() or dst.read_text() != body:
        dst.write_text(body)


def _init_agent(force_mock=False):
    global _agent, _bridge, _force_mock
    _force_mock = force_mock
    _install_bridge_impl()
    bridge = MockBridge() if force_mock else ArdourBridge(rpc_timeout=1.0)
    if force_mock:
        print("• using in-memory mock session")
    elif not bridge.connected():
        bridge = MockBridge()
        print("• Ardour not reachable — using in-memory mock session")
    else:
        bridge.rpc_timeout = 15.0
        print("• connected to live Ardour")
    _bridge = bridge
    _agent = StemAgent(bridge)


def _ensure_live_bridge():
    """Upgrade a mock server to live Ardour when the bridge appears later."""
    global _agent, _bridge
    if _force_mock:
        return
    if not isinstance(_bridge, MockBridge):
        return
    bridge = ArdourBridge(rpc_timeout=1.0)
    if not bridge.connected():
        return
    bridge.rpc_timeout = 15.0
    messages = list(getattr(_agent, "messages", []))
    _bridge = bridge
    _agent = StemAgent(bridge)
    _agent.messages = messages
    print("• connected to live Ardour")


def _record_agent_turn(message, reply):
    try:
        _agent.record_turn(message, reply)
    except Exception:
        pass


def _run_job(job_id, message):
    def on_event(kind, payload):
        with _jobs_lock:
            job = _jobs[job_id]
            if kind == "tool_call":
                job["activity"].append({"kind": "call", "name": payload["name"],
                                        "input": payload["input"]})
            elif kind == "tool_result":
                r = payload["result"]
                job["activity"].append({"kind": "result", "name": payload["name"],
                                        "ok": "error" not in r, "result": r})
    try:
        _ensure_live_bridge()
        reply = handle_local_intent(message, _agent.ctx, on_event)
        if reply is not None:
            _record_agent_turn(message, reply)
            with _jobs_lock:
                _jobs[job_id]["reply"] = reply
                _jobs[job_id]["state"] = "done"
            return
        reply = _agent.chat(message, on_event)
        with _jobs_lock:
            _jobs[job_id]["reply"] = reply
            _jobs[job_id]["state"] = "done"
    except Exception as e:
        try:
            reply = handle_local_intent(message, _agent.ctx, on_event)
        except Exception:
            reply = None
        if reply:
            _record_agent_turn(message, reply)
        with _jobs_lock:
            _jobs[job_id]["reply"] = reply or _friendly_error(e)
            _jobs[job_id]["state"] = "done" if reply else "error"


def _friendly_error(exc):
    text = str(exc)
    low = text.lower()
    if "401" in text or "unauthorized" in low or "user not found" in low:
        return ("LLM provider authentication failed. Check the provider and API "
                "key in ~/.stem/config.json or set STEM_PROVIDER plus the "
                "matching API key environment variable.")
    if "insufficient_quota" in low or "rate limit" in low or "429" in text:
        return ("LLM provider quota is exhausted or rate-limited. Update billing, "
                "choose another provider, or set a different key in "
                "~/.stem/config.json.")
    return f"Error: {text}"


def _session_snapshot():
    try:
        _ensure_live_bridge()
        restore_timeout = None
        if isinstance(_bridge, ArdourBridge):
            restore_timeout = _bridge.rpc_timeout
            _bridge.rpc_timeout = min(_bridge.rpc_timeout, 1.0)
        s = _bridge.get_session_overview()
        if restore_timeout is not None:
            _bridge.rpc_timeout = restore_timeout
        tracks = []
        for t in s.tracks:
            notes = 0
            if t.kind == "midi":
                try:
                    notes = len(_bridge.get_midi_notes(t.track_id))
                except Exception:
                    notes = 0
            tracks.append({"name": t.name, "kind": t.kind, "notes": notes,
                           "muted": t.muted})
        live = not isinstance(_bridge, MockBridge)
        return {"connected": live, "name": s.name, "tempo": s.tempo,
                "tracks": tracks}
    except Exception as e:
        if "_bridge" in globals() and isinstance(_bridge, ArdourBridge):
            _bridge.rpc_timeout = 15.0
        return {"connected": False, "error": str(e), "tracks": []}


def _instrument_summary(instruments):
    count = len(instruments)
    if count == 0:
        return ("No instruments are available yet. Open Ardour's plugin manager "
                "and run a plugin scan, then check the Library again.")
    categories = {}
    gm_count = 0
    preset_count = 0
    for instrument in instruments:
        category = instrument.get("category") or instrument.get("type") or "Other"
        categories[category] = categories.get(category, 0) + 1
        if instrument.get("type") == "GM":
            gm_count += 1
        preset_count += len(instrument.get("presets") or [])
    ranked = sorted(categories.items(), key=lambda item: (-item[1], item[0]))
    top = ", ".join(f"{name}: {n}" for name, n in ranked[:6])
    gm = " GM sounds are available." if gm_count else ""
    presets = f" {preset_count} presets were found." if preset_count else ""
    return f"Library: {count} instruments. {top}.{gm}{presets}"


def _instrument_snapshot():
    try:
        _ensure_live_bridge()
        instruments = _bridge.list_instruments()
        live = not isinstance(_bridge, MockBridge)
        return {
            "connected": live,
            "count": len(instruments),
            "summary": _instrument_summary(instruments),
            "instruments": instruments[:300],
        }
    except Exception as e:
        return {
            "connected": False,
            "count": 0,
            "summary": f"Instrument library unavailable: {e}",
            "instruments": [],
        }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            html = (WEB_DIR / "index.html").read_text()
            return self._send(200, html, "text/html; charset=utf-8")
        if self.path == "/api/session":
            return self._send(200, json.dumps(_session_snapshot()))
        if self.path == "/api/instruments":
            return self._send(200, json.dumps(_instrument_snapshot()))
        if self.path.startswith("/api/job/"):
            jid = self.path.rsplit("/", 1)[-1]
            with _jobs_lock:
                job = _jobs.get(jid)
            if not job:
                return self._send(404, json.dumps({"error": "no such job"}))
            return self._send(200, json.dumps(job))
        return self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if self.path == "/api/chat":
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
            msg = (payload.get("message") or "").strip()
            if not msg:
                return self._send(400, json.dumps({"error": "empty"}))
            jid = uuid.uuid4().hex[:12]
            with _jobs_lock:
                _jobs[jid] = {"state": "thinking", "activity": [], "reply": ""}
            threading.Thread(target=_run_job, args=(jid, msg),
                             daemon=True).start()
            return self._send(200, json.dumps({"job_id": jid}))
        if self.path == "/api/reset":
            # Start a fresh conversation (clear the agent's running history).
            try:
                _agent.reset()
            except Exception:
                pass
            return self._send(200, json.dumps({"ok": True}))
        if self.path == "/api/history":
            # Replace the running provider context with transcript text loaded
            # by the native Ardour panel's history drawer.
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return self._send(400, json.dumps({"error": "invalid json"}))
            turns = payload.get("turns")
            if not isinstance(turns, list):
                return self._send(400, json.dumps({"error": "turns must be a list"}))
            try:
                _agent.load_history(turns)
                count = len(getattr(_agent, "messages", []))
            except Exception as e:
                return self._send(500, json.dumps({"error": str(e)}))
            return self._send(200, json.dumps({"ok": True, "turns": count}))
        return self._send(404, json.dumps({"error": "not found"}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--embedded", action="store_true",
                        help="run for Ardour's native Stem panel")
    parser.add_argument("--mock", action="store_true",
                        help="force mock bridge for local tests")
    args = parser.parse_args()
    if args.embedded:
        os.environ["STEM_NO_BROWSER"] = "1"

    _init_agent(force_mock=args.mock)
    httpd = StemHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}"
    if args.embedded:
        print(f"• Stem native agent listening on {HOST}:{PORT}  "
              f"({len(registry.definitions())} tools loaded)")
    else:
        print(f"• Stem dev UI at {url}  ({len(registry.definitions())} tools loaded)")
    if not os.environ.get("STEM_NO_BROWSER"):
        try:
            webbrowser.open(url)
        except Exception:
            pass
    httpd.serve_forever()


if __name__ == "__main__":
    main()
