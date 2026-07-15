import os
from pathlib import Path


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


def test_live_bridge_uses_embedded_audio_import():
    source = BRIDGE_IMPL.read_text()

    assert "ARDOUR.LuaAPI.import_audio_file" in source
    assert "Editor:do_import" not in source


def test_live_bridge_uses_native_tempo_helper():
    source = BRIDGE_IMPL.read_text()
    tempo_handler = source.split("function handlers.set_tempo", 1)[1].split(
        "function handlers.", 1)[0]

    assert "ARDOUR.LuaAPI.set_session_tempo" in tempo_handler
    assert "Temporal.TempoMap.write_copy" not in tempo_handler


def test_live_bridge_can_create_midi_regions_without_editor():
    source = BRIDGE_IMPL.read_text()
    region_helper = source.split("local function get_or_make_region", 1)[1].split(
        "function handlers.insert_midi_notes", 1)[0]

    assert "ARDOUR.LuaAPI.ensure_midi_region" in region_helper
    assert "if not Editor then return nil end" in region_helper


def test_midi_note_insert_uses_model_undo_command_directly():
    source = BRIDGE_IMPL.read_text()
    insert_handler = source.split("function handlers.insert_midi_notes", 1)[1].split(
        "function handlers.get_midi_notes", 1)[0]

    assert "mm:apply_command(Session, cmd)" in insert_handler
    assert "begin_reversible_command" not in insert_handler
    assert "commit_reversible_command" not in insert_handler


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


def test_native_panel_has_simple_quick_actions_and_library():
    source = ARDOUR_STEM_PANEL.read_text()

    assert '_song_btn (_("Song"))' in source
    assert '_vocal_btn (_("Vox"))' in source
    assert '_arrange_btn (_("Band"))' in source
    assert '_library_btn (_("Lib"))' in source
    assert 'STEM_BASE "/api/instruments"' in source
    assert "on_library" in source


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


def test_native_panel_uses_embedded_python_runtime_when_available():
    source = ARDOUR_STEM_PANEL.read_text()

    assert "stem_python_env" in source
    assert "PYTHONHOME=" in source
    assert "PYTHONPATH=" in source
    assert 'path_exists (home + "/Python3")' in source
    assert 'path_exists (lib + "/os.py")' in source


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
