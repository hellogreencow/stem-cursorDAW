"""Music and session assertions used across tool / beat / live tests."""
from __future__ import annotations

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLAT_EQUIV = {"DB": "C#", "EB": "D#", "GB": "F#", "AB": "G#", "BB": "A#"}

MAJOR_SCALE = {0, 2, 4, 5, 7, 9, 11}
NATURAL_MINOR = {0, 2, 3, 5, 7, 8, 10}


def pitch_class(name: str) -> int:
    r = name.strip().upper().replace("♯", "#").replace("♭", "B")
    r = FLAT_EQUIV.get(r, r)
    if r not in NOTE_NAMES:
        raise ValueError(f"unknown note: {name}")
    return NOTE_NAMES.index(r)


def scale_pitch_classes(key: str, minor: bool = False) -> set[int]:
    root = pitch_class(key)
    intervals = NATURAL_MINOR if minor else MAJOR_SCALE
    return {(root + i) % 12 for i in intervals}


def note_pitch(note) -> int:
    if isinstance(note, dict):
        return int(note["pitch"])
    return int(note.pitch)


def note_start(note) -> float:
    if isinstance(note, dict):
        return float(note["start_beat"])
    return float(note.start_beat)


def assert_diatonic(notes, key: str, minor: bool = False, *,
                    allow_extra: set[int] | None = None):
    """Every note's pitch class is in the scale (plus optional borrowed pcs)."""
    allowed = scale_pitch_classes(key, minor)
    if allow_extra:
        allowed = allowed | allow_extra
    bad = [note_pitch(n) for n in notes
           if (note_pitch(n) % 12) not in allowed]
    assert not bad, (
        f"non-diatonic pitches {bad} for key={key} "
        f"{'minor' if minor else 'major'}; allowed pcs={sorted(allowed)}"
    )


def assert_chord_at(notes, beat: float, pitch_classes: list[int],
                    *, tol: float = 1e-6):
    found = sorted(
        note_pitch(n) % 12 for n in notes
        if abs(note_start(n) - beat) <= tol
    )
    expected = sorted(p % 12 for p in pitch_classes)
    assert found == expected, f"at beat {beat}: got {found}, want {expected}"


def assert_has_action_id(result: dict):
    assert "error" not in result, result
    assert result.get("action_id"), f"missing action_id: {result}"


def assert_undo_clears_notes(ctx, track_id: str, action_id: str):
    from stem.tools.core import registry
    undone = registry.execute("undo", {"action_id": action_id}, ctx)
    assert undone.get("undone"), undone
    notes = registry.execute("get_midi_notes", {"track_id": track_id}, ctx)
    assert notes.get("notes") == []


def session_fingerprint(bridge) -> dict:
    """Stable-ish snapshot for invariant checks (not a full deep equal)."""
    overview = bridge.get_session_overview()
    tracks = []
    for t in overview.tracks:
        notes = bridge.get_midi_notes(t.track_id) if t.kind == "midi" else []
        tracks.append({
            "id": t.track_id,
            "name": t.name,
            "kind": t.kind,
            "muted": t.muted,
            "gain_db": t.gain_db,
            "plugin_ids": [p.get("id") for p in (t.plugins or [])],
            "note_count": len(notes),
            "pitches": sorted(n.pitch for n in notes),
        })
    return {
        "tempo": overview.tempo,
        "meter": overview.meter,
        "sample_rate": overview.sample_rate,
        "markers": list(overview.markers),
        "tracks": tracks,
    }


def assert_session_equiv(a, b):
    assert session_fingerprint(a) == session_fingerprint(b)
