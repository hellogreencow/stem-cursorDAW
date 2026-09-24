import os
from pathlib import Path

import pytest

from .luaharness import call_bridge, handler_source, requires_lua, strip_lua_comments


def _ardour_root() -> Path:
    here = Path(__file__).resolve()
    candidates = []
    if os.environ.get("ARDOUR_AI_ROOT"):
        candidates.append(Path(os.environ["ARDOUR_AI_ROOT"]))
    candidates.extend([
        here.parents[2] / "ardour-ai",
        Path.home() / "Desktop" / "ardour-ai",
        Path.home() / "Desktop" / "Desktop - Oli’s MacBook Pro" / "ardour-ai",
    ])
    for candidate in candidates:
        if (candidate / "gtk2_ardour" / "stem_panel.cc").exists():
            return candidate
    return candidates[0]


BRIDGE_IMPL = Path(__file__).resolve().parents[1] / "stem" / "bridge" / "bridge_impl.lua"
WEBSERVER = Path(__file__).resolve().parents[1] / "stem" / "webserver.py"
ARDOUR_ROOT = _ardour_root()
ARDOUR_STEM_PANEL = ARDOUR_ROOT / "gtk2_ardour" / "stem_panel.cc"
ARDOUR_OSX_BUILD = ARDOUR_ROOT / "tools" / "osx_packaging" / "osx_build"

# The tests below that read ARDOUR_STEM_PANEL / ARDOUR_OSX_BUILD assert on the
# source of the private `ardour-ai` Ardour fork, which is a DIFFERENT repository
# and is not published. On any machine without a checkout of it they raised
# FileNotFoundError — i.e. this repo's suite could not go green anywhere but on
# the author's Mac. They are still worth running where the fork IS present, so
# they skip rather than fail when it is not. A skip is not a pass: see GAPS.md.
requires_ardour_ai_checkout = pytest.mark.skipif(
    not ARDOUR_STEM_PANEL.exists(),
    reason=(
        "no ardour-ai fork checkout found (looked in $ARDOUR_AI_ROOT and the "
        f"default locations; tried {ARDOUR_ROOT}). These assert on the fork's "
        "own C++/packaging sources, not on anything in this repository."
    ),
)


# ======================================================================
# Bridge contract.
#
# These four tests used to assert that the bridge calls the private ardour-ai
# fork's Lua helpers AND THAT IT CALLS NOTHING ELSE:
#
#     assert "ARDOUR.LuaAPI.import_audio_file" in source
#     assert "Editor:do_import" not in source          # <- the bug
#
# The negative half made the unpublished fork a hard requirement: any stock-
# Ardour code path was a test failure. An audit of the Ardour 8.12 and 9.8
# sources (BRIDGE_AUDIT.md) confirmed those three helpers exist in no public
# Ardour, so as written these tests pinned the project to one machine in the
# world and blocked the fix.
#
# What they should assert — and now do — is the actual contract:
#   * the fork helper is still preferred where it exists (nothing regressed for
#     the author's build), and
#   * where it does not exist the bridge takes a documented stock-Ardour path,
#     and
#   * where even that is impossible it fails with a specific, parseable reason
#     rather than silently or unintelligibly.
#
# Each also has a behavioural counterpart that RUNS the Lua under a mock Ardour
# (tests/lua/mock_ardour.lua), because a grep cannot tell you which path was
# actually taken.
# ======================================================================


def test_audio_import_prefers_fork_helper_and_falls_back_to_stock_ardour():
    source = strip_lua_comments(BRIDGE_IMPL.read_text())
    handler = handler_source("import_audio", code_only=True)

    # unchanged requirement: the fork helper is still used where present
    assert "ARDOUR.LuaAPI.import_audio_file" in source

    # was: assert "Editor:do_import" not in source
    # now: the stock path must EXIST, and must come second.
    # Editor:do_import is what Ardour's own share/scripts/s_import_files.lua
    # uses; binding at gtk2_ardour/luainstance.cc:945 (8.12) / :1017 (9.8).
    assert "Editor:do_import" in handler, (
        "no stock-Ardour import path: audio import would fail on every build "
        "except the private fork"
    )
    assert handler.index("ARDOUR.LuaAPI.import_audio_file") < handler.index(
        "Editor:do_import"), "the fork helper must still be tried first"


def test_tempo_prefers_fork_helper_and_falls_back_to_the_stock_tempo_map():
    handler = handler_source("set_tempo", code_only=True)

    assert "ARDOUR.LuaAPI.set_session_tempo" in handler

    # was: assert "Temporal.TempoMap.write_copy" not in tempo_handler
    # now: the stock transaction must be there, and must be complete.
    # write_copy has to be matched by update() or abort_update() or the map
    # copy dangles — Ardour's s_tempo_map.lua says so in its own comment.
    assert "Temporal.TempoMap.write_copy" in handler, (
        "no stock-Ardour tempo path: 'set the tempo to 90' — the first line of "
        "the README's example session — would fail on any public Ardour"
    )
    assert "Temporal.TempoMap.update" in handler
    assert "Temporal.TempoMap.abort_update" in handler, (
        "write_copy with no abort on the failure path leaves the tempo map "
        "half-edited"
    )
    assert handler.index("ARDOUR.LuaAPI.set_session_tempo") < handler.index(
        "Temporal.TempoMap.write_copy"), "the fork helper must still be tried first"


def test_midi_region_creation_prefers_fork_helper_and_explains_when_it_cannot():
    helper = strip_lua_comments(
        BRIDGE_IMPL.read_text()
    ).split("local function get_or_make_region", 1)[1].split(
        "function handlers.insert_midi_notes", 1)[0]

    assert "ARDOUR.LuaAPI.ensure_midi_region" in helper

    # was: assert "if not Editor then return nil end" in region_helper
    # A bare `return nil` gave the caller "could not create midi region" for
    # four different causes. The audit established that on stock Ardour 8 this
    # is genuinely impossible (no MidiTimeAxisView binding at all) — so the
    # requirement is not that it succeed, it is that it say WHY, in a string
    # the agent can relay to a user.
    assert "return nil, " in helper, (
        "the region helper must return a reason alongside nil so the failure "
        "can be reported instead of guessed at"
    )
    assert "no Editor" in helper
    assert "MidiTimeAxisView" in helper


def test_midi_note_insert_commits_through_the_model_not_a_manual_command():
    handler = handler_source("insert_midi_notes", code_only=True)

    # was: assert "mm:apply_command(Session, cmd)" in insert_handler
    # apply_command is marked deprecated in Ardour's binding source ("left here
    # in case any extant scripts use apply_command"). The current call is
    # apply_diff_command_as_commit. Both are bound on 8.12 and 9.8, so the
    # bridge uses the current one and keeps the deprecated one as fallback.
    # The test now names the behaviour rather than one exact byte sequence.
    assert "apply_diff_command_as_commit" in handler
    assert "apply_command" in handler, "keep the fallback for older builds"

    # unchanged, and still the important half: the MidiModel command carries
    # its own undo record, so the handler must not open one around it.
    assert "begin_reversible_command" not in handler
    assert "commit_reversible_command" not in handler


# --- behavioural: the same four claims, executed ----------------------------

@requires_lua
def test_bridge_runs_on_stock_ardour_9_without_the_fork(tmp_path):
    """The claim the old tests made impossible: a stock build works."""
    notes = [{"pitch": 60, "start_beat": 0, "length_beats": 2, "velocity": 100}]

    tempo = call_bridge(tmp_path, "set_tempo", {"bpm": 90}, ardour="9")
    assert tempo.result.get("ok") is True
    assert tempo.result.get("via") == "tempomap", "should use the stock path"

    notes_res = call_bridge(tmp_path, "insert_midi_notes",
                            {"track_id": "Chords", "notes": notes}, ardour="9")
    assert notes_res.result.get("ok") is True
    assert "apply_diff_command_as_commit" in notes_res.calls

    audio = call_bridge(tmp_path, "import_audio",
                        {"file_path": "/tmp/x.wav"}, ardour="9")
    assert audio.result.get("ok") is True
    assert "do_import" in audio.calls


@requires_lua
def test_bridge_still_prefers_the_fork_helpers_when_present(tmp_path):
    """Nothing regressed for the build that has them."""
    tempo = call_bridge(tmp_path, "set_tempo", {"bpm": 90}, fork=True)
    assert tempo.result.get("via") == "fork"
    assert "fork:set_session_tempo=90" in tempo.calls

    audio = call_bridge(tmp_path, "import_audio",
                        {"file_path": "/tmp/x.wav"}, fork=True)
    assert audio.result.get("ok") is True
    assert "fork:import_audio_file" in audio.calls

    notes = call_bridge(tmp_path, "insert_midi_notes",
                        {"track_id": "Chords",
                         "notes": [{"pitch": 60, "start_beat": 0,
                                    "length_beats": 1, "velocity": 90}]},
                        fork=True)
    assert notes.result.get("ok") is True
    assert "fork:ensure_midi_region" in notes.calls


@requires_lua
def test_ardour_8_degrades_with_a_specific_parseable_reason(tmp_path):
    """Where stock Ardour 8 genuinely cannot do it, say so usefully.

    MIDI region creation is impossible from Lua on 8.12 — no MidiTimeAxisView
    binding exists and RegionFactory exposes only region_by_id/regions/
    clone_region. The old message was "could not create midi region", which
    reads like a Stem bug. It must now name the cause and the way out.
    """
    res = call_bridge(tmp_path, "insert_midi_notes",
                      {"track_id": "Chords",
                       "notes": [{"pitch": 60, "start_beat": 0,
                                  "length_beats": 1, "velocity": 90}]},
                      ardour="8")
    message = res.handler_error
    assert message, res.raw
    assert "MidiTimeAxisView" in message
    assert "Ardour 9" in message
    assert message != "could not create midi region"

    # everything else on the SAME Ardour 8 build must still work
    assert call_bridge(tmp_path, "set_tempo", {"bpm": 90},
                       ardour="8").result.get("ok") is True
    assert call_bridge(tmp_path, "add_marker", {"position_seconds": 1},
                       ardour="8").result.get("ok") is True
    assert call_bridge(tmp_path, "import_audio", {"file_path": "/tmp/x.wav"},
                       ardour="8").result.get("ok") is True


@requires_lua
def test_ping_reports_what_this_ardour_build_can_do(tmp_path):
    """So the agent can say "this build can't make MIDI regions" up front
    rather than failing at the last step of a chord progression."""
    stock8 = call_bridge(tmp_path, "ping", ardour="8").result
    caps8 = stock8.get("caps", {})
    assert caps8.get("fork_set_session_tempo") is False
    assert caps8.get("editor_add_region") is False

    fork = call_bridge(tmp_path, "ping", fork=True).result["caps"]
    assert fork.get("fork_set_session_tempo") is True
    assert fork.get("fork_ensure_midi_region") is True


@requires_ardour_ai_checkout
def test_native_panel_launches_embedded_agent_without_browser():
    source = ARDOUR_STEM_PANEL.read_text()
    ensure_server = source.split("StemPanel::ensure_server ()", 1)[1].split(
        "/* ------------------------------------------------------------------ */", 1)[0]

    assert "-m stem.webserver --embedded" in ensure_server
    assert "STEM_NO_BROWSER=1" in ensure_server
    assert 'getenv ("STEM_ROOT")' in ensure_server
    assert "stem/webserver.py" in source
    assert "-sTCP:LISTEN" in ensure_server
    assert "Contents/Resources/stem" in source
    assert "STEM_ROOT=" in ensure_server


@requires_ardour_ai_checkout
def test_native_panel_has_simple_quick_actions_and_library():
    source = ARDOUR_STEM_PANEL.read_text()

    assert '_song_btn (_("Song"))' in source
    assert '_vocal_btn (_("Vox"))' in source
    assert '_arrange_btn (_("Band"))' in source
    assert '_library_btn (_("Lib"))' in source
    assert 'STEM_BASE "/api/instruments"' in source
    assert "on_library" in source


@requires_ardour_ai_checkout
def test_native_panel_keeps_chat_from_resizing_ardour_window():
    source = ARDOUR_STEM_PANEL.read_text()

    assert "set_size_request (320, -1)" in source
    assert "_chat_col.set_size_request (300, -1)" in source
    assert "_transcript_scroll.set_size_request (300, -1)" in source
    assert ("_transcript_scroll.set_policy "
            "(Gtk::POLICY_AUTOMATIC, Gtk::POLICY_AUTOMATIC)") in source
    assert "_entry.set_width_chars (24)" in source
    assert "_title.set_ellipsize (Pango::ELLIPSIZE_END)" in source
    assert "_status.set_ellipsize (Pango::ELLIPSIZE_END)" in source


@requires_ardour_ai_checkout
def test_macos_packaging_can_embed_stem_runtime():
    source = ARDOUR_OSX_BUILD.read_text()
    public_branch = source.split("--public)", 1)[1].split("shift ;;", 1)[0]
    noharvid_branch = source.split("--noharvid)", 1)[1].split(";;", 1)[0]

    assert "STEM_BUNDLE_ROOT" in source
    assert "Contents/Resources/stem" in source or "$Resources/stem" in source
    assert "venv/bin/python" in source
    assert "Embedding Stem Python runtime" in source
    assert "STEM_SOURCE_PYTHON=$(realpath" in source
    assert 'rm -rf "$STEM_VENV/_CodeSignature"' in source
    assert 'rm -f "$STEM_VENV/pyvenv.cfg"' in source
    assert 'cp -p "$STEM_SOURCE_PYTHON" "$STEM_PY_BIN/python3"' in source
    assert 'find "$STEM_VENV" -type l -delete' in source
    assert "stem/webserver.py" in source
    assert "WITH_GMSYNTH=1" in public_branch
    assert "WITH_GRATIS_X42_LV2=1" in public_branch
    assert "WITH_HARVID=" in noharvid_branch
    assert "WITH_XJADEO=" in noharvid_branch
    assert "adhoc_sign_app" in source
    assert 'local STEM_PYTHON_DYLIB=' in source
    assert 'codesign --force --sign - --timestamp=none "$STEM_PYTHON_DYLIB"' in source
    assert 'codesign --force --sign - --timestamp=none "$APP_PATH"' in source
    assert 'codesign --force --deep --sign - --timestamp=none "$APP_PATH"' not in source


@requires_ardour_ai_checkout
def test_native_panel_waits_for_agent_before_chat_post():
    source = ARDOUR_STEM_PANEL.read_text()
    worker = source.split("StemPanel::worker_run", 1)[1].split(
        "void\nStemPanel::push", 1)[0]
    drain = source.split("bool\nStemPanel::drain_queue", 1)[1].split(
        "/* ------------------------------------------------------------------ */", 1)[0]

    assert 'curl_get_with_timeout (STEM_BASE "/api/session", 1L, 1L)' in worker
    assert 'session.find ("\\"tracks\\"")' in worker
    assert 'session.find ("\\"connected\\"")' in worker
    assert 'http_post (STEM_BASE "/api/chat", body)' in worker
    assert worker.index('curl_get_with_timeout (STEM_BASE "/api/session", 1L, 1L)') < worker.index('http_post (STEM_BASE "/api/chat", body)')
    assert 'push ("panel_status", _("Connecting"))' in worker
    assert 'push ("panel_status", _("Working"))' in worker
    assert 'i->kind == "panel_status"' in drain


@requires_ardour_ai_checkout
def test_native_panel_uses_embedded_python_runtime_when_available():
    source = ARDOUR_STEM_PANEL.read_text()

    assert "stem_python_env" in source
    assert "PYTHONHOME=" in source
    assert "PYTHONPATH=" in source
    assert 'path_exists (home + "/Python3")' in source
    assert 'path_exists (lib + "/os.py")' in source


@requires_ardour_ai_checkout
def test_native_panel_syncs_selected_history_to_agent():
    source = ARDOUR_STEM_PANEL.read_text()
    selected = source.split("StemPanel::on_history_selected", 1)[1]

    assert 'STEM_BASE "/api/history"' in source
    assert 'sync_history_to_agent (turns)' in selected
    assert 'std::string ("assistant")' in selected
    assert 'curl_post_with_timeout (STEM_BASE "/api/history", body, 1L, 5L)' in source


def test_embedded_agent_suppresses_browser_launch():
    source = WEBSERVER.read_text()
    main = source.split("def main():", 1)[1]
    ensure_live = source.split("def _ensure_live_bridge():", 1)[1].split(
        "def _run_job", 1)[0]

    assert 'parser.add_argument("--embedded"' in main
    assert 'os.environ["STEM_NO_BROWSER"] = "1"' in main
    assert 'if not os.environ.get("STEM_NO_BROWSER"):' in main
    assert "webbrowser.open(url)" in main
    assert "_init_agent(force_mock=args.mock)" in main
    assert "StemHTTPServer((HOST, PORT), Handler)" in main
    assert "if _force_mock:" in ensure_live
