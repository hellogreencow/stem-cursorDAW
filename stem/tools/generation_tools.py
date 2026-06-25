"""Generation tools: text -> audio -> track."""
from pydantic import BaseModel, Field

from .registry import ToolRegistry
from .core import registry
from ..services.generation import generation_service


class GenerateSample(BaseModel):
    prompt: str = Field(description="Sound description, e.g. 'punchy 808 kick' "
                                    "or 'dark ambient pad in D minor'")
    duration_seconds: float = Field(default=8.0, gt=0, le=60)
    track_id: str = Field(description="Track to place the generated audio on")
    position_seconds: float = Field(default=0.0, ge=0)


@registry.register(
    "generate_sample",
    "Generate audio from a text description (ACE-Step local / Suno API) and "
    "place it on a track. Undoable. Errors with setup instructions if no "
    "backend is configured.",
    GenerateSample, mutates=True)
def generate_sample(args, ctx):
    wav_path = generation_service.generate_sample(args.prompt,
                                                  args.duration_seconds)
    action_id = ctx.bridge.import_audio(args.track_id, wav_path,
                                        args.position_seconds)
    return {"action_id": action_id, "file": wav_path}


class GenerateSong(BaseModel):
    prompt: str = Field(description="Song/vocal description, e.g. 'a soulful "
                                    "female vocal hook over warm chords' or "
                                    "'energetic rap verse, trap beat'")
    length_seconds: float = Field(default=20.0, gt=3, le=120,
                                  description="Clip length in seconds")
    instrumental: bool = Field(default=False,
                               description="True for no vocals (backing only)")
    position_seconds: float = Field(default=0.0, ge=0)


@registry.register(
    "generate_song",
    "Generate a produced song or sung vocal hook (real vocals, ElevenLabs "
    "Music) from a text description and import it as a new audio track. "
    "Use this for VOCALS / full musical audio — not for MIDI. Undoable.",
    GenerateSong, mutates=True)
def generate_song(args, ctx):
    from ..services.elevenlabs_music import elevenlabs_music
    if not elevenlabs_music.available():
        return {"error": "ElevenLabs not configured — set ELEVENLABS_API_KEY "
                "or elevenlabs_api_key in ~/.stem/config.json"}
    path = elevenlabs_music.generate(args.prompt, args.length_seconds,
                                     args.instrumental)
    action_id = ctx.bridge.import_audio("", path, args.position_seconds)
    return {"action_id": action_id, "file": path,
            "length_seconds": args.length_seconds}


class Empty(BaseModel):
    pass


@registry.register("get_generation_status",
                   "Check which audio-generation backends are available "
                   "(local ACE-Step, ElevenLabs vocals).",
                   Empty)
def get_generation_status(args, ctx):
    from ..services.elevenlabs_music import elevenlabs_music
    s = generation_service.status()
    s["elevenlabs_vocals"] = elevenlabs_music.available()
    return s
