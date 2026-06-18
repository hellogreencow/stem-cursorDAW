"""Stem web UI server — the sleek agent interface.

A local, self-contained web app: serves the Stem chat UI and drives the live
agent against the running Ardour session. No external framework — stdlib
http.server with a threaded agent runner so the UI can stream activity.

Run:  ./venv/bin/python -m stem.webserver   (then open http://127.0.0.1:8765)
"""
import json
import threading
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .bridge.ardour import ArdourBridge
from .bridge.mock import MockBridge
from .agent.loop import StemAgent
from .tools import generation_tools  # noqa: F401 — registers extra tools
from .tools.core import registry

WEB_DIR = Path(__file__).parent / "web"
HOST, PORT = "127.0.0.1", 8765

# ---- shared agent state ----
_agent = None
_bridge = None
_jobs = {}          # job_id -> {state, activity:[], reply}
_jobs_lock = threading.Lock()


def _init_agent():
    global _agent, _bridge
    bridge = ArdourBridge(rpc_timeout=15.0)
    if not bridge.connected():
        bridge = MockBridge()
        print("• Ardour not reachable — using in-memory mock session")
    else:
        print("• connected to live Ardour")
    _bridge = bridge
    _agent = StemAgent(bridge)


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
        reply = _agent.chat(message, on_event)
        with _jobs_lock:
            _jobs[job_id]["reply"] = reply
            _jobs[job_id]["state"] = "done"
    except Exception as e:
        with _jobs_lock:
            _jobs[job_id]["reply"] = f"Error: {e}"
            _jobs[job_id]["state"] = "error"


def _session_snapshot():
    try:
        s = _bridge.get_session_overview()
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
        return {"connected": False, "error": str(e), "tracks": []}


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
        return self._send(404, json.dumps({"error": "not found"}))


def main():
    _init_agent()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}"
    print(f"• Stem UI at {url}  ({len(registry.definitions())} tools loaded)")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    httpd.serve_forever()


if __name__ == "__main__":
    main()
