-- tests/lua/mock_ardour.lua
--
-- A mock of the Ardour Lua environment, shaped to match the real bindings
-- (isnil(), iter(), the cast chain), used by tests/test_bridge_regressions.py
-- to execute stem/bridge/bridge_impl.lua for real instead of grepping it.
--
-- Derived from the verification harness written during the Ardour 8.12 / 9.8
-- source audit; every shape here is justified in BRIDGE_AUDIT.md.
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

EDITOR_HAS_MIDI_TAV = (ARDOUR_MAJOR == "9")

local function nilptr() return { isnil = function() return true end } end

local function mklist(items)
  return { iter = function()
      local i = 0
      return function() i = i + 1 return items[i] end
  end }
end

-- Every Ardour-side call the bridge makes, in order. The undo-balance test
-- reads this list; nothing else may reorder it.
CALLS = {}
local function rec(n) CALLS[#CALLS+1] = n end

-- ---------- Temporal ----------
local Beats = {}
Beats.__index = Beats
local function mkbeats(w,t) return setmetatable({w=w,t=t},Beats) end
function Beats:to_ticks() return self.w*1920 + self.t end

local tpos = {} ; tpos.__index = tpos
function tpos:samples() return self.s end
local function mktpos(s) return setmetatable({s=s},tpos) end
local tcnt = {} ; tcnt.__index = tcnt
function tcnt:samples() return self.s end
local function mktcnt(s) return setmetatable({s=s},tcnt) end

local tempomap = {
  quarters_per_minute_at = function(self,pos)
      if QPM_FAILS then error("quarters_per_minute_at unavailable on this build") end
      rec("TempoMap:quarters_per_minute_at")
      return BPM
  end,
  tempo_at = function(self,pos)
      if not TEMPO_AT_OK then
        -- The real failure: Ardour's luabindings.cc carries a FIXME above
        -- TempoPoint saying parent-class Tempo access through it fails.
        error("TempoPoint: no member named 'quarter_notes_per_minute' (the FIXME)")
      end
      rec("TempoMap:tempo_at")
      return { to_tempo = function() return {
          quarter_notes_per_minute = function() return BPM end } end }
  end,
  set_tempo = function(self,tempo,pos) rec("TempoMap:set_tempo="..tempo.npm) BPM = tempo.npm end,
}

Temporal = {
  ticks_per_beat = 1920,
  Beats = setmetatable({}, {__call=function(_,w,t) return mkbeats(w,t) end}),
  timepos_t = setmetatable({}, {__call=function(_,s) return mktpos(s) end}),
  timecnt_t = setmetatable({}, {__call=function(_,s) return mktcnt(s) end}),
  Tempo = setmetatable({}, {__call=function(_,npm,enpm,nt) return {npm=npm} end}),
  TempoMap = {
    read = function() return tempomap end,
    write_copy = function() rec("TempoMap.write_copy") return tempomap end,
    update = function(tm) rec("TempoMap.update") end,
    abort_update = function() rec("TempoMap.abort_update") end,
  },
}

-- ---------- notes / model ----------
local NOTES = {}
local model = {
  isnil = function() return false end,
  new_note_diff_command = function(self,name) rec("new_note_diff_command:"..name)
     return { pending = {}, add = function(self,n) self.pending[#self.pending+1]=n end } end,
  apply_diff_command_as_commit = function(self, sess, cmd)
     rec("apply_diff_command_as_commit")
     for _,n in ipairs(cmd.pending) do NOTES[#NOTES+1]=n end end,
  apply_command = function(self, sess, cmd) rec("apply_command(DEPRECATED)") end,
}

-- ---------- regions / playlist ----------
local REGIONS = {}
local function mkregion(len_samples)
  local r
  r = {
    name=function() return "Stem Region" end,
    position=function() return mktpos(0) end,
    length=function() return mktcnt(r._len) end,
    set_length=function(self,c) rec("region:set_length="..tostring(c.s)) r._len=c.s end,
    to_midiregion=function() return r end,
    isnil=function() return false end,
    model=function() return model end,
    midi_source=function() return { model=function() return model end } end,
    _len = len_samples,
  }
  return r
end
local playlist = { region_list = function() return mklist(REGIONS) end }

-- ---------- routes / tracks ----------
local function mkroute(name, kind)
  local midi_track
  local route
  midi_track = { isnil=function() return kind~="midi" end, playlist=function() return playlist end }
  route = {
    name=function() return name end,
    muted=function() return false end,
    gain_control=function() return {get_value=function() return 1.0 end,
                                    set_value=function() rec("gain set") end} end,
    mute_control=function() return {set_value=function() rec("mute set") end} end,
    the_instrument=function() return nilptr() end,
    isnil=function() return false end,
    to_track=function() return route end,
    to_midi_track=function() return midi_track end,
    playlist=function() return playlist end,
    active=function() return true end,
  }
  return route
end
local ROUTES = { mkroute("Chords","midi"), mkroute("Drums","midi") }

-- ---------- Session ----------
local MARKS = {}
Session = {
  name=function() return "MockSession" end,
  nominal_sample_rate=function() return SR end,
  sample_rate=function() return SR end,
  transport_sample=function() return 0 end,
  transport_rolling=function() return false end,
  get_routes=function() return mklist(ROUTES) end,
  remove_route=function(self,r) rec("remove_route") end,
  request_locate=function() rec("request_locate") end,
  request_roll=function() rec("request_roll") end,
  request_stop=function() rec("request_stop") end,
  goto_start=function() rec("goto_start") end,
  save_state=function() rec("save_state") end,
  begin_reversible_command=function(self,n) rec("BEGIN:"..tostring(n)) end,
  commit_reversible_command=function() rec("COMMIT") end,
  abort_empty_reversible_command=function() rec("ABORT_EMPTY") return false end,
  engine=function() return {running=function() return true end,
        current_backend_name=function() return "Dummy" end, get_dsp_load=function() return 3.2 end} end,
  master_out=function() return nilptr() end,
  locations=function() return { list=function() return mklist(MARKS) end } end,
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
  undo=function(self,n) rec("Editor:undo("..n..")") end,
  rtav_from_route=function(self,r)
     return { to_timeaxisview=function()
         local tav = {}
         if EDITOR_HAS_MIDI_TAV then
            tav.to_midi_time_axis_view=function()
               return { add_region=function(self,pos,len,b)
                   -- the length is what the tempo read decides; the tempo
                   -- regression test asserts on exactly this number
                   rec("MidiTimeAxisView:add_region len="..tostring(len.s))
                   REGIONS[#REGIONS+1] = mkregion(len.s) end }
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
     MARKS[#MARKS+1] = { is_mark=function() return true end,
                         start=function() return pos end,
                         set_name=function(self,n) rec("marker renamed: "..n) end }
  end,
  do_import=function(self,...) rec("do_import") ROUTES[#ROUTES+1]=mkroute("imported.wav","audio") end,
}
if NO_EDITOR then Editor = nil end

-- ---------- ARDOUR / misc ----------
ARDOUR = {
  DSP = { accurate_coefficient_to_dB=function(c) return 0.0 end, dB_to_coefficient=function(d) return 1.0 end },
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
    new_plugin=function() return nilptr() end,
    new_noteptr=function(ch, st, len, pitch, vel)
        return { note=function() return pitch end, velocity=function() return vel end,
                 time=function() return st end, length=function() return len end } end,
    note_list=function(mm) return mklist(NOTES) end,
    -- ensure_midi_region / set_session_tempo / import_audio_file are ABSENT
    -- unless STEM_MOCK_FORK=1. That absence IS stock Ardour 8.12 and 9.8:
    -- the whole ARDOUR.LuaAPI namespace was dumped in both trees during the
    -- audit and neither carries them. See BRIDGE_AUDIT.md.
  },
}

if HAVE_FORK then
  ARDOUR.LuaAPI.ensure_midi_region = function(sess, mt, beats)
      rec("fork:ensure_midi_region")
      local r = mkregion(math.ceil(beats * (60.0/BPM) * SR))
      REGIONS[#REGIONS+1] = r
      return r
  end
  ARDOUR.LuaAPI.set_session_tempo = function(sess, bpm)
      rec("fork:set_session_tempo="..bpm) BPM = bpm return true
  end
  ARDOUR.LuaAPI.import_audio_file = function(sess, path, track, pos)
      rec("fork:import_audio_file")
      ROUTES[#ROUTES+1] = mkroute("imported.wav","audio")
      return true
  end
end

Evoral = { EventType={MIDI_EVENT=1} }
PBD = { GroupControlDisposition={NoGroup=0} }
Editing = { ImportDistinctFiles=0, ImportAsTrack=1 }
C = { StringVector=function() return { push_back=function() end } end }
