from stem.agent.loop import ToolContext
from stem.bridge.mock import MockBridge
from stem.language import execute_stemscript, looks_like_stemscript, parse_stemscript
from stem.services.elevenlabs_music import elevenlabs_music
from stem.tools import generation_tools  # noqa: F401 - registers tools


def test_stemscript_parser_accepts_music_shape():
    state = parse_stemscript("""
    stem:
    tempo 128
    key D minor
    duration 60s
    chords Andalusian
    drums breakbeat
    bass driving
    lead chant
    """)

    assert state.tempo == 128
    assert state.key == "D"
    assert state.minor
    assert state.duration_seconds == 60
    assert state.progression == "Andalusian"
    assert state.bars == 32


def test_stemscript_compiles_to_undoable_tracks():
    ctx = ToolContext(bridge=MockBridge())
    seen = []

    reply = execute_stemscript("""
    stem:
    tempo 128
    key D minor
    duration 30s
    chords Dm Bb C Dm
    drums breakbeat
    bass driving
    lead chant
    """, ctx, lambda kind, payload: seen.append((kind, payload)))

    assert "Ran StemScript" in reply
    assert ctx.bridge.tempo == 128
    overview = ctx.bridge.get_session_overview()
    assert len([t for t in overview.tracks if t.kind == "midi"]) == 4
    calls = [e[1]["name"] for e in seen if e[0] == "tool_call"]
    assert calls[:2] == ["set_tempo", "create_midi_track"]
    assert "insert_chord" in calls
    assert "insert_bassline" in calls
    assert "insert_drum_pattern" in calls
    assert "insert_midi_notes" in calls


def test_stemscript_song_uses_song_generation(monkeypatch, tmp_path):
    ctx = ToolContext(bridge=MockBridge())
    song = tmp_path / "song.wav"
    song.write_bytes(b"RIFFsong")
    monkeypatch.setattr(elevenlabs_music, "available", lambda: True)
    monkeypatch.setattr(elevenlabs_music, "generate",
                        lambda prompt, length, instrumental: str(song))

    reply = execute_stemscript("""
    stem:
    duration 12s
    song "party rock anthem with a massive chorus"
    """, ctx)

    assert "generated a song" in reply
    assert any(items == [(str(song), 0.0)] for items in ctx.bridge.audio.values())


def test_stemscript_detection_requires_script_shape():
    assert looks_like_stemscript("tempo 128\nkey D minor\nchords Andalusian")
    assert looks_like_stemscript(
        "stem:; tempo 128; key D minor; chords Dm Bb C Dm")
    assert not looks_like_stemscript("what key is this bridge in?")
