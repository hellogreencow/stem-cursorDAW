# PATCH_NOTES.md — what changed in the bridge and why

Ordered by how likely each one was to break at runtime, worst first. Plain
language; the file:line evidence for every claim is in `BRIDGE_AUDIT.md`.

Two files changed:
- `stem_bridge.lua` — the loader hook (31 lines → ~110)
- `bridge_impl.lua` — the implementation (721 lines → ~800)

Both compile clean under Lua 5.3 (`luac5.3 -p`), and both were run end to end
against a mock of the Ardour API — see `verify/` — under two capability
profiles: "stock Ardour 9" and "stock Ardour 8". All 12 test calls returned
valid JSON in both.

---

### 1. `add_marker` was leaving Ardour's undo stack open. **Broke every time.**

`Session:locations():add_mark(...)` does not exist. The `Locations` class is
bound but has no `add_mark` — only `add_range`. So the handler always threw.
The damage is the ordering: it called `Session:begin_reversible_command()`
*first*, so when the nil-call blew up, Ardour was left holding an un-committed
reversible command and the next real edit got folded into it. A user who typed
"add a marker at the drop" got a silent error **and** a poisoned undo history.

Now it uses `Editor:mouse_add_new_marker(pos, ARDOUR.LocationFlags.IsMark, 0)`,
which is what Ardour's own `add_cdmarker.lua` does and which opens and commits
its own undo record. The marker is then renamed by scanning
`Session:locations():list()`; that rename is best-effort inside a `pcall` and
never fails the call.

### 2. `set_tempo` never worked on stock Ardour. **Broke every time.**

`ARDOUR.LuaAPI.set_session_tempo` exists only in the `ardour-ai` fork. Since
"set the tempo to 90" is literally the first thing in the README's example
session, this is the most visible failure.

Now: the fork helper is tried first, and if it is not there the bridge runs
Ardour's documented tempo transaction —
`TempoMap.write_copy()` → `tm:set_tempo(Temporal.Tempo(bpm,bpm,4), timepos_t(0))`
→ `begin_reversible_command` → `TempoMap.update(tm)` →
`abort_empty_reversible_command` or `commit_reversible_command` — copied
step for step from Ardour's own `s_tempo_map.lua`. `write_copy` must always be
matched by `update` or `abort_update`, so a failure inside `set_tempo` now
calls `abort_update` rather than leaving the map half-edited.

### 3. Any error the bridge reported came back as invalid JSON. **Broke on every failure path.**

The encoder used `string.format("%q", s)`, which is Lua source quoting, not
JSON. I ran it: a newline becomes a backslash followed by a *real* newline and a
tab becomes `\9`, and Python's `json.loads` rejects both. Lua error strings
always contain newlines. So whenever the bridge failed, the Python side got an
unparseable response — i.e. the worst moments were also the least legible ones.
Replaced with a proper JSON escaper (`\n \r \t \b \f \" \\` and `\uXXXX` for
other control bytes). Round-tripped through real `lua5.3` → Python `json.loads`.

### 4. Session tempo read as `inf`. **Broke every read; masked by a fallback to 120.**

`TempoMap.read():tempo_at(pos):quarter_notes_per_minute()` calls a
`Temporal::Tempo` method on a `Temporal::TempoPoint`. Ardour's own binding
source carries a FIXME right above that class saying that exact thing does not
work. Ardour's snippet reads BPM as `tm:quarters_per_minute_at(pos)`, which
returns a plain number and involves no cast. That is now the primary path, with
the explicit `:to_tempo()` downcast as fallback and 120 as last resort.

This matters beyond cosmetics: `get_or_make_region` and `repair_midi_region`
size regions from the tempo. With the tempo stuck at the 120 fallback, a
session at 90 bpm got regions ~25% too short, which truncates the tail of a
progression.

### 5. Audio import never worked on stock Ardour. **Broke every time.**

`ARDOUR.LuaAPI.import_audio_file` is fork-only too. Now: fork helper first,
then `Editor:do_import(...)` exactly as Ardour's `s_import_files.lua` spells it
out. Because Ardour imports on a worker thread, the handler snapshots the route
list, imports, diffs, and returns `pending = true` if the new track has not
appeared yet — so the Python side polls instead of treating a slow import as a
failure. **Unverified:** whether `do_import` blocks. Flagged, not guessed.

### 6. On Ardour 8 the bridge could not create a MIDI region, and said so badly.

The post-June fallback —
`rtav_from_route():to_timeaxisview():to_midi_time_axis_view():add_region()` —
is Ardour **9** API. Ardour 8.12 has no `MidiTimeAxisView` Lua binding at all,
and there is no other way in: `RegionFactory` only exposes `region_by_id`,
`regions` and `clone_region` to Lua. So on the Ardour 8 the README tells people
to install, `insert_midi_notes` failed with `"could not create midi region"` —
true, useless, and it reads like a bug in Stem.

The logic is unchanged (it genuinely cannot be done); the message is now the
actual reason plus the two ways out: upgrade to Ardour 9, or draw one empty
MIDI region on the track by hand once, after which Stem reuses and extends it.

### 7. The loader was burning GUI-thread time ten times a second, forever.

The old loader called `loadfile()` **and re-executed the whole implementation
chunk on every `LuaTimerDS` tick** — recompiling a 35 KB file and rebuilding the
JSON codec and all 28 handler closures ~10×/s, on the thread that draws
Ardour's UI. Nothing hung, because none of it blocks, but it is pure waste and
it grows with the file.

Now: the chunk is compiled and run once; the returned dispatch function is
cached. A reload happens when you ask for one (`touch ~/.stem/reload`) or every
10 s as a safety net. In `bridge_impl.lua` all the setup moved to chunk level,
so a tick now does one `io.open` on `request.json` and nothing else. The
contract is unchanged — the file still returns a function — so the **old**
loader still works with the **new** impl, just at the old cost.

### 8. `ping` now reports what this Ardour can actually do.

`ping` returns a `caps` block: which of the three fork helpers exist, whether
there is an `Editor`, whether `MidiTimeAxisView:add_region` is reachable,
whether the TempoMap write API is there. The agent can then tell the user "this
Ardour can't create MIDI regions" *before* it promises a chord progression,
instead of failing at the last step.

### 9. Smaller corrections

- `to_insert()` → `to_plugininsert()`. Both are bound; `to_insert` is marked
  deprecated in the Ardour source. Same for `to_timeaxisview` (still used —
  there is no non-deprecated replacement on `RouteTimeAxisView`).
- `mm:apply_command(...)` → `mm:apply_diff_command_as_commit(...)`, with
  `apply_command` as fallback. The binding comment calls `apply_command`
  "deprecated: left here in case any extant scripts use apply_command". Passing
  `Session` is correct on both 8 and 9.
- `ARDOUR.PluginType.name(info.type)` → `name(info.type, true)`. The C++
  default for `short_name` is `true`, but a missing Lua argument arrives as
  `false`, so the bridge was silently collecting long type names.
- Hard-coded `1920` → `Temporal.ticks_per_beat` (which is 1920) with 1920 as
  fallback. If Ardour ever changes PPQN, every note length would have been
  silently wrong.
- `mr:model()` vs `mr:midi_source(0):model()` — the file used both. **Both are
  bound on stock Ardour**, so GAPS.md's note ("not `:model()`") does not hold
  upstream. Unified into one helper that tries `:model()` then the source.
- `Session:sample_rate()` and `Session:nominal_sample_rate()` were mixed within
  the same file; they are different numbers (current vs. base). Unified into
  one helper that sanity-checks the result (1 kHz–1 MHz) — which also contains
  the unexplained int64-max reading the author recorded.
- Numbers in JSON: integers now print as integers rather than `1.0`, so
  `sample_rate` stops arriving as `48000.0`.

---

## Two things that were fine and are worth saying

- **`Session:new_midi_track` was correct.** GAPS.md lists its argument
  order/types as the number-one risk. Argument for argument it matches
  `session.h` and Ardour's own `_rgh_midi_track_trick.lua` and
  `_route_template_generic_midi.lua`. Left untouched.
- **`new_noteptr` and the Beats types were correct.** `(chan, Beats time,
  Beats length, pitch, velocity)` with `Beats(whole, ticks)` at 1920 ticks per
  beat is exactly the binding. `Temporal.Beats.from_double` also exists, so
  that GAPS.md worry was unfounded too.

## One thing to fix outside the Lua

`tests/test_bridge_impl_contract.py` encodes "fork only" as a *requirement*, so
the corrected bridge fails four of its assertions. I ran them; exactly these
four flip, and none of them is a behaviour regression:

| Assertion | Why it now fails |
|---|---|
| `assert "Editor:do_import" not in source` | the stock audio-import fallback |
| `assert "Temporal.TempoMap.write_copy" not in tempo_handler` | the stock tempo fallback |
| `assert "if not Editor then return nil end" in region_helper` | that line now returns `nil, "<reason>"` so the caller can report *why* |
| `assert "mm:apply_command(Session, cmd)" in insert_handler` | the call is now `mm:apply_diff_command_as_commit (Session, cmd)` with `mm:apply_command (Session, cmd)` as fallback — the literal string the test greps for (no space before the paren) is gone |

The five other assertions in those four tests still pass — the fork helpers are
all still referenced and still tried first. These should be relaxed from
"fork only" to "prefers the fork helper, falls back to stock"; as written they
block the bridge from ever running on a machine that is not Oli's.

And `README.md` should stop saying Ardour 8.x. Today the honest line is:
**Ardour 9.x for stock, or the ardour-ai fork.** Publishing `ardour-ai`, or at
least the three `LuaAPI` additions as a patch, is what makes this repo
reproducible by anyone else.
