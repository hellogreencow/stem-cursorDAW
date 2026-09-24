# Stem — AI Producer Agent for Ardour

The Cursor of music production: a sidecar AI agent that reads and edits a
real Ardour session — places notes, builds chord progressions, programs
drums, controls the mixer — with every action undoable.

## Status

Working today: the complete agent stack against an in-memory session emulator,
plus the native Ardour bridge used by the bundled Stem panel. Core generation
flows, deterministic local fallbacks, StemScript, validation, undo, and the CLI
are covered by tests.

## Setup

```bash
cd ~/Desktop/stem
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
```

## Providers

Stem works with any LLM provider. Pick one:

```bash
export ANTHROPIC_API_KEY=sk-ant-...                  # default (Claude)
# or
export STEM_PROVIDER=openai OPENAI_API_KEY=sk-...
# or
export STEM_PROVIDER=openrouter OPENROUTER_API_KEY=sk-or-... \
       STEM_MODEL=anthropic/claude-sonnet-4.6        # any OpenRouter model
# or any OpenAI-compatible endpoint (Ollama, Groq, vLLM...):
export STEM_PROVIDER=custom STEM_BASE_URL=http://localhost:11434/v1 \
       STEM_MODEL=llama3
```

Or persist it in `~/.stem/config.json`:
```json
{"provider": "openrouter", "model": "anthropic/claude-sonnet-4.6", "api_key": "sk-or-..."}
```

CLI flags override everything: `--provider`, `--model`, `--api-key`, `--base-url`.

## Run

```bash
./venv/bin/python -m stem.cli --mock     # try it now, no Ardour needed
./venv/bin/python -m stem.cli            # auto-detects live Ardour
./venv/bin/python -m stem.cli --ui       # command-launched Apple-inspired UI
./venv/bin/python -m stem.cli --mock --script examples/party_anthem.stem
```

Example session:
```
you> set the tempo to 90 and lay down a dreamy progression in F, then play it
  ⚙ get_session_overview({})
  ⚙ set_tempo({'bpm': 90})
  ⚙ create_midi_track({'name': 'Dream Chords'})
  ⚙ insert_chord_progression({'track_id': ..., 'key': 'F', 'progression': 'I_V_vi_IV'})
  ⚙ transport_play({})
stem> Done — F, C, Dm, Bb at 90bpm, playing now. Say "undo" to revert.
```

## StemScript

StemScript is the small production language that maps directly to undoable
Ardour actions:

```text
stem:
tempo 128
key D minor
duration 60s
chords Dm Bb C Dm
drums four_on_floor
bass driving
lead chant
```

The same script can be pasted into the native Stem panel, launched from the
CLI with `--script`, or run from the web command UI.

## Connecting to real Ardour

### Which Ardour you need

Stem's bridge calls three Lua helpers that **do not exist in any released
Ardour**: `ARDOUR.LuaAPI.ensure_midi_region`, `ARDOUR.LuaAPI.set_session_tempo`
and `ARDOUR.LuaAPI.import_audio_file`. They are additions in the private
`ardour-ai` fork. This was established by dumping the whole `ARDOUR.LuaAPI`
namespace in the 8.12 and 9.8 source trees — see `BRIDGE_AUDIT.md` for the
file:line evidence.

The bridge now tries each fork helper first and falls back to a documented
stock-Ardour path, so it is no longer fork-only. What that buys you, as of
2026-09-21:

| Build | Tempo | MIDI notes | Audio import | Markers, mixer, transport |
|---|---|---|---|---|
| `ardour-ai` fork | ✅ native helper | ✅ native helper | ✅ native helper | ✅ |
| Stock Ardour 9.x | ✅ TempoMap transaction | ✅ via `MidiTimeAxisView:add_region` | ✅ via `Editor:do_import` | ✅ |
| Stock Ardour 8.x | ✅ TempoMap transaction | ⚠️ only into a region you drew by hand | ✅ via `Editor:do_import` | ✅ |

The Ardour 8 limitation is real and not a bug in Stem: 8.x has no
`MidiTimeAxisView` Lua binding at all, and `RegionFactory` exposes only
`region_by_id` / `regions` / `clone_region`, so an empty MIDI region cannot be
created from Lua. Draw one empty MIDI region on the track by hand once and Stem
will reuse and extend it; otherwise `insert_midi_notes` returns an error saying
exactly this. Everything else on 8.x works.

`ping` reports the capabilities of whatever build you are on, so the agent can
tell you what it cannot do before it promises it.

> **This table has a date on it for a reason.** A patch adding those three
> functions to upstream Ardour is being prepared separately. If it is merged
> and released, stock Ardour from that release onward moves to the fork's row
> and the fork stops being necessary — update this table with the version
> number when that happens, and check `ping`'s `caps` output on the new build
> rather than assuming.

### Setup

1. Install Ardour — **9.x for the full stock experience**, 8.x with the MIDI
   caveat above, or build the `ardour-ai` fork.
2. Enable OSC: Preferences → Control Surfaces → Open Sound Control (port 3819).
3. Copy the bridge script and start it:
   ```bash
   cp stem/bridge/stem_bridge.lua ~/Library/Preferences/Ardour9/scripts/
   ```
   (`Ardour8/scripts` on 8.x.) In Ardour: Edit → Lua Scripts → Stem Agent Bridge.
4. `./venv/bin/python -m stem.cli` — it will detect the live bridge.

The bridge speaks file-based JSON-RPC at `~/.stem/{request,response}.json`
(simple, dependency-free inside Ardour's Lua sandbox) plus OSC for transport.
`stem_bridge.lua` is a thin loader; the implementation is `bridge_impl.lua`,
which it compiles once and then runs on each `LuaTimerDS` tick.

## Audio generation

`generate_sample` uses ACE-Step (local) or Suno (API). Point it at the old
project's models to avoid re-downloading 9.4GB:

```bash
export ACE_STEP_DIR=~/Desktop/ai-music-daw   # has models/ + acestep-env/
# or: export SUNO_API_KEY=...
```

## Tests

```bash
./venv/bin/python -m pytest tests/ -q
```

104 tests. The bridge tests do not need Ardour: `tests/lua/mock_ardour.lua`
provides the Ardour binding shapes and the suite executes the real
`bridge_impl.lua` against it, one dispatch tick per call, under stock-Ardour-8,
stock-Ardour-9 and fork capability profiles. Install `lua5.3` to run them
(`apt-get install lua5.3` / `brew install lua@5.3`); without a Lua interpreter
the behavioural tests skip and only their source-level counterparts run.

Seven tests assert on the private `ardour-ai` fork's own C++ sources and skip
unless a checkout is present — point `ARDOUR_AI_ROOT` at one to run them.

## Layout

```
stem/
  bridge/    base.py (protocol) · mock.py · ardour.py
             stem_bridge.lua (loader) · bridge_impl.lua (the API surface)
  theory/    deterministic music theory (ported from ai-music-daw)
  tools/     typed tool registry — the agent's hands
  agent/     Claude tool-use loop
  services/  ACE-Step / Suno generation
  cli.py     minimal chat shell
tests/       vertical-slice + validation tests
  lua/       mock Ardour environment for the bridge tests
```

See PLAN.md for the roadmap and GAPS.md for known unknowns.
