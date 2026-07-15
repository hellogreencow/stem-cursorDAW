import http.client
import json
import threading

from stem import webserver
from stem.agent.loop import ToolContext
from stem.bridge.mock import MockBridge


def test_embedded_server_reuses_address_and_daemonizes_threads():
    assert webserver.StemHTTPServer.allow_reuse_address is True
    assert webserver.StemHTTPServer.daemon_threads is True


def test_force_mock_does_not_upgrade_to_live(monkeypatch):
    created = []

    class ExplodingArdourBridge:
        def __init__(self, *args, **kwargs):
            created.append((args, kwargs))
            raise AssertionError("forced mock must not probe live Ardour")

    monkeypatch.setattr(webserver, "ArdourBridge", ExplodingArdourBridge)
    webserver._bridge = MockBridge()
    webserver._agent = object()
    webserver._force_mock = True

    webserver._ensure_live_bridge()

    assert created == []
    assert isinstance(webserver._bridge, MockBridge)


def test_history_endpoint_replaces_running_agent_context():
    class RecordingAgent:
        def __init__(self):
            self.messages = []

        def load_history(self, turns):
            self.messages = list(turns)

    webserver._agent = RecordingAgent()
    httpd = webserver.StemHTTPServer(("127.0.0.1", 0), webserver.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        body = json.dumps({
            "turns": [
                {"role": "user", "content": "make drums"},
                {"role": "assistant", "content": "I made drums."},
            ]
        })
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=2)
        conn.request("POST", "/api/history", body, {"Content-Type": "application/json"})
        resp = conn.getresponse()
        payload = json.loads(resp.read())
    finally:
        httpd.shutdown()
        httpd.server_close()

    assert resp.status == 200
    assert payload == {"ok": True, "turns": 2}
    assert webserver._agent.messages == [
        {"role": "user", "content": "make drums"},
        {"role": "assistant", "content": "I made drums."},
    ]


def test_local_fallback_job_records_visible_turn():
    class RecordingAgent:
        def __init__(self):
            self.ctx = ToolContext(bridge=MockBridge())
            self.messages = []

        def record_turn(self, message, reply):
            self.messages.extend([
                {"role": "user", "content": message},
                {"role": "assistant", "content": reply},
            ])

    old_agent = webserver._agent
    old_bridge = webserver._bridge
    old_force_mock = webserver._force_mock
    try:
        webserver._agent = RecordingAgent()
        webserver._bridge = webserver._agent.ctx.bridge
        webserver._force_mock = True
        with webserver._jobs_lock:
            webserver._jobs["job"] = {"state": "thinking", "activity": [],
                                      "reply": ""}

        webserver._run_job("job", "set tempo to 94 bpm")

        with webserver._jobs_lock:
            job = webserver._jobs["job"]
        assert job["state"] == "done"
        assert webserver._agent.messages[0] == {
            "role": "user", "content": "set tempo to 94 bpm"}
        assert webserver._agent.messages[1]["role"] == "assistant"
        assert "94 BPM" in webserver._agent.messages[1]["content"]
    finally:
        webserver._agent = old_agent
        webserver._bridge = old_bridge
        webserver._force_mock = old_force_mock
        with webserver._jobs_lock:
            webserver._jobs.pop("job", None)


def test_instrument_endpoint_reports_library_summary():
    old_agent = webserver._agent
    old_bridge = webserver._bridge
    old_force_mock = webserver._force_mock
    try:
        webserver._bridge = MockBridge()
        webserver._agent = type("Agent", (), {
            "ctx": ToolContext(bridge=webserver._bridge),
            "messages": [],
        })()
        webserver._force_mock = True
        httpd = webserver.StemHTTPServer(("127.0.0.1", 0), webserver.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=2)
            conn.request("GET", "/api/instruments")
            resp = conn.getresponse()
            payload = json.loads(resp.read())
        finally:
            httpd.shutdown()
            httpd.server_close()
    finally:
        webserver._agent = old_agent
        webserver._bridge = old_bridge
        webserver._force_mock = old_force_mock

    assert resp.status == 200
    assert payload["count"] == 2
    assert payload["summary"].startswith("Library: 2 instruments.")
    assert {i["category"] for i in payload["instruments"]} == {"Piano", "Synth"}
