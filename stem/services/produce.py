"""Stem-owned instrumental production.

Stem builds the song (MIDI: chords, bass, drums, optional lead) from style /
key / progression. External APIs may only overlay vocals afterward — never
replace the bed with a full generated song.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from ..theory import ChordProgression


EventFn = Callable[[str, dict], None]

# Style → musical defaults. Explicit tool args always win.
STYLE_PRESETS: Dict[str, Dict[str, Any]] = {
    "pop": {
        "tempo": 118.0, "drums": "four_on_floor", "bass": "pulse",
        "progression": "I_V_vi_IV", "minor_progression": "i_VII_VI_V",
    },
    "rock": {
        "tempo": 120.0, "drums": "four_on_floor", "bass": "driving",
        "progression": "I_IV_V", "minor_progression": "i_VII_VI_V",
    },
    "party": {
        "tempo": 128.0, "drums": "four_on_floor", "bass": "pulse",
        "progression": "I_V_vi_IV", "minor_progression": "i_VII_VI_V",
    },
    "house": {
        "tempo": 124.0, "drums": "house", "bass": "driving",
        "progression": "i_VII_VI_V", "minor_progression": "i_VII_VI_V",
        "prefer_minor": True,
    },
    "deep_house": {
        "tempo": 124.0, "drums": "house", "bass": "driving",
        "progression": "i_VII_VI_V", "minor_progression": "Andalusian",
        "prefer_minor": True,
    },
    "hip_hop": {
        "tempo": 90.0, "drums": "hip_hop", "bass": "pulse",
        "progression": "i_VII_VI_V", "minor_progression": "i_VII_VI_V",
        "prefer_minor": True,
    },
    "trap": {
        "tempo": 140.0, "drums": "trap", "bass": "syncopated",
        "progression": "i_VII_VI_V", "minor_progression": "i_VII_VI_V",
        "prefer_minor": True,
    },
    "emotional": {
        "tempo": 96.0, "drums": "four_on_floor", "bass": "pulse",
        "progression": "Emotional", "minor_progression": "Andalusian",
    },
    "default": {
        "tempo": 120.0, "drums": "four_on_floor", "bass": "pulse",
        "progression": "I_V_vi_IV", "minor_progression": "Andalusian",
    },
}


@dataclass
class ProduceRequest:
    style: str = "default"
    key: str = "C"
    is_minor: bool = False
    tempo: Optional[float] = None
    progression: Optional[str] = None
    bars: int = 8
    drum_style: Optional[str] = None
    bass_style: Optional[str] = None
    include_lead: bool = False
    prompt: str = ""


def resolve_style(style: str) -> Dict[str, Any]:
    key = (style or "default").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "anthem": "party",
        "party_rock": "party",
        "edm": "house",
        "tech_house": "deep_house",
        "techno": "house",
        "rnb": "emotional",
        "r_and_b": "emotional",
        "ballad": "emotional",
        "indie": "pop",
        "folk": "emotional",
    }
    key = aliases.get(key, key)
    if key not in STYLE_PRESETS:
        # Fuzzy: first matching substring
        for name in STYLE_PRESETS:
            if name in key or key in name:
                key = name
                break
        else:
            key = "default"
    return dict(STYLE_PRESETS[key])


def _run(tool: str, args: dict, ctx, emit: Optional[EventFn],
         steps: list) -> dict:
    from ..tools.core import registry
    if emit:
        emit("tool_call", {"name": tool, "input": args})
    result = registry.execute(tool, args, ctx)
    if emit:
        emit("tool_result", {"name": tool, "result": result})
    steps.append({"tool": tool, "args": args, "result": result})
    return result


def _ok(result: dict) -> bool:
    return isinstance(result, dict) and "error" not in result


def _lead_notes(key: str, is_minor: bool, bars: int) -> List[dict]:
    """Simple scale-based lead motif (deterministic, Stem-owned)."""
    roots = {
        "C": 60, "C#": 61, "Db": 61, "D": 62, "D#": 63, "Eb": 63,
        "E": 64, "F": 65, "F#": 66, "Gb": 66, "G": 67,
        "G#": 68, "Ab": 68, "A": 69, "A#": 70, "Bb": 70, "B": 71,
    }
    root = roots.get(key, 60)
    scale = [0, 3, 5, 7, 10, 12] if is_minor else [0, 2, 4, 7, 9, 12]
    phrase = [
        (0, 1.0), (2, 1.0), (3, 1.0), (4, 1.0),
        (3, 1.0), (2, 1.0), (1, 1.0), (0, 1.0),
    ]
    notes = []
    for bar_pair in range(max(1, bars // 2)):
        base = bar_pair * 8.0
        for offset, (degree, length) in enumerate(phrase):
            notes.append({
                "pitch": root + scale[degree % len(scale)],
                "start_beat": base + float(offset),
                "length_beats": length,
                "velocity": 98 if offset in (0, 3) else 84,
            })
    return notes


def produce_instrumental(req: ProduceRequest, ctx,
                         emit: Optional[EventFn] = None) -> dict:
    """Build chords + bass + drums (optional lead) via Stem theory tools."""
    emit = emit or (lambda kind, payload: None)
    steps: list = []
    preset = resolve_style(req.style)
    if req.prompt:
        # Soft style inference from free text when style is default
        low = req.prompt.lower()
        if req.style in ("", "default"):
            for name in ("deep_house", "house", "trap", "hip_hop", "rock",
                         "party", "emotional", "pop"):
                if name.replace("_", " ") in low or name in low:
                    preset = resolve_style(name)
                    break
            if "anthem" in low or "party rock" in low:
                preset = resolve_style("party")

    is_minor = req.is_minor or bool(preset.get("prefer_minor"))
    tempo = float(req.tempo if req.tempo is not None else preset["tempo"])
    tempo = max(40.0, min(220.0, tempo))
    progression = req.progression or (
        preset["minor_progression"] if is_minor else preset["progression"])
    if progression not in ChordProgression.COMMON_PROGRESSIONS:
        progression = "Andalusian" if is_minor else "I_V_vi_IV"
    bars = max(4, min(64, int(req.bars)))
    drum_style = req.drum_style or preset["drums"]
    bass_style = req.bass_style or preset["bass"]
    cycles = max(1, (bars + 3) // 4)

    r = _run("set_tempo", {"bpm": tempo}, ctx, emit, steps)
    if not _ok(r):
        return {"error": r.get("error", "set_tempo failed"), "steps": steps}

    tracks: Dict[str, str] = {}

    r = _run("create_midi_track", {
        "name": "Stem Chords", "instrument_id": "gm:0", "preset": None,
    }, ctx, emit, steps)
    if not _ok(r):
        return {"error": r.get("error"), "steps": steps}
    tracks["chords"] = r["track_id"]

    for cycle in range(cycles):
        r = _run("insert_chord_progression", {
            "track_id": tracks["chords"],
            "key": req.key,
            "progression": progression,
            "octave": 3 if is_minor else 4,
            "beats_per_chord": 4.0,
            "start_beat": cycle * 16.0,
            "velocity": 86,
        }, ctx, emit, steps)
        if not _ok(r):
            return {"error": r.get("error"), "steps": steps}

    r = _run("create_midi_track", {
        "name": "Stem Bass", "instrument_id": "gm:33", "preset": None,
    }, ctx, emit, steps)
    if not _ok(r):
        return {"error": r.get("error"), "steps": steps}
    tracks["bass"] = r["track_id"]

    for cycle in range(cycles):
        r = _run("insert_bassline", {
            "track_id": tracks["bass"],
            "key": req.key,
            "progression": progression,
            "style": bass_style,
            "octave": 2,
            "beats_per_chord": 4.0,
            "start_beat": cycle * 16.0,
        }, ctx, emit, steps)
        if not _ok(r):
            return {"error": r.get("error"), "steps": steps}

    r = _run("create_midi_track", {
        "name": "Stem Drums", "instrument_id": "gm:drums", "preset": None,
    }, ctx, emit, steps)
    if not _ok(r):
        return {"error": r.get("error"), "steps": steps}
    tracks["drums"] = r["track_id"]

    r = _run("insert_drum_pattern", {
        "track_id": tracks["drums"],
        "style": drum_style,
        "bars": bars,
        "start_beat": 0.0,
    }, ctx, emit, steps)
    if not _ok(r):
        return {"error": r.get("error"), "steps": steps}

    if req.include_lead:
        r = _run("create_midi_track", {
            "name": "Stem Lead", "instrument_id": "gm:80", "preset": None,
        }, ctx, emit, steps)
        if not _ok(r):
            return {"error": r.get("error"), "steps": steps}
        tracks["lead"] = r["track_id"]
        r = _run("insert_midi_notes", {
            "track_id": tracks["lead"],
            "notes": _lead_notes(req.key, is_minor, bars),
            "start_beat": 0.0,
        }, ctx, emit, steps)
        if not _ok(r):
            return {"error": r.get("error"), "steps": steps}

    beat_sec = 60.0 / tempo
    for name, beat in (("intro", 0.0), ("verse", 16.0), ("chorus", 32.0)):
        if beat / 4.0 >= bars:
            continue
        _run("add_marker", {
            "name": name, "position_seconds": beat * beat_sec,
        }, ctx, emit, steps)

    return {
        "ok": True,
        "producer": "stem",
        "key": req.key,
        "is_minor": is_minor,
        "tempo": tempo,
        "progression": progression,
        "bars": bars,
        "drum_style": drum_style,
        "bass_style": bass_style,
        "tracks": tracks,
        "steps_used": len(steps),
        "note": "Stem produced this instrumental. Overlay vocals only after "
                "you are happy with the bed — do not replace it with an "
                "external full-song API.",
        "next": "When ready: analyze_instrumental → overlay_vocals (or "
                "generate_vocals).",
    }


def analyze_instrumental(ctx) -> dict:
    """Summarize the current Stem session for vocal overlay planning."""
    from ..tools.core import registry
    overview = registry.execute("get_session_overview", {}, ctx)
    if not isinstance(overview, dict) or "error" in overview:
        return overview if isinstance(overview, dict) else {"error": "overview failed"}

    tracks = overview.get("tracks") or []
    midi = [t for t in tracks if t.get("kind") == "midi"]
    audio = [t for t in tracks if t.get("kind") == "audio"]
    note_counts = {}
    for t in midi:
        notes = registry.execute("get_midi_notes", {"track_id": t["track_id"]}, ctx)
        note_counts[t["name"]] = len((notes or {}).get("notes") or [])

    ready = sum(note_counts.values()) > 0 or len(audio) > 0
    return {
        "ok": True,
        "ready_for_vocals": ready,
        "tempo": overview.get("tempo"),
        "meter": overview.get("meter"),
        "markers": overview.get("markers") or [],
        "midi_tracks": [
            {"name": t["name"], "track_id": t["track_id"],
             "notes": note_counts.get(t["name"], 0)}
            for t in midi
        ],
        "audio_tracks": [
            {"name": t["name"], "track_id": t["track_id"]} for t in audio
        ],
        "guidance": (
            "Instrumental looks ready — call overlay_vocals with lyrics/style."
            if ready else
            "No material yet — call produce_instrumental (or StemScript) first."
        ),
    }
