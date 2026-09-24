-- tests/lua/mock_ardour.lua
--
-- A mock of the Ardour Lua environment, shaped to match the real bindings
-- (isnil(), iter(), the cast chain), used by tests/test_bridge_regressions.py,
-- tests/test_bridge_impl_contract.py and tests/test_bridge_undo.py to execute
-- stem/bridge/bridge_impl.lua for real instead of grepping it.
--
-- Derived from the verification harness written during the Ardour 8.12 / 9.8
-- source audit; every shape here is justified in BRIDGE_AUDIT.md.
--
-- STATEFUL (added for the undo work). The mock now models, from Ardour source:
--   * the session's undo HISTORY with Ardour's exact transaction semantics:
--     begin while a command is open aborts BOTH and returns (8.12
--     session_state.cc:3382, 9.8 history_owner.cc:68); commit with nothing
--     open is ignored; an empty commit is discarded; add_command with nothing
--     open is ignored (history_owner.cc:35-47). Editor:undo(n) pops the top n
--     transactions whatever they are (PublicEditor::undo).
--   * StatefulDiffCommand on regions and playlists (clear_changes snapshot vs
--     current state), with a working :undo().
--   * a per-region MidiModel with NoteDiffCommand add/remove, applied through
--     apply_diff_command_as_commit = begin + apply + commit (midi_model.cc:104).
--   * controllables with real values (gain coefficient, mute 0/1) and NO
--     history, as in AutomationControl::set_value.
--   * routes that can be created and removed (not undoable, as in Ardour),
--     processors, Locations with add_location_mark semantics (its own
--     "add marker" command; silently no-op if a mark already sits there,
--     editor_ops.cc:1978 in 9.8), and a tempo map with write_copy/update.
--   * USER EDITS (USER.*) made the way the GUI makes them, so tests can put a
--     user's edit on the stack and check Stem's undo never touches it.
--
-- The profile is chosen by environment variables so pytest can drive it:
--
--   STEM_MOCK_ARDOUR     "8" | "9"  (default "9")
--                        8 = no MidiTimeAxisView binding, as in real 8.12
--   STEM_MOCK_FORK       "1"        expose the three ardour-ai fork helpers
--   STEM_MOCK_NO_EDITOR  "1"        no global Editor (session-script context)
--   STEM_MOCK_BPM        number     what the tempo map reports (default 120)
--   STEM_MOCK_QPM_FAILS  "1"        TempoMap:quarters_per_minute_at() errors
--   STEM_MOCK_TEMPO_AT_OK "1"       tempo_at():to_tempo() works (fallback path)
--   STEM_MOCK_MARKER_FAILS "1"      Editor:mouse_add_new_marker() raises
--   STEM_MOCK_SR         number     sample rate (default 48000)
--   STEM_MOCK_RT_QUEUE   "1"        controllable writes are queued (the RT
--                        path of AutomationControl::check_rt): get_value keeps
--                        the old value until the next dispatch tick
--   STEM_MOCK_SET_TEMPO_FAILS "1"   TempoMap:set_tempo raises (write fails)
--   STEM_MOCK_DIFF_APPLY_FAILS "1"  apply_diff_command_as_commit raises
--                        BEFORE touching anything (and so does apply_command)
--
-- By default `tempo_at` RAISES. That is not laziness: Ardour's own binding
-- source carries a FIXME saying parent-class Temporal::Tempo access through a
-- TempoPoint does not work, and the old bridge read tempo exactly that way.

local function env(n) return os.getenv(n) end
local function envnum(n, d) local v = tonumber(os.getenv(n) or "") return v or d end

local ARDOUR_MAJOR   = env("STEM_MOCK_ARDOUR") or "9"
local HAVE_FORK      = env("STEM_MOCK_FORK") == "1"
local NO_EDITOR      = env("STEM_MOCK_NO_EDITOR") == "1"
local BPM            = envnum("STEM_MOCK_BPM", 120.0)
local QPM_FAILS      = env("STEM_MOCK_QPM_FAILS") == "1"
local TEMPO_AT_OK    = env("STEM_MOCK_TEMPO_AT_OK") == "1"
local MARKER_FAILS   = env("STEM_MOCK_MARKER_FAILS") == "1"
local SR             = envnum("STEM_MOCK_SR", 48000)
local RT_QUEUE       = env("STEM_MOCK_RT_QUEUE") == "1"
local SET_TEMPO_FAILS= env("STEM_MOCK_SET_TEMPO_FAILS") == "1"
DIFF_APPLY_FAILS     = env("STEM_MOCK_DIFF_APPLY_FAILS") == "1"

EDITOR_HAS_MIDI_TAV = (ARDOUR_MAJOR == "9")

local function nilptr() return { isnil = function() return true end,
                                 sameinstance = function() return false end } end

local function mklist(items)
  local snap = {}
  for i, v in ipairs(items) do snap[i] = v end
  return { iter = function()
      local i = 0
      return function() i = i + 1 return snap[i] end
  end }
end

-- Every Ardour-side call the bridge makes, in order. The undo-balance test
-- reads this list; nothing else may reorder it. Transactions opened by
-- Ardour's own code (the editor, MidiModel) are recorded as "ARDOUR_BEGIN:"
-- so "BEGIN:" keeps meaning "the bridge itself called begin_reversible_command".
CALLS = {}
local function rec(n) CALLS[#CALLS+1] = n end

local NEXT_ID = 0
local function mkid() NEXT_ID = NEXT_ID + 1 local s = "id" .. NEXT_ID
  return { to_s = function() return s end } end

-- ---------- undo history (Ardour's semantics) ----------
HISTORY = { undo = {}, redo = {}, cur = nil, warnings = {} }

local function hist_begin(name, who)
  rec((who or "BEGIN:") .. tostring(name))
  if HISTORY.cur then
    -- session_state.cc:3384 / history_owner.cc:70: abort both, return
    HISTORY.warnings[#HISTORY.warnings+1] =
      "nested begin '" .. tostring(name) .. "' aborted '" .. HISTORY.cur.name .. "'"
    rec("NESTED_BEGIN_ABORTED_BOTH")
    HISTORY.cur = nil
    return
  end
  HISTORY.cur = { name = tostring(name), cmds = {} }
end

local function hist_add(cmd)
  if not HISTORY.cur then
    HISTORY.warnings[#HISTORY.warnings+1] = "add_command without a transaction: " .. tostring(cmd.name)
    rec("ADD_COMMAND_WITHOUT_TRANSACTION")
    return
  end
  local c = HISTORY.cur.cmds
  c[#c+1] = cmd
end

local function hist_commit(cmd, who)
  rec(who or "COMMIT")
  if not HISTORY.cur then
    HISTORY.warnings[#HISTORY.warnings+1] = "commit without a transaction"
    return
  end
  if cmd then hist_add(cmd) end
  local t = HISTORY.cur
  HISTORY.cur = nil
  if #t.cmds == 0 then return end
  HISTORY.undo[#HISTORY.undo+1] = t
  HISTORY.redo = {}
end

local function hist_abort(who)
  rec(who or "ABORT")
  HISTORY.cur = nil
end

local function editor_undo(n)
  for _ = 1, n do
    local t = table.remove(HISTORY.undo)
    if not t then return end
    for i = #t.cmds, 1, -1 do t.cmds[i].undo() end
    HISTORY.redo[#HISTORY.redo+1] = t
  end
end

local function editor_redo(n)
  for _ = 1, n do
    local t = table.remove(HISTORY.redo)
    if not t then return end
    for i = 1, #t.cmds do t.cmds[i].redo() end
    HISTORY.undo[#HISTORY.undo+1] = t
  end
end

-- ---------- Stateful objects ----------
-- obj._get_state() -> snapshot, obj._set_state(snapshot)
local function make_stateful(obj)
  obj._id = obj._id or mkid()
  obj.id = function() return obj._id end
  obj.to_stateful = function() return {
      clear_changes = function() obj._before = obj._get_state() end } end
  obj.to_statefuldestructible = function() return obj end
  obj.sameinstance = function(self, other) return other == obj end
  return obj
end

local function stateful_diff(obj)
  local before = obj._before or obj._get_state()
  local after = obj._get_state()
  obj._before = nil
  local cmd
  cmd = {
    name = "StatefulDiffCommand",
    undo = function() obj._set_state(before) end,
    redo = function() obj._set_state(after) end,
  }
  -- the Lua-visible wrapper
  cmd.lua = { undo = function() cmd.undo() end,
              empty = function() return false end }
  return cmd
end

-- ---------- Temporal ----------
local Beats = {}
Beats.__index = Beats
local function mkbeats_ticks(t) return setmetatable({ticks=t}, Beats) end
function Beats:to_ticks() return self.ticks end

local tpos = {} ; tpos.__index = tpos
function tpos:samples() return self.s end
local function mktpos(s) return setmetatable({s=s},tpos) end
local tcnt = {} ; tcnt.__index = tcnt
function tcnt:samples() return self.s end
-- domain: 0 = AudioTime, 1 = BeatTime (the values real 8.12 / 9.8 report for
-- Temporal.TimeDomain.AudioTime / .BeatTime)
function tcnt:time_domain() return self.domain or 0 end
function tcnt:str() return (self.domain == 1 and "b" or "a") .. tostring(self.ticks or self.s) end
local function mktcnt(s, domain, ticks) return setmetatable({s=s, domain=domain, ticks=ticks},tcnt) end

local function mk_tempomap(get_bpm, set_bpm)
  return {
    quarters_per_minute_at = function(self,pos)
        if QPM_FAILS then error("quarters_per_minute_at unavailable on this build") end
        rec("TempoMap:quarters_per_minute_at")
        return get_bpm()
    end,
    tempo_at = function(self,pos)
        if not TEMPO_AT_OK then
          -- The real failure: Ardour's luabindings.cc carries a FIXME above
          -- TempoPoint saying parent-class Tempo access through it fails.
          error("TempoPoint: no member named 'quarter_notes_per_minute' (the FIXME)")
        end
        rec("TempoMap:tempo_at")
        local b = get_bpm()
        return { to_tempo = function() return {
            quarter_notes_per_minute = function() return b end } end }
    end,
    set_tempo = function(self,tempo,pos)
        if SET_TEMPO_FAILS then error("TempoMap::set_tempo: bad tempo") end
        rec("TempoMap:set_tempo="..tempo.npm) set_bpm(tempo.npm)
    end,
  }
end

local live_map = mk_tempomap(function() return BPM end, function(v) BPM = v end)
local WRITE_COPY = nil
local function tempo_now() return BPM end

Temporal = {
  ticks_per_beat = 1920,
  Beats = setmetatable({}, {__call=function(_,w,t)
      assert(math.type(w) == "integer" and math.type(t) == "integer",
             "Temporal.Beats(int32, int32): got " .. tostring(w) .. ", " .. tostring(t))
      return mkbeats_ticks(w*1920 + t) end}),
  timepos_t = setmetatable({}, {__call=function(_,s) return mktpos(s) end}),
  timecnt_t = setmetatable({
      -- a beat-time count (bound in both 8.12 and 9.8)
      from_ticks = function(ticks, pos)
          assert(math.type(ticks) == "integer", "timecnt_t.from_ticks(int64): got " .. tostring(ticks))
          return mktcnt(math.floor(ticks * SR * 60.0 / (BPM * 1920) + 0.5), 1, ticks)
      end,
    }, {__call=function(_,s) return mktcnt(s, 0) end}),
  TimeDomain = { AudioTime = 0, BeatTime = 1 },
  Tempo = setmetatable({}, {__call=function(_,npm,enpm,nt) return {npm=npm} end}),
  TempoMap = {
    read = function() return live_map end,
    write_copy = function()
        rec("TempoMap.write_copy")
        local copy = { bpm = BPM }
        WRITE_COPY = copy
        return mk_tempomap(function() return copy.bpm end, function(v) copy.bpm = v end), copy
    end,
    update = function(tm)
        rec("TempoMap.update")
        if WRITE_COPY then BPM = WRITE_COPY.bpm end
        WRITE_COPY = nil
    end,
    abort_update = function() rec("TempoMap.abort_update") WRITE_COPY = nil end,
  },
}

-- ---------- notes / model ----------
local function mknote(ch, st, len, pitch, vel)
  return { note=function() return pitch end, velocity=function() return vel end,
           time=function() return st end, length=function() return len end,
           channel=function() return ch end }
end

local function note_eq_value(a, b)
  return a:note() == b:note() and a:channel() == b:channel() and a:velocity() == b:velocity()
     and a:time():to_ticks() == b:time():to_ticks() and a:length():to_ticks() == b:length():to_ticks()
end

local function mkmodel()
  local m = { notes = {} }
  local function remove_note(n)
    for i, x in ipairs(m.notes) do if x == n then table.remove(m.notes, i) return true end end
    for i, x in ipairs(m.notes) do if note_eq_value(x, n) then table.remove(m.notes, i) return true end end
    return false
  end
  m.isnil = function() return false end
  m.new_note_diff_command = function(self, name)
     rec("new_note_diff_command:"..name)
     return { name = name, adds = {}, removes = {},
              add = function(self,n) self.adds[#self.adds+1]=n end,
              remove = function(self,n) self.removes[#self.removes+1]=n end }
  end
  local function apply(cmd)
     for _, n in ipairs(cmd.removes) do remove_note(n) end
     for _, n in ipairs(cmd.adds) do m.notes[#m.notes+1] = n end
  end
  local function as_command(cmd)
     return { name = cmd.name,
              undo = function()
                 for _, n in ipairs(cmd.adds) do remove_note(n) end
                 for _, n in ipairs(cmd.removes) do m.notes[#m.notes+1] = n end
              end,
              redo = function() apply(cmd) end }
  end
  -- midi_model.cc:104-110: begin (its own), apply, commit
  m.apply_diff_command_as_commit = function(self, sess, cmd)
     if DIFF_APPLY_FAILS then error("apply_diff_command_as_commit: injected failure") end
     rec("apply_diff_command_as_commit")
     hist_begin(cmd.name, "ARDOUR_BEGIN:")
     apply(cmd)
     hist_commit(as_command(cmd), "ARDOUR_COMMIT")
  end
  m.apply_command = function(self, sess, cmd)
     if DIFF_APPLY_FAILS then error("apply_command: injected failure") end
     rec("apply_command(DEPRECATED)")
     hist_begin(cmd.name, "ARDOUR_BEGIN:")
     apply(cmd)
     hist_commit(as_command(cmd), "ARDOUR_COMMIT")
  end
  -- used by USER edits (the GUI applies diffs the same way)
  m._apply_as_commit = m.apply_diff_command_as_commit
  return m
end

-- ---------- regions / playlist ----------
local function mkregion(len_samples)
  local r
  local mdl = mkmodel()
  r = {
    name=function() return "Stem Region" end,
    position=function() return mktpos(0) end,
    -- MIDI regions keep their length in BEAT time. Measured in real Ardour
    -- 8.12 and 9.8: length():str() is "b<ticks>@b0" and set_length() with an
    -- AUDIO-time count is accepted without error and changes nothing. Only a
    -- beat-time count (timecnt_t.from_ticks) resizes the region.
    length=function() return mktcnt(r._len, 1) end,
    set_length=function(self,c)
        if (c.domain or 0) ~= 1 then
            rec("region:set_length IGNORED (audio-time count on a beat-time region)="..tostring(c.s))
            return
        end
        rec("region:set_length="..tostring(c.s)) r._len=c.s
    end,
    to_midiregion=function() return r end,
    isnil=function() return false end,
    model=function() return mdl end,
    midi_source=function() return { model=function() return mdl end } end,
    _len = len_samples,
    _model = mdl,
  }
  r._get_state = function() return { len = r._len } end
  r._set_state = function(s) r._len = s.len end
  return make_stateful(r)
end

local function mkplaylist()
  local pl = { regions = {} }
  pl.region_list = function() return mklist(pl.regions) end
  pl.add_region = function(self, r) pl.regions[#pl.regions+1] = r end
  pl.remove_region = function(self, r)
     rec("playlist:remove_region")
     for i, x in ipairs(pl.regions) do if x == r then table.remove(pl.regions, i) return end end
  end
  pl._get_state = function() local c = {} for i,v in ipairs(pl.regions) do c[i]=v end return c end
  pl._set_state = function(s) local c = {} for i,v in ipairs(s) do c[i]=v end pl.regions = c end
  return make_stateful(pl)
end

-- ---------- controllables ----------
-- AutomationControl::set_value: no history. RT_QUEUE models check_rt: the
-- value lands on the next dispatch tick.
PENDING_CONTROLS = {}
local function mkcontrol(label, initial)
  local c = { _v = initial }
  c.get_value = function() return c._v end
  c.set_value = function(self, v, gcd)
     rec(label .. " set")
     if RT_QUEUE then PENDING_CONTROLS[#PENDING_CONTROLS+1] = { c = c, v = v }
     else c._v = v end
  end
  return c
end
function flush_rt_queue()
  for _, p in ipairs(PENDING_CONTROLS) do p.c._v = p.v end
  PENDING_CONTROLS = {}
end

-- ---------- processors ----------
local function mkproc(uri)
  local p
  p = { _uri = uri, isnil=function() return false end, active=function() return true end }
  p.to_plugininsert = function() return {
      isnil=function() return false end,
      plugin=function() return { isnil=function() return false end,
                                 unique_id=function() return uri end } end } end
  p.sameinstance = function(self, o) return o == p end
  return p
end

-- ---------- routes / tracks ----------
local function mkroute(name, kind, instrument_uri)
  local midi_track
  local route
  local pl = mkplaylist()
  local gain = mkcontrol("gain", 1.0)
  local mute = mkcontrol("mute", 0)
  local inst = instrument_uri and mkproc(instrument_uri) or nil
  midi_track = { isnil=function() return kind~="midi" end, playlist=function() return pl end }
  route = {
    name=function() return name end,
    muted=function() return mute._v ~= 0 end,
    gain_control=function() return gain end,
    mute_control=function() return mute end,
    the_instrument=function() return inst or nilptr() end,
    isnil=function() return false end,
    to_track=function() return route end,
    to_midi_track=function() return midi_track end,
    playlist=function() return pl end,
    active=function() return true end,
    add_processor_by_index=function(self, p, idx, err, act) rec("add_processor") inst = p return 0 end,
    remove_processor=function(self, p, err, lock)
       rec("remove_processor") if inst == p then inst = nil return 0 end return -1 end,
    replace_processor=function(self, old, new, err)
       rec("replace_processor") if inst == old then inst = new return 0 end return -1 end,
    _kind = kind, _pl = pl, _gain = gain, _mute = mute,
  }
  route._get_state = function() return {} end
  route._set_state = function() end
  return make_stateful(route)
end
ROUTES = { mkroute("Chords","midi"), mkroute("Drums","midi") }
local function remove_route(r)
  for i, x in ipairs(ROUTES) do if x == r then table.remove(ROUTES, i) return true end end
  return false
end

-- ---------- Locations ----------
MARKS = {}
local function mklocation(name, pos)
  local l
  l = { _name = name, _pos = pos }
  l.is_mark = function() return true end
  l.start = function() return mktpos(l._pos) end
  l.name = function() return l._name end
  l.set_name = function(self, n) rec("marker renamed: "..n) l._name = n end
  return make_stateful(l)
end
local locations = {
  list = function() return mklist(MARKS) end,
  remove = function(self, l)
     rec("locations:remove")
     for i, x in ipairs(MARKS) do if x == l then table.remove(MARKS, i) return end end
  end,
}
local function marks_snapshot() local c = {} for i,v in ipairs(MARKS) do c[i]=v end return c end
-- editor_ops.cc:1972 add_location_mark_with_flag: silent no-op if a mark is
-- already there; otherwise begin "add marker", add, MementoCommand, commit.
local function add_location_mark(pos_samples)
  for _, l in ipairs(MARKS) do if l._pos == pos_samples then return end end
  local name = "mark" .. (#MARKS + 1)
  hist_begin("add marker", "ARDOUR_BEGIN:")
  local before = marks_snapshot()
  MARKS[#MARKS+1] = mklocation(name, pos_samples)
  local after = marks_snapshot()
  hist_commit({ name = "MementoCommand<Locations>",
                undo = function() MARKS = {} for i,v in ipairs(before) do MARKS[i]=v end end,
                redo = function() MARKS = {} for i,v in ipairs(after) do MARKS[i]=v end end },
              "ARDOUR_COMMIT")
end

-- ---------- Session ----------
Session = {
  name=function() return "MockSession" end,
  nominal_sample_rate=function() return SR end,
  sample_rate=function() return SR end,
  transport_sample=function() return 0 end,
  transport_rolling=function() return false end,
  get_routes=function() return mklist(ROUTES) end,
  route_by_name=function(self, n) for _, r in ipairs(ROUTES) do if r:name()==n then return r end end return nilptr() end,
  remove_route=function(self,r) rec("remove_route") remove_route(r) end,
  request_locate=function() rec("request_locate") end,
  request_roll=function() rec("request_roll") end,
  request_stop=function() rec("request_stop") end,
  goto_start=function() rec("goto_start") end,
  save_state=function() rec("save_state") end,
  begin_reversible_command=function(self,n) hist_begin(n, "BEGIN:") end,
  commit_reversible_command=function(self, cmd) hist_commit(cmd, "COMMIT") end,
  abort_reversible_command=function() hist_abort("ABORT") end,
  abort_empty_reversible_command=function()
     rec("ABORT_EMPTY")
     if not HISTORY.cur or #HISTORY.cur.cmds == 0 then HISTORY.cur = nil return true end
     return false
  end,
  -- A BOOLEAN, not a count (session.h: `bool collected_undo_commands () const`).
  -- Measured in real Ardour 8.12 and 9.8: false with nothing open or an open
  -- but empty command, true once the open command holds a change.
  collected_undo_commands=function() return HISTORY.cur ~= nil and #HISTORY.cur.cmds > 0 end,
  add_command=function(self, cmd) hist_add(cmd) end,
  add_stateful_diff_command=function(self, obj)
     rec("add_stateful_diff_command")
     local cmd = stateful_diff(obj)
     hist_add(cmd)
     return cmd.lua
  end,
  engine=function() return {running=function() return true end,
        current_backend_name=function() return "Dummy" end, get_dsp_load=function() return 3.2 end} end,
  master_out=function() return nilptr() end,
  locations=function() return locations end,
  new_midi_track=function(self, ci, co, strict, inst, pset, grp, howmany, name, order, mode, autoconn)
     rec(string.format("new_midi_track name=%s howmany=%s strict=%s",
         tostring(name), tostring(howmany), tostring(strict)))
     local t = mkroute(name,"midi")
     ROUTES[#ROUTES+1] = t
     return mklist({t})
  end,
}

-- ---------- Editor ----------
Editor = {
  access_action=function() rec("access_action") end,
  undo=function(self,n) rec("Editor:undo("..n..")") editor_undo(n) end,
  redo=function(self,n) rec("Editor:redo("..n..")") editor_redo(n) end,
  rtav_from_route=function(self,r)
     return { to_timeaxisview=function()
         local tav = {}
         if EDITOR_HAS_MIDI_TAV then
            tav.to_midi_time_axis_view=function()
               return { add_region=function(self,pos,len,commit)
                   -- the length is what the tempo read decides; the tempo
                   -- regression test asserts on exactly this number
                   rec("MidiTimeAxisView:add_region len="..tostring(len.s))
                   -- midi_time_axis.cc:1677: begin (if commit), clear_changes,
                   -- add_region, StatefulDiffCommand(playlist), commit
                   local pl = r._pl
                   if commit then hist_begin("create region", "ARDOUR_BEGIN:") end
                   pl.to_stateful().clear_changes()
                   pl:add_region(mkregion(len.s))
                   hist_add(stateful_diff(pl))
                   if commit then hist_commit(nil, "ARDOUR_COMMIT") end
               end }
            end
         end
         return tav
     end }
  end,
  mouse_add_new_marker=function(self,pos,flags,cue)
     if MARKER_FAILS then
        -- what the OLD bridge hit on every single call: a nil method. The
        -- point of the test is what state Ardour is left in when this throws.
        -- Real Lua errors carry "file:line:" and often a traceback, i.e. they
        -- contain NEWLINES and TABS. That is precisely what broke the old
        -- string.format("%q") encoder, so the mock reproduces it faithfully.
        error("attempt to call a nil value (method 'mouse_add_new_marker')\n"
              .. "stack traceback:\n\t[C]: in ?")
     end
     rec("mouse_add_new_marker")
     add_location_mark(pos:samples())
  end,
  do_import=function(self,...) rec("do_import") ROUTES[#ROUTES+1]=mkroute("imported.wav","audio") end,
}
Editor.add_location_mark = Editor.mouse_add_new_marker
if NO_EDITOR then Editor = nil end

-- ---------- ARDOUR / misc ----------
local AUDIBLE = { ["http://gareus.org/oss/lv2/gmsynth"] = true,
                  ["https://community.ardour.org/node/7596"] = true }
ARDOUR = {
  DSP = { accurate_coefficient_to_dB=function(c)
              if c <= 0 then return -math.huge end
              return 20 * math.log(c, 10) end,
          dB_to_coefficient=function(d) return 10 ^ (d / 20) end },
  ChanCount=function() return {} end, DataType=function() return {} end,
  PluginInfo=function() return nilptr() end,
  Track=function() return nilptr() end,
  PluginType={ LV2=1, name=function(t,short) return "LV2" end },
  PresentationInfo={ max_order=4294967295 },
  TrackMode={ Normal=0 },
  TransportRequestSource={ TRS_UI=0 },
  LocateTransportDisposition={ MustRoll=0, MustStop=1 },
  LocationFlags={ IsMark=1 },
  SrcQuality={SrcBest=0}, MidiTrackNameSource={SMFFileAndTrackName=0},
  MidiTempoMapDisposition={SMFTempoIgnore=0},
  RawMidiParser=function() return {} end,
  LuaAPI = {
    list_plugins=function() return mklist({}) end,
    new_plugin_info=function() return nilptr() end,
    new_plugin=function(sess, uri, t, preset)
        if AUDIBLE[uri] then return mkproc(uri) end
        return nilptr() end,
    new_noteptr=function(ch, st, len, pitch, vel) return mknote(ch, st, len, pitch, vel) end,
    note_list=function(mm) return mklist(mm.notes) end,
    -- ensure_midi_region / set_session_tempo / import_audio_file are ABSENT
    -- unless STEM_MOCK_FORK=1. That absence IS stock Ardour 8.12 and 9.8:
    -- the whole ARDOUR.LuaAPI namespace was dumped in both trees during the
    -- audit and neither carries them. See BRIDGE_AUDIT.md.
  },
}

if HAVE_FORK then
  -- the shapes of the ardour-patches PR (ardour-patches/ardour-lua-api.patch):
  -- each commits its own reversible command.
  ARDOUR.LuaAPI.ensure_midi_region = function(sess, mt, beats)
      rec("fork:ensure_midi_region")
      local pl = mt:playlist()
      local r = mkregion(math.ceil(beats * (60.0/BPM) * SR))
      hist_begin("create MIDI region", "ARDOUR_BEGIN:")
      pl.to_stateful().clear_changes()
      pl:add_region(r)
      hist_add(stateful_diff(pl))
      hist_commit(nil, "ARDOUR_COMMIT")
      return r
  end
  ARDOUR.LuaAPI.set_session_tempo = function(sess, bpm)
      rec("fork:set_session_tempo="..bpm)
      local before = BPM
      hist_begin("Set Tempo", "ARDOUR_BEGIN:")
      BPM = bpm
      hist_commit({ name = "TempoCommand",
                    undo = function() BPM = before end,
                    redo = function() BPM = bpm end }, "ARDOUR_COMMIT")
      return true
  end
  ARDOUR.LuaAPI.import_audio_file = function(sess, path, pos)
      rec("fork:import_audio_file")
      ROUTES[#ROUTES+1] = mkroute("imported.wav","audio")
      return "imported.wav"
  end
end

Evoral = { EventType={MIDI_EVENT=1} }
PBD = { GroupControlDisposition={NoGroup=0} }
Editing = { ImportDistinctFiles=0, ImportAsTrack=1 }
C = { StringVector=function() return { push_back=function() end } end }

-- ======================================================================
-- USER edits: what a person does in Ardour's GUI, done the GUI's way
-- ======================================================================
local function find(name) for _, r in ipairs(ROUTES) do if r:name() == name then return r end end end
local function beats_ticks(b) return math.floor(b * 1920 + 0.5) end

USER = {}
-- draw a region by hand (MidiTimeAxisView::add_region with commit)
function USER.draw_region(a)
  local r = find(a.track)
  hist_begin("create region", "ARDOUR_BEGIN:")
  r._pl.to_stateful().clear_changes()
  r._pl:add_region(mkregion(a.samples or 480000))
  hist_add(stateful_diff(r._pl))
  hist_commit(nil, "ARDOUR_COMMIT")
end
-- add a note with the pencil (a NoteDiffCommand committed on its own)
function USER.add_note(a)
  local r = find(a.track)
  local region = r._pl.regions[1]
  local m = region._model
  local cmd = m:new_note_diff_command("add note")
  cmd:add(mknote(a.channel or 0, mkbeats_ticks(beats_ticks(a.start_beat)),
                 mkbeats_ticks(beats_ticks(a.length_beats or 1)), a.pitch, a.velocity or 100))
  local save = DIFF_APPLY_FAILS
  DIFF_APPLY_FAILS = false
  m:apply_diff_command_as_commit(Session, cmd)
  DIFF_APPLY_FAILS = save
end
-- delete a note (by pitch/start)
function USER.delete_note(a)
  local r = find(a.track)
  local m = r._pl.regions[1]._model
  for _, n in ipairs(m.notes) do
    if n:note() == a.pitch and n:time():to_ticks() == beats_ticks(a.start_beat) then
      local cmd = m:new_note_diff_command("delete note")
      cmd:remove(n)
      local save = DIFF_APPLY_FAILS
      DIFF_APPLY_FAILS = false
      m:apply_diff_command_as_commit(Session, cmd)
      DIFF_APPLY_FAILS = save
      return
    end
  end
  error("USER.delete_note: no such note")
end
function USER.add_marker(a) add_location_mark(a.samples) end
function USER.set_gain(a) find(a.track)._gain._v = a.value end
function USER.set_mute(a) find(a.track)._mute._v = a.value end
function USER.set_tempo(a) BPM = a.bpm end   -- tempo dialog without undo, as the snippet does
function USER.resize_region(a)
  local region = find(a.track)._pl.regions[1]
  hist_begin("trim region", "ARDOUR_BEGIN:")
  region.to_stateful().clear_changes()
  region._len = a.samples
  hist_add(stateful_diff(region))
  hist_commit(nil, "ARDOUR_COMMIT")
end
function USER.ctrl_z() editor_undo(1) end
-- something else (a plugin GUI, another script) left a command open with
-- work already in it
function USER.open_dangling_command()
  hist_begin("dangling (another script)", "ARDOUR_BEGIN:")
  hist_add({ name = "their pending work", undo = function() end, redo = function() end })
end

-- ======================================================================
-- state snapshot for tests (JSON)
-- ======================================================================
local function jq(s) return '"' .. tostring(s):gsub('[%c"\\]', function(c)
  return string.format('\\u%04x', string.byte(c)) end) .. '"' end
local function jenc(v)
  local t = type(v)
  if t == "table" then
    if #v > 0 or next(v) == nil and getmetatable(v) == nil and v.__obj == nil then
      local p = {} for _, x in ipairs(v) do p[#p+1] = jenc(x) end
      return "[" .. table.concat(p, ",") .. "]"
    end
    local keys = {} for k in pairs(v) do keys[#keys+1] = tostring(k) end
    table.sort(keys)
    local p = {} for _, k in ipairs(keys) do p[#p+1] = jq(k) .. ":" .. jenc(v[k]) end
    return "{" .. table.concat(p, ",") .. "}"
  elseif t == "string" then return jq(v)
  elseif t == "number" then
    if v ~= v or v == math.huge or v == -math.huge then return "null" end
    if math.type(v) == "integer" then return tostring(v) end
    return string.format("%.12g", v)
  elseif t == "boolean" then return tostring(v)
  else return "null" end
end

function mock_state_json()
  local routes = {}
  for _, r in ipairs(ROUTES) do
    local regions = {}
    for _, g in ipairs(r._pl.regions) do
      local notes = {}
      for _, n in ipairs(g._model.notes) do
        notes[#notes+1] = { pitch = n:note(), start = n:time():to_ticks(),
                            length = n:length():to_ticks(), velocity = n:velocity(),
                            channel = n:channel() }
      end
      table.sort(notes, function(a, b)
        if a.start ~= b.start then return a.start < b.start end
        if a.pitch ~= b.pitch then return a.pitch < b.pitch end
        return a.velocity < b.velocity end)
      regions[#regions+1] = { length = g._len, notes = notes }
    end
    local inst = r:the_instrument()
    routes[#routes+1] = { name = r:name(), gain = r._gain._v, mute = r._mute._v,
                          instrument = (not inst:isnil()) and inst._uri or "", regions = regions }
  end
  local marks = {}
  for _, l in ipairs(MARKS) do marks[#marks+1] = { name = l._name, pos = l._pos } end
  local undo, redo = {}, {}
  for _, t in ipairs(HISTORY.undo) do undo[#undo+1] = t.name end
  for _, t in ipairs(HISTORY.redo) do redo[#redo+1] = t.name end
  return jenc({ routes = routes, tempo = BPM, markers = marks,
                history = { undo = undo, redo = redo,
                            open = HISTORY.cur and HISTORY.cur.name or "",
                            warnings = HISTORY.warnings },
                journal_depth = (type(STEM_UNDO_JOURNAL) == "table") and #STEM_UNDO_JOURNAL.entries or -1 })
end
