# Stem — The Cursor of Music Production

> **Detailed execution path + testing harness:** see [`EXECUTION_PLAN.md`](./EXECUTION_PLAN.md)
> (milestones M0–M5, ordered steps, dogfood gates, layered CI harness).

**One-line vision:** What Cursor did to VS Code, Stem does to Ardour — a pro-grade,
plugin-hosting DAW with a native AI agent that can do anything a producer can do,
faster: place notes, find chords, design sounds, dig samples, generate audio, mix.

**Core strategic inversion vs. the old plan:** the old plan rewrote the UI first
(Phases 1–3) and added AI last (Phase 4). That's backwards. The agent is the
product; the UI is polish. Cursor shipped on stock VS Code chrome. We ship the
agent against stock Ardour first, prove the magic, then earn the right to touch UI.

---

## Architecture

```
┌────────────────────────────────────────────────────────────┐
│  Agent Sidecar (Python or TypeScript, separate process)    │
│  • Claude API (agent loop, streaming, tool use)            │
│  • Tool registry (typed, validated, undoable)              │
│  • Music-theory engine (ported from ai-music-daw)          │
│  • Sample-library indexer (embeddings + audio features)    │
│  • Generation services (ACE-Step local, Suno API)          │
├────────────────────────────────────────────────────────────┤
│  Bridge Layer                                              │
│  • Ardour Lua API  — session read/write, MIDI, plugins     │
│  • OSC             — transport, mixer, real-time control   │
│  • Session XML     — offline deep reads (.ardour files)    │
├────────────────────────────────────────────────────────────┤
│  Ardour (stock at first; fork patches only when forced)    │
│  • Battle-tested engine, VST3/AU/LV2/LADSPA hosting        │
│  • Recording, automation, routing, export — all free       │
└────────────────────────────────────────────────────────────┘
```

**Why sidecar, not embedded:** iterate on the agent in Python/TS at AI-speed
without touching C++ or rebuilding Ardour; survive Ardour upgrades; swap models
freely; the same agent core later embeds into a chat panel when we fork.

---

## The Agent's Toolbox (the actual product)

Every tool is: typed schema → validation → execute via bridge → verify →
**undoable** (single most important trust feature; map every mutation to
Ardour's undo stack or snapshot before acting).

**Read (context = the "codebase" the agent sees):**
- `get_session_overview` — tempo, key, tracks, regions, markers, plugin chains
- `get_midi_region(region_id)` — notes as structured data
- `get_mixer_state` — levels, pans, sends, routing
- `analyze_audio(region_id)` — key/BPM/transient/spectral analysis
- `get_playhead / get_selection` — what the user is looking at *right now*

**Write (MIDI & arrangement):**
- `insert_midi_notes(track, notes[])` / `edit_midi_notes` / `quantize`
- `create_track(type, name)` / `duplicate_region` / `move_region`
- `set_tempo / set_meter / add_marker`

**Music intelligence (deterministic — the theory engine, so no hallucinated notes):**
- `suggest_chords(key, genre, mood)` → real voicings placed as MIDI
- `generate_pattern(style, role)` — drums/bass/melody (port from ai-music-daw)
- `detect_key / detect_chords` from existing material

**Sound & samples:**
- `search_samples(query)` — semantic search over the user's own library
  (CLAP-style audio embeddings + text; "find me a darker snare like this one")
- `generate_sample(prompt)` — ACE-Step locally, drop result on a track
- `load_plugin(track, plugin)` / `set_plugin_param` / `save_preset`

**Mix:**
- `set_volume/pan/send`, `add_eq_move`, `match_loudness(reference)`

---

## Phases

### Phase 0 — Bridge proof (days)
Stock Ardour + Lua/OSC. One end-to-end demo: agent reads session, places a
chord progression in key on a new MIDI track, user hits play. **This is the
"hello world" that validates everything.** Kill criterion: if Lua/OSC can't
do round-trip session manipulation reliably, pivot to fork-with-IPC immediately.

### Phase 1 — Agent MVP (weeks 1–3)
- Agent loop (Claude API, streaming, multi-step tool use)
- Read tools + MIDI write tools + theory engine port
- Undo integration for every mutation
- Interface: floating chat window (Tauri/Electron sidecar) alongside Ardour
- Dogfood target: produce one full beat using only chat commands

### Phase 2 — Sound superpowers (weeks 4–7)
- Sample library indexing + semantic search
- ACE-Step generation-to-track pipeline
- Plugin loading + parameter control (this is where "any plugin they need" pays off)
- Voice input (the Cursor-Tab equivalent: producer keeps hands on keys, talks)

### Phase 3 — Embed (weeks 8–12) — *first time we touch Ardour C++*
- Fork: dockable agent panel inside Ardour (extend existing GTK2 UI; do NOT
  rewrite to GTK4 — zero user value, infinite cost)
- In-context actions: right-click region → "Ask Stem", selection-aware prompts
- Inline diff-style preview: agent proposes notes ghosted in the piano roll,
  user accepts/rejects (the Cursor diff-review experience, for music)

### Phase 4 — The moat (months 3–6)
- Project memory: agent learns the producer's taste, sample picks, mix habits
- Multi-step autonomous tasks: "rough-mix this session", "arrange this loop
  into a full track structure"
- Mix/master analysis vs. reference tracks
- Preset/sound-pack marketplace hooks; collaboration

---

## What we salvage from prior work
- `ai-music-daw/backend/midi/` — scales, chords, progressions, pattern makers → agent tools
- `ai-music-daw/backend/services/` — ACE-Step + Suno wrappers → generation tools
- `gtk4_ardour/` — **archive it.** 311 lines, hello-world window; the GTK4
  direction is explicitly abandoned (Phase 3 extends GTK2 instead)
- The old README's IPC/command-registry/undo concepts — kept, but in the sidecar

## Honest risks
| Risk | Mitigation |
|---|---|
| Ardour Lua API gaps (some ops UI-only) | Phase 0 kill criterion; fork patches expose missing ops via Lua, upstream them |
| Latency: chat feels slow vs. just doing it | Streaming, fast model for small ops, deterministic tools do the heavy lifting |
| GPL (Ardour is GPL2+) | Fork stays GPL/open; sidecar agent is a separate process/product — same split Cursor uses conceptually |
| "Pros won't trust AI in their session" | Undo-everything + preview-before-apply; agent never destructively edits |
| ACE-Step quality vs. Suno | Both wired; local-first, API for quality |

## Success metric per phase
- P0: chord progression placed via chat, playable, undoable
- P1: full beat produced via chat only
- P2: "find me a sound like X" returns the right sample in <5s
- P3: a producer who has never seen it says "wait, do that again"

---

## Implementation status (sprint of 2026-06-11)

Phase 0/1 build lives in this repo (~/Desktop/stem), agent-first as planned:
- ✅ Bridge protocol + MockBridge (full emulator) + ArdourBridge (OSC + Lua RPC)
- ✅ 18 typed tools: session read/write, MIDI, theory (progressions/chords/
  scales/drums/basslines), transport, undo, generation
- ✅ Claude agent loop + CLI chat shell
- ✅ 8-test suite incl. the Phase 0 milestone end-to-end (mock)
- ⏳ Live Ardour verification blocked: Ardour not installed (see GAPS.md)
- ⏳ Phase 2 (sample search, plugin control): interfaces designed, not built
- 🗄 gtk4_ardour hello-world: archived/ignored per plan
