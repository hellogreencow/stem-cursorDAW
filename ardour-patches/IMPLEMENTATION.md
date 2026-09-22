# IMPLEMENTATION.md — three new `ARDOUR.LuaAPI` functions

What this is: real C++ Lua bindings for the three functions
`stem-cursorDAW`'s `bridge_impl.lua` calls and no public Ardour provides —
`ensure_midi_region`, `set_session_tempo`, `import_audio_file`. They were
written from the Ardour source, not from documentation, and every claim
below carries a `file:line`.

## Trees diffed against

| Version | Tag | Commit | Committed |
|---|---|---|---|
| **9.8** (primary) | `9.8` | `22ed8656c2533e325322ff11831448e5123e0d4b` | 2026-08-19 |
| **8.12** | `8.12` | `10517bff2b2c7b882b453296a939cf5d03174831` | 2025-03-09 |

Both shallow-cloned from `https://github.com/Ardour/Ardour`. Nothing was
pushed anywhere and nothing was authenticated to.

Patches:
- `ardour-lua-api.patch` — against tag `9.8`
- `ardour-lua-api-8.12.patch` — the same three functions against tag `8.12`

Both are `git diff` output from a real edited checkout, and both were
verified with `git apply --check` against a *pristine* checkout of their tag
(the tree was `git stash`ed to zero modifications first, so the check is
honest). Each touches three files and adds 317 lines:
`libs/ardour/ardour/lua_api.h`, `libs/ardour/lua_api.cc`,
`libs/ardour/luabindings.cc`.

---

## The headline: this is a libardour patch, not a GUI patch, and it works on 8 as well as 9

The prior audit concluded that on stock Ardour 8 a MIDI region cannot be
created from Lua **at all**, and that the only route on Ardour 9 is
`to_midi_time_axis_view():add_region()` at `gtk2_ardour/luainstance.cc:840,849`.

That conclusion was correct **for the question it asked** — what is *bound*
to Lua in 8.12. Nothing is. But this job is writing new bindings, so the
question that actually governs is: **does the underlying C++ exist in
libardour 8.12?** It does.

`MidiTimeAxisView::add_region()` lives at `gtk2_ardour/midi_time_axis.cc:1771`
in 8.12 and `:1677` in 9.8, and the two bodies are near-identical. Reading it
line by line, everything it touches is libardour:

| What `add_region` calls | Where it lives | GUI? |
|---|---|---|
| `_session->create_midi_source_by_stealing_name (track)` | `session.h:909` (8.12) / `:979` (9.8) | no |
| `RegionFactory::create (src, plist, announce)` | `region_factory.h:80` (8.12) / `:79` (9.8) | no |
| `Properties::start/length/automatic/whole_file/name/opaque` | `libs/ardour/region.cc` | no |
| `_session->config.get_draw_opaque_midi_regions()` | `session_configuration_vars*.h:46` (both) | no |
| `playlist()->add_region (region, pos, 1.0, false)` | `playlist.h:170` (both) | no |
| `_session->add_command (new StatefulDiffCommand (playlist()))` | `pbd/stateful_diff_command.h` (both) | no |
| `real_editor->begin/commit_reversible_command` | `Editor` | **yes** |
| `real_editor->snap_to (pos, RoundNearest)` | `Editor` | **yes** |

Only the last two are GUI-bound, and neither is needed:

- Undo: `Session` has the same methods in its own right. In 8.12 they are on
  `Session` directly (`session.h:1095,1106,1108,1149`); in 9.8 `Session`
  inherits them from `PBD::HistoryOwner` (`libs/pbd/pbd/history_owner.h:29,40,42,83`),
  which 9.8 already binds to Lua at `luabindings.cc:533-538`. The GUI's
  `Editor::begin_reversible_command` is a thin wrapper over the session's.
- Snapping: a scripted caller passes an exact position. Snapping it to the
  GUI's current grid would be a bug, not a feature. The binding does not snap,
  and the doc comment says so.

So all three functions go in `libs/ardour/lua_api.cc`, depend on nothing in
`gtk2_ardour/`, and are available in Ardour's Lua console, in `EditorHook`
scripts (which is how the Stem bridge runs), **and** in headless session
scripts where `Editor` does not exist at all.

**Verdict on the "Ardour 9 only?" question the brief asked: no. One
implementation, both versions.** The two patches differ only in diff context,
not in a single line of the functions themselves — the C++ is byte-identical
between them, which I confirmed by diffing the two patched `lua_api.cc` files
against each other.

---

## 1. `ensure_midi_region (Session, MidiTrack, beats) -> MidiRegion`

**What it does.** Find-or-create. If the track's playlist already holds a
MIDI region, it returns the earliest one, growing it in place first if it is
shorter than `beats` quarter notes. If there is none, it creates an empty one
of that length at the start of the timeline.

**Why that shape.** It is exactly what `bridge_impl.lua:509-550`'s
`get_or_make_region` does by hand — scan the playlist, extend if short,
otherwise create — so the whole helper collapses into one call. "Ensure" is
also the honest verb: it is idempotent, which matters for a bridge that is
re-entered on every `LuaTimerDS` tick.

**The C++ it calls**, in order (9.8 line numbers; 8.12 in brackets where they
differ):

- `MidiTrack::playlist()` — `track.h:143`
- `Playlist::region_list()` — `playlist.h:214`
- `Region::set_length (timecnt_t)` — `region.h:328`
- `Session::create_midi_source_by_stealing_name (Track)` — `session.h:979` [`:909`]
- `RegionFactory::create (Source, PropertyList)` — `region_factory.h:79` [`:80`]
- `RegionFactory::create (Region, PropertyList)` — `region_factory.h:83` [`:84`]
- `Playlist::add_region (Region, timepos_t, float, bool)` — `playlist.h:170`
- `Session::add_command (new StatefulDiffCommand (playlist))` — `history_owner.h:42` [`session.h:1108`]

modelled on `gtk2_ardour/midi_time_axis.cc:1677-1727` [`:1771-1821`].

**One deliberate divergence, stated plainly.** 8.12's `add_region` passes
`announce = true` for the whole-file region and `announce = false` for the
playlist copy; 9.8 takes the default (`true`) for both. I use the default in
both patches, i.e. I follow 9.8. The practical effect on 8.12 is that the
playlist region is also announced via `RegionFactory::CheckNewRegion`, which
is what puts it in the editor's region list — desirable for a region a script
just made. It is a behaviour difference from 8.12's own GUI path and I am
flagging it rather than burying it.

**Undo.** The function adds a `StatefulDiffCommand` but does **not** open its
own reversible command. That is intentional: the caller decides the
transaction boundary, exactly as `MidiTimeAxisView::add_region`'s `commit`
flag allows. The Stem bridge already wraps note insertion in
`begin_reversible_command`/`commit_reversible_command`, so the region
creation folds into the same undo step as the notes — which is the behaviour
a user wants.

**Not verified:** no Ardour was run. See "What is and is not verified" below.

---

## 2. `set_session_tempo (Session, bpm) -> bool`

**What it does.** Sets a constant tempo at the start of the session, with a
correct map transaction and a real undo record.

**The map copy/commit dance, which the brief asked me to get right.**
`Temporal::TempoMap` is a read-copy-update structure. `write_copy()`
(`tempo.h:801` [`:761`], `tempo.cc:4821`) takes a lock and hands back a
writable copy; **every** `write_copy()` must be matched by exactly one
`update()` (`tempo.h:802`, `tempo.cc:4827`) or `abort_update()`
(`tempo.h:803`, `tempo.cc:4848`), or the lock is never dropped. Ardour's own
snippet says this in as many words at `share/scripts/s_tempo_map.lua:24-27`.
Every early-return path in my function goes through one or the other — the
failure paths call `abort_update()`, including the `catch (...)`.

**Undo, which Ardour's own Lua snippet gets wrong.**
`s_tempo_map.lua:14-18` does `begin_reversible_command` → `update` →
`abort_empty_reversible_command`, and adds **no command**, so the undo stack
entry is empty and the tempo change is not undoable. The real pattern is in
libardour at `session.cc:7826-7857` [`:7683`]: capture `wmap->get_state()`
before and after and add a `Temporal::TempoCommand`
(`tempo.h:1299`, `tempo.cc:5466`; its `undo()`/`operator()` at `:5502`/`:5514`
re-apply the saved XML through another write_copy/update cycle). That is what
this binding does, so a scripted tempo change is undoable like any other edit.

**On the `inf` bug the audit found.** The prior audit traced "tempo reads as
`inf`" to the FIXME at `luabindings.cc:842-843` [`:803-804`] — reaching
through a `TempoPoint` into its `Tempo` parent fails. That is the **read**
path and this function is the **write** path, so the FIXME does not apply
here; `TempoMap::set_tempo (Tempo const&, timepos_t const&)`
(`tempo.h:846` [`:798`], `tempo.cc:1564`) takes a `Tempo` by const reference
and never goes through a `TempoPoint`. The read path stays what the audit
recommended: `TempoMap::quarters_per_minute_at()`.

**A detail worth knowing:** `set_tempo` requires tempo changes to be on a
beat and rounds to one (`tempo.cc:1570-1583`). I pass a beat-domain zero, so
there is nothing to round. I construct `Temporal::Tempo (bpm, bpm, 4)` —
start and end equal, i.e. constant, not ramped — matching the bound
constructor at `luabindings.cc:817`.

---

## 3. `import_audio_file (Session, path, sample_position) -> track id string`

**What it does.** Imports the file into the session, makes a whole-file
region from it, creates a new track of the right type, and drops the region
on it at `sample_position`. Returns the new track's `PBD::ID` as a string, or
`""` on failure.

**Why a new track, and why that return value.** Because that is what the call
site needs: `bridge_impl.lua:617-625` calls it with three arguments, keeps the
result as `track_id`, and reports `"audio import did not create a track"`
when it is empty. (The brief sketched `(path, track, position)`; the code's
actual contract is `(Session, path, samples)` and I implemented the code's.
See "Signatures" below.)

**The blocking question, which the brief asked me to settle: it blocks.**
`Session::import_files (ImportStatus&)` (`session.h:920`,
`libs/ardour/import.cc:903`) performs the whole read/convert/write loop in
the calling thread and sets `status.done = true` on its last line. It is not
asynchronous. The GUI runs it on a separate thread
(`gtk2_ardour/editor_audio_import.cc:586`, `pthread_create_and_store
("import", ...)`) purely so it can animate a progress dialog — not because
the function returns early.

**What that means for the Stem bridge, concretely.** The bridge runs inside
an `EditorHook` on `LuaTimerDS`, i.e. on the GUI thread. A synchronous import
will freeze Ardour's UI for as long as the import takes — a few hundred
milliseconds for a short stem, longer for a long file needing sample-rate
conversion. That is a real cost and it is documented in the header comment
rather than hidden. It is also strictly better than today's behaviour, which
is that the call does not exist and the import never happens. If it becomes a
problem the fix is a threaded variant with a polling `status` object exposed
to Lua; I did not write that, because a blocking call with an honest
docstring is a smaller thing to ask upstream to accept.

**The C++ it calls:**

- `Session::import_files (ImportStatus&)` — `session.h:920`, `import.cc:903`
- `ImportStatus` / `InterThreadInfo` fields — `ardour/import_status.h:34-63`,
  `ardour/interthread_info.h:29-38`
- `RegionFactory::region_by_name` — `region_factory.h:53`; `bump_name_once` —
  `ardour/utils.h:85`
- `RegionFactory::create (SourceList, PropertyList)` — `region_factory.h:81`
- `Session::new_audio_track` — `session.h:780` [`:732`];
  `Session::new_midi_track` — `session.h:~790` [`:741`]
- `Region::derive_properties` — `region.h:131` [`:126`]
- `Playlist::add_region` / `clear_owned_changes` / `rdiff_and_add_command` —
  `playlist.h:170`, `:106`

The region-building block is copied from `Editor::add_sources`
(`editor_audio_import.cc:792-814`, the `target_regions == 1` branch) and the
placement from `Editor::finish_bringing_in_material`
(`:1036-1062`, the `ImportToTrack` branch), both reduced to their libardour
parts.

**A portability trap I hit and handled:** `new_audio_track`/`new_midi_track`
take the route group as `RouteGroup*` in 8.12 and `std::shared_ptr<RouteGroup>`
in 9.8. Passing a literal `nullptr` compiles against both, since
`shared_ptr`'s `nullptr_t` constructor is non-explicit. That is the only
reason one body serves both trees.

---

## Signatures: what I implemented and why it differs from the brief

The brief sketched `ensure_midi_region(track, position, length)`,
`set_session_tempo(bpm)`, `import_audio_file(path, track, position)`. I
implemented the signatures the **call sites** use, because those are what
makes `bridge_impl.lua` run unmodified:

| Function | Implemented | Call site |
|---|---|---|
| `ensure_midi_region` | `(Session*, shared_ptr<MidiTrack>, double beats)` | `bridge_impl.lua:530` |
| `set_session_tempo` | `(Session*, double bpm)` | `bridge_impl.lua:630` |
| `import_audio_file` | `(Session*, string path, samplepos_t pos)` | `bridge_impl.lua:617` |

The leading `Session*` is not redundant padding — it is the house style for
every `ARDOUR.LuaAPI` function that touches session state (`new_send`,
`new_luaproc`, `set_automation_data`), because `LuaAPI` is a free-function
namespace with no implicit session.

**Consequence: `bridge_impl.lua` needs no changes to use these.** The three
`pcall`-guarded fork-helper calls it already makes will simply start
succeeding on a patched stock Ardour, and the stock fallbacks the previous
audit added stay in place as the path for an unpatched one.

---

## What is and is not verified

**Verified, by doing it:**

- Both patches apply to a pristine checkout of their tag —
  `git apply --check` against a tree confirmed clean by
  `git status --porcelain` first.
- **`libs/ardour/lua_api.cc` compiles.** `g++ -std=c++17 -fsyntax-only`
  against the real Ardour headers, on **both** 8.12 and 9.8, exits clean.
  `-fsyntax-only` runs the full front end, so every type, overload,
  conversion and member lookup in these 265 lines was checked against the
  actual headers — not eyeballed. This required installing the real
  dependencies (glibmm, liblo, vamp-hostsdk, rubberband, lv2, fftw3, sndfile,
  samplerate, libxml2, boost, lua5.3) plus two waf-generated headers: a
  minimal `libardour-config.h` stand-in, and `pbd/signals_generated.h`
  produced by running Ardour's own `libs/pbd/pbd/signals.py`.
- The compiler caught two real bugs in my first draft, which are fixed:
  a missing `#include "ardour/midi_source.h"` (so
  `shared_ptr<MidiSource>` would not convert to `shared_ptr<Source>`), and a
  most-vexing-parse — `const timepos_t pos (Temporal::Beats ());` declares a
  function, not a variable. Both were found by compiling, not by reading.
- **`libs/ardour/luabindings.cc` compiles too — on both tags.** This is the
  one that matters most for a binding patch: it is where LuaBridge's
  templates are instantiated for the three new function pointers, and a
  registration can look perfectly plausible yet fail inside LuaBridge's
  `Stack<>` machinery for a type it has no specialisation for. It does not
  fail. Both 8.12 and 9.8 exit 0 (8.12 emits one unrelated Boost deprecation
  pragma about global bind placeholders). All the types involved were already
  bound: `MidiTrack` at `luabindings.cc:1639` [`:1595`], `MidiRegion` at
  `:1737` [`:1695`], plus `Session*`, `std::string`, `double`, `bool` and
  `samplepos_t`.
- The three registrations match the surrounding entries exactly: plain
  `.addFunction` in the `LuaAPI` namespace block, same form as `new_send`
  two lines above.

**On whether that check has teeth:** it does, and I can show it rather than
assert it — the same check caught the two bugs listed above in my first
draft. A `shared_ptr` conversion that fails only because a header is missing,
and a most-vexing-parse that turns a variable into a function declaration,
are exactly the class of mistake that reads fine on the page. Both were found
by the compiler, not by me.

**NOT verified — do not let anyone read this as "it builds and works":**

- **No full Ardour build was done.** A complete `waf` build of Ardour on this
  box (4 CPUs, 3.9 GB RAM) is hours of compute and more disk than is free.
  What I ran is front-end checking of the two translation units this patch
  touches that contain code — not the other ~700 in the project, and not the
  link step.
- **No linking**, so nothing proves the symbols resolve at link time.
- **Nothing was run in Ardour.** No session was opened, no region created, no
  tempo set, no file imported. Runtime correctness — that the region really
  appears on the timeline, that the tempo really changes and undoes cleanly,
  that the imported track really shows up — is untested and needs a live
  session.
- **The undo behaviour is reasoned from Ardour's own code, not observed.**
- **I could not read `ardour-ai`.** It is private. So I cannot say whether
  Oli's fork implements these the same way, or whether his versions have
  semantics mine do not match. Mine are written to satisfy his call sites,
  which is the contract that is actually observable.

## What to test first in a live Ardour

1. Build the patch. Open Ardour's Lua console (Window → Scripting) and run
   `print (ARDOUR.LuaAPI.set_session_tempo (Session, 90))` — expect `true`,
   the tempo ruler to read 90, and one undo step named "Set Tempo" that
   actually reverts it.
2. Make a MIDI track. `local mt = Session:get_routes():front():to_midi_track()`
   then `ARDOUR.LuaAPI.ensure_midi_region (Session, mt, 16)` — expect a
   4-bar empty region at 0. Call it again with `32` — expect the **same**
   region, now twice as long, not a second one.
3. `print (ARDOUR.LuaAPI.import_audio_file (Session, "/path/to/a.wav", 0))` —
   expect a new track and a non-empty id string. Time it; that duration is
   how long the UI will be frozen.
4. Then run the Stem bridge itself against the patched build with the fork
   helpers as the only path, i.e. confirm the three `pcall`s succeed.
