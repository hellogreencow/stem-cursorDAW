"""Stem-first produce / analyze / overlay tools."""
from stem.agent.loop import ToolContext
from stem.bridge.mock import MockBridge
from stem.tools.core import registry
from stem.tools import generation_tools  # noqa: F401
from stem.tools import produce_tools  # noqa: F401
from stem.services.elevenlabs_music import elevenlabs_music


def test_produce_instrumental_builds_midi_bed():
    ctx = ToolContext(bridge=MockBridge())
    r = registry.execute("produce_instrumental", {
        "style": "party",
        "key": "F",
        "is_minor": True,
        "tempo": 124,
        "bars": 8,
        "include_lead": True,
        "prompt": "party rock anthem",
    }, ctx)
    assert r.get("ok")
    assert r["producer"] == "stem"
    assert r["tempo"] == 124
    assert r["key"] == "F"
    assert "chords" in r["tracks"] and "bass" in r["tracks"] and "drums" in r["tracks"]
    overview = ctx.bridge.get_session_overview()
    midi = [t for t in overview.tracks if t.kind == "midi"]
    assert len(midi) >= 3
    assert sum(len(n) for n in ctx.bridge.notes.values()) > 20


def test_analyze_instrumental_before_and_after_produce():
    ctx = ToolContext(bridge=MockBridge())
    empty = registry.execute("analyze_instrumental", {}, ctx)
    assert empty["ready_for_vocals"] is False

    registry.execute("produce_instrumental", {
        "style": "pop", "key": "C", "bars": 4,
    }, ctx)
    ready = registry.execute("analyze_instrumental", {}, ctx)
    assert ready["ready_for_vocals"] is True
    assert ready["tempo"] == 118.0 or ready["tempo"] == 118


def test_overlay_vocals_requires_instrumental(monkeypatch, tmp_path):
    ctx = ToolContext(bridge=MockBridge())
    r = registry.execute("overlay_vocals", {
        "prompt": "soft vocal",
        "lyrics": "hello",
        "length_seconds": 8,
        "require_instrumental": True,
    }, ctx)
    assert "error" in r
    assert "produce_instrumental" in r["error"]

    registry.execute("produce_instrumental", {
        "style": "pop", "key": "C", "bars": 4,
    }, ctx)
    full = tmp_path / "full.wav"
    vocal = tmp_path / "vocal.wav"
    full.write_bytes(b"RIFFfull")
    vocal.write_bytes(b"RIFFvocal")
    monkeypatch.setattr(elevenlabs_music, "available", lambda: True)
    monkeypatch.setattr(elevenlabs_music, "generate",
                        lambda prompt, length, instrumental: str(full))
    monkeypatch.setattr(elevenlabs_music, "isolate_vocals",
                        lambda path: str(vocal))

    r = registry.execute("overlay_vocals", {
        "prompt": "soft vocal",
        "lyrics": "hello",
        "length_seconds": 8,
        "require_instrumental": True,
    }, ctx)
    assert "error" not in r
    assert r.get("overlay") is True
    assert any(items == [(str(vocal), 0.0)] for items in ctx.bridge.audio.values())


def test_generate_song_blocked_without_opt_in(monkeypatch, tmp_path):
    ctx = ToolContext(bridge=MockBridge())
    song = tmp_path / "song.wav"
    song.write_bytes(b"RIFFsong")
    monkeypatch.setattr(elevenlabs_music, "available", lambda: True)
    monkeypatch.setattr(elevenlabs_music, "generate",
                        lambda prompt, length, instrumental: str(song))
    monkeypatch.delenv("STEM_ALLOW_EXTERNAL_FULL_SONG", raising=False)

    blocked = registry.execute("generate_song", {
        "prompt": "full song",
        "length_seconds": 8,
    }, ctx)
    assert "error" in blocked
    assert "Blocked" in blocked["error"]
    assert "produce_instrumental" in blocked["use_instead"]

    allowed = registry.execute("generate_song", {
        "prompt": "full song",
        "length_seconds": 8,
        "allow_external_full_song": True,
    }, ctx)
    assert "error" not in allowed
    assert allowed["file"] == str(song)
