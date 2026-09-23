"""ArdourBridge's client side of replace_midi_notes and the undo journal.

No Ardour here: _call is replaced by a recorder that answers the way the
Lua bridge's contract says it will. What is tested is the Python half of
that contract — what goes over the wire, and that Python's action record
stays 1:1 with the Lua journal (an action is recorded only when the bridge
succeeded, and undo names the entry it expects the bridge to pop).
"""
import pytest

from stem.bridge.ardour import ArdourBridge
from stem.bridge.base import MidiNote


class RecordingBridge(ArdourBridge):
    def __init__(self, answers=None):
        self.rpc_timeout = 1.0
        self._action_seq = []
        self.calls = []
        self.answers = answers or {}
        self.journal = []            # what a journaling Lua bridge would hold

    def _call(self, method, args=None):
        self.calls.append((method, args or {}))
        answer = self.answers.get(method)
        if callable(answer):
            return answer(args or {})
        if isinstance(answer, Exception):
            raise answer
        if answer is not None:
            return answer
        if method == "undo":
            if not self.journal:
                return {"ok": False}
            want = (args or {}).get("action_id")
            if want is not None and want != self.journal[-1]:
                raise RuntimeError("ardour bridge: not the top entry")
            self.journal.pop()
            return {"ok": True}
        entry = f"j{len(self.journal) + 1}"
        self.journal.append(entry)
        return {"ok": True, "action_id": entry, "undoable": True}


def test_replace_sends_absolute_notes_and_the_range():
    b = RecordingBridge()
    aid = b.replace_midi_notes("Keys", [MidiNote(60, 4.5, 1, 90, 9)], 4.0, 8.0)
    method, args = b.calls[-1]
    assert method == "replace_midi_notes"
    assert args == {"track_id": "Keys", "start_beat": 4.0, "end_beat": 8.0,
                    "notes": [{"pitch": 60, "start_beat": 4.5,
                               "length_beats": 1, "velocity": 90,
                               "channel": 9}]}
    assert aid == "j1" and b._action_seq == ["j1"]


def test_replace_open_end_leaves_end_beat_out_of_the_request():
    b = RecordingBridge()
    b.replace_midi_notes("Keys", [], 2.0)
    assert "end_beat" not in b.calls[-1][1]


def test_replace_accepts_dict_notes():
    b = RecordingBridge()
    b.replace_midi_notes("Keys", [{"pitch": 61, "start_beat": 0,
                                   "length_beats": 2}])
    assert b.calls[-1][1]["notes"] == [{"pitch": 61, "start_beat": 0,
                                        "length_beats": 2, "velocity": 100,
                                        "channel": 0}]


@pytest.mark.parametrize("failure", [
    RuntimeError("ardour bridge: no such track"),       # new Lua: raises
    {"error": "no such track"},                          # old Lua: a value
])
def test_a_failed_mutation_is_never_recorded(failure):
    b = RecordingBridge(answers={"replace_midi_notes": failure,
                                 "set_tempo": failure})
    for call in (lambda: b.replace_midi_notes("X", []),
                 lambda: b.set_tempo(90)):
        with pytest.raises(RuntimeError):
            call()
    assert b._action_seq == []
    assert b.undo() is False             # nothing to pop, nothing popped


def test_undo_names_the_top_entry_and_stays_aligned():
    b = RecordingBridge()
    first = b.set_tempo(100)
    second = b.replace_midi_notes("Keys", [])
    assert b.undo() is True
    assert b.calls[-1] == ("undo", {"action_id": second})
    assert b._action_seq == [first] and b.journal == [first]


def test_undo_back_to_an_action_pops_newest_first():
    b = RecordingBridge()
    a = b.set_tempo(100)
    b.set_track_gain("Keys", -3)
    b.replace_midi_notes("Keys", [])
    assert b.undo(a) is True
    assert b._action_seq == [] and b.journal == []
    assert [c for c in b.calls if c[0] == "undo"] == [
        ("undo", {"action_id": "j3"}), ("undo", {"action_id": "j2"}),
        ("undo", {"action_id": "j1"})]


def test_a_refused_undo_keeps_the_record():
    b = RecordingBridge()
    b.set_tempo(100)
    b.journal[-1] = "someone-else"       # the two sides drifted
    assert b.undo() is False
    assert b._action_seq == ["j1"]       # not silently dropped


def test_old_bridge_without_journal_ids_still_undoes():
    b = RecordingBridge(answers={"set_tempo": {"ok": True},
                                 "undo": {"ok": True}})
    aid = b.set_tempo(100)
    assert aid and b.undo() is True
    assert b.calls[-1] == ("undo", {})   # no id to name, so none sent


def test_add_instrument_asks_for_a_journal_and_records_only_if_journaled():
    b = RecordingBridge()
    r = b.add_instrument("Keys")
    assert b.calls[-1] == ("add_instrument", {"track_id": "Keys",
                                              "journal": True})
    assert r["action_id"] == "j1" and b._action_seq == ["j1"]

    old = RecordingBridge(answers={"add_instrument": {"added": True,
                                                      "instrument": True}})
    r = old.add_instrument("Keys")
    assert "action_id" not in r and old._action_seq == []


def test_get_midi_notes_keeps_channel_when_the_bridge_sends_it():
    b = RecordingBridge(answers={"get_midi_notes": {"notes": [
        {"pitch": 36, "start_beat": 0, "length_beats": 1, "velocity": 99,
         "channel": 9},
        {"pitch": 38, "start_beat": 1, "length_beats": 1}]}})
    notes = b.get_midi_notes("Drums")
    assert [n.channel for n in notes] == [9, 0]
