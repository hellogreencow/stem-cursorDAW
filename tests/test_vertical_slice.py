"""The Phase 0 milestone, as a test, against MockBridge:
command -> read session -> create track -> in-key progression -> verify -> undo.
Plus tool validation and theory sanity checks.
"""
import pytest

from stem.bridge.mock import MockBridge
from stem.tools.core import registry
from stem.agent.loop import ToolContext


@pytest.fixture
def ctx():
    return ToolContext(bridge=MockBridge())


def run(ctx, tool, **kwargs):
    return registry.execute(tool, kwargs, ctx)


def test_phase0_milestone(ctx):
    # read session
    overview = run(ctx, "get_session_overview")
    assert overview["tempo"] == 120.0 and overview["tracks"] == []

    # create track
    instruments = run(ctx, "list_instruments", query="piano")
    assert instruments["count"] == 1
    chosen = instruments["instruments"][0]["id"]

    r = run(ctx, "create_midi_track", name="Chords",
            instrument_id=chosen)
    track_id = r["track_id"]
    assert track_id and r["action_id"]
    assert ctx.bridge.tracks[track_id].plugins[0]["id"] == chosen

    # insert in-key progression (C major I-V-vi-IV)
    r = run(ctx, "insert_chord_progression", track_id=track_id,
            key="C", progression="I_V_vi_IV")
    assert r["chords"] == ["C", "G", "Am", "F"]
    assert r["notes_inserted"] == 12  # 4 triads
    action_id = r["action_id"]

    # verify: notes exist and are diatonic to C major
    notes = run(ctx, "get_midi_notes", track_id=track_id)["notes"]
    assert len(notes) == 12
    c_major = {0, 2, 4, 5, 7, 9, 11}
    assert all(n["pitch"] % 12 in c_major for n in notes)

    # C chord at beat 0: C-E-G
    beat0 = sorted(n["pitch"] % 12 for n in notes if n["start_beat"] == 0)
    assert beat0 == [0, 4, 7]

    # play (transport flips state)
    run(ctx, "transport_play")
    assert ctx.bridge.playing

    # undo the insertion specifically
    r = run(ctx, "undo", action_id=action_id)
    assert r["undone"]
    assert run(ctx, "get_midi_notes", track_id=track_id)["notes"] == []


def test_minor_key_progression(ctx):
    track_id = run(ctx, "create_midi_track", name="t")["track_id"]
    r = run(ctx, "insert_chord_progression", track_id=track_id,
            key="A", progression="Andalusian")
    assert "error" not in r
    # A natural minor plus G# — the Andalusian cadence's major V (E major)
    # borrows the raised 7th from harmonic minor.
    allowed = {9, 11, 0, 2, 4, 5, 7, 8}
    notes = run(ctx, "get_midi_notes", track_id=track_id)["notes"]
    assert all(n["pitch"] % 12 in allowed for n in notes)


def test_flat_key_names(ctx):
    track_id = run(ctx, "create_midi_track", name="t")["track_id"]
    r = run(ctx, "insert_chord", track_id=track_id, root="Bb",
            chord_type="major")
    assert r["chord"] == "A#"  # Bb == A# in this engine's naming
    assert sorted(p % 12 for p in r["pitches"]) == [2, 5, 10]


def test_drum_pattern(ctx):
    track_id = run(ctx, "create_midi_track", name="Drums")["track_id"]
    r = run(ctx, "insert_drum_pattern", track_id=track_id, style="trap",
            bars=2)
    assert "error" not in r and r["notes_inserted"] > 0
    notes = run(ctx, "get_midi_notes", track_id=track_id)["notes"]
    assert any(n["pitch"] == 36 for n in notes)  # kick present
    assert all(n.channel == 9 for n in ctx.bridge.notes[track_id])


def test_bassline_follows_progression(ctx):
    track_id = run(ctx, "create_midi_track", name="Bass")["track_id"]
    r = run(ctx, "insert_bassline", track_id=track_id, key="C",
            progression="I_IV_V", style="pulse")
    assert "error" not in r
    notes = run(ctx, "get_midi_notes", track_id=track_id)["notes"]
    roots = {n["pitch"] % 12 for n in notes}
    assert roots == {0, 5, 7}  # C, F, G roots only


def test_validation_rejects_garbage(ctx):
    assert "error" in run(ctx, "insert_midi_notes", track_id="nope",
                          notes=[{"pitch": 200, "start_beat": 0,
                                  "length_beats": 1}])
    assert "error" in run(ctx, "set_tempo", bpm=-5)
    assert "error" in run(ctx, "insert_chord_progression", track_id="x",
                          key="H", progression="I_V_vi_IV")
    assert "error" in run(ctx, "nonexistent_tool")


def test_mutations_on_missing_track_fail_loudly(ctx):
    r = run(ctx, "insert_chord_progression", track_id="ghost",
            key="C", progression="I_IV_V")
    assert "error" in r


def test_undo_last_without_id(ctx):
    track_id = run(ctx, "create_midi_track", name="t")["track_id"]
    run(ctx, "set_tempo", bpm=140)
    assert ctx.bridge.tempo == 140
    run(ctx, "undo")
    assert ctx.bridge.tempo == 120
    # track still exists (only last action undone)
    assert track_id in ctx.bridge.tracks
