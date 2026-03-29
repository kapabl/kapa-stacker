"""Build and incrementally update the in-memory index."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from src.infrastructure.indexer.index_store import (
    IndexStore, FileEntry, SymbolEntry, ImportEntry, EdgeEntry, CallEntry,
    compute_file_hash,
)
from src.infrastructure.parsers.language_detector import detect_language
from src.infrastructure.parsers.import_dispatcher import dispatch_parse_imports
from src.infrastructure.parsers.multi_lang_parser import MultiLangSymbolExtractor
from src.infrastructure.complexity.lizard_analyzer import analyze_lizard
from src.infrastructure.parsers.call_extractor import extract_calls
from src.infrastructure.indexer.graph_builder import (
    STORE_PATH, _build_module_index, _resolve_import,
)

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

GREEN = "\033[32m"
CYAN = "\033[36m"
DIM = "\033[2m"
RESET = "\033[0m"


def build_full(root: str = ".") -> IndexStore:
    """Load IndexStore from cache, or run full index_repo first."""
    store_path = Path(root) / STORE_PATH

    if store_path.exists():
        store = IndexStore.load(str(store_path))
        print(
            f"  {GREEN}✓{RESET} Loaded index: {store.file_count} files, "
            f"{store.symbol_count} symbols, {store.edge_count} edges, "
            f"{store.call_count} calls",
            file=sys.stderr,
        )
        return store

    print(f"  {CYAN}No cached index — running full index...{RESET}", file=sys.stderr)
    from src.infrastructure.indexer.index_all import index_repo
    index_repo(root)

    store = IndexStore.load(str(store_path))
    print(
        f"  {GREEN}✓{RESET} Index ready: {store.file_count} files, "
        f"{store.symbol_count} symbols, {store.edge_count} edges, "
        f"{store.call_count} calls",
        file=sys.stderr,
    )
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

    complexity = 0
    metrics = analyze_lizard([file_path])
    if file_path in metrics:
        complexity = metrics[file_path].complexity

    store.add_file(FileEntry(
        path=file_path,
        language=language,
        file_hash=file_hash,
        lines=line_count,
        complexity=complexity,
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

    # Extract call sites (unresolved — callee_file not yet known)
    language = detect_language(file_path)
    if language:
        call_sites = extract_calls(file_path, source, language)
        if not hasattr(store, '_raw_calls'):
            store._raw_calls = []
        store._raw_calls.extend(call_sites)


def find_source_files(root: str = ".") -> list[str]:
    """Walk the repo and find all source files. Paths normalized to relative."""
    files: list[str] = []
    root_path = Path(root).resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [directory for directory in dirnames if directory not in _SKIP_DIRS]
        for name in filenames:
            ext = Path(name).suffix.lower()
            if ext in _SOURCE_EXTENSIONS:
                full_path = Path(os.path.join(dirpath, name)).resolve()
                relative = str(full_path.relative_to(root_path))
                files.append(relative)
    return files


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




