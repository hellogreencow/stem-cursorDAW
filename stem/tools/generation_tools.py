"""Generation tools: text -> audio -> track."""
import re
from typing import List, Optional

from pydantic import BaseModel, Field

from .registry import ToolRegistry
from .core import registry
from ..services.generation import generation_service


def _provider_safe_vocal_prompt(prompt: str, lyrics: Optional[str] = None) -> str:
    """Convert user isolation wording into a positive music-generation prompt.

    ElevenLabs Music can reject prompts that frame the request as removing or
    excluding parts. We generate a vocal-forward clip, then stem-separate it, so
    the provider prompt should describe the desired voice and delivery only.
    """
    cleaned = prompt
    cleaned = re.sub(r"\blyrics?\s*:\s*.*$", "", cleaned,
                     flags=re.I | re.S)
    cleaned = re.sub(r"\bsing\s+[\"'][^\"']+[\"']", "", cleaned,
                     flags=re.I | re.S)
    cleaned = re.sub(
        r"\b(isolated|isolate|only|without\s+(?:music|song|backing|instruments?)|"
        r"no\s+(?:music|backing|instrumental|instruments?)|acapella|a cappella)\b",
        " ", cleaned, flags=re.I)
    cleaned = re.sub(
        r"\b(?:make|generate|create|get|give me|please|vocals?|voices?|voice|"
        r"seconds?|secs?|minutes?|mins?)\b",
        " ", cleaned, flags=re.I)
    cleaned = re.sub(r"\b\d+(?:\.\d+)?\b", " ", cleaned)
    cleaned = re.sub(r"[,.;:]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        cleaned = "expressive lead vocal, clear studio recording"
    else:
        cleaned = f"{cleaned}, expressive lead vocal, clear studio recording"
    if lyrics:
        cleaned = f"{cleaned}. Lyrics:\n{lyrics}"
    return cleaned


class GenerateSample(BaseModel):
    prompt: str = Field(description="Sound description, e.g. 'punchy 808 kick' "
                                    "or 'dark ambient pad in D minor'")
    duration_seconds: float = Field(default=8.0, gt=0, le=60)
    position_seconds: float = Field(default=0.0, ge=0)


@registry.register(
    "generate_sample",
    "Generate audio from a text description (ACE-Step local / Suno API) and "
    "import it as a new audio track. Undoable. Errors with setup instructions "
    "if no backend is configured.",
    GenerateSample, mutates=True)
def generate_sample(args, ctx):
    wav_path = generation_service.generate_sample(args.prompt,
                                                  args.duration_seconds)
    action_id = ctx.bridge.import_audio("", wav_path, args.position_seconds)
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


class GenerateSongStems(BaseModel):
    prompt: str = Field(description="Song description. The generated song is "
                                    "split into separate vocal and backing "
                                    "audio tracks for arranging in the DAW.")
    length_seconds: float = Field(default=20.0, gt=3, le=120)
    position_seconds: float = Field(default=0.0, ge=0)


@registry.register(
    "generate_song_stems",
    "Generate a produced song idea, split it into VOCALS and BACKING, and "
    "import both stems as separate audio tracks. Use when the user wants a "
    "vocal that can sit on top of generated instruments/backing rather than a "
    "single baked full mix. Undoable.",
    GenerateSongStems, mutates=True)
def generate_song_stems(args, ctx):
    from ..services.elevenlabs_music import elevenlabs_music
    if not elevenlabs_music.available():
        return {"error": "ElevenLabs not configured — set ELEVENLABS_API_KEY "
                "or elevenlabs_api_key in ~/.stem/config.json"}
    full = elevenlabs_music.generate(args.prompt, args.length_seconds,
                                     instrumental=False)
    stems = elevenlabs_music.separate_stems(full)
    vocal = next((p for n, p in stems.items() if "vocal" in n.lower()), None)
    backing = next((p for n, p in stems.items()
                    if any(k in n.lower() for k in
                           ("accomp", "instrument", "backing", "music"))),
                   None)
    if not vocal:
        return {"error": f"no vocal stem found in {sorted(stems)}"}
    if not backing:
        return {"error": f"no backing stem found in {sorted(stems)}"}
    vocal_action = ctx.bridge.import_audio("", vocal, args.position_seconds)
    backing_action = ctx.bridge.import_audio("", backing, args.position_seconds)
    return {
        "action_id": backing_action,
        "vocal_action_id": vocal_action,
        "backing_action_id": backing_action,
        "full_mix": full,
        "vocal_file": vocal,
        "backing_file": backing,
        "length_seconds": args.length_seconds,
    }


class GenerateVocals(BaseModel):
    prompt: str = Field(description="Describe the voice and delivery only — "
                                    "e.g. 'soulful female vocal, intimate, "
                                    "slight rasp'. The backing is generated then "
                                    "removed, so focus on the voice.")
    lyrics: Optional[str] = Field(
        default=None,
        description="Exact words to sing. Use line breaks for separate lines.")
    length_seconds: float = Field(default=20.0, gt=3, le=120)
    position_seconds: float = Field(default=0.0, ge=0)


@registry.register(
    "generate_vocals",
    "Generate ISOLATED vocals (just the singing voice, no backing). Generates "
    "a vocal-forward clip with ElevenLabs, then runs stem separation and keeps "
    "ONLY the vocal stem, importing it to its own track. Use when the user "
    "wants an acapella / vocal-only line or hook. NOTE: the melody is chosen by "
    "the model — this cannot sing a specific user-provided melody.",
    GenerateVocals, mutates=True)
def generate_vocals(args, ctx):
    from ..services.elevenlabs_music import elevenlabs_music
    if not elevenlabs_music.available():
        return {"error": "ElevenLabs not configured — set ELEVENLABS_API_KEY "
                "or elevenlabs_api_key in ~/.stem/config.json"}
    prompt = _provider_safe_vocal_prompt(args.prompt, args.lyrics)
    try:
        full = elevenlabs_music.generate(prompt, args.length_seconds,
                                         instrumental=False)
    except RuntimeError as e:
        if "bad_prompt" not in str(e) and "violated" not in str(e):
            raise
        fallback = _provider_safe_vocal_prompt(
            "expressive soulful lead vocal, clean studio pop", args.lyrics)
        full = elevenlabs_music.generate(fallback, args.length_seconds,
                                         instrumental=False)
    vocal_path = elevenlabs_music.isolate_vocals(full)
    action_id = ctx.bridge.import_audio("", vocal_path, args.position_seconds)
    return {"action_id": action_id, "file": vocal_path,
            "full_mix": full, "note": "Vocal stem only; melody chosen by model."}


class VocalLine(BaseModel):
    lyrics: str = Field(description="The words for this line/section. May span "
                                    "multiple lines.")
    duration_seconds: float = Field(default=6.0, ge=3, le=120,
                                    description="Section length (min 3s).")
    section: str = Field(default="Verse",
                         description="Section tag, e.g. Verse, Chorus, Hook.")
    styles: List[str] = Field(default_factory=list,
                              description="Per-section style/direction hints.")


class GenerateVocalLines(BaseModel):
    lines: List[VocalLine] = Field(description="Ordered sections/lines to sing.")
    style: List[str] = Field(
        default_factory=list,
        description="Overall voice/genre styles applied to the first section "
                    "(sets the tone), e.g. ['intimate female vocal', 'lo-fi'].")
    vocals_only: bool = Field(
        default=True,
        description="If true, strip the backing and keep only the vocal stem.")
    position_seconds: float = Field(default=0.0, ge=0)


@registry.register(
    "generate_vocal_lines",
    "Generate vocals with LINE-BY-LINE control using an ElevenLabs composition "
    "plan: each line becomes a section with its own lyrics, duration, and "
    "style. Optionally isolates the vocal stem. Use when the user wants "
    "specific lines sung (each line/section controlled), rather than one prompt. "
    "Each section is min 3 seconds; melody is still model-chosen.",
    GenerateVocalLines, mutates=True)
def generate_vocal_lines(args, ctx):
    from ..services.elevenlabs_music import elevenlabs_music
    if not elevenlabs_music.available():
        return {"error": "ElevenLabs not configured — set ELEVENLABS_API_KEY "
                "or elevenlabs_api_key in ~/.stem/config.json"}
    chunks = []
    for i, line in enumerate(args.lines):
        positive = list(line.styles)
        if i == 0 and args.style:
            positive = list(args.style) + positive  # first chunk sets the tone
        chunks.append({
            "text": f"[{line.section}]\n{line.lyrics}",
            "duration_ms": int(max(3.0, line.duration_seconds) * 1000),
            "positive_styles": positive,
            "negative_styles": [] if not args.vocals_only else ["instrumental"],
        })
    full = elevenlabs_music.generate_from_plan(chunks)
    out_path = elevenlabs_music.isolate_vocals(full) if args.vocals_only else full
    action_id = ctx.bridge.import_audio("", out_path, args.position_seconds)
    return {"action_id": action_id, "file": out_path, "full_mix": full,
            "sections": len(chunks),
            "note": "Line-level lyric/timing control; melody chosen by model."}


class IsolateVocals(BaseModel):
    file_path: str = Field(description="Path to an audio file to split.")
    keep: str = Field(default="vocals",
                      description="Which stem to import: 'vocals' or 'backing'.")
    position_seconds: float = Field(default=0.0, ge=0)


@registry.register(
    "isolate_vocals",
    "Split an existing audio file into vocals + backing (ElevenLabs stem "
    "separation) and import the chosen stem as a new track. Use to pull the "
    "acapella out of any song the user already has.",
    IsolateVocals, mutates=True)
def isolate_vocals(args, ctx):
    from ..services.elevenlabs_music import elevenlabs_music
    if not elevenlabs_music.available():
        return {"error": "ElevenLabs not configured."}
    if args.keep == "vocals":
        path = elevenlabs_music.isolate_vocals(args.file_path)
    else:
        stems = elevenlabs_music.separate_stems(args.file_path)
        path = next((p for n, p in stems.items()
                     if any(k in n.lower() for k in
                            ("accomp", "instrument", "backing", "music"))),
                    None)
        if not path:
            return {"error": f"no backing stem found in {sorted(stems)}"}
    action_id = ctx.bridge.import_audio("", path, args.position_seconds)
    return {"action_id": action_id, "file": path}


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
