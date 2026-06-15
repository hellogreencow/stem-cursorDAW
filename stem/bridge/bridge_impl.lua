-- Stem bridge implementation. Hot-reloaded by the loader hook each tick.
-- Edit freely; no Ardour restart or hook re-registration needed.
return function ()
    local home = os.getenv("HOME")
    local dir = home .. "/.stem"
    os.execute("mkdir -p " .. dir)
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
        return {
            name = Session:name(),
            tempo = tempo,
            sample_rate = Session:sample_rate(),
            playhead_seconds = Session:transport_sample() / Session:sample_rate(),
            tracks = tracks,
        }
    end

    function handlers.create_midi_track(args)
        local name = args.name or "Stem MIDI"
        local tl = Session:new_midi_track(
            ARDOUR.ChanCount(ARDOUR.DataType("midi"), 1),
            ARDOUR.ChanCount(ARDOUR.DataType("audio"), 2),
            true, ARDOUR.PluginInfo(), nil,
            nil, 1, name, ARDOUR.PresentationInfo.max_order,
            ARDOUR.TrackMode.Normal, true)
        for t in tl:iter() do
            return { track_id = t:name() }
        end
        return { error = "track creation returned empty list" }
    end

    local function find_track(name)
        for r in Session:get_routes():iter() do
            if r:name() == name then return r:to_track() end
        end
        return nil
    end

    -- find or create a MIDI region on a track's playlist spanning enough beats.
    -- Region creation goes through the editor's MidiTimeAxisView:add_region
    -- (this Ardour build has no LuaAPI region constructor).
    local function get_or_make_region(route, mt, beats_needed)
        local pl = mt:playlist()
        for r in pl:region_list():iter() do
            local mr = r:to_midiregion()
            if not mr:isnil() then return mr end
        end
        -- none yet: create a blank region via the track's editor view
        local rtav = Editor:rtav_from_route(route)
        if not rtav then return nil end
        local tav = rtav:to_timeaxisview()
        local mtav = tav:to_midi_time_axis_view()
        if not mtav then return nil end
        local sr = Session:nominal_sample_rate()
        -- 1 bar = 4 beats; length in samples at 120bpm fallback (0.5s/beat)
        local secs = (beats_needed + 1) * 0.5
        local pos = Temporal.timepos_t(0)
        local len = Temporal.timecnt_t(math.floor(sr * secs))
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

        Session:begin_reversible_command("Stem: insert MIDI notes")
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
        Session:commit_reversible_command(nil)
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

    function handlers.set_tempo(args)
        Session:begin_reversible_command("Stem: set tempo")
        local tm = Temporal.TempoMap.write_copy()
        tm:set_tempo(Temporal.Tempo(args.bpm, args.bpm, 4), Temporal.timepos_t(0))
        Temporal.TempoMap.update(tm)
        Session:commit_reversible_command(nil)
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
        local tr = find_track(args.track_id)
        if not tr or tr:isnil() then return { error = "no such track" } end
        tr:mute_control():set_value(args.muted and 1 or 0,
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

    function handlers.transport_play(args) Session:request_roll(ARDOUR.TransportRequestSource.TRS_UI) return { ok = true } end
    function handlers.transport_stop(args) Session:request_stop(false, false, ARDOUR.TransportRequestSource.TRS_UI) return { ok = true } end
    function handlers.locate(args)
        Session:request_locate(math.floor(args.seconds * Session:sample_rate()),
            ARDOUR.LocateTransportDisposition.MustStop, ARDOUR.TransportRequestSource.TRS_UI)
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
