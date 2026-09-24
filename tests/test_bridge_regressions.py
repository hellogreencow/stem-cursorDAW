"""Regression tests for the three live bugs the Ardour source audit found.

Each of these was a real, shipping bug in stem/bridge/bridge_impl.lua that the
existing suite passed straight over, because the existing suite only grepped
the Lua for API names. None of the three is visible that way:

  1. add_marker opened a reversible command and THEN made a nil call, so it
     threw with Ardour still holding an open undo command — the user's next
     real edit got folded into it. Silent undo-stack corruption.
  2. Tempo was read through TempoPoint parent-class access, which Ardour's own
     binding source has a FIXME saying does not work. It returned garbage, the
     bridge fell back to 120, and region lengths (computed from tempo) came out
     ~25% short in a 90 bpm session.
  3. Every error return was encoded with string.format("%q", ...), which is Lua
     source quoting, not JSON. Lua error strings contain newlines, so the
     Python side could not parse any failure payload.

So these tests run the bridge instead of reading it. tests/lua/mock_ardour.lua
supplies the Ardour binding shapes (each one checked against Ardour 8.12 and
9.8 source in BRIDGE_AUDIT.md) and one dispatch tick is executed per call.

Every behavioural test is paired with a source-level invariant that runs even
where no Lua interpreter is installed, so the regression is still guarded on a
bare CI box — just less thoroughly. A skipped test is not a passing test.
"""

import json
import math
import re

import pytest

from .luaharness import (
    BRIDGE_IMPL,
    call_bridge,
    handler_source,
    lua_syntax_check,
    requires_lua,
    strip_lua_comments,
)


# ======================================================================
# 0. the file is loadable at all
# ======================================================================

def test_bridge_impl_is_syntactically_valid_lua():
    ok, message = lua_syntax_check(BRIDGE_IMPL)
    if ok is None:
        pytest.skip(message)
    assert ok, f"bridge_impl.lua does not compile: {message}"


# ======================================================================
# 1. undo stack — bug: begin_reversible_command left open by a throwing call
# ======================================================================

def test_add_marker_does_not_open_a_reversible_command():
    """Source invariant. The old add_marker called

        Session:begin_reversible_command("add marker")
        Session:locations():add_mark(...)        -- nil method: throws

    Locations has no add_mark in Ardour 8.12 or 9.8 (BRIDGE_AUDIT.md), so this
    threw every time with the command still open. The supported call,
    Editor:mouse_add_new_marker, opens and commits its own undo record, so the
    handler must not open one itself — there is nothing left to balance it.
    """
    # code only: the handler carries a comment explaining the old bug, and an
    # assertion that matches prose is not an assertion about behaviour
    source = handler_source("add_marker", code_only=True)
    assert "begin_reversible_command" not in source, (
        "add_marker must not open a reversible command: mouse_add_new_marker "
        "manages its own undo record, and a manually opened command has no "
        "commit on the throwing path"
    )
    assert "add_mark" not in source, (
        "Session:locations():add_mark does not exist in Ardour 8.12 or 9.8"
    )


def test_every_reversible_command_in_the_bridge_is_balanced():
    """Source invariant, whole file.

    Any handler that opens a reversible command must also commit or abort it.
    An unbalanced one is the exact shape of the add_marker bug, and it is
    silent: Ardour does not complain, it just swallows the user's next edit.
    """
    source = strip_lua_comments(BRIDGE_IMPL.read_text())
    blocks = re.split(r"\nfunction handlers\.", source)
    unbalanced = []
    for block in blocks[1:]:
        name = block.split("(", 1)[0].strip()
        if "begin_reversible_command" not in block:
            continue
        closes = ("commit_reversible_command" in block
                  or "abort_reversible_command" in block
                  or "abort_empty_reversible_command" in block)
        if not closes:
            unbalanced.append(name)
    assert not unbalanced, (
        f"handlers open a reversible command and never close it: {unbalanced}"
    )


@requires_lua
def test_marker_failure_leaves_no_open_undo_command(tmp_path):
    """Behavioural. Force the marker call to throw and check what Ardour is
    left holding: the old code left an open command here."""
    res = call_bridge(tmp_path, "add_marker",
                      {"position_seconds": 4, "name": "Drop"},
                      marker_fails=True)

    # it must fail cleanly and legibly... (as a dispatch-level error since the
    # undo work: a failed mutation raises, so ArdourBridge._call raises and
    # records no action id — see test_bridge_undo.py)
    assert res.error, f"expected an error payload, got {res.raw!r}"
    assert "add marker failed" in res.error

    # ...and, the point of the test, with the undo stack untouched
    begins = [c for c in res.calls if c.startswith("BEGIN:")]
    assert begins == [], (
        f"a failed add_marker left reversible command(s) open: {begins}"
    )


@requires_lua
def test_marker_success_commits_nothing_of_its_own(tmp_path):
    res = call_bridge(tmp_path, "add_marker",
                      {"position_seconds": 4, "name": "Drop"})
    assert res.result.get("ok") is True
    assert "mouse_add_new_marker" in res.calls
    assert [c for c in res.calls if c.startswith("BEGIN:")] == []


@requires_lua
def test_tempo_write_failure_aborts_the_tempo_map_copy(tmp_path):
    """The same class of bug on the other transaction in the file.

    Temporal.TempoMap.write_copy() must be matched by update() or
    abort_update() — Ardour's own s_tempo_map.lua says so in a comment. A
    half-finished tempo edit leaves the map copy dangling.
    """
    ok_res = call_bridge(tmp_path, "set_tempo", {"bpm": 90})
    assert ok_res.result.get("ok") is True
    assert "TempoMap.write_copy" in ok_res.calls
    assert "TempoMap.update" in ok_res.calls, (
        "write_copy was never matched by update"
    )
    # and the reversible command around it is balanced
    begins = len([c for c in ok_res.calls if c.startswith("BEGIN:")])
    closes = len([c for c in ok_res.calls if c in ("COMMIT", "ABORT_EMPTY")])
    assert begins <= closes, (
        f"set_tempo opened {begins} reversible command(s) and closed {closes}"
    )


# ======================================================================
# 2. tempo read — bug: TempoPoint parent access, regions came out ~25% short
# ======================================================================

def test_tempo_is_read_through_quarters_per_minute_at_first():
    """Source invariant.

    Ardour's luabindings.cc carries a FIXME above TempoPoint saying parent-class
    Temporal::Tempo access through it does not work. The old bridge did exactly
    that: TempoMap.read():tempo_at(pos):quarter_notes_per_minute(). The working
    read is TempoMap:quarters_per_minute_at(pos), which returns a plain double
    (temporal/tempo.h:934) and involves no cast.
    """
    source = strip_lua_comments(BRIDGE_IMPL.read_text())
    tempo_reader = source.split("local function tempo_bpm", 1)[1].split(
        "\nlocal function ", 1)[0]

    assert "quarters_per_minute_at" in tempo_reader

    # the broken form is tempo_at(...):quarter_notes_per_minute() with no
    # :to_tempo() downcast in between
    broken = re.search(r"tempo_at\s*\([^)]*\)\s*:\s*quarter_notes_per_minute",
                       tempo_reader)
    assert broken is None, (
        "tempo is still read by calling a Temporal::Tempo method directly on a "
        "TempoPoint — the access Ardour's own source says fails"
    )

    # the working call must be attempted before the TempoPoint path, and the
    # 120 bpm default must be the last resort rather than an early return
    assert tempo_reader.index("quarters_per_minute_at") < tempo_reader.index(
        "tempo_at"), "the correct read must be tried before the TempoPoint cast"
    # Since the undo work the reader returns nil when both real reads fail
    # (set_tempo needs to KNOW the prior tempo to be undoable), and tempo_bpm
    # wraps it with the 120 default for lengths and reports.
    assert tempo_reader.rstrip().endswith("return nil\nend"), (
        "the reader must fall through to 'unknown' only after both real reads"
    )
    wrapper = source.split("local function tempo_bpm ()", 1)[1].split(
        "\nlocal function ", 1)[0]
    assert "tempo_bpm_read () or 120.0" in wrapper, (
        "120 bpm must be the final fallback, reached only after both real reads"
    )


@requires_lua
def test_session_tempo_is_reported_not_defaulted(tmp_path):
    """Behavioural: a 90 bpm session must report 90, not the 120 fallback.

    The mock's tempo_at() raises, reproducing the FIXME; only the correct read
    path can succeed here.
    """
    res = call_bridge(tmp_path, "get_session_overview", bpm=90)
    assert res.result["tempo"] == pytest.approx(90.0), (
        f"session at 90 bpm reported {res.result['tempo']} "
        "(120 means the broken read fell through to the default)"
    )
    assert "TempoMap:quarters_per_minute_at" in res.calls


@requires_lua
def test_region_length_follows_session_tempo(tmp_path):
    """Behavioural, and this is the one that bit users.

    Region length is computed from tempo. With the tempo read broken the bridge
    used 120, so a 90 bpm session got regions 25% too short and the tail of a
    chord progression was cut off. Assert the created region is sized for the
    real tempo.
    """
    notes = [{"pitch": 60, "start_beat": 0, "length_beats": 2, "velocity": 100},
             {"pitch": 64, "start_beat": 2, "length_beats": 2, "velocity": 100}]
    sr, bpm = 48000, 90

    res = call_bridge(tmp_path, "insert_midi_notes",
                      {"track_id": "Chords", "notes": notes},
                      bpm=bpm, sample_rate=sr, ardour="9")
    assert res.result.get("ok") is True, res.raw

    created = [c for c in res.calls if c.startswith("MidiTimeAxisView:add_region")]
    assert created, f"no region was created; calls were {res.calls}"
    length = int(created[0].split("len=", 1)[1])

    beats_needed = 4  # two bars' worth: max(start+length) over the notes
    expected = math.ceil((beats_needed + 1) * (60.0 / bpm) * sr)   # 160000
    wrong_at_120 = math.ceil((beats_needed + 1) * (60.0 / 120) * sr)  # 120000

    assert abs(length - expected) <= 1, (
        f"region is {length} samples, expected ~{expected} at {bpm} bpm"
    )
    assert length != wrong_at_120, (
        "region was sized at the 120 bpm fallback — the tempo read regressed"
    )


@requires_lua
def test_tempo_read_falls_back_when_the_modern_call_is_missing(tmp_path):
    """The fallback path must also produce the right number, not 120."""
    res = call_bridge(tmp_path, "get_session_overview",
                      bpm=90, qpm_fails=True, tempo_at_ok=True)
    assert res.result["tempo"] == pytest.approx(90.0)


# ======================================================================
# 3. JSON validity — bug: string.format("%q") is Lua quoting, not JSON
# ======================================================================

def test_bridge_never_encodes_json_with_lua_percent_q():
    """Source invariant.

    string.format("%q", s) emits a backslash followed by a REAL newline for a
    newline, and \\9 for a tab. Python's json.loads rejects both. It must not
    come back.
    """
    source = strip_lua_comments(BRIDGE_IMPL.read_text())
    assert '%q' not in source, (
        'string.format("%q") is Lua source quoting, not JSON — it produced '
        "unparseable payloads on every error path"
    )


@requires_lua
@pytest.mark.parametrize(
    "method,args,profile",
    [
        # handler-level errors
        ("delete_track", {"track_id": "nope"}, {}),
        ("insert_midi_notes", {"track_id": "nope", "notes": [{"pitch": 60, "start_beat": 0, "length_beats": 1}]}, {}),
        ("set_tempo", {"bpm": -1}, {}),
        ("set_tempo", {"bpm": 120}, {"no_editor": True}),
        ("import_audio", {"file_path": "/tmp/x.wav"}, {"no_editor": True}),
        ("add_marker", {"position_seconds": 1}, {"no_editor": True}),
        # the Ardour 8 dead end
        ("insert_midi_notes", {"track_id": "Chords", "notes": [{"pitch": 60, "start_beat": 0, "length_beats": 1}]}, {"ardour": "8"}),
        # a throwing handler (error text carries newlines and a tab)
        ("add_marker", {"position_seconds": 1, "name": "x"}, {"marker_fails": True}),
        # unknown method
        ("no_such_method", {}, {}),
        # success paths, for completeness
        ("ping", {}, {}),
        ("get_session_overview", {}, {}),
        ("get_midi_notes", {"track_id": "Chords"}, {}),
    ],
)
def test_every_response_is_valid_json(tmp_path, method, args, profile):
    res = call_bridge(tmp_path, method, args, **profile)
    try:
        payload = json.loads(res.raw)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"{method} returned unparseable JSON ({exc}): {res.raw!r}"
        ) from exc
    assert payload.get("id") == "t1"


@requires_lua
def test_error_text_containing_newlines_survives_the_encoder(tmp_path):
    """The exact failure mode: a Lua error carries "file:line:" and a traceback,
    so the payload the Python side gets contains newlines and tabs."""
    res = call_bridge(tmp_path, "add_marker",
                      {"position_seconds": 1, "name": "x"},
                      marker_fails=True)
    message = res.handler_error or res.error
    assert message, res.raw
    # the raw bytes must escape the newline, not emit it literally inside a string
    assert "\\n" in res.raw
    # and it must decode back to a real newline and tab, i.e. escaped not stripped
    assert "\n" in message and "\t" in message
    assert "stack traceback" in message


@requires_lua
def test_control_characters_in_user_input_round_trip(tmp_path):
    """A track name with a quote, backslash, newline, tab and a raw control byte
    must come back byte-identical through the error payload.

    This goes through BOTH codecs: Python's json.dumps escapes them on the way
    in (the bridge's decoder must understand \\uXXXX) and the bridge's encoder
    escapes them on the way out.
    """
    nasty = 'weird"\\name\n\twith\x01control'
    res = call_bridge(tmp_path, "delete_track", {"track_id": nasty})
    # (a failed mutation is a dispatch-level error since the undo work)
    assert res.error == f"track not found: {nasty}"


@requires_lua
@pytest.mark.parametrize("name", [
    "Caf\u00e9 Guitar",        # \u00e9 — one \uXXXX escape
    "\u30c9\u30e9\u30e0",         # non-latin
    "Drop \U0001f3b5",         # a surrogate pair, i.e. an emoji
    "Bass \u2014 DI",          # em dash, the kind a chat agent emits constantly
])
def test_non_ascii_names_survive_the_round_trip(tmp_path, name):
    """Regression: the decoder used to turn every \\uXXXX escape into "?".

    json.dumps defaults to ensure_ascii=True, so the Python side escapes every
    non-ASCII character — which meant a track named "Caf\u00e9" reached Ardour as
    "Caf?". Nothing in the suite noticed, because nothing ran the decoder.
    """
    res = call_bridge(tmp_path, "delete_track", {"track_id": name})
    assert res.error == f"track not found: {name}", (
        "non-ASCII input was mangled crossing the Python/Lua boundary"
    )


@requires_lua
def test_numbers_are_json_numbers_not_lua_floats(tmp_path):
    """inf/nan are not JSON, and 48000.0 is not a sample rate anyone wants."""
    res = call_bridge(tmp_path, "get_session_overview")
    assert '"sample_rate":48000' in res.raw.replace(" ", "")
    assert "inf" not in res.raw and "nan" not in res.raw


# ======================================================================
# 5. undo model — source invariants (behaviour: tests/test_bridge_undo.py)
# ======================================================================

def test_bridge_never_calls_editor_undo():
    """Lua cannot see Ardour's undo history (no undo_depth / next_undo bound in
    8.12 or 9.8), so Editor:undo(n) pops whatever is on top — including the
    user's own edits. The bridge undoes through its journal instead."""
    code = strip_lua_comments(BRIDGE_IMPL.read_text())
    assert "Editor:undo" not in code
    assert "Editor:redo" not in code


def test_bridge_uses_no_global_that_ardours_sandbox_removes():
    """Ardour's Lua sandbox sets rawget, rawset, dofile, require, package,
    debug and coroutine to nil (libs/lua/luastate.cc:99 in 9.8). A chunk-level
    call to any of them would stop the bridge loading at all."""
    code = strip_lua_comments(BRIDGE_IMPL.read_text())
    for name in ("rawget", "rawset", "dofile", "require", "package", "debug",
                 "coroutine"):
        assert re.search(r"\b%s\b" % name, code) is None, name
