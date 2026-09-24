"""The agent's toolbox.

Importing anything from this package registers the tools that have no
optional dependencies — the core session/MIDI/theory tools and ardour_help —
so every entry point sees the same toolbox. Before this, ardour_help was
registered only by webserver.py, which meant the CLI chat shell and the agent
daemon quietly shipped without it: the same agent answered "how do I record a
track?" in one front end and not the other.

Generation tools stay opt-in (``from stem.tools import generation_tools``)
because they pull the audio-generation service stack; the entry points that
want them already import them explicitly.
"""
from . import core  # noqa: F401 - registers the core toolbox
from . import ardour_tools  # noqa: F401 - registers ardour_help
from . import midi_edit  # noqa: F401 - registers the note-editing tools

registry = core.registry

__all__ = ["core", "ardour_tools", "midi_edit", "registry"]
