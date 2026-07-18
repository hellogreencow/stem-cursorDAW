# Stem Capability Matrix

Truth table for agent tools. Update when adding a `@registry.register` tool.
Legend: **Y** = supported/proven · **P** = partial/stub · **N** = no · **—** = n/a

| Tool | Mock | Live Ardour | Tested | Notes |
|---|---|---|---|---|
| `get_session_overview` | Y | Y | Y | Live may mark `untrusted_fields` for tempo/rate |
| `get_midi_notes` | Y | Y | Y | |
| `list_instruments` | Y | Y | Y | Live expands GM via FluidSynth |
| `create_midi_track` | Y | Y | Y | Phase 0 live verified |
| `insert_midi_notes` | Y | Y | Y | |
| `set_tempo` | Y | Y | Y | |
| `set_track_gain` | Y | Y | P | Covered indirectly / schema |
| `set_track_mute` | Y | Y | P | Covered indirectly / schema |
| `add_marker` | Y | Y | P | Beat MVP / StemScript path |
| `transport_play` | Y | Y | Y | |
| `transport_stop` | Y | Y | P | |
| `undo` | Y | Y | Y | Live Phase 0 verified |
| `diagnose_audio` | N* | Y | P | Mock returns unsupported |
| `make_tracks_audible` | N* | Y | P | Live-oriented |
| `insert_chord_progression` | Y | Y | Y | Phase 0 milestone |
| `insert_chord` | Y | Y | Y | |
| `get_scale_notes` | Y | — | Y | Theory-only |
| `insert_drum_pattern` | Y | Y | Y | |
| `insert_bassline` | Y | Y | Y | |
| `ardour_help` | — | — | Y | KB lookup, no DAW mutate |
| `generate_sample` | Y† | Y† | Y | Needs backend or monkeypatch |
| `generate_song` | Y† | Y† | Y | ElevenLabs |
| `generate_song_stems` | Y† | Y† | Y | |
| `generate_vocals` | Y† | Y† | Y | |
| `generate_vocal_lines` | Y† | Y† | Y | |
| `isolate_vocals` | Y† | Y† | Y | |
| `get_generation_status` | Y | Y | Y | |
| `search_samples` | Y | Y | Y | Token/metadata index (no embeddings yet) |
| `import_sample` | Y | Y | Y | Uses bridge.import_audio |
| `rebuild_sample_index` | Y | — | Y | Writes STEM_HOME/sample_index.json |
| `list_plugins` | Y | Y | Y | Instruments + effects |
| `load_plugin` | Y | Y | Y | Live Lua best-effort; mock proven |
| `get_plugin_params` | Y | Y | Y | Live may vary by plugin binding |
| `set_plugin_param` | Y | Y | Y | Undoable; clamps on mock |
| `get_playhead` | Y | Y | Y | |
| `get_selection` | Y | P | Y | Live Lua best-effort |
| `set_selection` | Y | N* | Y | Live reports unsupported |
| `propose_midi_notes` | Y | Y† | Y | Sidecar buffer (D011) |
| `list_proposals` | Y | Y† | Y | |
| `accept_proposal` | Y | Y† | Y | Commits via insert_midi_notes |
| `reject_proposal` | Y | Y† | Y | |
| `recall_memory` | Y | — | Y | STEM_HOME/memory/*.json |
| `update_memory` | Y | — | Y | Secrets redacted on write |
| `list_tasks` | Y | — | Y | Autonomous mode catalog |
| `run_task` | Y | Y* | Y | confirm=true required to execute |

\* Bridge ABC default / mock does not fully emulate engine audio.  
† Import path works on mock; generation backend may be stubbed in tests.
  Cassettes: `STEM_CASSETTE_DIR` + `STEM_CASSETTE_MODE=replay`.
  Proposals are Python-side on ArdourBridge (not piano-roll ghosts).
  `run_task` uses the same tools on live; dogfood still recommended.

**Not started (Phase 2+):** embeddings/CLAP search, piano-roll ghost preview,
`match_reference_loudness`, analyze_audio depth (M4.3).

Last updated: 2026-07-18 (M4.2 tasks + pr-fast CI).
