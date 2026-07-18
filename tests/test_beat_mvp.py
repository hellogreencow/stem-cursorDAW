"""M1 dogfood gate on mock: a full house beat via tools and via StemScript.

Acceptance shape (EXECUTION_PLAN M1): tempo + key, drums, bass, chords,
optional lead, play, undoable.
"""
from pathlib import Path

import pytest

from stem.agent.loop import ToolContext
from stem.bridge.mock import MockBridge
from stem.language import execute_stemscript
from stem.tools.core import registry
from tests.harness.asserts_music import (
    assert_diatonic,
    assert_has_action_id,
    note_pitch,
)


pytestmark = pytest.mark.dogfood


def run(ctx, tool, **kwargs):
    return registry.execute(tool, kwargs, ctx)


@pytest.fixture
def ctx():
    return ToolContext(bridge=MockBridge())


def _build_house_beat_via_tools(ctx):
    actions = []
    r = run(ctx, "set_tempo", bpm=124)
    assert_has_action_id(r)
    actions.append(r["action_id"])

    r = run(ctx, "add_marker", name="beat", position_seconds=0.0)
    assert_has_action_id(r)
    actions.append(r["action_id"])

    chords = run(ctx, "create_midi_track", name="Chords",
                 instrument_id="mock.piano")
    assert_has_action_id(chords)
    actions.append(chords["action_id"])
    chord_id = chords["track_id"]

    # Andalusian is defined on a minor scale (i–VII–VI–V).
    r = run(ctx, "insert_chord_progression", track_id=chord_id,
            key="F", progression="Andalusian")
    assert_has_action_id(r)
    actions.append(r["action_id"])

    drums = run(ctx, "create_midi_track", name="Drums",
                instrument_id="mock.synth")
    assert_has_action_id(drums)
    actions.append(drums["action_id"])
    r = run(ctx, "insert_drum_pattern", track_id=drums["track_id"],
            style="four_on_floor", bars=4)
    assert_has_action_id(r)
    assert r["notes_inserted"] > 0
    actions.append(r["action_id"])

    bass = run(ctx, "create_midi_track", name="Bass",
               instrument_id="mock.synth")
    assert_has_action_id(bass)
    actions.append(bass["action_id"])
    r = run(ctx, "insert_bassline", track_id=bass["track_id"],
            key="F", progression="Andalusian", style="driving")
    assert_has_action_id(r)
    actions.append(r["action_id"])

    lead = run(ctx, "create_midi_track", name="Lead",
               instrument_id="mock.synth")
    assert_has_action_id(lead)
    actions.append(lead["action_id"])
    r = run(ctx, "insert_midi_notes", track_id=lead["track_id"], notes=[
        {"pitch": 72, "start_beat": 0.0, "length_beats": 0.5, "velocity": 100},
        {"pitch": 75, "start_beat": 1.0, "length_beats": 0.5, "velocity": 96},
        {"pitch": 77, "start_beat": 2.0, "length_beats": 1.0, "velocity": 100},
    ])
    assert_has_action_id(r)
    actions.append(r["action_id"])

    run(ctx, "transport_play")
    assert ctx.bridge.playing
    run(ctx, "transport_stop")
    assert not ctx.bridge.playing

    overview = run(ctx, "get_session_overview")
    assert overview["tempo"] == 124.0
    assert len(overview["tracks"]) >= 4
    assert any(m.get("name") == "beat" for m in overview["markers"])

    drum_notes = run(ctx, "get_midi_notes",
                     track_id=drums["track_id"])["notes"]
    assert any(note_pitch(n) == 36 for n in drum_notes)

    return actions, chord_id


def test_house_beat_via_tools(ctx):
    actions, chord_id = _build_house_beat_via_tools(ctx)
    chord_notes = run(ctx, "get_midi_notes", track_id=chord_id)["notes"]
    assert len(chord_notes) >= 3
    # Undo last lead insert specifically — stack remains coherent.
    assert run(ctx, "undo", action_id=actions[-1]).get("undone")


def test_house_beat_via_stemscript(ctx):
    script = Path("examples/dogfood/house_beat.stem").read_text()
    reply = execute_stemscript(script, ctx)
    assert "error" not in reply.lower() or "Ran StemScript" in reply
    overview = run(ctx, "get_session_overview")
    assert overview["tempo"] == 124.0
    names = {t["name"] for t in overview["tracks"]}
    assert any("Chord" in n or "chord" in n.lower() for n in names)
    assert any("Drum" in n or "drum" in n.lower() for n in names)
    assert any("Bass" in n or "bass" in n.lower() for n in names)
    # Notes exist on every MIDI track
    for t in overview["tracks"]:
        if t["kind"] != "midi":
            continue
        notes = run(ctx, "get_midi_notes", track_id=t["track_id"])["notes"]
        assert notes, f"expected notes on {t['name']}"


def test_house_beat_stemscript_chords_diatonic_minor(ctx):
    script = Path("examples/dogfood/house_beat.stem").read_text()
    execute_stemscript(script, ctx)
    overview = run(ctx, "get_session_overview")
    chord_track = next(t for t in overview["tracks"] if "Chord" in t["name"])
    notes = run(ctx, "get_midi_notes",
                track_id=chord_track["track_id"])["notes"]
    # Andalusian in F minor borrows the major V (C major) → allow E natural.
    assert_diatonic(notes, "F", minor=True, allow_extra={4})
