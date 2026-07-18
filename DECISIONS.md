# Stem — Decision Log (M0+)

Running log of choices, why they were made, downstream effects, and observed
results. Append-only; newest entries at the bottom.

---

## D001 — Execute M0 harness slice before new features (2026-07-17)

**Decision:** Start implementation at EXECUTION_PLAN §13 items 1–5 (conftest,
capability matrix, beat MVP test, live smoke, tool coverage gate), not M2.

**Why:** Features without a gate become unfalsifiable debt. Beat MVP + coverage
gate make “done” measurable.

**Downstream:** Sample index / plugin work waits until PR-fast harness is green.

**Result:** Branch `cursor/execution-plan-test-harness-f5d5` carries plan + M0 code.

---

## D002 — Introduce `STEM_HOME` via `stem.paths` (2026-07-17)

**Decision:** Centralize filesystem roots in `stem/paths.py` (`stem_home()`,
`config_path()`, `generated_dir()`). Honor env `STEM_HOME`; default `~/.stem`.

**Why:** Tests must not touch the developer’s real mailbox/config/generated
audio. Module-level `Path.home() / ".stem"` made isolation impossible without
monkeypatching every module.

**Downstream effects:**
- `ArdourBridge`, providers, generation, ElevenLabs, webserver, agent_daemon
  resolve paths at call time (not import time).
- Pytest autouse fixture sets `STEM_HOME` to a temp dir.
- **Lua bridge still uses `$HOME/.stem`.** Live smoke must not set `STEM_HOME`
  unless Lua is also pointed there. Documented in live smoke script.
- `test_providers` stops monkeypatching `CONFIG_PATH`; uses `STEM_HOME` instead.

**Result:** `76 passed, 7 skipped`. Provider tests use `config_path()` under
isolated `STEM_HOME`. No writes to real `~/.stem` during pytest.

---

## D003 — Quarantine absurd live tempo / sample_rate reads (2026-07-17)

**Decision:** In `ArdourBridge.get_session_overview`, replace non-finite or
out-of-range tempo/sample_rate with safe defaults and list them in
`SessionOverview.untrusted_fields`. Surface that list from `get_session_overview`
tool JSON.

**Why:** GAPS.md notes tempo `inf` and sample_rate int64-max. Silent wrong
numbers poison agent planning; crashing is worse. Explicit untrusted > fake
certainty.

**Downstream:** Agents/tools can prefer `set_tempo` when `tempo` is untrusted.
MockBridge leaves `untrusted_fields` empty. Chord placement does not depend on
these fields (unchanged).

**Result:** `tests/test_session_trust.py` proves inf / int64-max → defaults +
`untrusted_fields`. Mock omits the key (no false distrust).

---

## D004 — Tool coverage gate = name mentioned in tests/ (2026-07-17)

**Decision:** CI fails if any `@registry.register` tool name does not appear as
a string anywhere under `tests/`.

**Why:** Cheapest signal that stops bare tools landing. Stronger per-tool
happy-path maps come later (EXECUTION_PLAN H2).

**Downstream:** Adding a tool requires at least a mention (ideally a real test).
Weak false confidence if someone only adds the string — acceptable for M0;
tighten later.

**Result:** Gate green; mixer/help tools gained real tests so names aren't
string-only placeholders.

---

## D005 — Capability matrix checked in as markdown (2026-07-17)

**Decision:** `CAPABILITY_MATRIX.md` at repo root (tool × mock × live × tested).

**Why:** Single glance for “what can we claim?” Aligns README honesty with
registry reality.

**Downstream:** Must be updated when tools are added (noted in coverage test
failure message). Not auto-generated yet (avoid codegen complexity in M0).

**Result:** matrix created from registry inventory.

---

## D006 — Skip Ardour panel contract tests without checkout (2026-07-17)

**Decision:** Tests that read `ardour-ai/gtk2_ardour/stem_panel.cc` (and OSX
packaging) skip unless the file exists or `ARDOUR_AI_ROOT` points at it.
In-repo Lua `bridge_impl.lua` contracts still always run.

**Why:** This cloud/workspace clone has no sibling Ardour fork. Failing PR-fast
on missing external trees blocks harness work without improving Stem truth.

**Downstream:** CI without ardour-ai will not catch panel string regressions —
document that full contract requires `ARDOUR_AI_ROOT`. Nightly/self-hosted
should set it.

**Result:** 7 panel/osx tests skipped in this environment; in-repo Lua +
webserver contracts still run. Suite: 76 passed, 7 skipped.

---

## D007 — httpx declared in requirements (2026-07-17)

**Decision:** Add `httpx>=0.27.0` to `requirements.txt` (already imported by
ElevenLabs client).

**Why:** Fresh venv installs were incomplete; tests importing generation tools
pull httpx transitively.

**Downstream:** Install docs unchanged; pip -r requirements now matches imports.

**Result:** Applied.

---

## D008 — Sample search MVP = tokens, not embeddings (2026-07-17)

**Decision:** Ship `search_samples` / `import_sample` / `rebuild_sample_index`
with filename/path/tag token overlap scoring. Defer CLAP/embeddings.

**Why:** Unblocks “find a snare / drop it” dogfood without model downloads.
Embeddings can replace the scorer later behind the same tool schema.

**Downstream:**
- Index at `STEM_HOME/sample_index.json`
- CLI: `python -m stem.index_samples <folder>`
- Stable ids via sha1(path), not `hash()` (randomized per process)
- Fixture library under `tests/fixtures/samples_lib/`

**Result:** `80 passed, 7 skipped`. Search ranks `dark_snare_tight` for
“dark snare”; import creates audio track on mock.

---

## D009 — Plugin control: bridge methods + mock fidelity first (2026-07-17)

**Decision:** Add `list_plugins` / `load_plugin` / `get_plugin_params` /
`set_plugin_param` as concrete Bridge methods (not ABC-breaking abstracts),
with full MockBridge behavior, ArdourBridge RPC, and Lua handlers that use
`LuaAPI.new_plugin` + `set/get_processor_param` behind `pcall`.

**Why:** M2.3 spike must be dogfoodable on mock/CI; live Lua API variance is
real (GAPS.md). Tools stay stable if Lua needs fixes later.

**Downstream:**
- Tool module `stem/tools/plugin_tools.py`
- Contract tests assert Lua handler presence
- Live param semantics may need iteration on real Ardour
- Undo: mock snapshots plugin graphs; live relies on Ardour undo stack via
  existing action_id sequencing after RPC

**Result:** Mock tools green (list/load/get/set + undo + clamp). Lua handler
contract asserts present. Live dogfood still required on a real Ardour box.

---

## D010 — Generation cassettes store decoded WAV, replay skips network (2026-07-17)

**Decision:** `STEM_CASSETTE_DIR` + `STEM_CASSETTE_MODE=replay|record|off`.
Replay copies a pre-recorded WAV keyed by sha1 of the request body — no HTTP,
no ffmpeg in PR CI. Record mode (manual/nightly) writes after a live call.

**Why:** VCR-of-mp3 still needs ffmpeg decode; storing the post-decode WAV is
the smallest reliable CI surface. Matches EXECUTION_PLAN H6 intent.

**Downstream:** ElevenLabs `_compose` checks cassettes first. Tests seed
cassettes under `tests/fixtures/cassettes/`. Missing cassette in replay =
hard fail (no silent network fallback).

**Result:** Cassette replay + fixture WAV tests green without network/ffmpeg.
`available()` is true under replay mode so tools do not short-circuit.

---

## D011 — Proposals live in the sidecar, not the piano roll (yet) (2026-07-17)

**Decision:** M3 preview/accept is a Python-side proposal buffer on the Bridge
(`propose_midi_notes` → `accept_proposal` / `reject_proposal`). No Ardour
ghost-note dependency for the first cut.

**Why:** EXECUTION_PLAN kill-pivot: structured diff before editor ghosts.
Unblocks Cursor-like review on mock + live without C++ piano-roll work.

**Downstream:**
- Accept inserts via normal undoable `insert_midi_notes`
- Reject discards; pending proposals visible via `list_proposals`
- Later: ghost rendering can consume the same proposal objects
- Selection/playhead are first-class read tools for agent context

**Result:** Proposal tools green on mock; accept undoable; reject idempotent
fail-closed. Live selection Lua is best-effort / set unsupported.

---

## D012 — Soak fuzzer is PR-gated at N=80, nightly can go longer (2026-07-17)

**Decision:** Deterministic MockBridge tool fuzzer with fixed seed; default
length 80 in PR (`@pytest.mark.nightly` + `slow` for 250-step run).

**Why:** Catches undo/pitch/serialization invariants without slowing the suite
into minutes. Seed printed on failure.

**Downstream:** Invariants: pitches 0–127, overview JSON-serializable, undo
stack never crashes, action_ids unique enough, session remains coherent.

**Result:** 80-step fuzzer green in PR gate (`not nightly`).

---

## D013 — Golden transcripts are JSON, not YAML (2026-07-17)

**Decision:** H5 agent goldens live as `.json` under `tests/fixtures/transcripts/`
and run via ScriptedProvider. No PyYAML dependency.

**Why:** Keep requirements thin; JSON is enough for tool scripts + predicates.

**Downstream:** Runner asserts tool name sequence (ordered or subset), session
predicates (tempo/tracks/notes), and reply substrings. Local-fallback goldens
can be added as a second file type later.

**Result:** Three goldens green (tempo/track, selection→propose, memory
defaults). Runner supports ordered tool subsequences + session predicates.

---

## D014 — Project memory is explicit tools + system injection (2026-07-17)

**Decision:** Store `STEM_HOME/memory/<project_id>.json`. Inject a short
memory block into the agent system prompt; expose `recall_memory` /
`update_memory` tools. Redact secret-like keys on write.

**Why:** M4.1 acceptance (“second session defaults”) needs durable state without
silent telepathy. Tools make memory inspectable/testable; prompt injection
makes it ambient for the model.

**Downstream:**
- `project_id` on StemAgent (default `"default"`)
- Caps: short notes, bounded lists
- Never store API keys / tokens
- Autonomy task modes still deferred

**Result:** Second-session system prompt includes tempo/key; secrets redacted
on disk; tools wired through ToolContext.project_id.

---

## D015 — Autonomous tasks require explicit confirm=true (2026-07-18)

**Decision:** `run_task` previews a plan when `confirm` is false/omitted; executes
only when `confirm=true`. Modes: `arrange_loop_to_song`, `rough_mix`.

**Why:** M4 acceptance — destructive multi-step ops must not fire on a single
ambiguous chat turn. Matches “mandatory user confirm for destructive ops.”

**Downstream:**
- Deterministic executors (tool registry only), not free-form LLM loops
- Step cap + verification checklist returned in the result
- Allowed-tool subset enforced inside the executor
- CI: `.github/workflows/pr-fast.yml` runs `pytest -m "not nightly and not live"`

**Result:** Preview-without-confirm leaves session empty; arrange + rough_mix
checklists green on mock; golden `arrange_task_confirm` passes; pr-fast workflow added.

---

## D016 — Offline synth dogfood WAV when gen APIs are absent (2026-07-18)

**Decision:** Ship `examples/dogfood/stem_f_minor_house.wav` built via
StemScript + `arrange_loop_to_song` + `scripts/render_mock_song.py`. Allowlist
`examples/dogfood/*.wav` in `.gitignore`.

**Why:** Cloud env has no ElevenLabs/ACE/Suno keys; Oli still needs something
playable from GitHub. Provenance stays on the Stem tool stack, not a random file.

**Downstream:** Quality is sketch-synth, not production vocals. Real
`generate_song` remains the path once keys exist locally.

**Result:** 24s / ~4.2MB WAV committed under examples/dogfood/.

---

## D017 — Listen harness gates vocal dogfood; keys never in git (2026-07-18)

**Decision:** Add `review_audio` / `improve_song_prompt` + 
`scripts/generate_and_review_song.py`. Commit WAVs + review JSON only.
API keys stay in env / local `.env` (gitignored).

**Why:** “Make better” needs a falsifiable listen loop, not vibes. Oli shared a
key in chat — use once for generation, warn to rotate, never commit.

**Downstream:** Agent can generate → review → improve prompt → regenerate.
CI tests harness on fixtures without network.

**Result:** Vocal track passed listen harness on attempt 1 (overall 84.6,
45s). Artifacts under examples/dogfood/; key not committed.
