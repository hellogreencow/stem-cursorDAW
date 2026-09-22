-- tests/lua/run_call.lua
--
-- Drive stem/bridge/bridge_impl.lua for one request, the way the LuaTimerDS
-- hook does: write ~/.stem/request.json, run one tick, read the response.
--
--   lua5.3 run_call.lua <mock_ardour.lua> <bridge_impl.lua> <method> <args-json-file>
--
-- HOME must point at a scratch directory containing .stem/ (pytest does this).
-- Writes  $HOME/.stem/response.json  (the bridge's own bytes, untouched — the
-- JSON-validity test parses exactly what Ardour would have handed Python) and
-- $HOME/.stem/calls.txt (one Ardour-side call per line, in order).

local mock_path   = assert(arg[1], "arg1: mock_ardour.lua")
local bridge_path = assert(arg[2], "arg2: bridge_impl.lua")
local method      = assert(arg[3], "arg3: method")
local args_file   = arg[4]

dofile(mock_path)

local args_json = "{}"
if args_file then
  local f = assert(io.open(args_file, "r"))
  args_json = f:read("*a")
  f:close()
end

local chunk = assert(loadfile(bridge_path))
local impl = chunk()
assert(type(impl) == "function",
       "bridge_impl.lua must return a function (the loader contract)")

local home = assert(os.getenv("HOME"))
local req  = home .. "/.stem/request.json"

local f = assert(io.open(req, "w"))
f:write(string.format('{"id":"t1","method":"%s","args":%s}', method, args_json))
f:close()

impl()   -- exactly one tick

local c = assert(io.open(home .. "/.stem/calls.txt", "w"))
for _, name in ipairs(CALLS) do c:write(name, "\n") end
c:close()
