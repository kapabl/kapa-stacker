"""Pre-compute and cache co-change matrix from git history."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

CACHE_FILE = ".stacker-cache/cochange.json"


def build_cochange_matrix(
    max_commits: int = 500,
    root: str = ".",
) -> dict[str, int]:
    """
    Analyze git log to find files that change together.
    Returns {"fileA::fileB": count} (sorted key pair).
    Caches to .stacker-cache/cochange.json.
    """
    cache_path = Path(root) / CACHE_FILE
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        result = subprocess.run(
            ["git", "log", f"--max-count={max_commits}",
             "--name-only", "--format="],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return {}
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {}

    matrix = _count_co_occurrences(result.stdout)
    cache_path.write_text(json.dumps(matrix, indent=2))
    return matrix


def load_cochange_cache(root: str = ".") -> dict[str, int] | None:
    cache_path = Path(root) / CACHE_FILE
    if cache_path.exists():
        return json.loads(cache_path.read_text())
    return None


def _count_co_occurrences(log_output: str) -> dict[str, int]:
    matrix: dict[str, int] = {}
    commit_files: list[str] = []

    for line in log_output.splitlines():
        stripped = line.strip()
        if not stripped:
            _add_pairs(commit_files, matrix)
            commit_files = []
        else:
            commit_files.append(stripped)

    _add_pairs(commit_files, matrix)
    return matrix


def _add_pairs(files: list[str], matrix: dict[str, int]) -> None:
    for i, a in enumerate(files):
        for b in files[i + 1:]:
            key = "::".join(sorted([a, b]))
            matrix[key] = matrix.get(key, 0) + 1
