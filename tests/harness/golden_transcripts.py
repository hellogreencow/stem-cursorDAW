"""H5 runner: JSON golden transcripts → ScriptedProvider → session predicates."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from stem.agent.loop import StemAgent
from stem.agent.providers import BaseProvider, CompletionResult, ToolCall
from stem.bridge.mock import MockBridge

import stem.tools.generation_tools  # noqa: F401
import stem.tools.plugin_tools  # noqa: F401
import stem.tools.proposal_tools  # noqa: F401
import stem.tools.sample_tools  # noqa: F401
import stem.tools.memory_tools  # noqa: F401
import stem.tools.ardour_tools  # noqa: F401


TRANSCRIPT_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "transcripts"


class ScriptedProvider(BaseProvider):
    def __init__(self, script):
        self.script = list(script)
        self.seen = []
        self.systems = []

    def complete(self, system, messages, tools):
        self.systems.append(system)
        self.seen.append([dict(m) for m in messages])
        if not self.script:
            raise AssertionError("ScriptedProvider exhausted before agent finished")
        return self.script.pop(0)


def _parse_script(raw_steps: list) -> list:
    out = []
    for step in raw_steps:
        calls = []
        for tc in step.get("tool_calls") or []:
            calls.append(ToolCall(
                id=str(tc.get("id") or f"t{len(calls)}"),
                name=tc["name"],
                input=dict(tc.get("input") or {}),
            ))
        out.append(CompletionResult(text=step.get("text") or "",
                                    tool_calls=calls))
    return out


def load_transcript(path: Path) -> dict:
    return json.loads(path.read_text())


def _apply_setup(bridge: MockBridge, spec: dict) -> dict:
    """Apply setup hooks; return replacements for script placeholders."""
    replacements = {}
    for setup in spec.get("setup") or []:
        if setup.get("locate") is not None:
            bridge.locate(float(setup["locate"]))
        sel = setup.get("selection") or {}
        if sel.get("create_track"):
            tid, _ = bridge.create_midi_track(sel["create_track"], "mock.piano")
            bridge.set_selection(
                track_ids=[tid],
                start_seconds=sel.get("start_seconds"),
                end_seconds=sel.get("end_seconds"),
            )
            replacements["__SELECTION_TRACK__"] = tid
    return replacements


def _apply_replacements(script: list, replacements: dict) -> list:
    script = copy.deepcopy(script)
    for step in script:
        for tc in step.get("tool_calls") or []:
            inp = tc.get("input") or {}
            for key, val in list(inp.items()):
                if isinstance(val, str) and val in replacements:
                    inp[key] = replacements[val]
            tc["input"] = inp
    return script


def run_transcript(spec: dict) -> dict:
    bridge = MockBridge()
    replacements = _apply_setup(bridge, spec)
    script = _apply_replacements(spec["script"], replacements)
    provider = ScriptedProvider(_parse_script(script))
    agent = StemAgent(
        bridge, provider=provider,
        project_id=spec.get("project_id", "default"),
    )
    reply = agent.chat(spec["user"])

    tool_names = []
    for msg in agent.messages:
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls") or []:
                tool_names.append(tc["name"])

    return {
        "reply": reply,
        "bridge": bridge,
        "agent": agent,
        "provider": provider,
        "tool_names": tool_names,
    }


def assert_transcript(spec: dict, result: dict):
    tools = result["tool_names"]
    expected = spec.get("expect_tools") or []
    if spec.get("expect_tools_ordered", True):
        it = iter(tools)
        for name in expected:
            for got in it:
                if got == name:
                    break
            else:
                raise AssertionError(
                    f"tool {name!r} not found in order; got {tools}")
    else:
        missing = [n for n in expected if n not in tools]
        assert not missing, f"missing tools {missing}; got {tools}"

    session = spec.get("expect_session") or {}
    bridge = result["bridge"]
    overview = bridge.get_session_overview()
    if "tempo" in session:
        assert overview.tempo == session["tempo"], overview.tempo
    if "min_tracks" in session:
        assert len(overview.tracks) >= session["min_tracks"]
    if "playing" in session:
        assert bridge.playing is session["playing"]
    if "min_notes_on_any_track" in session:
        counts = [
            len(bridge.get_midi_notes(t.track_id))
            for t in overview.tracks if t.kind == "midi"
        ]
        assert counts and max(counts) >= session["min_notes_on_any_track"]

    for needle in spec.get("expect_reply_contains") or []:
        assert needle.lower() in result["reply"].lower(), result["reply"]

    for needle in spec.get("expect_system_contains") or []:
        systems = result["provider"].systems
        assert systems, "provider never saw a system prompt"
        assert any(needle.lower() in s.lower() for s in systems), systems[0]


def iter_transcripts():
    if not TRANSCRIPT_DIR.exists():
        return []
    return [(path, load_transcript(path))
            for path in sorted(TRANSCRIPT_DIR.glob("*.json"))]
