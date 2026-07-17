-- Stem bridge implementation. Hot-reloaded by the loader hook each tick.
-- Edit freely; no Ardour restart or hook re-registration needed.
return function ()
    local home = os.getenv("HOME")
    local dir = home .. "/.stem"
    -- NEVER os.execute() here: hot-reloaded every tick -> per-tick subprocess
    -- wedges the GUI thread and leaks memory. ~/.stem already exists.
    local req_path = dir .. "/request.json"
    local resp_path = dir .. "/response.json"

    -- minimal JSON (objects of strings/numbers/bools + flat arrays), enough
    -- for our RPC envelope; avoids external deps inside Ardour's Lua.
    local json = {}
    function json.encode(v)
        local t = type(v)
        if t == "table" then
            if #v > 0 or next(v) == nil then
                local parts = {}
                for _, item in ipairs(v) do parts[#parts+1] = json.encode(item) end
                return "[" .. table.concat(parts, ",") .. "]"
            else
                local parts = {}
                for k, val in pairs(v) do
                    parts[#parts+1] = string.format("%q", k) .. ":" .. json.encode(val)
                end
                return "{" .. table.concat(parts, ",") .. "}"
            end
        elseif t == "string" then return string.format("%q", v)
        elseif t == "number" then
            if v ~= v or v == math.huge or v == -math.huge then return "0" end
            return tostring(v)
        elseif t == "boolean" then return tostring(v)
        else return "null" end
    end
    -- decode: minimal recursive-descent JSON parser (objects, arrays,
    -- strings, numbers, booleans, null) — enough for the RPC envelope.
    function json.decode(s)
        local pos = 1
        local function skip()
            while pos <= #s and s:sub(pos,pos):match("[ \t\r\n]") do pos = pos + 1 end
        end
        local parse_value
        local function parse_string()
            pos = pos + 1  -- skip opening quote
            local out = {}
            while pos <= #s do
                local c = s:sub(pos,pos)
                if c == '"' then pos = pos + 1 return table.concat(out) end
                if c == "\\" then
                    local n = s:sub(pos+1,pos+1)
                    if n == "n" then out[#out+1] = "\n"
                    elseif n == "t" then out[#out+1] = "\t"
                    elseif n == "r" then out[#out+1] = "\r"
                    elseif n == "u" then
                        out[#out+1] = "?" pos = pos + 4  -- skip \uXXXX
                    else out[#out+1] = n end
                    pos = pos + 2
                else
                    out[#out+1] = c
                    pos = pos + 1
                end
            end
            return nil
        end
        local function parse_number()
            local start = pos
            while pos <= #s and s:sub(pos,pos):match("[%d%.%-%+eE]") do pos = pos + 1 end
            return tonumber(s:sub(start, pos-1))
        end
        parse_value = function()
            skip()
            local c = s:sub(pos,pos)
            if c == '"' then return parse_string() end
            if c == "{" then
                pos = pos + 1
                local obj = {}
                skip()
                if s:sub(pos,pos) == "}" then pos = pos + 1 return obj end
                while true do
                    skip()
                    local k = parse_string()
                    skip()
                    pos = pos + 1  -- skip :
                    obj[k] = parse_value()
                    skip()
                    local d = s:sub(pos,pos)
                    pos = pos + 1
                    if d == "}" then return obj end
                    if d ~= "," then return nil end
                end
            end
            if c == "[" then
                pos = pos + 1
                local arr = {}
                skip()
                if s:sub(pos,pos) == "]" then pos = pos + 1 return arr end
                while true do
                    arr[#arr+1] = parse_value()
                    skip()
                    local d = s:sub(pos,pos)
                    pos = pos + 1
                    if d == "]" then return arr end
                    if d ~= "," then return nil end
                end
            end
            if s:sub(pos,pos+3) == "true" then pos = pos + 4 return true end
            if s:sub(pos,pos+4) == "false" then pos = pos + 5 return false end
            if s:sub(pos,pos+3) == "null" then pos = pos + 4 return nil end
            return parse_number()
        end
        local ok, v = pcall(parse_value)
        if ok then return v end
        return nil
    end

    local function read_file(p)
        local f = io.open(p, "r"); if not f then return nil end
        local c = f:read("*a"); f:close(); return c
    end
    local function write_file(p, c)
        local f = io.open(p, "w"); f:write(c); f:close()
    end

    -- ---- command implementations ----
    local handlers = {}

    function handlers.ping(args)
        return { pong = true, ardour = Session:name() }
    end

    function handlers.open_stem_window(args)
        local ok, err = pcall(function()
            Editor:access_action("Window", "toggle-stem-assistant")
        end)
        return { ok = ok, err = ok and nil or tostring(err) }
    end

    function handlers.get_session_overview(args)
        local tracks = {}
        for r in Session:get_routes():iter() do
            local tr = r:to_track()
            if not tr:isnil() then
                tracks[#tracks+1] = {
                    track_id = r:name(),
                    name = r:name(),
                    kind = (not r:to_track():to_midi_track():isnil()) and "midi" or "audio",
                    muted = r:muted(),
                    gain_db = ARDOUR.DSP.accurate_coefficient_to_dB(r:gain_control():get_value()),
                }
            end
        end
        local tempo = 120.0
        local ok_t, t = pcall(function()
            return Temporal.TempoMap.read():tempo_at(Temporal.timepos_t(0)):quarter_notes_per_minute()
        end)
        if ok_t and t == t and t ~= math.huge and t > 0 then tempo = t end
        local sample_rate = Session:nominal_sample_rate()
        return {
            name = Session:name(),
            tempo = tempo,
            sample_rate = sample_rate,
            playhead_seconds = Session:transport_sample() / sample_rate,
            tracks = tracks,
        }
    end

    local function instrument_catalog()
        local out = {}
        local seen = {}
        for info in ARDOUR.LuaAPI.list_plugins():iter() do
            if info:is_instrument() then
                local plugin_type = ARDOUR.PluginType.name(info.type)
                local key = plugin_type .. ":" .. info.unique_id
                if seen[key] then goto continue end
                seen[key] = true
                local presets = {}
                local ok_presets, available = pcall(function()
                    return info:get_presets()
                end)
                if ok_presets and available then
                    for preset in available:iter() do
                        presets[#presets + 1] = preset.label
                    end
                end
                out[#out + 1] = {
                    id = info.unique_id,
                    name = info.name,
                    creator = info.creator,
                    category = info.category,
                    type = plugin_type,
                    presets = presets,
                }
            end
            ::continue::
        end
        table.sort(out, function(a, b)
            return string.lower(a.name) < string.lower(b.name)
        end)
        return out
    end

    function handlers.list_instruments(args)
        return { instruments = instrument_catalog() }
    end

    -- Full plugin catalog (instruments + effects). kind: "instrument"|"effect"|nil
    function handlers.list_plugins(args)
        local want = args and args.kind or nil
        local out = {}
        local seen = {}
        for info in ARDOUR.LuaAPI.list_plugins():iter() do
            local is_inst = info:is_instrument()
            local kind = is_inst and "instrument" or "effect"
            if want == nil or want == kind then
                local plugin_type = ARDOUR.PluginType.name(info.type)
                local key = plugin_type .. ":" .. info.unique_id
                if not seen[key] then
                    seen[key] = true
                    out[#out + 1] = {
                        id = info.unique_id,
                        name = info.name,
                        creator = info.creator,
                        category = info.category,
                        type = plugin_type,
                        kind = kind,
                    }
                end
            end
        end
        table.sort(out, function(a, b)
            return string.lower(a.name) < string.lower(b.name)
        end)
        return { plugins = out, count = #out }
    end

    local function find_plugin_info(plugin_id)
        if not plugin_id or plugin_id == "" then return nil end
        for info in ARDOUR.LuaAPI.list_plugins():iter() do
            if info.unique_id == plugin_id or info.name == plugin_id then
                return info
            end
        end
        return nil
    end

    local function nth_insert(route, plugin_index)
        -- Prefer an iterator when the binding exists; fall back to instrument@0.
        local ok_iter, insert = pcall(function()
            local idx = 0
            for proc in route:each_processor() do
                local ins = proc:to_insert()
                if not ins:isnil() then
                    if idx == plugin_index then return ins end
                    idx = idx + 1
                end
            end
            return nil
        end)
        if ok_iter and insert and not insert:isnil() then
            return insert
        end
        if plugin_index == 0 then
            local cur = route:the_instrument()
            if cur and not cur:isnil() then
                local ins = cur:to_insert()
                if not ins:isnil() then return ins end
            end
        end
        return nil
    end

    function handlers.load_plugin(args)
        local route = find_route(args.track_id)
        if not route or route:isnil() then
            return { error = "track not found: " .. tostring(args.track_id) }
        end
        local info = find_plugin_info(args.plugin_id)
        if not info or info:isnil() then
            return { error = "plugin not found: " .. tostring(args.plugin_id) }
        end
        local proc = ARDOUR.LuaAPI.new_plugin(
            Session, info.unique_id, info.type, "")
        if not proc or proc:isnil() then
            return { error = "failed to instantiate plugin: " .. tostring(args.plugin_id) }
        end
        local position = args.position
        if position == nil then position = -1 end
        local ok, err = pcall(function()
            route:add_processor_by_index(proc, position, nil, true)
        end)
        if not ok then
            return { error = "add_processor failed: " .. tostring(err) }
        end
        return {
            ok = true,
            track_id = args.track_id,
            plugin_id = info.unique_id,
            plugin_name = info.name,
            kind = info:is_instrument() and "instrument" or "effect",
        }
    end

    function handlers.get_plugin_params(args)
        local route = find_route(args.track_id)
        if not route or route:isnil() then
            return { error = "track not found: " .. tostring(args.track_id) }
        end
        local plugin_index = tonumber(args.plugin_index) or 0
        local insert = nth_insert(route, plugin_index)
        if not insert or insert:isnil() then
            return { error = "no plugin at index " .. tostring(plugin_index) }
        end
        local plugin = insert:plugin(0)
        if plugin:isnil() then
            return { error = "insert has no plugin(0)" }
        end
        local params = {}
        local ok_count, count = pcall(function() return plugin:parameter_count() end)
        if not ok_count or not count then
            return {
                track_id = args.track_id,
                plugin_index = plugin_index,
                plugin_id = plugin:unique_id(),
                params = params,
                note = "parameter_count unavailable on this plugin",
            }
        end
        for i = 0, count - 1 do
            local ok_d, desc = pcall(function() return plugin:parameter_descriptor(i) end)
            local label = "param_" .. tostring(i)
            local lo, hi = 0.0, 1.0
            if ok_d and desc then
                if desc.label then label = desc.label end
                if desc.lower then lo = desc.lower end
                if desc.upper then hi = desc.upper end
            end
            local ok_v, val = pcall(function()
                return ARDOUR.LuaAPI.get_processor_param(insert, i)
            end)
            params[#params + 1] = {
                id = tostring(i),
                name = label,
                min = lo,
                max = hi,
                value = (ok_v and val) or 0.0,
            }
        end
        return {
            track_id = args.track_id,
            plugin_index = plugin_index,
            plugin_id = plugin:unique_id(),
            params = params,
        }
    end

    function handlers.set_plugin_param(args)
        local route = find_route(args.track_id)
        if not route or route:isnil() then
            return { error = "track not found: " .. tostring(args.track_id) }
        end
        local plugin_index = tonumber(args.plugin_index) or 0
        local insert = nth_insert(route, plugin_index)
        if not insert or insert:isnil() then
            return { error = "no plugin at index " .. tostring(plugin_index) }
        end
        local param_id = args.param_id
        local index = tonumber(param_id)
        if index == nil then
            -- Resolve by label if a name was passed instead of an index.
            local got = handlers.get_plugin_params({
                track_id = args.track_id, plugin_index = plugin_index,
            })
            if got.error then return got end
            for _, p in ipairs(got.params or {}) do
                if p.name == param_id or p.id == param_id then
                    index = tonumber(p.id)
                    break
                end
            end
        end
        if index == nil then
            return { error = "unknown param: " .. tostring(param_id) }
        end
        local ok, err = pcall(function()
            ARDOUR.LuaAPI.set_processor_param(insert, index, args.value)
        end)
        if not ok then
            return { error = "set_processor_param failed: " .. tostring(err) }
        end
        return {
            ok = true,
            track_id = args.track_id,
            plugin_index = plugin_index,
            param_id = tostring(index),
            value = args.value,
        }
    end

    local function find_instrument(instrument_id)
        if not instrument_id or instrument_id == "" then return nil end
        for info in ARDOUR.LuaAPI.list_plugins():iter() do
            if info:is_instrument() and
               (info.unique_id == instrument_id or info.name == instrument_id) then
                return info
            end
        end
        return nil
    end

    -- Default instrument order, matching Ardour's own (InstrumentSelector /
    -- Auditioner): GMSynth first, then Reasonable Synth.
    --
    -- a-fluidsynth is DELIBERATELY excluded as a default. It instantiates with
    -- NO soundfont loaded and its run() callback emits silence until the user
    -- drags in an .sf2. Defaulting MIDI tracks to it is the single most common
    -- "the agent wrote notes but pressing play makes no sound" cause.
    local AUDIBLE_DEFAULTS = {
        "http://gareus.org/oss/lv2/gmsynth",       -- GMSynth: GM + embedded SF
        "https://community.ardour.org/node/7596",  -- Reasonable Synth: built-in
    }
    local FLUID_URI = "urn:ardour:a-fluidsynth"

    local function audible_default_info()
        for _, uri in ipairs(AUDIBLE_DEFAULTS) do
            local info = ARDOUR.LuaAPI.new_plugin_info(uri, ARDOUR.PluginType.LV2)
            if info and not info:isnil() then return info end
        end
        return nil
    end

    function handlers.create_midi_track(args)
        local name = args.name or "Stem MIDI"
        local requested_id = args.instrument_id or ""
        local gm_program = tonumber(string.match(requested_id, "^gm:(%d+)$"))
        local gm_drums = requested_id == "gm:drums"
        local instrument = nil
        -- true only when the chosen synth actually understands GM program
        -- changes AND is audible (so a program-change is worth sending).
        local is_gm_synth = false

        -- 1) An explicit, real plugin pick always wins (honour the user).
        if requested_id ~= "" and not gm_program and not gm_drums then
            instrument = find_instrument(requested_id)
        end

        -- 2) General-MIDI request: needs a GM-capable, audible synth. Only
        --    GMSynth qualifies (a-fluidsynth would be silent w/o a soundfont).
        if (not instrument or instrument:isnil()) and (gm_program or gm_drums) then
            local gm = ARDOUR.LuaAPI.new_plugin_info(
                "http://gareus.org/oss/lv2/gmsynth", ARDOUR.PluginType.LV2)
            if gm and not gm:isnil() then
                instrument = gm
                is_gm_synth = true
            end
            -- else: no GM synth in this build -> fall through to audible default
        end

        -- 3) Audible default (GMSynth -> Reasonable Synth).
        if not instrument or instrument:isnil() then
            instrument = audible_default_info()
            is_gm_synth = instrument and not instrument:isnil()
                and instrument.unique_id == "http://gareus.org/oss/lv2/gmsynth"
        end

        local has_instrument = instrument and not instrument:isnil()
        if not has_instrument then
            instrument = ARDOUR.PluginInfo()
        end
        local tl = Session:new_midi_track(
            ARDOUR.ChanCount(ARDOUR.DataType("midi"), 1),
            ARDOUR.ChanCount(ARDOUR.DataType("audio"), 2),
            true, instrument, nil,
            nil, 1, name, ARDOUR.PresentationInfo.max_order,
            ARDOUR.TrackMode.Normal, true)
        for t in tl:iter() do
            local preset_loaded = false
            local program_loaded = false
            if has_instrument and is_gm_synth and gm_program then
                local insert = t:the_instrument():to_insert()
                if not insert:isnil() then
                    local parser = ARDOUR.RawMidiParser()
                    parser:process_byte(0xC0)
                    if parser:process_byte(gm_program) then
                        program_loaded = insert:write_immediate_event(
                            Evoral.EventType.MIDI_EVENT,
                            parser:buffer_size(), parser:midi_buffer())
                    end
                end
            end
            if has_instrument and args.preset and args.preset ~= "" then
                local insert = t:the_instrument():to_insert()
                if not insert:isnil() then
                    local plugin = insert:plugin(0)
                    if not plugin:isnil() then
                        local preset = plugin:preset_by_label(args.preset)
                        if preset and preset.valid then
                            plugin:load_preset(preset)
                            preset_loaded = true
                        end
                    end
                end
            end
            -- If a GM timbre was requested but no GM synth is installed, we
            -- substituted an audible generic synth. Tell the truth so the agent
            -- can relay it instead of pretending the timbre was applied.
            local gm_downgraded = (gm_program or gm_drums) and not is_gm_synth
            return {
                track_id = t:name(),
                instrument = has_instrument,
                instrument_id = has_instrument and instrument.unique_id or "",
                instrument_name = has_instrument and instrument.name or "",
                preset = preset_loaded and args.preset or "",
                gm_program = program_loaded and gm_program or nil,
                note = gm_downgraded and
                    ("No General MIDI synth is installed, so this track uses "
                     .. "the built-in Reasonable Synth. It is audible but plays "
                     .. "one generic tone rather than the requested instrument.")
                    or nil,
            }
        end
        return { error = "track creation returned empty list" }
    end

    local function find_track(name)
        for r in Session:get_routes():iter() do
            if r:name() == name then return r:to_track() end
        end
        return nil
    end

    local function find_route(name)
        for r in Session:get_routes():iter() do
            if r:name() == name then return r end
        end
        return nil
    end

    function handlers.delete_track(args)
        local route = find_route(args.track_id)
        if not route then
            return { error = "track not found: " .. tostring(args.track_id) }
        end
        Session:remove_route(route)
        return { ok = true, track_id = args.track_id }
    end

    -- Build a Processor for the best available audible default synth.
    local function new_audible_proc()
        for _, uri in ipairs(AUDIBLE_DEFAULTS) do
            local proc = ARDOUR.LuaAPI.new_plugin(
                Session, uri, ARDOUR.PluginType.LV2, "")
            if proc and not proc:isnil() then return proc, uri end
        end
        return nil, nil
    end

    -- Is this route's instrument a bare a-fluidsynth (silent: no soundfont)?
    local function instrument_is_silent_fluid(route)
        local cur = route:the_instrument()
        if cur:isnil() then return false, cur end
        local pi = cur:to_insert()
        if pi:isnil() then return false, cur end
        local pl = pi:plugin(0)
        if pl:isnil() then return false, cur end
        return pl:unique_id() == FLUID_URI, cur
    end

    -- Make a track audible: add a synth if it has none, or REPLACE a silent
    -- a-fluidsynth with the Reasonable Synth so existing tracks start sounding.
    function handlers.add_instrument(args)
        local route = find_route(args.track_id)
        if not route then return { error = "track not found: " .. tostring(args.track_id) } end
        local silent, cur = instrument_is_silent_fluid(route)
        if not cur:isnil() and not silent then
            return { track_id = args.track_id, instrument = true, already = true }
        end
        local proc, uri = new_audible_proc()
        if not proc then return { error = "no audible instrument plugin available" } end
        if silent then
            local rv = route:replace_processor(cur, proc, nil)
            return { track_id = args.track_id, instrument = true,
                     replaced = (rv == 0), instrument_id = uri }
        end
        route:add_processor_by_index(proc, 0, nil, true)
        return { track_id = args.track_id, instrument = true, added = true,
                 instrument_id = uri }
    end

    -- One-shot rescue: scan every MIDI track and make silent ones audible.
    -- Safe to call repeatedly; tracks with a working synth are left untouched.
    function handlers.fix_silent_instruments(args)
        local fixed = {}
        for r in Session:get_routes():iter() do
            local tr = r:to_track()
            if not tr:isnil() and not tr:to_midi_track():isnil() then
                local silent, cur = instrument_is_silent_fluid(r)
                if cur:isnil() then
                    local proc, uri = new_audible_proc()
                    if proc then
                        r:add_processor_by_index(proc, 0, nil, true)
                        fixed[#fixed + 1] = { track_id = r:name(), action = "added", instrument_id = uri }
                    end
                elseif silent then
                    local proc, uri = new_audible_proc()
                    if proc and r:replace_processor(cur, proc, nil) == 0 then
                        fixed[#fixed + 1] = { track_id = r:name(), action = "replaced", instrument_id = uri }
                    end
                end
            end
        end
        return { ok = true, fixed = fixed, count = #fixed }
    end

    function handlers.diagnose_audio(args)
        local d = {}
        d.sample_rate = Session:nominal_sample_rate()
        local eng = Session:engine()
        if eng then
            d.engine_running = eng:running()
            d.backend = eng:current_backend_name()
            d.dsp_load = eng:get_dsp_load()
        end
        d.transport_rolling = Session:transport_rolling()
        d.transport_sample = Session:transport_sample()
        local master = Session:master_out()
        if master and not master:isnil() then
            d.master = master:name()
            d.master_active = master:active()
            local o = master:output()
            d.master_out_ports = o:n_ports():n_audio()
            d.master_out_connected_to_hw = o:physically_connected()
        else
            d.master = "NONE"
        end
        local r = find_route(args.track_id or "Chords")
        if r then
            d.track = r:name()
            d.track_active = r:active()
            local inst = r:the_instrument()
            d.track_has_instrument = not inst:isnil()
            if not inst:isnil() then d.instrument_active = inst:active() end
        end
        return d
    end

    function handlers.get_track_content(args)
        local route = find_route(args.track_id)
        if not route then return { error = "no such track" } end
        local track = route:to_track()
        if track:isnil() then return { error = "route is not a track" } end
        local regions = {}
        for region in track:playlist():region_list():iter() do
            local item = {
                name = region:name(),
                position_samples = region:position():samples(),
                length_samples = region:length():samples(),
                midi_notes = 0,
            }
            local mr = region:to_midiregion()
            if not mr:isnil() then
                for _ in ARDOUR.LuaAPI.note_list(mr:model()):iter() do
                    item.midi_notes = item.midi_notes + 1
                end
            end
            regions[#regions + 1] = item
        end
        return { track_id = args.track_id, regions = regions }
    end

    function handlers.repair_midi_region(args)
        local route = find_track(args.track_id)
        if not route or route:isnil() then return { error = "no such track" } end
        local mt = route:to_midi_track()
        if mt:isnil() then return { error = "not a midi track" } end
        local sr = Session:nominal_sample_rate()
        local tempo = Temporal.TempoMap.read():tempo_at(
            Temporal.timepos_t(0)):quarter_notes_per_minute()
        local repaired = 0
        for region in mt:playlist():region_list():iter() do
            local mr = region:to_midiregion()
            if not mr:isnil() then
                local max_beat = 0
                for note in ARDOUR.LuaAPI.note_list(mr:model()):iter() do
                    local finish = (note:time():to_ticks() +
                                    note:length():to_ticks()) / 1920.0
                    if finish > max_beat then max_beat = finish end
                end
                local samples = math.ceil(
                    (max_beat + 1) * (60.0 / tempo) * sr)
                if region:length():samples() < samples then
                    region:set_length(Temporal.timecnt_t(samples))
                    repaired = repaired + 1
                end
            end
        end
        return { ok = true, repaired = repaired }
    end

    -- find or create a MIDI region on a track's playlist spanning enough beats.
    -- Region creation goes through the editor's MidiTimeAxisView:add_region
    -- (this Ardour build has no LuaAPI region constructor).
    local function get_or_make_region(route, mt, beats_needed)
        local pl = mt:playlist()
        local sr = Session:nominal_sample_rate()
        local tempo = 120.0
        local ok_t, t = pcall(function()
            return Temporal.TempoMap.read():tempo_at(
                Temporal.timepos_t(0)):quarter_notes_per_minute()
        end)
        if ok_t and t and t > 0 then tempo = t end
        local required_samples = math.ceil(
            (beats_needed + 1) * (60.0 / tempo) * sr)
        for r in pl:region_list():iter() do
            local mr = r:to_midiregion()
            if not mr:isnil() then
                if r:length():samples() < required_samples then
                    r:set_length(Temporal.timecnt_t(required_samples))
                end
                return mr
            end
        end
        local ok_region, region = pcall(function()
            return ARDOUR.LuaAPI.ensure_midi_region(
                Session, mt, beats_needed + 1)
        end)
        if ok_region and region and not region:isnil() then
            return region
        end
        -- none yet: create a blank region via the track's editor view
        if not Editor then return nil end
        local rtav = Editor:rtav_from_route(route)
        if not rtav then return nil end
        local tav = rtav:to_timeaxisview()
        local mtav = tav:to_midi_time_axis_view()
        if not mtav then return nil end
        local pos = Temporal.timepos_t(0)
        local len = Temporal.timecnt_t(required_samples)
        mtav:add_region(pos, len, true)
        for r in pl:region_list():iter() do
            local mr = r:to_midiregion()
            if not mr:isnil() then return mr end
        end
        return nil
    end

    function handlers.insert_midi_notes(args)
        local route = find_track(args.track_id)
        if not route or route:isnil() then return { error = "no such track" } end
        local mt = route:to_midi_track()
        if mt:isnil() then return { error = "not a midi track" } end

        -- furthest beat we need the region to span
        local max_beat = 4
        for _, n in ipairs(args.notes) do
            local e = n.start_beat + n.length_beats
            if e > max_beat then max_beat = e end
        end

        local mr = get_or_make_region(route, mt, max_beat)
        if not mr or mr:isnil() then return { error = "could not create midi region" } end

        local mm = mr:midi_source(0):model()
        local cmd = mm:new_note_diff_command("stem insert")
        local Beats = Temporal.Beats
        for _, n in ipairs(args.notes) do
            -- Beats(whole, ticks); 1920 ticks per quarter note
            local function to_beats(b)
                local whole = math.floor(b)
                local ticks = math.floor((b - whole) * 1920 + 0.5)
                return Beats(whole, ticks)
            end
            local note = ARDOUR.LuaAPI.new_noteptr(
                n.channel or 0,
                to_beats(n.start_beat),
                to_beats(n.length_beats),
                n.pitch, n.velocity or 100)
            cmd:add(note)
        end
        mm:apply_command(Session, cmd)
        return { ok = true, inserted = #args.notes }
    end

    function handlers.get_midi_notes(args)
        local tr = find_track(args.track_id)
        if not tr or tr:isnil() then return { error = "no such track" } end
        local mt = tr:to_midi_track()
        if mt:isnil() then return { error = "not a midi track" } end
        local out = {}
        for r in mt:playlist():region_list():iter() do
            local mr = r:to_midiregion()
            if not mr:isnil() then
                local mm = mr:model()
                for note in ARDOUR.LuaAPI.note_list(mm):iter() do
                    out[#out+1] = {
                        pitch = note:note(),
                        start_beat = note:time():to_ticks() / 1920.0,
                        length_beats = note:length():to_ticks() / 1920.0,
                        velocity = note:velocity(),
                    }
                end
            end
        end
        return { notes = out }
    end

    function handlers.import_audio(args)
        local sr = 48000
        local ok_sr, s = pcall(function() return Session:nominal_sample_rate() end)
        if ok_sr and s and s > 0 and s < 1000000 then sr = s end
        local track_id = ARDOUR.LuaAPI.import_audio_file(
            Session, args.file_path,
            math.floor((args.position_seconds or 0) * sr))
        if track_id and track_id ~= "" then
            return {
                ok = true, imported = args.file_path,
                track_id = track_id
            }
        end
        return { error = "audio import did not create a track" }
    end

    function handlers.set_tempo(args)
        if not ARDOUR.LuaAPI.set_session_tempo(Session, args.bpm) then
            return { error = "tempo change failed" }
        end
        return { ok = true }
    end

    function handlers.set_track_gain(args)
        local tr = find_track(args.track_id)
        if not tr or tr:isnil() then return { error = "no such track" } end
        tr:gain_control():set_value(
            ARDOUR.DSP.dB_to_coefficient(args.gain_db),
            PBD.GroupControlDisposition.NoGroup)
        return { ok = true }
    end

    function handlers.set_track_mute(args)
        local route = find_route(args.track_id)
        if not route or route:isnil() then return { error = "no such track or bus" } end
        route:mute_control():set_value(args.muted and 1 or 0,
            PBD.GroupControlDisposition.NoGroup)
        return { ok = true }
    end

    function handlers.add_marker(args)
        Session:begin_reversible_command("Stem: add marker")
        local pos = Temporal.timepos_t(math.floor(args.position_seconds * Session:sample_rate()))
        Session:locations():add_mark(pos, args.name, false)
        Session:commit_reversible_command(nil)
        return { ok = true }
    end

    function handlers.transport_play(args)
        -- Start with an explicit LocateRoll request. Unlike OSC this is
        -- acknowledged, and unlike goto_start it cannot stop after locating
        -- when the transport state machine is already transitioning.
        if args and args.from_start == false then
            Session:request_roll(ARDOUR.TransportRequestSource.TRS_UI)
        else
            Session:request_locate(
                0, false,
                ARDOUR.LocateTransportDisposition.MustRoll,
                ARDOUR.TransportRequestSource.TRS_UI)
        end
        return { ok = true, requested = "roll", from_start = not (args and args.from_start == false) }
    end
    function handlers.rewind(args)
        Session:goto_start(false)
        return { ok = true }
    end
    function handlers.transport_stop(args) Session:request_stop(false, false, ARDOUR.TransportRequestSource.TRS_UI) return { ok = true } end
    function handlers.locate(args)
        Session:request_locate(math.floor(args.seconds * Session:sample_rate()),
            false, ARDOUR.LocateTransportDisposition.MustStop,
            ARDOUR.TransportRequestSource.TRS_UI)
        return { ok = true }
    end

    function handlers.undo(args) Editor:undo(1) return { ok = true } end
    function handlers.save_session(args) Session:save_state("", false, false, false, false, false) return { ok = true } end

    -- ---- dispatch: one poll per LuaTimerDS tick (~100ms, UI thread) ----
    -- the request file is consumed (deleted) once handled; factory locals
    -- don't persist across ticks, so dedupe-by-id alone is not enough.
    local function dispatch()
        local raw = read_file(req_path)
        if not raw or raw == "" then return end
        os.remove(req_path)
        local req = json.decode(raw)
        if not req then
            write_file(dir .. "/bridge_error.log", "decode failed for: " .. raw)
            return
        end
        local h = handlers[req.method]
        local resp
        if h then
            local ok, result = pcall(h, req.args or {})
            resp = ok and { id = req.id, result = result }
                      or { id = req.id, error = tostring(result) }
        else
            resp = { id = req.id, error = "unknown method: " .. tostring(req.method) }
        end
        local ok2, encoded = pcall(json.encode, resp)
        if not ok2 then
            encoded = string.format('{"id":%q,"error":"encode failed: %s"}',
                tostring(req.id), tostring(encoded):gsub('"', "'"))
        end
        write_file(resp_path, encoded)
    end

    dispatch()

end
