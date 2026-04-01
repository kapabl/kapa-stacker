"""Daemon query handlers — wire use cases to daemon actions."""

from __future__ import annotations

import socket

from src.infrastructure.git.git_client import GitClient
from src.infrastructure.parsers.multi_lang_parser import (
    MultiLangImportParser,
    MultiLangSymbolExtractor,
)
from src.infrastructure.complexity.cached_analyzer import CachedComplexityAnalyzer
from src.infrastructure.llm.ollama_backend import OllamaLLMService
from src.infrastructure.llm.llm_text_generator import LlmTextGenerator
from src.infrastructure.llm.rule_based_generator import RuleBasedGenerator
from src.infrastructure.git.cochange_adapter import CachedCochangeProvider
from src.infrastructure.diff.difftastic_classifier import DifftasticClassifier
from src.application.analyze_branch import AnalyzeBranchUseCase
from src.interface.reporters.json_reporter import build_json


def handle_analyze(params: dict, conn: socket.socket) -> dict:
    """Run branch analysis and return JSON-serializable result."""
    git = GitClient()
    base = params.get("base") or git.detect_base()
    max_files = params.get("max_files", 3)
    max_lines = params.get("max_lines", 200)

    llm = OllamaLLMService()
    text_generator = LlmTextGenerator(llm) if llm.available else RuleBasedGenerator()

    analyze_use_case = AnalyzeBranchUseCase(
        git=git,
        parser=MultiLangImportParser(),
        symbols=MultiLangSymbolExtractor(),
        complexity=CachedComplexityAnalyzer(),
        cochange=CachedCochangeProvider(),
        diff_classifier=DifftasticClassifier(),
        text_generator=text_generator,
    )

    result = analyze_use_case.execute(base, max_files, max_lines)
    if not result.files:
        return {"branch": result.branch, "base": base, "total_prs": 0, "prs": []}

    return build_json(result.prs, result.branch, base, result.graph)


def handle_impact(params: dict, conn: socket.socket) -> dict:
    """Find files affected by changes to a target file."""
    from src.domain.service.graph_queries import find_impact

    target = params.get("target")
    if not target:
        raise ValueError("Missing 'target' parameter")

    store = _get_index_store()
    if target not in store.files:
        raise ValueError(f"File not found in index: {target}")
    result = find_impact(target, store.get_dependents)
    return {
        "query": "impact",
        "target": result.target,
        "direct": result.direct,
        "transitive": result.transitive,
        "total_affected": result.total_affected,
    }


def handle_deps(params: dict, conn: socket.socket) -> dict:
    """Find transitive dependencies of a target file."""
    from src.domain.service.graph_queries import find_deps

    target = params.get("target")
    if not target:
        raise ValueError("Missing 'target' parameter")

    store = _get_index_store()
    if target not in store.files:
        raise ValueError(f"File not found in index: {target}")
    deps = find_deps(target, store.get_dependencies)
    return {
        "query": "deps",
        "target": target,
        "dependencies": deps,
        "total": len(deps),
    }


def handle_hotspots(params: dict, conn: socket.socket) -> dict:
    """Find riskiest files — high complexity + many dependents."""
    from src.domain.service.graph_queries import find_hotspots

    limit = params.get("limit", 20)
    store = _get_index_store()

    results = find_hotspots(
        list(store.files.keys()),
        get_complexity=lambda path: store.files[path].complexity if path in store.files else 0,
        get_dependents=store.get_dependents,
        limit=limit,
    )
    return {
        "query": "hotspots",
        "hotspots": [
            {"path": entry.path, "complexity": entry.complexity,
             "dependents": entry.dependent_count, "score": round(entry.score, 1)}
            for entry in results
        ],
    }


def handle_calls(params: dict, conn: socket.socket) -> dict:
    """Find symbol impact via pure call graph."""
    from src.domain.service.graph_queries import find_symbol_impact

    symbol = params.get("target")
    if not symbol:
        raise ValueError("Missing 'target' parameter (symbol name)")

    store = _get_index_store()

    files = store.get_files_defining_symbol(symbol)
    if not files:
        raise ValueError(f"Symbol not found in index: {symbol}")
    target_file, _ = _find_symbol_definition(store, symbol)

    result = find_symbol_impact(
        symbol, target_file, store.get_callers,
    )
    return {
        "query": "symbol_impact",
        "symbol": result.target_symbol,
        "file": result.target_file,
        "call_chains": [
            {
                "caller_function": chain.caller_function,
                "caller_file": chain.caller_file,
                "callee_function": chain.callee_function,
                "line": chain.line,
                "depth": chain.depth,
            }
            for chain in result.call_chains
        ],
        "affected_files": result.affected_files,
        "total_affected": result.total_affected,
    }


def handle_refs(params: dict, conn: socket.socket) -> dict:
    """Find all references to a symbol via LSP (calls + type refs)."""
    symbol = params.get("target")
    if not symbol:
        raise ValueError("Missing 'target' parameter (symbol name)")

    lsp = _get_lsp_resolver()
    if not lsp or not lsp.available:
        raise ValueError("LSP not available — no language server running")

    store = _get_index_store()
    files = store.get_files_defining_symbol(symbol)
    if not files:
        raise ValueError(f"Symbol not found in index: {symbol}")
    target_file, symbol_line = _find_symbol_definition(store, symbol)

    references = lsp.get_references(target_file, symbol, symbol_line)

    return {
        "query": "refs",
        "symbol": symbol,
        "file": target_file,
        "references": references,
        "total_references": len(references),
    }


def _classify_references(
    raw_refs: list[dict], symbol: str,
) -> list[dict]:
    """Enrich references with source line text and classification."""
    from pathlib import Path

    # Cache file contents to avoid re-reading
    file_lines_cache: dict[str, list[str]] = {}
    results: list[dict] = []

    for ref in raw_refs:
        file_path = ref["file"]
        line_num = ref["line"]

        if file_path not in file_lines_cache:
            try:
                file_lines_cache[file_path] = Path(file_path).read_text(
                    errors="replace",
                ).splitlines()
            except (FileNotFoundError, PermissionError):
                file_lines_cache[file_path] = []

        lines = file_lines_cache[file_path]
        source_line = lines[line_num - 1].strip() if line_num <= len(lines) else ""
        kind = _classify_line(source_line, symbol)

        results.append({
            "file": file_path,
            "line": line_num,
            "source": source_line,
            "kind": kind,
        })

    return results


def _classify_line(source_line: str, symbol: str) -> str:
    """Classify a reference line as call, type, inherits, member, or ref."""
    stripped = source_line.lstrip()

    # Inheritance: "class Foo : public Symbol" or ": Symbol("
    if f": public {symbol}" in source_line or f": protected {symbol}" in source_line:
        return "inherits"
    if f": private {symbol}" in source_line:
        return "inherits"

    # Constructor call: "Symbol(" or "new Symbol("
    if f"{symbol}(" in source_line or f"new {symbol}" in source_line:
        return "call"

    # Member function: "Symbol::method" or "symbol->method" or "symbol.method"
    if f"{symbol}::" in source_line:
        return "member"

    # Pointer/reference type: "Symbol*" or "Symbol&" or "Symbol " as param
    if f"{symbol}*" in source_line or f"{symbol}&" in source_line:
        return "type"
    if f"{symbol} " in source_line and ("(" in source_line or "," in source_line):
        return "type"

    # Cast
    if f"static_cast<{symbol}" in source_line or f"dynamic_cast<{symbol}" in source_line:
        return "cast"

    # Template
    if f"<{symbol}>" in source_line or f"<{symbol}," in source_line:
        return "template"

    return "ref"


def _find_symbol_definition(store, symbol_name: str) -> tuple[str, int]:
    """Find the best definition of a symbol — prefers declaration over implementation.

    For C++ classes: prefers the class/struct in .h over the constructor in .cpp.
    Returns (file_path, line).
    """
    _DECLARATION_KINDS = {"class", "struct", "interface", "enum", "type"}
    _DEFINITION_KINDS = {"function", "func", "method", "methodSpec", "prototype"}

    best_file = ""
    best_line = 1
    best_priority = 99

    for file_path in store.get_files_defining_symbol(symbol_name):
        for symbol in store.get_symbols_for_file(file_path):
            if symbol.name != symbol_name:
                continue
            if symbol.kind in _DECLARATION_KINDS:
                priority = 0  # declaration — best for LSP queries
            elif symbol.kind in _DEFINITION_KINDS and file_path.endswith((".h", ".hpp")):
                priority = 1  # prototype in header
            elif symbol.kind in _DEFINITION_KINDS:
                priority = 2  # implementation
            else:
                priority = 3

            if priority < best_priority:
                best_priority = priority
                best_file = file_path
                best_line = symbol.line

    if not best_file:
        files = store.get_files_defining_symbol(symbol_name)
        best_file = files[0] if files else ""

    return best_file, best_line


def handle_symbol_impact_full(params: dict, conn: socket.socket) -> dict:
    """Unified symbol impact — best available from static index + LSP."""
    from src.domain.service.graph_queries import find_symbol_impact, find_impact

    symbol = params.get("target")
    if not symbol:
        raise ValueError("Missing 'target' parameter (symbol name)")

    store = _get_index_store()
    files = store.get_files_defining_symbol(symbol)
    if not files:
        raise ValueError(f"Symbol not found in index: {symbol}")
    target_file, symbol_line = _find_symbol_definition(store, symbol)

    # Call graph (static index)
    call_result = find_symbol_impact(symbol, target_file, store.get_callers)
    call_chains = [
        {
            "caller_function": chain.caller_function,
            "caller_file": chain.caller_file,
            "callee_function": chain.callee_function,
            "line": chain.line,
            "depth": chain.depth,
        }
        for chain in call_result.call_chains
    ]

    # File deps (static index)
    file_result = find_impact(target_file, store.get_dependents)
    file_deps = {
        "direct": file_result.direct,
        "transitive": file_result.transitive,
        "total": file_result.total_affected,
    }

    # LSP refs — wait for LSP to become ready
    lsp_refs = []
    lsp_status = "unavailable"
    lsp = _wait_for_lsp(conn)
    if lsp:
        lsp_status = "ready"
        raw_refs = lsp.get_references(target_file, symbol, symbol_line)
        lsp_refs = _classify_references(raw_refs, symbol)

    return {
        "query": "symbol_impact_full",
        "symbol": symbol,
        "file": target_file,
        "calls": call_chains,
        "file_deps": file_deps,
        "lsp_refs": lsp_refs,
        "lsp_status": lsp_status,
    }


def handle_status(params: dict, conn: socket.socket) -> dict:
    """Return daemon status."""
    store = _get_index_store()
    lsp = _get_lsp_resolver()
    return {
        "running": True,
        "index_files": store.file_count if store else 0,
        "index_symbols": store.symbol_count if store else 0,
        "index_edges": store.edge_count if store else 0,
        "index_calls": store.call_count if store else 0,
        "lsp_available": bool(lsp and lsp.available),
    }


# Module-level shared state — set by daemon on boot
_index_store: "IndexStore | None" = None
_lsp_resolver = None


def set_index_store(store) -> None:
    global _index_store
    _index_store = store


def set_lsp_resolver(resolver) -> None:
    global _lsp_resolver
    _lsp_resolver = resolver


def _get_index_store():
    from src.infrastructure.indexer.index_store import IndexStore
    global _index_store
    if _index_store is None:
        _index_store = IndexStore()
    return _index_store


def _get_lsp_resolver():
    return _lsp_resolver


def _wait_for_lsp(conn: socket.socket, timeout: int = 300):
    """Block until the LSP resolver is ready, streaming progress to client."""
    import time
    from src.interface.daemon.protocol import DaemonResponse

    deadline = time.monotonic() + timeout
    last_message = ""
    while time.monotonic() < deadline:
        lsp = _get_lsp_resolver()
        if lsp and lsp.available:
            conn.sendall(DaemonResponse.progress("LSP: 100%").serialize())
            return lsp
        message = lsp.progress_message if lsp else "LSP: starting..."
        if message and message != last_message:
            conn.sendall(DaemonResponse.progress(message).serialize())
            last_message = message
        time.sleep(0.5)
    return _get_lsp_resolver()


def handle_symbol_file_impact(params: dict, conn: socket.socket) -> dict:
    """Find file-level impact for a symbol's file."""
    from src.domain.service.graph_queries import find_impact

    symbol = params.get("target")
    if not symbol:
        raise ValueError("Missing 'target' parameter (symbol name)")

    store = _get_index_store()
    files = store.get_files_defining_symbol(symbol)
    if not files:
        raise ValueError(f"Symbol not found in index: {symbol}")
    target_file = files[0]

    result = find_impact(target_file, store.get_dependents)
    return {
        "query": "file_impact",
        "symbol": symbol,
        "target": result.target,
        "direct": result.direct,
        "transitive": result.transitive,
        "total_affected": result.total_affected,
    }


def handle_reindex(params: dict, conn: socket.socket) -> dict:
    """Re-index specific files or the entire repo."""
    from src.infrastructure.indexer.incremental_indexer import update_file, find_source_files
    from src.infrastructure.indexer.graph_builder import STORE_PATH

    store = _get_index_store()
    files = params.get("files")
    if not files:
        files = find_source_files(".")

    for file_path in files:
        update_file(store, file_path)

    store.save(STORE_PATH)
    set_index_store(store)
    return {"reindexed": len(files)}


def build_handler_map(server=None) -> dict:
    """Build action → handler mapping for the query router."""
    handlers = {
        "analyze": handle_analyze,
        "impact": handle_impact,
        "deps": handle_deps,
        "hotspots": handle_hotspots,
        "calls": handle_calls,
        "refs": handle_refs,
        "symbol_file_impact": handle_symbol_file_impact,
        "symbol_impact_full": handle_symbol_impact_full,
        "reindex": handle_reindex,
        "status": handle_status,
    }
    if server:
        handlers["shutdown"] = lambda params, conn: _handle_shutdown(server)
    return handlers


def _handle_shutdown(server) -> dict:
    """Shutdown the daemon gracefully."""
    server.request_shutdown()
    return {"message": "Shutting down"}
