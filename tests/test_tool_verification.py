"""The undo/verification contract, against MockBridge.

Every test here is about one promise from PLAN.md: "undoable — single most
important trust feature". They exist because each failure they describe has
actually happened in this project (see TOOLBOX_AUDIT.md): a mutation with no
undo path reported as fine, an undo that silently reverted nothing, a
two-mutation tool that could only be half undone, and a checkpoint taken
before a call that then failed.
"""
import pytest

from stem.agent.loop import ToolContext
from stem.bridge.mock import MockBridge
from stem.tools.core import registry
from stem.tools import generation_tools  # noqa: F401 - registers tools
from stem.tools.verification import (
    diff, fingerprint, journal_for, scope_from_input,
)
from stem.services.elevenlabs_music import elevenlabs_music


@pytest.fixture
def ctx():
    return ToolContext(bridge=MockBridge())


def run(ctx, tool, **kwargs):
    return registry.execute(tool, kwargs, ctx)


def make_track(ctx, name="Chords"):
    return run(ctx, "create_midi_track", name=name)["track_id"]


# ---- the registry verifies every mutation ----

def test_mutating_tool_reports_what_actually_changed(ctx):
    track_id = make_track(ctx)

    r = run(ctx, "insert_chord", track_id=track_id, root="C",
            chord_type="major")

    assert r["verified"]["checked"] is True
    assert r["verified"]["changed"] is True
    assert any("notes 0 -> 3" in d for d in r["verified"]["changes"])
    assert r["undo"] == {"available": True, "action_id": r["action_id"]}


def test_read_only_tool_is_not_fingerprinted(ctx):
    make_track(ctx)
    r = run(ctx, "get_session_overview")
    assert "verified" not in r and "undo" not in r


def test_tempo_and_gain_changes_are_seen(ctx):
    track_id = make_track(ctx)

    tempo = run(ctx, "set_tempo", bpm=90)
    gain = run(ctx, "set_track_gain", track_id=track_id, gain_db=-6)

    assert tempo["verified"]["changes"] == ["tempo 120.0 -> 90.0"]
    assert any(".gain_db" in d for d in gain["verified"]["changes"])


def test_mutation_that_changes_nothing_is_flagged(ctx, monkeypatch):
    """A bridge that accepts a write and does nothing is the silent failure
    this block exists to catch."""
    track_id = make_track(ctx)
    monkeypatch.setattr(ctx.bridge, "set_track_mute",
                        lambda track_id, muted: "fake_action")

    r = run(ctx, "set_track_mute", track_id=track_id, muted=True)

    assert r["verified"]["changed"] is False
    assert "nothing measurable changed" in r["verified"]["note"]


def test_failed_tool_that_already_changed_the_session_says_so(ctx, monkeypatch):
    track_id = make_track(ctx)

    def half_done(track_id_, notes, start_beat=0.0):
        MockBridge.insert_midi_notes(ctx.bridge, track_id_, notes, start_beat)
        raise RuntimeError("bridge died after writing")

    monkeypatch.setattr(ctx.bridge, "insert_midi_notes", half_done)
    r = run(ctx, "insert_chord", track_id=track_id, root="C",
            chord_type="major")

    assert r["error"] == "bridge died after writing"
    assert any("notes 0 -> 3" in d for d in r["partial_mutation"])
    assert r["undo"]["available"] is False


# ---- a mutating tool with no undo path admits it ----

def test_tool_without_action_id_is_reported_as_not_undoable(ctx, monkeypatch):
    monkeypatch.setattr(ctx.bridge, "fix_silent_instruments",
                        lambda: {"ok": True, "fixed": ["trk_1"], "count": 1})

    r = run(ctx, "make_tracks_audible")

    assert r["undo"]["available"] is False
    assert "Ardour" in r["undo"]["reason"]
    assert r["warning"] == "mutating tool returned no action_id"


def test_make_tracks_audible_is_registered_as_mutating():
    # It replaces plugins on the user's tracks. Registered read-only, the
    # registry never checked it and nothing told the user it was one-way.
    assert registry.get("make_tracks_audible").mutates is True


# ---- undo is verified, not assumed ----

def test_undo_confirms_the_session_came_back(ctx):
    track_id = make_track(ctx)
    inserted = run(ctx, "insert_chord_progression", track_id=track_id,
                   key="C", progression="I_V_vi_IV")

    r = run(ctx, "undo", action_id=inserted["action_id"])

    assert r["undone"] is True
    assert r["restored"] is True
    assert run(ctx, "get_midi_notes", track_id=track_id)["notes"] == []


def test_undo_that_changes_nothing_is_caught(ctx, monkeypatch):
    """The Lua add_marker family: undo returns success, session untouched."""
    track_id = make_track(ctx)
    run(ctx, "insert_chord", track_id=track_id, root="C", chord_type="major")
    monkeypatch.setattr(ctx.bridge, "undo", lambda action_id=None: True)

    r = run(ctx, "undo")

    assert r["undone"] is True
    assert r["restored"] is False
    assert "never on the DAW's undo stack" in r["warning"]


def test_undo_that_reverts_the_wrong_thing_is_caught(ctx, monkeypatch):
    """On a live bridge undo() pops Ardour's stack, which may hold someone
    else's edit. The tool must not call that a successful undo."""
    track_id = make_track(ctx)
    marked = run(ctx, "add_marker", name="drop", position_seconds=8.0)

    def wrong_undo(action_id=None):
        ctx.bridge.tempo = 77.0          # something else moved instead
        return True

    monkeypatch.setattr(ctx.bridge, "undo", wrong_undo)
    r = run(ctx, "undo", action_id=marked["action_id"])

    assert r["undone"] is True
    assert r["restored"] is False
    assert r["unexpected"]
    assert "did not come back" in r["warning"]


def test_undo_of_unknown_action_is_refused(ctx):
    r = run(ctx, "undo", action_id="not_a_real_action")
    assert r["undone"] is False
    assert "unknown action_id" in r["note"]


# ---- multi-mutation tools undo as one ----

def test_generate_song_stems_undoes_both_tracks(ctx, monkeypatch, tmp_path):
    full = tmp_path / "song.wav"
    vocal = tmp_path / "vocals.wav"
    backing = tmp_path / "accompaniment.wav"
    for p in (full, vocal, backing):
        p.write_bytes(b"RIFFfake")
    monkeypatch.setattr(elevenlabs_music, "available", lambda: True)
    monkeypatch.setattr(elevenlabs_music, "generate",
                        lambda prompt, length, instrumental: str(full))
    monkeypatch.setattr(elevenlabs_music, "separate_stems",
                        lambda path: {"vocals": str(vocal),
                                      "accompaniment": str(backing)})

    r = run(ctx, "generate_song_stems", prompt="soulful hook",
            length_seconds=12)
    assert len(ctx.bridge.tracks) == 2
    assert r["action_id"] == r["vocal_action_id"]      # the FIRST mutation

    undone = run(ctx, "undo", action_id=r["action_id"])

    assert undone["undone"] is True
    assert ctx.bridge.tracks == {}, "half an undo is worse than none"


# ---- the emulator's own undo stack stays honest ----

def test_failed_import_leaves_no_phantom_undo_entry():
    """Regression: MockBridge.import_audio used to checkpoint before
    validating, so a failed import pushed an undo entry that swallowed the
    user's next undo."""
    bridge = MockBridge()
    bridge.set_tempo(90)
    depth = len(bridge._undo_stack)

    with pytest.raises(KeyError):
        bridge.import_audio("no_such_track", "/tmp/nope.wav")

    assert len(bridge._undo_stack) == depth
    assert bridge.undo() is True
    assert bridge.tempo == 120.0


# ---- fingerprint plumbing ----

def test_fingerprint_scope_only_reads_named_tracks(ctx):
    track_id = make_track(ctx)
    assert scope_from_input({"track_id": track_id}) == [track_id]
    assert scope_from_input({"bpm": 120}) == []
    fp = fingerprint(ctx.bridge, [track_id])
    assert track_id in fp["notes"] and fp["partial"] is False


def test_fingerprint_survives_a_dead_bridge(ctx, monkeypatch):
    def boom():
        raise RuntimeError("no response from Ardour bridge")
    monkeypatch.setattr(ctx.bridge, "get_session_overview", boom)

    fp = fingerprint(ctx.bridge, [])

    assert fp["partial"] is True
    assert diff(fp, fp)["checked"] is False


def test_journal_is_per_bridge():
    one, two = MockBridge(), MockBridge()
    assert journal_for(one) is journal_for(one)
    assert journal_for(one) is not journal_for(two)


def test_verification_can_be_switched_off(ctx, monkeypatch):
    monkeypatch.setenv("STEM_VERIFY", "0")
    track_id = make_track(ctx)
    r = run(ctx, "insert_chord", track_id=track_id, root="C",
            chord_type="major")
    assert "verified" not in r
