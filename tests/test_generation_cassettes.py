"""H6: generation cassettes / fixture WAV — no network in PR CI."""
import shutil
from pathlib import Path

import pytest

from stem.services.cassettes import cassette_key, cassette_path
from stem.services.elevenlabs_music import elevenlabs_music
from stem.services.generation import generation_service
from stem.agent.loop import ToolContext
from stem.bridge.mock import MockBridge
from stem.tools.core import registry
import stem.tools.generation_tools  # noqa: F401


FIXTURE_WAV = (Path(__file__).resolve().parent / "fixtures" / "samples_lib"
               / "warm_pad_dm.wav")


@pytest.mark.generation
def test_fixture_wav_generate_sample(monkeypatch, mock_ctx):
    monkeypatch.setenv("STEM_GEN_FIXTURE_WAV", str(FIXTURE_WAV))
    path = generation_service.generate_sample("ignored prompt", 4)
    assert Path(path).exists()
    assert Path(path).stat().st_size == FIXTURE_WAV.stat().st_size


@pytest.mark.generation
def test_cassette_replay_generate_song(monkeypatch, tmp_path, mock_ctx):
    body = {
        "prompt": "cassette test hook",
        "music_length_ms": 12000,
        "model_id": "music_v2",
        "force_instrumental": False,
    }
    cassette_dir = tmp_path / "cassettes"
    cassette_dir.mkdir()
    key = cassette_key(body)
    shutil.copyfile(FIXTURE_WAV, cassette_dir / f"{key}.wav")

    monkeypatch.setenv("STEM_CASSETTE_DIR", str(cassette_dir))
    monkeypatch.setenv("STEM_CASSETTE_MODE", "replay")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)

    assert elevenlabs_music.available()
    out = elevenlabs_music.generate("cassette test hook", length_seconds=12,
                                    instrumental=False)
    assert Path(out).exists()

    # Tool path: import onto mock session
    r = registry.execute("generate_song", {
        "prompt": "cassette test hook",
        "length_seconds": 12,
        "instrumental": False,
        "allow_external_full_song": True,
    }, mock_ctx)
    assert "error" not in r
    assert any(mock_ctx.bridge.audio.values())


@pytest.mark.generation
def test_cassette_miss_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("STEM_CASSETTE_DIR", str(tmp_path / "empty"))
    monkeypatch.setenv("STEM_CASSETTE_MODE", "replay")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    (tmp_path / "empty").mkdir()
    with pytest.raises(RuntimeError, match="cassette miss"):
        elevenlabs_music.generate("no such cassette", 8)


@pytest.mark.generation
def test_cassette_path_stable_for_body():
    body = {"prompt": "x", "music_length_ms": 3000, "model_id": "music_v2",
            "force_instrumental": True}
    assert cassette_key(body) == cassette_key(dict(body))
    # path helper returns None without STEM_CASSETTE_DIR
    assert cassette_path(body) is None
