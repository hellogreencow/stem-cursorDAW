-- tests/lua/run_script.lua
--
-- Drive stem/bridge/bridge_impl.lua through a SEQUENCE of steps in one Lua
-- state, the way Ardour's LuaTimerDS hook does over time: bridge requests,
-- the user's own GUI edits in between (USER.* in mock_ardour.lua), and chunk
-- reloads (the loader re-runs the chunk every 10 s). Undo is a property of a
-- sequence, so it cannot be tested one isolated call at a time.
--
--   lua5.3 run_script.lua <mock_ardour.lua> <bridge_impl.lua> <steps.lua>
--
-- steps.lua returns a list of:
--   { kind = "call",   method = "set_tempo", json = '{"bpm":90}' }
--   { kind = "user",   fn = "add_note", args = { track = "Chords", ... } }
--   { kind = "reload" }          -- re-run the chunk, as the loader does
--   { kind = "flush" }           -- deliver queued RT control writes
-- Writes $HOME/.stem/script_out.jsonl: one line per step,
--   {"step":i,"kind":..,"response":<the bridge's own bytes or null>,"state":{...}}

local mock_path   = assert(arg[1], "arg1: mock_ardour.lua")
local bridge_path = assert(arg[2], "arg2: bridge_impl.lua")
local steps_path  = assert(arg[3], "arg3: steps.lua")

dofile(mock_path)
local steps = dofile(steps_path)

local function load_impl()
  local chunk = assert(loadfile(bridge_path))
  local impl = chunk()
  assert(type(impl) == "function", "bridge_impl.lua must return a function")
  return impl
end
local impl = load_impl()

local home = assert(os.getenv("HOME"))
local req_path  = home .. "/.stem/request.json"
local resp_path = home .. "/.stem/response.json"
local out = assert(io.open(home .. "/.stem/script_out.jsonl", "w"))

for i, s in ipairs(steps) do
  local response = "null"
  if s.kind == "call" then
    os.remove(resp_path)
    local f = assert(io.open(req_path, "w"))
    f:write(string.format('{"id":"t%d","method":"%s","args":%s}', i, s.method, s.json or "{}"))
    f:close()
    impl()
    local r = io.open(resp_path, "r")
    if r then response = r:read("*a") r:close() end
  elseif s.kind == "user" then
    assert(USER[s.fn], "no USER edit named " .. tostring(s.fn))
    USER[s.fn](s.args or {})
  elseif s.kind == "reload" then
    impl = load_impl()
  elseif s.kind == "flush" then
    flush_rt_queue()
  else
    error("unknown step kind " .. tostring(s.kind))
  end
  out:write(string.format('{"step":%d,"kind":"%s","response":%s,"state":%s}\n',
                          i, s.kind, response, mock_state_json()))
end
out:close()

local c = assert(io.open(home .. "/.stem/calls.txt", "w"))
for _, name in ipairs(CALLS) do c:write(name, "\n") end
c:close()
