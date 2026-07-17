"""Sample library tools: search + import onto the timeline."""
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from .core import registry
from ..services.sample_index import SampleIndex, default_index
from ..paths import stem_home


class SearchSamples(BaseModel):
    query: str = Field(description="Text query, e.g. 'dark snare' or '808 kick'")
    limit: int = Field(default=8, ge=1, le=50)


@registry.register(
    "search_samples",
    "Search the user's indexed sample library by name/path/tags. "
    "Run the indexer first if empty: python -m stem.index_samples ~/Samples. "
    "Returns ranked paths; use import_sample to place one on the timeline.",
    SearchSamples)
def search_samples(args, ctx):
    index = default_index()
    if not index.entries:
        return {
            "results": [],
            "count": 0,
            "error": "sample index empty — run: python -m stem.index_samples <folder>",
            "index_path": str(stem_home() / "sample_index.json"),
        }
    results = index.search(args.query, limit=args.limit)
    return {"results": results, "count": len(results),
            "index_path": str(index.index_path)}


class ImportSample(BaseModel):
    path: Optional[str] = Field(
        default=None,
        description="Absolute path to an audio file to import")
    sample_id: Optional[str] = Field(
        default=None,
        description="Id from search_samples results")
    position_seconds: float = Field(default=0.0, ge=0)
    track_id: str = Field(
        default="",
        description="Existing audio track id, or empty to create a new track")


@registry.register(
    "import_sample",
    "Import a sample file (or search hit by sample_id) onto an audio track. "
    "Undoable. Prefer search_samples first when the user describes a sound.",
    ImportSample, mutates=True)
def import_sample(args, ctx):
    path = args.path
    if args.sample_id and not path:
        entry = default_index().get(args.sample_id)
        if not entry:
            return {"error": f"unknown sample_id: {args.sample_id}"}
        path = entry.path
    if not path:
        return {"error": "provide path or sample_id"}
    p = Path(path).expanduser()
    if not p.exists():
        return {"error": f"file not found: {p}"}
    action_id = ctx.bridge.import_audio(args.track_id, str(p.resolve()),
                                        args.position_seconds)
    return {"action_id": action_id, "file": str(p.resolve())}


class RebuildSampleIndex(BaseModel):
    root: str = Field(description="Folder to scan for audio samples")


@registry.register(
    "rebuild_sample_index",
    "Scan a folder and rebuild the local sample index used by search_samples. "
    "Does not modify the Ardour session.",
    RebuildSampleIndex)
def rebuild_sample_index(args, ctx):
    index = SampleIndex()
    count = index.build(Path(args.root))
    return {"indexed": count, "index_path": str(index.index_path)}
