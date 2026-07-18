"""Autonomous multi-step task modes (M4.2).

Deterministic executors: plan → confirm → run through the tool registry with
a hard step cap and a verification checklist.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional


@dataclass
class TaskSpec:
    name: str
    summary: str
    destructive: bool = True
    max_steps: int = 20
    allowed_tools: List[str] = field(default_factory=list)


TASKS: Dict[str, TaskSpec] = {
    "arrange_loop_to_song": TaskSpec(
        name="arrange_loop_to_song",
        summary="Turn a short loop into a simple intro/verse/chorus layout "
                "with markers and repeated harmonic material.",
        max_steps=24,
        allowed_tools=[
            "get_session_overview", "set_tempo", "create_midi_track",
            "list_instruments", "insert_chord_progression", "insert_bassline",
            "insert_drum_pattern", "add_marker", "get_midi_notes",
        ],
    ),
    "rough_mix": TaskSpec(
        name="rough_mix",
        summary="Apply a conservative gain/mute pass across existing tracks "
                "so the session is audible and balanced enough to sketch.",
        max_steps=16,
        allowed_tools=[
            "get_session_overview", "set_track_gain", "set_track_mute",
            "make_tracks_audible",
        ],
    ),
}


def list_tasks() -> list:
    return [
        {
            "task": t.name,
            "summary": t.summary,
            "destructive": t.destructive,
            "max_steps": t.max_steps,
            "allowed_tools": list(t.allowed_tools),
        }
        for t in TASKS.values()
    ]


def preview_task(task_name: str, *, key: str = "C", tempo: float = 120.0,
                 is_minor: bool = False) -> dict:
    if task_name not in TASKS:
        return {"error": f"unknown task: {task_name}",
                "known": sorted(TASKS)}
    spec = TASKS[task_name]
    if task_name == "arrange_loop_to_song":
        plan = [
            f"Set tempo to {tempo:g} BPM if needed",
            f"Ensure chords/bass/drums tracks exist (key {key}"
            f"{' minor' if is_minor else ' major'})",
            "Place a 4-bar loop progression, then repeat for verse/chorus",
            "Add intro/verse/chorus markers",
            "Verify tracks, notes, and markers",
        ]
    else:
        plan = [
            "Read session tracks",
            "Unmute tracks; set role-based rough gains",
            "Optionally make silent MIDI instruments audible",
            "Verify no track left at absurd gain",
        ]
    return {
        "needs_confirm": True,
        "task": task_name,
        "summary": spec.summary,
        "destructive": spec.destructive,
        "plan": plan,
        "max_steps": spec.max_steps,
        "hint": "Re-call run_task with confirm=true to execute.",
        "defaults": {"key": key, "tempo": tempo, "is_minor": is_minor},
    }


def _exec(tool: str, args: dict, ctx, allowed: set, steps: list,
          emit: Optional[Callable] = None) -> dict:
    if tool not in allowed:
        return {"error": f"tool not allowed in this task: {tool}"}
    if emit:
        emit("tool_call", {"name": tool, "input": args})
    from ..tools.core import registry
    result = registry.execute(tool, args, ctx)
    if emit:
        emit("tool_result", {"name": tool, "result": result})
    steps.append({"tool": tool, "args": args, "result": result})
    return result


def _ok(result: dict) -> bool:
    return isinstance(result, dict) and "error" not in result


def run_arrange_loop_to_song(ctx, *, key: str = "C", tempo: float = 120.0,
                             is_minor: bool = False,
                             emit: Optional[Callable] = None) -> dict:
    spec = TASKS["arrange_loop_to_song"]
    allowed = set(spec.allowed_tools)
    steps: list = []
    checklist = {
        "tempo_set": False,
        "tracks_ready": False,
        "notes_present": False,
        "markers_present": False,
    }

    overview = _exec("get_session_overview", {}, ctx, allowed, steps, emit)
    if not _ok(overview):
        return {"ok": False, "error": overview.get("error"), "steps": steps}

    if abs(float(overview.get("tempo", 120)) - tempo) > 0.01:
        r = _exec("set_tempo", {"bpm": tempo}, ctx, allowed, steps, emit)
        if not _ok(r):
            return {"ok": False, "error": r.get("error"), "steps": steps,
                    "checklist": checklist}
    checklist["tempo_set"] = True

    tracks = {t["name"]: t["track_id"] for t in overview.get("tracks", [])}
    roles = [
        ("Stem Arrange Chords", "chords", "mock.piano"),
        ("Stem Arrange Bass", "bass", "mock.synth"),
        ("Stem Arrange Drums", "drums", "mock.synth"),
    ]
    for name, _role, inst in roles:
        if name not in tracks:
            created = _exec("create_midi_track", {
                "name": name, "instrument_id": inst,
            }, ctx, allowed, steps, emit)
            if not _ok(created):
                return {"ok": False, "error": created.get("error"),
                        "steps": steps, "checklist": checklist}
            tracks[name] = created["track_id"]
    checklist["tracks_ready"] = True

    progression = "Andalusian" if is_minor else "I_V_vi_IV"
    # Loop at 0, verse copy at 16, chorus copy at 32 (beats)
    for start in (0.0, 16.0, 32.0):
        r = _exec("insert_chord_progression", {
            "track_id": tracks["Stem Arrange Chords"],
            "key": key, "progression": progression,
            "start_beat": start, "beats_per_chord": 4.0,
        }, ctx, allowed, steps, emit)
        if not _ok(r):
            return {"ok": False, "error": r.get("error"), "steps": steps,
                    "checklist": checklist}
        r = _exec("insert_bassline", {
            "track_id": tracks["Stem Arrange Bass"],
            "key": key, "progression": progression, "style": "pulse",
            "start_beat": start,
        }, ctx, allowed, steps, emit)
        if not _ok(r):
            return {"ok": False, "error": r.get("error"), "steps": steps,
                    "checklist": checklist}

    r = _exec("insert_drum_pattern", {
        "track_id": tracks["Stem Arrange Drums"],
        "style": "four_on_floor", "bars": 12, "start_beat": 0.0,
    }, ctx, allowed, steps, emit)
    if not _ok(r):
        return {"ok": False, "error": r.get("error"), "steps": steps,
                "checklist": checklist}

    # Markers (seconds ≈ beats * 60 / tempo for 4/4 rough placement)
    beat_sec = 60.0 / tempo
    for name, beat in (("intro", 0.0), ("verse", 16.0), ("chorus", 32.0)):
        r = _exec("add_marker", {
            "name": name, "position_seconds": beat * beat_sec,
        }, ctx, allowed, steps, emit)
        if not _ok(r):
            return {"ok": False, "error": r.get("error"), "steps": steps,
                    "checklist": checklist}

    # Verify
    notes = _exec("get_midi_notes", {
        "track_id": tracks["Stem Arrange Chords"],
    }, ctx, allowed, steps, emit)
    checklist["notes_present"] = _ok(notes) and len(notes.get("notes", [])) > 0
    final = _exec("get_session_overview", {}, ctx, allowed, steps, emit)
    marker_names = {m.get("name") for m in (final.get("markers") or [])}
    checklist["markers_present"] = {"intro", "verse", "chorus"} <= marker_names

    if len(steps) > spec.max_steps:
        return {"ok": False, "error": "exceeded max_steps",
                "steps": steps, "checklist": checklist}

    ok = all(checklist.values())
    return {
        "ok": ok,
        "task": spec.name,
        "checklist": checklist,
        "steps_used": len(steps),
        "max_steps": spec.max_steps,
        "tracks": tracks,
        "steps": [{"tool": s["tool"], "ok": _ok(s["result"])} for s in steps],
    }


def run_rough_mix(ctx, emit: Optional[Callable] = None) -> dict:
    spec = TASKS["rough_mix"]
    allowed = set(spec.allowed_tools)
    steps: list = []
    checklist = {
        "tracks_seen": False,
        "gains_applied": False,
        "unmuted": False,
    }

    overview = _exec("get_session_overview", {}, ctx, allowed, steps, emit)
    if not _ok(overview):
        return {"ok": False, "error": overview.get("error"), "steps": steps}
    tracks = overview.get("tracks") or []
    checklist["tracks_seen"] = True
    if not tracks:
        return {
            "ok": False,
            "error": "no tracks to mix — create material first",
            "checklist": checklist,
            "steps_used": len(steps),
        }

    gains_ok = True
    unmuted = True
    for t in tracks:
        name = (t.get("name") or "").lower()
        tid = t["track_id"]
        if "drum" in name:
            gain = -1.0
        elif "bass" in name:
            gain = -3.0
        elif "chord" in name or "pad" in name:
            gain = -6.0
        elif "lead" in name or "vocal" in name:
            gain = -2.0
        else:
            gain = -4.0
        r = _exec("set_track_gain", {"track_id": tid, "gain_db": gain},
                  ctx, allowed, steps, emit)
        gains_ok = gains_ok and _ok(r)
        r = _exec("set_track_mute", {"track_id": tid, "muted": False},
                  ctx, allowed, steps, emit)
        unmuted = unmuted and _ok(r)

    # Best-effort audibility (may be unsupported on mock)
    _exec("make_tracks_audible", {}, ctx, allowed, steps, emit)
    checklist["gains_applied"] = gains_ok
    checklist["unmuted"] = unmuted
    ok = all(checklist.values())
    return {
        "ok": ok,
        "task": spec.name,
        "checklist": checklist,
        "steps_used": len(steps),
        "max_steps": spec.max_steps,
        "tracks_mixed": len(tracks),
        "steps": [{"tool": s["tool"], "ok": _ok(s["result"])} for s in steps],
    }


def execute_task(task_name: str, ctx, *, confirm: bool = False,
                 key: str = "C", tempo: float = 120.0, is_minor: bool = False,
                 emit: Optional[Callable] = None) -> dict:
    if task_name not in TASKS:
        return {"error": f"unknown task: {task_name}",
                "known": sorted(TASKS)}
    if not confirm:
        return preview_task(task_name, key=key, tempo=tempo, is_minor=is_minor)
    if task_name == "arrange_loop_to_song":
        return run_arrange_loop_to_song(
            ctx, key=key, tempo=tempo, is_minor=is_minor, emit=emit)
    if task_name == "rough_mix":
        return run_rough_mix(ctx, emit=emit)
    return {"error": f"no executor for {task_name}"}
