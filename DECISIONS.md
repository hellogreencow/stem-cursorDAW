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
