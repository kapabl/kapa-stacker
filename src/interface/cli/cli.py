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
DIM = "\033[2m"
RESET = "\033[0m"


def main() -> None:
    args = _parse_args()
    args.func(args)
    sys.exit(0)


# ── Subcommand handlers ──────────────────────────────────────────────────


def _cmd_init(args):
    """Interactive setup for the current branch."""
    import json

    git = GitClient()
    branch = git.current_branch()
    base = git.detect_base()

    print(f"\n{BOLD}  kapa-cortex — initializing for branch {CYAN}{branch}{RESET}\n")

    base_input = input(f"  Base branch [{base}]: ").strip()
    if base_input:
        base = base_input

    max_files_input = input(f"  Approximate files per PR [3]: ").strip()
    max_files = int(max_files_input) if max_files_input else 3

    max_lines_input = input(f"  Approximate code lines per PR [200]: ").strip()
    max_lines = int(max_lines_input) if max_lines_input else 200

    config = {
        "branch": branch,
        "base": base,
        "max_files": max_files,
        "max_lines": max_lines,
    }

    config_dir = Path(".cortex-cache/branches") / branch.replace("/", "-")
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.json"
    config_path.write_text(json.dumps(config, indent=2))

    print(f"\n  {GREEN}Config saved to {config_path}{RESET}")
    print(f"  These are soft targets — the partitioner respects dependency")
    print(f"  constraints and test pairing even if it exceeds these limits.\n")
    print(f"  Next: {CYAN}kapa-cortex analyze{RESET}")


def _cmd_setup(args):
    """Install all dependencies and configure."""
    from src.infrastructure.setup import run_full_setup
    success = run_full_setup(ollama_model=args.ai_model, minimal=args.minimal)
    sys.exit(0 if success else 1)


def _rust(cmd_args: list[str]):
    """Delegate to the Rust binary."""
    import subprocess
    result = subprocess.run(["kapa-cortex-core"] + cmd_args)
    if result.returncode != 0:
        sys.exit(result.returncode)


def _cmd_index(args):
    _rust(["index", "."])


def _cmd_reindex(args):
    _rust(["reindex"] + (args.files or []))


def _cmd_lookup(args):
    cmd = ["lookup", args.symbol]
    if args.json:
        cmd.append("--json")
    _rust(cmd)


def _cmd_refs(args):
    cmd = ["refs"] + args.fqn
    if args.json:
        cmd.append("--json")
    _rust(cmd)


def _cmd_explain(args):
    cmd = ["explain", args.fqn]
    if args.json:
        cmd.append("--json")
    _rust(cmd)


def _cmd_impact(args):
    target = args.symbol or getattr(args, "file", None)
    if not target:
        print(f"  {RED}Provide a file or --symbol NAME{RESET}")
        sys.exit(1)
    cmd = ["impact", target]
    if args.json:
        cmd.append("--json")
    _rust(cmd)


def _cmd_hotspots(args):
    cmd = ["hotspots", "--limit", str(args.limit)]
    if args.json:
        cmd.append("--json")
    _rust(cmd)


def _cmd_deps(args):
    cmd = ["deps", args.file]
    if args.json:
        cmd.append("--json")
    _rust(cmd)


def _cmd_analyze(args):
    """Analyze branch and propose stacked PRs."""
    _apply_branch_config(args)
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
    _apply_branch_config(args)
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

    # ── init ──
    init_parser = subparsers.add_parser("init", help="Interactive setup for current branch")
    init_parser.set_defaults(func=_cmd_init)

    # ── setup ──
    setup_parser = subparsers.add_parser("setup", help="Install all dependencies")
    setup_parser.add_argument("--minimal", action="store_true", help="Smallest LLM model")
    setup_parser.set_defaults(func=_cmd_setup)

    # ── index ──
    index_parser = subparsers.add_parser("index", help="Pre-compute caches")
    index_parser.set_defaults(func=_cmd_index)

    # ── reindex ──
    reindex_parser = subparsers.add_parser("reindex", help="Re-index files via daemon")
    reindex_parser.add_argument("files", nargs="*", help="Files to re-index (all if omitted)")
    reindex_parser.set_defaults(func=_cmd_reindex)

    # ── impact ──
    impact_parser = subparsers.add_parser("impact", help="What breaks if this changes")
    impact_parser.add_argument("file", nargs="?", help="File to analyze (file-to-file impact)")
    impact_parser.add_argument("--symbol", type=str, metavar="NAME", help="Symbol to analyze")
    impact_parser.add_argument("--calls", action="store_true", help="Show only call chains")
    impact_parser.add_argument("--files", action="store_true", help="Show only file dependencies")
    impact_parser.add_argument("--refs", action="store_true", help="Show only type/reference usage")
    impact_parser.add_argument("--json", action="store_true", help="JSON output")
    impact_parser.set_defaults(func=_cmd_impact)

    # ── lookup ──
    lookup_parser = subparsers.add_parser("lookup", help="Find all definitions of a symbol")
    lookup_parser.add_argument("symbol", help="Symbol name to look up")
    lookup_parser.add_argument("--json", action="store_true", help="JSON output")
    lookup_parser.set_defaults(func=_cmd_lookup)

    # ── refs ──
    refs_parser = subparsers.add_parser("refs", help="Find all references to a symbol (LSP)")
    refs_parser.add_argument("fqn", nargs="+", help="Fully qualified name(s) (e.g. Class::method)")
    refs_parser.add_argument("--json", action="store_true", help="JSON output")
    refs_parser.set_defaults(func=_cmd_refs)

    # ── explain ──
    explain_parser = subparsers.add_parser("explain", help="Compact symbol summary")
    explain_parser.add_argument("fqn", help="Fully qualified name (e.g. Class::method)")
    explain_parser.add_argument("--json", action="store_true", help="JSON output")
    explain_parser.set_defaults(func=_cmd_explain)

    # ── hotspots ──
    hotspots_parser = subparsers.add_parser("hotspots", help="Rank files by complexity × dependents")
    hotspots_parser.add_argument("--limit", type=int, default=20, help="Max results")
    hotspots_parser.add_argument("--json", action="store_true", help="JSON output")
    hotspots_parser.set_defaults(func=_cmd_hotspots)

    # ── deps ──
    deps_parser = subparsers.add_parser("deps", help="Show forward dependencies of a file")
    deps_parser.add_argument("file", help="File to analyze")
    deps_parser.add_argument("--json", action="store_true", help="JSON output")
    deps_parser.set_defaults(func=_cmd_deps)

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
    daemon_parser = subparsers.add_parser("daemon", help="Manage daemon (start/stop/status)")
    daemon_parser.add_argument("daemon_action", choices=["start", "stop", "status"])
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


def _apply_branch_config(args):
    """Load branch config from init and apply as defaults."""
    import json

    git = GitClient()
    branch = git.current_branch()
    config_path = Path(".cortex-cache/branches") / branch.replace("/", "-") / "config.json"

    if not config_path.exists():
        return

    config = json.loads(config_path.read_text())

    if getattr(args, "base", None) is None:
        args.base = config.get("base")
    if getattr(args, "max_files", None) == 3:  # still at default
        args.max_files = config.get("max_files", 3)
    if getattr(args, "max_lines", None) == 200:  # still at default
        args.max_lines = config.get("max_lines", 200)


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
    """Start the Rust daemon (foreground)."""
    import subprocess
    subprocess.run(["kapa-cortex-core", "daemon", "start"])


def _stop_daemon():
    """Stop the Rust daemon."""
    import subprocess
    subprocess.run(["kapa-cortex-core", "daemon", "stop"])


def _print_daemon_status():
    """Print Rust daemon status."""
    import subprocess
    subprocess.run(["kapa-cortex-core", "status"])




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
