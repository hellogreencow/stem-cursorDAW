# ardour-patches

Patches against **upstream Ardour** (not against this repo) that add the three
`ARDOUR.LuaAPI` functions `stem/bridge/bridge_impl.lua` calls and that no public
Ardour ships:

- `ARDOUR.LuaAPI.ensure_midi_region (session, midi_track, beats)` — find-or-create an empty MIDI region on a track
- `ARDOUR.LuaAPI.set_session_tempo (session, bpm)` — set the session tempo through a full `TempoMap` write_copy/update transaction, with an undoable `TempoCommand`
- `ARDOUR.LuaAPI.import_audio_file (session, path, sample_position)` — import an audio file onto a new track via `Session::import_files`

Until now these lived only in the private `ardour-ai` fork, so Stem ran on exactly one
machine. With one of these patches applied to a stock Ardour checkout, the bridge's
existing call sites work unchanged.

## Files

| File | What it is |
|---|---|
| `ardour-lua-api.patch` | Unified diff against Ardour tag **9.8** (commit `22ed8656c2533e325322ff11831448e5123e0d4b`) |
| `ardour-lua-api-8.12.patch` | The same change against Ardour tag **8.12** (commit `10517bff2b2c7b882b453296a939cf5d03174831`) |
| `IMPLEMENTATION.md` | Per function: what it does, which libardour C++ it calls (file:line), which versions it works on, what was verified versus what is untested |
| `UPSTREAMING.md` | Whether this is plausibly submittable to Ardour upstream, and what cuts against it |
| `VERIFY.md` | Exactly how the compile checks were run, rerunnable |

Both patches touch only `libs/ardour/ardour/lua_api.h`, `libs/ardour/lua_api.cc` and
`libs/ardour/luabindings.cc` — libardour only, no `gtk2_ardour` dependency — so the
functions are reachable from session scripts, EditorHook scripts (the bridge) and the
Lua console alike, headless included.

## How to apply

```sh
git clone https://github.com/Ardour/Ardour.git
cd Ardour
git checkout 9.8            # or 8.12
git apply /path/to/stem-cursorDAW/ardour-patches/ardour-lua-api.patch
#            ...or ardour-lua-api-8.12.patch on the 8.12 tag
./waf configure && ./waf    # normal Ardour build
```

Then run Stem against that build. `bridge_impl.lua` needs no changes: the
signatures match its existing call sites.

## What was and was not verified

- Both touched translation units (`lua_api.cc`, `luabindings.cc`) compile clean
  with `g++ -std=c++17 -fsyntax-only` on both tags (exit 0). See `VERIFY.md`.
- **Not done:** no full `waf` build, no link step, never run in a live Ardour.
  Runtime correctness is untested. `IMPLEMENTATION.md` ends with the four things
  to try first in a real session.

## Two things to know before relying on it

- `import_audio_file` **blocks**. `Session::import_files` is synchronous
  (`libs/ardour/import.cc:903`), so called from the bridge's GUI-timer hook an
  import will freeze Ardour's UI for the duration of the import.
- `set_session_tempo` records a real `TempoCommand`, so it is undoable. Ardour's
  own `s_tempo_map.lua` example script does not, so the tempo change that snippet
  teaches cannot be undone.
