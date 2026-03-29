"""
Prompt-driven PR extraction from a feature branch.

Given a natural-language prompt describing what to extract, this module:
  1. Scans the branch diff for matching files
  2. Resolves dependencies (files needed by the extracted set)
  3. Generates git commands to create a new branch + PR with just those files
  4. Can execute immediately or generate a plan

Usage examples:
    python stacked_pr_analyzer.py --extract "gradle init-script files"
    python stacked_pr_analyzer.py --extract "all CMakeLists.txt changes"
    python stacked_pr_analyzer.py --extract "the new Buck2 targets"
    python stacked_pr_analyzer.py --extract "python test files only"
    python stacked_pr_analyzer.py --extract "src/core/ changes"

Matching strategy (layered, all additive):
  1. Path pattern matching (glob-style, regex, directory prefix)
  2. Keyword matching in file paths and diff content
  3. Language/filetype matching
  4. Dependency resolution (pull in files the matched set depends on)
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from stacked_pr_analyzer import ChangedFile

import networkx as nx


# ---------------------------------------------------------------------------
# Extraction rules — parsed from prompt
# ---------------------------------------------------------------------------

@dataclass
class ExtractionRule:
    """A single matching rule derived from a prompt."""
    kind: str              # "glob", "regex", "path_prefix", "keyword", "ext", "lang"
    pattern: str           # the actual pattern
    description: str = ""  # human-readable explanation


# ---------------------------------------------------------------------------
# Prompt → rules parser
# ---------------------------------------------------------------------------

# Map keywords to file extensions / globs
_KEYWORD_MAP: dict[str, list[ExtractionRule]] = {
    # Build systems
    "gradle": [
        ExtractionRule("glob", "*.gradle", "Gradle Groovy build files"),
        ExtractionRule("glob", "*.gradle.kts", "Gradle Kotlin DSL files"),
        ExtractionRule("glob", "gradle/**", "Gradle wrapper/config"),
        ExtractionRule("glob", "gradle.properties", "Gradle properties"),
        ExtractionRule("glob", "gradlew*", "Gradle wrapper scripts"),
        ExtractionRule("glob", "buildSrc/**", "Gradle buildSrc"),
    ],
    "init-script": [
        ExtractionRule("glob", "*.gradle", "Gradle files (init-script context)"),
        ExtractionRule("glob", "*.gradle.kts", "Gradle KTS files"),
        ExtractionRule("keyword", "init-script", "Files referencing init-script"),
        ExtractionRule("keyword", "initscript", "Files referencing initscript"),
        ExtractionRule("keyword", "init.gradle", "init.gradle references"),
    ],
    "cmake": [
        ExtractionRule("glob", "CMakeLists.txt", "CMake list files"),
        ExtractionRule("glob", "*.cmake", "CMake modules"),
        ExtractionRule("glob", "cmake/**", "CMake directory"),
    ],
    "buck": [
        ExtractionRule("glob", "BUCK", "Buck2 build files"),
        ExtractionRule("glob", "TARGETS", "Buck2 target files"),
        ExtractionRule("glob", "*.bxl", "BXL extension files"),
        ExtractionRule("glob", ".buckconfig*", "Buck configuration"),
    ],
    "buck2": [
        ExtractionRule("glob", "BUCK", "Buck2 build files"),
        ExtractionRule("glob", "TARGETS", "Buck2 target files"),
        ExtractionRule("glob", "*.bxl", "BXL extension files"),
        ExtractionRule("glob", ".buckconfig*", "Buck configuration"),
    ],
    "starlark": [
        ExtractionRule("glob", "*.bzl", "Starlark/Bazel files"),
        ExtractionRule("glob", "*.star", "Starlark files"),
        ExtractionRule("glob", "BUILD", "Bazel BUILD files"),
        ExtractionRule("glob", "BUILD.bazel", "Bazel BUILD files"),
        ExtractionRule("glob", "WORKSPACE", "Bazel workspace"),
    ],
    "bazel": [
        ExtractionRule("glob", "*.bzl", "Bazel Starlark files"),
        ExtractionRule("glob", "BUILD", "Bazel BUILD files"),
        ExtractionRule("glob", "BUILD.bazel", "Bazel BUILD files"),
        ExtractionRule("glob", "WORKSPACE*", "Bazel workspace"),
        ExtractionRule("glob", ".bazelrc*", "Bazel config"),
    ],
    "python": [
        ExtractionRule("ext", ".py", "Python source files"),
        ExtractionRule("ext", ".pyi", "Python type stubs"),
    ],
    "test": [
        ExtractionRule("glob", "**/test_*.py", "Python tests"),
        ExtractionRule("glob", "**/*_test.py", "Python tests"),
        ExtractionRule("glob", "**/*_test.go", "Go tests"),
        ExtractionRule("glob", "**/*.test.ts", "TS tests"),
        ExtractionRule("glob", "**/*.test.tsx", "TSX tests"),
        ExtractionRule("glob", "**/*.spec.ts", "TS specs"),
        ExtractionRule("glob", "**/*Test.java", "Java tests"),
        ExtractionRule("glob", "**/*Test.kt", "Kotlin tests"),
        ExtractionRule("glob", "**/*_test.cpp", "C++ tests"),
        ExtractionRule("glob", "**/test_*.cpp", "C++ tests"),
        ExtractionRule("glob", "**/__tests__/**", "JS test dirs"),
    ],
    "tests": [],  # alias, filled below
    "docs": [
        ExtractionRule("ext", ".md", "Markdown docs"),
        ExtractionRule("ext", ".rst", "reStructuredText docs"),
        ExtractionRule("ext", ".adoc", "AsciiDoc"),
        ExtractionRule("glob", "docs/**", "Docs directory"),
        ExtractionRule("glob", "doc/**", "Doc directory"),
    ],
    "config": [
        ExtractionRule("ext", ".yaml", "YAML configs"),
        ExtractionRule("ext", ".yml", "YAML configs"),
        ExtractionRule("ext", ".toml", "TOML configs"),
        ExtractionRule("ext", ".json", "JSON configs"),
        ExtractionRule("ext", ".ini", "INI configs"),
        ExtractionRule("ext", ".cfg", "Config files"),
        ExtractionRule("ext", ".properties", "Properties files"),
    ],
    "cpp": [
        ExtractionRule("ext", ".cpp", "C++ source"),
        ExtractionRule("ext", ".cc", "C++ source"),
        ExtractionRule("ext", ".cxx", "C++ source"),
        ExtractionRule("ext", ".h", "C/C++ headers"),
        ExtractionRule("ext", ".hpp", "C++ headers"),
        ExtractionRule("ext", ".hxx", "C++ headers"),
    ],
    "c++": [],  # alias
    "java": [
        ExtractionRule("ext", ".java", "Java source"),
    ],
    "kotlin": [
        ExtractionRule("ext", ".kt", "Kotlin source"),
        ExtractionRule("ext", ".kts", "Kotlin script"),
    ],
}
# Aliases
_KEYWORD_MAP["tests"] = _KEYWORD_MAP["test"]
_KEYWORD_MAP["c++"] = _KEYWORD_MAP["cpp"]


def parse_extraction_prompt(prompt: str) -> list[ExtractionRule]:
    """
    Parse a natural-language extraction prompt into matching rules.

    Examples:
        "gradle init-script files"  → glob *.gradle + keyword init-script
        "all CMakeLists.txt changes" → glob CMakeLists.txt
        "src/core/ changes"          → path_prefix src/core/
        "python test files"          → ext .py + glob **/test_*.py
        "the *.bxl files"            → glob *.bxl
    """
    rules: list[ExtractionRule] = []
    prompt_lower = prompt.lower().strip()

    # 1. Extract explicit glob patterns (*.ext, **/*.ext, path/*)
    glob_pattern = re.compile(r'([*?[\]{}]+[\w./\-*?]*|[\w./\-]+[*?[\]{}]+[\w./\-*?]*)')
    for m in glob_pattern.finditer(prompt):
        pat = m.group(1)
        rules.append(ExtractionRule("glob", pat, f"Glob pattern: {pat}"))

    # 2. Extract explicit path prefixes (directory/ or dir/subdir/)
    path_prefix_re = re.compile(r'(?:^|\s)([\w\-]+(?:/[\w\-]+)*/)(?:\s|$)')
    for m in path_prefix_re.finditer(prompt):
        prefix = m.group(1)
        rules.append(ExtractionRule("path_prefix", prefix, f"Path prefix: {prefix}"))

    # 3. Extract specific filenames
    filename_re = re.compile(r'(?:^|\s)([\w\-]+\.[\w.]+)(?:\s|$)')
    for m in filename_re.finditer(prompt):
        name = m.group(1)
        if not glob_pattern.search(name):
            rules.append(ExtractionRule("glob", f"**/{name}", f"Filename: {name}"))

    # 4. Match keywords from the keyword map
    for keyword, keyword_rules in _KEYWORD_MAP.items():
        if keyword in prompt_lower:
            rules.extend(keyword_rules)

    # 5. Extract quoted strings as keywords
    quoted_re = re.compile(r'["\']([^"\']+)["\']')
    for m in quoted_re.finditer(prompt):
        rules.append(ExtractionRule("keyword", m.group(1), f"Keyword: {m.group(1)}"))

    # Deduplicate
    seen = set()
    unique_rules = []
    for r in rules:
        key = (r.kind, r.pattern)
        if key not in seen:
            seen.add(key)
            unique_rules.append(r)

    return unique_rules


# ---------------------------------------------------------------------------
# File matching
# ---------------------------------------------------------------------------

def match_files(
    files: list[ChangedFile],
    rules: list[ExtractionRule],
) -> list[ChangedFile]:
    """
    Apply extraction rules to find matching files.
    Returns files that match at least one rule.
    """
    matched: set[str] = set()

    for f in files:
        for rule in rules:
            if _file_matches_rule(f, rule):
                matched.add(f.path)
                break

    return [f for f in files if f.path in matched]


def _file_matches_rule(f: "ChangedFile", rule: ExtractionRule) -> bool:
    """Check if a file matches a single rule."""
    if rule.kind == "glob":
        if fnmatch.fnmatch(f.path, rule.pattern):
            return True
        if fnmatch.fnmatch(Path(f.path).name, rule.pattern):
            return True
        return False

    elif rule.kind == "path_prefix":
        return f.path.startswith(rule.pattern)

    elif rule.kind == "ext":
        return Path(f.path).suffix.lower() == rule.pattern

    elif rule.kind == "regex":
        return bool(re.search(rule.pattern, f.path))

    elif rule.kind == "keyword":
        # Match in path or diff content
        kw = rule.pattern.lower()
        if kw in f.path.lower():
            return True
        if kw in f.diff_text.lower():
            return True
        return False

    elif rule.kind == "lang":
        from lang_parsers import _detect_lang
        return _detect_lang(f.path) == rule.pattern

    return False


# ---------------------------------------------------------------------------
# Dependency-aware extraction
# ---------------------------------------------------------------------------

def resolve_extraction_deps(
    matched: list[ChangedFile],
    all_files: list[ChangedFile],
    G: nx.DiGraph,
    include_deps: bool = True,
) -> list[ChangedFile]:
    """
    Given matched files, also pull in files they depend on.
    This ensures the extracted PR can compile/work independently.

    Args:
        matched: files directly matched by extraction rules
        all_files: all changed files on the branch
        G: dependency graph
        include_deps: if True, pull in transitive dependencies
    """
    if not include_deps:
        return matched

    matched_paths = {f.path for f in matched}
    all_map = {f.path: f for f in all_files}

    # Find all transitive dependencies of matched files
    deps_to_add: set[str] = set()
    for f in matched:
        if f.path in G:
            # out_edges = files this file depends on
            for _, dep in nx.dfs_edges(G, f.path):
                if dep not in matched_paths and dep in all_map:
                    deps_to_add.add(dep)

    # Combine
    result_paths = matched_paths | deps_to_add
    result = [f for f in all_files if f.path in result_paths]

    # Sort: deps first, then matched
    result.sort(key=lambda f: (f.path not in deps_to_add, f.path))

    return result


# ---------------------------------------------------------------------------
# Extraction plan generation
# ---------------------------------------------------------------------------

@dataclass
class ExtractionPlan:
    """Plan for extracting a subset of files into a new PR."""
    prompt: str
    rules: list[ExtractionRule]
    matched_files: list  # ChangedFile
    dep_files: list      # ChangedFile (pulled in for deps)
    all_files: list      # matched + deps
    branch_name: str
    base_branch: str
    source_branch: str
    commands: list[str] = field(default_factory=list)


def create_extraction_plan(
    prompt: str,
    all_files: list,  # ChangedFile
    G: nx.DiGraph,
    source_branch: str,
    base_branch: str,
    branch_name: str | None = None,
    include_deps: bool = True,
) -> ExtractionPlan:
    """
    Create a plan to extract files matching the prompt into a new PR branch.
    """
    rules = parse_extraction_prompt(prompt)
    matched = match_files(all_files, rules)
    resolved = resolve_extraction_deps(matched, all_files, G, include_deps)

    matched_paths = {f.path for f in matched}
    dep_files = [f for f in resolved if f.path not in matched_paths]

    # Generate branch name
    if not branch_name:
        slug = re.sub(r"[^a-z0-9]+", "-", prompt.lower())[:40].strip("-")
        branch_name = f"extract/{slug}"

    # Generate git commands
    commands = _generate_extraction_commands(
        files=resolved,
        source_branch=source_branch,
        base_branch=base_branch,
        branch_name=branch_name,
        prompt=prompt,
    )

    return ExtractionPlan(
        prompt=prompt,
        rules=rules,
        matched_files=matched,
        dep_files=dep_files,
        all_files=resolved,
        branch_name=branch_name,
        base_branch=base_branch,
        source_branch=source_branch,
        commands=commands,
    )


def _generate_extraction_commands(
    files: list,
    source_branch: str,
    base_branch: str,
    branch_name: str,
    prompt: str,
) -> list[str]:
    """Generate the git commands to extract files into a new branch."""
    commands = []

    # 1. Create branch from base
    commands.append(f"git checkout -b {branch_name} {base_branch}")

    # 2. Checkout files from source branch
    checkout_files = [f.path for f in files if f.status != "D"]
    deleted_files = [f.path for f in files if f.status == "D"]

    for i in range(0, len(checkout_files), 20):
        batch = checkout_files[i:i+20]
        file_args = " ".join(f'"{f}"' for f in batch)
        commands.append(f"git checkout {source_branch} -- {file_args}")

    if deleted_files:
        file_args = " ".join(f'"{f}"' for f in deleted_files)
        commands.append(f"git rm {file_args}")

    # 3. Stage and commit
    commands.append("git add -A")

    file_list = "\n".join(f"  - {f.path}" for f in files)
    commit_msg = (
        f"Extract: {prompt}\n\n"
        f"Files:\n{file_list}\n\n"
        f"Extracted from {source_branch} by stacked-pr-analyzer"
    )
    escaped_msg = commit_msg.replace("'", "'\\''")
    commands.append(f"git commit -m $'{escaped_msg}'")

    # 4. Push
    commands.append(f"git push -u origin {branch_name}")

    # 5. Return to source
    commands.append(f"git checkout {source_branch}")

    return commands


# ---------------------------------------------------------------------------
# Output / reporting
# ---------------------------------------------------------------------------

RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
DIM = "\033[2m"
RED = "\033[31m"


def print_extraction_plan(plan: ExtractionPlan) -> None:
    """Print the extraction plan."""
    print()
    print(f"{BOLD}{'=' * 60}{RESET}")
    print(f"{BOLD}  PR Extraction Plan{RESET}")
    print(f"{BOLD}{'=' * 60}{RESET}")
    print(f'  Prompt      : {CYAN}"{plan.prompt}"{RESET}')
    print(f"  Source      : {CYAN}{plan.source_branch}{RESET}")
    print(f"  Base        : {CYAN}{plan.base_branch}{RESET}")
    print(f"  New branch  : {GREEN}{plan.branch_name}{RESET}")
    print()

    # Rules
    print(f"  {BOLD}Matching rules:{RESET}")
    for r in plan.rules:
        print(f"    [{r.kind:12s}] {r.pattern:30s} {DIM}{r.description}{RESET}")

    # Matched files
    print()
    print(f"  {BOLD}Matched files ({len(plan.matched_files)}):{RESET}")
    total_lines = 0
    for f in plan.matched_files:
        total_lines += f.code_lines
        print(f"    [{f.status}] {f.path}  (+{f.added}/-{f.removed})")

    # Dependency files
    if plan.dep_files:
        print()
        print(f"  {BOLD}Dependency files pulled in ({len(plan.dep_files)}):{RESET}")
        for f in plan.dep_files:
            total_lines += f.code_lines
            print(f"    [{f.status}] {f.path}  (+{f.added}/-{f.removed})  {DIM}(dependency){RESET}")

    print()
    print(f"  {BOLD}Summary:{RESET} {len(plan.all_files)} files, {total_lines} lines changed")

    # Git commands
    print()
    print(f"  {BOLD}Git commands:{RESET}")
    print(f"  {DIM}{'─' * 50}{RESET}")
    for cmd in plan.commands:
        print(f"  {cmd}")

    # Remaining files
    matched_paths = {f.path for f in plan.all_files}
    print()
    print(f"  {DIM}Remaining on branch: files not included in this extraction{RESET}")
    print(f"  {DIM}can be split into further PRs with another --extract or{RESET}")
    print(f"  {DIM}the automatic --generate-plan.{RESET}")
    print()
