# Known Gaps & Blockers

Honest ledger: what works, what's stubbed, what's unverified.

## Verified working
- Theory engine: scales, chords, progressions, drum/bass patterns (8 tests)
- Tool registry: 18 tools, pydantic validation, no silent mutations
- MockBridge: full session emulation incl. per-action undo
- Vertical slice: command → track → in-key progression → verify → play → undo

## Unverified (needs live Ardour — NOT INSTALLED on this machine)
- **Everything in `stem_bridge.lua`.** Written against the Ardour 8.x Lua API
  from documentation knowledge; specific risk areas:
  - `Session:new_midi_track(...)` argument order/types
  - `ARDOUR.LuaAPI.new_midi_region` — may not exist under that name;
    creating an empty MIDI region from Lua may need
    `Session:create_midi_source` + playlist add instead
  - `ARDOUR.LuaAPI.note_list` iteration and `new_noteptr` beat types
  - Temporal API (`Temporal.Beats.from_double`, `TempoMap.write_copy`) —
    changed significantly in Ardour 7+; calls may need adjustment
  - The polling loop: `EditorAction` + `usleep` blocks the GUI thread.
    Correct mechanism is a session script with `LuaTimerDS` or signal hook —
    restructure on first live test.
- OSC transport paths (`/transport_play` etc.) — standard, low risk.
- `import_audio` has no Lua handler yet (region import API TBD).

## Stubbed
- Suno generation: interface + env wiring only; needs SUNO_API_KEY + live test
- ACE-Step: shells out to the old project's CLI; untested since the model
  checkpoints weren't loaded during the sprint
- Semantic sample search (Phase 2): not started
- Plugin load/param control (Phase 2): not started — needs Lua
  `LuaAPI.new_plugin` verification

## Blockers hit during sprint
- Ardour not installed; official binaries are email/pay-gated, no brew
  formula. Options: pay/donate for the binary (fastest), or build the fork
  in ~/Desktop/ardour-ai from source (hours).
- No ANTHROPIC_API_KEY in env — agent loop (stem/agent/loop.py) is code-
  complete but the live LLM round trip is untested. The exact tool sequence
  the LLM would emit was executed manually and passes.

## Next highest-leverage step
Install Ardour → run `stem_bridge.lua` → fix the Lua calls the live API
rejects (expect 1-2 hours of iteration) → re-run the Phase 0 milestone
against the real session. Everything above the bridge is already proven.

---
## UPDATE 2026-06-11 (evening): LIVE BRIDGE VERIFIED

The full Phase 0 milestone now works against a live, self-compiled Ardour 9:
create track → insert in-key chord progression → verify notes → undo. Verified
with real C-major (C/G/Am/F) and F-major (I-IV-V) progressions; undo cleanly
removes them (9 notes → 0).

Ardour-9.x Lua API corrections discovered (now in bridge_impl.lua):
- Region creation: NO LuaAPI constructor. Use the editor view:
  Editor:rtav_from_route(route):to_timeaxisview():to_midi_time_axis_view()
  :add_region(pos, len, true)
- MIDI model: midi_region:midi_source(0):model() (not :model())
- Notes: ARDOUR.LuaAPI.new_noteptr(chan, Beats(whole,ticks), Beats(...), pitch, vel)
  with 1920 ticks/beat. Read back via note:time():to_ticks()/1920.
- Undo: Editor:undo(1) (Session:undo not bound in Lua)
- Bridge runs as an EditorHook on LuaTimerDS, hot-reloading ~/.stem/bridge_impl.lua
- JSON encoder MUST guard inf/nan (tempo read returns inf — needs correct API)

Still imperfect (non-blocking): session tempo reads as inf (fallback=120),
sample_rate reads as int64-max. Both are wrong-API-signature issues, cosmetic
for now since chord/note placement doesn't depend on them.

---
## BUILD GOTCHA (2026-06-11): do NOT `brew install lua`
Ardour bundles its own Lua 5.3.5 (libs/lua/lua-5.3.5). The build uses
CXXFLAGS `-I/opt/homebrew/include`. If Homebrew's `lua` formula is installed,
its lua.h/lauxlib.h (5.4+) sit in /opt/homebrew/include and SHADOW the bundled
headers → version-mismatched include guards → `luaL_checknumber undeclared`
errors compiling any file that includes LuaBridge (e.g. ardour_ui.cc).
Fix: `brew uninstall lua`. (We only installed it for `luac` syntax-checking;
use `python3 -c "import ..."` or the bundled luac instead.)


---
## UPDATE 2026-09-21: BRIDGE AUDITED AGAINST ARDOUR SOURCE

Every Ardour/Lua call in `bridge_impl.lua` has now been checked call-by-call
against the real binding source — Ardour **8.12** (`10517bff`) and **9.8**
(`22ed8656`). 54 call sites, each verdict with a file:line citation, in
`BRIDGE_AUDIT.md`; `PATCH_NOTES.md` has the changes in plain language.

### The headline: the fork is not optional, and the README used to say it was

Three calls the bridge makes exist in **no public Ardour**, 8.12 or 9.8 (the
whole `ARDOUR.LuaAPI` namespace was dumped in both trees):

- `ARDOUR.LuaAPI.ensure_midi_region`
- `ARDOUR.LuaAPI.set_session_tempo`
- `ARDOUR.LuaAPI.import_audio_file`

They are `ardour-ai` fork additions, and `ardour-ai` is unpublished. Before
this change, Stem ran on exactly one machine in the world. Each of the three
now falls back to a stock-Ardour path, and README carries a per-version
capability table. **A separate effort is preparing a patch that adds these
three to upstream Ardour**; if it lands, stock Ardour from that release gains
the native helpers and the table's fork row and stock row converge. Until a
released Ardour actually ships them, the honest statement is the one above.

### Three live bugs, now fixed and each with a regression test

1. **`add_marker` corrupted the undo stack.** It called
   `Session:begin_reversible_command()` and *then*
   `Session:locations():add_mark(...)`, which does not exist in either version.
   So it threw with a reversible command still open, and the user's next real
   edit got folded into it. Now `Editor:mouse_add_new_marker`, which manages
   its own undo record. Tests: `test_marker_failure_leaves_no_open_undo_command`,
   `test_every_reversible_command_in_the_bridge_is_balanced`.
2. **The "tempo reads as inf" note above has a cause.** Ardour's own
   `luabindings.cc` carries a FIXME saying parent-class `Temporal::Tempo`
   access through a `TempoPoint` does not work — which is exactly what the
   bridge did. `TempoMap:quarters_per_minute_at()` is the working read. This
   was **not cosmetic**: region lengths are computed from tempo, so with the
   read stuck at the 120 fallback a 90 bpm session got regions ~25% short and
   the tail of a progression was cut off. Test:
   `test_region_length_follows_session_tempo`.
3. **Every error return was invalid JSON.** The encoder used
   `string.format("%q", …)`, which is Lua *source* quoting, not JSON: a newline
   becomes a backslash followed by a real newline. Lua error strings always
   contain newlines, so the Python side could not parse any failure — the worst
   moments were the least readable ones. Test:
   `test_error_text_containing_newlines_survives_the_encoder`.

### A fourth, found while writing those tests

The bridge's JSON **decoder** turned every `\uXXXX` escape into a literal `?`.
Python's `json.dumps` escapes all non-ASCII by default, so any non-ASCII text
the agent sent was destroyed on arrival: a track named `Café` reached Ardour as
`Caf?`, and em dashes and emoji in marker names went the same way. Fixed,
surrogate pairs included. Test: `test_non_ascii_names_survive_the_round_trip`.

### Stale entries above, corrected

- **`Session:new_midi_track` argument order** — listed as risk #1 above. It is
  **correct**, argument for argument, against `session.h`. Left alone.
- **`new_noteptr` / `Temporal.Beats`** — **correct**. `Temporal.Beats.from_double`
  exists too, so that worry was unfounded.
- **`ARDOUR.LuaAPI.new_midi_region`** — correctly suspected absent; the
  suggested workaround (`create_midi_source` + playlist add) is *also* not
  available from Lua.
- **`midi_region:model()` vs `:midi_source(0):model()`** — the 2026-06-11 note
  says "not `:model()`". Upstream, **both are bound**. The bridge now tries
  `:model()` and falls back to the source.
- **The GUI-blocking `EditorAction` + `usleep` loop** — already gone at HEAD;
  `stem_bridge.lua` is an `EditorHook` on `LuaTimerDS`, which is the correct
  mechanism. That item is done. (It was, however, re-reading and re-executing
  the whole 35 KB implementation on every tick, ~10×/s on the GUI thread. The
  loader now compiles once and caches.)
- **`import_audio` "has no Lua handler yet"** — it does now, both paths.
- **`sample_rate` reads as int64-max** — unexplained by the binding source;
  the value is now sanity-checked (1 kHz–1 MHz) rather than trusted.

### Still unverified — what to test in a live session

Nothing below has been run against real Ardour; it is read from source.

- Whether `Editor:do_import` blocks. The handler snapshots the route list,
  imports, diffs, and returns `pending = true` if the new track has not
  appeared, so a slow import is polled rather than treated as a failure. If it
  turns out to block, that polling is harmless but pointless.
- `Locations:mark_at` nil behaviour through LuaBridge — the marker rename is
  best-effort inside a `pcall` and never fails the call.
- Whether `abort_empty_reversible_command` reports as expected on 9.x.
- The int64-max `sample_rate` reading.

### How the bridge is tested now

`tests/test_bridge_regressions.py` executes the real `bridge_impl.lua` under
`tests/lua/mock_ardour.lua` — stock-8, stock-9 and fork capability profiles,
one dispatch tick per call. Grep-only tests could not have caught any of the
four bugs above; 15 of the 30 tests in that file fail against the pre-audit
bridge and none fails against the current one.

Two honesty notes on the suite:
- Seven tests in `test_bridge_impl_contract.py` assert on the `ardour-ai`
  fork's own C++ and packaging sources. They used to raise `FileNotFoundError`
  anywhere the fork was absent, i.e. the suite could not pass on any machine
  but the author's. They now **skip** with a reason and run when
  `ARDOUR_AI_ROOT` points at a checkout. A skip is not a pass — nothing in
  public CI covers the native panel.
- The behavioural bridge tests need a Lua interpreter (`lua5.3`). Without one
  they skip, and only their source-level counterparts run.
