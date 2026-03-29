"""Orchestrate full repo indexing — one command to warm all caches."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from src.infrastructure.indexer.ctags_indexer import generate_ctags
from src.infrastructure.indexer.import_cache import build_import_index
from src.infrastructure.indexer.cochange_cache import build_cochange_matrix
from src.infrastructure.indexer.complexity_cache import build_complexity_index

BOLD = "\033[1m"
GREEN = "\033[32m"
CYAN = "\033[36m"
DIM = "\033[2m"
RESET = "\033[0m"

_SOURCE_EXTENSIONS = {
    ".py", ".pyi", ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp",
    ".java", ".kt", ".kts", ".go", ".rs",
    ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".gradle", ".cmake", ".bzl", ".bxl", ".star", ".groovy",
}

_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".mypy_cache",
    "venv", ".venv", "env", ".env", "dist", "build",
    ".stacker-cache", ".tox", ".pytest_cache",
}


def index_repo(root: str = ".") -> dict[str, float]:
    """
    Run all indexers and return timing info.
    Creates .stacker-cache/ with:
      - tags.json       (ctags symbol index)
      - imports.json    (import graph, hash-cached)
      - cochange.json   (co-change matrix from git log)
      - complexity.json (lizard/scc metrics, hash-cached)
    """
    print(f"\n{BOLD}Indexing repo...{RESET}")

    source_files = _find_source_files(root)
    print(f"  Found {len(source_files)} source files")

    timings: dict[str, float] = {}

    timings["ctags"] = _timed("ctags", lambda: generate_ctags(root))
    timings["imports"] = _timed("imports", lambda: build_import_index(source_files, root))
    timings["cochange"] = _timed("cochange", lambda: build_cochange_matrix(root=root))
    timings["complexity"] = _timed("complexity", lambda: build_complexity_index(source_files, root))

    total = sum(timings.values())
    print(f"\n  {GREEN}Done{RESET} in {total:.1f}s")
    print(f"  Cache: {CYAN}.stacker-cache/{RESET}")
    print()
    return timings


def _find_source_files(root: str) -> list[str]:
    """Walk the repo and find all source files."""
    files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            ext = Path(name).suffix.lower()
            if ext in _SOURCE_EXTENSIONS:
                files.append(os.path.join(dirpath, name))
    return files


def _timed(label: str, fn) -> float:
    start = time.monotonic()
    try:
        fn()
        elapsed = time.monotonic() - start
        print(f"  {GREEN}✓{RESET} {label:12s} {elapsed:.1f}s")
    except Exception as e:
        elapsed = time.monotonic() - start
        print(f"  ✗ {label:12s} failed: {e}")
    return elapsed
