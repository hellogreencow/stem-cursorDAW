# UPSTREAMING.md — could this go to Ardour as a PR?

Short answer: **two of the three, plausibly yes. One, probably not as
written.** And the reason it matters is that an accepted upstream patch is
the difference between `stem-cursorDAW` running on one Mac and running
anywhere.

None of what follows was discussed with any Ardour developer. It is my read
of the project from its source, its own Lua scripts and the shape of the
existing `LuaAPI` surface — not a report of anyone's opinion.

## What helps the case

**It is a libardour patch.** It adds nothing to `gtk2_ardour/`, depends on no
GUI class, and therefore works in headless session scripts as well as in the
editor. That is the direction Ardour's own Lua surface leans: the
`ARDOUR.LuaAPI` namespace is deliberately session-level, and the editor-only
bindings are quarantined in `gtk2_ardour/luainstance.cc`. A patch that moves
capability *out* of the GUI is an easier argument than one that adds more
into it.

**Two of the three close real gaps that Ardour's own scripts trip over.**

- `ensure_midi_region` fills a genuine hole: as of 9.8 there is no way to
  create a MIDI region from Lua without the editor, and on 8.x no way at all.
  `RegionFactory` exposes only `region_by_id`, `regions` and `clone_region`
  to Lua, and `create_midi_source*` is not bound. Any script that wants to
  write notes must first obtain a region it cannot make.
- `set_session_tempo` is arguably a **bug fix** dressed as a feature.
  Ardour's own snippet `share/scripts/s_tempo_map.lua:14-18` demonstrates the
  tempo transaction with `begin_reversible_command` → `update` →
  `abort_empty_reversible_command` and **adds no command**, so the change it
  teaches is not undoable. The correct pattern is in libardour at
  `session.cc:7826-7857`. A patch that wraps the correct pattern in one
  binding — and, ideally, fixes the snippet too — is the kind of thing that
  lands.

**It matches house style.** Doxygen comments on the declarations in the same
form as the neighbours, tabs, `ARDOUR::LuaAPI::` free functions taking
`Session*` first, plain `.addFunction` registrations placed next to
`new_send`. Nothing invents a new convention.

## What works against it

**Upstream will likely want different signatures.** I implemented the
signatures `bridge_impl.lua` calls, because that is what frees Oli's project
today. For upstream, the objections almost write themselves:

- `ensure_midi_region (Session*, MidiTrack, double beats)` hardcodes position
  zero and takes length as a bare `double` of quarter notes. Ardour has spent
  the whole 7.x/8.x cycle moving to typed time — `timepos_t`, `timecnt_t`,
  `Temporal::Beats` — and all three are already bound to Lua. The
  upstream-shaped version is
  `ensure_midi_region (Session*, shared_ptr<MidiTrack>, timepos_t const& pos, timecnt_t const& length)`,
  and a reviewer would be right to ask for it.
- `import_audio_file` returning a **stringified `PBD::ID`** is un-Ardour-like.
  Every other binding that produces an object returns the object; the natural
  return is `std::shared_ptr<Track>`, which Lua can nil-check and use
  directly. The string exists only because Oli's Lua wanted a string.
- The name `ensure_*` has no precedent in the `LuaAPI` namespace.

None of these is fatal, but together they mean the honest expectation is
"accepted after a round of signature changes", not "merged as sent". And
changed signatures break Oli's call sites, which is the tension to be aware
of: **the version that upstreams cleanly is not the version that drops into
`bridge_impl.lua` unmodified.** The good news is that the Lua-side fix is
three call sites.

**`import_audio_file` is the weak one, and the reason is the blocking call.**
`Session::import_files()` runs synchronously in the calling thread
(`libs/ardour/import.cc:903`, sets `status.done` on its last line). Ardour's
GUI is careful about this — it runs imports on a dedicated thread
(`editor_audio_import.cc:586`) precisely so the UI survives. A binding that
invites a script running on the GUI timer to block it is something a
maintainer may reasonably decline, and adding "it blocks" to the docstring
does not make the freeze shorter. The upstream-acceptable shape is probably a
non-blocking variant with an `ImportStatus` handle exposed to Lua so the
script can poll `progress` and `done` — which is a bigger patch than this
one, and a design conversation rather than a code drop.

**Three unrelated functions in one patch.** Upstream would likely prefer
three commits, or three PRs. Easy to do; worth doing before sending.

**It is not compile-proven end to end.** See IMPLEMENTATION.md — one
translation unit is front-end checked on both tags; no full build was done
and nothing was run in Ardour. Sending an unbuilt patch to a project of
Ardour's seriousness would be rude. **Anyone taking this upstream should
build it and exercise it in a live session first.** That is the single
biggest gap between this patch and a submittable one, and it is a gap of
compute, not of understanding.

## What I would actually do

1. **Don't send it yet.** Build it, run the four checks at the end of
   IMPLEMENTATION.md, and fix whatever a live session finds.
2. **Send `set_session_tempo` first, on its own.** It is the strongest of the
   three: it closes a real gap, it does the transaction correctly, and it can
   be framed alongside a fix to `s_tempo_map.lua`'s missing `TempoCommand` —
   which gives the patch an obvious, verifiable bug behind it. Ask on the
   Ardour developer list or IRC before opening the PR; that is how this
   project prefers to be approached.
3. **Then `ensure_midi_region`, respecified with `timepos_t`/`timecnt_t`.**
   Lead with the gap, not the API: "there is currently no way to create a
   MIDI region from Lua without the GUI" is the sentence that makes the case.
4. **Hold `import_audio_file` back** as fork-local, or open a discussion about
   a non-blocking import binding rather than a PR. This one I would call
   fork-local until someone upstream says otherwise.

## The part that matters for Stem regardless

Even if upstream takes none of it, this patch changes Oli's situation
materially, and this is the real point:

- It is **public, readable C++** against a named upstream tag, so anyone can
  build a working Ardour for Stem. The dependency stops being "a private repo
  on one Mac" and becomes "apply a 317-line patch and build" — which is a
  README instruction, not a blocker.
- It works on **8.12 and 9.8**, so the README's "Ardour 8.x" claim can become
  true rather than aspirational.
- It removes the need to publish `ardour-ai` at all, if Oli would rather not.

A fork-local patch that anyone can apply is a completely different object
from an unpublished fork. The first is reproducible; the second is a single
point of failure with a person attached to it.
