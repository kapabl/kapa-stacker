"""
Plan executor for stacked PR creation.

Generates, persists, and executes a step-by-step plan to split a feature
branch into stacked PRs. Each step is a git command (or sequence) that
can be run individually or as a batch.

Plan lifecycle:
  1. analyze    → generates .stacked-pr-plan.json
  2. check-plan → shows current state (what's done, what's next)
  3. run-plan   → executes pending steps (all or --step N)

Git strategy for stacked PRs:
  - For each PR, create a branch from its dependency (or base if independent)
  - Use `git checkout <source-branch> -- <files>` to bring in the right files
  - Commit with a descriptive message
  - Push with -u to set upstream tracking

Branch naming: stack/<base>/<N>-<slug>
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Plan data structures
# ---------------------------------------------------------------------------

PLAN_FILE = ".stacked-pr-plan.json"


class StepStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class PlanStep:
    """A single executable step in the plan."""
    id: int
    pr_index: int
    phase: str                     # "branch", "checkout", "commit", "push", "pr"
    description: str               # human-readable
    commands: list[str]            # actual git/gh commands to run
    status: str = StepStatus.PENDING
    output: str = ""               # captured stdout/stderr on execution
    executed_at: str = ""          # ISO timestamp
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "pr_index": self.pr_index,
            "phase": self.phase,
            "description": self.description,
            "commands": self.commands,
            "status": self.status,
            "output": self.output,
            "executed_at": self.executed_at,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, d: dict) -> PlanStep:
        return cls(**d)


@dataclass
class PRPlan:
    """Plan for a single stacked PR."""
    index: int
    title: str
    branch_name: str
    base_branch: str              # what this PR's branch is created from
    files: list[str]
    depends_on: list[int]
    merge_strategy: str
    code_lines: int
    risk_score: float

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "title": self.title,
            "branch_name": self.branch_name,
            "base_branch": self.base_branch,
            "files": self.files,
            "depends_on": self.depends_on,
            "merge_strategy": self.merge_strategy,
            "code_lines": self.code_lines,
            "risk_score": self.risk_score,
        }

    @classmethod
    def from_dict(cls, d: dict) -> PRPlan:
        return cls(**d)


@dataclass
class StackedPRPlan:
    """Complete execution plan for a stacked PR set."""
    version: int = 1
    created_at: str = ""
    source_branch: str = ""
    base_branch: str = ""
    repo_root: str = ""
    total_prs: int = 0
    prs: list[PRPlan] = field(default_factory=list)
    steps: list[PlanStep] = field(default_factory=list)
    mermaid: str = ""

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "created_at": self.created_at,
            "source_branch": self.source_branch,
            "base_branch": self.base_branch,
            "repo_root": self.repo_root,
            "total_prs": self.total_prs,
            "prs": [pr.to_dict() for pr in self.prs],
            "steps": [s.to_dict() for s in self.steps],
            "mermaid": self.mermaid,
        }

    @classmethod
    def from_dict(cls, d: dict) -> StackedPRPlan:
        plan = cls(
            version=d.get("version", 1),
            created_at=d.get("created_at", ""),
            source_branch=d.get("source_branch", ""),
            base_branch=d.get("base_branch", ""),
            repo_root=d.get("repo_root", ""),
            total_prs=d.get("total_prs", 0),
            prs=[PRPlan.from_dict(p) for p in d.get("prs", [])],
            steps=[PlanStep.from_dict(s) for s in d.get("steps", [])],
            mermaid=d.get("mermaid", ""),
        )
        return plan

    def save(self, path: str = PLAN_FILE) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2) + "\n")

    @classmethod
    def load(cls, path: str = PLAN_FILE) -> StackedPRPlan:
        if not Path(path).exists():
            raise FileNotFoundError(f"No plan found at {path}. Run the analyzer first.")
        data = json.loads(Path(path).read_text())
        return cls.from_dict(data)


# ---------------------------------------------------------------------------
# Slug generation
# ---------------------------------------------------------------------------

def _slugify(text: str, max_len: int = 40) -> str:
    """Convert a PR title to a branch-safe slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower())
    slug = slug.strip("-")
    if len(slug) > max_len:
        slug = slug[:max_len].rsplit("-", 1)[0]
    return slug


# ---------------------------------------------------------------------------
# Plan generation
# ---------------------------------------------------------------------------

def generate_plan(
    prs: list,  # list of ProposedPR from stacked_pr_analyzer
    source_branch: str,
    base_branch: str,
    repo_root: str = ".",
    remote: str = "origin",
    create_prs: bool = True,
) -> StackedPRPlan:
    """
    Generate a complete execution plan from analyzer output.

    For each proposed PR, generates steps:
      1. Create branch from dependency (or base)
      2. Checkout files from source branch
      3. Stage and commit
      4. Push to remote
      5. (Optional) Create GitHub PR via gh CLI
    """
    plan = StackedPRPlan(
        created_at=datetime.now(timezone.utc).isoformat(),
        source_branch=source_branch,
        base_branch=base_branch,
        repo_root=repo_root,
        total_prs=len(prs),
    )

    step_id = 0

    # Build PR index → branch name mapping
    branch_names: dict[int, str] = {}
    for pr in prs:
        slug = _slugify(pr.title)
        branch_name = f"stack/{base_branch}/{pr.index:02d}-{slug}"
        branch_names[pr.index] = branch_name

    # Determine merge order (topological)
    merged: set[int] = set()
    merge_order: list = []
    remaining = list(prs)
    while remaining:
        ready = [pr for pr in remaining if all(d in merged for d in pr.depends_on)]
        if not ready:
            # Circular — just take remaining in order
            ready = remaining[:]
        for pr in ready:
            merge_order.append(pr)
            merged.add(pr.index)
            remaining.remove(pr)

    # Generate steps in merge order
    for pr in merge_order:
        branch_name = branch_names[pr.index]
        files = [f.path for f in pr.files]

        # Determine the base for this PR's branch
        if pr.depends_on:
            # Branch from the highest dependency
            dep_idx = max(pr.depends_on)
            pr_base = branch_names[dep_idx]
        else:
            pr_base = base_branch

        pr_plan = PRPlan(
            index=pr.index,
            title=pr.title,
            branch_name=branch_name,
            base_branch=pr_base,
            files=files,
            depends_on=pr.depends_on,
            merge_strategy=pr.merge_strategy,
            code_lines=pr.total_code_lines,
            risk_score=pr.risk_score,
        )
        plan.prs.append(pr_plan)

        # Step 1: Create branch
        step_id += 1
        plan.steps.append(PlanStep(
            id=step_id,
            pr_index=pr.index,
            phase="branch",
            description=f"Create branch '{branch_name}' from '{pr_base}'",
            commands=[f"git checkout -b {branch_name} {pr_base}"],
        ))

        # Step 2: Checkout files from source branch
        step_id += 1
        # For deleted files, we need git rm instead of checkout
        checkout_files = [f.path for f in pr.files if f.status != "D"]
        deleted_files = [f.path for f in pr.files if f.status == "D"]

        cmds = []
        if checkout_files:
            # Batch checkout in groups to avoid arg-too-long
            for i in range(0, len(checkout_files), 20):
                batch = checkout_files[i:i+20]
                file_args = " ".join(f'"{f}"' for f in batch)
                cmds.append(f"git checkout {source_branch} -- {file_args}")
        if deleted_files:
            file_args = " ".join(f'"{f}"' for f in deleted_files)
            cmds.append(f"git rm {file_args}")

        plan.steps.append(PlanStep(
            id=step_id,
            pr_index=pr.index,
            phase="checkout",
            description=f"Checkout {len(files)} file(s) from '{source_branch}'",
            commands=cmds,
        ))

        # Step 3: Stage and commit
        step_id += 1
        commit_msg = _generate_commit_message(pr)
        # Use heredoc-style for safe message passing
        plan.steps.append(PlanStep(
            id=step_id,
            pr_index=pr.index,
            phase="commit",
            description=f"Commit: {pr.title}",
            commands=[
                "git add -A",
                f"git commit -m {_shell_quote(commit_msg)}",
            ],
        ))

        # Step 4: Push
        step_id += 1
        plan.steps.append(PlanStep(
            id=step_id,
            pr_index=pr.index,
            phase="push",
            description=f"Push '{branch_name}' to {remote}",
            commands=[f"git push -u {remote} {branch_name}"],
        ))

        # Step 5: Create PR (optional)
        if create_prs:
            step_id += 1
            pr_body = _generate_pr_body(pr, pr_plan, branch_names)
            gh_cmd = (
                f'gh pr create --base {pr_base} --head {branch_name} '
                f'--title {_shell_quote(pr.title)} '
                f'--body {_shell_quote(pr_body)}'
            )
            plan.steps.append(PlanStep(
                id=step_id,
                pr_index=pr.index,
                phase="pr",
                description=f"Create GitHub PR: {pr.title}",
                commands=[gh_cmd],
            ))

    # Step N+1: Return to source branch
    step_id += 1
    plan.steps.append(PlanStep(
        id=step_id,
        pr_index=0,
        phase="cleanup",
        description=f"Return to original branch '{source_branch}'",
        commands=[f"git checkout {source_branch}"],
    ))

    # Generate mermaid diagram
    plan.mermaid = _generate_mermaid(plan)

    return plan


# ---------------------------------------------------------------------------
# Commit / PR message generation
# ---------------------------------------------------------------------------

def _generate_commit_message(pr) -> str:
    """Generate a descriptive commit message for a PR."""
    files_summary = "\n".join(f"  - {f.path} (+{f.added}/-{f.removed})" for f in pr.files)
    msg = f"""{pr.title}

Files:
{files_summary}

Strategy: {pr.merge_strategy}
Part of stacked PR set (generated by stacked-pr-analyzer)"""
    return msg


def _generate_pr_body(pr, pr_plan: PRPlan, branch_names: dict[int, str]) -> str:
    """Generate a GitHub PR body with context."""
    lines = ["## Summary", ""]

    # File list
    lines.append(f"**{len(pr.files)} files** | **{pr.total_code_lines} code lines** | "
                 f"**risk: {pr.risk_score}** | **strategy: {pr.merge_strategy}**")
    lines.append("")
    lines.append("### Files")
    for f in pr.files:
        status_emoji = {"A": "+", "M": "~", "D": "-", "R": ">"}.get(f.status, "?")
        lines.append(f"- `{status_emoji}` `{f.path}` (+{f.added}/-{f.removed})")

    # Dependencies
    if pr.depends_on:
        lines.append("")
        lines.append("### Dependencies")
        lines.append("This PR depends on:")
        for dep_idx in pr.depends_on:
            dep_branch = branch_names.get(dep_idx, "unknown")
            lines.append(f"- PR #{dep_idx} (`{dep_branch}`)")
        lines.append("")
        lines.append("> **Merge order**: merge dependencies first, then rebase this PR.")

    # Merge strategy
    lines.append("")
    lines.append(f"### Merge Strategy: `{pr.merge_strategy}`")
    lines.append(pr_plan.merge_strategy and f"_{pr.description}_" or "")

    lines.append("")
    lines.append("---")
    lines.append("*Generated by [stacked-pr-analyzer]()*")

    return "\n".join(lines)


def _shell_quote(s: str) -> str:
    """Shell-quote a string using $'...' syntax for safety."""
    escaped = s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
    return f"$'{escaped}'"


# ---------------------------------------------------------------------------
# Mermaid diagram generation
# ---------------------------------------------------------------------------

def _generate_mermaid(plan: StackedPRPlan) -> str:
    """Generate a Mermaid flowchart of the PR stack."""
    lines = ["```mermaid", "graph BT"]

    strategy_style = {
        "squash": "fill:#4CAF50,color:#fff",
        "merge": "fill:#FF9800,color:#fff",
        "rebase": "fill:#2196F3,color:#fff",
    }

    # Base node
    lines.append(f'  base["{plan.base_branch}"]')
    lines.append(f"  style base fill:#666,color:#fff")

    for pr in plan.prs:
        node_id = f"pr{pr.index}"
        label = f"{pr.title}\\n{len(pr.files)} files, {pr.code_lines} lines\\n[{pr.merge_strategy}]"
        lines.append(f'  {node_id}["{label}"]')

        style = strategy_style.get(pr.merge_strategy, "fill:#999,color:#fff")
        lines.append(f"  style {node_id} {style}")

        if pr.depends_on:
            for dep in pr.depends_on:
                lines.append(f"  {node_id} --> pr{dep}")
        else:
            lines.append(f"  {node_id} --> base")

    lines.append("```")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Plan execution
# ---------------------------------------------------------------------------

RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"
DIM = "\033[2m"


def _run_command(cmd: str, dry_run: bool = False) -> tuple[bool, str]:
    """Execute a shell command, return (success, output)."""
    if dry_run:
        return True, f"[DRY RUN] {cmd}"

    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=120,
        )
        output = result.stdout + result.stderr
        return result.returncode == 0, output.strip()
    except subprocess.TimeoutExpired:
        return False, "Command timed out after 120s"
    except Exception as e:
        return False, str(e)


def execute_plan(
    plan: StackedPRPlan,
    step_id: Optional[int] = None,
    dry_run: bool = False,
    interactive: bool = True,
    plan_path: str = PLAN_FILE,
) -> bool:
    """
    Execute plan steps.

    Args:
        plan: The plan to execute
        step_id: If set, execute only this step. Otherwise execute all pending.
        dry_run: Print commands without executing
        interactive: Prompt before each PR group
        plan_path: Where to save progress
    """
    steps_to_run = []

    if step_id is not None:
        step = next((s for s in plan.steps if s.id == step_id), None)
        if not step:
            print(f"{RED}Step {step_id} not found.{RESET}")
            return False
        if step.status == StepStatus.COMPLETED:
            print(f"{YELLOW}Step {step_id} already completed. Use --force to re-run.{RESET}")
            return False
        steps_to_run = [step]
    else:
        steps_to_run = [s for s in plan.steps if s.status in (StepStatus.PENDING, StepStatus.FAILED)]

    if not steps_to_run:
        print(f"{GREEN}All steps already completed!{RESET}")
        return True

    current_pr = -1
    success = True

    for step in steps_to_run:
        # Print PR header when entering a new PR
        if step.pr_index != current_pr and step.pr_index > 0:
            current_pr = step.pr_index
            pr_plan = next((p for p in plan.prs if p.index == current_pr), None)
            if pr_plan:
                print()
                print(f"{BOLD}{'─' * 60}{RESET}")
                print(f"{BOLD}  PR #{current_pr}: {pr_plan.title}{RESET}")
                print(f"  Branch: {CYAN}{pr_plan.branch_name}{RESET}")
                print(f"  Base:   {CYAN}{pr_plan.base_branch}{RESET}")
                print(f"  Files:  {', '.join(pr_plan.files)}")
                print(f"{BOLD}{'─' * 60}{RESET}")

                if interactive and not dry_run:
                    resp = input(f"\n  Proceed with PR #{current_pr}? [Y/n/skip/quit] ").strip().lower()
                    if resp in ("n", "quit", "q"):
                        print(f"{YELLOW}Aborted.{RESET}")
                        plan.save(plan_path)
                        return False
                    if resp == "skip":
                        # Skip all steps for this PR
                        for s in steps_to_run:
                            if s.pr_index == current_pr:
                                s.status = StepStatus.SKIPPED
                        plan.save(plan_path)
                        continue

        # Execute step
        step.status = StepStatus.IN_PROGRESS
        plan.save(plan_path)

        prefix = f"  [{step.id}/{len(plan.steps)}]"
        print(f"{prefix} {step.description}")

        all_ok = True
        all_output = []

        for cmd in step.commands:
            print(f"    {DIM}$ {cmd}{RESET}")
            ok, output = _run_command(cmd, dry_run=dry_run)
            all_output.append(output)

            if output and not dry_run:
                for line in output.splitlines()[:5]:
                    print(f"    {DIM}{line}{RESET}")

            if not ok:
                all_ok = False
                print(f"    {RED}FAILED{RESET}")
                break

        step.output = "\n".join(all_output)
        step.executed_at = datetime.now(timezone.utc).isoformat()

        if all_ok:
            step.status = StepStatus.COMPLETED
            print(f"    {GREEN}OK{RESET}")
        else:
            step.status = StepStatus.FAILED
            step.error = all_output[-1] if all_output else "Unknown error"
            success = False
            plan.save(plan_path)

            if interactive:
                resp = input(f"\n  Step failed. [r]etry / [s]kip / [q]uit? ").strip().lower()
                if resp == "r":
                    step.status = StepStatus.PENDING
                    # Re-run will happen on next loop — but we need to restart
                    return execute_plan(plan, step_id=step.id, dry_run=dry_run,
                                        interactive=interactive, plan_path=plan_path)
                elif resp == "s":
                    step.status = StepStatus.SKIPPED
                else:
                    plan.save(plan_path)
                    return False
            else:
                plan.save(plan_path)
                return False

        plan.save(plan_path)

    return success


# ---------------------------------------------------------------------------
# Plan status / check-plan
# ---------------------------------------------------------------------------

def check_plan(plan: StackedPRPlan) -> None:
    """Print current plan status."""
    print()
    print(f"{BOLD}{'=' * 60}{RESET}")
    print(f"{BOLD}  Stacked PR Plan Status{RESET}")
    print(f"{BOLD}{'=' * 60}{RESET}")
    print(f"  Created    : {plan.created_at}")
    print(f"  Source     : {CYAN}{plan.source_branch}{RESET}")
    print(f"  Base       : {CYAN}{plan.base_branch}{RESET}")
    print(f"  Total PRs  : {plan.total_prs}")
    print()

    # Summary counts
    statuses = {s.status for s in plan.steps}
    completed = sum(1 for s in plan.steps if s.status == StepStatus.COMPLETED)
    pending = sum(1 for s in plan.steps if s.status == StepStatus.PENDING)
    failed = sum(1 for s in plan.steps if s.status == StepStatus.FAILED)
    skipped = sum(1 for s in plan.steps if s.status == StepStatus.SKIPPED)
    total = len(plan.steps)

    pct = int(completed / total * 100) if total else 0
    bar_len = 30
    filled = int(bar_len * completed / total) if total else 0
    bar = f"[{'█' * filled}{'░' * (bar_len - filled)}]"

    print(f"  Progress: {bar} {pct}%")
    print(f"  {GREEN}Completed: {completed}{RESET}  |  "
          f"Pending: {pending}  |  "
          f"{RED}Failed: {failed}{RESET}  |  "
          f"{DIM}Skipped: {skipped}{RESET}  |  "
          f"Total: {total}")
    print()

    # Per-PR status
    current_pr = -1
    for step in plan.steps:
        if step.pr_index != current_pr:
            current_pr = step.pr_index
            if current_pr > 0:
                pr_plan = next((p for p in plan.prs if p.index == current_pr), None)
                if pr_plan:
                    # Determine PR overall status
                    pr_steps = [s for s in plan.steps if s.pr_index == current_pr]
                    pr_completed = all(s.status == StepStatus.COMPLETED for s in pr_steps)
                    pr_failed = any(s.status == StepStatus.FAILED for s in pr_steps)
                    if pr_completed:
                        icon = f"{GREEN}✓{RESET}"
                    elif pr_failed:
                        icon = f"{RED}✗{RESET}"
                    else:
                        icon = f"{YELLOW}◦{RESET}"
                    print(f"  {icon} {BOLD}PR #{current_pr}: {pr_plan.title}{RESET}")
                    print(f"    Branch: {pr_plan.branch_name}")
            else:
                print(f"  {BOLD}Setup/Cleanup{RESET}")

        status_icon = {
            StepStatus.COMPLETED: f"{GREEN}✓{RESET}",
            StepStatus.PENDING: f"{DIM}○{RESET}",
            StepStatus.FAILED: f"{RED}✗{RESET}",
            StepStatus.SKIPPED: f"{DIM}⊘{RESET}",
            StepStatus.IN_PROGRESS: f"{YELLOW}►{RESET}",
        }.get(step.status, "?")

        print(f"    {status_icon} Step {step.id}: {step.description}")
        if step.status == StepStatus.FAILED and step.error:
            error_line = step.error.splitlines()[0][:80] if step.error else ""
            print(f"      {RED}Error: {error_line}{RESET}")

    # Next action
    print()
    next_step = next((s for s in plan.steps if s.status in (StepStatus.PENDING, StepStatus.FAILED)), None)
    if next_step:
        print(f"  {BOLD}Next:{RESET} Step {next_step.id} — {next_step.description}")
        print(f"  Run: {CYAN}python stacked_pr_analyzer.py --run-plan{RESET}")
        print(f"  Or:  {CYAN}python stacked_pr_analyzer.py --run-plan --step {next_step.id}{RESET}")
    else:
        print(f"  {GREEN}{BOLD}All steps completed!{RESET}")

    # Show mermaid if available
    if plan.mermaid:
        print()
        print(f"  {BOLD}PR Stack Diagram:{RESET}")
        print()
        for line in plan.mermaid.splitlines():
            print(f"  {line}")
    print()


# ---------------------------------------------------------------------------
# Git command script generation (non-interactive)
# ---------------------------------------------------------------------------

def generate_shell_script(plan: StackedPRPlan, include_gh: bool = True) -> str:
    """Generate a complete shell script from the plan."""
    lines = [
        "#!/usr/bin/env bash",
        "# Stacked PR creation script",
        f"# Generated: {plan.created_at}",
        f"# Source: {plan.source_branch} → {plan.base_branch}",
        f"# PRs: {plan.total_prs}",
        "",
        "set -euo pipefail",
        "",
        '# Colors',
        'GREEN="\\033[32m"',
        'YELLOW="\\033[33m"',
        'RED="\\033[31m"',
        'BOLD="\\033[1m"',
        'RESET="\\033[0m"',
        "",
        'log() { echo -e "${GREEN}[STACK]${RESET} $1"; }',
        'warn() { echo -e "${YELLOW}[WARN]${RESET} $1"; }',
        'fail() { echo -e "${RED}[FAIL]${RESET} $1"; exit 1; }',
        "",
        f'SOURCE_BRANCH="{plan.source_branch}"',
        f'BASE_BRANCH="{plan.base_branch}"',
        "",
        "# Ensure we're on the source branch and it's clean",
        'CURRENT=$(git rev-parse --abbrev-ref HEAD)',
        'if [ "$CURRENT" != "$SOURCE_BRANCH" ]; then',
        '  warn "Currently on $CURRENT, expected $SOURCE_BRANCH"',
        '  read -p "Continue anyway? [y/N] " -n 1 -r; echo',
        '  [[ $REPLY =~ ^[Yy]$ ]] || exit 1',
        'fi',
        "",
        'if ! git diff --quiet HEAD 2>/dev/null; then',
        '  fail "Working tree has uncommitted changes. Commit or stash first."',
        'fi',
        "",
    ]

    current_pr = -1
    for step in plan.steps:
        if step.phase == "pr" and not include_gh:
            continue

        if step.pr_index != current_pr and step.pr_index > 0:
            current_pr = step.pr_index
            pr_plan = next((p for p in plan.prs if p.index == current_pr), None)
            if pr_plan:
                lines.extend([
                    f"# {'=' * 58}",
                    f"# PR #{current_pr}: {pr_plan.title}",
                    f"# Branch: {pr_plan.branch_name}",
                    f"# Base: {pr_plan.base_branch}",
                    f"# Files: {', '.join(pr_plan.files)}",
                    f"# {'=' * 58}",
                    f'log "Creating PR #{current_pr}: {pr_plan.title}"',
                    "",
                ])

        lines.append(f"# Step {step.id}: {step.description}")
        for cmd in step.commands:
            lines.append(cmd)
        lines.append("")

    lines.extend([
        f'log "Done! All {plan.total_prs} PR branches created."',
        f'log "Return to source: git checkout $SOURCE_BRANCH"',
    ])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Print git commands (human-readable, copy-pasteable)
# ---------------------------------------------------------------------------

def print_commands(plan: StackedPRPlan, include_gh: bool = True) -> None:
    """Print all git commands in a copy-pasteable format."""
    print()
    print(f"{BOLD}{'=' * 60}{RESET}")
    print(f"{BOLD}  Git Commands for Stacked PR Creation{RESET}")
    print(f"{BOLD}{'=' * 60}{RESET}")
    print(f"  Source: {CYAN}{plan.source_branch}{RESET} → Base: {CYAN}{plan.base_branch}{RESET}")
    print()

    current_pr = -1
    for step in plan.steps:
        if step.phase == "pr" and not include_gh:
            continue

        if step.pr_index != current_pr and step.pr_index > 0:
            current_pr = step.pr_index
            pr_plan = next((p for p in plan.prs if p.index == current_pr), None)
            if pr_plan:
                print(f"\n  {BOLD}# ── PR #{current_pr}: {pr_plan.title} ──{RESET}")
                if pr_plan.depends_on:
                    deps = ", ".join(f"#{d}" for d in pr_plan.depends_on)
                    print(f"  {DIM}# depends on: {deps}{RESET}")
                print()

        for cmd in step.commands:
            print(f"  {cmd}")

    print()
    print(f"  {BOLD}# Return to original branch{RESET}")
    print(f"  git checkout {plan.source_branch}")
    print()
