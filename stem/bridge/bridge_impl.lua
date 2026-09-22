-- Stem bridge implementation (bridge_impl.lua)
--
-- Audited call-by-call against the real Ardour Lua bindings:
--   Ardour 8.12  (git tag 8.12, commit 10517bff2b2c7b882b453296a939cf5d03174831)
--   Ardour 9.8   (git tag 9.8,  commit 22ed8656c2533e325322ff11831448e5123e0d4b)
-- See BRIDGE_AUDIT.md for the file:line citation behind every verdict.
--
-- STRUCTURE CHANGE: everything except dispatch() now runs at CHUNK level, i.e.
-- once per load, not once per 100 ms tick. The chunk still returns a function,
-- so the old loader keeps working (just at the old cost); the new loader caches
-- the returned function and calls it per tick.
--
-- PORTABILITY: three helpers this bridge used to call unconditionally —
--   ARDOUR.LuaAPI.ensure_midi_region, ARDOUR.LuaAPI.set_session_tempo,
--   ARDOUR.LuaAPI.import_audio_file
-- do not exist in upstream Ardour 8.12 or 9.8 (whole LuaAPI namespace dumped;
-- the namespace opens at libs/ardour/luabindings.cc:3183 in 8.12 and :3318 in 9.8). They are
-- additions in the private ardour-ai fork. Each is now tried first and falls
-- back to a stock-Ardour path, so this file runs on the fork AND on stock
-- Ardour 9.x, and fails with a clear message rather than silently on 8.x.

-- ======================================================================
-- one-time setup (chunk level)
-- ======================================================================

local home = os.getenv ("HOME")
local dir  = home .. "/.stem"
-- NEVER os.execute() here: this used to be hot-reloaded every tick -> a
-- per-tick subprocess wedges the GUI thread and leaks memory. ~/.stem exists.
local req_path  = dir .. "/request.json"
local resp_path = dir .. "/response.json"

-- Temporal.ticks_per_beat is a bound constant (luabindings.cc:640 in 8.12,
-- :679 in 9.8) and equals 1920 (libs/temporal/temporal/types.h:65). Read it
-- rather than hard-coding, so a future PPQN change does not silently halve
-- every note length.
local TPB = 1920
do
    local ok, v = pcall (function () return Temporal.ticks_per_beat end)
    if ok and type (v) == "number" and v > 0 then TPB = v end
end

-- ----------------------------------------------------------------------
-- minimal JSON
-- ----------------------------------------------------------------------
-- FIX: the previous encoder used string.format("%q", s) for strings. Lua's %q
-- is Lua-source-literal quoting, NOT JSON: a newline is emitted as a backslash
-- followed by a REAL newline, and control bytes come out as \ddd. Any string
-- with a newline produced invalid JSON — and the error path is exactly where
-- newlines live (pcall messages carry "file:line: message" plus tracebacks),
-- so every interesting failure came back unparseable on the Python side.
-- utf8.char is Lua 5.3+ (Ardour bundles 5.3/5.4); the manual encoder keeps this
-- file loadable on an older interpreter rather than erroring at chunk level.
local utf8_char
if utf8 and utf8.char then
    utf8_char = utf8.char
else
    utf8_char = function (cp)
        if cp < 0x80 then return string.char (cp) end
        if cp < 0x800 then
            return string.char (0xC0 + math.floor (cp / 0x40), 0x80 + cp % 0x40)
        end
        if cp < 0x10000 then
            return string.char (0xE0 + math.floor (cp / 0x1000),
                                0x80 + math.floor (cp / 0x40) % 0x40,
                                0x80 + cp % 0x40)
        end
        return string.char (0xF0 + math.floor (cp / 0x40000),
                            0x80 + math.floor (cp / 0x1000) % 0x40,
                            0x80 + math.floor (cp / 0x40) % 0x40,
                            0x80 + cp % 0x40)
    end
end

local json = {}

local JSON_ESC = {
    ['"']  = '\\"',  ['\\'] = '\\\\', ['\b'] = '\\b',
    ['\f'] = '\\f',  ['\n'] = '\\n',  ['\r'] = '\\r', ['\t'] = '\\t',
}

local function json_quote (s)
    local out = s:gsub ('[%c"\\]', function (c)
        return JSON_ESC[c] or string.format ('\\u%04x', string.byte (c))
    end)
    return '"' .. out .. '"'
end

function json.encode (v)
    local t = type (v)
    if t == "table" then
        if #v > 0 or next (v) == nil then
            local parts = {}
            for _, item in ipairs (v) do parts[#parts+1] = json.encode (item) end
            return "[" .. table.concat (parts, ",") .. "]"
        else
            local parts = {}
            for k, val in pairs (v) do
                parts[#parts+1] = json_quote (tostring (k)) .. ":" .. json.encode (val)
            end
            return "{" .. table.concat (parts, ",") .. "}"
        end
    elseif t == "string" then return json_quote (v)
    elseif t == "number" then
        -- guard inf/nan: they are not valid JSON and Ardour reads have
        -- produced both (see the tempo notes below).
        if v ~= v or v == math.huge or v == -math.huge then return "0" end
        if v == math.floor (v) and math.abs (v) < 2^53 then
            return string.format ("%d", v)
        end
        return string.format ("%.10g", v)
    elseif t == "boolean" then return tostring (v)
    else return "null" end
end

function json.decode (s)
    local pos = 1
    local function skip ()
        while pos <= #s and s:sub (pos, pos):match ("[ \t\r\n]") do pos = pos + 1 end
    end
    local parse_value
    local function parse_string ()
        pos = pos + 1
        local out = {}
        while pos <= #s do
            local c = s:sub (pos, pos)
            if c == '"' then pos = pos + 1 return table.concat (out) end
            if c == "\\" then
                local n = s:sub (pos+1, pos+1)
                if n == "n" then out[#out+1] = "\n"
                elseif n == "t" then out[#out+1] = "\t"
                elseif n == "r" then out[#out+1] = "\r"
                elseif n == "b" then out[#out+1] = "\b"
                elseif n == "f" then out[#out+1] = "\f"
                elseif n == "u" then
                    -- FIX: this used to emit a literal "?" and skip the digits,
                    -- so every \uXXXX escape was destroyed. Python's json.dumps
                    -- defaults to ensure_ascii=True, which means EVERY non-ASCII
                    -- character the agent sends arrives escaped: a track named
                    -- "Cafe\u0301" or a marker "Drop \ud83c\udfb5" reached Ardour
                    -- as "Caf?" / "Drop ?". Decode it properly, surrogate pairs
                    -- included.
                    local hex = s:sub (pos+2, pos+5)
                    local cp  = tonumber (hex, 16)
                    pos = pos + 4
                    if cp and cp >= 0xD800 and cp <= 0xDBFF
                       and s:sub (pos+2, pos+3) == "\\u" then
                        local lo = tonumber (s:sub (pos+4, pos+7), 16)
                        if lo and lo >= 0xDC00 and lo <= 0xDFFF then
                            cp = 0x10000 + (cp - 0xD800) * 0x400 + (lo - 0xDC00)
                            pos = pos + 6
                        end
                    end
                    out[#out+1] = cp and utf8_char (cp) or "?"
                else out[#out+1] = n end
                pos = pos + 2
            else
                out[#out+1] = c
                pos = pos + 1
            end
        end
        return nil
    end
    local function parse_number ()
        local start = pos
        while pos <= #s and s:sub (pos, pos):match ("[%d%.%-%+eE]") do pos = pos + 1 end
        return tonumber (s:sub (start, pos-1))
    end
    parse_value = function ()
        skip ()
        local c = s:sub (pos, pos)
        if c == '"' then return parse_string () end
        if c == "{" then
            pos = pos + 1
            local obj = {}
            skip ()
            if s:sub (pos, pos) == "}" then pos = pos + 1 return obj end
            while true do
                skip ()
                local k = parse_string ()
                skip ()
                pos = pos + 1
                obj[k] = parse_value ()
                skip ()
                local d = s:sub (pos, pos)
                pos = pos + 1
                if d == "}" then return obj end
                if d ~= "," then return nil end
            end
        end
        if c == "[" then
            pos = pos + 1
            local arr = {}
            skip ()
            if s:sub (pos, pos) == "]" then pos = pos + 1 return arr end
            while true do
                arr[#arr+1] = parse_value ()
                skip ()
                local d = s:sub (pos, pos)
                pos = pos + 1
                if d == "]" then return arr end
                if d ~= "," then return nil end
            end
        end
        if s:sub (pos, pos+3) == "true"  then pos = pos + 4 return true  end
        if s:sub (pos, pos+4) == "false" then pos = pos + 5 return false end
        if s:sub (pos, pos+3) == "null"  then pos = pos + 4 return nil   end
        return parse_number ()
    end
    local ok, v = pcall (parse_value)
    if ok then return v end
    return nil
end

local function read_file (p)
    local f = io.open (p, "r"); if not f then return nil end
    local c = f:read ("*a"); f:close (); return c
end
local function write_file (p, c)
    local f = io.open (p, "w"); if not f then return false end
    f:write (c); f:close (); return true
end

-- ----------------------------------------------------------------------
-- capability probe (once per load)
-- ----------------------------------------------------------------------
-- Which of the fork-only helpers and which of the Ardour-9-only editor
-- bindings this build actually has. Reported by handlers.ping so the Python
-- side can tell the user WHY something is unavailable instead of guessing.
local caps = {}
do
    local L = ARDOUR and ARDOUR.LuaAPI or nil
    caps.fork_ensure_midi_region = (L ~= nil and type (L.ensure_midi_region)  == "function")
    caps.fork_set_session_tempo  = (L ~= nil and type (L.set_session_tempo)   == "function")
    caps.fork_import_audio_file  = (L ~= nil and type (L.import_audio_file)   == "function")
    caps.editor                  = (Editor ~= nil)
    -- MidiTimeAxisView:add_region is bound in Ardour 9 (gtk2_ardour/luainstance.cc:840,849)
    -- and NOT in 8.12 (no MidiTimeAxisView binding at all there).
    caps.editor_add_region = false
    if Editor then
        local ok = pcall (function ()
            local rl = Session:get_routes ()
            for r in rl:iter () do
                local rtav = Editor:rtav_from_route (r)
                if rtav then
                    local tav = rtav:to_timeaxisview ()
                    if tav and tav.to_midi_time_axis_view then caps.editor_add_region = true end
                    break
                end
            end
        end)
        if not ok then caps.editor_add_region = false end
    end
    caps.tempo_write_copy = (Temporal ~= nil and Temporal.TempoMap ~= nil
                             and type (Temporal.TempoMap.write_copy) == "function")
end

-- ----------------------------------------------------------------------
-- session helpers
-- ----------------------------------------------------------------------

-- Sample rate. Session:nominal_sample_rate() is bound in both versions
-- (luabindings.cc:3045 / :3184) and returns samplecnt_t (int64). The author
-- recorded it once reading as int64-max on his build; the binding source does
-- not explain that, so the value is sanity-checked rather than trusted.
local function sample_rate ()
    local ok, s = pcall (function () return Session:nominal_sample_rate () end)
    if ok and type (s) == "number" and s > 1000 and s < 1000000 then return s end
    local ok2, s2 = pcall (function () return Session:sample_rate () end)
    if ok2 and type (s2) == "number" and s2 > 1000 and s2 < 1000000 then return s2 end
    return 48000
end

-- Tempo. FIX: the old code did
--     Temporal.TempoMap.read():tempo_at(pos):quarter_notes_per_minute()
-- which calls a Temporal::Tempo method on a Temporal::TempoPoint userdata.
-- luabindings.cc carries an explicit FIXME immediately above that class
-- (8.12:803, 9.8:842): "direct access to parent class Temporal::Tempo fails
-- here". That is the reported `inf`. Ardour's own snippet
-- share/scripts/s_tempo_map.lua:8 reads BPM as
--     tm:quarters_per_minute_at (pos)
-- which returns a plain double with no cast involved
-- (libs/temporal/temporal/tempo.h:934). Fallback keeps the old path via the
-- explicit :to_tempo() downcast that s_tempo_map.lua:39 uses when it does go
-- through a TempoPoint.
local function tempo_bpm ()
    local pos_ok, pos = pcall (function () return Temporal.timepos_t (0) end)
    if not pos_ok then return 120.0 end

    local ok, v = pcall (function ()
        return Temporal.TempoMap.read ():quarters_per_minute_at (pos)
    end)
    if ok and type (v) == "number" and v == v and v ~= math.huge and v > 0 then
        return v
    end

    local ok2, v2 = pcall (function ()
        return Temporal.TempoMap.read ():tempo_at (pos):to_tempo ():quarter_notes_per_minute ()
    end)
    if ok2 and type (v2) == "number" and v2 == v2 and v2 ~= math.huge and v2 > 0 then
        return v2
    end

    return 120.0
end

local function find_route (name)
    for r in Session:get_routes ():iter () do
        if r:name () == name then return r end
    end
    return nil
end

local function find_track (name)
    local r = find_route (name)
    if not r then return nil end
    return r:to_track ()
end

-- ======================================================================
-- command implementations
-- ======================================================================
local handlers = {}

function handlers.ping (args)
    return {
        pong    = true,
        ardour  = Session:name (),
        -- what this build can actually do, so the agent never promises a
        -- capability this Ardour does not have.
        caps = {
            fork_ensure_midi_region = caps.fork_ensure_midi_region,
            fork_set_session_tempo  = caps.fork_set_session_tempo,
            fork_import_audio_file  = caps.fork_import_audio_file,
            editor                  = caps.editor,
            editor_add_region       = caps.editor_add_region,
            tempo_write_copy        = caps.tempo_write_copy,
        },
    }
end

function handlers.open_stem_window (args)
    -- "Window"/"toggle-stem-assistant" is an action registered by the ardour-ai
    -- fork's Stem panel. Editor:access_action is bound in both upstream versions
    -- (luainstance.cc:1056 / :1107); the ACTION is fork-only, hence the pcall.
    local ok, err = pcall (function ()
        Editor:access_action ("Window", "toggle-stem-assistant")
    end)
    return { ok = ok, err = ok and nil or tostring (err) }
end

function handlers.get_session_overview (args)
    local tracks = {}
    for r in Session:get_routes ():iter () do
        local tr = r:to_track ()
        if not tr:isnil () then
            tracks[#tracks+1] = {
                track_id = r:name (),
                name     = r:name (),
                kind     = (not tr:to_midi_track ():isnil ()) and "midi" or "audio",
                muted    = r:muted (),
                gain_db  = ARDOUR.DSP.accurate_coefficient_to_dB (r:gain_control ():get_value ()),
            }
        end
    end
    local sr = sample_rate ()
    return {
        name             = Session:name (),
        tempo            = tempo_bpm (),
        sample_rate      = sr,
        playhead_seconds = Session:transport_sample () / sr,
        tracks           = tracks,
    }
end

local function instrument_catalog ()
    local out, seen = {}, {}
    for info in ARDOUR.LuaAPI.list_plugins ():iter () do
        if info:is_instrument () then
            -- PluginManager::plugin_type_name(PluginType, bool short_name = true)
            -- (plugin_manager.h:123), bound as ARDOUR.PluginType.name
            -- (luabindings.cc:2418). LuaBridge's Stack<bool>::get on a MISSING
            -- argument returns lua_toboolean(none) == false, so calling it with
            -- one argument silently asked for the LONG name. Pass it explicitly.
            local plugin_type = ARDOUR.PluginType.name (info.type, true)
            local key = plugin_type .. ":" .. info.unique_id
            if not seen[key] then
                seen[key] = true
                local presets = {}
                local ok_presets, available = pcall (function () return info:get_presets () end)
                if ok_presets and available then
                    for preset in available:iter () do
                        presets[#presets + 1] = preset.label
                    end
                end
                out[#out + 1] = {
                    id       = info.unique_id,
                    name     = info.name,
                    creator  = info.creator,
                    category = info.category,
                    type     = plugin_type,
                    presets  = presets,
                }
            end
        end
    end
    table.sort (out, function (a, b) return string.lower (a.name) < string.lower (b.name) end)
    return out
end

function handlers.list_instruments (args)
    return { instruments = instrument_catalog () }
end

local function find_instrument (instrument_id)
    if not instrument_id or instrument_id == "" then return nil end
    for info in ARDOUR.LuaAPI.list_plugins ():iter () do
        if info:is_instrument () and
           (info.unique_id == instrument_id or info.name == instrument_id) then
            return info
        end
    end
    return nil
end

-- Default instrument order, matching Ardour's own (InstrumentSelector /
-- share/scripts/_route_template_generic_midi.lua:55-58): GMSynth first, then
-- Reasonable Synth.
--
-- a-fluidsynth is DELIBERATELY excluded as a default. It instantiates with NO
-- soundfont loaded and emits silence until the user drags in an .sf2.
local AUDIBLE_DEFAULTS = {
    "http://gareus.org/oss/lv2/gmsynth",       -- GMSynth: GM + embedded SF
    "https://community.ardour.org/node/7596",  -- Reasonable Synth: built-in
}
local FLUID_URI = "urn:ardour:a-fluidsynth"

local function audible_default_info ()
    for _, uri in ipairs (AUDIBLE_DEFAULTS) do
        local info = ARDOUR.LuaAPI.new_plugin_info (uri, ARDOUR.PluginType.LV2)
        if info and not info:isnil () then return info end
    end
    return nil
end

function handlers.create_midi_track (args)
    local name         = args.name or "Stem MIDI"
    local requested_id = args.instrument_id or ""
    local gm_program   = tonumber (string.match (requested_id, "^gm:(%d+)$"))
    local gm_drums     = requested_id == "gm:drums"
    local instrument   = nil
    local is_gm_synth  = false

    if requested_id ~= "" and not gm_program and not gm_drums then
        instrument = find_instrument (requested_id)
    end

    if (not instrument or instrument:isnil ()) and (gm_program or gm_drums) then
        local gm = ARDOUR.LuaAPI.new_plugin_info (
            "http://gareus.org/oss/lv2/gmsynth", ARDOUR.PluginType.LV2)
        if gm and not gm:isnil () then
            instrument  = gm
            is_gm_synth = true
        end
    end

    if not instrument or instrument:isnil () then
        instrument  = audible_default_info ()
        is_gm_synth = instrument and not instrument:isnil ()
            and instrument.unique_id == "http://gareus.org/oss/lv2/gmsynth"
    end

    local has_instrument = instrument and not instrument:isnil ()
    if not has_instrument then
        -- ARDOUR.PluginInfo() is the bound nil-shared_ptr constructor
        -- (luabindings.cc:1147-1148, .addNilPtrConstructor()) and is exactly how
        -- share/scripts/_route_template_generic_midi.lua:68 expresses
        -- "no instrument".
        instrument = ARDOUR.PluginInfo ()
    end

    -- Session::new_midi_track — VERIFIED. session.h:744 (8.12) / :808 (9.8):
    --   (ChanCount in, ChanCount out, bool strict_io, shared_ptr<PluginInfo>,
    --    Plugin::PresetRecord*, RouteGroup*, uint32_t how_many,
    --    string name_template, PresentationInfo::order_t, TrackMode,
    --    bool input_auto_connect, bool trigger_visibility = false)
    -- Argument order and types below match share/scripts/_rgh_midi_track_trick.lua:61
    -- and _route_template_generic_midi.lua:71 exactly. The 12th parameter is
    -- omitted; LuaBridge's Stack<bool>::get on a missing index is
    -- lua_toboolean(none) == false (libs/lua/LuaBridge/detail/Stack.h:607), which
    -- is the C++ default. The 6th parameter changed C++ type between 8 and 9
    -- (RouteGroup* -> shared_ptr<RouteGroup>) but nil is correct for both.
    local tl = Session:new_midi_track (
        ARDOUR.ChanCount (ARDOUR.DataType ("midi"), 1),
        ARDOUR.ChanCount (ARDOUR.DataType ("audio"), 2),
        true, instrument, nil,
        nil, 1, name, ARDOUR.PresentationInfo.max_order,
        ARDOUR.TrackMode.Normal, true)

    for t in tl:iter () do
        local preset_loaded  = false
        local program_loaded = false
        if has_instrument and is_gm_synth and gm_program then
            -- to_plugininsert is the current cast name; to_insert is marked
            -- deprecated in both versions (luabindings.cc:1868 / :1912).
            local insert = t:the_instrument ():to_plugininsert ()
            if not insert:isnil () then
                local parser = ARDOUR.RawMidiParser ()
                parser:process_byte (0xC0)
                if parser:process_byte (gm_program) then
                    program_loaded = insert:write_immediate_event (
                        Evoral.EventType.MIDI_EVENT,
                        parser:buffer_size (), parser:midi_buffer ())
                end
            end
        end
        if has_instrument and args.preset and args.preset ~= "" then
            local insert = t:the_instrument ():to_plugininsert ()
            if not insert:isnil () then
                local plugin = insert:plugin (0)
                if not plugin:isnil () then
                    local preset = plugin:preset_by_label (args.preset)
                    if preset and preset.valid then
                        plugin:load_preset (preset)
                        preset_loaded = true
                    end
                end
            end
        end
        local gm_downgraded = (gm_program or gm_drums) and not is_gm_synth
        return {
            track_id        = t:name (),
            instrument      = has_instrument,
            instrument_id   = has_instrument and instrument.unique_id or "",
            instrument_name = has_instrument and instrument.name or "",
            preset          = preset_loaded and args.preset or "",
            gm_program      = program_loaded and gm_program or nil,
            note = gm_downgraded and
                ("No General MIDI synth is installed, so this track uses "
                 .. "the built-in Reasonable Synth. It is audible but plays "
                 .. "one generic tone rather than the requested instrument.")
                or nil,
        }
    end
    return { error = "track creation returned empty list" }
end

function handlers.delete_track (args)
    local route = find_route (args.track_id)
    if not route then
        return { error = "track not found: " .. tostring (args.track_id) }
    end
    Session:remove_route (route)
    return { ok = true, track_id = args.track_id }
end

local function new_audible_proc ()
    for _, uri in ipairs (AUDIBLE_DEFAULTS) do
        local proc = ARDOUR.LuaAPI.new_plugin (Session, uri, ARDOUR.PluginType.LV2, "")
        if proc and not proc:isnil () then return proc, uri end
    end
    return nil, nil
end

local function instrument_is_silent_fluid (route)
    local cur = route:the_instrument ()
    if cur:isnil () then return false, cur end
    local pi = cur:to_plugininsert ()
    if pi:isnil () then return false, cur end
    local pl = pi:plugin (0)
    if pl:isnil () then return false, cur end
    return pl:unique_id () == FLUID_URI, cur
end

function handlers.add_instrument (args)
    local route = find_route (args.track_id)
    if not route then return { error = "track not found: " .. tostring (args.track_id) } end
    local silent, cur = instrument_is_silent_fluid (route)
    if not cur:isnil () and not silent then
        return { track_id = args.track_id, instrument = true, already = true }
    end
    local proc, uri = new_audible_proc ()
    if not proc then return { error = "no audible instrument plugin available" } end
    if silent then
        local rv = route:replace_processor (cur, proc, nil)
        return { track_id = args.track_id, instrument = true,
                 replaced = (rv == 0), instrument_id = uri }
    end
    route:add_processor_by_index (proc, 0, nil, true)
    return { track_id = args.track_id, instrument = true, added = true, instrument_id = uri }
end

function handlers.fix_silent_instruments (args)
    local fixed = {}
    for r in Session:get_routes ():iter () do
        local tr = r:to_track ()
        if not tr:isnil () and not tr:to_midi_track ():isnil () then
            local silent, cur = instrument_is_silent_fluid (r)
            if cur:isnil () then
                local proc, uri = new_audible_proc ()
                if proc then
                    r:add_processor_by_index (proc, 0, nil, true)
                    fixed[#fixed + 1] = { track_id = r:name (), action = "added", instrument_id = uri }
                end
            elseif silent then
                local proc, uri = new_audible_proc ()
                if proc and r:replace_processor (cur, proc, nil) == 0 then
                    fixed[#fixed + 1] = { track_id = r:name (), action = "replaced", instrument_id = uri }
                end
            end
        end
    end
    return { ok = true, fixed = fixed, count = #fixed }
end

function handlers.diagnose_audio (args)
    local d = {}
    d.sample_rate = sample_rate ()
    local eng = Session:engine ()
    if eng then
        d.engine_running = eng:running ()
        d.backend        = eng:current_backend_name ()
        d.dsp_load       = eng:get_dsp_load ()
    end
    d.transport_rolling = Session:transport_rolling ()
    d.transport_sample  = Session:transport_sample ()
    local master = Session:master_out ()
    if master and not master:isnil () then
        d.master        = master:name ()
        d.master_active = master:active ()
        local o = master:output ()
        d.master_out_ports          = o:n_ports ():n_audio ()
        d.master_out_connected_to_hw = o:physically_connected ()
    else
        d.master = "NONE"
    end
    local r = find_route (args.track_id or "Chords")
    if r then
        d.track        = r:name ()
        d.track_active = r:active ()
        local inst = r:the_instrument ()
        d.track_has_instrument = not inst:isnil ()
        if not inst:isnil () then d.instrument_active = inst:active () end
    end
    return d
end

-- MidiRegion:model() IS bound in both versions
-- (luabindings.cc:1698 in 8.12, :1740 in 9.8, the non-const overload), so the
-- GAPS.md note "use midi_region:midi_source(0):model(), not :model()" is not
-- true of stock Ardour. Both routes are kept, :model() first.
local function region_model (mr)
    local ok, mm = pcall (function () return mr:model () end)
    if ok and mm and not mm:isnil () then return mm end
    local ok2, mm2 = pcall (function () return mr:midi_source (0):model () end)
    if ok2 and mm2 and not mm2:isnil () then return mm2 end
    return nil
end

function handlers.get_track_content (args)
    local route = find_route (args.track_id)
    if not route then return { error = "no such track" } end
    local track = route:to_track ()
    if track:isnil () then return { error = "route is not a track" } end
    local regions = {}
    for region in track:playlist ():region_list ():iter () do
        local item = {
            name             = region:name (),
            position_samples = region:position ():samples (),
            length_samples   = region:length ():samples (),
            midi_notes       = 0,
        }
        local mr = region:to_midiregion ()
        if not mr:isnil () then
            local mm = region_model (mr)
            if mm then
                for _ in ARDOUR.LuaAPI.note_list (mm):iter () do
                    item.midi_notes = item.midi_notes + 1
                end
            end
        end
        regions[#regions + 1] = item
    end
    return { track_id = args.track_id, regions = regions }
end

function handlers.repair_midi_region (args)
    local track = find_track (args.track_id)
    if not track or track:isnil () then return { error = "no such track" } end
    local mt = track:to_midi_track ()
    if mt:isnil () then return { error = "not a midi track" } end
    local sr    = sample_rate ()
    local tempo = tempo_bpm ()
    local repaired = 0
    for region in mt:playlist ():region_list ():iter () do
        local mr = region:to_midiregion ()
        if not mr:isnil () then
            local mm = region_model (mr)
            local max_beat = 0
            if mm then
                for note in ARDOUR.LuaAPI.note_list (mm):iter () do
                    local finish = (note:time ():to_ticks () + note:length ():to_ticks ()) / TPB
                    if finish > max_beat then max_beat = finish end
                end
            end
            local samples = math.ceil ((max_beat + 1) * (60.0 / tempo) * sr)
            if region:length ():samples () < samples then
                region:set_length (Temporal.timecnt_t (samples))
                repaired = repaired + 1
            end
        end
    end
    return { ok = true, repaired = repaired }
end

-- Find or create a MIDI region on a track's playlist spanning enough beats.
--
-- There is NO region constructor in ARDOUR.LuaAPI and RegionFactory exposes only
-- region_by_id / regions / clone_region to Lua (luabindings.cc:3151-3154 in 8.12,
-- :3283-3286 in 9.8). So a blank MIDI region cannot be made from libardour alone.
-- Three routes, tried in order:
--   1. ARDOUR.LuaAPI.ensure_midi_region — ardour-ai fork only.
--   2. Editor -> MidiTimeAxisView:add_region — Ardour 9 only
--      (luainstance.cc:840 addCast to_midi_time_axis_view, :849 add_region).
--      Absent from 8.12: there is no MidiTimeAxisView binding there at all.
--   3. Nothing. On stock Ardour 8.x this genuinely cannot be done from Lua;
--      say so instead of returning a silent nil.
local function get_or_make_region (route, mt, beats_needed)
    local pl    = mt:playlist ()
    local sr    = sample_rate ()
    local tempo = tempo_bpm ()
    local required_samples = math.ceil ((beats_needed + 1) * (60.0 / tempo) * sr)

    for r in pl:region_list ():iter () do
        local mr = r:to_midiregion ()
        if not mr:isnil () then
            if r:length ():samples () < required_samples then
                r:set_length (Temporal.timecnt_t (required_samples))
            end
            return mr
        end
    end

    if caps.fork_ensure_midi_region then
        local ok_region, region = pcall (function ()
            return ARDOUR.LuaAPI.ensure_midi_region (Session, mt, beats_needed + 1)
        end)
        if ok_region and region and not region:isnil () then return region end
    end

    if not Editor then
        return nil, "no Editor in this Lua context: cannot create a MIDI region"
    end
    local rtav = Editor:rtav_from_route (route)
    if not rtav then
        return nil, "no editor view for this route"
    end
    local tav = rtav:to_timeaxisview ()
    if not tav or type (tav.to_midi_time_axis_view) ~= "function" then
        return nil, "this Ardour has no MidiTimeAxisView Lua binding "
                 .. "(added in Ardour 9; absent in 8.x). Creating an empty MIDI "
                 .. "region from Lua is not possible on this build — upgrade to "
                 .. "Ardour 9, or draw one empty MIDI region on the track by hand "
                 .. "once and Stem will reuse and extend it."
    end
    local mtav = tav:to_midi_time_axis_view ()
    if not mtav then
        return nil, "to_midi_time_axis_view returned nil (not a MIDI track view?)"
    end
    mtav:add_region (Temporal.timepos_t (0), Temporal.timecnt_t (required_samples), true)

    for r in pl:region_list ():iter () do
        local mr = r:to_midiregion ()
        if not mr:isnil () then return mr end
    end
    return nil, "add_region did not produce a MIDI region"
end

function handlers.insert_midi_notes (args)
    local track = find_track (args.track_id)
    if not track or track:isnil () then return { error = "no such track" } end
    local mt = track:to_midi_track ()
    if mt:isnil () then return { error = "not a midi track" } end
    if not args.notes or #args.notes == 0 then return { ok = true, inserted = 0 } end

    local max_beat = 4
    for _, n in ipairs (args.notes) do
        local e = n.start_beat + n.length_beats
        if e > max_beat then max_beat = e end
    end

    local route = find_route (args.track_id)
    local mr, why = get_or_make_region (route, mt, max_beat)
    if not mr or mr:isnil () then
        return { error = why or "could not create midi region" }
    end

    local mm = region_model (mr)
    if not mm then return { error = "midi region has no model" } end

    -- MidiModel::new_note_diff_command is bound in both (luabindings.cc:1811 / :1855).
    local cmd   = mm:new_note_diff_command ("stem insert")
    local Beats = Temporal.Beats          -- ctor is (int32 whole, int32 ticks),
                                          -- luabindings.cc:649 / :688
    local function to_beats (b)
        local whole = math.floor (b)
        local ticks = math.floor ((b - whole) * TPB + 0.5)
        return Beats (whole, ticks)
    end
    for _, n in ipairs (args.notes) do
        local note = ARDOUR.LuaAPI.new_noteptr (
            n.channel or 0,
            to_beats (n.start_beat),
            to_beats (n.length_beats),
            n.pitch, n.velocity or 100)
        cmd:add (note)
    end

    -- apply_command still exists but is explicitly marked deprecated in the
    -- binding ("left here in case any extant scripts use apply_command",
    -- luabindings.cc:1809 / :1853). apply_diff_command_as_commit is the current
    -- name and is bound in both. Its first parameter changed C++ type between 8
    -- and 9 (Session* -> PBD::HistoryOwner*) but Session is-a HistoryOwner and
    -- the Lua class derives from it in 9 (luabindings.cc:3171), so passing
    -- Session is right for both.
    local applied = pcall (function () mm:apply_diff_command_as_commit (Session, cmd) end)
    if not applied then
        mm:apply_command (Session, cmd)
    end
    return { ok = true, inserted = #args.notes }
end

function handlers.get_midi_notes (args)
    local track = find_track (args.track_id)
    if not track or track:isnil () then return { error = "no such track" } end
    local mt = track:to_midi_track ()
    if mt:isnil () then return { error = "not a midi track" } end
    local out = {}
    for r in mt:playlist ():region_list ():iter () do
        local mr = r:to_midiregion ()
        if not mr:isnil () then
            local mm = region_model (mr)
            if mm then
                -- NOTE (unverified, flagged in BRIDGE_AUDIT.md): note:time() is
                -- SOURCE-relative, and MidiRegion:model() is the source's model.
                -- With one region starting at 0 with no start offset — which is
                -- all Stem creates today — source beats == timeline beats. A
                -- trimmed or moved region will report shifted beats.
                for note in ARDOUR.LuaAPI.note_list (mm):iter () do
                    out[#out+1] = {
                        pitch        = note:note (),
                        start_beat   = note:time ():to_ticks () / TPB,
                        length_beats = note:length ():to_ticks () / TPB,
                        velocity     = note:velocity (),
                    }
                end
            end
        end
    end
    return { notes = out }
end

function handlers.import_audio (args)
    local sr  = sample_rate ()
    local pos = math.floor ((args.position_seconds or 0) * sr)

    -- 1) fork helper, if this build has it.
    if caps.fork_import_audio_file then
        local ok, track_id = pcall (function ()
            return ARDOUR.LuaAPI.import_audio_file (Session, args.file_path, pos)
        end)
        if ok and track_id and track_id ~= "" then
            return { ok = true, imported = args.file_path, track_id = track_id }
        end
    end

    -- 2) stock Ardour: Editor:do_import, exactly as the bundled
    --    share/scripts/s_import_files.lua does (identical in 8.12 and 9.8).
    --    Binding: gtk2_ardour/luainstance.cc:945 / :1017 (addRefFunction, because
    --    the timepos_t parameter is by reference). Signature:
    --    public_editor.h:300 / :228.
    if not Editor then
        return { error = "no audio import path: this build has neither "
                      .. "ARDOUR.LuaAPI.import_audio_file nor an Editor" }
    end
    local before = {}
    for r in Session:get_routes ():iter () do before[r:name ()] = true end

    local files = C.StringVector ()
    files:push_back (args.file_path)
    local where = Temporal.timepos_t (pos)

    local ok, err = pcall (function ()
        Editor:do_import (files,
            Editing.ImportDistinctFiles, Editing.ImportAsTrack, ARDOUR.SrcQuality.SrcBest,
            ARDOUR.MidiTrackNameSource.SMFFileAndTrackName,
            ARDOUR.MidiTempoMapDisposition.SMFTempoIgnore,
            where, ARDOUR.PluginInfo (), ARDOUR.Track (), false)
    end)
    if not ok then
        return { error = "do_import failed: " .. tostring (err) }
    end

    local created = nil
    for r in Session:get_routes ():iter () do
        if not before[r:name ()] then created = r:name () break end
    end
    -- UNVERIFIED: whether do_import has finished by the time it returns (Ardour
    -- runs the import on a worker thread behind a progress dialog). If created
    -- is nil the import may simply still be running — the Python side should
    -- poll get_session_overview rather than treat this as a failure.
    return {
        ok       = true,
        imported = args.file_path,
        track_id = created or "",
        pending  = (created == nil),
    }
end

function handlers.set_tempo (args)
    local bpm = tonumber (args.bpm)
    if not bpm or bpm <= 0 then return { error = "bad bpm" } end

    -- 1) fork helper, if present.
    if caps.fork_set_session_tempo then
        local ok, rv = pcall (function ()
            return ARDOUR.LuaAPI.set_session_tempo (Session, bpm)
        end)
        if ok and rv then return { ok = true, bpm = bpm, via = "fork" } end
    end

    -- 2) stock Ardour: the write_copy / set_tempo / update transaction, exactly
    --    as share/scripts/s_tempo_map.lua:11-20 does it. Bindings:
    --    TempoMap.write_copy / update / abort_update are static functions
    --    (luabindings.cc:839-841 in 8.12, :878-880 in 9.8); TempoMap:set_tempo
    --    takes (Tempo const&, timepos_t const&) (:842 / :881); the Tempo ctor is
    --    (double npm, double end_npm, int note_type) (:778 / :817).
    --
    --    write_copy MUST be matched by update() or abort_update() — the snippet
    --    says so in its own comment — hence the pcall + abort on failure.
    if not caps.tempo_write_copy then
        return { error = "no tempo API available in this Ardour build" }
    end

    local tm = Temporal.TempoMap.write_copy ()
    local ok, err = pcall (function ()
        tm:set_tempo (Temporal.Tempo (bpm, bpm, 4), Temporal.timepos_t (0))
    end)
    if not ok then
        pcall (function () Temporal.TempoMap.abort_update () end)
        return { error = "set_tempo failed: " .. tostring (err) }
    end

    Session:begin_reversible_command ("Stem: set tempo")
    Temporal.TempoMap.update (tm)
    -- abort_empty_reversible_command is bound in both (Session in 8.12
    -- luabindings.cc:3122; PBD::HistoryOwner in 9.8 :537) and is what
    -- s_tempo_map.lua uses to avoid committing an empty command.
    local aborted = false
    pcall (function () aborted = Session:abort_empty_reversible_command () end)
    if not aborted then
        Session:commit_reversible_command (nil)
    end
    return { ok = true, bpm = bpm, via = "tempomap" }
end

function handlers.set_track_gain (args)
    local track = find_track (args.track_id)
    if not track or track:isnil () then return { error = "no such track" } end
    track:gain_control ():set_value (
        ARDOUR.DSP.dB_to_coefficient (args.gain_db),
        PBD.GroupControlDisposition.NoGroup)
    return { ok = true }
end

function handlers.set_track_mute (args)
    local route = find_route (args.track_id)
    if not route or route:isnil () then return { error = "no such track or bus" } end
    route:mute_control ():set_value (args.muted and 1 or 0,
        PBD.GroupControlDisposition.NoGroup)
    return { ok = true }
end

function handlers.add_marker (args)
    -- FIX: Session:locations():add_mark(...) DOES NOT EXIST. The Locations class
    -- is bound in both versions (luabindings.cc:1251 in 8.12, :1296 in 9.8) and
    -- exposes list/mark_at/add_range/remove/... — but no add_mark. Calling it
    -- raised "attempt to call a nil value (method 'add_mark')" AFTER
    -- Session:begin_reversible_command had already opened a command, so every
    -- add_marker left Ardour holding an un-committed reversible command and the
    -- next real edit got folded into it. That is the worst failure in the old
    -- file: it corrupted the undo stack rather than just erroring.
    --
    -- The supported route is the editor's own marker action, which opens and
    -- commits its own undo record: Editor:mouse_add_new_marker(timepos_t, flags,
    -- cue_id), bound at gtk2_ardour/luainstance.cc:1040 (8.12) / :1007 (9.8) to
    -- PublicEditor::add_location_mark (public_editor.h:260). This is exactly what
    -- the bundled share/scripts/add_cdmarker.lua:5 does.
    if not Editor then return { error = "no Editor: cannot add a marker" } end
    local sr  = sample_rate ()
    local pos = Temporal.timepos_t (math.floor ((args.position_seconds or 0) * sr))

    local ok, err = pcall (function ()
        Editor:mouse_add_new_marker (pos, ARDOUR.LocationFlags.IsMark, 0)
    end)
    if not ok then
        return { error = "add marker failed: " .. tostring (err) }
    end

    -- The action names the marker itself ("mark1", ...). Rename it if we can
    -- find it again. UNVERIFIED against a live session: Locations:mark_at
    -- returns a raw Location* and its nil behaviour through LuaBridge has not
    -- been tested here, so this is best-effort and never fails the call.
    local renamed = false
    if args.name and args.name ~= "" then
        pcall (function ()
            for l in Session:locations ():list ():iter () do
                if l:is_mark () and l:start ():samples () == pos:samples () then
                    l:set_name (args.name)
                    renamed = true
                    return
                end
            end
        end)
    end
    return { ok = true, named = renamed, name = args.name or "" }
end

function handlers.transport_play (args)
    -- Start with an explicit LocateRoll request. Unlike OSC this is
    -- acknowledged, and unlike goto_start it cannot stop after locating when the
    -- transport state machine is already transitioning.
    if args and args.from_start == false then
        Session:request_roll (ARDOUR.TransportRequestSource.TRS_UI)
    else
        Session:request_locate (
            0, false,
            ARDOUR.LocateTransportDisposition.MustRoll,
            ARDOUR.TransportRequestSource.TRS_UI)
    end
    return { ok = true, requested = "roll", from_start = not (args and args.from_start == false) }
end

function handlers.rewind (args)
    Session:goto_start (false)
    return { ok = true }
end

function handlers.transport_stop (args)
    Session:request_stop (false, false, ARDOUR.TransportRequestSource.TRS_UI)
    return { ok = true }
end

function handlers.locate (args)
    Session:request_locate (math.floor (args.seconds * sample_rate ()),
        false, ARDOUR.LocateTransportDisposition.MustStop,
        ARDOUR.TransportRequestSource.TRS_UI)
    return { ok = true }
end

function handlers.undo (args)
    -- Editor:undo(n) — PublicEditor::undo(uint32_t n = 1) in 8.12
    -- (public_editor.h:176), EditingContext::undo in 9.8
    -- (editing_context.h:472). Session:undo is not bound in either.
    Editor:undo (1)
    return { ok = true }
end

function handlers.save_session (args)
    -- Session::save_state(snapshot, pending, switch_to_snapshot, template_only,
    -- for_archive, only_used_assets) — session.h:607 / :621. Bound in
    -- LuaBindings::session (8.12) / LuaBindings::non_rt (9.8); both are
    -- registered for the editor Lua state (luainstance.cc:786 / :788).
    Session:save_state ("", false, false, false, false, false)
    return { ok = true }
end

-- ======================================================================
-- dispatch: one poll per LuaTimerDS tick (~100 ms, GUI thread)
-- ======================================================================
local function dispatch ()
    local raw = read_file (req_path)
    if not raw or raw == "" then return end
    os.remove (req_path)
    local req = json.decode (raw)
    if not req then
        write_file (dir .. "/bridge_error.log", "decode failed for: " .. raw)
        return
    end
    local h = handlers[req.method]
    local resp
    if h then
        local ok, result = pcall (h, req.args or {})
        resp = ok and { id = req.id, result = result }
                  or  { id = req.id, error = tostring (result) }
    else
        resp = { id = req.id, error = "unknown method: " .. tostring (req.method) }
    end
    local ok2, encoded = pcall (json.encode, resp)
    if not ok2 then
        encoded = '{"id":' .. json_quote (tostring (req.id))
               .. ',"error":' .. json_quote ("encode failed: " .. tostring (encoded)) .. '}'
    end
    write_file (resp_path, encoded)
end

return function ()
    dispatch ()
end
