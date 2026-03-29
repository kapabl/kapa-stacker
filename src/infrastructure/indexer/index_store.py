"""In-memory index store — the daemon's core data structure."""

from __future__ import annotations

import hashlib
import msgpack
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FileEntry:
    """Indexed file metadata."""
    path: str
    language: str
    file_hash: str
    lines: int = 0
    complexity: int = 0


@dataclass
class SymbolEntry:
    """Symbol defined in a file."""
    name: str
    kind: str
    line: int
    scope: str
    file_path: str


@dataclass
class ImportEntry:
    """Import/dependency from a file."""
    raw: str
    module: str
    kind: str
    file_path: str


@dataclass
class CallEntry:
    """A resolved function call: caller → callee across files."""
    caller_file: str
    caller_function: str
    callee_file: str
    callee_function: str
    line: int


@dataclass
class EdgeEntry:
    """Dependency edge between files."""
    source: str
    target: str
    kind: str
    weight: float


class IndexStore:
    """In-memory index of files, symbols, imports, and edges."""

    def __init__(self):
        self.files: dict[str, FileEntry] = {}
        self.symbols: dict[str, list[SymbolEntry]] = {}  # file_path → symbols
        self.imports: dict[str, list[ImportEntry]] = {}   # file_path → imports
        self.edges: list[EdgeEntry] = []
        self.calls: list[CallEntry] = []                  # resolved call graph
        self._symbol_index: dict[str, list[str]] = {}     # symbol_name → file_paths
        self._dependents: dict[str, list[str]] = {}        # target → [source files]
        self._dependencies: dict[str, list[str]] = {}      # source → [target files]
        # strong name: (callee_function, callee_file) → [calls]
        self._callers_by_strong_name: dict[tuple[str, str], list[CallEntry]] = {}
        # weak name: callee_function → [calls] (for initial lookup by name only)
        self._callers_by_name: dict[str, list[CallEntry]] = {}

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def symbol_count(self) -> int:
        return sum(len(syms) for syms in self.symbols.values())

    @property
    def edge_count(self) -> int:
        return len(self.edges)

    def add_file(self, entry: FileEntry) -> None:
        self.files[entry.path] = entry

    def add_symbols(self, file_path: str, entries: list[SymbolEntry]) -> None:
        self.symbols[file_path] = entries
        for symbol in entries:
            self._symbol_index.setdefault(symbol.name, []).append(file_path)

    def add_imports(self, file_path: str, entries: list[ImportEntry]) -> None:
        self.imports[file_path] = entries

    def add_edge(self, edge: EdgeEntry) -> None:
        self.edges.append(edge)
        self._dependents.setdefault(edge.target, []).append(edge.source)
        self._dependencies.setdefault(edge.source, []).append(edge.target)

    def add_call(self, call: CallEntry) -> None:
        self.calls.append(call)
        strong_key = (call.callee_function, call.callee_file)
        self._callers_by_strong_name.setdefault(strong_key, []).append(call)
        self._callers_by_name.setdefault(call.callee_function, []).append(call)

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def remove_file(self, file_path: str) -> None:
        """Remove a file and all its associated data."""
        self.files.pop(file_path, None)
        old_symbols = self.symbols.pop(file_path, [])
        for symbol in old_symbols:
            paths = self._symbol_index.get(symbol.name, [])
            self._symbol_index[symbol.name] = [
                path for path in paths if path != file_path
            ]
        self.imports.pop(file_path, None)
        self.edges = [
            edge for edge in self.edges
            if edge.source != file_path and edge.target != file_path
        ]
        self.calls = [
            call for call in self.calls
            if call.caller_file != file_path and call.callee_file != file_path
        ]
        # Rebuild reverse indexes
        self._rebuild_indexes()

    def get_symbols_for_file(self, file_path: str) -> list[SymbolEntry]:
        return self.symbols.get(file_path, [])

    def get_imports_for_file(self, file_path: str) -> list[ImportEntry]:
        return self.imports.get(file_path, [])

    def get_files_defining_symbol(self, symbol_name: str) -> list[str]:
        return self._symbol_index.get(symbol_name, [])

    def get_callers(self, symbol_name: str, file_path: str) -> list[CallEntry]:
        """Find call sites targeting a specific (name, file) strong name."""
        return self._callers_by_strong_name.get((symbol_name, file_path), [])

    def get_callers_by_name(self, symbol_name: str) -> list[CallEntry]:
        """Find call sites by name only (for initial symbol lookup)."""
        return self._callers_by_name.get(symbol_name, [])

    def get_dependents(self, file_path: str) -> list[str]:
        """Files that depend on the given file (reverse edges)."""
        return self._dependents.get(file_path, [])

    def get_dependencies(self, file_path: str) -> list[str]:
        """Files that the given file depends on (forward edges)."""
        return self._dependencies.get(file_path, [])

    def _rebuild_indexes(self) -> None:
        """Rebuild all reverse indexes from edges and calls."""
        self._dependents.clear()
        self._dependencies.clear()
        self._callers_by_strong_name.clear()
        self._callers_by_name.clear()
        for edge in self.edges:
            self._dependents.setdefault(edge.target, []).append(edge.source)
            self._dependencies.setdefault(edge.source, []).append(edge.target)
        for call in self.calls:
            strong_key = (call.callee_function, call.callee_file)
            self._callers_by_strong_name.setdefault(strong_key, []).append(call)
            self._callers_by_name.setdefault(call.callee_function, []).append(call)

    def save(self, path: str) -> None:
        """Persist index to MessagePack file."""
        data = {
            "files": {
                file_path: {
                    "language": entry.language,
                    "file_hash": entry.file_hash,
                    "lines": entry.lines,
                    "complexity": entry.complexity,
                }
                for file_path, entry in self.files.items()
            },
            "symbols": {
                file_path: [
                    {"name": sym.name, "kind": sym.kind, "line": sym.line, "scope": sym.scope}
                    for sym in syms
                ]
                for file_path, syms in self.symbols.items()
            },
            "imports": {
                file_path: [
                    {"raw": imp.raw, "module": imp.module, "kind": imp.kind}
                    for imp in imps
                ]
                for file_path, imps in self.imports.items()
            },
            "edges": [
                {"source": edge.source, "target": edge.target,
                 "kind": edge.kind, "weight": edge.weight}
                for edge in self.edges
            ],
            "calls": [
                {"caller_file": call.caller_file, "caller_function": call.caller_function,
                 "callee_file": call.callee_file, "callee_function": call.callee_function,
                 "line": call.line}
                for call in self.calls
            ],
        }
        target_path = Path(path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(msgpack.packb(data, use_bin_type=True))

    @classmethod
    def load(cls, path: str) -> IndexStore:
        """Load index from MessagePack file."""
        raw = Path(path).read_bytes()
        data = msgpack.unpackb(raw, raw=False)

        store = cls()

        for file_path, file_data in data.get("files", {}).items():
            store.add_file(FileEntry(
                path=file_path,
                language=file_data["language"],
                file_hash=file_data["file_hash"],
                lines=file_data.get("lines", 0),
                complexity=file_data.get("complexity", 0),
            ))

        for file_path, sym_list in data.get("symbols", {}).items():
            entries = [
                SymbolEntry(
                    name=sym["name"], kind=sym["kind"],
                    line=sym.get("line", 0), scope=sym.get("scope", ""),
                    file_path=file_path,
                )
                for sym in sym_list
            ]
            store.add_symbols(file_path, entries)

        for file_path, imp_list in data.get("imports", {}).items():
            entries = [
                ImportEntry(
                    raw=imp["raw"], module=imp["module"],
                    kind=imp.get("kind", ""), file_path=file_path,
                )
                for imp in imp_list
            ]
            store.add_imports(file_path, entries)

        for edge_data in data.get("edges", []):
            store.add_edge(EdgeEntry(
                source=edge_data["source"], target=edge_data["target"],
                kind=edge_data["kind"], weight=edge_data["weight"],
            ))

        for call_data in data.get("calls", []):
            store.add_call(CallEntry(
                caller_file=call_data["caller_file"],
                caller_function=call_data["caller_function"],
                callee_file=call_data["callee_file"],
                callee_function=call_data["callee_function"],
                line=call_data["line"],
            ))

        return store


def compute_file_hash(file_path: str) -> str:
    """Compute MD5 hash of a file for change detection."""
    try:
        content = Path(file_path).read_bytes()
        return hashlib.md5(content).hexdigest()
    except (FileNotFoundError, PermissionError):
        return ""
