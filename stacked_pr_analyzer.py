#!/usr/bin/env python3
"""
Stacked PR Analyzer — split a feature branch into reviewable stacked PRs.

Analyzes code dependencies (imports, symbols, co-change history),
complexity metrics (scc), and builds a dependency graph (networkx)
to partition changes into small, mergeable, dependency-ordered PRs.

Tools used:
  - tree-sitter / ast-grep / ctags — multi-language import & symbol parsing
  - scc                             — code complexity & line metrics
  - networkx                        — graph algorithms (toposort, partitioning)
  - git                             — diff, log, rename tracking

Constraints (configurable):
  - ~3 files per PR (soft limit)
  - ~200 lines of code per PR (text/md exempt)
  - Dependency-aware: each PR can merge into base independently

Usage:
    python stacked_pr_analyzer.py [--base main] [--max-files 3] [--max-lines 200]
    python stacked_pr_analyzer.py --json
    python stacked_pr_analyzer.py --visualize       # DOT graph
    python stacked_pr_analyzer.py --generate-plan   # create .stacked-pr-plan.json + shell script
    python stacked_pr_analyzer.py --check-plan      # show plan progress
    python stacked_pr_analyzer.py --run-plan         # execute plan interactively
    python stacked_pr_analyzer.py --run-plan --step 5  # execute single step
    python stacked_pr_analyzer.py --run-plan --dry-run # preview without executing
    python stacked_pr_analyzer.py --print-commands   # print copy-pasteable git commands
    python stacked_pr_analyzer.py --shell-script     # output executable bash script
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import networkx as nx

from lang_parsers import (
    ImportInfo,
    SymbolInfo,
    FileComplexity,
    parse_imports,
    parse_symbols,
    analyze_complexity_best,
)
from plan_executor import (
    StackedPRPlan,
    generate_plan,
    check_plan,
    execute_plan,
    print_commands,
    generate_shell_script,
    PLAN_FILE,
)
from extract_pr import (
    create_extraction_plan,
    print_extraction_plan,
)
from llm_backend import (
    get_llm,
    check_backends,
    build_grouping_prompt,
    build_pr_description_prompt,
    parse_json_response,
    LLMBackend,
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

_TEXT_EXTS = frozenset({
    ".md", ".txt", ".rst", ".adoc", ".csv", ".json", ".yaml",
    ".yml", ".toml", ".ini", ".cfg", ".lock", ".log",
    ".properties", ".env.example",
})


@dataclass
class ChangedFile:
    path: str
    added: int
    removed: int
    status: str  # A/M/D/R
    diff_text: str = ""
    complexity: Optional[FileComplexity] = None
    symbols_defined: list[SymbolInfo] = field(default_factory=list)
    symbols_used: set[str] = field(default_factory=set)

    @property
    def is_text_or_docs(self) -> bool:
        return Path(self.path).suffix.lower() in _TEXT_EXTS

    @property
    def code_lines(self) -> int:
        return self.added + self.removed

    @property
    def module_key(self) -> str:
        parts = Path(self.path).parts
        return "__root__" if len(parts) == 1 else parts[0]

    @property
    def ext(self) -> str:
        return Path(self.path).suffix.lower()

    @property
    def cyclomatic_complexity(self) -> int:
        return self.complexity.complexity if self.complexity else 0


@dataclass
class ProposedPR:
    index: int
    title: str
    files: list[ChangedFile] = field(default_factory=list)
    depends_on: list[int] = field(default_factory=list)
    merge_strategy: str = "squash"
    description: str = ""
    risk_score: float = 0.0

    @property
    def total_code_lines(self) -> int:
        return sum(f.code_lines for f in self.files if not f.is_text_or_docs)

    @property
    def total_all_lines(self) -> int:
        return sum(f.code_lines for f in self.files)

    @property
    def total_complexity(self) -> int:
        return sum(f.cyclomatic_complexity for f in self.files)


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def run_git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{result.stderr}")
    return result.stdout.strip()


def current_branch() -> str:
    return run_git("rev-parse", "--abbrev-ref", "HEAD")


def resolve_base(base: str) -> str:
    for ref in [base, f"origin/{base}"]:
        try:
            run_git("rev-parse", "--verify", ref)
            return ref
        except RuntimeError:
            continue
    raise SystemExit(
        f"Error: base ref '{base}' not found locally or on origin.\n"
        f"Try: git fetch origin {base}"
    )


def merge_base(base_ref: str) -> str:
    return run_git("merge-base", base_ref, "HEAD")


def get_file_source(path: str) -> str:
    try:
        return run_git("show", f"HEAD:{path}")
    except RuntimeError:
        return ""


def diff_stat(base_ref: str) -> list[ChangedFile]:
    mb = merge_base(base_ref)
    # Use --find-renames --find-copies for better tracking
    raw = run_git("diff", "--numstat", "--find-renames", "--find-copies",
                  "--diff-filter=ADMR", mb, "HEAD")
    if not raw:
        return []

    name_status = run_git("diff", "--name-status", "--find-renames", "--find-copies",
                          "--diff-filter=ADMR", mb, "HEAD")
    status_map: dict[str, str] = {}
    for line in name_status.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            status_map[parts[-1]] = parts[0][0]

    files: list[ChangedFile] = []
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added = int(parts[0]) if parts[0] != "-" else 0
        removed = int(parts[1]) if parts[1] != "-" else 0
        path = parts[-1]  # last element handles renames
        diff_text = _file_diff(mb, path)
        files.append(ChangedFile(
            path=path, added=added, removed=removed,
            status=status_map.get(path, "M"), diff_text=diff_text,
        ))
    return files


def _file_diff(mb: str, path: str) -> str:
    try:
        return run_git("diff", mb, "HEAD", "--", path)
    except RuntimeError:
        return ""


# ---------------------------------------------------------------------------
# Git co-change analysis (temporal coupling)
# ---------------------------------------------------------------------------

def analyze_cochange_history(
    file_paths: list[str], max_commits: int = 500,
) -> dict[tuple[str, str], int]:
    """
    Analyze git log to find files that frequently change together.
    Returns {(file_a, file_b): co_change_count}.
    """
    path_set = set(file_paths)
    try:
        log = run_git(
            "log", f"--max-count={max_commits}", "--name-only",
            "--pretty=format:---COMMIT---",
        )
    except RuntimeError:
        return {}

    cochange: dict[tuple[str, str], int] = defaultdict(int)
    current_files: list[str] = []

    for line in log.splitlines():
        if line == "---COMMIT---":
            # Process previous commit's files
            relevant = [f for f in current_files if f in path_set]
            for i in range(len(relevant)):
                for j in range(i + 1, len(relevant)):
                    pair = tuple(sorted([relevant[i], relevant[j]]))
                    cochange[pair] += 1
            current_files = []
        elif line.strip():
            current_files.append(line.strip())

    # Process last commit
    relevant = [f for f in current_files if f in path_set]
    for i in range(len(relevant)):
        for j in range(i + 1, len(relevant)):
            pair = tuple(sorted([relevant[i], relevant[j]]))
            cochange[pair] += 1

    return dict(cochange)


# ---------------------------------------------------------------------------
# Enrichment: complexity + symbols
# ---------------------------------------------------------------------------

def enrich_files(files: list[ChangedFile]) -> None:
    """Add complexity metrics and symbol data to each file."""
    # scc complexity
    paths = [f.path for f in files if not f.is_text_or_docs]
    existing_paths = [p for p in paths if os.path.exists(p)]
    if existing_paths:
        metrics = analyze_complexity_best(existing_paths)
        for f in files:
            if f.path in metrics:
                f.complexity = metrics[f.path]

    # Symbol extraction (defined symbols + used symbols from diff)
    for f in files:
        if f.is_text_or_docs:
            continue
        source = get_file_source(f.path)
        if source:
            f.symbols_defined = parse_symbols(f.path, source)
            # Extract symbols referenced in added lines
            added_source = "\n".join(
                line[1:] for line in f.diff_text.splitlines()
                if line.startswith("+") and not line.startswith("+++")
            )
            added_symbols = parse_symbols(f.path, added_source)
            f.symbols_used = {s.name for s in added_symbols}


# ---------------------------------------------------------------------------
# Dependency graph (networkx)
# ---------------------------------------------------------------------------

def build_dependency_graph(files: list[ChangedFile]) -> nx.DiGraph:
    """
    Build a directed dependency graph among changed files.

    Edges: A → B means "A depends on B" (B must land first).

    Detection layers:
      1. Import-based: file A imports a module that maps to file B
      2. Symbol-based: file A uses a symbol defined in file B
      3. Co-change affinity (weighted, not hard dep)
    """
    G = nx.DiGraph()
    for f in files:
        G.add_node(f.path, file=f)

    # Build module→path and symbol→path indexes
    module_to_path: dict[str, str] = {}
    symbol_to_path: dict[str, set[str]] = defaultdict(set)

    for f in files:
        mod = _path_to_module(f.path)
        module_to_path[mod] = f.path
        short = mod.rsplit(".", 1)[-1]
        module_to_path.setdefault(short, f.path)
        # Register directory-based module (e.g., "src.utils")
        dir_mod = _path_to_module(str(Path(f.path).parent))
        if dir_mod and dir_mod != ".":
            module_to_path.setdefault(dir_mod, f.path)

        for sym in f.symbols_defined:
            symbol_to_path[sym.name].add(f.path)

    # Layer 1: Import-based dependencies
    for f in files:
        imports = _extract_imports(f)
        for imp in imports:
            norm = imp.replace("/", ".").replace("::", ".").lstrip(".")
            for key, target in module_to_path.items():
                if target == f.path:
                    continue
                if norm == key or norm.endswith(f".{key}") or key.endswith(f".{norm}"):
                    G.add_edge(f.path, target, kind="import", weight=1.0)

    # Layer 2: Symbol-based dependencies
    for f in files:
        for sym_name in f.symbols_used:
            providers = symbol_to_path.get(sym_name, set())
            for provider in providers:
                if provider != f.path:
                    if not G.has_edge(f.path, provider):
                        G.add_edge(f.path, provider, kind="symbol", weight=0.8)

    # Break cycles (keep strongest edges)
    while not nx.is_directed_acyclic_graph(G):
        try:
            cycle = nx.find_cycle(G)
            # Remove the weakest edge in the cycle
            weakest = min(cycle, key=lambda e: G.edges[e[0], e[1]].get("weight", 1.0))
            G.remove_edge(weakest[0], weakest[1])
        except nx.NetworkXNoCycle:
            break

    return G


def add_cochange_affinity(
    G: nx.DiGraph, files: list[ChangedFile],
) -> dict[tuple[str, str], float]:
    """
    Compute co-change affinity scores. Files that historically change
    together should be in the same PR when possible.
    Returns affinity scores (higher = more coupled).
    """
    paths = [f.path for f in files]
    cochange = analyze_cochange_history(paths)
    affinity: dict[tuple[str, str], float] = {}
    if cochange:
        max_count = max(cochange.values()) or 1
        for pair, count in cochange.items():
            affinity[pair] = count / max_count
    return affinity


def _extract_imports(file: ChangedFile) -> set[str]:
    """Extract imports from a file's added diff lines."""
    added_lines = set()
    for line in file.diff_text.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added_lines.add(line[1:].strip())

    full_source = get_file_source(file.path)
    if not full_source:
        full_source = "\n".join(
            line[1:] for line in file.diff_text.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )

    import_infos = parse_imports(file.path, full_source)
    result: set[str] = set()
    for info in import_infos:
        if any(info.raw in added for added in added_lines) or not added_lines:
            result.add(info.module)
    return result


def _path_to_module(path: str) -> str:
    p = Path(path).with_suffix("")
    return str(p).replace("/", ".").replace("\\", ".")


# ---------------------------------------------------------------------------
# Graph partitioning → PR groups
# ---------------------------------------------------------------------------

def _label_for_files(files: list[ChangedFile]) -> str:
    modules = {f.module_key for f in files}
    if all(f.is_text_or_docs for f in files):
        return "docs/config updates"
    if len(modules) == 1:
        mod = next(iter(modules))
        return "root-level changes" if mod == "__root__" else f"{mod} changes"
    return "cross-module changes"


# ---------------------------------------------------------------------------
# Test file pairing
# ---------------------------------------------------------------------------

# Patterns: test_foo.py ↔ foo.py, foo_test.go ↔ foo.go, Foo.test.tsx ↔ Foo.tsx
_TEST_PATTERNS = [
    # Python: test_foo.py ↔ foo.py
    (re.compile(r"^(.*/)?test_(.+)\.py$"), r"\1\2.py"),
    (re.compile(r"^(.*/)?(.+)_test\.py$"), r"\1\2.py"),
    # Go: foo_test.go ↔ foo.go
    (re.compile(r"^(.*/)?(.+)_test\.go$"), r"\1\2.go"),
    # JS/TS: foo.test.ts ↔ foo.ts, foo.spec.ts ↔ foo.ts
    (re.compile(r"^(.*/)?(.+)\.(test|spec)\.(tsx?|jsx?)$"), r"\1\2.\4"),
    # Java/Kotlin: FooTest.java ↔ Foo.java
    (re.compile(r"^(.*/)?(.+)Test\.(java|kt|kts)$"), r"\1\2.\3"),
    # C++: foo_test.cpp ↔ foo.cpp, test_foo.cpp ↔ foo.cpp
    (re.compile(r"^(.*/)?(.+)_test\.(cpp|cc|cxx)$"), r"\1\2.\3"),
    (re.compile(r"^(.*/)?test_(.+)\.(cpp|cc|cxx)$"), r"\1\2.\3"),
    # Rust: mod tests in same file (handled by module), but also test files
    (re.compile(r"^(.*/)tests/(.+)\.rs$"), r"\1src/\2.rs"),
    # __tests__ directory (JS/TS convention)
    (re.compile(r"^(.*/)?__tests__/(.+)\.(tsx?|jsx?)$"), r"\1\2.\3"),
]


def find_test_pairs(files: list[ChangedFile]) -> dict[str, str]:
    """
    Find test→implementation file pairs among changed files.
    Returns {test_path: impl_path} for pairs where both exist in the changeset.
    """
    all_paths = {f.path for f in files}
    pairs: dict[str, str] = {}

    for f in files:
        for pattern, replacement in _TEST_PATTERNS:
            m = pattern.match(f.path)
            if m:
                impl_path = pattern.sub(replacement, f.path)
                if impl_path in all_paths and impl_path != f.path:
                    pairs[f.path] = impl_path
                break  # first match wins

    return pairs


def partition_into_prs(
    G: nx.DiGraph,
    files: list[ChangedFile],
    affinity: dict[tuple[str, str], float],
    max_files: int = 3,
    max_code_lines: int = 200,
) -> list[ProposedPR]:
    """
    Partition the dependency graph into PR-sized groups.

    Algorithm:
      1. Find test↔implementation pairs (hard constraint: keep together)
      2. Topological sort (dep-free files first)
      3. Greedy bin-packing with affinity-aware neighbor preference
      4. Co-changing files get priority to land in same PR
    """
    if not files:
        return []

    file_map = {f.path: f for f in files}

    # Test file pairing — these must land in the same PR
    test_pairs = find_test_pairs(files)
    # Reverse map: impl → [test_paths]
    impl_to_tests: dict[str, list[str]] = defaultdict(list)
    for test_path, impl_path in test_pairs.items():
        impl_to_tests[impl_path].append(test_path)

    # Topological ordering — depended-upon files come first
    try:
        topo_order = list(nx.topological_sort(G))
    except nx.NetworkXUnfeasible:
        topo_order = sorted(G.nodes(), key=lambda n: -G.in_degree(n))

    # Add files not in graph
    all_paths = {f.path for f in files}
    graph_paths = set(topo_order)
    remaining_paths = all_paths - graph_paths
    remaining_sorted = sorted(
        remaining_paths,
        key=lambda p: (not file_map[p].is_text_or_docs, file_map[p].module_key, p),
    )
    ordered_paths = topo_order + remaining_sorted

    # Greedy bin-packing with affinity
    prs: list[ProposedPR] = []
    assigned: set[str] = set()

    def _start_pr() -> ProposedPR:
        pr = ProposedPR(index=len(prs) + 1, title="", files=[])
        prs.append(pr)
        return pr

    def _pr_affinity(pr: ProposedPR, path: str) -> float:
        """How much does this file want to be in this PR?"""
        score = 0.0
        for existing in pr.files:
            pair = tuple(sorted([existing.path, path]))
            score += affinity.get(pair, 0.0)
            # Same directory bonus
            if Path(existing.path).parent == Path(path).parent:
                score += 0.3
            # Same module bonus
            if existing.module_key == file_map[path].module_key:
                score += 0.2
        return score

    current_pr: Optional[ProposedPR] = None

    for path in ordered_paths:
        if path in assigned:
            continue

        f = file_map[path]
        code_contribution = 0 if f.is_text_or_docs else f.code_lines

        # Check if current PR can accept this file
        can_fit = (
            current_pr is not None
            and len(current_pr.files) < max_files
            and (f.is_text_or_docs
                 or current_pr.total_code_lines + code_contribution <= max_code_lines
                 or current_pr.total_code_lines == 0)
        )

        if not can_fit:
            # Check if any existing non-full PR has high affinity
            best_pr = None
            best_score = 0.0
            for pr in prs:
                if len(pr.files) >= max_files:
                    continue
                if not f.is_text_or_docs and pr.total_code_lines + code_contribution > max_code_lines and pr.total_code_lines > 0:
                    continue
                score = _pr_affinity(pr, path)
                if score > best_score:
                    best_score = score
                    best_pr = pr

            if best_pr and best_score > 0.3:
                current_pr = best_pr
            else:
                current_pr = _start_pr()

        current_pr.files.append(f)
        assigned.add(path)

        # Pull in paired test files (hard constraint)
        for test_path in impl_to_tests.get(path, []):
            if test_path not in assigned and test_path in file_map:
                current_pr.files.append(file_map[test_path])
                assigned.add(test_path)
        # Also pull impl if this is a test file
        impl_path = test_pairs.get(path)
        if impl_path and impl_path not in assigned and impl_path in file_map:
            current_pr.files.append(file_map[impl_path])
            assigned.add(impl_path)

    for pr in prs:
        pr.title = f"PR #{pr.index}: {_label_for_files(pr.files)}"

    return prs


# ---------------------------------------------------------------------------
# LLM-powered grouping (optional, enhances partition_into_prs)
# ---------------------------------------------------------------------------

def partition_with_llm(
    files: list[ChangedFile],
    G: nx.DiGraph,
    max_files: int = 3,
    max_code_lines: int = 200,
) -> list[ProposedPR] | None:
    """
    Use a local LLM to semantically group files into PRs.
    Returns None if LLM is unavailable (caller should use rule-based).
    """
    llm = get_llm(verbose=False)
    if not llm.available:
        return None

    # Build file summaries
    file_summaries = []
    for f in files:
        file_summaries.append({
            "path": f.path,
            "status": f.status,
            "added": f.added,
            "removed": f.removed,
            "module": f.module_key,
            "is_docs": f.is_text_or_docs,
        })

    # Build dependency edges
    dep_edges = [(u, v) for u, v in G.edges()]

    prompt = build_grouping_prompt(file_summaries, dep_edges, max_files, max_code_lines)
    response = llm.query(prompt, json_mode=True, max_tokens=4096)

    if not response.ok:
        return None

    data = parse_json_response(response)
    if not data or not isinstance(data, dict) or "prs" not in data:
        return None

    # Convert LLM output to ProposedPR objects
    file_map = {f.path: f for f in files}
    prs: list[ProposedPR] = []

    for i, pr_data in enumerate(data["prs"], 1):
        pr_files = []
        for path in pr_data.get("files", []):
            if path in file_map:
                pr_files.append(file_map[path])

        if not pr_files:
            continue

        prs.append(ProposedPR(
            index=i,
            title=pr_data.get("title", f"PR #{i}"),
            files=pr_files,
            depends_on=pr_data.get("depends_on", []),
            merge_strategy=pr_data.get("merge_strategy", "squash"),
        ))

    # Catch any unassigned files
    assigned = {f.path for pr in prs for f in pr.files}
    unassigned = [f for f in files if f.path not in assigned]
    if unassigned:
        prs.append(ProposedPR(
            index=len(prs) + 1,
            title=f"PR #{len(prs) + 1}: remaining changes",
            files=unassigned,
        ))

    return prs if prs else None


def generate_pr_descriptions_llm(
    prs: list[ProposedPR],
    source_branch: str,
) -> dict[int, str]:
    """
    Use a local LLM to generate PR descriptions.
    Returns {pr_index: description_markdown}.
    """
    llm = get_llm(verbose=False)
    if not llm.available:
        return {}

    descriptions: dict[int, str] = {}
    for pr in prs:
        file_dicts = [
            {"path": f.path, "status": f.status, "added": f.added, "removed": f.removed}
            for f in pr.files
        ]

        # Build diff summary from first few files
        diff_summary = ""
        for f in pr.files[:3]:
            added_lines = [
                line[1:] for line in f.diff_text.splitlines()
                if line.startswith("+") and not line.startswith("+++")
            ][:10]
            if added_lines:
                diff_summary += f"\n--- {f.path} ---\n" + "\n".join(added_lines)

        deps = [f"PR #{d}" for d in pr.depends_on]

        prompt = build_pr_description_prompt(
            title=pr.title,
            files=file_dicts,
            diff_summary=diff_summary,
            depends_on=deps,
            merge_strategy=pr.merge_strategy,
        )
        response = llm.query(prompt, max_tokens=1024)
        if response.ok:
            descriptions[pr.index] = response.text

    return descriptions


# ---------------------------------------------------------------------------
# PR dependency edges & merge strategies
# ---------------------------------------------------------------------------

def compute_pr_dependencies(prs: list[ProposedPR], G: nx.DiGraph) -> None:
    file_to_pr: dict[str, int] = {}
    for pr in prs:
        for f in pr.files:
            file_to_pr[f.path] = pr.index

    for pr in prs:
        dep_prs: set[int] = set()
        for f in pr.files:
            for _, dep_path in G.out_edges(f.path):
                dep_idx = file_to_pr.get(dep_path)
                if dep_idx and dep_idx != pr.index:
                    dep_prs.add(dep_idx)
        pr.depends_on = sorted(dep_prs)


def compute_risk_scores(prs: list[ProposedPR]) -> None:
    """
    Risk score per PR based on:
      - Code line count (normalized)
      - Cyclomatic complexity
      - Number of cross-PR dependencies
      - File type diversity
    """
    for pr in prs:
        line_risk = min(pr.total_code_lines / 500.0, 1.0)
        complexity_risk = min(pr.total_complexity / 50.0, 1.0)
        dep_risk = min(len(pr.depends_on) / 5.0, 1.0)
        ext_diversity = len({f.ext for f in pr.files if not f.is_text_or_docs})
        diversity_risk = min(ext_diversity / 4.0, 1.0)
        pr.risk_score = round(
            0.3 * line_risk + 0.3 * complexity_risk + 0.2 * dep_risk + 0.2 * diversity_risk,
            2,
        )


def assign_merge_strategies(prs: list[ProposedPR]) -> None:
    depended_on: set[int] = set()
    for pr in prs:
        depended_on.update(pr.depends_on)

    for pr in prs:
        all_docs = all(f.is_text_or_docs for f in pr.files)
        has_dependents = pr.index in depended_on

        if has_dependents:
            pr.merge_strategy = "merge"
            pr.description = (
                "Use **merge commit** — later PRs depend on this one, "
                "preserving the merge point simplifies rebasing."
            )
        elif all_docs:
            pr.merge_strategy = "rebase"
            pr.description = "Use **rebase** — docs/config only, linear history."
        elif pr.risk_score > 0.6:
            pr.merge_strategy = "merge"
            pr.description = (
                "Use **merge commit** — high complexity, "
                "preserves full context for bisect/revert."
            )
        else:
            pr.merge_strategy = "squash"
            pr.description = "Use **squash merge** — clean single commit on main."


# ---------------------------------------------------------------------------
# Visualization (DOT / graphviz)
# ---------------------------------------------------------------------------

def generate_dot(prs: list[ProposedPR]) -> str:
    lines = ['digraph stacked_prs {', '  rankdir=BT;', '  node [shape=box, style=rounded];']

    color_map = {"squash": "#4CAF50", "merge": "#FF9800", "rebase": "#2196F3"}

    for pr in prs:
        color = color_map.get(pr.merge_strategy, "#999")
        file_list = "\\n".join(f.path for f in pr.files[:5])
        if len(pr.files) > 5:
            file_list += f"\\n... +{len(pr.files) - 5} more"
        label = f"{pr.title}\\n{file_list}\\n[{pr.merge_strategy}] risk={pr.risk_score}"
        lines.append(f'  pr{pr.index} [label="{label}", fillcolor="{color}", style="rounded,filled", fontcolor="white"];')

    for pr in prs:
        for dep in pr.depends_on:
            lines.append(f"  pr{pr.index} -> pr{dep};")

    lines.append("}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Output / reporting
# ---------------------------------------------------------------------------

RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RED = "\033[31m"
DIM = "\033[2m"


def _risk_color(score: float) -> str:
    if score < 0.3:
        return GREEN
    elif score < 0.6:
        return YELLOW
    return RED


def print_report(
    prs: list[ProposedPR], base: str, branch: str, total_files: int,
    G: nx.DiGraph,
) -> None:
    print()
    print(f"{BOLD}{'=' * 70}{RESET}")
    print(f"{BOLD}  Stacked PR Analysis{RESET}")
    print(f"{BOLD}{'=' * 70}{RESET}")
    print(f"  Branch          : {CYAN}{branch}{RESET}")
    print(f"  Base            : {CYAN}{base}{RESET}")
    print(f"  Changed files   : {total_files}")
    print(f"  Proposed PRs    : {GREEN}{len(prs)}{RESET}")
    print(f"  Dependency edges: {G.number_of_edges()} file-level, "
          f"{sum(len(pr.depends_on) for pr in prs)} PR-level")
    print(f"{BOLD}{'=' * 70}{RESET}")

    for pr in prs:
        rc = _risk_color(pr.risk_score)
        print()
        print(f"  {BOLD}{pr.title}{RESET}  {rc}risk={pr.risk_score}{RESET}")
        print(f"  {DIM}{'─' * 60}{RESET}")
        if pr.depends_on:
            dep_str = ", ".join(f"PR #{d}" for d in pr.depends_on)
            print(f"    Depends on : {YELLOW}{dep_str}{RESET}")
        else:
            print(f"    Depends on : {DIM}(none — merge independently){RESET}")
        print(f"    Strategy   : {GREEN}{pr.merge_strategy}{RESET}")
        print(f"    {DIM}{pr.description}{RESET}")
        print(f"    Code lines : {pr.total_code_lines}  |  Total: {pr.total_all_lines}  |  Complexity: {pr.total_complexity}")
        print(f"    Files ({len(pr.files)}):")
        for f in pr.files:
            tag = f"[{f.status}]"
            cx = f" cx={f.cyclomatic_complexity}" if f.cyclomatic_complexity else ""
            doc_tag = f" {DIM}(docs — no line budget){RESET}" if f.is_text_or_docs else ""
            print(f"      {tag:4s} {f.path}  (+{f.added}/-{f.removed}){cx}{doc_tag}")

    # Merge order
    print()
    print(f"  {BOLD}Recommended merge order:{RESET}")
    print(f"  {DIM}{'─' * 60}{RESET}")
    merged: set[int] = set()
    order = 1
    remaining = list(prs)
    while remaining:
        ready = [pr for pr in remaining if all(d in merged for d in pr.depends_on)]
        if not ready:
            print(f"  {YELLOW}WARNING: circular dependency among remaining PRs{RESET}")
            for pr in remaining:
                print(f"    PR #{pr.index} depends on {pr.depends_on}")
            break
        # Within a level, sort by risk (review risky ones first)
        ready.sort(key=lambda pr: -pr.risk_score)
        for pr in ready:
            deps_note = ""
            if pr.depends_on:
                deps_note = f"  (after {', '.join(f'#{d}' for d in pr.depends_on)})"
            rc = _risk_color(pr.risk_score)
            print(f"  {order}. {pr.title}  [{pr.merge_strategy}] {rc}risk={pr.risk_score}{RESET}{deps_note}")
            merged.add(pr.index)
            remaining.remove(pr)
            order += 1

    # Parallelism opportunities
    print()
    print(f"  {BOLD}Parallelism:{RESET}")
    print(f"  {DIM}{'─' * 60}{RESET}")
    merged2: set[int] = set()
    remaining2 = list(prs)
    wave = 1
    while remaining2:
        ready2 = [pr for pr in remaining2 if all(d in merged2 for d in pr.depends_on)]
        if not ready2:
            break
        if len(ready2) > 1:
            names = ", ".join(f"PR #{pr.index}" for pr in ready2)
            print(f"  Wave {wave}: {names}  (can review/merge in parallel)")
        else:
            print(f"  Wave {wave}: PR #{ready2[0].index}  (sequential)")
        for pr in ready2:
            merged2.add(pr.index)
            remaining2.remove(pr)
        wave += 1

    print()
    print(f"  {BOLD}Strategy legend:{RESET}")
    print(f"  {DIM}{'─' * 60}{RESET}")
    print(f"  {GREEN}squash{RESET}  — self-contained, clean single commit")
    print(f"  {YELLOW}merge{RESET}   — has dependents or high complexity, preserve context")
    print(f"  {CYAN}rebase{RESET}  — docs/config only, linear history")
    print(f"  After merging each wave, rebase remaining PRs onto updated base")
    print()


def print_report_json(prs: list[ProposedPR], base: str, branch: str, G: nx.DiGraph) -> None:
    data = {
        "branch": branch,
        "base": base,
        "total_prs": len(prs),
        "file_dependency_edges": G.number_of_edges(),
        "prs": [
            {
                "index": pr.index,
                "title": pr.title,
                "files": [
                    {
                        "path": f.path,
                        "status": f.status,
                        "added": f.added,
                        "removed": f.removed,
                        "is_docs": f.is_text_or_docs,
                        "complexity": f.cyclomatic_complexity,
                    }
                    for f in pr.files
                ],
                "code_lines": pr.total_code_lines,
                "total_lines": pr.total_all_lines,
                "complexity": pr.total_complexity,
                "depends_on": pr.depends_on,
                "merge_strategy": pr.merge_strategy,
                "risk_score": pr.risk_score,
            }
            for pr in prs
        ],
    }
    print(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _run_analysis(args) -> tuple[str, str, list[ChangedFile], nx.DiGraph, list, dict]:
    """Shared analysis logic for all modes."""
    branch = current_branch()
    base_ref = resolve_base(args.base)

    print(f"Analyzing {branch} vs {base_ref}...", file=sys.stderr)

    files = diff_stat(base_ref)
    if not files:
        print(f"No changes found between {base_ref} and HEAD.")
        sys.exit(0)

    print(f"  Found {len(files)} changed files, enriching...", file=sys.stderr)
    enrich_files(files)

    print(f"  Building dependency graph...", file=sys.stderr)
    G = build_dependency_graph(files)

    print(f"  Analyzing co-change history...", file=sys.stderr)
    affinity = add_cochange_affinity(G, files)

    # Try LLM-powered grouping if --ai is set
    prs = None
    use_ai = getattr(args, "ai", False)
    if use_ai:
        print(f"  Using LLM for semantic grouping...", file=sys.stderr)
        prs = partition_with_llm(G=G, files=files, max_files=args.max_files, max_code_lines=args.max_lines)
        if prs:
            print(f"  LLM grouped {len(files)} files into {len(prs)} PRs", file=sys.stderr)

    # Fall back to rule-based partitioning
    if not prs:
        if use_ai:
            print(f"  LLM unavailable, using rule-based partitioning...", file=sys.stderr)
        else:
            print(f"  Partitioning into PRs...", file=sys.stderr)
        prs = partition_into_prs(G, files, affinity, args.max_files, args.max_lines)

    compute_pr_dependencies(prs, G)
    compute_risk_scores(prs)
    assign_merge_strategies(prs)

    # Generate LLM descriptions if --ai
    if use_ai:
        descriptions = generate_pr_descriptions_llm(prs, branch)
        for pr in prs:
            if pr.index in descriptions:
                pr.description = descriptions[pr.index]

    return branch, base_ref, files, G, prs, affinity


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze a branch and propose stacked PRs with merge strategies.",
    )
    # Analysis options
    parser.add_argument("--base", default="main", help="Base branch (default: main)")
    parser.add_argument("--max-files", type=int, default=3, help="Soft max files per PR")
    parser.add_argument("--max-lines", type=int, default=200, help="Max code lines per PR (docs exempt)")

    # Output modes
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--visualize", action="store_true", help="Output DOT graph to stdout")
    parser.add_argument("--dot-file", type=str, help="Write DOT graph to file")

    # Plan generation & execution
    parser.add_argument("--generate-plan", action="store_true",
                        help="Generate execution plan (.stacked-pr-plan.json + commands)")
    parser.add_argument("--check-plan", action="store_true",
                        help="Show current plan status and next steps")
    parser.add_argument("--run-plan", action="store_true",
                        help="Execute the plan (all pending steps)")
    parser.add_argument("--step", type=int, default=None,
                        help="Execute only this step number (with --run-plan)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview commands without executing (with --run-plan)")
    parser.add_argument("--no-interactive", action="store_true",
                        help="Don't prompt before each PR (with --run-plan)")
    parser.add_argument("--print-commands", action="store_true",
                        help="Print all git commands (copy-pasteable)")
    parser.add_argument("--shell-script", action="store_true",
                        help="Output a complete bash script")
    parser.add_argument("--no-gh", action="store_true",
                        help="Skip GitHub PR creation commands")
    parser.add_argument("--plan-file", type=str, default=PLAN_FILE,
                        help=f"Plan file path (default: {PLAN_FILE})")

    # Extraction mode
    parser.add_argument("--extract", type=str, default=None, metavar="PROMPT",
                        help='Extract subset into a PR: --extract "gradle init-script files"')
    parser.add_argument("--extract-branch", type=str, default=None,
                        help="Custom branch name for extraction")
    parser.add_argument("--no-deps", action="store_true",
                        help="Don't pull in dependency files during extraction")

    # AI / LLM options
    parser.add_argument("--ai", action="store_true",
                        help="Use local LLM (ollama/llama-cpp) for smarter analysis")
    parser.add_argument("--ai-backend", type=str, default=None,
                        choices=["ollama", "llama-cpp", "none"],
                        help="Force a specific LLM backend")
    parser.add_argument("--ai-model", type=str, default=None,
                        help="Force a specific model (e.g., qwen2.5-coder:7b)")
    parser.add_argument("--ai-pull", action="store_true",
                        help="Auto-pull model via ollama if not available")
    parser.add_argument("--ai-check", action="store_true",
                        help="Check available LLM backends and models")
    parser.add_argument("--setup", action="store_true",
                        help="Setup ollama (install, start, pull model)")
    parser.add_argument("--setup-minimal", action="store_true",
                        help="Setup with smallest model (~1.6 GB)")

    args = parser.parse_args()

    # ── Setup ollama ──
    if args.setup or args.setup_minimal:
        from setup_ollama import run_setup
        success = run_setup(
            model=args.ai_model,
            minimal=args.setup_minimal,
        )
        sys.exit(0 if success else 1)

    # ── AI check (no analysis needed) ──
    if args.ai_check:
        results = check_backends()
        print(f"\n{BOLD}  LLM Backend Status{RESET}")
        print(f"  {'─' * 50}")
        for name, info in results.items():
            avail = f"{GREEN}available{RESET}" if info.get("available") else f"{RED}unavailable{RESET}"
            print(f"  {name:12s}: {avail}")
            for k, v in info.items():
                if k == "available":
                    continue
                if k == "models" and isinstance(v, list):
                    print(f"    {k}: {', '.join(v[:10])}" + (f" (+{len(v)-10} more)" if len(v) > 10 else ""))
                else:
                    print(f"    {k}: {v}")
        print()
        print(f"  {BOLD}Usage:{RESET}")
        print(f"    {CYAN}python stacked_pr_analyzer.py --ai{RESET}                    # auto-detect")
        print(f"    {CYAN}python stacked_pr_analyzer.py --ai --ai-model qwen2.5-coder:7b{RESET}")
        print(f"    {CYAN}python stacked_pr_analyzer.py --ai --ai-pull{RESET}           # auto-pull model")
        print()
        print(f"  {BOLD}Install ollama:{RESET}")
        print(f"    macOS:  {CYAN}brew install ollama && ollama serve{RESET}")
        print(f"    Linux:  {CYAN}curl -fsSL https://ollama.com/install.sh | sh{RESET}")
        print(f"    WSL2:   {CYAN}curl -fsSL https://ollama.com/install.sh | sh{RESET}")
        print(f"    Then:   {CYAN}ollama pull qwen2.5-coder:7b{RESET}")
        print()
        return

    # Initialize LLM if --ai flag is set
    if args.ai:
        get_llm(backend=args.ai_backend, model=args.ai_model, auto_pull=args.ai_pull)

    # ── Check plan (no analysis needed) ──
    if args.check_plan:
        plan = StackedPRPlan.load(args.plan_file)
        check_plan(plan)
        return

    # ── Run plan (no analysis needed) ──
    if args.run_plan:
        plan = StackedPRPlan.load(args.plan_file)
        success = execute_plan(
            plan,
            step_id=args.step,
            dry_run=args.dry_run,
            interactive=not args.no_interactive,
            plan_path=args.plan_file,
        )
        sys.exit(0 if success else 1)

    # ── Extraction mode (prompt-driven) ──
    if args.extract:
        branch = current_branch()
        base_ref = resolve_base(args.base)
        print(f"Extracting from {branch} vs {base_ref}...", file=sys.stderr)

        files = diff_stat(base_ref)
        if not files:
            print(f"No changes found between {base_ref} and HEAD.")
            sys.exit(0)

        enrich_files(files)
        G = build_dependency_graph(files)

        plan = create_extraction_plan(
            prompt=args.extract,
            all_files=files,
            G=G,
            source_branch=branch,
            base_branch=args.base,
            branch_name=args.extract_branch,
            include_deps=not args.no_deps,
            use_llm=getattr(args, "ai", False),
        )
        print_extraction_plan(plan)

        if not plan.all_files:
            print(f"\n  {YELLOW}No files matched the prompt. Try a different query.{RESET}")
            sys.exit(1)

        return

    # ── All other modes require analysis ──
    branch, base_ref, files, G, prs, affinity = _run_analysis(args)

    # ── Generate plan ──
    if args.generate_plan or args.print_commands or args.shell_script:
        plan = generate_plan(
            prs=prs,
            source_branch=branch,
            base_branch=args.base,
            repo_root=os.getcwd(),
            create_prs=not args.no_gh,
        )
        plan.save(args.plan_file)
        print(f"Plan saved to {args.plan_file}", file=sys.stderr)

        if args.shell_script:
            print(generate_shell_script(plan, include_gh=not args.no_gh))
        elif args.print_commands:
            print_commands(plan, include_gh=not args.no_gh)
        else:
            # Default: show report + commands + mermaid
            print_report(prs, args.base, branch, len(files), G)
            print()
            print_commands(plan, include_gh=not args.no_gh)
            if plan.mermaid:
                print(f"\n  {BOLD}Mermaid Diagram (paste into GitHub):{RESET}\n")
                for line in plan.mermaid.splitlines():
                    print(f"  {line}")
                print()
            print(f"  {BOLD}Next steps:{RESET}")
            print(f"    Review:  {CYAN}python stacked_pr_analyzer.py --check-plan{RESET}")
            print(f"    Execute: {CYAN}python stacked_pr_analyzer.py --run-plan{RESET}")
            print(f"    Dry run: {CYAN}python stacked_pr_analyzer.py --run-plan --dry-run{RESET}")
            print()
        return

    # ── Visualization ──
    if args.visualize or args.dot_file:
        dot = generate_dot(prs)
        if args.dot_file:
            Path(args.dot_file).write_text(dot)
            print(f"DOT graph written to {args.dot_file}", file=sys.stderr)
        else:
            print(dot)
        if not args.json and not args.visualize:
            return

    # ── Default output ──
    if args.json:
        print_report_json(prs, args.base, branch, G)
    elif not args.visualize:
        print_report(prs, args.base, branch, len(files), G)


if __name__ == "__main__":
    main()
