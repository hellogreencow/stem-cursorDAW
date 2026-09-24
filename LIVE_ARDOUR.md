# Stem bridge against real Ardour (8.12 and 9.8)

Branch `tab/live-ardour-test` = PR #5 (`tab/lua-undo-wrapping`, itself on #2) with PR #6
(`tab/midi-editing-tools`, on #4) merged in. Everything below was run on 2026-09-24 against
stock Ardour, not the mock.

## How Ardour ran

- **Ardour 8.12**: Debian trixie package `ardour 1:8.12.0+ds-1`. **Ardour 9.8**: Debian forky package
  `ardour 1:9.8.0+ds-1`. Each was installed into its own minimal Debian chroot, because the box is Debian 12
  and its own `ardour` is 7.3. Forky's debootstrap stopped on an unconfigured `libonnxruntime1.23`
  (it was missing `libre2-11`). `apt-get -f install` inside the chroot fixed that.
- Both ran headless under Xvfb (1600x1000) on the **None (Dummy)** audio backend. Debian hides that backend
  until the rc option `hide-dummy-backend` is 0. The engine dialog was clicked through once with xdotool.
- **Bridge loading.** The bridge was loaded the way `stem_bridge.lua` is meant to be: as an EditorHook on
  `LuaSignal.LuaTimerDS`, registered in `~/.config/ardour{8,9}/ui_scripts`. That is the file Script Manager
  writes. It was written by hand here: the base64 of Ardour's own serialisation of the script plus the
  `string.dump` of `factory`, with the signal bitset (LuaTimerDS = bit 47 of 50 in 8.12, bit 49 of 52 in 9.8).
- **Test-only wrapper.** `~/.stem/bridge_impl.lua` was a small wrapper. It passes every request to the
  repo's `stem/bridge/bridge_impl.lua` unchanged, and adds a side channel that evaluates Lua inside Ardour.
  That channel was used for reading state and for making "user" edits.
- **Python side.** `ArdourBridge` ran from `stem/bridge/ardour.py` with `HOME` pointed at the same `~/.stem`,
  so it used the real file mailbox.

## Handler-by-handler

"Mock claim" is what the mock suite said before this branch: 309 passed. Every handler below was
covered and claimed to work, with undo restoring the prior state.

| Handler | Mock claim | Real 8.12 | Real 9.8 |
|---|---|---|---|
| create_midi_track | creates the track, journals it, undo removes it | Works. ACE Reasonable Synth. Undo removes the track. | **Before fix: Ardour crashed.** Kernel log: `ArdourGUI[41973]: segfault at 0 ip 00007f158199dbfc sp 00007ffc58f38670 error 4 in libardour.so.3.0.0`. After 1a8d4e3: works, undo removes it. |
| insert_midi_notes, notes inside a region | works, undo removes notes | Works on a region drawn by hand (see limits). Undo removes the notes and keeps the user's region. | Works. The bridge makes the region itself (`MidiTimeAxisView:add_region`, 120000 samples = `b9600`). Undo removes the notes and that region. |
| insert_midi_notes, no region on 8.x | refuses with an explanation | Refuses: `this Ardour has no MidiTimeAxisView Lua binding (added in Ardour 9; absent in 8.x). Creating an empty MIDI region from Lua is not possible on this build — upgrade to Ardour 9, or draw one empty MIDI region on the track by hand once and Stem will reuse and extend it.` Matches the mock. | n/a |
| insert_midi_notes, notes past region end | region grows to fit | **Before fix: region stayed 174087 samples (`b13927`).** A note at beat 16 was written outside it. After 11c32d8: 155637 -> 456000 (`b36480`). Undo -> 155637 exactly. | **Before fix: region stayed 120000 (`b9600`).** After: 120000 -> 456000 (`b36480`). Undo -> 120000. |
| replace_midi_notes (beats 0-2 -> one note) | one step, undo exact | removed 2, added 1. Undo gives back the exact three notes. | same |
| get_midi_notes | channel preserved | channels 0/3/9 read back exactly | same |
| set_tempo 90 | tempo-map write, not on Ardour's stack | 120 -> 90 (`via: tempomap`). Undo -> 120. | same |
| set_track_gain -6 dB | controllable write, undo restores | 1.0 -> 0.5012, read back immediately. Undo -> 1.0. | same |
| set_track_mute | same | 0 -> 1. Undo -> 0. | same |
| add_marker "Drop" at 4 s | location added, undo removes | at 192000 samples. Undo removes it. | same |
| import_audio (2 s 440 Hz wav, ffmpeg) | stock `Editor:do_import`, flagged UNVERIFIED on whether it is synchronous | **Synchronous.** Track "tone" with its 96000-sample region exists when the handler returns (`pending: false`). Undo removes the track. | same |
| import_audio, missing file | not covered | Returns `{"ok": true, "pending": true, "track_id": ""}` and no error. ArdourBridge turns this into `Ardour did not create an audio track`. **Not fixed.** | same |
| add_instrument journal=true, track already has one | no-op entry | `already: true`, no-op entry, undo is a no-op | same |
| add_instrument journal=true, track without one | adds, undo removes | Adds ACE Reasonable Synth. Undo removes it. The plugin identity check (`sameinstance`) works live. | same |
| open-command guard (`collected_undo_commands`) | a count > 0 means refuse | **Returns a boolean.** `type(...) == "boolean"`; false with nothing open or an empty open command, true once a diff is added. **Before fix, the guard never fired.** With a user command left open (holding a region mute), insert_midi_notes went ahead and the user's pending command was gone (collected went true -> false). After 3e82bce: refuses. | same |

### Undo cases (fixed bridge, both versions: all passed)

- **A user edit made first stays intact after Stem's undo.** The user drew a region (8.12). The user muted
  the region as an Ardour reversible command, committed. Stem then set gain -6 and Stem undid it. Gain went
  back to 1.0 and the region stayed muted. After Stem's actions, Ardour's own `Editor:undo(1)` still undid
  the user's latest command cleanly.
- **Undo refuses when the user changed the same thing since.**
  - Gain: Stem set -6 dB, then the user set 0.25. Undo returned
    `cannot undo 'gain on Keys': it has been changed since Stem set it; not undoing over your change`,
    and gain stayed 0.25.
  - Notes: Stem inserted two notes, then the user deleted one with an Ardour note-diff command. Undo returned
    `cannot undo 'insert notes': 1 of the 2 notes Stem wrote have been changed or deleted since; not undoing over your edit`.
  - Instrument: the user swapped Stem's instrument for a new instance. Undo returned
    `cannot undo 'add instrument on Keys': the instrument on that track has been changed since`.
- **A failing mutation leaves no open reversible command.** These failed with Ardour's command state clean
  (`collected_undo_commands() == false`), the journal depth unchanged and the session unchanged:
  - bad pitch 200: `insert_midi_notes: note 1 has bad pitch 200 (0-127)`
  - unknown track: `no such track: Nope`
  - negative length: `replace_midi_notes: note 1 has bad length_beats -1`
  - `set_tempo 0`: `bad bpm: 0`
  - gain on an unknown track
- **With a user command open and holding a change,** insert_midi_notes, replace_midi_notes, set_tempo and
  add_marker all refused with `Ardour has an unfinished edit open (a reversible command that already holds changes); finish or cancel it before Stem edits the session`.
  The user's command stayed open. When the user then committed it, their change was kept.

### Python ArdourBridge, end to end (real mailbox)

- **9.8, new track made through the tools:** quantize (grid 0.5), transpose (+3) and edit_midi_notes
  (repitch + revelocity one note, delete another). Each ran, each was one step, and each undo restored the
  exact notes.
- **8.12, on the user-drawn region:** the same three tools. Inserting at beats 8-10 grew the region through
  the fixed path (168000 -> 278400 samples).
- **Found while doing this:** the `insert_midi_notes` tool dropped the MIDI channel. It was sent channel 5
  and Ardour got 0. The tool's `NoteSpec` had no `channel` field. Fixed in 968ac3f. After the fix, channel 5
  survives the insert, quantize, transpose and edit.

## Fixes (commits on tab/live-ardour-test, author Oliver Morley)

| Commit | What |
|---|---|
| 3e82bce | Open-command guard reads Ardour's boolean. The mock now returns a boolean. |
| 11c32d8 | Region grow uses a beat-time length (`timecnt_t.from_ticks`) and errors if the resize does not take. Mock MIDI regions are beat-time and ignore audio-time `set_length`. |
| 1a8d4e3 | `new_midi_track` gets `ARDOUR.RouteGroup()` on 9.x and nil on 8.x. The mock raises where 9.8 segfaulted. |
| 968ac3f | The `insert_midi_notes` tool passes `channel` through. |

- **Pytest.** Before: 309 passed / 0 failed / 7 skipped. After: 317 passed / 0 failed / 7 skipped. The 7
  skipped tests need the private fork.
- **The mock catches the old bugs.** With the realistic mock, the pre-fix bridge fails 10 existing tests plus
  the new ones. Measured with `STEM_BRIDGE_IMPL_UNDER_TEST`.

## Confirmed but not fixed

- **Note times are source-relative.** On 8.12 the hand-drawn region starts at sample 6150 (`b492`). A note
  Stem writes at "beat 0" plays about 0.26 beats later, and `get_midi_notes` reports source beats.
  `get_midi_notes` already flags this as unverified. It is now verified. It only matters for regions that
  do not start at 0 with no offset, which the bridge never creates itself.
- **import_audio on a missing file** returns ok/pending instead of an error (see table).
- **Quantize report wording.** Quantize's `verified.changes` said "7 notes changed in place" when 3 notes
  changed (`notes_changed: 3`). This is only the report's wording.
- **Tick rounding.** 0.07 beats is stored as 134 ticks (0.0698). This is expected at 1920 PPQN.

## Limits

- Debian's builds, not the ardour.org binaries.
- Dummy backend and Xvfb: nothing was listened to, and no audio device was involved.
- The engine dialog was clicked through with xdotool. On 8.12 the one empty MIDI region was drawn with the
  mouse, because 8.12 cannot create one from Lua.
- The other "user edits" were made through Ardour's Lua API as real reversible commands, not with the GUI.
- **Not tested:**
  - gain automation
  - tempo maps with more than one tempo
  - the fork-only helpers
  - fix_silent_instruments
  - repair_midi_region
  - delete_track
- The 9.8 crash was reproduced once, before the fix. After the fix, create_midi_track ran on 9.8 five
  times without a crash.
