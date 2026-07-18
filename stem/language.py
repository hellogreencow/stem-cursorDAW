"""StemScript: a compact music-production language for Ardour actions."""
import math
import re
import shlex
from dataclasses import dataclass, field
from typing import Callable, Optional

from .tools.core import registry
from .tools import generation_tools  # noqa: F401 - registers generation tools
from .tools import produce_tools  # noqa: F401 - registers produce/overlay


EventFn = Callable[[str, dict], None]

ROOTS = {
    "C": "C", "C#": "C#", "DB": "Db", "D": "D", "D#": "D#", "EB": "Eb",
    "E": "E", "F": "F", "F#": "F#", "GB": "Gb", "G": "G", "G#": "G#",
    "AB": "Ab", "A": "A", "A#": "A#", "BB": "Bb", "B": "B",
}

PROGRESSION_ALIASES = {
    "pop": "I_V_vi_IV",
    "anthem": "I_V_vi_IV",
    "rock": "I_IV_V",
    "party": "I_V_vi_IV",
    "emotional": "Emotional",
    "gaelic": "Andalusian",
    "celtic": "Andalusian",
    "war": "Andalusian",
    "andalusian": "Andalusian",
    "i_vii_vi_v": "i_VII_VI_V",
    "i-vii-vi-v": "i_VII_VI_V",
    "i_v_vi_iv": "I_V_vi_IV",
    "i-v-vi-iv": "I_V_vi_IV",
}

GM = {
    "chords": "gm:0",
    "piano": "gm:0",
    "bass": "gm:33",
    "drums": "gm:drums",
    "lead": "gm:80",
    "chant": "gm:109",
    "strings": "gm:48",
}


@dataclass
class StemScriptState:
    tempo: float = 120.0
    key: str = "C"
    minor: bool = False
    duration_seconds: float = 32.0
    progression: str = "I_V_vi_IV"
    chord_symbols: list[str] = field(default_factory=list)
    drum_style: str = "four_on_floor"
    bass_style: str = "pulse"
    lead_style: Optional[str] = None
    vocal: Optional[str] = None
    song_prompt: Optional[str] = None
    instrumental: bool = False

    @property
    def bars(self) -> int:
        beats = self.duration_seconds * self.tempo / 60.0
        return max(1, min(64, int(math.ceil(beats / 4.0))))


class StemScriptError(ValueError):
    pass


def looks_like_stemscript(text: str) -> bool:
    lines = _content_lines(text)
    if not lines:
        return False
    if lines[0].lower() in ("stem", "stem:", "stemscript", "stemscript:"):
        return True
    commands = {
        "tempo", "bpm", "key", "duration", "length", "chords", "progression",
        "drums", "bass", "lead", "melody", "vocal", "vocals", "song",
    }
    return sum(1 for line in lines if _head(line) in commands) >= 2


def execute_stemscript(script: str, ctx, emit: Optional[EventFn] = None) -> str:
    emit = emit or (lambda kind, payload: None)
    state = parse_stemscript(script)

    _run("set_tempo", {"bpm": state.tempo}, ctx, emit)
    # `song:` is a style/intent hint only — Stem always builds MIDI.
    # External full-song generation is never invoked from StemScript.
    # Vocals only when an explicit `vocal:` line is present (and not instrumental).

    chords_track = _track("StemScript Chords", "chords", ctx, emit)
    if state.chord_symbols:
        _insert_chord_symbols(chords_track, state, ctx, emit)
    else:
        _repeat_progression(chords_track, state, ctx, emit)

    bass_track = _track("StemScript Bass", "bass", ctx, emit)
    _repeat_bassline(bass_track, state, ctx, emit)

    drums_track = _track("StemScript Drums", "drums", ctx, emit)
    _run("insert_drum_pattern", {
        "track_id": drums_track,
        "style": state.drum_style,
        "bars": state.bars,
        "start_beat": 0.0,
    }, ctx, emit)

    if state.lead_style:
        lead_track = _track("StemScript Lead", state.lead_style, ctx, emit)
        _run("insert_midi_notes", {
            "track_id": lead_track,
            "notes": _lead_notes(state),
            "start_beat": 0.0,
        }, ctx, emit)

    if state.vocal and not state.instrumental:
        result = _run("overlay_vocals", {
            "prompt": f"{state.vocal}, {state.key} "
                      f"{'minor' if state.minor else 'major'} vocal",
            "lyrics": state.vocal,
            "length_seconds": state.duration_seconds,
            "position_seconds": 0.0,
            "require_instrumental": True,
        }, ctx, emit)
        if "error" in result:
            return result["error"]

    song_note = ""
    if state.song_prompt:
        song_note = f" Style intent from song: {state.song_prompt!r}."
    return (f"Ran StemScript: {state.bars} bars in {state.key} "
            f"{'minor' if state.minor else 'major'} at {state.tempo:g} BPM."
            f"{song_note} Stem produced the instrumental"
            f"{'; vocals overlaid' if state.vocal and not state.instrumental else ''}.")


def parse_stemscript(script: str) -> StemScriptState:
    state = StemScriptState()
    for line in _content_lines(script):
        if line.lower() in ("stem", "stem:", "stemscript", "stemscript:"):
            continue
        parts = shlex.split(line)
        if not parts:
            continue
        cmd = parts[0].lower()
        args = parts[1:]
        if cmd in ("tempo", "bpm"):
            state.tempo = _number(args, line, 40.0, 220.0)
        elif cmd == "key":
            if not args:
                raise StemScriptError("key requires a root note")
            root = _root(args[0])
            state.key = root
            state.minor = any(a.lower().startswith("min") for a in args[1:])
            if state.minor and state.progression == "I_V_vi_IV":
                state.progression = "Andalusian"
        elif cmd in ("duration", "length"):
            state.duration_seconds = _duration(" ".join(args), 32.0)
        elif cmd in ("chords", "progression"):
            value = " ".join(args).strip()
            if not value:
                raise StemScriptError("chords requires symbols or a progression")
            tokens = value.split()
            if len(tokens) > 1 and all(_is_chord_symbol(t) for t in tokens):
                state.chord_symbols = tokens
            else:
                state.progression = _progression(value)
                state.chord_symbols = []
        elif cmd == "drums":
            state.drum_style = _drum_style(args)
        elif cmd == "bass":
            state.bass_style = args[0].lower() if args else "pulse"
        elif cmd in ("lead", "melody"):
            state.lead_style = args[0].lower() if args else "lead"
        elif cmd in ("vocal", "vocals"):
            state.vocal = " ".join(args).strip()
        elif cmd == "song":
            state.song_prompt = " ".join(args).strip()
            state.instrumental = any(a.lower() in ("instrumental", "no-vocals",
                                                    "no_vocals")
                                     for a in args)
        else:
            raise StemScriptError(f"unknown StemScript command: {parts[0]}")
    return state


def _content_lines(script: str) -> list[str]:
    out = []
    for raw in script.splitlines():
        for part in raw.split(";"):
            line = part.split("#", 1)[0].strip()
            if line:
                out.append(line)
    return out


def _head(line: str) -> str:
    return line.split(None, 1)[0].rstrip(":").lower()


def _number(args: list[str], line: str, lo: float, hi: float) -> float:
    if not args:
        raise StemScriptError(f"{line!r} needs a number")
    try:
        value = float(args[0])
    except ValueError as exc:
        raise StemScriptError(f"{args[0]!r} is not a number") from exc
    return max(lo, min(hi, value))


def _duration(value: str, default: float) -> float:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(s|sec|secs|second|seconds|min|mins|"
                  r"minute|minutes)?", value, re.I)
    if not m:
        return default
    seconds = float(m.group(1))
    unit = (m.group(2) or "s").lower()
    if unit.startswith("min"):
        seconds *= 60.0
    return max(3.0, min(120.0, seconds))


def _root(value: str) -> str:
    key = value.upper().replace("♯", "#").replace("♭", "B")
    if key not in ROOTS:
        raise StemScriptError(f"unknown key root: {value}")
    return ROOTS[key]


def _progression(value: str) -> str:
    normalized = value.strip().replace(" ", "_")
    alias = PROGRESSION_ALIASES.get(normalized.lower(), normalized)
    return alias


def _is_chord_symbol(value: str) -> bool:
    return bool(re.match(r"^[A-Ga-g](?:#|b|♯|♭)?(?:m|min|maj)?[0-9]?$", value))


def _chord_parts(symbol: str) -> tuple[str, str]:
    m = re.match(r"^([A-Ga-g](?:#|b|♯|♭)?)(.*)$", symbol)
    if not m:
        raise StemScriptError(f"bad chord symbol: {symbol}")
    suffix = m.group(2).lower()
    if suffix.startswith("m") and not suffix.startswith("maj"):
        chord_type = "minor"
    else:
        chord_type = "major"
    return _root(m.group(1)), chord_type


def _drum_style(args: list[str]) -> str:
    value = " ".join(args).lower().replace("-", "_")
    if "trap" in value:
        return "trap"
    if "hip" in value:
        return "hip_hop"
    if "break" in value or "war" in value or "gaelic" in value:
        return "breakbeat"
    if "dnb" in value or "drum" in value and "bass" in value:
        return "dnb"
    if "house" in value:
        return "house"
    return "four_on_floor"


def _run(tool: str, args: dict, ctx, emit: EventFn) -> dict:
    emit("tool_call", {"name": tool, "input": args})
    result = registry.execute(tool, args, ctx)
    emit("tool_result", {"name": tool, "result": result})
    return result


def _track(name: str, role: str, ctx, emit: EventFn) -> str:
    result = _run("create_midi_track", {
        "name": name,
        "instrument_id": GM.get(role, GM["lead"]),
        "preset": None,
    }, ctx, emit)
    if "error" in result:
        raise StemScriptError(result["error"])
    return result["track_id"]


def _repeat_progression(track_id: str, state: StemScriptState, ctx,
                        emit: EventFn) -> None:
    cycle_beats = 16.0
    cycles = max(1, int(math.ceil(state.bars / 4.0)))
    for i in range(cycles):
        _run("insert_chord_progression", {
            "track_id": track_id,
            "key": state.key,
            "progression": state.progression,
            "octave": 3 if state.minor else 4,
            "beats_per_chord": 4.0,
            "start_beat": i * cycle_beats,
            "velocity": 84,
        }, ctx, emit)


def _repeat_bassline(track_id: str, state: StemScriptState, ctx,
                     emit: EventFn) -> None:
    cycle_beats = 16.0
    cycles = max(1, int(math.ceil(state.bars / 4.0)))
    for i in range(cycles):
        _run("insert_bassline", {
            "track_id": track_id,
            "key": state.key,
            "progression": state.progression,
            "style": state.bass_style,
            "octave": 2,
            "beats_per_chord": 4.0,
            "start_beat": i * cycle_beats,
        }, ctx, emit)


def _insert_chord_symbols(track_id: str, state: StemScriptState, ctx,
                          emit: EventFn) -> None:
    beat = 0.0
    symbols = state.chord_symbols
    repeats = max(1, int(math.ceil(state.bars / max(1, len(symbols)))))
    for _ in range(repeats):
        for symbol in symbols:
            root, chord_type = _chord_parts(symbol)
            _run("insert_chord", {
                "track_id": track_id,
                "root": root,
                "chord_type": chord_type,
                "octave": 3 if state.minor else 4,
                "start_beat": beat,
                "length_beats": 4.0,
                "velocity": 84,
            }, ctx, emit)
            beat += 4.0


def _lead_notes(state: StemScriptState) -> list[dict]:
    roots = {
        "C": 60, "C#": 61, "Db": 61, "D": 62, "D#": 63, "Eb": 63,
        "E": 64, "F": 65, "F#": 66, "Gb": 66, "G": 67,
        "G#": 68, "Ab": 68, "A": 69, "A#": 70, "Bb": 70, "B": 71,
    }
    root = roots.get(state.key, 60)
    scale = [0, 3, 5, 7, 10, 12] if state.minor else [0, 2, 4, 7, 9, 12]
    phrase = [0, 2, 3, 4, 3, 2, 1, 0]
    notes = []
    for bar_pair in range(max(1, state.bars // 2)):
        base = bar_pair * 8.0
        for offset, degree in enumerate(phrase):
            notes.append({
                "pitch": root + scale[degree],
                "start_beat": base + float(offset),
                "length_beats": 1.0,
                "velocity": 96 if offset in (0, 3) else 82,
            })
    return notes
