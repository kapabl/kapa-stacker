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
    args.func(args)


# ── Subcommand handlers ──────────────────────────────────────────────────


def _cmd_setup(args):
    """Install all dependencies and configure."""
    from src.infrastructure.setup import run_full_setup
    success = run_full_setup(ollama_model=args.ai_model, minimal=args.minimal)
    sys.exit(0 if success else 1)


def _cmd_index(args):
    """Pre-compute caches."""
    from src.infrastructure.indexer.index_all import index_repo
    index_repo()


def _cmd_analyze(args):
    """Analyze branch and propose stacked PRs."""
    git = GitClient()
    if args.base is None:
        args.base = git.detect_base()

    llm = _build_llm(args)
    analysis = _run_analysis(args, git, llm)

    if not analysis.files:
        print("No changes found.")
        sys.exit(0)

    if args.json:
        print_json(analysis.prs, args.base, analysis.branch, analysis.graph)
    elif args.dot:
        dot = generate_dot(analysis.prs)
        print(dot)
    else:
        print_analysis(analysis.prs, analysis.branch, args.base, len(analysis.files), analysis.graph)


def _cmd_plan(args):
    """Generate execution plan with git commands."""
    git = GitClient()
    if args.base is None:
        args.base = git.detect_base()

    llm = _build_llm(args)
    analysis = _run_analysis(args, git, llm)

    if not analysis.files:
        print("No changes found.")
        sys.exit(0)

    text_generator = _build_text_generator(llm)
    plan_use_case = GeneratePlanUseCase(text_generator)
    plan = plan_use_case.execute(
        analysis.prs, analysis.branch, args.base,
        create_github_prs=not args.no_gh,
    )
    store = JsonPlanStore(args.plan_file)
    store.save(plan)
    print(f"Plan saved to {args.plan_file}", file=sys.stderr)

    if args.shell_script:
        print(generate_shell_script(plan))
    elif args.commands:
        print_commands(plan)
    else:
        print_analysis(analysis.prs, analysis.branch, args.base, len(analysis.files), analysis.graph)
        print_commands(plan)


def _cmd_run(args):
    """Execute a generated plan."""
    store = JsonPlanStore(args.plan_file)
    plan = store.load()
    if not plan:
        print(f"  {RED}No plan found. Run: kapa-cortex plan{RESET}")
        sys.exit(1)

    runner = ShellCommandRunner()
    execute_use_case = ExecutePlanUseCase(runner, store)
    success = execute_use_case.execute(plan, step_id=args.step, dry_run=args.dry_run)
    sys.exit(0 if success else 1)


def _cmd_status(args):
    """Show plan progress."""
    store = JsonPlanStore(args.plan_file)
    plan = store.load()
    if not plan:
        print(f"  {RED}No plan found. Run: kapa-cortex plan{RESET}")
        sys.exit(1)
    print_plan_status(plan)


def _cmd_extract(args):
    """Extract a subset of changes into a PR branch."""
    git = GitClient()
    if args.base is None:
        args.base = git.detect_base()

    llm = _build_llm(args)
    result = _run_extraction(args, git, llm)
    print_extraction(result)
    if not result.all_files:
        print(f"  {YELLOW}No files matched. Try a different query.{RESET}")
        sys.exit(1)


def _cmd_daemon(args):
    """Manage the daemon."""
    if args.daemon_action == "start":
        _start_daemon()
    elif args.daemon_action == "stop":
        _stop_daemon()
    elif args.daemon_action == "status":
        _print_daemon_status()
    elif args.daemon_action == "query":
        if not args.query_action:
            print(f"  {RED}Missing query action. Example: kapa-cortex daemon query analyze{RESET}")
            sys.exit(1)
        _run_daemon_query(args.query_action)


def _cmd_install_skill(args):
    """Install Claude Code skill."""
    _install_claude_skill()


def _cmd_ai_check(args):
    """Check LLM backend status."""
    _print_ai_status()


# ── Argument parser ──────────────────────────────────────────────────────


def _parse_args():
    root = argparse.ArgumentParser(
        prog="kapa-cortex",
        description="Local code intelligence engine — stacked PRs, repo analysis, dependency graphs.",
    )
    root.add_argument("--no-ai", action="store_true", help="Disable local LLM")
    root.add_argument("--ai-backend", type=str, choices=["ollama", "llama-cpp", "none"])
    root.add_argument("--ai-model", type=str)

    subparsers = root.add_subparsers(dest="command")

    # ── setup ──
    setup_parser = subparsers.add_parser("setup", help="Install all dependencies")
    setup_parser.add_argument("--minimal", action="store_true", help="Smallest LLM model")
    setup_parser.set_defaults(func=_cmd_setup)

    # ── index ──
    index_parser = subparsers.add_parser("index", help="Pre-compute caches")
    index_parser.set_defaults(func=_cmd_index)

    # ── analyze ──
    analyze_parser = subparsers.add_parser("analyze", help="Analyze branch, propose stacked PRs")
    analyze_parser.add_argument("--base", default=None)
    analyze_parser.add_argument("--max-files", type=int, default=3)
    analyze_parser.add_argument("--max-lines", type=int, default=200)
    analyze_parser.add_argument("--json", action="store_true", help="JSON output")
    analyze_parser.add_argument("--dot", action="store_true", help="DOT graph output")
    analyze_parser.set_defaults(func=_cmd_analyze)

    # ── plan ──
    plan_parser = subparsers.add_parser("plan", help="Generate execution plan")
    plan_parser.add_argument("--base", default=None)
    plan_parser.add_argument("--max-files", type=int, default=3)
    plan_parser.add_argument("--max-lines", type=int, default=200)
    plan_parser.add_argument("--plan-file", default=".cortex-plan.json")
    plan_parser.add_argument("--no-gh", action="store_true", help="Skip GitHub PR creation")
    plan_parser.add_argument("--commands", action="store_true", help="Print git commands only")
    plan_parser.add_argument("--shell-script", action="store_true", help="Output as bash script")
    plan_parser.set_defaults(func=_cmd_plan)

    # ── run ──
    run_parser = subparsers.add_parser("run", help="Execute a generated plan")
    run_parser.add_argument("--plan-file", default=".cortex-plan.json")
    run_parser.add_argument("--step", type=int, default=None, help="Execute single step")
    run_parser.add_argument("--dry-run", action="store_true", help="Preview without executing")
    run_parser.set_defaults(func=_cmd_run)

    # ── status ──
    status_parser = subparsers.add_parser("status", help="Show plan progress")
    status_parser.add_argument("--plan-file", default=".cortex-plan.json")
    status_parser.set_defaults(func=_cmd_status)

    # ── extract ──
    extract_parser = subparsers.add_parser("extract", help="Extract file subset into PR branch")
    extract_parser.add_argument("prompt", help="Natural language description")
    extract_parser.add_argument("--base", default=None)
    extract_parser.add_argument("--branch", type=str, dest="extract_branch")
    extract_parser.add_argument("--no-deps", action="store_true")
    extract_parser.set_defaults(func=_cmd_extract)

    # ── daemon ──
    daemon_parser = subparsers.add_parser("daemon", help="Manage daemon (start/stop/status/query)")
    daemon_parser.add_argument("daemon_action", choices=["start", "stop", "status", "query"])
    daemon_parser.add_argument("query_action", nargs="?", default=None, help="Query action (for daemon query)")
    daemon_parser.set_defaults(func=_cmd_daemon)

    # ── install-skill ──
    skill_parser = subparsers.add_parser("install-skill", help="Install Claude Code skill")
    skill_parser.set_defaults(func=_cmd_install_skill)

    # ── ai-check ──
    ai_parser = subparsers.add_parser("ai-check", help="Check LLM backend status")
    ai_parser.set_defaults(func=_cmd_ai_check)

    args = root.parse_args()
    if not hasattr(args, "func"):
        root.print_help()
        sys.exit(0)

    return args


# ── Shared helpers ───────────────────────────────────────────────────────


def _build_llm(args):
    if getattr(args, "no_ai", False) or getattr(args, "ai_backend", None) == "none":
        return NullLLMService()
    return OllamaLLMService(
        backend=getattr(args, "ai_backend", None),
        model=getattr(args, "ai_model", None),
    )


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
        prompt=args.prompt, all_files=files, graph=dep_graph,
        source_branch=git.current_branch(), base_branch=args.base,
        branch_name=getattr(args, "extract_branch", None),
        include_deps=not args.no_deps,
    )


def _install_claude_skill():
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
    from src.interface.daemon.client import is_daemon_running
    from src.interface.daemon.server import DaemonServer
    from src.interface.daemon.query_router import QueryRouter
    from src.interface.daemon.handlers import build_handler_map, set_index_store
    from src.infrastructure.indexer.index_store import IndexStore
    from src.infrastructure.indexer.incremental_indexer import build_full

    if is_daemon_running():
        print(f"  {YELLOW}Daemon already running.{RESET}")
        return

    cache_path = ".cortex-cache/index.msgpack"

    def on_start():
        if Path(cache_path).exists():
            print(f"  {CYAN}Loading index from cache...{RESET}")
            store = IndexStore.load(cache_path)
        else:
            print(f"  {CYAN}Building index from source...{RESET}")
            store = build_full()
            store.save(cache_path)
        set_index_store(store)
        print(f"  {GREEN}Index: {store.file_count} files, {store.symbol_count} symbols, {store.edge_count} edges{RESET}")

    def on_stop():
        from src.interface.daemon.handlers import _get_index_store
        store = _get_index_store()
        if store and store.file_count > 0:
            store.save(cache_path)
            print(f"  {CYAN}Index saved to {cache_path}{RESET}")

    print(f"  {BOLD}Starting kapa-cortex daemon...{RESET}")
    router = QueryRouter(build_handler_map())
    server = DaemonServer(router, on_start=on_start, on_stop=on_stop)
    print(f"  {GREEN}Listening on unix socket{RESET}")
    server.start()


def _stop_daemon():
    from src.interface.daemon.client import is_daemon_running, send_query

    if not is_daemon_running():
        print(f"  {YELLOW}No daemon running.{RESET}")
        return

    response = send_query("shutdown")
    print(f"  {GREEN}Daemon stopped.{RESET}" if response.status == "ok"
          else f"  {RED}Failed: {response.error}{RESET}")


def _print_daemon_status():
    from src.interface.daemon.client import is_daemon_running, send_query

    if not is_daemon_running():
        print(f"  {RED}Daemon not running.{RESET}")
        print(f"  Start with: {CYAN}kapa-cortex daemon start{RESET}")
        return

    response = send_query("status")
    if response.status == "ok":
        print(f"  {GREEN}Daemon running{RESET}")
        for key, value in response.data.items():
            print(f"    {key}: {value}")
    else:
        print(f"  {RED}Error: {response.error}{RESET}")


def _run_daemon_query(action):
    import json as json_mod
    from src.interface.daemon.client import is_daemon_running, send_query

    if not is_daemon_running():
        print(f"  {RED}Daemon not running.{RESET}")
        print(f"  Start with: {CYAN}kapa-cortex daemon start{RESET}")
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
        for key, value in info.items():
            if key == "available":
                continue
            if key == "models" and isinstance(value, list):
                print(f"    {key}: {', '.join(value[:10])}")
            else:
                print(f"    {key}: {value}")
    print(f"\n  AI is ON by default. Use {CYAN}--no-ai{RESET} to disable.")
    print(f"  Setup: {CYAN}kapa-cortex setup{RESET}")
    print()
