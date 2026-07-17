"""H5: parametrize all JSON golden transcripts."""
import pytest

from stem.services.memory import update_memory
from tests.harness.golden_transcripts import (
    TRANSCRIPT_DIR,
    assert_transcript,
    iter_transcripts,
    run_transcript,
)


CASES = iter_transcripts()


@pytest.mark.parametrize("path,spec", CASES, ids=[p.stem for p, _ in CASES])
def test_golden_transcript(path, spec):
    if spec.get("name") == "memory_defaults":
        update_memory("golden-mem", preferred_tempo=95.0, preferred_key="F")

    result = run_transcript(spec)
    assert_transcript(spec, result)

    if spec.get("name") == "selection_aware_propose":
        bridge = result["bridge"]
        assert all(len(ns) == 0 for ns in bridge.notes.values())
        pending = [p for p in bridge.list_proposals() if p["status"] == "pending"]
        assert pending


def test_transcript_fixtures_exist():
    assert TRANSCRIPT_DIR.exists()
    assert list(TRANSCRIPT_DIR.glob("*.json")), "no golden transcripts found"
