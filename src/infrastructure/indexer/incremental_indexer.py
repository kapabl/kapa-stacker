"""Build and incrementally update the in-memory index."""

from __future__ import annotations

import os
from pathlib import Path

from src.infrastructure.indexer.index_store import (
    IndexStore, FileEntry, SymbolEntry, ImportEntry, EdgeEntry,
    compute_file_hash,
)
from src.infrastructure.parsers.language_detector import detect_language
from src.infrastructure.parsers.import_dispatcher import dispatch_parse_imports
from src.infrastructure.parsers.multi_lang_parser import MultiLangSymbolExtractor

_SOURCE_EXTENSIONS = {
    ".py", ".pyi", ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp",
    ".java", ".kt", ".kts", ".go", ".rs",
    ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".gradle", ".cmake", ".bzl", ".bxl", ".star", ".groovy",
}

_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".mypy_cache",
    "venv", ".venv", "env", ".env", "dist", "build",
    ".cortex-cache", ".tox", ".pytest_cache",
}

_symbol_extractor = MultiLangSymbolExtractor()


def build_full(root: str = ".") -> IndexStore:
    """Build a complete index from all source files in the repo."""
    store = IndexStore()
    file_paths = find_source_files(root)

    for file_path in file_paths:
        index_file(store, file_path)

    _build_edges(store)
    return store


def update_file(store: IndexStore, file_path: str) -> None:
    """Re-index a single file. Removes old data first."""
    existing = store.files.get(file_path)
    current_hash = compute_file_hash(file_path)

    if existing and existing.file_hash == current_hash:
        return  # unchanged

    store.remove_file(file_path)

    if not os.path.exists(file_path):
        return  # deleted

    index_file(store, file_path)
    _rebuild_edges_for_file(store, file_path)


def index_file(store: IndexStore, file_path: str) -> None:
    """Parse and index a single file into the store."""
    language = detect_language(file_path)
    if not language:
        return

    try:
        source = Path(file_path).read_text(errors="replace")
    except (FileNotFoundError, PermissionError):
        return

    file_hash = compute_file_hash(file_path)
    line_count = source.count("\n") + 1

    store.add_file(FileEntry(
        path=file_path,
        language=language,
        file_hash=file_hash,
        lines=line_count,
    ))

    # Parse symbols
    symbol_defs = _symbol_extractor.extract(file_path, source)
    symbol_entries = [
        SymbolEntry(
            name=sym.name, kind=sym.kind,
            line=sym.line, scope=sym.scope,
            file_path=file_path,
        )
        for sym in symbol_defs
    ]
    store.add_symbols(file_path, symbol_entries)

    # Parse imports
    import_refs = dispatch_parse_imports(file_path, source)
    import_entries = [
        ImportEntry(
            raw=imp.raw, module=imp.module,
            kind=imp.kind, file_path=file_path,
        )
        for imp in import_refs
    ]
    store.add_imports(file_path, import_entries)


def find_source_files(root: str = ".") -> list[str]:
    """Walk the repo and find all source files."""
    files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [directory for directory in dirnames if directory not in _SKIP_DIRS]
        for name in filenames:
            ext = Path(name).suffix.lower()
            if ext in _SOURCE_EXTENSIONS:
                files.append(os.path.join(dirpath, name))
    return files


def _build_edges(store: IndexStore) -> None:
    """Build dependency edges from imports → file definitions."""
    module_index = _build_module_index(store)

    for file_path, imports in store.imports.items():
        for imp in imports:
            target = _resolve_import(imp.module, file_path, module_index)
            if target:
                store.add_edge(EdgeEntry(
                    source=file_path, target=target,
                    kind="import", weight=1.0,
                ))


def _rebuild_edges_for_file(store: IndexStore, file_path: str) -> None:
    """Rebuild edges involving a specific file after update."""
    # Remove old edges for this file
    store.edges = [
        edge for edge in store.edges
        if edge.source != file_path and edge.target != file_path
    ]

    module_index = _build_module_index(store)

    # Rebuild outgoing edges (this file's imports)
    for imp in store.get_imports_for_file(file_path):
        target = _resolve_import(imp.module, file_path, module_index)
        if target:
            store.add_edge(EdgeEntry(
                source=file_path, target=target,
                kind="import", weight=1.0,
            ))

    # Rebuild incoming edges (other files importing this one)
    for other_path, imports in store.imports.items():
        if other_path == file_path:
            continue
        for imp in imports:
            target = _resolve_import(imp.module, other_path, module_index)
            if target == file_path:
                store.add_edge(EdgeEntry(
                    source=other_path, target=file_path,
                    kind="import", weight=1.0,
                ))


def _build_module_index(store: IndexStore) -> dict[str, str]:
    """Map module-like keys to file paths."""
    index: dict[str, str] = {}
    for file_path in store.files:
        mod = _path_to_module(file_path)
        index[mod] = file_path
        short = mod.rsplit(".", 1)[-1]
        index.setdefault(short, file_path)
    return index


def _resolve_import(
    module: str, source_path: str, module_index: dict[str, str],
) -> str | None:
    """Resolve an import module to a file path."""
    normalized = module.replace("/", ".").replace("::", ".").lstrip(".")
    for key, target in module_index.items():
        if target == source_path:
            continue
        if normalized == key or normalized.endswith(f".{key}") or key.endswith(f".{normalized}"):
            return target
    return None


def _path_to_module(path: str) -> str:
    module_path = Path(path).with_suffix("")
    return str(module_path).replace("/", ".").replace("\\", ".")
