"""Pre-compute and cache complexity metrics for all source files."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from src.infrastructure.complexity.analyzer import LizardAnalyzer

CACHE_FILE = ".cortex-cache/complexity.json"


def build_complexity_index(
    file_paths: list[str],
    root: str = ".",
) -> dict[str, dict]:
    """
    Analyze complexity for all files, cache the result.
    Uses file hashes to skip unchanged files.
    """
    cache_path = Path(root) / CACHE_FILE
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    existing = _load_cache(cache_path)
    hashes = _compute_hashes(file_paths)
    analyzer = LizardAnalyzer()

    stale_files = []
    result: dict[str, dict] = {}

    total_files = len(file_paths)
    for index, path in enumerate(file_paths, 1):
        file_hash = hashes.get(path, "")
        cached = existing.get(path)

        if cached and cached.get("hash") == file_hash:
            result[path] = cached
            continue

        stale_files.append(path)

    if stale_files:
        fresh = analyzer.analyze(stale_files)
        for path, metrics in fresh.items():
            result[path] = {
                "hash": hashes.get(path, ""),
                "language": metrics.language,
                "lines": metrics.lines,
                "code": metrics.code,
                "complexity": metrics.complexity,
                "avg_cyclomatic": metrics.avg_cyclomatic,
                "max_cyclomatic": metrics.max_cyclomatic,
            }

    cache_path.write_text(json.dumps(result, indent=2))
    return result


def load_complexity_cache(root: str = ".") -> dict[str, dict] | None:
    cache_path = Path(root) / CACHE_FILE
    if cache_path.exists():
        return json.loads(cache_path.read_text())
    return None


def _load_cache(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def _compute_hashes(file_paths: list[str]) -> dict[str, str]:
    result = {}
    for path in file_paths:
        try:
            content = Path(path).read_bytes()
            result[path] = hashlib.md5(content).hexdigest()
        except (FileNotFoundError, PermissionError):
            pass
    return result
