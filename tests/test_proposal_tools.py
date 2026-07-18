"""M3 selection + sidecar proposal accept/reject."""
import stem.tools.proposal_tools  # noqa: F401
from stem.tools.core import registry


def run(ctx, tool, **kwargs):
    return registry.execute(tool, kwargs, ctx)


def test_playhead_and_selection(mock_ctx):
    mock_ctx.bridge.locate(3.5)
    assert run(mock_ctx, "get_playhead")["playhead_seconds"] == 3.5

    track = run(mock_ctx, "create_midi_track", name="Lead")["track_id"]
    sel = run(mock_ctx, "set_selection", track_ids=[track],
              start_seconds=0.0, end_seconds=4.0)
    assert sel["supported"] is True
    assert sel["track_ids"] == [track]
    got = run(mock_ctx, "get_selection")
    assert got["track_ids"] == [track]
    assert got["end_seconds"] == 4.0


def test_propose_accept_writes_notes(mock_ctx):
    track = run(mock_ctx, "create_midi_track", name="P")["track_id"]
    prop = run(mock_ctx, "propose_midi_notes", track_id=track, summary="motif",
               notes=[
                   {"pitch": 60, "start_beat": 0.0, "length_beats": 1.0},
                   {"pitch": 64, "start_beat": 1.0, "length_beats": 1.0},
               ])
    assert prop["status"] == "pending"
    assert run(mock_ctx, "get_midi_notes", track_id=track)["notes"] == []

    listed = run(mock_ctx, "list_proposals")
    assert listed["count"] == 1

    accepted = run(mock_ctx, "accept_proposal",
                   proposal_id=prop["proposal_id"])
    assert accepted["status"] == "accepted"
    assert accepted.get("action_id")
    notes = run(mock_ctx, "get_midi_notes", track_id=track)["notes"]
    assert len(notes) == 2

    # Undo the accept insert
    run(mock_ctx, "undo", action_id=accepted["action_id"])
    assert run(mock_ctx, "get_midi_notes", track_id=track)["notes"] == []


def test_propose_reject_leaves_session_clean(mock_ctx):
    track = run(mock_ctx, "create_midi_track", name="P")["track_id"]
    prop = run(mock_ctx, "propose_midi_notes", track_id=track, notes=[
        {"pitch": 72, "start_beat": 0.0, "length_beats": 0.5},
    ])
    rejected = run(mock_ctx, "reject_proposal",
                   proposal_id=prop["proposal_id"])
    assert rejected["status"] == "rejected"
    assert run(mock_ctx, "get_midi_notes", track_id=track)["notes"] == []
    # Second reject fails closed
    again = run(mock_ctx, "reject_proposal",
                proposal_id=prop["proposal_id"])
    assert "error" in again
