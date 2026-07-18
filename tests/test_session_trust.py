"""Live-field quarantine: absurd tempo/sample_rate must not be trusted."""
from stem.bridge.ardour import ArdourBridge, _sanitize_sample_rate, _sanitize_tempo
from stem.tools.core import registry
from stem.agent.loop import ToolContext


class FakeOverviewBridge(ArdourBridge):
    def __init__(self, payload):
        self.rpc_timeout = 1.0
        self._action_seq = []
        self._payload = payload

    def _call(self, method, args=None):
        if method == "get_session_overview":
            return self._payload
        raise AssertionError(method)


def test_sanitize_tempo_rejects_inf():
    value, ok = _sanitize_tempo(float("inf"))
    assert not ok and value == 120.0


def test_sanitize_sample_rate_rejects_int64_max():
    value, ok = _sanitize_sample_rate(2**63 - 1)
    assert not ok and value == 48000


def test_overview_marks_untrusted_fields():
    bridge = FakeOverviewBridge({
        "name": "live",
        "tempo": float("inf"),
        "sample_rate": 2**63 - 1,
        "meter": "4/4",
        "tracks": [],
        "markers": [],
        "playhead_seconds": 0.0,
    })
    overview = bridge.get_session_overview()
    assert overview.tempo == 120.0
    assert overview.sample_rate == 48000
    assert set(overview.untrusted_fields) == {"tempo", "sample_rate"}

    tool = registry.execute("get_session_overview", {}, ToolContext(bridge=bridge))
    assert set(tool["untrusted_fields"]) == {"tempo", "sample_rate"}


def test_trusted_overview_omits_untrusted_key(mock_ctx):
    tool = registry.execute("get_session_overview", {}, mock_ctx)
    assert "untrusted_fields" not in tool
    assert tool["tempo"] == 120.0
