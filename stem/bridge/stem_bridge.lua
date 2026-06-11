ardour {
    ["type"]    = "EditorHook",
    name        = "Stem Agent Bridge",
    license     = "GPL",
    author      = "Stem",
    description = [[JSON-RPC command server for the Stem AI agent. Fires on
Ardour's deci-second UI timer; reads ~/.stem/request.json, executes against
the session, writes ~/.stem/response.json. Enable in
Edit > Lua Scripts > Script Manager > Action Hooks.]]
}

function signals ()
    return LuaSignal.Set():add ({[LuaSignal.LuaTimerDS] = true})
end

function factory ()
return function (signal, ref, ...)

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
        elseif t == "number" or t == "boolean" then return tostring(v)
        else return "null" end
    end
    -- decode: cheap but sufficient for our request envelope (the Python side
    -- writes compact, known-shape JSON). Uses load() on a translated literal.
    function json.decode(s)
        s = s:gsub('("[^"]-"):', '[%1]=')
        s = s:gsub('%[(%["[^"]-"%])%]=', '%1=')
        local f = load("return " .. s:gsub('%[(["%[])', '{%1'):gsub('(["%]])%]', '%1}'))
        if f then return f() end
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
        local tm = Temporal.TempoMap.read()
        return {
            name = Session:name(),
            tempo = tm:tempo_at(Temporal.timepos_t(0)):quarter_notes_per_minute(),
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

    function handlers.insert_midi_notes(args)
        local tr = find_track(args.track_id)
        if not tr or tr:isnil() then return { error = "no such track" } end
        local mt = tr:to_midi_track()
        if mt:isnil() then return { error = "not a midi track" } end

        Session:begin_reversible_command("Stem: insert MIDI notes")

        local pl = mt:playlist()
        -- find or create a region at position 0 spanning the notes
        local region
        for r in pl:region_list():iter() do region = r break end
        if not region then
            -- create an empty source/region the documented way
            local pos = Temporal.timepos_t(0)
            region = ARDOUR.LuaAPI.new_midi_region(Session, pl, pos,
                Temporal.timecnt_t(Session:sample_rate() * 8))
        end
        local mr = region:to_midiregion()
        if mr:isnil() then return { error = "could not obtain midi region" } end

        local mm = mr:model()
        local midi_command = mm:new_note_diff_command("stem insert")
        local bfc = Temporal.Beats
        for _, n in ipairs(args.notes) do
            local note = ARDOUR.LuaAPI.new_noteptr(
                n.channel or 0,
                bfc.from_double(n.start_beat),
                bfc.from_double(n.length_beats),
                n.pitch, n.velocity or 100)
            midi_command:add(note)
        end
        mm:apply_command(Session, midi_command)

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
                        start_beat = note:time():to_double(),
                        length_beats = note:length():to_double(),
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

    function handlers.undo(args) Session:undo(1) return { ok = true } end
    function handlers.save_session(args) Session:save_state("", false, false, false, false, false) return { ok = true } end

    -- ---- dispatch: one poll per LuaTimerDS tick (~100ms, UI thread) ----
    -- the request file is consumed (deleted) once handled; factory locals
    -- don't persist across ticks, so dedupe-by-id alone is not enough.
    local function dispatch()
        local raw = read_file(req_path)
        if not raw or raw == "" then return end
        os.remove(req_path)
        local req = json.decode(raw)
        if not req then return end
        local h = handlers[req.method]
        local resp
        if h then
            local ok, result = pcall(h, req.args or {})
            resp = ok and { id = req.id, result = result }
                      or { id = req.id, error = tostring(result) }
        else
            resp = { id = req.id, error = "unknown method: " .. tostring(req.method) }
        end
        write_file(resp_path, json.encode(resp))
    end

    dispatch()

end
end
