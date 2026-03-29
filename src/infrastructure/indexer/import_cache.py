"""Pre-compute and cache import graphs for all source files."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

CACHE_FILE = ".cortex-cache/imports.json"
_MAX_WORKERS = os.cpu_count() or 4


def build_import_index(
    file_paths: list[str],
    root: str = ".",
) -> dict[str, list[dict]]:
    """
    Parse imports for all files, cache the result.
    Returns {file_path: [{raw, module, kind}, ...]}.
    """
    cache_path = Path(root) / CACHE_FILE
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    existing = _load_cache(cache_path)
    hashes = _compute_hashes(file_paths)
    result: dict[str, list[dict]] = {}
    stale_files: list[str] = []

    for path in file_paths:
        file_hash = hashes.get(path, "")
        cached = existing.get(path)
        if cached and cached.get("hash") == file_hash:
            result[path] = cached["imports"]
        else:
            stale_files.append(path)

    cached_count = len(result)
    total_files = len(file_paths)

    if stale_files:
        fresh = _parse_imports_parallel(stale_files)
        result.update(fresh)

    _report_progress(total_files, total_files, cached_count, len(stale_files))
    _save_cache(cache_path, result, hashes)
    return result


def _parse_imports_single(path: str) -> tuple[str, list[dict]]:
    """Parse imports for a single file. Runs in worker process."""
    from src.infrastructure.parsers.import_dispatcher import dispatch_parse_imports

    source = _read_file(path)
    if not source:
        return path, []

    imports = dispatch_parse_imports(path, source)
    return path, [
        {"raw": imp.raw, "module": imp.module, "kind": imp.kind}
        for imp in imports
    ]


def _parse_imports_parallel(file_paths: list[str]) -> dict[str, list[dict]]:
    """Parse imports in parallel using multiple processes."""
    result: dict[str, list[dict]] = {}
    with ProcessPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        futures = {pool.submit(_parse_imports_single, path): path for path in file_paths}
        for future in as_completed(futures):
            try:
                path, imports = future.result()
                result[path] = imports
            except Exception:
                pass
    return result


def _report_progress(index, total, cached, parsed):
    try:
        from src.infrastructure.indexer.index_all import set_progress
        set_progress(f"{index}/{total}  ({cached} cached, {parsed} parsed)")
    except ImportError:
        pass


def load_import_cache(root: str = ".") -> dict[str, list[dict]] | None:
    cache_path = Path(root) / CACHE_FILE
    if cache_path.exists():
        data = json.loads(cache_path.read_text())
        return {k: v.get("imports", []) for k, v in data.items()}
    return None


def _load_cache(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def _save_cache(path: Path, result: dict, hashes: dict) -> None:
    data = {}
    for file_path, imports in result.items():
        data[file_path] = {
            "hash": hashes.get(file_path, ""),
            "imports": imports,
        }
    path.write_text(json.dumps(data, indent=2))


def _compute_hashes(file_paths: list[str]) -> dict[str, str]:
    result = {}
    for path in file_paths:
        try:
            content = Path(path).read_bytes()
            result[path] = hashlib.md5(content).hexdigest()
        except (FileNotFoundError, PermissionError):
            pass
    return result


def _read_file(path: str) -> str:
    try:
        return Path(path).read_text(errors="replace")
    except (FileNotFoundError, PermissionError):
        return ""
