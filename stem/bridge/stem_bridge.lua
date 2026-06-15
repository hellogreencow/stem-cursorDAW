ardour {
    ["type"]    = "EditorHook",
    name        = "Stem Agent Bridge",
    license     = "GPL",
    author      = "Stem",
    description = [[Loader for the Stem AI agent bridge. Hot-reloads
~/.stem/bridge_impl.lua on Ardour's deci-second UI timer, so the bridge
implementation can be updated without touching Ardour. Register once in
Edit > Lua Scripts > Script Manager > Action Hooks.]]
}

function signals ()
    return LuaSignal.Set():add ({[LuaSignal.LuaTimerDS] = true})
end

function factory ()
return function (signal, ref, ...)
    local path = os.getenv("HOME") .. "/.stem/bridge_impl.lua"
    local chunk = loadfile (path)
    if chunk then
        local ok, impl = pcall (chunk)
        if ok and type (impl) == "function" then
            local ok2, err = pcall (impl)
            if not ok2 then
                local f = io.open (os.getenv("HOME") .. "/.stem/bridge_error.log", "w")
                if f then f:write (tostring (err)) f:close () end
            end
        end
    end
end
end
