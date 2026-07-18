"""Sample index MVP: build, search, import via tools."""
from pathlib import Path

import stem.tools.sample_tools  # noqa: F401
from stem.services.sample_index import SampleIndex
from stem.tools.core import registry
from stem.paths import stem_home


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "samples_lib"


def run(ctx, tool, **kwargs):
    return registry.execute(tool, kwargs, ctx)


def test_build_and_search_ranks_snare(mock_ctx):
    index = SampleIndex(stem_home() / "sample_index.json")
    n = index.build(FIXTURES)
    assert n == 5

    hits = index.search("dark snare", limit=3)
    assert hits
    assert "snare" in hits[0]["name"]
    assert hits[0]["duration_seconds"] is not None


def test_search_samples_tool_and_import(mock_ctx):
    rebuilt = run(mock_ctx, "rebuild_sample_index", root=str(FIXTURES))
    assert rebuilt["indexed"] == 5

    found = run(mock_ctx, "search_samples", query="808 kick")
    assert found["count"] >= 1
    top = found["results"][0]
    assert "808" in top["name"] or "kick" in top["name"]

    imported = run(mock_ctx, "import_sample", sample_id=top["id"],
                   position_seconds=0.0)
    assert "action_id" in imported
    assert Path(imported["file"]).exists()
    overview = run(mock_ctx, "get_session_overview")
    assert any(t["kind"] == "audio" for t in overview["tracks"])


def test_search_samples_empty_index_errors_clearly(mock_ctx):
    r = run(mock_ctx, "search_samples", query="snare")
    assert r["count"] == 0
    assert "index empty" in r.get("error", "")


def test_import_sample_by_path(mock_ctx):
    path = FIXTURES / "warm_pad_dm.wav"
    r = run(mock_ctx, "import_sample", path=str(path))
    assert "action_id" in r
