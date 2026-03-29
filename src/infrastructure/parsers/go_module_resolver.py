"""Resolve Go import paths to local file paths using go.mod."""

from __future__ import annotations

import os
from pathlib import Path


class GoModuleResolver:
    """Maps Go import paths to local directories using go.mod."""

    def __init__(self, root: str = "."):
        self._root = Path(root).resolve()
        self._module_path = ""
        self._replace_map: dict[str, str] = {}
        self._parse_go_mod()

    def _parse_go_mod(self) -> None:
        go_mod = self._root / "go.mod"
        if not go_mod.exists():
            return

        lines = go_mod.read_text().splitlines()
        in_replace = False
        for line in lines:
            stripped = line.strip()

            if stripped.startswith("module "):
                self._module_path = stripped.split(None, 1)[1]

            if stripped == "replace (":
                in_replace = True
                continue
            if in_replace and stripped == ")":
                in_replace = False
                continue

            if "=>" in stripped:
                self._parse_replace(stripped)

    def _parse_replace(self, line: str) -> None:
        parts = line.strip().rstrip(",").split("=>")
        if len(parts) != 2:
            return
        module_prefix = parts[0].strip().split()[0]
        local_path = parts[1].strip().split()[0]
        if local_path.startswith("./"):
            self._replace_map[module_prefix] = local_path[2:]

    @property
    def available(self) -> bool:
        return bool(self._module_path)

    def resolve(self, import_path: str) -> str | None:
        """Resolve a Go import path to a local directory path.

        Returns the directory path (relative to root) or None.
        """
        # Check replace directives first (longest prefix match)
        local_dir = self._match_replace(import_path)
        if local_dir:
            return local_dir

        # Strip module prefix for local packages
        if self._module_path and import_path.startswith(self._module_path + "/"):
            relative = import_path[len(self._module_path) + 1:]
            return relative

        return None

    def _match_replace(self, import_path: str) -> str | None:
        """Find the longest matching replace directive."""
        best_match = ""
        best_local = ""
        for prefix, local_path in self._replace_map.items():
            if import_path.startswith(prefix) and len(prefix) > len(best_match):
                best_match = prefix
                best_local = local_path

        if not best_match:
            return None

        suffix = import_path[len(best_match):]
        return best_local + suffix

    def resolve_to_files(
        self, import_path: str, dir_index: dict[str, list[str]],
    ) -> list[str]:
        """Resolve a Go import to matching source files in the index."""
        local_dir = self.resolve(import_path)
        if not local_dir:
            return []
        return dir_index.get(local_dir, [])


def build_dir_index(known_files: set[str]) -> dict[str, list[str]]:
    """Map directory → list of .go files in that directory."""
    index: dict[str, list[str]] = {}
    for file_path in known_files:
        if file_path.endswith(".go"):
            file_dir = str(Path(file_path).parent)
            index.setdefault(file_dir, []).append(file_path)
    return index
