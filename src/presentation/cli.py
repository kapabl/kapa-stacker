"""Presentation: CLI entry point. No business logic here."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.infrastructure.git.git_client import GitClient
from src.infrastructure.git.command_executor import ShellCommandRunner
from src.infrastructure.parsers.multi_lang_parser import MultiLangImportParser, MultiLangSymbolExtractor
from src.infrastructure.complexity.analyzer import LizardSccAnalyzer
from src.infrastructure.llm.ollama_backend import OllamaLLMService, NullLLMService, check_llm_backends
from src.infrastructure.persistence.json_plan_store import JsonPlanStore

from src.application.analyze_branch import AnalyzeBranchUseCase
from src.application.extract_files import ExtractFilesUseCase
from src.application.generate_plan import GeneratePlanUseCase
from src.application.execute_plan import ExecutePlanUseCase

from src.presentation.reporters.text_reporter import print_analysis
from src.presentation.reporters.json_reporter import print_json
from src.presentation.reporters.dot_reporter import generate_dot
from src.presentation.reporters.plan_reporter import print_plan_status, print_commands, generate_shell_script
from src.presentation.reporters.extraction_reporter import print_extraction

BOLD = "\033[0m\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
RESET = "\033[0m"


def main() -> None:
    args = _parse_args()

    # ── Setup (installs ALL deps: ollama, ctags, scc, ast-grep, lizard) ──
    if args.setup or args.setup_minimal:
        from src.infrastructure.setup import run_full_setup
        success = run_full_setup(ollama_model=args.ai_model, minimal=args.setup_minimal)
        sys.exit(0 if success else 1)

    # ── Index (pre-compute ctags, imports, co-change, complexity) ──
    if args.index:
        from src.infrastructure.indexer.index_all import index_repo
        index_repo()
        return

    # ── AI check ──
    if args.ai_check:
        _print_ai_status()
        return

    # ── Build dependencies ──
    git = GitClient()
    store = JsonPlanStore(args.plan_file)
    llm = _build_llm(args)

    # ── Check plan ──
    if args.check_plan:
        print_plan_status(store.load())
        return

    # ── Run plan ──
    if args.run_plan:
        runner = ShellCommandRunner()
        uc = ExecutePlanUseCase(runner, store)
        ok = uc.execute(store.load(), step_id=args.step, dry_run=args.dry_run)
        sys.exit(0 if ok else 1)

    # ── Extract ──
    if args.extract:
        result = _run_extraction(args, git, llm)
        print_extraction(result)
        if not result.all_files:
            print(f"  {YELLOW}No files matched. Try a different query.{RESET}")
            sys.exit(1)
        return

    # ── Analyze ──
    analysis = _run_analysis(args, git, llm)
    if not analysis.files:
        print("No changes found.")
        sys.exit(0)

    # ── Output ──
    if args.generate_plan or args.print_commands or args.shell_script:
        plan_uc = GeneratePlanUseCase()
        plan = plan_uc.execute(
            analysis.prs, analysis.branch, args.base,
            create_github_prs=not args.no_gh,
        )
        store.save(plan)
        print(f"Plan saved to {args.plan_file}", file=sys.stderr)

        if args.shell_script:
            print(generate_shell_script(plan))
        elif args.print_commands:
            print_commands(plan)
        else:
            print_analysis(analysis.prs, analysis.branch, args.base, len(analysis.files), analysis.graph)
            print_commands(plan)
            if plan.mermaid:
                print(f"\n  {BOLD}Mermaid:{RESET}\n")
                for line in plan.mermaid.splitlines():
                    print(f"  {line}")
                print()
        return

    if args.visualize or args.dot_file:
        dot = generate_dot(analysis.prs)
        if args.dot_file:
            Path(args.dot_file).write_text(dot)
        else:
            print(dot)
        if args.json:
            print_json(analysis.prs, args.base, analysis.branch, analysis.graph)
        return

    if args.json:
        print_json(analysis.prs, args.base, analysis.branch, analysis.graph)
    else:
        print_analysis(analysis.prs, analysis.branch, args.base, len(analysis.files), analysis.graph)


def _parse_args():
    p = argparse.ArgumentParser(description="Split branches into stacked PRs.")

    p.add_argument("--base", default="main")
    p.add_argument("--max-files", type=int, default=3)
    p.add_argument("--max-lines", type=int, default=200)

    p.add_argument("--json", action="store_true")
    p.add_argument("--visualize", action="store_true")
    p.add_argument("--dot-file", type=str)

    p.add_argument("--generate-plan", action="store_true")
    p.add_argument("--check-plan", action="store_true")
    p.add_argument("--run-plan", action="store_true")
    p.add_argument("--step", type=int, default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--print-commands", action="store_true")
    p.add_argument("--shell-script", action="store_true")
    p.add_argument("--no-gh", action="store_true")
    p.add_argument("--plan-file", default=".stacked-pr-plan.json")

    p.add_argument("--extract", type=str, metavar="PROMPT")
    p.add_argument("--extract-branch", type=str)
    p.add_argument("--no-deps", action="store_true")

    # AI is ON by default. Use --no-ai to disable.
    p.add_argument("--no-ai", action="store_true", help="Disable local LLM")
    p.add_argument("--ai-backend", type=str, choices=["ollama", "llama-cpp", "none"])
    p.add_argument("--ai-model", type=str)
    p.add_argument("--ai-pull", action="store_true")
    p.add_argument("--ai-check", action="store_true")

    p.add_argument("--setup", action="store_true",
                   help="Install all deps (ollama, ctags, scc, ast-grep, lizard)")
    p.add_argument("--setup-minimal", action="store_true",
                   help="Setup with smallest LLM model")
    p.add_argument("--index", action="store_true",
                   help="Pre-compute caches (ctags, imports, co-change, complexity)")

    return p.parse_args()


def _build_llm(args):
    if args.no_ai or args.ai_backend == "none":
        return NullLLMService()
    return OllamaLLMService(backend=args.ai_backend, model=args.ai_model, auto_pull=args.ai_pull)


def _run_analysis(args, git, llm):
    parser = MultiLangImportParser()
    symbols = MultiLangSymbolExtractor()
    complexity = LizardSccAnalyzer()
    uc = AnalyzeBranchUseCase(git, parser, symbols, complexity, llm)
    print(f"Analyzing...", file=sys.stderr)
    return uc.execute(args.base, args.max_files, args.max_lines)


def _run_extraction(args, git, llm):
    base_ref = git.resolve_base(args.base)
    files = git.diff_stat(base_ref)
    parser = MultiLangImportParser()
    symbols = MultiLangSymbolExtractor()
    complexity = LizardSccAnalyzer()

    # Quick enrichment for extraction
    import networkx as nx
    from src.domain.service.dependency_resolver import build_dependency_edges
    imports_by_file = {}
    for f in files:
        source = git.file_source(f.path)
        if source:
            imports_by_file[f.path] = parser.parse(f.path, source)
    edges = build_dependency_edges(files, imports_by_file)
    G = nx.DiGraph()
    for f in files:
        G.add_node(f.path)
    for s, t, _, _ in edges:
        G.add_edge(s, t)

    uc = ExtractFilesUseCase(llm)
    return uc.execute(
        prompt=args.extract, all_files=files, graph=G,
        source_branch=git.current_branch(), base_branch=args.base,
        branch_name=args.extract_branch, include_deps=not args.no_deps,
    )


def _print_ai_status():
    results = check_llm_backends()
    print(f"\n{BOLD}  LLM Backends{RESET}")
    for name, info in results.items():
        avail = f"{GREEN}available{RESET}" if info.get("available") else f"{RED}unavailable{RESET}"
        print(f"  {name:12s}: {avail}")
        for k, v in info.items():
            if k == "available":
                continue
            if k == "models" and isinstance(v, list):
                print(f"    {k}: {', '.join(v[:10])}")
            else:
                print(f"    {k}: {v}")
    print(f"\n  AI is ON by default. Use {CYAN}--no-ai{RESET} to disable.")
    print(f"  Setup: {CYAN}python -m src.presentation.cli --setup{RESET}")
    print()
