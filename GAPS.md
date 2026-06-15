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
