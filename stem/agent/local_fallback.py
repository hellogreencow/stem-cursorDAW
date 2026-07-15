"""Deterministic fallback intents for core music-generation flows.

This is deliberately narrow. It keeps the app useful when the configured LLM
provider is unavailable, but still routes every action through the normal tool
registry and bridge so imports, undo ids, and activity reporting stay identical
to LLM-driven runs.
"""
import re
from typing import Callable, Optional

from ..language import execute_stemscript, looks_like_stemscript, StemScriptError
from ..tools import generation_tools  # noqa: F401 - registers generation tools
from ..tools.core import registry


EventFn = Callable[[str, dict], None]


def _duration(message: str, default: float) -> float:
    m = re.search(
        r"\b(\d+(?:\.\d+)?)\s*(seconds?|secs?|sec|s|minutes?|mins?|min)\b",
        message, re.I)
    if not m:
        return default
    value = float(m.group(1))
    unit = m.group(2).lower()
    if unit.startswith("min"):
        value *= 60.0
    return max(3.0, min(value, 120.0))


def _lyrics(message: str) -> Optional[str]:
    m = re.search(r"\blyrics?\s*:\s*(.+)$", message, re.I | re.S)
    if m:
        return m.group(1).strip()
    m = re.search(r"\bsing\s+[\"']([^\"']+)[\"']", message, re.I | re.S)
    if m:
        return m.group(1).strip()
    return None


def _tempo(message: str, default: float = 90.0) -> float:
    m = re.search(r"\b(?:at|tempo|bpm)\s*(\d{2,3})(?:\s*bpm)?\b",
                  message, re.I)
    if not m:
        m = re.search(r"\b(\d{2,3})\s*bpm\b", message, re.I)
    if not m:
        return default
    return max(40.0, min(float(m.group(1)), 220.0))


def _key(message: str) -> tuple:
    m = re.search(
        r"\b(?:in|key of)\s+([A-Ga-g](?:#|b|♯|♭)?)\s*(major|minor|maj|min)?\b",
        message, re.I)
    if not m:
        return ("A" if "minor" in message.lower() else "C",
                "minor" in message.lower())
    root = m.group(1).replace("♯", "#").replace("♭", "b")
    mode = (m.group(2) or "").lower()
    return (root[0].upper() + root[1:], mode.startswith("min"))


def _has_explicit_key(message: str) -> bool:
    return bool(re.search(
        r"\b(?:in|key of)\s+[A-Ga-g](?:#|b|♯|♭)?\s*(?:major|minor|maj|min)?\b",
        message, re.I))


def _drum_style(message: str) -> str:
    low = message.lower()
    for style in ("trap", "house", "hip_hop", "breakbeat", "dnb",
                  "four_on_floor"):
        if style.replace("_", " ") in low or style in low:
            return style
    if "dance" in low or "club" in low:
        return "four_on_floor"
    if "rap" in low or "hip hop" in low:
        return "hip_hop"
    return "four_on_floor"


def _execute(tool: str, args: dict, ctx, emit: EventFn) -> dict:
    emit("tool_call", {"name": tool, "input": args})
    result = registry.execute(tool, args, ctx)
    emit("tool_result", {"name": tool, "result": result})
    return result


def _run(tool: str, args: dict, ctx, emit: EventFn) -> dict:
    tool_def = registry._tools.get(tool)
    if (tool_def and tool_def.mutates
            and not getattr(ctx, "_stem_local_overview_read", False)):
        setattr(ctx, "_stem_local_overview_read", True)
        overview = _execute("get_session_overview", {}, ctx, emit)
        if not _ok(overview):
            return overview
    return _execute(tool, args, ctx, emit)


def _ok(result: dict) -> bool:
    return "error" not in result


def _file(result: dict) -> str:
    return result.get("file") or result.get("full_mix") or "the generated file"


def _vocal_lines(lyrics: str, duration: float) -> list:
    lines = [line.strip() for line in lyrics.splitlines() if line.strip()]
    if not lines:
        lines = [lyrics.strip()]
    per_line = max(3.0, duration / max(1, len(lines)))
    return [{
        "lyrics": line,
        "duration_seconds": per_line,
        "section": "Hook" if len(lines) == 1 else f"Line {i + 1}",
        "styles": [],
    } for i, line in enumerate(lines)]


def _lead_motif(key: str, is_minor: bool, bars: int = 4) -> list:
    roots = {
        "C": 60, "C#": 61, "Db": 61, "D": 62, "D#": 63, "Eb": 63,
        "E": 64, "F": 65, "F#": 66, "Gb": 66, "G": 67,
        "G#": 68, "Ab": 68, "A": 69, "A#": 70, "Bb": 70, "B": 71,
    }
    root = roots.get(key, 62)
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
                "pitch": root + scale[degree],
                "start_beat": base + float(offset),
                "length_beats": length,
                "velocity": 98 if offset in (0, 3) else 84,
            })
    return notes


def _tech_house_bass_notes(key: str, bars: int) -> list:
    roots = {
        "C": 36, "C#": 37, "Db": 37, "D": 38, "D#": 39, "Eb": 39,
        "E": 40, "F": 41, "F#": 42, "Gb": 42, "G": 43,
        "G#": 44, "Ab": 44, "A": 45, "A#": 46, "Bb": 46, "B": 47,
    }
    root = roots.get(key, 45)
    pattern = [
        (0.00, root, 0.45, 112),
        (0.75, root + 12, 0.20, 88),
        (1.50, root + 3, 0.35, 98),
        (2.00, root, 0.45, 108),
        (2.75, root + 10, 0.20, 86),
        (3.50, root + 7, 0.35, 102),
    ]
    notes = []
    for bar in range(bars):
        base = bar * 4.0
        for offset, pitch, length, velocity in pattern:
            notes.append({
                "pitch": pitch,
                "start_beat": base + offset,
                "length_beats": length,
                "velocity": velocity,
            })
    return notes


def _tech_house_chord_stabs(key: str, is_minor: bool, bars: int) -> list:
    roots = {
        "C": 60, "C#": 61, "Db": 61, "D": 62, "D#": 63, "Eb": 63,
        "E": 64, "F": 65, "F#": 66, "Gb": 66, "G": 67,
        "G#": 68, "Ab": 68, "A": 69, "A#": 70, "Bb": 70, "B": 71,
    }
    root = roots.get(key, 69)
    chord = [0, 3, 7, 10] if is_minor else [0, 4, 7, 11]
    notes = []
    for bar in range(0, bars, 2):
        for beat in (1.5, 3.0, 5.5, 7.0):
            start = bar * 4.0 + beat
            if start >= bars * 4.0:
                continue
            for interval in chord:
                notes.append({
                    "pitch": root + interval,
                    "start_beat": start,
                    "length_beats": 0.35,
                    "velocity": 74,
                })
    return notes


def _tech_house_perc_notes(bars: int) -> list:
    notes = []
    for bar in range(bars):
        base = bar * 4.0
        for step in range(8):
            notes.append({
                "pitch": 42,
                "start_beat": base + step * 0.5,
                "length_beats": 0.12,
                "velocity": 62 if step % 2 else 82,
            })
        for beat in (1.75, 3.75):
            notes.append({
                "pitch": 46,
                "start_beat": base + beat,
                "length_beats": 0.18,
                "velocity": 88,
            })
    return notes


def _build_deep_house_track(raw: str, ctx, emit: EventFn) -> Optional[str]:
    key, is_minor = _key(raw)
    if not _has_explicit_key(raw):
        key, is_minor = "A", True
    tempo = _tempo(raw, default=126.0)
    bars = 32 if "full" in raw.lower() or "length" in raw.lower() else 16

    result = _run("set_tempo", {"bpm": tempo}, ctx, emit)
    if not _ok(result):
        return result["error"]

    result = _run("create_midi_track", {
        "name": "Stem Deep Bass",
        "instrument_id": "gm:38",
        "preset": None,
    }, ctx, emit)
    if not _ok(result):
        return result["error"]
    bass_track = result["track_id"]

    result = _run("insert_midi_notes", {
        "track_id": bass_track,
        "notes": _tech_house_bass_notes(key, bars),
        "start_beat": 0.0,
    }, ctx, emit)
    if not _ok(result):
        return result["error"]

    result = _run("create_midi_track", {
        "name": "Stem House Drums",
        "instrument_id": "gm:drums",
        "preset": None,
    }, ctx, emit)
    if not _ok(result):
        return result["error"]
    drums_track = result["track_id"]

    result = _run("insert_drum_pattern", {
        "track_id": drums_track,
        "style": "house",
        "bars": bars,
        "start_beat": 0.0,
    }, ctx, emit)
    if not _ok(result):
        return result["error"]

    result = _run("create_midi_track", {
        "name": "Stem Chord Stabs",
        "instrument_id": "gm:4",
        "preset": None,
    }, ctx, emit)
    if not _ok(result):
        return result["error"]
    chords_track = result["track_id"]

    result = _run("insert_midi_notes", {
        "track_id": chords_track,
        "notes": _tech_house_chord_stabs(key, is_minor, bars),
        "start_beat": 0.0,
    }, ctx, emit)
    if not _ok(result):
        return result["error"]

    result = _run("create_midi_track", {
        "name": "Stem Hats & Perc",
        "instrument_id": "gm:drums",
        "preset": None,
    }, ctx, emit)
    if not _ok(result):
        return result["error"]
    perc_track = result["track_id"]

    result = _run("insert_midi_notes", {
        "track_id": perc_track,
        "notes": _tech_house_perc_notes(bars),
        "start_beat": 0.0,
    }, ctx, emit)
    if not _ok(result):
        return result["error"]

    return (f"Built a {bars}-bar deep/tech-house track in {key} "
            f"{'minor' if is_minor else 'major'} at {tempo:g} BPM with a "
            "dense bass hook, house drums, chord stabs, and hat/percussion "
            "layers. Each step can be undone.")


def _build_backing_music(raw: str, ctx, emit: EventFn) -> Optional[str]:
    low = raw.lower()
    gaelic_chant = any(w in low for w in (
        "gaelic", "celtic", "war chant", "battle chant", "chant"))
    key, is_minor = _key(raw)
    if gaelic_chant and not _has_explicit_key(raw):
        key, is_minor = "D", True
    progression = "Andalusian" if is_minor else "I_V_vi_IV"
    tempo = _tempo(raw, default=88.0 if gaelic_chant else 90.0)
    drum_style = "breakbeat" if gaelic_chant else _drum_style(raw)
    lead_name = "Stem Chant Lead" if gaelic_chant else "Stem Lead"
    chord_name = "Stem Drone Chords" if gaelic_chant else "Stem Chords"

    result = _run("set_tempo", {"bpm": tempo}, ctx, emit)
    if not _ok(result):
        return result["error"]

    result = _run("create_midi_track", {
        "name": chord_name,
        "instrument_id": "gm:48",
        "preset": None,
    }, ctx, emit)
    if not _ok(result):
        return result["error"]
    chords_track = result["track_id"]

    duration = _duration(raw, 60.0 if "minute" in low else 20.0)
    bars = max(4, min(64, int((duration * tempo / 60.0 + 3.99) // 4)))
    cycles = max(1, int((bars + 3) // 4))
    for cycle in range(cycles):
        result = _run("insert_chord_progression", {
            "track_id": chords_track,
            "key": key,
            "progression": progression,
            "octave": 3,
            "beats_per_chord": 4.0,
            "start_beat": cycle * 16.0,
            "velocity": 78,
        }, ctx, emit)
        if not _ok(result):
            return result["error"]

    result = _run("create_midi_track", {
        "name": "Stem War Bass" if gaelic_chant else "Stem Bass",
        "instrument_id": "gm:33",
        "preset": None,
    }, ctx, emit)
    if not _ok(result):
        return result["error"]
    bass_track = result["track_id"]

    for cycle in range(cycles):
        result = _run("insert_bassline", {
            "track_id": bass_track,
            "key": key,
            "progression": progression,
            "style": "driving" if gaelic_chant else "pulse",
            "octave": 2,
            "beats_per_chord": 4.0,
            "start_beat": cycle * 16.0,
        }, ctx, emit)
        if not _ok(result):
            return result["error"]

    result = _run("create_midi_track", {
        "name": "Stem War Drums" if gaelic_chant else "Stem Drums",
        "instrument_id": "gm:drums",
        "preset": None,
    }, ctx, emit)
    if not _ok(result):
        return result["error"]
    drums_track = result["track_id"]

    result = _run("insert_drum_pattern", {
        "track_id": drums_track,
        "style": drum_style,
        "bars": bars,
        "start_beat": 0.0,
    }, ctx, emit)
    if not _ok(result):
        return result["error"]

    result = _run("create_midi_track", {
        "name": lead_name,
        "instrument_id": "gm:109" if gaelic_chant else "gm:80",
        "preset": None,
    }, ctx, emit)
    if not _ok(result):
        return result["error"]
    lead_track = result["track_id"]

    result = _run("insert_midi_notes", {
        "track_id": lead_track,
        "notes": _lead_motif(key, is_minor, bars=bars),
        "start_beat": 0.0,
    }, ctx, emit)
    if not _ok(result):
        return result["error"]

    style = "Gaelic war-chant" if gaelic_chant else "instrumental"
    return (f"Built a {duration:g} second {style} backing in {key} "
            f"{'minor' if is_minor else 'major'} at {tempo:g} BPM with "
            "drone chords, bass, drums, and a lead motif behind the existing "
            "vocals. Each step can be undone.")


def _build_chiptune_platformer(raw: str, ctx, emit: EventFn) -> Optional[str]:
    key, is_minor = _key(raw)
    if not _has_explicit_key(raw):
        key, is_minor = "C", False
    tempo = _tempo(raw, default=160.0)
    duration = _duration(raw, 16.0)
    bars = max(4, min(16, int((duration * tempo / 60.0 + 3.99) // 4)))
    cycles = max(1, int((bars + 3) // 4))
    progression = "I_V_vi_IV" if not is_minor else "i_VII_VI_V"

    result = _run("set_tempo", {"bpm": tempo}, ctx, emit)
    if not _ok(result):
        return result["error"]

    result = _run("create_midi_track", {
        "name": "Stem Retro Lead",
        "instrument_id": "gm:80",
        "preset": None,
    }, ctx, emit)
    if not _ok(result):
        return result["error"]
    lead_track = result["track_id"]

    result = _run("insert_midi_notes", {
        "track_id": lead_track,
        "notes": _lead_motif(key, is_minor, bars=bars),
        "start_beat": 0.0,
    }, ctx, emit)
    if not _ok(result):
        return result["error"]

    result = _run("create_midi_track", {
        "name": "Stem Arcade Chords",
        "instrument_id": "gm:12",
        "preset": None,
    }, ctx, emit)
    if not _ok(result):
        return result["error"]
    chords_track = result["track_id"]

    for cycle in range(cycles):
        result = _run("insert_chord_progression", {
            "track_id": chords_track,
            "key": key,
            "progression": progression,
            "octave": 4,
            "beats_per_chord": 4.0,
            "start_beat": cycle * 16.0,
            "velocity": 92,
        }, ctx, emit)
        if not _ok(result):
            return result["error"]

    result = _run("create_midi_track", {
        "name": "Stem Bouncy Bass",
        "instrument_id": "gm:33",
        "preset": None,
    }, ctx, emit)
    if not _ok(result):
        return result["error"]
    bass_track = result["track_id"]

    for cycle in range(cycles):
        result = _run("insert_bassline", {
            "track_id": bass_track,
            "key": key,
            "progression": progression,
            "style": "bounce",
            "octave": 2,
            "beats_per_chord": 4.0,
            "start_beat": cycle * 16.0,
        }, ctx, emit)
        if not _ok(result):
            return result["error"]

    result = _run("create_midi_track", {
        "name": "Stem Arcade Drums",
        "instrument_id": "gm:drums",
        "preset": None,
    }, ctx, emit)
    if not _ok(result):
        return result["error"]
    drums_track = result["track_id"]

    result = _run("insert_drum_pattern", {
        "track_id": drums_track,
        "style": "four_on_floor",
        "bars": bars,
        "start_beat": 0.0,
    }, ctx, emit)
    if not _ok(result):
        return result["error"]

    return (f"Built an original retro platformer theme in {key} "
            f"{'minor' if is_minor else 'major'} at {tempo:g} BPM with "
            "arcade lead, bright chords, bouncy bass, and drums. "
            "Each step can be undone.")


def _add_vocals_and_extend(raw: str, ctx, emit: EventFn,
                           lyrics: Optional[str]) -> Optional[str]:
    tempo = _tempo(raw, default=90.0 if "slower" in raw.lower() else 100.0)
    duration = _duration(raw, 60.0)
    backing_reply = _build_backing_music(
        f"{raw}, {duration:g} seconds, at {tempo:g} bpm", ctx, emit)
    if backing_reply and (
            "error" in backing_reply.lower()
            or "not configured" in backing_reply.lower()):
        return backing_reply

    result = _run("generate_vocals", {
        "prompt": raw,
        "lyrics": lyrics,
        "length_seconds": duration,
        "position_seconds": 0.0,
    }, ctx, emit)
    if not _ok(result):
        return result["error"]
    return (f"Set the song up as a {duration:g} second slower arrangement at "
            f"{tempo:g} BPM and added isolated vocals that can sit over the "
            f"instruments. Vocal: {_file(result)}. Each step can be undone.")


def handle_local_intent(message: str, ctx, emit: Optional[EventFn] = None):
    """Handle recognized direct production commands.

    Returns a final reply string when handled, or None when the request should
    go to the LLM agent.
    """
    emit = emit or (lambda kind, payload: None)
    raw = message.strip()
    low = raw.lower()

    if not raw:
        return None

    setattr(ctx, "_stem_local_overview_read", False)

    if looks_like_stemscript(raw):
        try:
            return execute_stemscript(raw, ctx, emit)
        except StemScriptError as e:
            return f"StemScript error: {e}"

    tempo_only = (
        ("tempo" in low or re.search(r"\b\d{2,3}\s*bpm\b", low))
        and not any(w in low for w in (
            "generate", "make", "create", "song", "vocal", "voice", "sing",
            "instrument", "sample", "loop", "beat", "chord", "bass", "drum",
        ))
    )
    if tempo_only:
        tempo = _tempo(raw, default=120.0)
        result = _run("set_tempo", {"bpm": tempo}, ctx, emit)
        if not _ok(result):
            return result["error"]
        return f"Tempo set to {tempo:g} BPM. This can be undone."

    if ("generation" in low or "generator" in low or "backend" in low) and (
            "status" in low or "available" in low or "check" in low):
        result = _run("get_generation_status", {}, ctx, emit)
        if not _ok(result):
            return result["error"]
        available = [k for k, v in result.items() if v is True]
        missing = [k for k, v in result.items() if v is False]
        return ("Generation status: available: "
                f"{', '.join(available) or 'none'}; unavailable: "
                f"{', '.join(missing) or 'none'}.")

    wants_vocal = any(w in low for w in (
        "vocal", "vocals", "voice", "voices", "sing", "acapella", "a cappella"))
    isolated = any(w in low for w in (
        "isolated", "vocal only", "vocals only", "voice only", "voices only",
        "acapella", "a cappella", "without song", "without music",
        "no backing", "no instrumental", "no instruments"))
    wants_song = any(w in low for w in (
        "song", "hook", "chorus", "verse", "anthem", "party rock"))
    wants_instrumental = any(w in low for w in (
        "instrumental", "sample", "loop", "beat", "backing", "no vocals"))
    explicit_stems = any(w in low for w in (
        "separate", "separated", "stems", "split", "vocal and backing",
        "vocals and backing", "voice and backing"))
    vocal_for_existing_backing = wants_vocal and any(w in low for w in (
        "apply", "on top of", "over the", "over this", "over my",
        "to the beat", "to this beat", "to my beat", "to the instruments",
        "to these instruments", "with the instruments", "existing beat",
        "existing instruments", "current session", "this track",
        "these tracks"))
    wants_arrangement = any(w in low for w in (
        "arrangement", "backing track", "instrument tracks", "instruments",
        "chords", "bassline", "bass line", "drums", "beat"))
    wants_background_music = (
        not explicit_stems
        and any(w in low for w in (
            "background", "backing", "accompaniment", "behind", "under",
            "underneath", "music bed", "bed"))
        and any(w in low for w in (
            "music", "tune", "chant", "instrumental", "track", "score",
            "vocals", "voice", "voices"))
    )
    wants_song_improvement = any(w in low for w in (
        "make the entire song better", "make this song better",
        "make it better", "improve the song", "finish the song",
        "extend the song", "minute long", "full minute"))
    wants_fitting_vocals = (
        wants_vocal
        and (wants_song_improvement or "slower" in low or "slow down" in low)
        and any(w in low for w in (
            "add", "fit", "fits", "that fit", "for this", "for the song",
            "for it"))
    )
    wants_retro_platformer = any(w in low for w in (
        "mario", "super mario", "platformer", "8-bit", "8 bit", "chiptune",
        "nes"))
    wants_deep_house_track = (
        "bass" in low
        and any(w in low for w in (
            "chris stussy", "stussy", "deep house", "tech house", "house",
            "full length", "full track"))
    )

    duration = _duration(raw, 20.0)
    lyrics = _lyrics(raw)

    if wants_deep_house_track and not wants_vocal:
        return _build_deep_house_track(raw, ctx, emit)

    if wants_retro_platformer and not wants_vocal:
        return _build_chiptune_platformer(raw, ctx, emit)

    if wants_fitting_vocals:
        return _add_vocals_and_extend(raw, ctx, emit, lyrics)

    if wants_background_music or wants_song_improvement:
        return _build_backing_music(raw, ctx, emit)

    if wants_arrangement and wants_vocal and not vocal_for_existing_backing:
        key, is_minor = _key(raw)
        progression = "i_VII_VI_V" if is_minor else "I_V_vi_IV"
        tempo = _tempo(raw)
        drum_style = _drum_style(raw)

        result = _run("set_tempo", {"bpm": tempo}, ctx, emit)
        if not _ok(result):
            return result["error"]

        result = _run("create_midi_track", {
            "name": "Stem Chords",
            "instrument_id": "gm:0",
            "preset": None,
        }, ctx, emit)
        if not _ok(result):
            return result["error"]
        chords_track = result["track_id"]

        result = _run("insert_chord_progression", {
            "track_id": chords_track,
            "key": key,
            "progression": progression,
            "octave": 4,
            "beats_per_chord": 4.0,
            "start_beat": 0.0,
            "velocity": 86,
        }, ctx, emit)
        if not _ok(result):
            return result["error"]

        result = _run("create_midi_track", {
            "name": "Stem Bass",
            "instrument_id": "gm:33",
            "preset": None,
        }, ctx, emit)
        if not _ok(result):
            return result["error"]
        bass_track = result["track_id"]

        result = _run("insert_bassline", {
            "track_id": bass_track,
            "key": key,
            "progression": progression,
            "style": "pulse",
            "octave": 2,
            "beats_per_chord": 4.0,
            "start_beat": 0.0,
        }, ctx, emit)
        if not _ok(result):
            return result["error"]

        result = _run("create_midi_track", {
            "name": "Stem Drums",
            "instrument_id": "gm:drums",
            "preset": None,
        }, ctx, emit)
        if not _ok(result):
            return result["error"]
        drums_track = result["track_id"]

        result = _run("insert_drum_pattern", {
            "track_id": drums_track,
            "style": drum_style,
            "bars": 4,
            "start_beat": 0.0,
        }, ctx, emit)
        if not _ok(result):
            return result["error"]

        result = _run("generate_vocals", {
            "prompt": raw,
            "lyrics": lyrics,
            "length_seconds": duration,
            "position_seconds": 0.0,
        }, ctx, emit)
        if not _ok(result):
            return result["error"]

        return (f"Built a {key} {'minor' if is_minor else 'major'} backing "
                f"arrangement at {tempo:g} BPM with chords, bass, drums, and "
                f"an isolated vocal track. Vocal: {_file(result)}. "
                "Each step can be undone.")

    if wants_vocal and isolated:
        if lyrics and ("\n" in lyrics or "line by line" in low or "line-by-line" in low):
            args = {
                "lines": _vocal_lines(lyrics, duration),
                "style": [raw.split("lyrics", 1)[0].strip()[:160]],
                "vocals_only": True,
                "position_seconds": 0.0,
            }
            result = _run("generate_vocal_lines", args, ctx, emit)
            if not _ok(result):
                return result["error"]
            return (f"Generated isolated vocal lines and imported them as audio. "
                    f"File: {_file(result)}. This can be undone.")
        args = {
            "prompt": raw,
            "lyrics": lyrics,
            "length_seconds": duration,
            "position_seconds": 0.0,
        }
        result = _run("generate_vocals", args, ctx, emit)
        if not _ok(result):
            return result["error"]
        return (f"Generated isolated vocals and imported them as a new audio "
                f"track. File: {_file(result)}. This can be undone.")

    if wants_song and wants_vocal and explicit_stems:
        args = {
            "prompt": raw,
            "length_seconds": duration,
            "position_seconds": 0.0,
        }
        result = _run("generate_song_stems", args, ctx, emit)
        if not _ok(result):
            return result["error"]
        return ("Generated a song idea, split it into separate vocal and backing "
                "tracks, and imported both into the session. "
                f"Vocal: {result.get('vocal_file')}. "
                f"Backing: {result.get('backing_file')}. This can be undone.")

    if vocal_for_existing_backing:
        args = {
            "prompt": raw,
            "lyrics": lyrics,
            "length_seconds": duration,
            "position_seconds": 0.0,
        }
        result = _run("generate_vocals", args, ctx, emit)
        if not _ok(result):
            return result["error"]
        return ("Generated isolated vocals and imported them as a new audio "
                "track so they can sit over the existing instruments. "
                f"File: {_file(result)}. This can be undone.")

    full_song_request = any(w in low for w in (
        "full song", "make a song", "make me a song", "generate a song",
        "create a song", "anthem", "party rock"))
    if wants_song and (wants_vocal or full_song_request):
        args = {
            "prompt": raw,
            "length_seconds": _duration(raw, 60.0 if full_song_request else duration),
            "instrumental": "instrumental" in low or "no vocals" in low,
            "position_seconds": 0.0,
        }
        result = _run("generate_song", args, ctx, emit)
        if not _ok(result):
            return result["error"]
        mode = "instrumental song" if args["instrumental"] else "song with vocals"
        return (f"Generated a {mode} and imported it as a new audio track. "
                f"File: {_file(result)}. This can be undone.")

    if wants_instrumental and not wants_vocal:
        args = {
            "prompt": raw,
            "duration_seconds": _duration(raw, 8.0),
            "track_id": None,
            "position_seconds": 0.0,
        }
        result = _run("generate_sample", args, ctx, emit)
        if not _ok(result):
            return result["error"]
        return (f"Generated instrumental audio and imported it as a new track. "
                f"File: {_file(result)}. This can be undone.")

    return None
