"""Helpers for driving the real bridge_impl.lua under a mock Ardour.

The bridge is Lua that talks to Ardour's C++ bindings. We cannot run Ardour
here, but we CAN run the Lua: tests/lua/mock_ardour.lua provides the binding
shapes (verified against Ardour 8.12 and 9.8 source — see BRIDGE_AUDIT.md) and
this module executes bridge_impl.lua against it, one dispatch tick per call.

That matters because the three bugs the audit found were all behavioural and
all invisible to a source grep: an unbalanced undo command, a tempo read that
returned the wrong number, and an error payload that did not parse.

If no Lua interpreter is installed the behavioural tests skip (and say so);
each of them is paired with a source-level invariant test that always runs.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
BRIDGE_IMPL = REPO_ROOT / "stem" / "bridge" / "bridge_impl.lua"
MOCK_ARDOUR = TESTS_DIR / "lua" / "mock_ardour.lua"
RUN_CALL = TESTS_DIR / "lua" / "run_call.lua"


def lua_binary():
    for name in ("lua5.3", "lua5.4", "lua"):
        found = shutil.which(name)
        if found:
            return found
    return None


requires_lua = pytest.mark.skipif(
    lua_binary() is None,
    reason="no Lua interpreter on PATH (apt-get install lua5.3) — "
           "behavioural bridge tests need one; the paired source-invariant "
           "tests in this file still run",
)


def lua_syntax_check(path):
    """luac -p if available; returns (ok, message) or (None, why) if no luac."""
    for name in ("luac5.3", "luac5.4", "luac"):
        found = shutil.which(name)
        if found:
            proc = subprocess.run([found, "-p", str(path)],
                                  capture_output=True, text=True)
            return proc.returncode == 0, proc.stderr.strip()
    return None, "no luac on PATH"


class BridgeResult:
    """One dispatch tick: the raw response bytes, the parse, the call trace."""

    def __init__(self, raw, calls):
        self.raw = raw
        self.calls = calls

    @property
    def parsed(self):
        """json.loads of exactly the bytes the Python side would receive.

        Deliberately NOT tolerant: this is the assertion that the payload is
        valid JSON. The old encoder used string.format("%q"), which is Lua
        source quoting, and every error payload failed here.
        """
        return json.loads(self.raw)

    @property
    def result(self):
        return self.parsed.get("result")

    @property
    def error(self):
        """The dispatch-level error (a handler that threw)."""
        return self.parsed.get("error")

    @property
    def handler_error(self):
        """The error a handler returned as a value ({error = "..."})."""
        res = self.result
        return res.get("error") if isinstance(res, dict) else None


def call_bridge(tmp_path, method, args=None, **profile):
    """Run one bridge method under a mock-Ardour profile.

    profile keys map to the mock's environment switches:
        ardour="8"|"9", fork=True, no_editor=True, bpm=90,
        qpm_fails=True, tempo_at_ok=True, marker_fails=True, sample_rate=44100
    """
    lua = lua_binary()
    assert lua, "call_bridge used without Lua; guard the test with @requires_lua"

    home = Path(tmp_path) / "home"
    (home / ".stem").mkdir(parents=True, exist_ok=True)

    args_file = home / "args.json"
    args_file.write_text(json.dumps(args or {}))

    env = dict(os.environ)
    env["HOME"] = str(home)
    env["STEM_MOCK_ARDOUR"] = str(profile.get("ardour", "9"))
    if profile.get("fork"):
        env["STEM_MOCK_FORK"] = "1"
    if profile.get("no_editor"):
        env["STEM_MOCK_NO_EDITOR"] = "1"
    if profile.get("qpm_fails"):
        env["STEM_MOCK_QPM_FAILS"] = "1"
    if profile.get("tempo_at_ok"):
        env["STEM_MOCK_TEMPO_AT_OK"] = "1"
    if profile.get("marker_fails"):
        env["STEM_MOCK_MARKER_FAILS"] = "1"
    if "bpm" in profile:
        env["STEM_MOCK_BPM"] = str(profile["bpm"])
    if "sample_rate" in profile:
        env["STEM_MOCK_SR"] = str(profile["sample_rate"])

    proc = subprocess.run(
        [lua, str(RUN_CALL), str(MOCK_ARDOUR), str(BRIDGE_IMPL),
         method, str(args_file)],
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT), timeout=60,
    )
    assert proc.returncode == 0, (
        f"lua harness failed for {method}:\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )

    resp_path = home / ".stem" / "response.json"
    assert resp_path.exists(), (
        f"bridge wrote no response for {method} "
        f"(stdout: {proc.stdout} stderr: {proc.stderr})"
    )
    raw = resp_path.read_text()
    calls_path = home / ".stem" / "calls.txt"
    calls = calls_path.read_text().splitlines() if calls_path.exists() else []
    return BridgeResult(raw, calls)


def strip_lua_comments(text):
    """Remove Lua comments so an assertion greps CODE, not prose.

    bridge_impl.lua documents each fixed bug in a comment directly above the
    fix, so a naive `assert "begin_reversible_command" not in source` matches
    the explanation of the bug and fails on the corrected file. Stripping
    comments is what makes these invariants mean what they say.
    """
    out = []
    for line in text.splitlines():
        # long comments (--[[ ... ]]) are not used in this file; line comments are
        idx = line.find("--")
        if idx != -1:
            # don't cut inside a string literal
            before = line[:idx]
            if before.count('"') % 2 == 0 and before.count("'") % 2 == 0:
                line = before
        out.append(line)
    return "\n".join(out)


def handler_source(name, code_only=False):
    """The text of one `function handlers.<name>` block."""
    source = BRIDGE_IMPL.read_text()
    marker = f"function handlers.{name}"
    assert marker in source, f"no handler named {name} in bridge_impl.lua"
    after = source.split(marker, 1)[1]
    # up to the next top-level definition
    ends = [i for i in (after.find("\nfunction handlers."),
                        after.find("\nlocal function ")) if i != -1]
    block = after[:min(ends)] if ends else after
    return strip_lua_comments(block) if code_only else block
