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

1. Install Ardour 8.x (https://ardour.org — or build from the fork in
   `~/Desktop/ardour-ai`).
2. Enable OSC: Preferences → Control Surfaces → Open Sound Control (port 3819).
3. Copy the bridge script and start it:
   ```bash
   cp stem/bridge/stem_bridge.lua ~/Library/Preferences/Ardour8/scripts/
   ```
   In Ardour: Edit → Lua Scripts → Stem Agent Bridge.
4. `./venv/bin/python -m stem.cli` — it will detect the live bridge.

The bridge speaks file-based JSON-RPC at `~/.stem/{request,response}.json`
(simple, dependency-free inside Ardour's Lua sandbox) plus OSC for transport.

## Audio generation

`generate_sample` uses ACE-Step (local) or Suno (API). Point it at the old
project's models to avoid re-downloading 9.4GB:

```bash
export ACE_STEP_DIR=~/Desktop/ai-music-daw   # has models/ + acestep-env/
# or: export SUNO_API_KEY=...
```

## Tests

```bash
./venv/bin/python -m pytest tests/ -q   # 8 tests, includes the Phase 0 milestone
```

## Layout

```
stem/
  bridge/    base.py (protocol) · mock.py · ardour.py · stem_bridge.lua
  theory/    deterministic music theory (ported from ai-music-daw)
  tools/     typed tool registry — the agent's hands
  agent/     Claude tool-use loop
  services/  ACE-Step / Suno generation
  cli.py     minimal chat shell
tests/       vertical-slice + validation tests
```

See PLAN.md for the roadmap and GAPS.md for known unknowns.
