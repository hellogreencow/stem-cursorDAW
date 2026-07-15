"""Ardour how-to tool: teach the user how to operate Ardour itself.

Backed by ardour_kb (Ardour's own keybinding/menu definitions + a curated
FAQ), so the agent can answer "how do I do X in Ardour?" with real shortcuts
and menu locations instead of guessing.
"""
from pydantic import BaseModel, Field

from .core import registry
from ..services.ardour_kb import kb


class ArdourHelp(BaseModel):
    question: str = Field(
        description="What the user wants to do in Ardour, e.g. 'how do I "
                    "record a track', 'zoom in', 'add a reverb plugin', "
                    "'change tempo'. Use the user's own words.")


@registry.register(
    "ardour_help",
    "Look up how to do something in Ardour itself (keyboard shortcuts, menu "
    "locations, workflow tips). Use this when the user asks how to USE the DAW "
    "— e.g. record, add a plugin, zoom, export — rather than asking you to "
    "make music. Returns real shortcuts (macOS symbols: ⌘ Cmd ⌃ Ctrl ⇧ Shift "
    "⌥ Option) and menu groups; relay them clearly to the user.",
    ArdourHelp)
def ardour_help(args, ctx):
    res = kb.search(args.question)
    if not res["commands"] and not res["tips"]:
        return {"found": False,
                "note": "No exact match. Suggest the user check the relevant "
                        "top menu (Session, Track, Region, View, Window) or "
                        "ask to rephrase."}
    return {"found": True,
            "commands": res["commands"],
            "tips": res["tips"]}
