"""Generate and cache universal-ctags index for the repo."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

TAGS_FILE = ".stacker-cache/tags.json"


def generate_ctags(root: str = ".") -> dict[str, list[dict]]:
    """
    Run universal-ctags on the repo, return {file: [symbols]}.
    Caches result to .stacker-cache/tags.json.
    """
    cache = Path(root) / TAGS_FILE
    cache.parent.mkdir(parents=True, exist_ok=True)

    try:
        result = subprocess.run(
            [
                "ctags", "--output-format=json", "--fields=+neKS",
                "--kinds-all=*", "--recurse", "-f", "-", root,
            ],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            return {}
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {}

    index = _parse_ctags_json(result.stdout)
    cache.write_text(json.dumps(index, indent=2))
    return index


def load_ctags_cache(root: str = ".") -> dict[str, list[dict]] | None:
    """Load cached ctags if available."""
    cache = Path(root) / TAGS_FILE
    if cache.exists():
        return json.loads(cache.read_text())
    return None


def _parse_ctags_json(raw: str) -> dict[str, list[dict]]:
    index: dict[str, list[dict]] = {}
    for line in raw.splitlines():
        try:
            entry = json.loads(line)
            path = entry.get("path", "")
            if path:
                index.setdefault(path, []).append({
                    "name": entry.get("name", ""),
                    "kind": entry.get("kind", ""),
                    "line": entry.get("line", 0),
                    "scope": entry.get("scope", ""),
                })
        except json.JSONDecodeError:
            continue
    return index
