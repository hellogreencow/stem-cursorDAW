"""CLI: python -m stem.index_samples ~/Samples"""
from __future__ import annotations

import sys
from pathlib import Path

from .services.sample_index import SampleIndex


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print("Usage: python -m stem.index_samples <samples_folder> [index.json]")
        return 2
    root = Path(argv[0])
    index_path = Path(argv[1]) if len(argv) > 1 else None
    index = SampleIndex(index_path)
    n = index.build(root)
    print(f"Indexed {n} samples → {index.index_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
