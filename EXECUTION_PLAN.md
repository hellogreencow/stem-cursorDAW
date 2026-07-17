# Stem — Execution Plan to Cursor-for-DAW

**Intent:** Get Stem from a working Phase 0/1 agent spine to a trustworthy, dogfoodable “Cursor of music production.”  
**Stakes:** Without a sequenced path and a harness that can falsify regressions, we ship vibes, not leverage.  
**Success criteria:**
1. A producer can make a full beat (MIDI + audio + basic mix) via chat/StemScript against live Ardour, with undo everywhere.
2. Every mutating capability has an automated proof (mock + contract + optional live).
3. “Done” is measurable per milestone — no phase advances on narrative alone.

**North-star definition of “there”:**  
Stem is the agent layer producers trust the way Cursor is trusted on code: session-aware, tool-precise, undoable, previewable, fast enough to stay in flow. Ardour remains the engine; Stem remains the sidecar-first product (embed later, never rewrite the DAW).

---

## 0. Current baseline (do not re-litigate)

### Already real
- Bridge protocol + `MockBridge` + `ArdourBridge` (OSC + Lua JSON-RPC file mailbox)
- Live Phase 0 proven on self-built Ardour 9: create MIDI track → in-key progression → verify → undo
- Typed tool registry (pydantic), agent loop, multi-provider LLM support
- Theory engine (scales/chords/progressions/drums/bass)
- StemScript language + CLI + optional web UI + in-Ardour panel daemon
- Generation hooks (ACE-Step / Suno / ElevenLabs) with partial tests
- Existing pytest suite: vertical slice, providers, local fallback, StemScript, bridge contracts, generation, webserver

### Explicitly not “there” yet
- Semantic sample search, real plugin load/param control
- Cursor-like preview/accept on piano roll
- Project memory / autonomous mix-arrange
- Hard CI gate that includes live Ardour (or a faithful substitute)
- Production packaging story beyond developer install

### Non-goals (protect focus)
- Rewriting Ardour UI to GTK4 / custom DAW chrome
- Replacing Ardour’s audio engine
- Building a marketplace before dogfood reliability

---

## 1. Operating principles

1. **Agent is the product; UI is earned.** Ship capability through tools + bridge first.
2. **Every mutation is undoable** (action_id → bridge undo or Ardour undo). No silent writes.
3. **Deterministic music logic stays local** (theory engine). LLMs choose; they do not invent pitches unchecked.
4. **Mock is the unit truth; live is the integration truth.** Contracts catch Lua drift.
5. **Kill criteria are real.** If a path fails its kill test, pivot (fork IPC / narrow API surface) — do not paper over.
6. **One dogfood metric per phase.** If a human cannot complete the metric, the phase is open.

---

## 2. Milestone map (ordered)

```
M0  Harden foundation          → reliable spine on mock + live smoke
M1  Complete Producer MVP      → full beat via chat only (live)
M2  Sound superpowers          → samples + plugins + generation reliability
M3  Cursor-feel embed          → panel + selection + preview/accept
M4  Moat                       → memory + autonomous multi-step tasks
M5  Ship harness & release     → CI matrix + release gates (this doc §8–9)
```

Each milestone below lists: goal, prerequisites, ordered steps, acceptance tests, kill/pivot.

---

## 3. M0 — Harden foundation

**Goal:** Make today’s spine boringly reliable before adding surface area.

### Prerequisites
- Dev machine with Ardour 8/9 (binary or self-built fork)
- Python venv from `requirements.txt`
- At least one LLM key **or** willingness to dogfood via local fallback + StemScript
- Bridge scripts installed (`stem_bridge.lua` / `bridge_impl.lua`)

### Steps

#### M0.1 Inventory & truth sync
1. Freeze a **capability matrix** (tool × mock × live × tested) in `docs/capability-matrix.md` (or appendix in this file until created).
2. Reconcile README / PLAN / GAPS with reality (tempo/sample_rate cosmetic bugs, import_audio status, generation backends).
3. Tag baseline commit as `baseline-m0`.

#### M0.2 Bridge contract freeze
1. Enumerate every JSON-RPC method the Python `ArdourBridge` calls.
2. For each method, document Lua entrypoint, args, return shape, undo behavior.
3. Extend `tests/test_bridge_impl_contract.py` so **string/AST contracts** fail if a method disappears or undo path regresses.
4. Add a **schema fixture** (`tests/fixtures/bridge_rpc_schema.json`) listing request/response shapes.

#### M0.3 Mock fidelity pass
1. Diff `Bridge` ABC methods vs `MockBridge` vs `ArdourBridge` — close any gap where mock lies.
2. Ensure mock undo semantics match live for: create track, insert notes, set tempo, import audio, mute/gain.
3. Add property-style tests: random sequences of N mutations then undo-all → empty/equivalent session.

#### M0.4 Live smoke (manual → scripted)
1. Script `scripts/live_smoke.sh`: start assumptions documented; run Phase 0 milestone against live bridge; print pass/fail JSON.
2. Fix remaining known API wrong-reads (tempo `inf`, sample_rate int64-max) or quarantine them behind explicit “untrusted fields.”
3. Record golden session XML / note dumps for two keys (C major, F minor).

#### M0.5 Agent loop hardening
1. Golden transcript tests with `ScriptedProvider` for: progression, drums+bass, undo, validation errors.
2. Assert step-cap, tool-error surfacing, history normalization (already partially covered — extend to failure modes).
3. Provider matrix smoke: anthropic / openai / openrouter / custom — config resolution only in CI; one live provider optional nightly.

### M0 acceptance
- [ ] `pytest -q` green on clean clone
- [ ] Live smoke script passes on reference Ardour once
- [ ] Capability matrix checked in
- [ ] No mutating tool without `action_id` in happy path (enforced by registry warning → test fails if warning present)

### M0 kill/pivot
If live Lua cannot create/edit MIDI regions reliably after focused fixes: **fork Ardour with thin IPC** exposing the missing ops; keep Python agent unchanged.

---

## 4. M1 — Complete Producer MVP (“full beat via chat”)

**Goal:** Dogfood metric from PLAN Phase 1: produce one full beat using only chat/StemScript.

### Definition of a “full beat” (acceptance artifact)
A session containing, at minimum:
- Tempo + key set
- Drum pattern (audible/MIDI)
- Bassline following harmony
- Chord or stab layer
- Optional lead motif
- Transport play works
- Entire construction undoable step-by-step
- Export or bounce path documented (even if manual File→Export for now)

### Steps

#### M1.1 Tool completeness for beatcraft
1. Audit tools vs beat recipe; add only what’s missing:
   - region move/duplicate (if not present)
   - quantize / simple note edit
   - track naming / ordering helpers as needed
2. `make_tracks_audible` / instrument attach: ensure mock + live both produce hearable MIDI (soft synth or GM path documented).
3. Markers for section labels (intro/verse) — already have `add_marker`; wire into local fallback + StemScript.

#### M1.2 Arrangement primitives
1. StemScript: support multi-section scripts (repeat bars, scene markers).
2. Local fallback: one canonical “build me a beat in X” path that is deterministic and tested.
3. Agent system prompt: constrained recipe for beat-building (tools-first, verify notes in-key).

#### M1.3 Audibility & diagnose loop
1. Harden `diagnose_audio` + `make_tracks_audible` against live.
2. Add tool: `get_mixer_state` (read) if missing — levels/mute/solo snapshot.
3. Acceptance: after beat build, `diagnose_audio` reports no obvious silence causes (muted, −inf gain, no instrument).

#### M1.4 Dogfood loop
1. Produce **Beat A** (house) and **Beat B** (hip-hop) via chat only; save `.ardour` + notes dump under `examples/dogfood/`.
2. Timebox: if >N minutes of agent thrash, file tool gap issues — do not “prompt harder.”
3. Capture transcripts as golden fixtures (redact keys).

### M1 acceptance
- [ ] Two dogfood beats reproducibly built via `--script` or chat against mock
- [ ] At least one beat reproduced against live Ardour
- [ ] Undo from final state back to empty (or pre-beat) session
- [ ] New tests: `tests/test_beat_mvp.py` covering the recipe end-to-end on mock

### M1 kill/pivot
If hearability depends on proprietary plugins unavailable in CI: ship a **documented stock instrument path** (FluidSynth/SF2 or Ardour built-in) and treat third-party plugins as M2.

---

## 5. M2 — Sound superpowers

**Goal:** PLAN Phase 2 — samples, generation-to-track reliability, plugin control.

### Steps

#### M2.1 Generation reliability
1. Unify generation backends behind one interface with explicit status: `available | misconfigured | failed`.
2. ACE-Step: document model path; add offline fixture mode that injects a tiny wav for CI.
3. ElevenLabs / Suno: record/replay HTTP cassettes (VCR-style) for CI; live calls nightly/manual.
4. Stem separation paths: golden wav fixtures; assert track counts and naming.
5. Always import at session sample rate (tests already cover resample — extend edge cases).

#### M2.2 Sample library index (semantic search)
1. Design index format: path, duration, BPM/key if known, embedding ref, tags.
2. MVP search: **metadata + simple audio features** before full CLAP embeddings.
3. Tool: `search_samples(query, limit)` → results; `import_sample(path, track?, position)`.
4. Indexer CLI: `python -m stem.services.index_samples ~/Samples`.
5. Tests: fixture library of 5–10 short wavs; query returns expected id.

#### M2.3 Plugin control
1. Live spike: list plugins, load onto track, set one parameter, undo.
2. Tools: `list_plugins`, `load_plugin`, `set_plugin_param`, `get_plugin_params`.
3. Contract tests for Lua plugin APIs; mock implements in-memory plugin graph.
4. Preset save/load: only after load/param are solid.

#### M2.4 Voice input (optional stretch)
1. Push-to-talk in panel/CLI that transcribes to the same agent inbox.
2. Not blocking for M2 exit; track as M2.4 stretch.

### M2 acceptance
- [ ] `generate_*` tools pass cassette + fixture CI without real API keys
- [ ] `search_samples` returns ranked hits on fixture library
- [ ] Plugin load + one param change works live once; mock+contract covered
- [ ] Dogfood: “find a snare / generate a pad / drop on timeline” in one session

### M2 kill/pivot
If Lua plugin API is insufficient: expose minimal plugin ops via Ardour fork IPC; keep tool schemas stable.

---

## 6. M3 — Cursor-feel embed

**Goal:** PLAN Phase 3 — agent feels native; selection-aware; preview/accept for notes.

### Steps

#### M3.1 Native panel productization
1. Stabilize embedded agent daemon lifecycle (launch, restart, trust flags).
2. History sync, quick actions, library browser — keep panel from resizing Ardour (contracts exist; keep green).
3. Packaging: macOS embedded Python runtime path tested in CI (string/contract + smoke if available).

#### M3.2 Selection & context
1. Tools: `get_playhead`, `get_selection` (tracks/regions/time range).
2. Agent prompt injects “what user is looking at.”
3. Right-click / action hook: “Ask Stem about selection” (fork UI patch as needed).

#### M3.3 Preview / accept (the Cursor diff analog)
1. Define **Proposal** object: intended note/region mutations not yet committed.
2. Bridge support: ghost notes or secondary playlist layer **or** sidecar preview buffer if Ardour cannot ghost easily.
3. UI: Accept / Reject / Accept-hunk in panel.
4. Tests: proposal apply/reject/undo on mock; live optional.

#### M3.4 Latency budget
1. Stream tool traces to panel.
2. Fast-path local intents remain for common recipes.
3. Measure p50/p95 “user send → first mutation” on mock; set budget targets.

### M3 acceptance
- [ ] Selection-aware prompt proven in a scripted agent test
- [ ] Preview/accept for MIDI notes works on mock; demoed live once
- [ ] Panel dogfood: full beat without external CLI
- [ ] “Producer who hasn’t seen it says do that again” — qualitative gate with 2 external testers

### M3 kill/pivot
If ghost notes are impossible without deep editor work: ship **diff list in panel** (textual/structured proposal) first; ghost later.

---

## 7. M4 — Moat

**Goal:** PLAN Phase 4 — memory + autonomy that compounds.

### Steps

#### M4.1 Project memory
1. Store per-project: preferred keys/tempos, sample picks, plugin chains, mix habits (`~/.stem/memory/<project-id>.json`).
2. Retrieve into agent context with strict size caps.
3. Tests: memory write/read/redaction; no secrets in memory files.

#### M4.2 Autonomous multi-step tasks
1. Task modes: `rough_mix`, `arrange_loop_to_song`, `match_reference_loudness` (incremental).
2. Each mode = planner prompt + allowed tool subset + verification checklist.
3. Hard step caps + mandatory user confirm for destructive ops.

#### M4.3 Reference / analysis
1. `analyze_audio` MVP: loudness, rough key/BPM (reuse existing libs carefully).
2. Compare vs reference track report (non-mutating first).

#### M4.4 Collaboration hooks (late)
1. Export StemScript + session notes as shareable recipe.
2. Marketplace explicitly **after** reliability — stub interfaces only.

### M4 acceptance
- [ ] Memory improves second-session defaults in a scripted test
- [ ] One autonomous task (`arrange_loop_to_song` or `rough_mix`) completes on mock with verification checklist
- [ ] Destructive actions require confirm; covered by test

---

## 8. Testing harness (thorough, layered, ship-blocking)

This is the end-state harness. Build it incrementally; **M5 makes it the release gate.**

### 8.1 Design goals
- Fast feedback on every PR (`< 2 minutes` default suite)
- High confidence on music correctness (pitches, undo, imports)
- Live Ardour optional in PR, mandatory in release/nightly when hardware available
- Deterministic fixtures; no network in default CI
- Clear failure taxonomy: theory / tool / bridge-contract / live / agent / generation / UI

### 8.2 Layer cake

```
L0  Unit            theory, StemScript parse, provider config, pure helpers
L1  Tool            registry validation + MockBridge mutations/undo
L2  Agent           ScriptedProvider loops, local fallback intents, history
L3  Contract        Lua/panel/packaging string+schema contracts (no Ardour process)
L4  Generation      fixture wav + HTTP cassettes; never live keys in PR CI
L5  Integration     webserver/daemon job lifecycle against mock
L6  Live            real Ardour bridge (nightly / pre-release / manual)
L7  Dogfood         golden beats + transcripts; human checklist
L8  Perf/soak       long mutation sequences, undo stacks, mailbox races
```

### 8.3 Directory layout (target)

```
tests/
  unit/                 # L0
  tools/                # L1 (migrate vertical_slice here over time)
  agent/                # L2
  contract/             # L3 (bridge_impl, panel, packaging)
  generation/           # L4
  integration/          # L5
  live/                 # L6 (skip unless STEM_LIVE=1)
  dogfood/              # L7 scripts + optional pytest markers
  soak/                 # L8
  fixtures/
    audio/              # tiny wav/flac
    cassettes/          # HTTP replays
    sessions/           # expected note dumps / overview JSON
    transcripts/        # scripted agent dialogues
    samples_lib/        # mini library for search
  harness/
    asserts_music.py    # diatonicity, chord tone helpers
    bridge_factory.py
    scripted_provider.py
    live_gate.py
```

### 8.4 Markers & commands

```bash
# Default PR gate (no network, no Ardour)
pytest -q -m "not live and not nightly"

# Full local
pytest -q

# Live Ardour
STEM_LIVE=1 pytest -q -m live

# Nightly (cassettes refresh optional, live if runner has Ardour)
pytest -q -m "nightly or live"
```

Suggested `pytest.ini` markers: `live`, `nightly`, `slow`, `generation`, `dogfood`.

### 8.5 Required harness capabilities (build these)

#### H1 Music assertions library
- `assert_diatonic(notes, key, scale)`
- `assert_chord_at(notes, beat, pitches)`
- `assert_undo_clears(bridge, track_id, action_id)`
- `assert_session_equiv(a, b)` for overview+notes

#### H2 Tool exercise matrix
- Auto-discover registered tools; ensure each has:
  - schema rejection test (garbage input)
  - happy-path mock test **or** explicit `xfail`/`skip` with ticket ref
- Fail CI if a new `@registry.register` has zero coverage (coverage map file or test plugin).

#### H3 Bridge fuzzer (soak)
- Random valid tool sequences (length 50–200) on MockBridge
- Invariants: undo stack coherence; no crash; overview JSON serializable; note pitches in 0–127

#### H4 Live harness
- Preflight: bridge ping, session writable, sample rate known
- Run Phase 0 + Beat MVP subset
- Artifact dir: `artifacts/live/<timestamp>/` with request/response logs, note dumps
- Hard timeout + GUI non-block check (bridge must stay on LuaTimerDS)

#### H5 Agent golden transcripts
- YAML/JSON dialogues: user → expected tool names (ordered or partial order) → final predicates on session
- Run via ScriptedProvider; also “LLM-free” local fallback goldens

#### H6 Generation sandbox
- Missing API keys → structured error (assert message quality)
- With cassettes → import track count / naming / resample
- Virus/size guards: reject absurd durations already in schema; keep tested

#### H7 Panel / daemon
- Mailbox protocol round-trip without Ardour (temp dirs)
- Restart safety: duplicate daemon, stale response files
- Contract tests remain the PR gate for Lua UI strings

#### H8 CI matrix
| Job | Runs | Blocks merge |
|---|---|---|
| `pr-fast` | L0–L5, not live/nightly | Yes |
| `pr-contract` | L3 | Yes |
| `nightly` | L4 refresh optional + L6 if self-hosted runner | No (alerts) |
| `release` | L0–L7 checklist + live smoke signed off | Yes for tag |

### 8.6 Flake & determinism rules
1. No wall-clock sleeps for sync except live; prefer polling with timeout.
2. No real network in `pr-fast`.
3. Seeds fixed for fuzz tests; print seed on failure.
4. Temp dirs via `tmp_path`; never write to `~/.stem` in unit tests (point `STEM_HOME` to temp).

### 8.7 Coverage targets (pragmatic, not vanity)
- Theory + tools + agent loop: **≥ 90% line** of those packages
- Bridge Python: **≥ 85%** with mock
- Lua: contract coverage of every RPC method name + undo path (not line%)
- Overall repo: track trend; do not block on chasing web HTML CSS

### 8.8 Harness build sequence (implementation order)
1. Add `pytest.ini` markers + `STEM_HOME` isolation fixture (global conftest).
2. Extract `tests/harness/asserts_music.py` from vertical slice helpers.
3. Tool coverage map gate.
4. Migrate/expand goldens for beat MVP.
5. Generation cassettes + fixture wavs.
6. Fuzzer soak (nightly).
7. Live runner script + pytest `live` module.
8. CI workflows (`pr-fast`, `nightly`).
9. Release checklist doc linking harness jobs to M0–M4 acceptances.

---

## 9. M5 — Ship gate (“on paper → on the clock”)

**Goal:** The plan is executable as a release process, not a wish list.

### Steps
1. Implement harness §8.8 items 1–5 before large M2 feature work lands.
2. Wire GitHub Actions (or local `make test`) for `pr-fast`.
3. Maintain `CHANGELOG.md` per milestone.
4. Release candidate checklist:
   - [ ] `pr-fast` green
   - [ ] Live smoke signed (human or self-hosted)
   - [ ] Dogfood beats A/B reproducible from scripts
   - [ ] Capability matrix updated
   - [ ] Known gaps filed, none unmarked critical
5. Tag `v0.x.0` only when M1 acceptances hold; `v0.y.0` for M2, etc.

---

## 10. Workstream graph (parallelism)

```
Foundation (M0) ─────────────────────────────┐
   │                                         │
   ├─ Beat MVP tools (M1) ──── Dogfood A/B ──┼─→ M5 gates
   │                                         │
   ├─ Harness core (H1–H3, CI) ──────────────┤
   │                                         │
   └─ Generation fixtures (H6) ─ M2 gen ─────┤
                                              │
Samples index (M2) ──┐                        │
Plugin spike (M2) ───┴─→ M2 accept ───────────┤
                                              │
Panel/selection (M3) ─ Preview ─→ M3 accept ──┤
Memory/tasks (M4) ──────────────→ M4 accept ──┘
```

**Rule:** Harness core is not a trailing cleanup — it runs in parallel with M1 and gates M2+.

---

## 11. Novelty options (pick deliberately)

| Variant | Idea | When to use |
|---|---|---|
| **Minimalist** | Only M0+M1+harness L0–L5; defer plugins/search | Fastest path to believable demo |
| **Orthogonal** | Treat StemScript as the primary API; LLM as compiler to StemScript | Cuts agent flakiness; stronger goldens |
| **Edge** | “Session as git”: snapshot every mutation as diffable JSON; accept/reject = apply patch | Supercharges Cursor analogy; more engineering |

Default recommendation: **Minimalist to finish M1**, adopt **Orthogonal StemScript goldens** in the harness, keep **Edge session-diff** as M3 experiment.

---

## 12. Risk register (active)

| Risk | Signal | Mitigation |
|---|---|---|
| Lua API drift across Ardour versions | Contract tests fail / live smoke fails | Pin supported Ardour; contract fixtures per version |
| Agent nondeterminism | Dogfood thrash | Local fallback + StemScript goldens; tighten tool prompts |
| Generation cost/flakes | CI red or $$ | Cassettes + fixtures; live keys only nightly |
| Plugin surface infinite | Scope creep | One synth + one effect path first |
| Undo mistrust | User stops using agent | Undo invariants in fuzzer; never mutate without action_id |
| GPL boundary confusion | Distribution risk | Keep sidecar separate; document license split |

---

## 13. Immediate next actions (first concrete slice)

Do these in order this week-shaped engineering slice (effort in subsystems, not calendar):

1. **`tests/conftest.py`**: isolate `STEM_HOME`; markers; music assert helpers.
2. **Capability matrix** checked in (tool × mock/live/test).
3. **`tests/test_beat_mvp.py`**: house beat recipe on MockBridge + StemScript twin.
4. **Live smoke script** + fix untrusted tempo/sample_rate reads (or mark untrusted).
5. **Tool coverage gate** so new tools cannot land bare.
6. Only then start M2 sample index MVP.

---

## 14. Traceability back to PLAN.md

| PLAN phase | This doc | Exit artifact |
|---|---|---|
| Phase 0 bridge proof | M0 (+ already largely done) | Live smoke green |
| Phase 1 agent MVP | M1 | Full beat chat-only |
| Phase 2 sound | M2 | Samples + plugins + gen CI |
| Phase 3 embed | M3 | Preview/accept + panel dogfood |
| Phase 4 moat | M4 | Memory + one autonomous task |
| (implicit ship) | M5 + §8 harness | Release checklist |

---

## Rigid Self-Check
- **Why this?** Converts vision into ordered, falsifiable work with a harness that can say “no.”
- **What it changes?** Progress becomes milestone evidence (beats, contracts, CI) instead of accumulated files.
- **Humanity Furtherance:** +1 — raises creative agency with accountable engineering.
- **First-Principles Trace:** (1) undoable tools over UI, (2) mock+live dual truth, (3) dogfood beats as the unit of done.
- **Next Bold Variant:** Session-as-git proposals (Edge variant) prototyped behind a flag during M3.
