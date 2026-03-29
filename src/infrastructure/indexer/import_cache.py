"""Pre-compute and cache import graphs for all source files."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from src.infrastructure.parsers.import_dispatcher import dispatch_parse_imports

CACHE_FILE = ".stacker-cache/imports.json"


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

    for path in file_paths:
        file_hash = hashes.get(path, "")
        cached = existing.get(path)

        if cached and cached.get("hash") == file_hash:
            result[path] = cached["imports"]
            continue

        source = _read_file(path)
        if not source:
            continue

        imports = dispatch_parse_imports(path, source)
        result[path] = [
            {"raw": imp.raw, "module": imp.module, "kind": imp.kind}
            for imp in imports
        ]

    _save_cache(cache_path, result, hashes)
    return result


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
