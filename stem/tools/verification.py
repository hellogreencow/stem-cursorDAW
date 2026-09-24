"""Post-execution verification and the undo journal.

Every mutating tool is measured, not trusted. The registry fingerprints the
session immediately before and after a mutating handler runs, records the pair
in a journal keyed by the action_id the tool returned, and attaches a
``verified`` block to the tool result saying what actually changed in the DAW.

Why this exists: an ``action_id`` only promises that *something* can be undone.
It does not prove the mutation landed, and it does not prove the bridge's undo
stack will put the session back. Both of those have failed in this project
before (see TOOLBOX_AUDIT.md), and a mutation that silently did not happen —
or an undo that silently reverts the wrong thing — is exactly the failure that
loses a producer's work. Fingerprints turn both into visible facts.

Cost: one ``get_session_overview`` call, plus one ``get_midi_notes`` call per
track named in the tool's own arguments, on each side of a mutation. Against
MockBridge that is free; against a live Ardour it is two to four RPC round
trips. Set ``STEM_VERIFY=0`` to turn it off.
"""
import hashlib
import os
import weakref
from typing import Optional

#: fields of TrackInfo that are cheap to read and worth watching
TRACK_FIELDS = ("name", "kind", "muted", "soloed", "gain_db", "pan")


def verification_enabled() -> bool:
    """Verification is on unless STEM_VERIFY says otherwise."""
    return os.environ.get("STEM_VERIFY", "1").strip().lower() not in (
        "0", "false", "no", "off")


def scope_from_input(raw_input) -> list:
    """Track ids this tool call talks about, so note reads stay bounded.

    Empty means "overview only" — cheap, and still catches tempo, meter,
    marker, mute, gain and track-count changes.
    """
    if not isinstance(raw_input, dict):
        return []
    ids = []
    for key in ("track_id", "target_track_id", "source_track_id"):
        value = raw_input.get(key)
        if isinstance(value, str) and value:
            ids.append(value)
    extra = raw_input.get("track_ids")
    if isinstance(extra, (list, tuple)):
        ids.extend(t for t in extra if isinstance(t, str) and t)
    seen = set()
    return [t for t in ids if not (t in seen or seen.add(t))]


def _note_digest(notes) -> dict:
    """Order-independent digest of a track's notes."""
    rows = sorted(
        (round(float(n.start_beat), 6), int(n.pitch),
         round(float(n.length_beats), 6), int(n.velocity),
         int(getattr(n, "channel", 0)))
        for n in notes)
    h = hashlib.sha1(repr(rows).encode()).hexdigest()[:16]
    return {"count": len(rows), "digest": h}


def fingerprint(bridge, track_ids=None) -> dict:
    """Read what the session looks like right now.

    Never raises: a bridge that cannot answer produces a fingerprint marked
    ``partial``, and verification then reports "could not check" instead of
    inventing a verdict.
    """
    fp = {"tracks": {}, "notes": {}, "partial": False, "unreadable": []}
    try:
        overview = bridge.get_session_overview()
    except Exception as e:                       # live bridge may be down
        fp["partial"] = True
        fp["unreadable"].append(f"session overview: {e}")
        return fp
    fp["tempo"] = getattr(overview, "tempo", None)
    fp["meter"] = getattr(overview, "meter", None)
    fp["playhead_seconds"] = getattr(overview, "playhead_seconds", None)
    fp["markers"] = [dict(m) if isinstance(m, dict) else {"name": str(m)}
                     for m in (getattr(overview, "markers", None) or [])]
    for t in getattr(overview, "tracks", None) or []:
        fp["tracks"][t.track_id] = {f: getattr(t, f, None) for f in TRACK_FIELDS}
        fp["tracks"][t.track_id]["plugins"] = list(getattr(t, "plugins", []) or [])
    for track_id in (track_ids or []):
        if track_id not in fp["tracks"]:
            continue                              # unknown id: nothing to read
        if fp["tracks"][track_id].get("kind") != "midi":
            continue
        try:
            fp["notes"][track_id] = _note_digest(bridge.get_midi_notes(track_id))
        except Exception as e:
            fp["partial"] = True
            fp["unreadable"].append(f"notes on {track_id}: {e}")
    return fp


def diff(before: Optional[dict], after: Optional[dict]) -> dict:
    """What changed between two fingerprints, in words a producer would use."""
    out = {"changed": False, "details": []}
    if not before or not after:
        out["checked"] = False
        return out
    out["checked"] = not (before.get("partial") or after.get("partial"))

    if before.get("tempo") != after.get("tempo"):
        out["details"].append(
            f"tempo {before.get('tempo')} -> {after.get('tempo')}")
    if before.get("meter") != after.get("meter"):
        out["details"].append(
            f"meter {before.get('meter')} -> {after.get('meter')}")

    b_marks = [m.get("name") for m in before.get("markers", [])]
    a_marks = [m.get("name") for m in after.get("markers", [])]
    if b_marks != a_marks:
        added = len(a_marks) - len(b_marks)
        out["details"].append(
            f"markers {len(b_marks)} -> {len(a_marks)}"
            + (f" (+{added})" if added > 0 else ""))

    b_tracks, a_tracks = before.get("tracks", {}), after.get("tracks", {})
    for track_id in sorted(set(a_tracks) - set(b_tracks)):
        out["details"].append(
            f"track added: {a_tracks[track_id].get('name')} ({track_id})")
    for track_id in sorted(set(b_tracks) - set(a_tracks)):
        out["details"].append(
            f"track removed: {b_tracks[track_id].get('name')} ({track_id})")
    for track_id in sorted(set(b_tracks) & set(a_tracks)):
        for field in TRACK_FIELDS + ("plugins",):
            b_val, a_val = b_tracks[track_id].get(field), a_tracks[track_id].get(field)
            if b_val != a_val:
                out["details"].append(
                    f"{track_id}.{field} {b_val!r} -> {a_val!r}")

    for track_id in sorted(set(before.get("notes", {})) | set(after.get("notes", {}))):
        b_notes = before.get("notes", {}).get(track_id)
        a_notes = after.get("notes", {}).get(track_id)
        if b_notes == a_notes:
            continue
        b_count = b_notes["count"] if b_notes else "?"
        a_count = a_notes["count"] if a_notes else "?"
        if b_notes and a_notes and b_count == a_count:
            out["details"].append(f"{track_id}: {a_count} notes changed in place")
        else:
            out["details"].append(f"{track_id}: notes {b_count} -> {a_count}")

    out["changed"] = bool(out["details"])
    return out


def same(before: Optional[dict], after: Optional[dict]) -> bool:
    """True when two fingerprints describe the same session state."""
    return bool(before) and bool(after) and not diff(before, after)["changed"]


class JournalEntry:
    def __init__(self, action_id, tool, before, after, changes):
        self.action_id = action_id
        self.tool = tool
        self.before = before
        self.after = after
        self.changes = changes


class MutationJournal:
    """Ordered record of this session's mutations, per bridge.

    Kept in the tools layer on purpose: it works the same against MockBridge,
    ArdourBridge, or anything added later, and it is what lets ``undo`` say
    whether the session actually came back.
    """

    def __init__(self):
        self.entries: list = []

    def record(self, action_id, tool, before, after, changes) -> JournalEntry:
        entry = JournalEntry(action_id, tool, before, after, changes)
        self.entries.append(entry)
        return entry

    def get(self, action_id) -> Optional[JournalEntry]:
        if action_id is None:
            return self.entries[-1] if self.entries else None
        for entry in reversed(self.entries):
            if entry.action_id == action_id:
                return entry
        return None

    def forget_from(self, entry: JournalEntry) -> None:
        """Drop that entry and everything after it — undo went back past them."""
        if entry in self.entries:
            self.entries = self.entries[:self.entries.index(entry)]


_JOURNALS: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
_FALLBACK: dict = {}


def journal_for(bridge) -> MutationJournal:
    """The journal for this bridge instance (one session, one journal)."""
    try:
        journal = _JOURNALS.get(bridge)
        if journal is None:
            journal = MutationJournal()
            _JOURNALS[bridge] = journal
        return journal
    except TypeError:                             # not weak-referenceable
        return _FALLBACK.setdefault(id(bridge), MutationJournal())
