"""Use case: analyze a branch and propose stacked PRs."""

from __future__ import annotations

import os

import networkx as nx

from src.domain.entity.changed_file import ChangedFile
from src.domain.entity.symbol_ref import SymbolRef
from src.domain.entity.proposed_pr import ProposedPR
from src.domain.service.dependency_resolver import build_dependency_edges
from src.domain.factory.pr_set_factory import partition
from src.domain.service.merge_order_resolver import compute_pr_dependencies
from src.domain.policy.risk_policy import compute_risk
from src.domain.policy.merge_strategy_policy import assign_strategies
from src.domain.service.test_pair_finder import find_test_pairs
from src.domain.port.git_reader import GitReader
from src.domain.port.import_parser import ImportParser
from src.domain.port.symbol_extractor import SymbolExtractor
from src.domain.port.complexity_analyzer import ComplexityAnalyzer
from src.domain.port.cochange_provider import CochangeProvider
from src.domain.port.diff_classifier import DiffClassifier
from src.domain.port.definition_resolver import DefinitionResolver
from src.domain.port.text_generator import TextGenerator


class AnalyzeBranchUseCase:
    """Orchestrates the full analysis pipeline."""

    def __init__(
        self,
        git: GitReader,
        parser: ImportParser,
        symbols: SymbolExtractor,
        complexity: ComplexityAnalyzer,
        cochange: CochangeProvider,
        diff_classifier: DiffClassifier,
        text_generator: TextGenerator,
        resolver: DefinitionResolver | None = None,
    ):
        self._git = git
        self._parser = parser
        self._symbols = symbols
        self._complexity = complexity
        self._cochange = cochange
        self._diff_classifier = diff_classifier
        self._text_generator = text_generator
        self._resolver = resolver

    def execute(
        self,
        base: str,
        max_files: int = 3,
        max_code_lines: int = 200,
    ) -> AnalysisResult:
        branch = self._git.current_branch()
        base_ref = self._git.resolve_base(base)
        files = self._git.diff_stat(base_ref)

        if not files:
            return AnalysisResult(branch=branch, base=base, files=[], prs=[], graph=nx.DiGraph())

        self._enrich(files)
        imports_by_file = self._parse_imports(files)
        edges = build_dependency_edges(files, imports_by_file, self._resolver)

        dep_graph = self._build_graph(files, edges)
        topo = self._topo_sort(dep_graph)
        affinity = self._cochange_affinity(files)
        test_pairs = find_test_pairs(files)

        prs = partition(files, topo, test_pairs, affinity, max_files, max_code_lines)

        file_edges = [(s, t) for s, t, _, _ in edges]
        compute_pr_dependencies(prs, file_edges)

        for proposed_pr in prs:
            proposed_pr.risk_score = compute_risk(proposed_pr)
            diff_combined = "\n".join(file.diff_text for file in proposed_pr.files)
            paths = [file.path for file in proposed_pr.files]
            symbols = [sym.name for file in proposed_pr.files for sym in file.symbols_defined]
            title = self._text_generator.generate_title(diff_combined, paths, symbols)
            proposed_pr.title = f"PR #{proposed_pr.index}: {title}"
            proposed_pr.description = self._text_generator.generate_summary(
                diff_combined, paths, proposed_pr.depends_on,
            )

        assign_strategies(prs)

        return AnalysisResult(branch=branch, base=base, files=files, prs=prs, graph=dep_graph)

    def _enrich(self, files: list[ChangedFile]) -> None:
        paths = [file.path for file in files if not file.is_text_or_docs]
        existing = [path for path in paths if os.path.exists(path)]
        if existing:
            metrics = self._complexity.analyze(existing)
            for file in files:
                if file.path in metrics:
                    file.complexity = metrics[file.path]

        for file in files:
            if file.is_text_or_docs:
                continue
            source = self._git.file_source(file.path)
            if source:
                file.symbols_defined = self._symbols.extract(file.path, source)
                added = "\n".join(
                    line[1:] for line in file.diff_text.splitlines()
                    if line.startswith("+") and not line.startswith("+++")
                )
                file.symbols_used = [
                    SymbolRef(name=s.name, kind=s.kind)
                    for s in self._symbols.extract(file.path, added)
                ]
            if file.diff_text:
                file.structural_ratio = self._diff_classifier.structural_ratio(file.path, file.diff_text)

    def _parse_imports(self, files: list[ChangedFile]) -> dict[str, list]:
        result = {}
        for file in files:
            added_lines = {
                line[1:].strip()
                for line in file.diff_text.splitlines()
                if line.startswith("+") and not line.startswith("+++")
            }
            source = self._git.file_source(file.path)
            if not source:
                source = "\n".join(added_lines)
            all_imports = self._parser.parse(file.path, source)
            result[file.path] = [
                imp for imp in all_imports
                if any(imp.raw in al for al in added_lines) or not added_lines
            ]
        return result

    def _build_graph(self, files, edges):
        dep_graph = nx.DiGraph()
        for file in files:
            dep_graph.add_node(file.path, file=file)
        for src, tgt, kind, weight in edges:
            dep_graph.add_edge(src, tgt, kind=kind, weight=weight)
        while not nx.is_directed_acyclic_graph(dep_graph):
            try:
                cycle = nx.find_cycle(dep_graph)
                weakest = min(cycle, key=lambda e: dep_graph.edges[e[0], e[1]].get("weight", 1.0))
                dep_graph.remove_edge(weakest[0], weakest[1])
            except nx.NetworkXNoCycle:
                break
        return dep_graph

    def _topo_sort(self, dep_graph):
        try:
            return list(nx.topological_sort(dep_graph))
        except nx.NetworkXUnfeasible:
            return sorted(dep_graph.nodes(), key=lambda n: -dep_graph.in_degree(n))

    def _cochange_affinity(self, files):
        paths = [file.path for file in files]
        cochange = self._cochange.cochange_history(paths)
        if not cochange:
            return {}
        max_count = max(cochange.values()) or 1
        return {pair: count / max_count for pair, count in cochange.items()}


class AnalysisResult:
    """Result of the analyze branch use case."""

    def __init__(self, branch, base, files, prs, graph):
        self.branch = branch
        self.base = base
        self.files = files
        self.prs = prs
        self.graph = graph
