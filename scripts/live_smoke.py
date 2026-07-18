#!/usr/bin/env python3
"""Phase 0 live smoke against a running Ardour + Stem bridge.

Usage:
  python scripts/live_smoke.py

Exit codes: 0 pass, 1 fail, 2 bridge unreachable.

IMPORTANT (D002): Do not set STEM_HOME for this script unless the Lua bridge
is also reading that directory. Stock stem_bridge.lua uses $HOME/.stem.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Ensure repo root on path when invoked as a script.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Refuse a custom STEM_HOME so we don't talk past Lua's mailbox.
if os.environ.get("STEM_HOME"):
    print(json.dumps({
        "ok": False,
        "error": "STEM_HOME is set; unset it for live smoke (Lua uses ~/.stem)",
    }))
    sys.exit(2)


def main() -> int:
    from stem.bridge.ardour import ArdourBridge
    from stem.agent.loop import ToolContext
    from stem.tools.core import registry
    import stem.tools.generation_tools  # noqa: F401
    import stem.tools.ardour_tools  # noqa: F401

    bridge = ArdourBridge(rpc_timeout=10.0)
    if not bridge.connected():
        print(json.dumps({
            "ok": False,
            "error": "Ardour bridge unreachable (ping failed)",
        }))
        return 2

    ctx = ToolContext(bridge=bridge)
    report = {"ok": True, "steps": []}

    def step(name, tool, **kwargs):
        result = registry.execute(tool, kwargs, ctx)
        entry = {"step": name, "tool": tool, "result": result}
        report["steps"].append(entry)
        if "error" in result:
            report["ok"] = False
        return result

    overview = step("read", "get_session_overview")
    if overview.get("untrusted_fields"):
        report["untrusted_fields"] = overview["untrusted_fields"]

    created = step("create_track", "create_midi_track", name="StemSmoke Chords")
    track_id = created.get("track_id")
    if not track_id:
        report["ok"] = False
        print(json.dumps(report, indent=2))
        return 1

    prog = step(
        "progression", "insert_chord_progression",
        track_id=track_id, key="C", progression="I_V_vi_IV",
    )
    notes = step("verify", "get_midi_notes", track_id=track_id)
    note_list = notes.get("notes") or []
    report["notes_inserted"] = len(note_list)
    if len(note_list) < 3:
        report["ok"] = False

    action_id = prog.get("action_id")
    if action_id:
        undone = step("undo", "undo", action_id=action_id)
        after = step("verify_empty", "get_midi_notes", track_id=track_id)
        if not undone.get("undone") or after.get("notes"):
            report["ok"] = False
    else:
        report["ok"] = False

    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
