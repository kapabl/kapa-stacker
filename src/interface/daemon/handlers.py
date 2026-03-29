"""Daemon query handlers — wire use cases to daemon actions."""

from __future__ import annotations

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


def handle_analyze(params: dict) -> dict:
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


def handle_impact(params: dict) -> dict:
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


def handle_deps(params: dict) -> dict:
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


def handle_hotspots(params: dict) -> dict:
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


def handle_calls(params: dict) -> dict:
    """Find call-graph impact for a symbol."""
    from src.domain.service.graph_queries import find_call_impact

    symbol = params.get("target")
    if not symbol:
        raise ValueError("Missing 'target' parameter (symbol name)")

    store = _get_index_store()

    def get_symbol_file(name):
        files = store.get_files_defining_symbol(name)
        return files[0] if files else None

    result = find_call_impact(symbol, store.get_callers_of_symbol, get_symbol_file)
    return {
        "query": "call_impact",
        "symbol": result.target_symbol,
        "file": result.target_file,
        "direct_callers": [
            {"caller_file": chain.caller_file, "caller_function": chain.caller_function,
             "line": chain.line}
            for chain in result.direct_callers
        ],
        "transitive_callers": [
            {"caller_file": chain.caller_file, "caller_function": chain.caller_function,
             "line": chain.line}
            for chain in result.transitive_callers
        ],
        "total_call_chains": result.total_call_chains,
    }


def handle_status(params: dict) -> dict:
    """Return daemon status."""
    store = _get_index_store()
    return {
        "running": True,
        "index_files": store.file_count if store else 0,
        "index_symbols": store.symbol_count if store else 0,
        "index_edges": store.edge_count if store else 0,
        "index_calls": store.call_count if store else 0,
    }


# Module-level index store — shared across handlers in daemon process
_index_store: "IndexStore | None" = None


def set_index_store(store) -> None:
    """Set the shared index store (called by daemon on boot)."""
    global _index_store
    _index_store = store


def _get_index_store():
    """Get the shared index store."""
    from src.infrastructure.indexer.index_store import IndexStore
    global _index_store
    if _index_store is None:
        _index_store = IndexStore()
    return _index_store


def build_handler_map(server=None) -> dict:
    """Build action → handler mapping for the query router."""
    handlers = {
        "analyze": handle_analyze,
        "impact": handle_impact,
        "deps": handle_deps,
        "hotspots": handle_hotspots,
        "calls": handle_calls,
        "status": handle_status,
    }
    if server:
        handlers["shutdown"] = lambda params: _handle_shutdown(server)
    return handlers


def _handle_shutdown(server) -> dict:
    """Shutdown the daemon gracefully."""
    server.request_shutdown()
    return {"message": "Shutting down"}
