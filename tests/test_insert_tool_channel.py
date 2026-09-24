"""The insert_midi_notes tool keeps each note's MIDI channel.

Found running the Python tools against real Ardour 9.8: a note inserted with
"channel": 5 through the insert_midi_notes TOOL came back on channel 0. The
bridge keeps channels (live: get_midi_notes returned 0/3/9 exactly), but the
tool's NoteSpec had no channel field, so pydantic dropped it before the bridge
saw it.
"""
from stem.agent.loop import ToolContext
from stem.bridge.mock import MockBridge
from stem.tools.core import registry


def test_insert_midi_notes_tool_passes_the_channel_through():
    ctx = ToolContext(bridge=MockBridge())
    track = registry.execute("create_midi_track", {"name": "Keys"}, ctx)["track_id"]
    r = registry.execute("insert_midi_notes", {"track_id": track, "notes": [
        {"pitch": 60, "start_beat": 0.0, "length_beats": 1.0},
        {"pitch": 62, "start_beat": 1.0, "length_beats": 1.0, "channel": 5},
        {"pitch": 36, "start_beat": 2.0, "length_beats": 1.0, "channel": 9},
    ]}, ctx)
    assert "error" not in r, r
    got = sorted((n.pitch, n.channel) for n in ctx.bridge.get_midi_notes(track))
    assert got == [(36, 9), (60, 0), (62, 5)]


def test_insert_midi_notes_tool_rejects_a_bad_channel():
    ctx = ToolContext(bridge=MockBridge())
    track = registry.execute("create_midi_track", {"name": "Keys"}, ctx)["track_id"]
    r = registry.execute("insert_midi_notes", {"track_id": track, "notes": [
        {"pitch": 60, "start_beat": 0.0, "length_beats": 1.0, "channel": 16}]}, ctx)
    assert "error" in r
