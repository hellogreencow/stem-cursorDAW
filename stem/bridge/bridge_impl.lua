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
local function tempo_bpm_read ()
    local pos_ok, pos = pcall (function () return Temporal.timepos_t (0) end)
    if not pos_ok then return nil end

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

    return nil
end

-- For lengths and reports a default is tolerable; for an undo inverse it is
-- not (set_tempo uses tempo_bpm_read and refuses when it cannot read).
local function tempo_bpm ()
    return tempo_bpm_read () or 120.0
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
-- UNDO: what Ardour can and cannot do, and what this bridge does about it
-- ======================================================================
--
-- Facts from Ardour source (8.12 = tag 8.12, 9.8 = tag 9.8):
--
--  * Lua cannot see Ardour's undo history. The only history calls bound are
--    begin/commit/abort_reversible_command, abort_empty_reversible_command,
--    collected_undo_commands, add_command, add_stateful_diff_command
--    (8.12 libs/ardour/luabindings.cc:3118-3124 on Session; 9.8
--    luabindings.cc:532-539 on PBD::HistoryOwner). There is no undo_depth,
--    no next_undo, no way to ask "is the top of the stack mine?".
--  * Editor:undo(n) (gtk2_ardour/luainstance.cc:907 in 8.12, :972 in 9.8)
--    pops whatever is on top. The old handlers.undo called it blindly, and
--    most Stem mutations put NOTHING on the stack, so it popped the user's
--    own previous edit.
--  * Reversible commands do not nest: begin_reversible_command while one is
--    open aborts BOTH and returns (8.12 session_state.cc:3382-3393, 9.8
--    libs/pbd/history_owner.cc:68-79). So the bridge may never open a command
--    around a call that opens its own (apply_diff_command_as_commit,
--    MidiTimeAxisView:add_region(..., true), Editor:add_location_mark, the
--    fork's set_session_tempo).
--  * Controllable writes (gain, mute, solo, pan) never touch undo history:
--    AutomationControl::set_value (9.8 automation_control.cc:125) has no
--    history call, and gain_control.cc / mute_control.cc / solo_control.cc /
--    gtk2_ardour/gain_meter.cc contain none. Route creation and removal are
--    not undoable in Ardour either (Session::new_midi_track, session.cc:2813
--    in 9.8; the GUI's remove-track prompt says "This action cannot be
--    undone", editor_ops.cc:8018 in 9.8 / :8517 in 8.12). Processor add /
--    replace / remove are not undoable (route.cc adds history only for
--    automation mementos; processor_box.cc has no history call).
--
-- So the design is:
--
--  1. Every Stem mutation that Ardour CAN record is recorded, as a named
--     reversible command, balanced, never nested, with nothing that can fail
--     between begin and commit (in_transaction below; failure -> the diffs
--     already applied are undone and the command is aborted).
--  2. Every Stem mutation ALSO pushes exactly one entry on the bridge's own
--     journal, carrying the exact inverse, computed at mutation time from
--     the state it replaced. handlers.undo pops the top entry and applies
--     that inverse. It never calls Editor:undo, so it can never pop an edit
--     the user made.
--  3. Before applying an inverse, the entry checks the object is still the
--     way Stem left it. If the user has since changed that same object, undo
--     refuses (and says why) rather than silently clobbering their edit.
--  4. A mutation that fails raises (dispatch-level error) BEFORE anything is
--     journaled. That matters on the Python side: ArdourBridge._call raises
--     on a dispatch error, so no action id is recorded there either, and the
--     Python action sequence and this journal stay one-to-one. (A handler
--     that returned {error=...} as a VALUE was silently recorded as an
--     action by ArdourBridge, which would misalign every later undo.)
--
-- Where the inverse is applied through a MidiModel diff or a playlist /
-- region diff, it is itself committed as a named reversible command
-- ("Stem: undo ..."), so Ardour's own history stays truthful: the user's
-- Ctrl-Z after a Stem undo re-applies the Stem change, exactly as reverting
-- a commit works.
--
-- The journal lives in a global so the loader's periodic reload of this
-- chunk (stem_bridge.lua reloads every 10 s) does not wipe it. It does not
-- survive an Ardour restart; undo then says there is nothing to undo.

-- Plain _G indexing, NOT rawget/rawset: Ardour's Lua sandbox sets rawget and
-- rawset to nil (libs/lua/luastate.cc:99 in 9.8), and a nil call here would
-- stop the whole chunk from loading.
local J = _G.STEM_UNDO_JOURNAL
if type (J) ~= "table" or J.version ~= 1 then
    J = { version = 1, seq = 0, entries = {} }
    _G.STEM_UNDO_JOURNAL = J
end

-- Handlers raise on failure (see 4. above). error(msg, 0) keeps the message
-- free of a "file:line:" prefix so the user-facing text stays readable.
local function fail (msg) error (msg, 0) end

local function object_key (obj)
    local ok, k = pcall (function () return obj:id ():to_s () end)
    if ok and k then return k end
    local ok2, n = pcall (function () return obj:name () end)
    if ok2 and n then return "name:" .. n end
    return tostring (obj)
end

local function route_by_key (key)
    for r in Session:get_routes ():iter () do
        if object_key (r) == key then return r end
    end
    return nil
end

local function journal_push (label, entry)
    J.seq = J.seq + 1
    local id = string.format ("stem-%d", J.seq)
    entry.id      = id
    entry.label   = label
    entry.session = Session:name ()
    J.entries[#J.entries + 1] = entry
    return id
end

-- An action Ardour could record and a no-op both still get an entry: the
-- Python side records one action per successful call, and the two sequences
-- must stay one-to-one.
local function journal_noop (label)
    return journal_push (label, {
        check   = function () return true end,
        inverse = function () end,
        noop    = true,
    })
end

-- Ardour aborts BOTH commands if one is begun while another is open
-- (history_owner.cc:68). If something (a plugin GUI, another script) has left
-- a command open with work in it, refuse up front rather than destroy that
-- work. collected_undo_commands is bound in both (8.12 luabindings.cc:3121,
-- 9.8 :536). It reads 0 for an open-but-empty command, which this cannot see.
local function assert_no_open_command ()
    local ok, n = pcall (function () return Session:collected_undo_commands () end)
    if ok and type (n) == "number" and n > 0 then
        fail ("Ardour has an unfinished edit open (" .. n .. " pending undo "
              .. "command(s)); finish or cancel it before Stem edits the session")
    end
end

local function near (a, b)
    return type (a) == "number" and type (b) == "number" and math.abs (a - b) <= 1e-6
end

-- One named reversible command around fn, for changes made through
-- StatefulDiffCommands (region / playlist properties). fn gets tx with
-- tx.before(obj) / tx.after(obj) bracketing each change (clear_changes, then
-- add_stateful_diff_command — the pattern of Ardour's own
-- share/scripts/_insert_region_gaps.lua:39-51, identical in 8.12 and 9.8).
-- If fn throws, every diff already recorded is undone (StatefulDiffCommand:undo
-- is bound, luabindings.cc:542 / :578) and the command is aborted, so a
-- failure leaves neither an open command nor a half-applied change.
local function in_transaction (name, fn)
    local diffs = {}
    local tx = {}
    function tx.before (obj) obj:to_stateful ():clear_changes () end
    function tx.after (obj)
        diffs[#diffs + 1] = Session:add_stateful_diff_command (obj:to_statefuldestructible ())
    end
    assert_no_open_command ()
    Session:begin_reversible_command (name)
    local ok, err = pcall (fn, tx)
    if not ok then
        for i = #diffs, 1, -1 do
            local d = diffs[i]
            pcall (function () d:undo () end)
        end
        pcall (function () Session:abort_reversible_command () end)
        error (err, 0)
    end
    if not Session:abort_empty_reversible_command () then
        Session:commit_reversible_command (nil)
    end
end

-- ----------------------------------------------------------------------
-- notes: validation, ticks, value identity
-- ----------------------------------------------------------------------

local function to_int (v)
    if math.tointeger then return math.tointeger (v) or v end
    return v
end

local function beats_to_ticks (b)
    return math.floor (b * TPB + 0.5)
end

local function ticks_to_beats_obj (t)
    local whole = math.floor (t / TPB)
    return Temporal.Beats (to_int (whole), to_int (t - whole * TPB))
end

local function finite (x) return type (x) == "number" and x == x and x ~= math.huge and x ~= -math.huge end

-- Validate EVERYTHING before any mutation. Same note shape insert_midi_notes
-- has always taken: {pitch, start_beat, length_beats, velocity=100, channel=0}.
local function validate_notes (notes, what)
    if notes == nil then return {} end
    if type (notes) ~= "table" then fail (what .. ": notes must be a list") end
    local out = {}
    for i, n in ipairs (notes) do
        if type (n) ~= "table" then fail (string.format ("%s: note %d is not an object", what, i)) end
        local pitch = tonumber (n.pitch)
        local st    = tonumber (n.start_beat)
        local len   = tonumber (n.length_beats)
        local vel   = (n.velocity == nil) and 100 or tonumber (n.velocity)
        local ch    = (n.channel  == nil) and 0   or tonumber (n.channel)
        if not finite (pitch) or pitch ~= math.floor (pitch) or pitch < 0 or pitch > 127 then
            fail (string.format ("%s: note %d has bad pitch %s (0-127)", what, i, tostring (n.pitch)))
        end
        if not finite (st) or st < 0 then
            fail (string.format ("%s: note %d has bad start_beat %s", what, i, tostring (n.start_beat)))
        end
        if not finite (len) or len <= 0 then
            fail (string.format ("%s: note %d has bad length_beats %s", what, i, tostring (n.length_beats)))
        end
        if not finite (vel) or vel ~= math.floor (vel) or vel < 0 or vel > 127 then
            fail (string.format ("%s: note %d has bad velocity %s (0-127)", what, i, tostring (n.velocity)))
        end
        if not finite (ch) or ch ~= math.floor (ch) or ch < 0 or ch > 15 then
            fail (string.format ("%s: note %d has bad channel %s (0-15)", what, i, tostring (n.channel)))
        end
        local lt = beats_to_ticks (len)
        if lt < 1 then lt = 1 end
        out[#out + 1] = { pitch = to_int (pitch), start = beats_to_ticks (st), length = lt,
                          velocity = to_int (vel), channel = to_int (ch) }
    end
    return out
end

local function new_note (v)
    return ARDOUR.LuaAPI.new_noteptr (v.channel, ticks_to_beats_obj (v.start),
                                      ticks_to_beats_obj (v.length), v.pitch, v.velocity)
end

local function note_value (note)
    return { pitch = note:note (), start = note:time ():to_ticks (),
             length = note:length ():to_ticks (), velocity = note:velocity (),
             channel = note:channel () }
end

local function value_key (v)
    return string.format ("%d:%d:%d:%d:%d", v.pitch, v.start, v.length, v.velocity, v.channel)
end

-- For each wanted value, the model's own NotePtr carrying it (each model note
-- used once). Returns the list, or nil + how many are missing.
local function find_model_notes (mm, values)
    local pool = {}
    for note in ARDOUR.LuaAPI.note_list (mm):iter () do
        local k = value_key (note_value (note))
        pool[k] = pool[k] or {}
        table.insert (pool[k], note)
    end
    local found, missing = {}, 0
    for _, v in ipairs (values) do
        local bucket = pool[value_key (v)]
        if bucket and #bucket > 0 then
            found[#found + 1] = table.remove (bucket)
        else
            missing = missing + 1
        end
    end
    if missing > 0 then return nil, missing end
    return found
end

-- Apply a MidiModel note diff as ONE reversible command. The model opens and
-- commits its own command (9.8 midi_model.cc:104-110, 8.12 :95-101), so no
-- command may be open here. apply_command is the deprecated alias of the
-- same function (luabindings.cc:1809 / :1853), kept as a fallback.
local function apply_note_diff (mm, cmd)
    local applied = pcall (function () mm:apply_diff_command_as_commit (Session, cmd) end)
    if not applied then
        mm:apply_command (Session, cmd)
    end
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

-- ----------------------------------------------------------------------
-- MIDI regions
-- ----------------------------------------------------------------------

local function first_midi_region (mt)
    for r in mt:playlist ():region_list ():iter () do
        local mr = r:to_midiregion ()
        if not mr:isnil () then return r, mr end
    end
    return nil
end

local function samples_for_beats (beats)
    return math.ceil ((beats + 1) * (60.0 / tempo_bpm ()) * sample_rate ())
end

-- Grow a region as its own reversible command (region length is a Stateful
-- property; StatefulDiffCommand is Ardour's record for it). Returns the old
-- length in samples.
local function extend_region (region, samples, label)
    local old = region:length ():samples ()
    in_transaction (label, function (tx)
        tx.before (region)
        region:set_length (Temporal.timecnt_t (samples))
        tx.after (region)
    end)
    return old
end

local function remove_region_reversibly (mt, region, label)
    local pl = mt:playlist ()
    in_transaction (label, function (tx)
        tx.before (pl)
        pl:remove_region (region)
        tx.after (pl)
    end)
end

local function region_note_count (mr)
    local mm = region_model (mr)
    if not mm then return 0 end
    local n = 0
    for _ in ARDOUR.LuaAPI.note_list (mm):iter () do n = n + 1 end
    return n
end

local function region_still_on (mt, region)
    local key = object_key (region)
    for r in mt:playlist ():region_list ():iter () do
        if object_key (r) == key then return true end
    end
    return false
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
        -- how undo works on this bridge: every mutation carries an action_id
        -- and handlers.undo applies its recorded inverse (never Editor:undo).
        undo_model = "bridge-journal",
        undo_depth = #J.entries,
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
        -- program / preset setup can throw; the track already exists by
        -- then, so a failure removes it again (no half-made track, no entry)
        local setup_ok, setup_err = pcall (function ()
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
        end)
        if not setup_ok then
            pcall (function () Session:remove_route (t) end)
            fail ("track setup failed, track removed again: " .. tostring (setup_err))
        end
        local gm_downgraded = (gm_program or gm_drums) and not is_gm_synth
        -- Journal: Ardour cannot undo track creation (see UNDO above), so the
        -- inverse is removing the route again — refused if the track has
        -- since gained content, because then removing it would destroy work
        -- that is not Stem's (Stem's own later edits on it are undone first:
        -- the journal is last-in, first-out).
        local key, tname = object_key (t), t:name ()
        local action_id = journal_push ("create track " .. tname, {
            check = function ()
                local r = route_by_key (key)
                if not r then return true end   -- already gone: nothing to do
                local tr = r:to_track ()
                if not tr:isnil () then
                    for _ in tr:playlist ():region_list ():iter () do
                        return false, "track '" .. r:name () .. "' now has regions on it that Stem "
                            .. "did not put there; not deleting it"
                    end
                end
                return true
            end,
            inverse = function ()
                local r = route_by_key (key)
                if r then Session:remove_route (r) end
            end,
        })
        return {
            track_id        = tname,
            instrument      = has_instrument,
            instrument_id   = has_instrument and instrument.unique_id or "",
            instrument_name = has_instrument and instrument.name or "",
            preset          = preset_loaded and args.preset or "",
            gm_program      = program_loaded and gm_program or nil,
            action_id       = action_id,
            undoable        = true,
            note = gm_downgraded and
                ("No General MIDI synth is installed, so this track uses "
                 .. "the built-in Reasonable Synth. It is audible but plays "
                 .. "one generic tone rather than the requested instrument.")
                or nil,
        }
    end
    fail ("track creation returned empty list")
end


function handlers.delete_track (args)
    local route = find_route (args.track_id)
    if not route then
        fail ("track not found: " .. tostring (args.track_id))
    end
    -- Not journaled and not undoable: Ardour itself cannot undo a route
    -- removal (the GUI's prompt says so, editor_ops.cc:8018 in 9.8), and the
    -- Python side records no action for it, so the journals stay aligned.
    Session:remove_route (route)
    return { ok = true, track_id = args.track_id, undoable = false }
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
    if not route then fail ("track not found: " .. tostring (args.track_id)) end
    -- Processor changes never reach Ardour's undo history (see UNDO above).
    -- This handler journals an inverse ONLY when the caller asks for it
    -- (args.journal == true): ArdourBridge records no action for
    -- add_instrument today, and an entry it does not know about would make
    -- its next undo pop the wrong thing.
    local want_journal = (args.journal == true)
    local silent, cur = instrument_is_silent_fluid (route)
    if not cur:isnil () and not silent then
        local out = { track_id = args.track_id, instrument = true, already = true, undoable = want_journal }
        if want_journal then out.action_id = journal_noop ("add instrument (none needed)") end
        return out
    end
    local proc, uri = new_audible_proc ()
    if not proc then fail ("no audible instrument plugin available") end
    local key = object_key (route)
    if silent then
        local rv = route:replace_processor (cur, proc, nil)
        if rv ~= 0 then fail ("replace_processor failed (" .. tostring (rv) .. ")") end
        local out = { track_id = args.track_id, instrument = true, replaced = true,
                      instrument_id = uri, undoable = want_journal }
        if want_journal then
            out.action_id = journal_push ("replace instrument on " .. args.track_id, {
                check = function ()
                    local r = route_by_key (key)
                    if not r then return false, "the track is gone" end
                    local now = r:the_instrument ()
                    if now:isnil () or not now:sameinstance (proc) then
                        return false, "the instrument on that track has been changed since"
                    end
                    return true
                end,
                inverse = function ()
                    local r = route_by_key (key)
                    local rv2 = r:replace_processor (proc, cur, nil)
                    if rv2 ~= 0 then fail ("could not put the previous instrument back (" .. tostring (rv2) .. ")") end
                end,
            })
        end
        return out
    end
    route:add_processor_by_index (proc, 0, nil, true)
    local out = { track_id = args.track_id, instrument = true, added = true,
                  instrument_id = uri, undoable = want_journal }
    if want_journal then
        out.action_id = journal_push ("add instrument on " .. args.track_id, {
            check = function ()
                local r = route_by_key (key)
                if not r then return false, "the track is gone" end
                local now = r:the_instrument ()
                if now:isnil () or not now:sameinstance (proc) then
                    return false, "the instrument on that track has been changed since"
                end
                return true
            end,
            inverse = function ()
                local r = route_by_key (key)
                local rv2 = r:remove_processor (proc, nil, true)
                if rv2 ~= 0 then fail ("could not remove the instrument (" .. tostring (rv2) .. ")") end
            end,
        })
    end
    return out
end

function handlers.fix_silent_instruments (args)
    -- Same undo story as add_instrument: journaled only on args.journal == true.
    local want_journal = (args.journal == true)
    local fixed, undo_steps = {}, {}
    for r in Session:get_routes ():iter () do
        local tr = r:to_track ()
        if not tr:isnil () and not tr:to_midi_track ():isnil () then
            local silent, cur = instrument_is_silent_fluid (r)
            local key = object_key (r)
            if cur:isnil () then
                local proc, uri = new_audible_proc ()
                if proc then
                    r:add_processor_by_index (proc, 0, nil, true)
                    fixed[#fixed + 1] = { track_id = r:name (), action = "added", instrument_id = uri }
                    undo_steps[#undo_steps + 1] = { key = key, proc = proc }
                end
            elseif silent then
                local proc, uri = new_audible_proc ()
                if proc and r:replace_processor (cur, proc, nil) == 0 then
                    fixed[#fixed + 1] = { track_id = r:name (), action = "replaced", instrument_id = uri }
                    undo_steps[#undo_steps + 1] = { key = key, proc = proc, old = cur }
                end
            end
        end
    end
    local out = { ok = true, fixed = fixed, count = #fixed, undoable = want_journal }
    if want_journal then
        out.action_id = journal_push ("fix silent instruments (" .. #fixed .. ")", {
            check = function ()
                for _, s in ipairs (undo_steps) do
                    local r = route_by_key (s.key)
                    local now = r and r:the_instrument ()
                    if not r or now:isnil () or not now:sameinstance (s.proc) then
                        return false, "an instrument Stem added has been changed since"
                    end
                end
                return true
            end,
            inverse = function ()
                for i = #undo_steps, 1, -1 do
                    local s = undo_steps[i]
                    local r = route_by_key (s.key)
                    if s.old then r:replace_processor (s.proc, s.old, nil)
                    else r:remove_processor (s.proc, nil, true) end
                end
            end,
        })
    end
    return out
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
    if not track or track:isnil () then fail ("no such track") end
    local mt = track:to_midi_track ()
    if mt:isnil () then fail ("not a midi track") end
    local repaired, undo_steps = 0, {}
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
            local samples = samples_for_beats (max_beat)
            if region:length ():samples () < samples then
                -- was a bare set_length: invisible to Ardour's undo. Now a
                -- StatefulDiffCommand in a named command, like a GUI trim.
                local old = extend_region (region, samples, "Stem: repair MIDI region")
                undo_steps[#undo_steps + 1] = { region = region, old = old, new = samples }
                repaired = repaired + 1
            end
        end
    end
    local out = { ok = true, repaired = repaired, undoable = (args.journal == true) }
    if args.journal == true then
        out.action_id = journal_push ("repair MIDI regions (" .. repaired .. ")", {
            check = function ()
                for _, s in ipairs (undo_steps) do
                    if s.region:length ():samples () ~= s.new then
                        return false, "a repaired region has been resized since"
                    end
                end
                return true
            end,
            inverse = function ()
                for i = #undo_steps, 1, -1 do
                    extend_region (undo_steps[i].region, undo_steps[i].old, "Stem: undo repair MIDI region")
                end
            end,
        })
    end
    return out
end

-- Find or create the track's MIDI region, growing it to span beats_needed.
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
--
-- UNDO: each route here is its own reversible command, committed before the
-- note diff (commands cannot nest). add_region(..., true) opens and commits
-- "create region" itself (9.8 midi_time_axis.cc:1677-1726); growing an
-- existing region is extend_region's StatefulDiffCommand. The returned info
-- says what was done so the caller's journal entry can reverse it.
local function get_or_make_region (route, mt, beats_needed)
    local required_samples = samples_for_beats (beats_needed)
    local info = { created = false, old_len = nil, new_len = nil }

    local region, mr = first_midi_region (mt)
    if region then
        if region:length ():samples () < required_samples then
            info.old_len = extend_region (region, required_samples, "Stem: extend MIDI region")
            info.new_len = required_samples
        end
        return mr, nil, info, region
    end

    if caps.fork_ensure_midi_region then
        local ok_region, made = pcall (function ()
            return ARDOUR.LuaAPI.ensure_midi_region (Session, mt, beats_needed + 1)
        end)
        if ok_region and made and not made:isnil () then
            info.created = true
            local r2 = first_midi_region (mt)
            return made, nil, info, r2 or made
        end
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

    local r3, mr3 = first_midi_region (mt)
    if r3 then
        info.created = true
        return mr3, nil, info, r3
    end
    return nil, "add_region did not produce a MIDI region"
end

-- The journal entry shared by insert_midi_notes and replace_midi_notes.
-- added   = note values Stem put in (removed again on undo)
-- removed = the model's own NotePtrs Stem took out (re-added on undo, the
--           same objects, which is what NoteDiffCommand::undo itself does)
-- info    = what get_or_make_region did to the region (created / grown)
local function journal_note_edit (label, mt, region, mr, added, removed, info)
    return journal_push (label, {
        check = function ()
            if info.created or info.old_len then
                if not region_still_on (mt, region) then
                    return false, "the MIDI region Stem wrote into is no longer on the track"
                end
            end
            if #added > 0 then
                local mm = region_model (mr)
                if not mm then return false, "the MIDI region has no model any more" end
                local _, missing = find_model_notes (mm, added)
                if missing then
                    return false, string.format ("%d of the %d notes Stem wrote have been "
                        .. "changed or deleted since; not undoing over your edit", missing, #added)
                end
            end
            if info.old_len and region:length ():samples () ~= info.new_len then
                return false, "the region has been resized since Stem grew it"
            end
            if info.created and #removed == 0 then
                -- the region will be removed: it must hold nothing but Stem's notes
                if region_note_count (mr) ~= #added then
                    return false, "the region Stem created now holds notes Stem did not write; "
                        .. "not deleting it"
                end
            end
            return true
        end,
        inverse = function ()
            if #added > 0 or #removed > 0 then
                local mm = region_model (mr)
                local found = find_model_notes (mm, added)
                local cmd = mm:new_note_diff_command ("Stem: undo " .. label)
                for _, n in ipairs (found) do cmd:remove (n) end
                for _, n in ipairs (removed) do cmd:add (n) end
                apply_note_diff (mm, cmd)
            end
            if info.created and #removed == 0 then
                remove_region_reversibly (mt, region, "Stem: undo create MIDI region")
            elseif info.old_len then
                extend_region (region, info.old_len, "Stem: undo extend MIDI region")
            end
        end,
    })
end

-- If the note diff fails AFTER get_or_make_region created or grew the region,
-- put the region back before re-raising: a failed call must leave no partial
-- change and no journal entry (the add_marker bug class, one level up).
local function with_region_rollback (mt, region, info, fn)
    local ok, err = pcall (fn)
    if ok then return end
    if info and info.created then
        pcall (function () remove_region_reversibly (mt, region, "Stem: roll back create MIDI region") end)
    elseif info and info.old_len then
        pcall (function () extend_region (region, info.old_len, "Stem: roll back extend MIDI region") end)
    end
    error (err, 0)
end

local function midi_track_or_fail (track_id)
    local track = find_track (track_id)
    if not track or track:isnil () then fail ("no such track: " .. tostring (track_id)) end
    local mt = track:to_midi_track ()
    if mt:isnil () then fail ("not a midi track: " .. tostring (track_id)) end
    return mt
end

local function max_end_beat (values, floor_beats)
    local m = floor_beats or 0
    for _, v in ipairs (values) do
        local e = (v.start + v.length) / TPB
        if e > m then m = e end
    end
    return m
end

function handlers.insert_midi_notes (args)
    -- validate everything first: nothing may change before the last check
    local mt     = midi_track_or_fail (args.track_id)
    local values = validate_notes (args.notes, "insert_midi_notes")
    assert_no_open_command ()
    if #values == 0 then
        return { ok = true, inserted = 0, undoable = true,
                 action_id = journal_noop ("insert 0 notes") }
    end

    local route = find_route (args.track_id)
    local mr, why, info, region = get_or_make_region (route, mt, max_end_beat (values, 4))
    if not mr or mr:isnil () then
        fail (why or "could not create midi region")
    end
    local mm = region_model (mr)
    with_region_rollback (mt, region, info, function ()
        if not mm then fail ("midi region has no model") end

        -- MidiModel::new_note_diff_command is bound in both (luabindings.cc:1811 / :1855).
        local cmd = mm:new_note_diff_command ("Stem: insert notes")
        for _, v in ipairs (values) do cmd:add (new_note (v)) end

        -- apply_diff_command_as_commit opens and commits its own undo record
        -- (midi_model.cc:104 in 9.8), so the handler opens none around it — a
        -- command opened here would make Ardour abort both (history_owner.cc:70).
        -- Its first parameter changed C++ type between 8 and 9 (Session* ->
        -- PBD::HistoryOwner*); Session is-a HistoryOwner in 9 (luabindings.cc:3171),
        -- so passing Session is right for both.
        apply_note_diff (mm, cmd)
    end)

    local action_id = journal_note_edit ("insert notes", mt, region, mr, values, {}, info)
    return { ok = true, inserted = #values, action_id = action_id, undoable = true }
end

-- replace_midi_notes(track_id, notes, start_beat = 0.0, end_beat = nil)
--
-- Contract (the Python side builds against exactly this):
--   * existing notes whose START lies in [start_beat, end_beat) are removed;
--     end_beat absent (nil) = to the end of the region;
--   * `notes` go in at their own ABSOLUTE start_beat (the frame get_midi_notes
--     reports), even outside the range;
--   * note dict = insert_midi_notes' shape {pitch, start_beat, length_beats,
--     velocity, channel};
--   * the removal and the additions are ONE NoteDiffCommand, applied as one
--     reversible command (the remove+add-in-one-diff shape of Ardour's own
--     share/scripts/reverse_midi.lua:53-67, identical in 8.12 and 9.8), and
--     ONE journal entry, so one undo restores the exact prior notes.
--   * returns an action id.
-- Growing (or, on an empty track, creating) the region is a separate Ardour
-- command, committed first because commands cannot nest; the Stem undo step
-- reverses it together with the notes.
function handlers.replace_midi_notes (args)
    local mt     = midi_track_or_fail (args.track_id)
    local values = validate_notes (args.notes, "replace_midi_notes")
    local start_beat = (args.start_beat == nil) and 0.0 or tonumber (args.start_beat)
    if not finite (start_beat) or start_beat < 0 then
        fail ("replace_midi_notes: bad start_beat " .. tostring (args.start_beat))
    end
    local end_beat = nil
    if args.end_beat ~= nil then
        end_beat = tonumber (args.end_beat)
        if not finite (end_beat) or end_beat < start_beat then
            fail ("replace_midi_notes: bad end_beat " .. tostring (args.end_beat)
                  .. " (must be >= start_beat)")
        end
    end
    local lo = beats_to_ticks (start_beat)
    local hi = end_beat and beats_to_ticks (end_beat) or nil
    assert_no_open_command ()

    local region, mr = first_midi_region (mt)
    if not region and #values == 0 then
        -- nothing there and nothing to write: a successful no-op
        return { ok = true, removed = 0, added = 0, undoable = true,
                 action_id = journal_noop ("replace notes (nothing to replace)") }
    end

    local info = { created = false }
    if not region or region:length ():samples () < samples_for_beats (max_end_beat (values, 0)) then
        local route = find_route (args.track_id)
        local why
        mr, why, info, region = get_or_make_region (route, mt, max_end_beat (values, 4))
        if not mr or mr:isnil () then fail (why or "could not create midi region") end
    end
    local mm = region_model (mr)
    local removed = {}
    with_region_rollback (mt, region, info, function ()
        if not mm then fail ("midi region has no model") end

        for note in ARDOUR.LuaAPI.note_list (mm):iter () do
            local t = note:time ():to_ticks ()
            if t >= lo and (hi == nil or t < hi) then removed[#removed + 1] = note end
        end

        if #removed > 0 or #values > 0 then
            local cmd = mm:new_note_diff_command ("Stem: replace notes")
            for _, n in ipairs (removed) do cmd:remove (n) end
            for _, v in ipairs (values) do cmd:add (new_note (v)) end
            apply_note_diff (mm, cmd)
        end
    end)

    local action_id = journal_note_edit ("replace notes", mt, region, mr, values, removed, info)
    return { ok = true, removed = #removed, added = #values,
             action_id = action_id, undoable = true }
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
                        -- was missing: a read-modify-write through
                        -- replace_midi_notes reset every note to channel 0.
                        -- Evoral::Note::channel is bound (luabindings.cc:1015 in 9.8).
                        channel      = note:channel (),
                    }
                end
            end
        end
    end
    return { notes = out }
end

function handlers.import_audio (args)
    if type (args.file_path) ~= "string" or args.file_path == "" then
        fail ("import_audio: file_path is required")
    end
    local sr  = sample_rate ()
    local pos = math.floor ((tonumber (args.position_seconds) or 0) * sr)

    local before = {}
    for r in Session:get_routes ():iter () do before[object_key (r)] = true end
    local function created_route ()
        for r in Session:get_routes ():iter () do
            if not before[object_key (r)] then return r end
        end
        return nil
    end

    -- Journal: the import lands on a NEW track (ImportAsTrack; the fork helper
    -- does the same). Ardour cannot undo track creation, so the inverse is
    -- removing that track again.
    local function journal_import (route)
        local key, name = object_key (route), route:name ()
        return journal_push ("import " .. name, {
            check = function () return true end,
            inverse = function ()
                local r = route_by_key (key)
                if r then Session:remove_route (r) end
            end,
        })
    end

    -- 1) fork helper, if this build has it.
    if caps.fork_import_audio_file then
        local ok, track_id = pcall (function ()
            return ARDOUR.LuaAPI.import_audio_file (Session, args.file_path, pos)
        end)
        if ok and track_id and track_id ~= "" then
            local route = created_route ()
            if route then
                return { ok = true, imported = args.file_path, track_id = route:name (),
                         action_id = journal_import (route), undoable = true }
            end
            -- the helper reported success but no new track appeared: it
            -- imported onto an existing track, which Stem cannot reverse
            -- exactly. Still one journal entry (Python records one action);
            -- its undo says so instead of guessing.
            local tid = (type (track_id) == "string") and track_id or ""
            local action_id = journal_push ("import onto an existing track", {
                check = function ()
                    return false, "that import went onto an existing track; Stem cannot "
                        .. "remove just the imported audio"
                end,
                inverse = function () end,
            })
            return { ok = true, imported = args.file_path, track_id = tid,
                     action_id = action_id, undoable = false }
        end
    end

    -- 2) stock Ardour: Editor:do_import, exactly as the bundled
    --    share/scripts/s_import_files.lua does (identical in 8.12 and 9.8).
    --    Binding: gtk2_ardour/luainstance.cc:945 / :1017 (addRefFunction, because
    --    the timepos_t parameter is by reference). Signature:
    --    public_editor.h:300 / :228.
    if not Editor then
        fail ("no audio import path: this build has neither "
              .. "ARDOUR.LuaAPI.import_audio_file nor an Editor")
    end

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
        fail ("do_import failed: " .. tostring (err))
    end

    local route = created_route ()
    -- UNVERIFIED: whether do_import has finished by the time it returns (Ardour
    -- runs the import on a worker thread behind a progress dialog). If no
    -- track exists yet the import may simply still be running. ArdourBridge
    -- treats an empty track_id as a failure and records no action, so this
    -- path journals nothing either (the two sequences stay aligned).
    if not route then
        return { ok = true, imported = args.file_path, track_id = "",
                 pending = true, undoable = false }
    end
    return {
        ok        = true,
        imported  = args.file_path,
        track_id  = route:name (),
        pending   = false,
        action_id = journal_import (route),
        undoable  = true,
    }
end

function handlers.set_tempo (args)
    local bpm = tonumber (args.bpm)
    if not finite (bpm) or bpm <= 0 then fail ("bad bpm: " .. tostring (args.bpm)) end

    -- The inverse needs the tempo being replaced. Read it BEFORE writing.
    -- Exact for a constant tempo at the session start (what Stem writes); a
    -- ramped first tempo would come back unramped — the end tempo of a ramp
    -- is not readable from Lua (Temporal::Tempo binds note_type,
    -- note_types_per_minute, quarter_notes_per_minute only; 9.8
    -- luabindings.cc:816-826).
    local prior = tempo_bpm_read ()
    if not prior then
        -- without the old value there is no exact inverse, and a tempo change
        -- Stem cannot take back is exactly what this work exists to prevent
        fail ("cannot read the current tempo on this build, so a tempo change "
              .. "could not be undone; not changing it")
    end
    assert_no_open_command ()

    local function write_fork (value)
        local ok, rv = pcall (function ()
            return ARDOUR.LuaAPI.set_session_tempo (Session, value)
        end)
        return ok and rv
    end

    local function write_stock (value)
        -- write_copy MUST be matched by update() or abort_update() — the
        -- snippet share/scripts/s_tempo_map.lua:11-20 says so in its own comment.
        local tm = Temporal.TempoMap.write_copy ()
        local ok, err = pcall (function ()
            tm:set_tempo (Temporal.Tempo (value, value, 4), Temporal.timepos_t (0))
        end)
        if not ok then
            pcall (function () Temporal.TempoMap.abort_update () end)
            fail ("set_tempo failed: " .. tostring (err))
        end
        Temporal.TempoMap.update (tm)
    end

    local via
    -- 1) fork helper, if present. It commits a real Temporal::TempoCommand
    --    (the pattern of 9.8 session.cc:7826 / tempo.cc:5466), so this change
    --    IS on Ardour's undo stack, as its own command — nothing is opened here.
    if caps.fork_set_session_tempo and write_fork (bpm) then
        via = "fork"
    else
        -- 2) stock Ardour: write_copy / set_tempo / update. Bindings:
        --    TempoMap.write_copy / update / abort_update are static functions
        --    (luabindings.cc:839-841 in 8.12, :878-880 in 9.8); TempoMap:set_tempo
        --    takes (Tempo const&, timepos_t const&) (:842 / :881); the Tempo ctor is
        --    (double npm, double end_npm, int note_type) (:778 / :817).
        --
        --    This path CANNOT reach Ardour's undo history: that needs a
        --    Temporal::TempoCommand built from TempoMap::get_state() before and
        --    after, and neither TempoCommand nor TempoMap:get_state is bound to
        --    Lua in 8.12 or 9.8. (The begin/commit this handler used to put
        --    around update() recorded nothing: libtemporal has no access to
        --    session history, so abort_empty_reversible_command dropped it
        --    every time.) The bridge journal is the undo for this path.
        if not caps.tempo_write_copy then
            fail ("no tempo API available in this Ardour build")
        end
        write_stock (bpm)
        via = "tempomap"
    end

    local set_to = tempo_bpm ()
    local action_id = journal_push ("set tempo " .. prior .. " -> " .. bpm, {
        check = function ()
            local now = tempo_bpm ()
            if near (now, prior) then return true end   -- already back
            if not (near (now, bpm) or near (now, set_to)) then
                return false, string.format ("the tempo has been changed since (now %.3f, Stem set %.3f)", now, bpm)
            end
            return true
        end,
        inverse = function ()
            if near (tempo_bpm (), prior) then return end
            if via == "fork" and write_fork (prior) then return end
            write_stock (prior)
        end,
    })
    return { ok = true, bpm = bpm, via = via, previous_bpm = prior,
             action_id = action_id, undoable = true,
             on_ardour_undo_stack = (via == "fork") }
end

-- Controllable writes are never on Ardour's undo stack (see UNDO above), so
-- both of these journal the prior value and restore it on undo. set_value can
-- be queued to the RT thread (AutomationControl::check_rt, 9.8
-- automation_control.cc:407-416), so the read-back right after the write may
-- still be the old value; the undo check accepts either the value asked for or
-- the value read back, and refuses only a value neither Stem nor the prior
-- state explains (the user moved the control since).
local function journal_control (label, get_ctrl, prior, wanted, readback)
    return journal_push (label, {
        check = function ()
            local c = get_ctrl ()
            if not c then return false, "that track is gone" end
            local now = c:get_value ()
            if near (now, prior) or near (now, wanted) or near (now, readback) then return true end
            return false, "it has been changed since Stem set it; not undoing over your change"
        end,
        inverse = function ()
            get_ctrl ():set_value (prior, PBD.GroupControlDisposition.NoGroup)
        end,
    })
end

function handlers.set_track_gain (args)
    local track = find_track (args.track_id)
    if not track or track:isnil () then fail ("no such track: " .. tostring (args.track_id)) end
    local db = tonumber (args.gain_db)
    if not finite (db) then fail ("bad gain_db: " .. tostring (args.gain_db)) end
    local key   = object_key (track)
    local ctrl  = track:gain_control ()
    local prior = ctrl:get_value ()
    local want  = ARDOUR.DSP.dB_to_coefficient (db)
    ctrl:set_value (want, PBD.GroupControlDisposition.NoGroup)
    local action_id = journal_control ("gain on " .. args.track_id, function ()
        local r = route_by_key (key)
        return r and r:gain_control () or nil
    end, prior, want, ctrl:get_value ())
    return { ok = true, action_id = action_id, undoable = true,
             previous_gain_db = ARDOUR.DSP.accurate_coefficient_to_dB (prior) }
end

function handlers.set_track_mute (args)
    local route = find_route (args.track_id)
    if not route or route:isnil () then fail ("no such track or bus: " .. tostring (args.track_id)) end
    local key   = object_key (route)
    local ctrl  = route:mute_control ()
    local prior = ctrl:get_value ()
    local want  = args.muted and 1 or 0
    ctrl:set_value (want, PBD.GroupControlDisposition.NoGroup)
    local action_id = journal_control ((args.muted and "mute " or "unmute ") .. args.track_id, function ()
        local r = route_by_key (key)
        return r and r:mute_control () or nil
    end, prior, want, ctrl:get_value ())
    return { ok = true, action_id = action_id, undoable = true, previously_muted = (prior ~= 0) }
end

function handlers.add_marker (args)
    -- FIX: Session:locations():add_mark(...) DOES NOT EXIST. The Locations class
    -- is bound in both versions (luabindings.cc:1251 in 8.12, :1296 in 9.8) and
    -- exposes list/mark_at/add_range/remove/... — but no add_mark. Calling it
    -- raised "attempt to call a nil value (method 'add_mark')" AFTER
    -- Session:begin_reversible_command had already opened a command. Ardour
    -- then ABORTS the next begin_reversible_command together with the dangling
    -- one (session_state.cc:3382 in 8.12, history_owner.cc:68 in 9.8), so the
    -- user's next real edit silently never reached the undo history.
    --
    -- The supported route is the editor's own marker action, which opens and
    -- commits its own undo record ("add marker", a MementoCommand on Locations —
    -- 9.8 editor_ops.cc:1972 add_location_mark_with_flag): Editor:mouse_add_new_marker
    -- (timepos_t, flags, cue_id), bound at gtk2_ardour/luainstance.cc:1040 (8.12)
    -- / :1007 (9.8). This is exactly what the bundled share/scripts/add_cdmarker.lua:5
    -- does. Nothing is opened around it (commands cannot nest).
    if not Editor then fail ("no Editor: cannot add a marker") end
    local secs = tonumber (args.position_seconds or 0)
    if not finite (secs) or secs < 0 then fail ("bad position_seconds: " .. tostring (args.position_seconds)) end
    local sr  = sample_rate ()
    local pos = Temporal.timepos_t (math.floor (secs * sr))

    assert_no_open_command ()
    local before = {}
    for l in Session:locations ():list ():iter () do before[object_key (l)] = true end

    local ok, err = pcall (function ()
        Editor:mouse_add_new_marker (pos, ARDOUR.LocationFlags.IsMark, 0)
    end)
    if not ok then
        fail ("add marker failed: " .. tostring (err))
    end

    local loc = nil
    for l in Session:locations ():list ():iter () do
        if not before[object_key (l)] and l:is_mark () then loc = l break end
    end
    if not loc then
        -- add_location_mark_with_flag returns silently when a marker already
        -- sits at that position (editor_ops.cc:1978 in 9.8). Say so — and
        -- journal nothing, because nothing happened.
        fail ("no marker was added: Ardour already has a marker at "
              .. string.format ("%.3f", secs) .. " s")
    end

    local renamed = false
    if args.name and args.name ~= "" then
        renamed = pcall (function () loc:set_name (args.name) end)
    end

    -- Journal: the inverse removes exactly this Location (Locations:remove is
    -- bound, luabindings.cc:1307 in 9.8). Bridge-side because the matching
    -- MementoCommand cannot be built from Lua (Locations:get_state is unbound).
    local key = object_key (loc)
    local action_id = journal_push ("add marker " .. (args.name or ""), {
        check = function () return true end,
        inverse = function ()
            for l in Session:locations ():list ():iter () do
                if object_key (l) == key then Session:locations ():remove (l) return end
            end
        end,
    })
    return { ok = true, named = renamed, name = args.name or "",
             action_id = action_id, undoable = true }
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
    -- Undo exactly the most recent Stem action and nothing else, by applying
    -- the inverse its journal entry recorded. NEVER Editor:undo(1): Lua cannot
    -- see what is on top of Ardour's stack (see UNDO above), and blindly
    -- popping it undid the user's own edits.
    --
    -- Every outcome is returned as a VALUE, never raised. ArdourBridge pops its
    -- own action id before calling this (and calls it n times for
    -- undo(action_id)); a raised error would break its loop half-way and
    -- misalign the two sequences. A refused entry is dropped for the same
    -- reason, and the reply says so.
    local n = #J.entries
    if n == 0 then
        return { ok = false, undone = false,
                 error = "nothing Stem did is left to undo in this Ardour session "
                      .. "(Ardour's own undo history was not touched)" }
    end
    local e = J.entries[n]
    if args.action_id ~= nil and args.action_id ~= e.id then
        return { ok = false, undone = false, top = e.id,
                 error = "action " .. tostring (args.action_id) .. " is not the most "
                      .. "recent Stem action (" .. e.id .. ", " .. e.label .. ")" }
    end
    J.entries[n] = nil

    if e.session ~= Session:name () then
        return { ok = false, undone = false, action_id = e.id, label = e.label, dropped = true,
                 error = "that action was made in another session (" .. tostring (e.session) .. ")" }
    end
    local ok, allowed, why = pcall (e.check)
    if not ok then allowed, why = false, tostring (allowed) end
    if not allowed then
        return { ok = false, undone = false, action_id = e.id, label = e.label, dropped = true,
                 error = "cannot undo '" .. e.label .. "': " .. tostring (why) }
    end
    local ok2, err = pcall (e.inverse)
    if not ok2 then
        -- never leave a command open behind a failed inverse
        pcall (function () Session:abort_reversible_command () end)
        return { ok = false, undone = false, action_id = e.id, label = e.label, dropped = true,
                 error = "undo of '" .. e.label .. "' failed: " .. tostring (err) }
    end
    return { ok = true, undone = true, action_id = e.id, label = e.label,
             noop = e.noop or false, remaining = #J.entries }
end

-- Read-only: what Stem can still undo, newest last.
function handlers.get_undo_journal (args)
    local out = {}
    for _, e in ipairs (J.entries) do
        out[#out + 1] = { action_id = e.id, label = e.label, noop = e.noop or false }
    end
    return { entries = out, count = #out }
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
