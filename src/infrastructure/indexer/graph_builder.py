"""Build IndexStore from JSON caches — single source of truth."""

from __future__ import annotations

from pathlib import Path

from src.infrastructure.indexer.index_store import (
    IndexStore, FileEntry, SymbolEntry, ImportEntry, EdgeEntry, CallEntry,
)
from src.infrastructure.indexer.ctags_indexer import load_ctags_cache
from src.infrastructure.indexer.import_cache import load_import_cache
from src.infrastructure.indexer.complexity_cache import load_complexity_cache
from src.infrastructure.indexer.call_cache import load_call_cache
from src.infrastructure.parsers.language_detector import detect_language

STORE_PATH = ".cortex-cache/index.msgpack"

# Symbol kinds that can never be call targets (language-agnostic exclusion)
_NON_CALLABLE_KINDS = frozenset({
    "chapter", "section", "subsection", "subsubsection", "l4subsection",
    "l5subsection", "footnote", "play",  # markdown/docs
    "const", "constant", "define", "macro", "macroparam",  # constants/macros
    "var", "variable", "globalVar", "local", "field", "member",
    "anonMember", "parameter", "receiver",  # variables/fields
    "enum", "enumerator",  # enum values
    "package", "packageName", "namespace", "nsprefix",  # packages
    "label", "target", "id",  # misc non-callable
    "key", "string", "number", "boolean", "null", "array", "object",  # JSON/data
    "heredoc", "oneof", "message", "service", "rpc",  # protobuf
    "type", "talias",  # type aliases (not constructors)
})


def _normalize_path(path: str) -> str:
    """Strip leading ./ for consistent path keys."""
    if path.startswith("./"):
        return path[2:]
    return path


def build_index_store(root: str = ".") -> IndexStore:
    """Assemble IndexStore from JSON caches and save as msgpack."""
    store = IndexStore()

    _load_files(store, root)
    _load_symbols(store, root)
    _load_imports(store, root)
    _build_edges(store, root)
    _load_and_resolve_calls(store, root)

    store_path = Path(root) / STORE_PATH
    store.save(str(store_path))
    return store


def _load_files(store: IndexStore, root: str) -> None:
    """Populate file entries from complexity cache."""
    complexity = load_complexity_cache(root) or {}
    for raw_path, metrics in complexity.items():
        file_path = _normalize_path(raw_path)
        language = detect_language(file_path) or metrics.get("language", "")
        store.add_file(FileEntry(
            path=file_path,
            language=language,
            file_hash=metrics.get("hash", ""),
            lines=metrics.get("lines", 0),
            complexity=metrics.get("complexity", 0),
        ))


def _load_symbols(store: IndexStore, root: str) -> None:
    """Populate symbols from ctags cache."""
    ctags = load_ctags_cache(root) or {}
    for raw_path, symbol_list in ctags.items():
        file_path = _normalize_path(raw_path)
        entries = [
            SymbolEntry(
                name=sym.get("name", ""),
                kind=sym.get("kind", ""),
                line=sym.get("line", 0),
                scope=sym.get("scope", ""),
                file_path=file_path,
            )
            for sym in symbol_list
        ]
        store.add_symbols(file_path, entries)


def _load_imports(store: IndexStore, root: str) -> None:
    """Populate imports from import cache."""
    imports = load_import_cache(root) or {}
    for raw_path, import_list in imports.items():
        file_path = _normalize_path(raw_path)
        entries = [
            ImportEntry(
                raw=imp.get("raw", ""),
                module=imp.get("module", ""),
                kind=imp.get("kind", ""),
                file_path=file_path,
            )
            for imp in import_list
        ]
        store.add_imports(file_path, entries)


def _build_edges(store: IndexStore, root: str = ".") -> None:
    """Build dependency edges from imports → file definitions."""
    resolvers = _build_resolvers(root, store)

    for file_path, imports in store.imports.items():
        for imp in imports:
            targets = _resolve_import_chain(
                imp.module, file_path, resolvers,
            )
            for target in targets:
                store.add_edge(EdgeEntry(
                    source=file_path, target=target,
                    kind="import", weight=1.0,
                ))


def _load_and_resolve_calls(store: IndexStore, root: str) -> None:
    """Load raw call sites from cache and resolve to cross-file edges.

    Only resolves a call when the caller has a dependency edge to the
    callee's file — prevents false matches on common function names.
    """
    raw_calls = load_call_cache(root) or {}

    # symbol name → all files that define it (exclude non-callable kinds)
    symbol_to_files: dict[str, list[str]] = {}
    for file_path, symbol_list in store.symbols.items():
        for symbol in symbol_list:
            if symbol.kind not in _NON_CALLABLE_KINDS:
                symbol_to_files.setdefault(symbol.name, []).append(file_path)

    # caller_file → set of files it depends on (has import edge to)
    deps_of: dict[str, set[str]] = {}
    for edge in store.edges:
        deps_of.setdefault(edge.source, set()).add(edge.target)

    # caller_file → set of raw import module strings
    imports_of: dict[str, set[str]] = {}
    for file_path, import_list in store.imports.items():
        imports_of[file_path] = {imp.module for imp in import_list}

    for raw_caller, call_list in raw_calls.items():
        caller_file = _normalize_path(raw_caller)
        caller_deps = deps_of.get(caller_file, set())
        caller_imports = imports_of.get(caller_file, set())
        for call in call_list:
            callee_name = call.get("callee_name", "")
            candidate_files = symbol_to_files.get(callee_name, [])

            callee_file = _pick_callee_file(
                caller_file, candidate_files, caller_deps, caller_imports,
            )
            if not callee_file:
                continue

            store.add_call(CallEntry(
                caller_file=caller_file,
                caller_function=call.get("caller_function", ""),
                callee_file=callee_file,
                callee_function=callee_name,
                line=call.get("line", 0),
            ))


def _pick_callee_file(
    caller_file: str,
    candidate_files: list[str],
    caller_deps: set[str],
    caller_imports: set[str],
) -> str | None:
    """Pick the correct callee file from candidates.

    1. Prefer files the caller imports via resolved edge.
    2. Fuzzy: match candidate path segments against raw import strings.
    3. Unambiguous: only one candidate.
    4. Ambiguous with no signal → skip.
    """
    others = [f for f in candidate_files if f != caller_file]
    if not others:
        return None

    # Strong: caller has a resolved import edge to this file
    for candidate in others:
        if candidate in caller_deps:
            return candidate

    # Fuzzy: check if any raw import mentions path segments of the candidate
    # e.g. import "buck2_error.context" matches "app/buck2_error/src/context.rs"
    if caller_imports:
        for candidate in others:
            if _import_matches_path(caller_imports, candidate):
                return candidate

    # Unambiguous: only one file defines this symbol
    if len(others) == 1:
        return others[0]

    return None


def _import_matches_path(imports: set[str], candidate_path: str) -> bool:
    """Check if any import string fuzzy-matches the candidate file path."""
    # Convert path to dot segments: app/buck2_error/src/context.rs → buck2_error.context
    path_parts = Path(candidate_path).with_suffix("").parts
    # Skip common path noise: app, src, lib, mod
    meaningful = [p for p in path_parts if p not in ("app", "src", "lib", "mod", ".")]

    for imp in imports:
        imp_parts = imp.split(".")
        # Check if the last 2+ segments of the import match meaningful path parts
        if len(imp_parts) >= 2 and len(meaningful) >= 2:
            if imp_parts[-1] in meaningful and imp_parts[-2] in meaningful:
                return True
    return False


def _build_module_index(store: IndexStore) -> dict[str, str]:
    """Map module-like keys to file paths for O(1) import resolution.

    Builds exact-match, suffix, and prefix lookups.
    """
    exact: dict[str, str] = {}
    suffix: dict[str, str] = {}   # last segment → file_path (first wins)

    for file_path in store.files:
        mod = _path_to_module(file_path)
        exact[mod] = file_path
        # Register all suffixes: a.b.c → "c", "b.c", "a.b.c"
        parts = mod.split(".")
        for depth in range(1, len(parts)):
            key = ".".join(parts[-depth:])
            suffix.setdefault(key, file_path)

    return {"exact": exact, "suffix": suffix}


def _build_resolvers(root: str, store: IndexStore) -> list[callable]:
    """Build a chain of import resolvers. Each returns targets or empty."""
    from src.infrastructure.parsers.go_module_resolver import GoModuleResolver

    from src.infrastructure.parsers.go_module_resolver import build_dir_index

    known_files = set(store.files.keys())
    module_index = _build_module_index(store)
    resolvers: list[callable] = []

    # Language-specific resolvers (auto-detect from project files)
    go_resolver = GoModuleResolver(root)
    if go_resolver.available:
        dir_index = build_dir_index(known_files)
        resolvers.append(
            lambda module, source: [
                target for target in go_resolver.resolve_to_files(module, dir_index)
                if target != source
            ]
        )

    # Generic module index (fallback for all languages)
    resolvers.append(
        lambda module, source: (
            [target] if (target := _resolve_import(module, source, module_index)) else []
        )
    )

    return resolvers


def _resolve_import_chain(
    module: str, source_path: str, resolvers: list[callable],
) -> list[str]:
    """Try each resolver in order. First one that returns results wins."""
    for resolver in resolvers:
        targets = resolver(module, source_path)
        if targets:
            return targets
    return []


def _resolve_import(
    module: str, source_path: str, module_index: dict,
) -> str | None:
    """Resolve an import module to a file path (O(1) lookups)."""
    normalized = module.replace("/", ".").replace("::", ".").lstrip(".")

    exact = module_index["exact"]
    suffix = module_index["suffix"]

    # Exact match: import module matches a file's full module path
    target = exact.get(normalized)
    if target and target != source_path:
        return target

    # Suffix match: import "bar.baz" matches file "foo.bar.baz"
    target = suffix.get(normalized)
    if target and target != source_path:
        return target

    # Prefix match: import "foo.bar.baz.func" → try progressively shorter
    parts = normalized.split(".")
    for depth in range(len(parts) - 1, 0, -1):
        prefix = ".".join(parts[:depth])
        target = exact.get(prefix)
        if target and target != source_path:
            return target

    return None


def _path_to_module(path: str) -> str:
    module_path = Path(path).with_suffix("")
    return str(module_path).replace("/", ".").replace("\\", ".")
