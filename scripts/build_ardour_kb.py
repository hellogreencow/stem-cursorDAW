#!/usr/bin/env python3
"""Build Stem's Ardour command knowledge base from Ardour's own definitions.

Sources (machine-readable, shipped with Ardour — no screenshots needed):
  - ardour.keys      : every keybinding -> action + group
  - *_actions.cc     : human labels for action ids (register_action(..., _("Label")))

Output: stem/data/ardour_commands.json — a flat list of
  {action, label, key, key_mac, group, menu}
that the agent can search to teach the user how to do things in Ardour.

Usage:
  python scripts/build_ardour_kb.py [ARDOUR_SRC_DIR]
Default ARDOUR_SRC_DIR = ~/Desktop/ardour-ai
"""
import html
import json
import os
import re
import sys
from pathlib import Path

ARDOUR = Path(sys.argv[1] if len(sys.argv) > 1
              else os.path.expanduser("~/Desktop/ardour-ai"))
OUT = Path(__file__).resolve().parent.parent / "stem" / "data" / "ardour_commands.json"

# macOS modifier mapping (verified from libs/gtkmm2ext/keyboard.cc __APPLE__ block)
MAC_MOD = {"Primary": "⌘", "Secondary": "⌃",
           "Tertiary": "⇧", "Level4": "⌥"}
# generic (Linux/Windows) names
GEN_MOD = {"Primary": "Ctrl", "Secondary": "Alt",
           "Tertiary": "Shift", "Level4": "Super"}

KEYNAME = {
    "equal": "=", "minus": "-", "plus": "+", "comma": ",", "period": ".",
    "slash": "/", "backslash": "\\", "bracketleft": "[", "bracketright": "]",
    "semicolon": ";", "apostrophe": "'", "grave": "`", "space": "Space",
    "Return": "Return", "Tab": "Tab", "Delete": "Delete", "BackSpace": "Backspace",
    "Up": "↑", "Down": "↓", "Left": "←", "Right": "→",
    "Page_Up": "PageUp", "Page_Down": "PageDown", "Home": "Home", "End": "End",
    "KP_Add": "Numpad +", "KP_Subtract": "Numpad -", "KP_Enter": "Numpad Enter",
}


def keyname(k: str) -> str:
    if k in KEYNAME:
        return KEYNAME[k]
    if len(k) == 1:
        return k.upper()
    if re.fullmatch(r"KP_\d", k):
        return "Numpad " + k[-1]
    return k


def render_key(raw: str, mac: bool) -> str:
    """'Primary-Tertiary-z' -> '⌘⇧Z' (mac) or 'Ctrl+Shift+Z' (generic)."""
    parts = raw.split("-")
    key = parts[-1]
    mods = parts[:-1]
    table = MAC_MOD if mac else GEN_MOD
    mod_str = "".join(table.get(m, m) for m in mods) if mac \
        else "+".join(table.get(m, m) for m in mods)
    kn = keyname(key)
    if mac:
        return mod_str + kn
    return (mod_str + "+" + kn) if mod_str else kn


def humanize(action: str) -> str:
    """'Editor/temporal-zoom-in' -> 'Temporal zoom in'."""
    leaf = action.split("/")[-1]
    leaf = leaf.replace("-", " ").replace("_", " ").strip()
    return leaf[:1].upper() + leaf[1:] if leaf else action


def load_labels() -> dict:
    """action-id -> human label, scraped from register_action(..., _("Label"))."""
    labels = {}
    pat = re.compile(
        r'register(?:_toggle|_radio)?_action\s*\([^,]+,\s*'
        r'(?:X_\(")?"?([A-Za-z0-9\-_]+)"?\)?\s*,\s*_\("([^"]+)"\)')
    for cc in ARDOUR.glob("gtk2_ardour/*_actions.cc"):
        try:
            txt = cc.read_text(errors="ignore")
        except OSError:
            continue
        for m in pat.finditer(txt):
            labels.setdefault(m.group(1), m.group(2))
    return labels


def find_keys_file() -> Path:
    for cand in [ARDOUR / "build" / "gtk2_ardour" / "ardour.keys",
                 ARDOUR / "gtk2_ardour" / "ardour.keys.in"]:
        if cand.exists():
            return cand
    raise SystemExit("could not find ardour.keys — pass ARDOUR_SRC_DIR")


def main():
    labels = load_labels()
    keys_file = find_keys_file()
    txt = keys_file.read_text(errors="ignore")
    bind = re.compile(r'<Binding\s+key="([^"]+)"\s+action="([^"]+)"'
                      r'(?:\s+group="([^"]+)")?\s*/>')
    seen = set()
    out = []
    for m in bind.finditer(txt):
        raw_key, action, group = m.group(1), m.group(2), html.unescape(m.group(3) or "")
        leaf = action.split("/")[-1]
        label = labels.get(leaf) or labels.get(action) or humanize(action)
        if action in seen:
            continue
        seen.add(action)
        out.append({
            "action": action,
            "label": label,
            "key": render_key(raw_key, mac=False),
            "key_mac": render_key(raw_key, mac=True),
            "group": group,
        })
    out.sort(key=lambda r: (r["group"], r["label"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(f"wrote {len(out)} commands -> {OUT}")
    print("samples:")
    for r in out[:6]:
        print(f"  {r['key_mac']:10} {r['label']}  ({r['group']})")


if __name__ == "__main__":
    main()
