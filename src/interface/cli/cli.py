"""Interface: CLI entry point. No business logic here."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.infrastructure.git.git_client import GitClient
from src.infrastructure.git.command_executor import ShellCommandRunner
from src.infrastructure.parsers.multi_lang_parser import MultiLangImportParser, MultiLangSymbolExtractor
from src.infrastructure.complexity.cached_analyzer import CachedComplexityAnalyzer
from src.infrastructure.llm.ollama_backend import OllamaLLMService, NullLLMService, check_llm_backends
from src.infrastructure.llm.llm_text_generator import LlmTextGenerator
from src.infrastructure.llm.rule_based_generator import RuleBasedGenerator
from src.infrastructure.persistence.json_plan_store import JsonPlanStore
from src.infrastructure.git.cochange_adapter import CachedCochangeProvider
from src.infrastructure.diff.difftastic_classifier import DifftasticClassifier

from src.application.analyze_branch import AnalyzeBranchUseCase
from src.application.extract_files import ExtractFilesUseCase
from src.application.generate_plan import GeneratePlanUseCase
from src.application.execute_plan import ExecutePlanUseCase

from src.interface.reporters.text_reporter import print_analysis
from src.interface.reporters.json_reporter import print_json
from src.interface.reporters.dot_reporter import generate_dot
from src.interface.reporters.plan_reporter import print_plan_status, print_commands, generate_shell_script
from src.interface.reporters.extraction_reporter import print_extraction

BOLD = "\033[0m\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
RESET = "\033[0m"


def main() -> None:
    args = _parse_args()

    # ── Install skill ──
    if args.install_skill:
        _install_claude_skill()
        return

    # ── Daemon ──
    if args.daemon:
        _start_daemon()
        return

    if args.daemon_stop:
        _stop_daemon()
        return

    if args.daemon_status:
        _print_daemon_status()
        return

    if args.query:
        _run_daemon_query(args.query)
        return

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
    if args.base is None:
        args.base = git.detect_base()

    if args.show_base:
        print(args.base)
        return

    store = JsonPlanStore(args.plan_file)
    llm = _build_llm(args)

    # ── Check plan ──
    if args.check_plan:
        print_plan_status(store.load())
        return

    # ── Run plan ──
    if args.run_plan:
        runner = ShellCommandRunner()
        execute_use_case = ExecutePlanUseCase(runner, store)
        success = execute_use_case.execute(store.load(), step_id=args.step, dry_run=args.dry_run)
        sys.exit(0 if success else 1)

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
    arg_parser = argparse.ArgumentParser(description="kapa-cortex — local code intelligence engine.")

    arg_parser.add_argument("--base", default=None)
    arg_parser.add_argument("--max-files", type=int, default=3)
    arg_parser.add_argument("--max-lines", type=int, default=200)

    arg_parser.add_argument("--json", action="store_true")
    arg_parser.add_argument("--visualize", action="store_true")
    arg_parser.add_argument("--dot-file", type=str)

    arg_parser.add_argument("--generate-plan", action="store_true")
    arg_parser.add_argument("--check-plan", action="store_true")
    arg_parser.add_argument("--run-plan", action="store_true")
    arg_parser.add_argument("--step", type=int, default=None)
    arg_parser.add_argument("--dry-run", action="store_true")
    arg_parser.add_argument("--print-commands", action="store_true")
    arg_parser.add_argument("--shell-script", action="store_true")
    arg_parser.add_argument("--no-gh", action="store_true")
    arg_parser.add_argument("--plan-file", default=".cortex-plan.json")

    arg_parser.add_argument("--extract", type=str, metavar="PROMPT")
    arg_parser.add_argument("--extract-branch", type=str)
    arg_parser.add_argument("--no-deps", action="store_true")

    # AI is ON by default. Use --no-ai to disable.
    arg_parser.add_argument("--no-ai", action="store_true", help="Disable local LLM")
    arg_parser.add_argument("--ai-backend", type=str, choices=["ollama", "llama-cpp", "none"])
    arg_parser.add_argument("--ai-model", type=str)
    arg_parser.add_argument("--ai-pull", action="store_true")
    arg_parser.add_argument("--ai-check", action="store_true")
    arg_parser.add_argument("--show-base", action="store_true",
                   help="Print detected base branch and exit")

    arg_parser.add_argument("--setup", action="store_true",
                   help="Install all deps (ollama, ctags, scc, ast-grep, lizard)")
    arg_parser.add_argument("--setup-minimal", action="store_true",
                   help="Setup with smallest LLM model")
    arg_parser.add_argument("--index", action="store_true",
                   help="Pre-compute caches (ctags, imports, co-change, complexity)")

    # Skill
    arg_parser.add_argument("--install-skill", action="store_true",
                   help="Install kapa-cortex as a Claude Code skill")

    # Daemon
    arg_parser.add_argument("--daemon", action="store_true",
                   help="Start daemon (warm LSPs, in-memory index)")
    arg_parser.add_argument("--daemon-stop", action="store_true",
                   help="Stop running daemon")
    arg_parser.add_argument("--daemon-status", action="store_true",
                   help="Show daemon status")
    arg_parser.add_argument("--query", type=str, metavar="ACTION",
                   help="Send query to running daemon")

    return arg_parser.parse_args()


def _build_llm(args):
    if args.no_ai or args.ai_backend == "none":
        return NullLLMService()
    return OllamaLLMService(backend=args.ai_backend, model=args.ai_model, auto_pull=args.ai_pull)


def _build_text_generator(llm):
    if llm.available:
        return LlmTextGenerator(llm)
    return RuleBasedGenerator()


def _run_analysis(args, git, llm):
    parser = MultiLangImportParser()
    symbols = MultiLangSymbolExtractor()
    complexity = CachedComplexityAnalyzer()
    cochange = CachedCochangeProvider()
    diff_classifier = DifftasticClassifier()
    text_generator = _build_text_generator(llm)
    analyze_use_case = AnalyzeBranchUseCase(
        git, parser, symbols, complexity,
        cochange, diff_classifier, text_generator,
    )
    print(f"Analyzing...", file=sys.stderr)
    return analyze_use_case.execute(args.base, args.max_files, args.max_lines)


def _run_extraction(args, git, llm):
    base_ref = git.resolve_base(args.base)
    files = git.diff_stat(base_ref)
    parser = MultiLangImportParser()
    symbols = MultiLangSymbolExtractor()
    complexity = CachedComplexityAnalyzer()

    # Quick enrichment for extraction
    import networkx as nx
    from src.domain.service.dependency_resolver import build_dependency_edges
    imports_by_file = {}
    for file in files:
        source = git.file_source(file.path)
        if source:
            imports_by_file[file.path] = parser.parse(file.path, source)
    edges = build_dependency_edges(files, imports_by_file)
    dep_graph = nx.DiGraph()
    for file in files:
        dep_graph.add_node(file.path)
    for src, dst, _, _ in edges:
        dep_graph.add_edge(src, dst)

    extract_use_case = ExtractFilesUseCase(llm)
    return extract_use_case.execute(
        prompt=args.extract, all_files=files, graph=dep_graph,
        source_branch=git.current_branch(), base_branch=args.base,
        branch_name=args.extract_branch, include_deps=not args.no_deps,
    )


def _install_claude_skill():
    """Install kapa-cortex as a Claude Code skill."""
    import shutil

    skill_source = Path(__file__).resolve().parent.parent / "skill"
    skill_target = Path.home() / ".claude" / "skills" / "kapa-cortex"

    if not skill_source.exists():
        print(f"  {RED}Skill source not found at {skill_source}{RESET}")
        print(f"  {RED}kapa-cortex may not be installed correctly.{RESET}")
        sys.exit(1)

    if skill_target.exists():
        shutil.rmtree(skill_target)

    shutil.copytree(skill_source, skill_target)
    print(f"  {GREEN}Skill installed to {skill_target}{RESET}")
    print(f"  Claude Code will auto-trigger on phrases like:")
    print(f"    {CYAN}\"split this branch into PRs\"{RESET}")
    print(f"    {CYAN}\"analyze my changes\"{RESET}")
    print(f"    {CYAN}\"what depends on this file\"{RESET}")
    print(f"  Or invoke directly: {CYAN}/kapa-cortex{RESET}")


def _start_daemon():
    """Start the daemon server."""
    from src.interface.daemon.client import is_daemon_running
    from src.interface.daemon.server import DaemonServer
    from src.interface.daemon.query_router import QueryRouter

    if is_daemon_running():
        print(f"  {YELLOW}Daemon already running.{RESET}")
        return

    print(f"  {BOLD}Starting kapa-cortex daemon...{RESET}")
    from src.interface.daemon.handlers import build_handler_map
    router = QueryRouter(build_handler_map())
    server = DaemonServer(router)
    print(f"  {GREEN}Listening on unix socket{RESET}")
    server.start()  # blocks


def _stop_daemon():
    """Send stop signal to running daemon."""
    from src.interface.daemon.client import is_daemon_running, send_query

    if not is_daemon_running():
        print(f"  {YELLOW}No daemon running.{RESET}")
        return

    response = send_query("shutdown")
    print(f"  {GREEN}Daemon stopped.{RESET}" if response.status == "ok"
          else f"  {RED}Failed: {response.error}{RESET}")


def _print_daemon_status():
    """Show daemon status."""
    from src.interface.daemon.client import is_daemon_running, send_query

    if not is_daemon_running():
        print(f"  {RED}Daemon not running.{RESET}")
        print(f"  Start with: {CYAN}kapa-cortex --daemon{RESET}")
        return

    response = send_query("status")
    if response.status == "ok":
        print(f"  {GREEN}Daemon running{RESET}")
        for key, value in response.data.items():
            print(f"    {key}: {value}")
    else:
        print(f"  {RED}Error: {response.error}{RESET}")


def _run_daemon_query(action: str):
    """Send a query to the daemon and print the result."""
    import json as json_mod
    from src.interface.daemon.client import is_daemon_running, send_query

    if not is_daemon_running():
        print(f"  {RED}Daemon not running.{RESET}")
        print(f"  Start with: {CYAN}kapa-cortex --daemon{RESET}")
        sys.exit(1)

    response = send_query(action)
    if response.status == "ok":
        print(json_mod.dumps(response.data, indent=2))
    else:
        print(f"  {RED}Error: {response.error}{RESET}", file=sys.stderr)
        sys.exit(1)


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
    print(f"  Setup: {CYAN}kapa-cortex --setup{RESET}")
    print()
