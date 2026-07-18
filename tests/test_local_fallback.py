from stem.agent.local_fallback import handle_local_intent
from stem.agent.loop import ToolContext
from stem.bridge.mock import MockBridge
from stem.tools import generation_tools  # noqa: F401 - registers tools
from stem.services.elevenlabs_music import elevenlabs_music
from stem.services.generation import generation_service


def test_webserver_upgrades_mock_bridge_when_ardour_appears(monkeypatch):
    from stem import webserver

    class FakeArdour:
        def __init__(self, rpc_timeout=1.0):
            self.rpc_timeout = rpc_timeout

        def connected(self):
            return True

    old_agent = webserver._agent
    old_bridge = webserver._bridge
    try:
        webserver._bridge = MockBridge()
        webserver._agent = type("Agent", (), {"ctx": ToolContext(
            bridge=webserver._bridge),
            "messages": [{"role": "user", "content": "loaded history"}]})()
        monkeypatch.setattr(webserver, "ArdourBridge", FakeArdour)

        webserver._ensure_live_bridge()

        assert isinstance(webserver._bridge, FakeArdour)
        assert webserver._agent.ctx.bridge is webserver._bridge
        assert webserver._bridge.rpc_timeout == 15.0
        assert webserver._agent.messages == [{"role": "user",
                                              "content": "loaded history"}]
    finally:
        webserver._agent = old_agent
        webserver._bridge = old_bridge


def events():
    seen = []
    return seen, lambda kind, payload: seen.append((kind, payload))


def test_local_generation_status(monkeypatch):
    ctx = ToolContext(bridge=MockBridge())
    monkeypatch.setattr(generation_service, "ace_available", False)
    monkeypatch.setattr(generation_service, "suno_available", False)
    monkeypatch.setattr(elevenlabs_music, "available", lambda: True)
    seen, emit = events()

    reply = handle_local_intent("check generation backend status", ctx, emit)

    assert "elevenlabs" in reply.lower()
    assert seen[0][1]["name"] == "get_generation_status"


def test_local_set_tempo():
    ctx = ToolContext(bridge=MockBridge())
    seen, emit = events()

    reply = handle_local_intent("set tempo to 94 bpm", ctx, emit)

    assert "94 BPM" in reply
    assert ctx.bridge.tempo == 94
    assert seen[0][1] == {"name": "get_session_overview", "input": {}}
    assert seen[2][1] == {"name": "set_tempo", "input": {"bpm": 94.0}}


def test_local_isolated_vocals(monkeypatch, tmp_path):
    ctx = ToolContext(bridge=MockBridge())
    full = tmp_path / "full.wav"
    vocal = tmp_path / "vocal.wav"
    full.write_bytes(b"RIFFfull")
    vocal.write_bytes(b"RIFFvocal")
    monkeypatch.setattr(elevenlabs_music, "available", lambda: True)
    monkeypatch.setattr(elevenlabs_music, "generate",
                        lambda prompt, length, instrumental: str(full))
    monkeypatch.setattr(elevenlabs_music, "isolate_vocals",
                        lambda path: str(vocal))
    seen, emit = events()

    reply = handle_local_intent(
        'make isolated vocals without music, sing "stay here", 4 seconds',
        ctx, emit)

    assert "isolated vocals" in reply.lower()
    assert any(e[1]["name"] == "generate_vocals" for e in seen
               if e[0] == "tool_call")
    assert any(items == [(str(vocal), 0.0)] for items in ctx.bridge.audio.values())


def test_local_full_song_uses_stem_produce_then_overlay(monkeypatch, tmp_path):
    ctx = ToolContext(bridge=MockBridge())
    full = tmp_path / "full.wav"
    vocal = tmp_path / "vocal.wav"
    full.write_bytes(b"RIFFfull")
    vocal.write_bytes(b"RIFFvocal")
    monkeypatch.setattr(elevenlabs_music, "available", lambda: True)
    monkeypatch.setattr(elevenlabs_music, "generate",
                        lambda prompt, length, instrumental: str(full))
    monkeypatch.setattr(elevenlabs_music, "isolate_vocals",
                        lambda path: str(vocal))
    seen, emit = events()

    reply = handle_local_intent(
        "generate a full song with vocals, soulful hook, 5 seconds",
        ctx, emit)

    assert "stem produced" in reply.lower()
    assert "not an external full-song" in reply.lower()
    calls = [e[1]["name"] for e in seen if e[0] == "tool_call"]
    assert "produce_instrumental" in calls
    assert "overlay_vocals" in calls
    assert "generate_song" not in calls
    assert any(items == [(str(vocal), 0.0)] for items in ctx.bridge.audio.values())


def test_local_separated_song_stems_uses_stem_bed_plus_overlay(monkeypatch,
                                                              tmp_path):
    ctx = ToolContext(bridge=MockBridge())
    full = tmp_path / "song.wav"
    vocal = tmp_path / "vocals.wav"
    for p in (full, vocal):
        p.write_bytes(b"RIFF")
    monkeypatch.setattr(elevenlabs_music, "available", lambda: True)
    monkeypatch.setattr(elevenlabs_music, "generate",
                        lambda prompt, length, instrumental: str(full))
    monkeypatch.setattr(elevenlabs_music, "isolate_vocals",
                        lambda path: str(vocal))
    seen, emit = events()

    reply = handle_local_intent(
        "generate a song with vocals and separate stems so I can apply the "
        "voice on top of the backing, 4 seconds",
        ctx, emit)

    assert "stem produced the instrumental" in reply.lower()
    calls = [e[1]["name"] for e in seen if e[0] == "tool_call"]
    assert "produce_instrumental" in calls
    assert "overlay_vocals" in calls
    assert "generate_song_stems" not in calls
    assert any(items == [(str(vocal), 0.0)] for items in ctx.bridge.audio.values())


def test_local_vocal_on_existing_instruments_uses_isolated_vocal(monkeypatch, tmp_path):
    ctx = ToolContext(bridge=MockBridge())
    full = tmp_path / "full.wav"
    vocal = tmp_path / "vocal.wav"
    full.write_bytes(b"RIFFfull")
    vocal.write_bytes(b"RIFFvocal")
    monkeypatch.setattr(elevenlabs_music, "available", lambda: True)
    monkeypatch.setattr(elevenlabs_music, "generate",
                        lambda prompt, length, instrumental: str(full))
    monkeypatch.setattr(elevenlabs_music, "isolate_vocals",
                        lambda path: str(vocal))
    seen, emit = events()

    reply = handle_local_intent(
        "add a soulful vocal hook on top of the instruments, 5 seconds",
        ctx, emit)

    assert "existing instruments" in reply.lower()
    calls = [e[1]["name"] for e in seen if e[0] == "tool_call"]
    assert "overlay_vocals" in calls
    assert "generate_song" not in calls
    assert any(items == [(str(vocal), 0.0)] for items in ctx.bridge.audio.values())


def test_local_arrangement_with_isolated_vocal(monkeypatch, tmp_path):
    ctx = ToolContext(bridge=MockBridge())
    full = tmp_path / "full.wav"
    vocal = tmp_path / "vocal.wav"
    full.write_bytes(b"RIFFfull")
    vocal.write_bytes(b"RIFFvocal")
    monkeypatch.setattr(elevenlabs_music, "available", lambda: True)
    monkeypatch.setattr(elevenlabs_music, "generate",
                        lambda prompt, length, instrumental: str(full))
    monkeypatch.setattr(elevenlabs_music, "isolate_vocals",
                        lambda path: str(vocal))
    seen, emit = events()

    reply = handle_local_intent(
        'make instruments with chords, bassline, drums and isolated vocals '
        'in D minor at 92 bpm, sing "hold the night", 4 seconds',
        ctx, emit)

    assert "D minor backing arrangement" in reply
    overview = ctx.bridge.get_session_overview()
    midi_tracks = [t for t in overview.tracks if t.kind == "midi"]
    audio_tracks = [t for t in overview.tracks if t.kind == "audio"]
    assert len(midi_tracks) == 3
    assert len(audio_tracks) == 1
    assert ctx.bridge.tempo == 92
    assert any(len(notes) >= 12 for notes in ctx.bridge.notes.values())
    assert any(items == [(str(vocal), 0.0)] for items in ctx.bridge.audio.values())
    calls = [e[1]["name"] for e in seen if e[0] == "tool_call"]
    assert calls[:3] == ["get_session_overview", "set_tempo", "create_midi_track"]
    assert "insert_chord_progression" in calls
    assert "insert_bassline" in calls
    assert "insert_drum_pattern" in calls
    assert calls[-1] == "generate_vocals"


def test_local_background_tune_for_existing_vocals_stays_local():
    ctx = ToolContext(bridge=MockBridge())
    seen, emit = events()

    reply = handle_local_intent(
        "i need you to produce a gaelic war chant type tune for the "
        "background of these vocals",
        ctx, emit)

    assert "Gaelic war-chant backing" in reply
    assert "D minor" in reply
    assert ctx.bridge.tempo == 88
    overview = ctx.bridge.get_session_overview()
    midi_tracks = [t for t in overview.tracks if t.kind == "midi"]
    assert len(midi_tracks) == 4
    assert any(t.name == "Stem Chant Lead" for t in midi_tracks)
    assert any(len(notes) >= 8 for notes in ctx.bridge.notes.values())
    calls = [e[1]["name"] for e in seen if e[0] == "tool_call"]
    assert calls[:3] == ["get_session_overview", "set_tempo", "create_midi_track"]
    assert "insert_chord_progression" in calls
    assert "insert_bassline" in calls
    assert "insert_drum_pattern" in calls
    assert "insert_midi_notes" in calls
    assert "generate_vocals" not in calls


def test_local_make_song_better_and_minute_long_stays_local():
    ctx = ToolContext(bridge=MockBridge())
    seen, emit = events()

    reply = handle_local_intent(
        "how would you make the entire song better? and a minute long please",
        ctx, emit)

    assert "60 second" in reply
    assert "backing" in reply
    overview = ctx.bridge.get_session_overview()
    midi_tracks = [t for t in overview.tracks if t.kind == "midi"]
    assert len(midi_tracks) == 4
    drum_calls = [e[1]["input"] for e in seen
                  if e[0] == "tool_call"
                  and e[1]["name"] == "insert_drum_pattern"]
    assert drum_calls[0]["bars"] >= 20


def test_local_add_fitting_vocals_and_slow_minute_wins_over_backing(monkeypatch, tmp_path):
    ctx = ToolContext(bridge=MockBridge())
    full = tmp_path / "full.wav"
    vocal = tmp_path / "vocal.wav"
    full.write_bytes(b"RIFFfull")
    vocal.write_bytes(b"RIFFvocal")
    monkeypatch.setattr(elevenlabs_music, "available", lambda: True)
    monkeypatch.setattr(elevenlabs_music, "generate",
                        lambda prompt, length, instrumental: str(full))
    monkeypatch.setattr(elevenlabs_music, "isolate_vocals",
                        lambda path: str(vocal))
    seen, emit = events()

    reply = handle_local_intent(
        "now add vocals that fit and make the song one minute long and slower bpm",
        ctx, emit)

    assert "added isolated vocals" in reply.lower()
    assert "60 second" in reply
    assert "90 BPM" in reply
    assert ctx.bridge.tempo == 90
    assert any(items == [(str(vocal), 0.0)] for items in ctx.bridge.audio.values())
    calls = [e[1]["name"] for e in seen if e[0] == "tool_call"]
    # Fitting-vocals path still uses generate_vocals after Stem bed arrange
    assert "generate_vocals" in calls or "overlay_vocals" in calls
    vocal_call = "overlay_vocals" if "overlay_vocals" in calls else "generate_vocals"
    assert calls.index(vocal_call) > calls.index("insert_drum_pattern")


def test_local_retro_platformer_theme_stays_local_and_original():
    ctx = ToolContext(bridge=MockBridge())
    seen, emit = events()

    reply = handle_local_intent(
        "make a me a super mario inspired theme song",
        ctx, emit)

    assert "original retro platformer theme" in reply
    assert ctx.bridge.tempo == 160
    overview = ctx.bridge.get_session_overview()
    midi_tracks = [t for t in overview.tracks if t.kind == "midi"]
    assert len(midi_tracks) == 4
    calls = [e[1]["name"] for e in seen if e[0] == "tool_call"]
    assert "insert_midi_notes" in calls
    assert "generate_song" not in calls
    assert "generate_vocals" not in calls


def test_local_deep_house_bass_track_builds_full_arrangement():
    ctx = ToolContext(bridge=MockBridge())
    seen, emit = events()

    reply = handle_local_intent(
        "chris stussy inspired bass track. full length please",
        ctx, emit)

    assert "32-bar deep/tech-house track" in reply
    assert ctx.bridge.tempo == 126
    overview = ctx.bridge.get_session_overview()
    midi_tracks = [t for t in overview.tracks if t.kind == "midi"]
    assert {t.name for t in midi_tracks} == {
        "Stem Deep Bass",
        "Stem House Drums",
        "Stem Chord Stabs",
        "Stem Hats & Perc",
    }
    assert len(midi_tracks) == 4
    assert sum(len(notes) for notes in ctx.bridge.notes.values()) > 500
    calls = [e[1]["name"] for e in seen if e[0] == "tool_call"]
    assert calls.count("create_midi_track") == 4
    assert "insert_drum_pattern" in calls
    assert calls.count("insert_midi_notes") == 3
    assert "generate_song" not in calls


def test_local_party_rock_anthem_uses_stem_produce(monkeypatch, tmp_path):
    ctx = ToolContext(bridge=MockBridge())
    seen, emit = events()

    reply = handle_local_intent("make me a party rock anthem", ctx, emit)

    assert "stem produced" in reply.lower()
    assert "instrumental" in reply.lower()
    overview = ctx.bridge.get_session_overview()
    midi = [t for t in overview.tracks if t.kind == "midi"]
    assert len(midi) >= 3
    calls = [e[1]["name"] for e in seen if e[0] == "tool_call"]
    assert "produce_instrumental" in calls
    assert "generate_song" not in calls


def test_local_instrumental_sample(monkeypatch, tmp_path):
    ctx = ToolContext(bridge=MockBridge())
    wav = tmp_path / "loop.wav"
    wav.write_bytes(b"RIFFloop")
    monkeypatch.setattr(generation_service, "generate_sample",
                        lambda prompt, duration: str(wav))

    reply = handle_local_intent("make a bright instrumental loop, 4 seconds", ctx)

    assert "instrumental audio" in reply.lower()
    assert any(items == [(str(wav), 0.0)] for items in ctx.bridge.audio.values())


def test_unrecognized_intent_returns_none():
    assert handle_local_intent("what key is this bridge in?", ToolContext(
        bridge=MockBridge())) is None
