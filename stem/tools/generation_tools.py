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


class Empty(BaseModel):
    pass


@registry.register("get_generation_status",
                   "Check which audio-generation backends are available.",
                   Empty)
def get_generation_status(args, ctx):
    return generation_service.status()
