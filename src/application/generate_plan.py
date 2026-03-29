"""Use case: generate an execution plan from analysis results."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from src.domain.entity.execution_plan import ExecutionPlan, PRPlan, PlanStep
from src.domain.entity.proposed_pr import ProposedPR
from src.domain.port.text_generator import TextGenerator


class GeneratePlanUseCase:
    """Creates git execution plans from proposed PRs."""

    def __init__(self, text_generator: TextGenerator):
        self._text_generator = text_generator

    def execute(
        self,
        prs: list[ProposedPR],
        source_branch: str,
        base_branch: str,
        remote: str = "origin",
        create_github_prs: bool = True,
    ) -> ExecutionPlan:
        plan = ExecutionPlan(
            created_at=datetime.now(timezone.utc).isoformat(),
            source_branch=source_branch,
            base_branch=base_branch,
            total_prs=len(prs),
        )

        branch_names = self._generate_branch_names(prs, base_branch)
        merge_order = self._resolve_merge_order(prs)

        step_id = 0
        for pr in merge_order:
            branch = branch_names[pr.index]
            pr_base = self._resolve_pr_base(pr, branch_names, base_branch)
            files = [file.path for file in pr.files]

            plan.prs.append(PRPlan(
                index=pr.index, title=pr.title,
                branch_name=branch, base_branch=pr_base,
                files=files, depends_on=pr.depends_on,
                merge_strategy=pr.merge_strategy,
                code_lines=pr.total_code_lines,
                risk_score=pr.risk_score,
            ))

            step_id = self._add_pr_steps(
                plan, pr, branch, pr_base, source_branch,
                remote, create_github_prs, step_id,
            )

        step_id += 1
        plan.steps.append(PlanStep(
            id=step_id, pr_index=0, phase="cleanup",
            description=f"Return to '{source_branch}'",
            commands=[f"git checkout {source_branch}"],
        ))

        plan.mermaid = _generate_mermaid(plan)
        return plan

    def _generate_branch_names(self, prs, base):
        names = {}
        for pr in prs:
            slug = re.sub(r"[^a-z0-9]+", "-", pr.title.lower())[:40].strip("-")
            names[pr.index] = f"stack/{base}/{pr.index:02d}-{slug}"
        return names

    def _resolve_merge_order(self, prs):
        merged, result, remaining = set(), [], list(prs)
        while remaining:
            ready = [proposed for proposed in remaining if all(d in merged for d in proposed.depends_on)]
            if not ready:
                result.extend(remaining)
                break
            for proposed in ready:
                result.append(proposed)
                merged.add(proposed.index)
                remaining.remove(proposed)
        return result

    def _resolve_pr_base(self, pr, branch_names, base_branch):
        if pr.depends_on:
            return branch_names[max(pr.depends_on)]
        return base_branch

    def _add_pr_steps(self, plan, pr, branch, pr_base, source, remote, create_prs, step_id):
        step_id += 1
        plan.steps.append(PlanStep(
            id=step_id, pr_index=pr.index, phase="branch",
            description=f"Create branch '{branch}'",
            commands=[f"git checkout -b {branch} {pr_base}"],
        ))

        step_id += 1
        checkout = [file.path for file in pr.files if file.status != "D"]
        deleted = [file.path for file in pr.files if file.status == "D"]
        cmds = []
        for i in range(0, len(checkout), 20):
            batch = checkout[i:i+20]
            args = " ".join(f'"{path}"' for path in batch)
            cmds.append(f"git checkout {source} -- {args}")
        if deleted:
            args = " ".join(f'"{path}"' for path in deleted)
            cmds.append(f"git rm {args}")
        plan.steps.append(PlanStep(
            id=step_id, pr_index=pr.index, phase="checkout",
            description=f"Checkout {len(pr.files)} file(s)",
            commands=cmds,
        ))

        step_id += 1
        diff_combined = "\n".join(file.diff_text for file in pr.files)
        commit_message = self._text_generator.generate_commit_message(diff_combined, pr.title)
        plan.steps.append(PlanStep(
            id=step_id, pr_index=pr.index, phase="commit",
            description=f"Commit: {pr.title}",
            commands=["git add -A", f"git commit -m '{commit_message}'"],
        ))

        step_id += 1
        plan.steps.append(PlanStep(
            id=step_id, pr_index=pr.index, phase="push",
            description=f"Push '{branch}'",
            commands=[f"git push -u {remote} {branch}"],
        ))

        if create_prs:
            step_id += 1
            plan.steps.append(PlanStep(
                id=step_id, pr_index=pr.index, phase="pr",
                description=f"Create GitHub PR: {pr.title}",
                commands=[f"gh pr create --base {pr_base} --head {branch} --title '{pr.title}' --body 'Part of stacked PR set'"],
            ))

        return step_id


def _generate_mermaid(plan: ExecutionPlan) -> str:
    lines = ["```mermaid", "graph BT"]
    lines.append(f'  base["{plan.base_branch}"]')
    lines.append(f"  style base fill:#666,color:#fff")

    colors = {"squash": "fill:#4CAF50,color:#fff", "merge": "fill:#FF9800,color:#fff", "rebase": "fill:#2196F3,color:#fff"}
    for pr in plan.prs:
        nid = f"pr{pr.index}"
        label = f"{pr.title}\\n{len(pr.files)} files, {pr.code_lines} lines"
        lines.append(f'  {nid}["{label}"]')
        lines.append(f"  style {nid} {colors.get(pr.merge_strategy, 'fill:#999,color:#fff')}")
        if pr.depends_on:
            for dep in pr.depends_on:
                lines.append(f"  {nid} --> pr{dep}")
        else:
            lines.append(f"  {nid} --> base")

    lines.append("```")
    return "\n".join(lines)
