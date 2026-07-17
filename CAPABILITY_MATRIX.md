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

\* Bridge ABC default / mock does not fully emulate engine audio.  
† Import path works on mock; generation backend may be stubbed in tests.

**Not started (Phase 2+):** `search_samples`, `load_plugin`, `set_plugin_param`,
`get_playhead`, `get_selection`, proposal/preview accept.

Last updated: 2026-07-17 (M0 harness slice).
