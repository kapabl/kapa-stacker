# JSON Output Schema Reference

## Analysis output (`--json`)

```json
{
  "branch": "string — current branch name",
  "base": "string — base branch",
  "total_prs": "int — number of proposed PRs",
  "file_dependency_edges": "int — edges in dependency graph",
  "prs": ["array of PR objects"]
}
```

### PR object

```json
{
  "index": "int — PR number (1-based)",
  "title": "string — generated title",
  "files": ["array of file objects"],
  "code_lines": "int — total code lines (added + removed)",
  "total_lines": "int — total lines including docs",
  "complexity": "int — total cyclomatic complexity",
  "depends_on": ["array of PR indices this PR depends on"],
  "merge_strategy": "string — squash | merge | rebase",
  "risk_score": "float 0.0-1.0"
}
```

### File object

```json
{
  "path": "string — relative file path",
  "status": "string — A (added) | M (modified) | D (deleted) | R (renamed)",
  "added": "int — lines added",
  "removed": "int — lines removed",
  "is_docs": "bool — true for docs/config files",
  "complexity": "int — cyclomatic complexity"
}
```

## Risk score (0.0 - 1.0)

Computed from:
- 30% line risk — structural code lines / 500 (deflated by structural ratio)
- 30% complexity risk — cyclomatic complexity / 50
- 20% dependency risk — number of PR dependencies / 5
- 20% diversity risk — number of distinct file extensions / 4

## Merge strategy

- `squash` — standalone PRs with no dependents
- `merge` — PRs with dependents or high risk score
- `rebase` — docs-only PRs

## Plan output (`--generate-plan`)

```json
{
  "version": "1.0",
  "created_at": "ISO timestamp",
  "source_branch": "string",
  "base_branch": "string",
  "repo_root": "string",
  "total_prs": "int",
  "prs": ["array of PRPlan objects"],
  "steps": ["array of PlanStep objects"],
  "mermaid": "string — mermaid diagram"
}
```

### PRPlan object

```json
{
  "index": "int",
  "title": "string",
  "branch_name": "string — generated branch name",
  "base_branch": "string",
  "files": ["array of file paths"],
  "depends_on": ["array of PR indices"],
  "merge_strategy": "string",
  "code_lines": "int",
  "risk_score": "float"
}
```

### PlanStep object

```json
{
  "id": "int",
  "pr_index": "int",
  "phase": "string — checkout | cherry-pick | push | pr",
  "description": "string",
  "commands": ["array of shell commands"],
  "status": "string — pending | done | failed | skipped",
  "output": "string",
  "executed_at": "string or null",
  "error": "string"
}
```
