"""Domain service: graph queries on the dependency index."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass


@dataclass
class ImpactResult:
    """Result of an impact analysis query."""
    target: str
    direct: list[str]
    transitive: list[str]

    @property
    def total_affected(self) -> int:
        return len(self.direct) + len(self.transitive)


@dataclass
class CallChain:
    """A call chain from caller to callee."""
    caller_file: str
    caller_function: str
    callee_file: str
    callee_function: str
    line: int


@dataclass
class CallImpactResult:
    """Result of a call-graph impact query."""
    target_symbol: str
    target_file: str
    direct_callers: list[CallChain]
    transitive_callers: list[CallChain]

    @property
    def total_call_chains(self) -> int:
        return len(self.direct_callers) + len(self.transitive_callers)


@dataclass
class HotspotEntry:
    """A file ranked by risk — high complexity + many dependents."""
    path: str
    complexity: int
    dependent_count: int
    score: float


def find_impact(
    target_path: str,
    get_dependents: callable,
) -> ImpactResult:
    """Find all files affected by changes to target_path.

    Uses BFS to walk reverse edges (who depends on this file).
    """
    direct = get_dependents(target_path)
    transitive = _bfs_reverse(target_path, get_dependents, max_depth=10)
    transitive_only = [
        path for path in transitive
        if path not in direct and path != target_path
    ]

    return ImpactResult(
        target=target_path,
        direct=direct,
        transitive=transitive_only,
    )


def find_deps(
    target_path: str,
    get_dependencies: callable,
) -> list[str]:
    """Find all transitive dependencies of target_path.

    Uses BFS to walk forward edges (what does this file depend on).
    """
    return _bfs_forward(target_path, get_dependencies, max_depth=10)


def find_hotspots(
    file_paths: list[str],
    get_complexity: callable,
    get_dependents: callable,
    limit: int = 20,
) -> list[HotspotEntry]:
    """Rank files by complexity × dependent count.

    Files that are both complex and heavily depended upon
    are the riskiest to change — they're hotspots.
    """
    entries: list[HotspotEntry] = []

    for path in file_paths:
        complexity = get_complexity(path)
        dependent_count = len(get_dependents(path))
        if complexity == 0 and dependent_count == 0:
            continue
        score = complexity * (1 + dependent_count)
        entries.append(HotspotEntry(
            path=path,
            complexity=complexity,
            dependent_count=dependent_count,
            score=score,
        ))

    entries.sort(key=lambda entry: entry.score, reverse=True)
    return entries[:limit]


def find_call_impact(
    symbol_name: str,
    target_file: str,
    get_callers: callable,
    max_depth: int = 5,
) -> CallImpactResult:
    """Find all functions that call a given symbol, transitively.

    Uses strong names (function, file) throughout to prevent
    false matches on common function names.

    get_callers(name, file) → list[CallEntry]
    """
    direct = [
        CallChain(
            caller_file=call.caller_file,
            caller_function=call.caller_function,
            callee_file=call.callee_file,
            callee_function=call.callee_function,
            line=call.line,
        )
        for call in get_callers(symbol_name, target_file)
    ]

    visited: set[tuple[str, str]] = {(symbol_name, target_file)}
    queue: list[tuple[str, str, int]] = []
    for chain in direct:
        key = (chain.caller_function, chain.caller_file)
        if key not in visited:
            visited.add(key)
            queue.append((chain.caller_function, chain.caller_file, 1))

    transitive: list[CallChain] = []
    while queue:
        current_func, current_file, depth = queue.pop(0)
        if depth >= max_depth:
            continue
        for call in get_callers(current_func, current_file):
            chain = CallChain(
                caller_file=call.caller_file,
                caller_function=call.caller_function,
                callee_file=call.callee_file,
                callee_function=call.callee_function,
                line=call.line,
            )
            transitive.append(chain)
            key = (call.caller_function, call.caller_file)
            if key not in visited:
                visited.add(key)
                queue.append((call.caller_function, call.caller_file, depth + 1))

    return CallImpactResult(
        target_symbol=symbol_name,
        target_file=target_file,
        direct_callers=direct,
        transitive_callers=transitive,
    )


@dataclass
class SymbolImpactResult:
    """Full blast radius of a symbol: call graph + file dependencies."""
    target_symbol: str
    target_file: str
    caller_files: list[str]
    affected_files: list[str]

    @property
    def total_affected(self) -> int:
        return len(self.affected_files)


def find_symbol_impact(
    symbol_name: str,
    target_file: str,
    get_callers: callable,
    get_dependents: callable,
    max_depth: int = 5,
) -> SymbolImpactResult:
    """Full impact of a symbol: trace call graph, then fan out via file deps.

    1. Find all files containing callers (direct + transitive) of the symbol.
    2. For each caller file, walk reverse import edges to find affected files.
    """
    call_result = find_call_impact(
        symbol_name, target_file, get_callers, max_depth,
    )

    caller_files: set[str] = set()
    for chain in call_result.direct_callers:
        caller_files.add(chain.caller_file)
    for chain in call_result.transitive_callers:
        caller_files.add(chain.caller_file)

    affected: set[str] = set(caller_files)
    target_file = call_result.target_file
    if target_file:
        affected.add(target_file)

    for caller_file in caller_files:
        dependents = _bfs_reverse(caller_file, get_dependents, max_depth=5)
        affected.update(dependents)

    affected.discard(target_file)

    return SymbolImpactResult(
        target_symbol=symbol_name,
        target_file=target_file,
        caller_files=sorted(caller_files),
        affected_files=sorted(affected),
    )


def _bfs_reverse(
    start: str,
    get_dependents: callable,
    max_depth: int,
) -> list[str]:
    """BFS over reverse dependency edges."""
    visited: set[str] = {start}
    queue: list[tuple[str, int]] = [(start, 0)]
    result: list[str] = []

    while queue:
        current, depth = queue.pop(0)
        if depth >= max_depth:
            continue
        for dependent in get_dependents(current):
            if dependent not in visited:
                visited.add(dependent)
                result.append(dependent)
                queue.append((dependent, depth + 1))

    return result


def _bfs_forward(
    start: str,
    get_dependencies: callable,
    max_depth: int,
) -> list[str]:
    """BFS over forward dependency edges."""
    visited: set[str] = {start}
    queue: list[tuple[str, int]] = [(start, 0)]
    result: list[str] = []

    while queue:
        current, depth = queue.pop(0)
        if depth >= max_depth:
            continue
        for dependency in get_dependencies(current):
            if dependency not in visited:
                visited.add(dependency)
                result.append(dependency)
                queue.append((dependency, depth + 1))

    return result
