from stem.bridge.ardour import ArdourBridge


class FakeArdourBridge(ArdourBridge):
    def __init__(self):
        self.rpc_timeout = 1.0
        self._action_seq = []
        self.imported_path = None

    def _call(self, method, args=None):
        if method == "get_session_overview":
            return {"sample_rate": 48000, "tracks": []}
        if method == "import_audio":
            self.imported_path = args["file_path"]
            return {"track_id": "Imported"}
        if method == "get_track_content":
            return {"regions": [{"length_samples": 1000}]}
        raise AssertionError(method)


def test_import_audio_resamples_to_session_rate(monkeypatch):
    bridge = FakeArdourBridge()
    monkeypatch.setattr(bridge, "_audio_sample_rate", lambda path: 44100)
    monkeypatch.setattr(bridge, "_resample_audio",
                        lambda path, rate: f"/tmp/resampled-{rate}.wav")

    action_id = bridge.import_audio("", "/tmp/source.wav", 0)

    assert action_id
    assert bridge.imported_path == "/tmp/resampled-48000.wav"


def test_import_audio_keeps_matching_rate(monkeypatch):
    bridge = FakeArdourBridge()
    monkeypatch.setattr(bridge, "_audio_sample_rate", lambda path: 48000)
    monkeypatch.setattr(bridge, "_resample_audio",
                        lambda path, rate: (_ for _ in ()).throw(
                            AssertionError("should not resample")))

    bridge.import_audio("", "/tmp/source.wav", 0)

    assert bridge.imported_path == "/tmp/source.wav"
