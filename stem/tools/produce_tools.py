"""Stem-first production tools: own the instrumental, overlay vocals later."""
from typing import Optional

from pydantic import BaseModel, Field

from .core import registry
from .generation_tools import GenerateVocals, generate_vocals
from ..services.produce import (
    ProduceRequest, analyze_instrumental, produce_instrumental,
)
from ..theory import ChordProgression


_PROG_HELP = ", ".join(ChordProgression.COMMON_PROGRESSIONS.keys())


class ProduceInstrumental(BaseModel):
    style: str = Field(
        default="default",
        description="Genre/style hint: pop, rock, party, house, deep_house, "
                    "hip_hop, trap, emotional — sets tempo/drums/progression "
                    "defaults when not overridden.")
    key: str = Field(default="C", description="Tonal center, e.g. C, F#, Bb")
    is_minor: bool = Field(default=False)
    tempo: Optional[float] = Field(
        default=None, gt=20, lt=400,
        description="BPM. Omit to use style default.")
    progression: Optional[str] = Field(
        default=None,
        description=f"Named progression. One of: {_PROG_HELP}. "
                    "Omit to use style default.")
    bars: int = Field(default=8, ge=4, le=64)
    drum_style: Optional[str] = Field(
        default=None,
        description="four_on_floor | house | hip_hop | trap | breakbeat | dnb")
    bass_style: Optional[str] = Field(
        default=None,
        description="pulse | walking | syncopated | bounce | driving")
    include_lead: bool = Field(
        default=False,
        description="Add a Stem-written lead motif track.")
    prompt: str = Field(
        default="",
        description="Free-text intent used only to infer style defaults when "
                    "style is 'default'. Does NOT call an external song API.")


@registry.register(
    "produce_instrumental",
    "STEM PRODUCES THE SONG: build a full instrumental bed in the DAW "
    "(chords + bass + drums, optional lead) from style/key/progression/bars. "
    "This is the default way to make a track. NEVER use generate_song for this. "
    "When the producer is happy, call analyze_instrumental then overlay_vocals.",
    ProduceInstrumental, mutates=True)
def produce_instrumental_tool(args, ctx):
    return produce_instrumental(
        ProduceRequest(
            style=args.style,
            key=args.key,
            is_minor=args.is_minor,
            tempo=args.tempo,
            progression=args.progression,
            bars=args.bars,
            drum_style=args.drum_style,
            bass_style=args.bass_style,
            include_lead=args.include_lead,
            prompt=args.prompt,
        ),
        ctx,
    )


class Empty(BaseModel):
    pass


@registry.register(
    "analyze_instrumental",
    "Inspect the current Stem session (tempo, MIDI/audio tracks, note counts, "
    "markers) and report whether it is ready for vocal overlay. Call this after "
    "produce_instrumental when the producer is comfortable with the bed.",
    Empty)
def analyze_instrumental_tool(args, ctx):
    return analyze_instrumental(ctx)


class OverlayVocals(BaseModel):
    prompt: str = Field(
        description="Voice/delivery only — e.g. 'intimate female lead, "
                    "deep house'. Does not generate instruments.")
    lyrics: Optional[str] = Field(
        default=None,
        description="Exact words to sing. Use line breaks for lines.")
    length_seconds: float = Field(default=20.0, gt=3, le=120)
    position_seconds: float = Field(default=0.0, ge=0)
    require_instrumental: bool = Field(
        default=True,
        description="If true, refuse when the session has no Stem material "
                    "(no MIDI notes / audio). Set false only to import an "
                    "acapella into an empty session on purpose.")


@registry.register(
    "overlay_vocals",
    "OPTIONAL SECOND STEP: generate an ISOLATED vocal stem and import it on "
    "top of the Stem instrumental. Requires an existing bed unless "
    "require_instrumental=false. Never replaces the Stem track with a full "
    "external song+vocals mix.",
    OverlayVocals, mutates=True)
def overlay_vocals(args, ctx):
    if args.require_instrumental:
        analysis = analyze_instrumental(ctx)
        if not analysis.get("ready_for_vocals"):
            return {
                "error": "No Stem instrumental found. Call produce_instrumental "
                         "(or StemScript / arrange) first, then overlay_vocals. "
                         "Stem owns the song; the API only adds a vocal layer.",
                "analysis": analysis,
            }
    result = generate_vocals(
        GenerateVocals(
            prompt=args.prompt,
            lyrics=args.lyrics,
            length_seconds=args.length_seconds,
            position_seconds=args.position_seconds,
        ),
        ctx,
    )
    if isinstance(result, dict) and "error" not in result:
        result["overlay"] = True
        result["note"] = (
            "Vocal stem overlaid on Stem instrumental — not a full-song API mix."
        )
    return result
