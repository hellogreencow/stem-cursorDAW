"""Gate: every registered tool must be mentioned under tests/.

D004 — cheap bare-tool detector. Stronger per-tool maps come later.
Also reminds authors to update CAPABILITY_MATRIX.md.
"""
from pathlib import Path

import stem.tools.ardour_tools  # noqa: F401
import stem.tools.generation_tools  # noqa: F401
import stem.tools.sample_tools  # noqa: F401
import stem.tools.plugin_tools  # noqa: F401
import stem.tools.proposal_tools  # noqa: F401
import stem.tools.memory_tools  # noqa: F401
import stem.tools.task_tools  # noqa: F401
from stem.tools.core import registry


ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
MATRIX = ROOT / "CAPABILITY_MATRIX.md"


def _test_corpus() -> str:
    parts = []
    for path in TESTS.rglob("*.py"):
        parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def test_every_registered_tool_is_mentioned_in_tests():
    names = sorted(registry._tools)
    assert names, "tool registry is empty — import side effects broken?"
    corpus = _test_corpus()
    missing = [n for n in names if n not in corpus]
    assert not missing, (
        "Tools with no mention under tests/ (add a real test, not just a "
        f"string): {missing}. Also update CAPABILITY_MATRIX.md."
    )


def test_capability_matrix_lists_every_tool():
    body = MATRIX.read_text(encoding="utf-8")
    missing = [n for n in sorted(registry._tools) if f"`{n}`" not in body]
    assert not missing, (
        f"CAPABILITY_MATRIX.md missing tools: {missing}"
    )
