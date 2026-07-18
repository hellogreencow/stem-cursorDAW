"""M3 context + proposal review tools (sidecar accept/reject)."""
from typing import List, Optional

from pydantic import BaseModel, Field

from .core import registry
from ..bridge.base import MidiNote


class Empty(BaseModel):
    pass


@registry.register(
    "get_playhead",
    "Get the current playhead position in seconds.",
    Empty)
def get_playhead(args, ctx):
    return {"playhead_seconds": ctx.bridge.get_playhead()}


class SetSelection(BaseModel):
    track_ids: Optional[List[str]] = None
    start_seconds: Optional[float] = Field(default=None, ge=0)
    end_seconds: Optional[float] = Field(default=None, ge=0)
    region_ids: Optional[List[str]] = None


@registry.register(
    "get_selection",
    "Read what the user currently has selected (tracks, regions, time range). "
    "Use this before editing so changes target their focus.",
    Empty)
def get_selection(args, ctx):
    return ctx.bridge.get_selection()


@registry.register(
    "set_selection",
    "Set the logical selection context the agent should treat as focused "
    "(mock/full; live Ardour may be read-only depending on Lua bindings).",
    SetSelection)
def set_selection(args, ctx):
    return ctx.bridge.set_selection(
        track_ids=args.track_ids,
        start_seconds=args.start_seconds,
        end_seconds=args.end_seconds,
        region_ids=args.region_ids,
    )


class NoteSpec(BaseModel):
    pitch: int = Field(ge=0, le=127)
    start_beat: float = Field(ge=0)
    length_beats: float = Field(gt=0)
    velocity: int = Field(default=100, ge=1, le=127)


class ProposeNotes(BaseModel):
    track_id: str
    notes: List[NoteSpec]
    start_beat: float = Field(default=0.0, ge=0)
    summary: str = Field(default="", description="Short human label for the diff")


@registry.register(
    "propose_midi_notes",
    "Stage MIDI notes as a reviewable proposal without writing the session yet. "
    "User/agent should accept_proposal or reject_proposal next (Cursor-style diff).",
    ProposeNotes)
def propose_midi_notes(args, ctx):
    notes = [MidiNote(pitch=n.pitch, start_beat=n.start_beat,
                      length_beats=n.length_beats, velocity=n.velocity)
             for n in args.notes]
    proposal_id = ctx.bridge.propose_midi_notes(
        args.track_id, notes, args.start_beat, args.summary)
    return {"proposal_id": proposal_id, "note_count": len(notes),
            "status": "pending"}


@registry.register(
    "list_proposals",
    "List staged MIDI proposals and their pending/accepted/rejected status.",
    Empty)
def list_proposals(args, ctx):
    items = ctx.bridge.list_proposals()
    return {"proposals": items, "count": len(items)}


class ProposalRef(BaseModel):
    proposal_id: str


@registry.register(
    "accept_proposal",
    "Commit a pending proposal into the session (undoable insert).",
    ProposalRef, mutates=True)
def accept_proposal(args, ctx):
    action_id = ctx.bridge.accept_proposal(args.proposal_id)
    return {"action_id": action_id, "proposal_id": args.proposal_id,
            "status": "accepted"}


@registry.register(
    "reject_proposal",
    "Discard a pending proposal without changing the session.",
    ProposalRef)
def reject_proposal(args, ctx):
    ok = ctx.bridge.reject_proposal(args.proposal_id)
    if not ok:
        return {"error": f"could not reject proposal {args.proposal_id}"}
    return {"proposal_id": args.proposal_id, "status": "rejected"}
