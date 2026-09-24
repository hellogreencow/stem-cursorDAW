# BRIDGE_AUDIT.md — stem-cursorDAW Ardour Lua bridge, call-by-call

Audited 2026-09-21 against the real Ardour source. No Ardour was run; every
verdict below comes from reading the binding source and the scripts Ardour
ships, with the file and line quoted. Where the source does not settle a
question it says so, and the live test that would settle it is at the bottom.

## Ardour versions pinned

| Version | Tag | Commit | Why |
|---|---|---|---|
| **8.12** (primary) | `8.12` | `10517bff2b2c7b882b453296a939cf5d03174831` (2025-02-28) | Latest 8.x tag. README.md tells the user to "Install Ardour 8.x", so this is the version the project claims to support. |
| **9.8** (cross-check) | `9.8` | `22ed8656c2533e325322ff11831448e5123e0d4b` (2026-08-19) | Latest release. GAPS.md's 2026-06-11 update says the bridge was verified against "a live, self-compiled Ardour 9", so 8.x and 9.x are both live questions. |

Both shallow-cloned from `https://github.com/Ardour/Ardour`. Every line
reference below is `file:line` **in those two trees**, 8.12 first.

The binding surface lives in three places, not one:
- `libs/ardour/luabindings.cc` — `ARDOUR.*`, `Temporal.*`, `Evoral.*`, `PBD.*`, `Session`
- `gtk2_ardour/luainstance.cc` — `Editor` (`PublicEditor`) and the editor-side classes
- `share/scripts/` — Ardour's own scripts, which are the authoritative idiom

---

## 0. What is actually in the repo (read this first)

**GAPS.md's risk list is out of date, and it understates one problem and
overstates another.**

The brief describes `stem_bridge.lua` as an unverified 700-line file with an
`EditorAction` + `usleep` polling loop. That is not what is at HEAD
(`a626630`). At HEAD:

- `stem/bridge/stem_bridge.lua` is **31 lines** — already an `EditorHook` on
  `LuaSignal.LuaTimerDS`. **The GUI-blocking `EditorAction` + `usleep` loop is
  already gone, and the mechanism it was replaced with is the correct one.**
  Item 5 of the brief was fixed before I got here; I verified the replacement
  rather than performing it (details in row L1–L3 below).
- The real API surface — 721 lines, 28 handlers — is
  `stem/bridge/bridge_impl.lua`, hot-reloaded by that loader. **That is the file
  this audit is about.**

### Headline findings

1. **The bridge does not run on stock Ardour at all. It requires a private
   fork.** Three functions it calls —
   `ARDOUR.LuaAPI.ensure_midi_region`, `ARDOUR.LuaAPI.set_session_tempo`,
   `ARDOUR.LuaAPI.import_audio_file` — **do not exist in Ardour 8.12 or 9.8.**
   I dumped the entire `ARDOUR.LuaAPI` namespace in both trees
   (`luabindings.cc:3183` / `:3318` onward); none of the three appears
   anywhere in either source tree. They are additions in Oli's `ardour-ai`
   fork, and `tests/test_bridge_impl_contract.py` **asserts their presence**
   (`test_live_bridge_uses_embedded_audio_import`,
   `test_live_bridge_uses_native_tempo_helper`,
   `test_live_bridge_can_create_midi_regions_without_editor`), which is how I
   know they are deliberate fork APIs rather than mistakes.
   `ardour-ai` is not published: `hellogreencow` has 31 public repos and none
   of them is it (checked via the GitHub API, 2026-09-21). So today the bridge
   runs on exactly one machine, and README.md's "Ardour 8.x … **or** build from
   the fork" is wrong — the fork is not optional.

2. **On stock Ardour 8.x the bridge cannot create a MIDI region, full stop.**
   The fallback path added after the June live test —
   `Editor:rtav_from_route():to_timeaxisview():to_midi_time_axis_view():add_region()`
   — is **Ardour 9 only**. `luainstance.cc:840` (`addCast<MidiTimeAxisView>
   ("to_midi_time_axis_view")`) and `:849` (`add_region`) exist in 9.8 and
   **there is no `MidiTimeAxisView` binding of any kind in 8.12**. And there is
   no other route: `RegionFactory` exposes only `region_by_id`, `regions` and
   `clone_region` to Lua (`luabindings.cc:3151-3154` / `:3283-3286`), so
   libardour alone cannot make a blank region. GAPS.md's original suspicion —
   "`new_midi_region` may not exist … may need `create_midi_source` + playlist
   add" — was right about the absence and wrong about the workaround: there is
   no `create_midi_source` in Lua either.

3. **`add_marker` was corrupting the undo stack, not just failing.**
   `Session:locations():add_mark(...)` **is not bound**. The `Locations` class
   exists (`luabindings.cc:1251` / `:1296`) and exposes
   `list / mark_at / add_range / remove / …` — no `add_mark`. The handler
   called `Session:begin_reversible_command()` *first*, so the nil-call error
   propagated out with a reversible command still open, and the next real edit
   got folded into it. This is the most damaging bug in the file.

4. **Found the cause of "session tempo reads as inf".** The code did
   `TempoMap.read():tempo_at(pos):quarter_notes_per_minute()`, calling a
   `Temporal::Tempo` method on a `Temporal::TempoPoint` userdata. Ardour's own
   binding carries a FIXME directly above that class saying it does not work:

   > `/* FIXME, direct access to parent class Temporal::Tempo fails here,`
   > `* even thought it is access via UserdataPtr at the same address */`
   > — `luabindings.cc:803-804` (8.12), `:842-843` (9.8)

   Ardour's own snippet reads BPM the cast-free way:
   `tm:quarters_per_minute_at(tp)` (`share/scripts/s_tempo_map.lua:8`, bound at
   `luabindings.cc:884` / `:923`, returns a plain `double`). When Ardour's
   snippet *does* go through a `TempoPoint` it uses the explicit downcast
   `:to_tempo()` (`s_tempo_map.lua:39`). I am confident but not certain this is
   the whole story — see "Not settled by source" below.

5. **The JSON encoder produced invalid JSON for any string with a newline —
   i.e. for every error message.** `string.format("%q", s)` is Lua-source
   quoting, not JSON. I ran it: a Lua error string comes out with a backslash
   followed by a *real* newline, and `\9` for tab, and Python's `json.loads`
   rejects it (`Invalid \escape`). So the bridge's own failures came back to
   the Python side unparseable. Measured, not inferred — the reproduction is in
   `verify/` alongside this file.

---

## 1. Call-by-call table

Verdict key: **OK** = exists with that name and that argument order ·
**FORK** = does not exist upstream, fork-only · **WRONG** = exists but used
incorrectly · **MISSING** = does not exist under that name at all ·
**8-INCOMPAT** = fine on 9.x, absent on 8.x · **NOTE** = works, worth knowing.

### Loader — `stem_bridge.lua`

| # | Call site | What the code does | What the real API says | Verdict |
|---|---|---|---|---|
| L1 | `["type"] = "EditorHook"` | registers as an action hook | EditorHook is the script type that takes `signals()` + `factory()`; shape matches `share/scripts/_hook_test.lua` exactly | **OK** |
| L2 | `LuaSignal.Set():add({[LuaSignal.LuaTimerDS]=true})` | fires on the deci-second timer | `LuaTimerDS` declared `gtk2_ardour/luasignal_syms.h:89` (8.12) / `luasignal_syms.inc.h:91` (9.8); emitted from `LuaInstance::every_point_one_seconds` (`luainstance.cc:1511` / `:1576`), wired via `Timers::rapid_connect` (`luainstance.cc:1481`) | **OK** — this is the right mechanism; the GAPS.md worry is resolved |
| L3 | `function(signal, ref, ...)` | hook callback | matches `_hook_test.lua` | **OK** |
| L4 | `loadfile(path)` + `pcall(chunk)` + `pcall(impl)` **every tick** | hot reload | correct API, but this recompiles a 35 KB file and rebuilds every closure ~10×/s on the GUI thread, forever | **NOTE — real cost, fixed** |
| L5 | `os.getenv`, `io.open`, `loadfile` | file RPC | available **unless** the user has Preferences → "sandbox all Lua scripts" on: `LuaState::sandbox()` then runs `os = nil io = nil loadfile = nil` (`libs/lua/luastate.cc:99-102`). Default is `false` (`gtk2_ardour/ui_config_vars.h:158`) | **OK by default — documented trap** |

### `bridge_impl.lua` — session read

| # | Call site | What the code does | What the real API says | Verdict |
|---|---|---|---|---|
| 1 | `Session:name()` | session name | `luabindings.cc:3088` / `:3222` | **OK** |
| 2 | `Session:get_routes():iter()` | iterate routes | `:3074` / `:3208` | **OK** |
| 3 | `r:to_track()`, `:to_midi_track()`, `:isnil()` | casts | bound as casts on Route/Track | **OK** |
| 4 | `r:muted()`, `r:gain_control():get_value()` | mixer read | bound | **OK** |
| 5 | `ARDOUR.DSP.accurate_coefficient_to_dB` / `dB_to_coefficient` | gain conversion | `:3265`,`:3263` / `:3400`,`:3398` | **OK** |
| 6 | `Temporal.TempoMap.read():tempo_at(timepos_t(0)):quarter_notes_per_minute()` | read BPM | every piece exists (`read` `:838`/`:877`, `tempo_at` `:847`/`:886`, `quarter_notes_per_minute` `:781`/`:820`) — but the last call reaches through `TempoPoint` into its `Tempo` parent, which the binding's own FIXME at `:803-804` / `:842-843` says fails. Ardour's own snippet uses `quarters_per_minute_at` (`s_tempo_map.lua:8`, bound `:884`/`:923`) | **WRONG** — this is the `inf` |
| 7 | `Temporal.timepos_t(0)` | position | ctor is `(samplepos_t)`, `:674-675` / `:713-714` — a sample-domain position | **OK** |
| 8 | `Session:nominal_sample_rate()` | sample rate | `:3045` / `:3184`, returns `samplecnt_t` (int64, `libs/temporal/temporal/types.h:52`); `long` and `long long` are both specialised in LuaBridge (`Stack.h:332`, `:428`) so the int64-max the author saw is **not explained by the binding** | **OK by source — see "Not settled"** |
| 9 | `Session:transport_sample()` | playhead | `:3043` / `:3182` | **OK** |
| 10 | `track:playlist():region_list():iter()` | regions | `Track::playlist` `:1585`/`:1629`; `Playlist::region_list` `:1508`/`:1552` | **OK** |
| 11 | `region:position():samples()`, `region:length():samples()` | region extent | `Region::position` `:1627`/`:1671`, `length` `:1629`/`:1673`; `timepos_t::samples` `:695`/`:734` | **OK** |
| 12 | `region:to_midiregion()` | cast | bound | **OK** |
| 13 | `mr:model()` (lines 468, 599) **vs** `mr:midi_source(0):model()` (line 569) | get the MidiModel | **both are bound**: `MidiRegion::model` `:1698`/`:1740` and `MidiRegion::midi_source` `:1697`/`:1739` + `MidiSource::model` `:1755`/`:1799`. GAPS.md's note "use `midi_source(0):model()`, **not** `:model()`" is **not true of stock Ardour** — it may be true of the fork | **OK, but inconsistent** |
| 14 | `ARDOUR.LuaAPI.note_list(mm)` | notes in a model | `:3213` / `:3348`; `lua_api.h:430` takes `shared_ptr<MidiModel>` | **OK** |
| 15 | `note:note()`, `:velocity()`, `:time()`, `:length()` | note fields | `NotePtr` class `:970-976` / `:1009-1015` | **OK** |
| 16 | `/ 1920.0` for ticks→beats | PPQN | `Temporal::ticks_per_beat = 1920` (`libs/temporal/temporal/types.h:65`) and is **bound as a constant** (`luabindings.cc:640` / `:679`) | **OK (magic number)** |
| 17 | `note:time()` treated as timeline beats | note position | `note:time()` is **source-relative**; `MidiRegion:model()` is the *source's* model. Equal only while the region starts at 0 with no start offset | **NOTE — latent** |
| 18 | `Session:engine()`, `:running()`, `:current_backend_name()`, `:get_dsp_load()` | diagnostics | `:3126` / `:3259` + AudioEngine class | **OK** |
| 19 | `Session:master_out()`, `:output():n_ports():n_audio()`, `:physically_connected()` | master check | all bound (`:3104`/`:3244`; `IO::physically_connected` `:1334`) | **OK** |
| 20 | `Session:transport_rolling()` | transport | `:3040` / `:3179` | **OK** |

### `bridge_impl.lua` — session write

| # | Call site | What the code does | What the real API says | Verdict |
|---|---|---|---|---|
| 21 | `Session:new_midi_track(ChanCount(midi,1), ChanCount(audio,2), true, instrument, nil, nil, 1, name, max_order, TrackMode.Normal, true)` | create MIDI track | **argument order and types are exactly right.** `session.h:744` (8.12) / `:808` (9.8): `(ChanCount in, ChanCount out, bool strict_io, shared_ptr<PluginInfo>, PresetRecord*, RouteGroup*, uint32_t how_many, string name, PresentationInfo::order_t, TrackMode, bool input_auto_connect, bool trigger_visibility=false)`. Identical shape to `share/scripts/_rgh_midi_track_trick.lua:61` and `_route_template_generic_midi.lua:71`. Omitting the 12th arg is safe: `Stack<bool>::get` is `lua_toboolean(none)` = false (`libs/lua/LuaBridge/detail/Stack.h:607`). The 6th param's C++ type changed 8→9 (`RouteGroup*` → `shared_ptr<RouteGroup>`), but `nil` is right for both | **OK — GAPS.md's top risk is a false alarm** |
| 22 | `ARDOUR.PluginInfo()` for "no instrument" | null plugin ptr | `.beginWSPtrClass<PluginInfo>` + `.addNilPtrConstructor()` (`:1147-1148` / `:1187`); exactly the idiom in `_route_template_generic_midi.lua:68` | **OK** |
| 23 | `ARDOUR.LuaAPI.list_plugins()`, `new_plugin_info(uri, type)`, `new_plugin(Session, uri, type, "")` | plugin lookup/instantiate | `:3188`,`:3190`,`:3191` / `:3323`,`:3325`,`:3326` | **OK** |
| 24 | `ARDOUR.PluginType.name(info.type)` | type name | `PluginManager::plugin_type_name(PluginType, bool short_name = **true**)` (`plugin_manager.h:123`), bound `:2418` / `:2479`. Called with one argument, LuaBridge passes `false` for the missing bool — so this silently asks for the **long** name | **NOTE — wrong flavour** |
| 25 | `t:the_instrument():to_insert()` | get PluginInsert | `the_instrument` `:1471`/`:1517`; `to_insert` is bound but **explicitly marked deprecated** (`:1868` / `:1912`) — `to_plugininsert` is the current name | **NOTE — deprecated** |
| 26 | `ARDOUR.RawMidiParser()`, `:process_byte()`, `:buffer_size()`, `:midi_buffer()` | build a program-change | `:2228-2234` | **OK** |
| 27 | `insert:write_immediate_event(Evoral.EventType.MIDI_EVENT, size, buf)` | send program change | `PluginInsert::write_immediate_event` `:2051` / `:2095` | **OK** |
| 28 | `plugin:preset_by_label()`, `:load_preset()`, `preset.valid` | preset load | bound; `PresetRecord` fields `:1839-1842` | **OK** |
| 29 | `Session:remove_route(route)` | delete track | `:3141` / `:3273` | **OK** |
| 30 | `route:replace_processor(cur, proc, nil)`, `route:add_processor_by_index(proc, 0, nil, true)` | swap/add synth | bound; `add_processor_by_index` used identically in `_rgh_midi_track_trick.lua:73` | **OK** |
| 31 | `ARDOUR.LuaAPI.ensure_midi_region(Session, mt, beats)` | make a region | **absent from 8.12 and 9.8** (full `LuaAPI` namespace dumped). Fork-only; the repo's own test asserts it | **FORK** |
| 32 | `Editor:rtav_from_route(route)` | route → view | `luainstance.cc:1030` / `:1087` | **OK** |
| 33 | `:to_timeaxisview()` | cast | `:846` / `:878` — **marked deprecated in both** | **NOTE** |
| 34 | `:to_midi_time_axis_view()` then `mtav:add_region(pos, len, true)` | make a blank region | **9.8 only**: `addCast<MidiTimeAxisView>("to_midi_time_axis_view")` `luainstance.cc:840`, `add_region` `:849`. **No `MidiTimeAxisView` binding exists in 8.12.** No alternative either: `RegionFactory` exposes only `region_by_id`/`regions`/`clone_region` (`:3151-3154` / `:3283-3286`) | **8-INCOMPAT** |
| 35 | `region:set_length(Temporal.timecnt_t(samples))` | extend region | `Region::set_length` `:1665`/`:1707`; `timecnt_t` ctor is `(samplepos_t)` `:708`/`:747` | **OK** — but not wrapped in a reversible command, so not undoable |
| 36 | `mm:new_note_diff_command("stem insert")` | undo-able note batch | `:1811` / `:1855` | **OK** |
| 37 | `cmd:add(note)` | queue a note | `NoteDiffCommand::add` `:1821` / `:1865` | **OK** |
| 38 | `ARDOUR.LuaAPI.new_noteptr(chan, Beats, Beats, pitch, vel)` | build a note | `:3212` / `:3347`; `lua_api.h:427` = `(uint8_t chan, Beats time, Beats length, uint8_t note, uint8_t velocity)`. **Argument order is exactly right.** | **OK — GAPS.md risk 3 clears** |
| 39 | `Temporal.Beats(whole, ticks)` | beat time | ctor `(int32_t, int32_t)` `:649` / `:688`. **`Temporal.Beats.from_double` also exists** (`:658`/`:697`), so GAPS.md's worry about it was unfounded | **OK** |
| 40 | `mm:apply_command(Session, cmd)` | commit notes | bound in both — but the binding comment says *"deprecated: left here in case any extant scripts use apply_command"* (`:1809` / `:1853`). Current name: `apply_diff_command_as_commit`. Its first param changed 8→9 (`Session*` → `PBD::HistoryOwner*`); passing `Session` is right for both because 9 declares `deriveClass<Session, PBD::HistoryOwner>` (`:3171`) | **NOTE — deprecated, still works** |
| 41 | `ARDOUR.LuaAPI.set_session_tempo(Session, bpm)` | set tempo | **absent from 8.12 and 9.8.** Fork-only. Stock route is `TempoMap.write_copy()` → `tm:set_tempo(Temporal.Tempo(bpm,bpm,4), timepos_t(0))` → `TempoMap.update(tm)`, per `share/scripts/s_tempo_map.lua:11-20`; bindings `:839-841`/`:878-880`, `set_tempo` `:842`/`:881`, `Tempo` ctor `(double,double,int)` `:778`/`:817` | **FORK** |
| 42 | `ARDOUR.LuaAPI.import_audio_file(Session, path, pos)` | import audio | **absent from 8.12 and 9.8.** Fork-only. Stock route is `Editor:do_import(...)` (`luainstance.cc:945`/`:1017`, signature `public_editor.h:300`/`:228`), recipe verbatim in `share/scripts/s_import_files.lua` | **FORK** |
| 43 | `tr:gain_control():set_value(coef, PBD.GroupControlDisposition.NoGroup)` | set gain | bound | **OK** |
| 44 | `route:mute_control():set_value(0/1, NoGroup)` | mute | bound | **OK** |
| 45 | `Session:locations():add_mark(pos, name, false)` | add marker | **MISSING.** `Locations` is bound (`:1251`/`:1296`) with `list / auto_loop_location / auto_punch_location / session_range_location / first_mark_after / first_mark_before / first_mark_at / mark_at / range_starts_at / add_range / remove / marks_either_side / find_all_between / next_section` — **no `add_mark`**. Ardour's own marker script uses `Editor:mouse_add_new_marker(timepos_t, LocationFlags, cue_id)` (`share/scripts/add_cdmarker.lua:5`; bound `luainstance.cc:1040`/`:1007` → `PublicEditor::add_location_mark`, `public_editor.h:260`) | **MISSING — and it leaks an open undo command** |
| 46 | `Session:begin_reversible_command(...)` / `commit_reversible_command(nil)` around it | undo wrapper | both bound (8.12 on Session `:3118-3119`; 9.8 on `PBD::HistoryOwner` `:533-534`, reached because Session derives from it). But `begin` runs **before** the nil-call in row 45, so it is never committed. `abort_empty_reversible_command` exists for exactly this (`:3122` / `:537`) and `s_tempo_map.lua:18` uses it | **WRONG — ordering** |
| 47 | `Session:request_locate(0, false, MustRoll, TRS_UI)` | play from start | `:3050`/`:3189`; `LocateTransportDisposition.MustRoll` `:2613`/`:2687`; `TransportRequestSource.TRS_UI` `:2609`/`:2683` | **OK** |
| 48 | `Session:request_roll(TRS_UI)` / `request_stop(false,false,TRS_UI)` / `goto_start(false)` | transport | `:3051`,`:3052`,`:3062` / `:3191`,`:3192`,`:3202` | **OK** |
| 49 | `Editor:undo(1)` | undo | `PublicEditor::undo(uint32_t n = 1)` (`public_editor.h:176`) bound `luainstance.cc:907`; in 9.8 it is `EditingContext::undo` (`editing_context.h:472`) bound `:972`. `Session:undo` is not bound in either — the GAPS.md note is correct | **OK** |
| 50 | `Session:save_state("", false×5)` | save | `session.h:607`/`:621` — 6 params, all defaulted; bound in `LuaBindings::session` (8.12) / `LuaBindings::non_rt` (9.8), and the editor state registers it (`luainstance.cc:786` / `:788`) | **OK** |
| 51 | `Editor:access_action("Window","toggle-stem-assistant")` | open Stem panel | `access_action` is bound (`luainstance.cc:1056`/`:1107`); the **action** is fork-only. Already `pcall`ed | **OK (fork action)** |
| 52 | `json.encode` via `string.format("%q", s)` | serialise | Lua `%q` is source-literal quoting, not JSON. Verified by running it: newline → backslash + real newline, tab → `\9`; Python `json.loads` raises `Invalid \escape` | **WRONG — measured** |
| 53 | `json.encode` inf/nan guard → `"0"` | number guard | correct and necessary, but it was papering over row 6 rather than fixing it | **NOTE** |
| 54 | `Session:sample_rate()` in `add_marker`/`locate`, `nominal_sample_rate()` elsewhere | sample rate | both bound (`:3044`,`:3045` / `:3183`,`:3184`) and they are **different values** (current vs. base) | **NOTE — inconsistent** |

---

## 2. Not settled by the Ardour source — say so out loud

1. **Why `Session:nominal_sample_rate()` read as int64-max.** The binding is
   right, the type is `int64_t`, and LuaBridge specialises both `long`
   (`Stack.h:332`) and `long long` (`:428`), so nothing in 8.12 or 9.8 explains
   it. Either it is fork-specific, or it was a different call in an earlier
   revision of the file. The corrected bridge sanity-checks the value
   (1 kHz–1 MHz) and falls back rather than trusting it. **Unresolved.**

2. **Whether `quarters_per_minute_at` alone fixes the `inf`.** The FIXME is
   Ardour's own words and the official snippet avoids the parent-class call, so
   the diagnosis is strong — but two other bundled scripts
   (`barlow_arp.lua:655`, `simple_arp.lua:434`) *do* call
   `tempo_at(pos):quarter_notes_per_minute()` directly, in debug prints where a
   wrong number would go unnoticed. So the FIXME may describe a narrower
   failure than I am attributing to it. The fix is safe either way, because
   `quarters_per_minute_at` returns a plain `double` with no cast involved.
   **Likely but unconfirmed.**

3. **Whether `Editor:do_import` returns before the import finishes.** Ardour
   runs imports on a worker thread behind a progress window. The corrected code
   diffs the route list before and after and reports `pending = true` if no new
   track appeared yet, rather than calling it a failure. **Unverified.**

4. **Whether `Locations:mark_at` returns usefully through LuaBridge when the
   `Location*` is null.** The corrected `add_marker` avoids it entirely and
   scans `locations():list()` instead, inside a `pcall`, and never fails the
   call over a failed rename. **Unverified.**

5. **What `ensure_midi_region`, `set_session_tempo` and `import_audio_file`
   actually do in the fork.** I never saw `ardour-ai`; it is not on GitHub. The
   corrected bridge calls each of them *first* when present and falls back, so
   the fork keeps working exactly as it does today.

6. **Nothing here was run inside Ardour.** I ran the corrected Lua under real
   Lua 5.3 against a mock of the Ardour API (see `verify/`), which proves
   syntax, dispatch, code-path selection and JSON validity — and proves nothing
   about the bindings themselves.

---

## 3. What to test in a live Ardour session, in order

Paste each into Window → Scripting. Ten minutes total.

```lua
-- 1. Which Ardour am I on, and does it have the region API?
print (ARDOUR.LuaAPI.ensure_midi_region, ARDOUR.LuaAPI.set_session_tempo,
       ARDOUR.LuaAPI.import_audio_file)          -- three nils on stock Ardour
local r                                          -- first MIDI track in the session
for x in Session:get_routes():iter() do
    local t = x:to_track()
    if not t:isnil() and not t:to_midi_track():isnil() then r = x break end
end
local tav = Editor:rtav_from_route(r):to_timeaxisview()
print ("MidiTimeAxisView:", tav.to_midi_time_axis_view) -- nil on 8.x

-- 2. The inf. Run both lines and compare.
local tm = Temporal.TempoMap.read()
local p  = Temporal.timepos_t(0)
print ("quarters_per_minute_at:", tm:quarters_per_minute_at (p))   -- expect 120
print ("tempo_at:...:qnpm     :", tm:tempo_at(p):quarter_notes_per_minute())
print ("tempo_at:to_tempo     :", tm:tempo_at(p):to_tempo():quarter_notes_per_minute())

-- 3. The int64-max sample rate.
print (Session:nominal_sample_rate(), Session:sample_rate())
-- (AudioEngine has no sample_rate binding; only AudioBackend does, luabindings.cc:2905)

-- 4. The marker path (this should place a marker at the playhead).
Editor:mouse_add_new_marker (Temporal.timepos_t (Session:transport_sample()),
                             ARDOUR.LocationFlags.IsMark, 0)
for l in Session:locations():list():iter() do print (l:name(), l:is_mark()) end

-- 5. The tempo transaction.
local tmw = Temporal.TempoMap.write_copy()
tmw:set_tempo (Temporal.Tempo (90, 90, 4), Temporal.timepos_t (0))
Session:begin_reversible_command ("test tempo")
Temporal.TempoMap.update (tmw)
if not Session:abort_empty_reversible_command() then
    Session:commit_reversible_command (nil)
end
print ("now:", Temporal.TempoMap.read():quarters_per_minute_at (Temporal.timepos_t(0)))
```

If line 1 prints three nils **and** `MidiTimeAxisView` is nil, you are on
Ardour 8 and note insertion cannot work until you either upgrade to 9 or draw
one empty MIDI region on the track by hand.
