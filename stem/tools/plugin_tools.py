"""Plugin catalog + load/param tools (M2.3)."""
from typing import Optional

from pydantic import BaseModel, Field

from .core import registry


class ListPlugins(BaseModel):
    kind: Optional[str] = Field(
        default=None,
        description="Filter: 'instrument', 'effect', or omit for all")
    query: Optional[str] = Field(
        default=None,
        description="Optional case-insensitive substring filter on name/"
                    "creator/category")


@registry.register(
    "list_plugins",
    "List plugins available in the DAW (instruments and/or effects). "
    "Use before load_plugin. For MIDI instruments only, list_instruments "
    "also works.",
    ListPlugins)
def list_plugins(args, ctx):
    plugins = ctx.bridge.list_plugins(args.kind)
    if args.query:
        q = args.query.casefold()
        plugins = [
            p for p in plugins
            if q in " ".join(str(p.get(k, "")) for k in
                             ("name", "creator", "category", "id", "type")).casefold()
        ]
    return {"plugins": plugins, "count": len(plugins)}


class LoadPlugin(BaseModel):
    track_id: str
    plugin_id: str = Field(description="Plugin unique id from list_plugins")
    position: Optional[int] = Field(
        default=None,
        description="Insert index; omit to append at end of chain")


@registry.register(
    "load_plugin",
    "Load a plugin (instrument or effect) onto a track. Undoable. "
    "Pick plugin_id via list_plugins first.",
    LoadPlugin, mutates=True)
def load_plugin(args, ctx):
    action_id = ctx.bridge.load_plugin(
        args.track_id, args.plugin_id, args.position)
    return {"action_id": action_id, "track_id": args.track_id,
            "plugin_id": args.plugin_id}


class GetPluginParams(BaseModel):
    track_id: str
    plugin_index: int = Field(
        default=0, ge=0,
        description="0-based index in the track's processor/plugin chain")


@registry.register(
    "get_plugin_params",
    "Read the parameters of a plugin already on a track (by chain index).",
    GetPluginParams)
def get_plugin_params(args, ctx):
    return ctx.bridge.get_plugin_params(args.track_id, args.plugin_index)


class SetPluginParam(BaseModel):
    track_id: str
    param_id: str = Field(
        description="Parameter id from get_plugin_params (or its name)")
    value: float
    plugin_index: int = Field(default=0, ge=0)


@registry.register(
    "set_plugin_param",
    "Set one plugin parameter on a track. Undoable. "
    "Call get_plugin_params first to learn ids and ranges.",
    SetPluginParam, mutates=True)
def set_plugin_param(args, ctx):
    action_id = ctx.bridge.set_plugin_param(
        args.track_id, args.param_id, args.value, args.plugin_index)
    return {"action_id": action_id, "track_id": args.track_id,
            "plugin_index": args.plugin_index,
            "param_id": args.param_id, "value": args.value}
