from stem.agent.loop import ToolContext
from stem.bridge.mock import MockBridge
from stem.tools.core import registry
from stem.tools import generation_tools  # noqa: F401 - registers tools
from stem.tools.generation_tools import _provider_safe_vocal_prompt
from stem.services.elevenlabs_music import elevenlabs_music
from stem.services.generation import generation_service


def run(ctx, tool, **kwargs):
    return registry.execute(tool, kwargs, ctx)


def test_generate_sample_can_create_new_audio_track(monkeypatch, tmp_path):
    ctx = ToolContext(bridge=MockBridge())
    wav = tmp_path / "sample.wav"
    wav.write_bytes(b"RIFFfake")
    monkeypatch.setattr(generation_tools.generation_service,
                        "generate_sample", lambda prompt, duration: str(wav))

    r = run(ctx, "generate_sample", prompt="warm analog pad",
            duration_seconds=4)

    assert "error" not in r
    audio_tracks = [t for t in ctx.bridge.tracks.values()
                    if t.kind == "audio"]
    assert len(audio_tracks) == 1
    assert ctx.bridge.audio[audio_tracks[0].track_id] == [(str(wav), 0.0)]


def test_generate_sample_schema_does_not_expose_existing_track_target():
    tool = next(t for t in registry.definitions()
                if t["name"] == "generate_sample")

    assert "track_id" not in tool["input_schema"]["properties"]


def test_generation_service_falls_back_to_elevenlabs_instrumental(monkeypatch,
                                                                 tmp_path):
    wav = tmp_path / "instrumental.wav"
    wav.write_bytes(b"RIFFfake")
    monkeypatch.setattr(generation_service, "ace_available", False)
    monkeypatch.setattr(generation_service, "suno_available", False)
    monkeypatch.setattr(elevenlabs_music, "available", lambda: True)
    monkeypatch.setattr(elevenlabs_music, "generate",
                        lambda prompt, duration, instrumental: str(wav)
                        if instrumental else "")

    assert generation_service.generate_sample("bright plucked synth", 4) == str(wav)


def test_generate_song_imports_full_mix_as_audio_track(monkeypatch, tmp_path):
    ctx = ToolContext(bridge=MockBridge())
    song = tmp_path / "song.wav"
    song.write_bytes(b"RIFFfake")
    monkeypatch.setattr(elevenlabs_music, "available", lambda: True)
    monkeypatch.setattr(elevenlabs_music, "generate",
                        lambda prompt, length, instrumental: str(song))

    r = run(ctx, "generate_song", prompt="soulful hook",
            length_seconds=12, instrumental=False)

    assert "error" not in r
    assert r["file"] == str(song)
    assert any(items == [(str(song), 0.0)]
               for items in ctx.bridge.audio.values())


def test_generate_song_stems_imports_vocal_and_backing(monkeypatch, tmp_path):
    ctx = ToolContext(bridge=MockBridge())
    full = tmp_path / "song.wav"
    vocal = tmp_path / "vocals.wav"
    backing = tmp_path / "accompaniment.wav"
    for p in (full, vocal, backing):
        p.write_bytes(b"RIFFfake")
    monkeypatch.setattr(elevenlabs_music, "available", lambda: True)
    monkeypatch.setattr(elevenlabs_music, "generate",
                        lambda prompt, length, instrumental: str(full))
    monkeypatch.setattr(elevenlabs_music, "separate_stems",
                        lambda path: {
                            "vocals": str(vocal),
                            "accompaniment": str(backing),
                        })

    r = run(ctx, "generate_song_stems", prompt="soulful hook",
            length_seconds=8)

    assert "error" not in r
    assert r["full_mix"] == str(full)
    assert r["vocal_file"] == str(vocal)
    assert r["backing_file"] == str(backing)
    imported = list(ctx.bridge.audio.values())
    assert [(str(vocal), 0.0)] in imported
    assert [(str(backing), 0.0)] in imported


def test_generate_vocals_imports_isolated_vocal_stem(monkeypatch, tmp_path):
    ctx = ToolContext(bridge=MockBridge())
    full = tmp_path / "full.wav"
    vocal = tmp_path / "vocals.wav"
    full.write_bytes(b"RIFFfull")
    vocal.write_bytes(b"RIFFvocal")
    monkeypatch.setattr(elevenlabs_music, "available", lambda: True)
    monkeypatch.setattr(elevenlabs_music, "generate",
                        lambda prompt, length, instrumental: str(full))
    monkeypatch.setattr(elevenlabs_music, "isolate_vocals",
                        lambda path: str(vocal))

    r = run(ctx, "generate_vocals", prompt="intimate lead vocal",
            lyrics="stay with me", length_seconds=8, position_seconds=2)

    assert "error" not in r
    assert r["file"] == str(vocal)
    assert r["full_mix"] == str(full)
    assert any(items == [(str(vocal), 2)]
               for items in ctx.bridge.audio.values())


def test_vocal_prompt_sanitizes_negative_isolation_language():
    prompt = _provider_safe_vocal_prompt(
        'make isolated vocals without music, sing "stay here", 4 seconds',
        "stay here")

    lowered = prompt.lower()
    assert "without music" not in lowered
    assert "isolated" not in lowered
    assert "sing" not in lowered
    assert "stay here" in prompt


def test_generate_vocal_lines_builds_composition_plan(monkeypatch, tmp_path):
    ctx = ToolContext(bridge=MockBridge())
    full = tmp_path / "planned.wav"
    vocal = tmp_path / "planned_vocals.wav"
    full.write_bytes(b"RIFFplan")
    vocal.write_bytes(b"RIFFvocal")
    seen = {}
    monkeypatch.setattr(elevenlabs_music, "available", lambda: True)
    monkeypatch.setattr(elevenlabs_music, "generate_from_plan",
                        lambda chunks: seen.setdefault("chunks", chunks)
                        and str(full))
    monkeypatch.setattr(elevenlabs_music, "isolate_vocals",
                        lambda path: str(vocal))

    r = run(ctx, "generate_vocal_lines", lines=[{
        "lyrics": "first line",
        "duration_seconds": 3,
        "section": "Hook",
        "styles": ["breathy"],
    }], style=["neo soul"], vocals_only=True)

    assert "error" not in r
    assert r["file"] == str(vocal)
    assert seen["chunks"][0]["text"] == "[Hook]\nfirst line"
    assert seen["chunks"][0]["positive_styles"] == ["neo soul", "breathy"]
    assert "instrumental" in seen["chunks"][0]["negative_styles"]
