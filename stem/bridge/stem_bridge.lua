-- Stem Agent Bridge — loader hook (stem_bridge.lua)
--
-- Drop in ~/Library/Preferences/Ardour<N>/scripts/ (macOS) or
-- ~/.config/ardour<N>/scripts/ (Linux), then register once in
-- Edit > Lua Scripts > Script Manager > Action Hooks.
--
-- Verified against Ardour 8.12 (tag 8.12, 10517bff) and 9.8 (tag 9.8, 22ed8656):
--   * EditorHook + LuaSignal.LuaTimerDS is the correct, non-blocking mechanism.
--     LuaTimerDS is declared in gtk2_ardour/luasignal_syms.h:89 (8.12) /
--     luasignal_syms.inc.h:91 (9.8) and emitted from
--     LuaInstance::every_point_one_seconds (luainstance.cc:1511 / 1576), which is
--     wired to Timers::rapid_connect (luainstance.cc:1481). It fires on the GUI
--     thread roughly every 100 ms. Nothing here sleeps or spins: the old
--     EditorAction + ARDOUR.LuaAPI.usleep shape would have frozen the GUI.
--   * The hook callback signature is (signal, ref, ...) — see the bundled
--     share/scripts/_hook_test.lua.
--
-- CHANGE vs. the previous loader: the previous version called loadfile() AND
-- re-executed the whole 700-line implementation chunk on EVERY tick (~10x/sec,
-- forever). That recompiled the file, rebuilt the JSON codec and rebuilt every
-- handler closure ten times a second on the GUI thread. Here the chunk is
-- compiled and run once, the resulting dispatch function is cached, and a
-- reload only happens when you ask for one (touch ~/.stem/reload) or every
-- STEM_RELOAD_SECONDS as a safety net.
--
-- DISPATCH is by name in bridge_impl.lua (handlers[req.method]), so a new
-- handler such as replace_midi_notes needs no change here. The undo journal
-- (bridge_impl.lua, "UNDO") is kept in the global STEM_UNDO_JOURNAL precisely
-- so that the reloads below do not wipe it: loadfile() runs the chunk in this
-- script's own Lua state, and its globals persist across re-runs. Only an
-- Ardour restart clears it (then undo says there is nothing left to undo).

ardour {
    ["type"]    = "EditorHook",
    name        = "Stem Agent Bridge",
    license     = "GPL",
    author      = "Stem",
    description = [[Loader for the Stem AI agent bridge. Runs ~/.stem/bridge_impl.lua
on Ardour's deci-second UI timer. Hot-reload by touching ~/.stem/reload.
Register once in Edit > Lua Scripts > Script Manager > Action Hooks.]]
}

function signals ()
    return LuaSignal.Set():add ({[LuaSignal.LuaTimerDS] = true})
end

function factory ()
    local HOME         = os.getenv ("HOME")
    local IMPL_PATH    = HOME .. "/.stem/bridge_impl.lua"
    local RELOAD_FLAG  = HOME .. "/.stem/reload"
    local ERROR_LOG    = HOME .. "/.stem/bridge_error.log"
    local RELOAD_SECS  = 10        -- safety-net reload interval

    local impl         = nil       -- cached dispatch function
    local last_load    = 0         -- os.time() of last successful load
    local last_error   = nil       -- de-dupe identical error spam

    local function log (msg)
        local text = tostring (msg)
        if text == last_error then return end
        last_error = text
        local f = io.open (ERROR_LOG, "w")
        if f then f:write (text .. "\n") f:close () end
    end

    local function load_impl ()
        local chunk, lerr = loadfile (IMPL_PATH)
        if not chunk then
            -- file missing or syntax error: keep the previous impl running
            if lerr then log ("load: " .. tostring (lerr)) end
            return false
        end
        local ok, produced = pcall (chunk)
        if not ok then
            log ("chunk: " .. tostring (produced))
            return false
        end
        if type (produced) ~= "function" then
            log ("chunk: bridge_impl.lua must return a function")
            return false
        end
        impl = produced
        last_load = os.time ()
        last_error = nil
        return true
    end

    return function (signal, ref, ...)
        local now = os.time ()

        -- explicit hot-reload: `touch ~/.stem/reload`
        local flag = io.open (RELOAD_FLAG, "r")
        if flag then
            flag:close ()
            os.remove (RELOAD_FLAG)
            load_impl ()
        elseif impl == nil or (now - last_load) >= RELOAD_SECS then
            load_impl ()
        end

        if impl == nil then return end

        local ok, err = pcall (impl)
        if not ok then log ("run: " .. tostring (err)) end
    end
end
