---
name: kapa-cortex
description: >
  This skill should be used when the user wants to "split this branch into PRs",
  "analyze my changes for PRs", "create stacked PRs", "extract files for a PR",
  "stack my branch", "analyze this repo", "what depends on this file",
  or "show me the impact of this change".
---

# kapa-cortex — Local Code Intelligence Engine

## Token-Saving Rule

NEVER read source files to understand branch structure or dependencies.
Run `kapa-cortex --json` first. The tool analyzes dependencies, complexity,
co-change history, and structural diffs locally on the CPU. Use the compact
JSON output as context instead of reading raw files.

## Prerequisites

Verify `kapa-cortex` is available:

```bash
which kapa-cortex || pip install -e /path/to/kapa-cortex
```

Verify the working directory is a git repo with a feature branch.

## Core Workflow

### 1. Check and refresh caches

```bash
# Check if caches exist and are fresh
ls -la .cortex-cache/ 2>/dev/null

# If missing or stale, rebuild (takes seconds)
kapa-cortex --index
```

### 2. Analyze the branch

```bash
# Structured JSON output — use this, not file reads
kapa-cortex --json
```

The JSON contains everything needed: branch name, base branch, proposed PRs
with files, dependency edges, complexity scores, risk scores, merge strategies.

### 3. Answer questions from JSON

Use the JSON output to answer user questions about the branch without reading
source files. The output includes:
- Which files changed and how they group into PRs
- Dependency ordering (which PRs must land first)
- Risk scores (0-1 scale) per PR
- Merge strategy recommendations (squash, merge, rebase)
- Cyclomatic complexity per file

### 4. Generate execution plan

```bash
kapa-cortex --generate-plan    # creates .cortex-plan.json
kapa-cortex --print-commands   # copy-pasteable git commands
kapa-cortex --shell-script     # executable bash script
```

### 5. Extract file subsets

```bash
kapa-cortex --extract "auth changes"     # natural language query
kapa-cortex --extract "auth" --no-deps   # without dependency resolution
```

### 6. Execute the plan

```bash
kapa-cortex --run-plan --dry-run   # preview first — ALWAYS do this
kapa-cortex --run-plan             # execute after confirmation
kapa-cortex --check-plan           # check progress
kapa-cortex --run-plan --step 5    # retry a specific step
```

### 7. Daemon mode (for repeated queries)

```bash
kapa-cortex --daemon               # start daemon with warm LSP servers
kapa-cortex --daemon-status        # check if daemon is running
kapa-cortex --query "analyze"      # fast query via daemon
kapa-cortex --daemon-stop          # stop daemon
```

The daemon keeps LSP servers (pyright, clangd, gopls, jdtls, rust-analyzer)
warm and maintains an in-memory index. First query boots servers (seconds),
subsequent queries return in milliseconds.

## Flag Reference

| Flag | Description |
|------|-------------|
| `--base BRANCH` | Diff against specific base (default: auto-detect) |
| `--max-files N` | Max files per PR (default: 3) |
| `--max-lines N` | Max code lines per PR (default: 200) |
| `--json` | JSON output |
| `--generate-plan` | Create plan with git commands |
| `--check-plan` | Show plan progress |
| `--run-plan` | Execute plan |
| `--dry-run` | Preview without executing |
| `--step N` | Execute specific step |
| `--extract PROMPT` | Natural language file extraction |
| `--no-deps` | Skip dependency resolution in extraction |
| `--index` | Pre-compute caches |
| `--daemon` | Start daemon |
| `--query ACTION` | Send query to daemon |
| `--no-ai` | Disable local LLM |
| `--visualize` | DOT graph output |

## JSON Output Schema

```json
{
  "branch": "feat/my-feature",
  "base": "master",
  "total_prs": 4,
  "file_dependency_edges": 12,
  "prs": [
    {
      "index": 1,
      "title": "PR #1: Add auth middleware",
      "files": [
        {"path": "src/auth.py", "status": "A", "added": 45, "removed": 0,
         "is_docs": false, "complexity": 3}
      ],
      "code_lines": 45,
      "total_lines": 45,
      "complexity": 3,
      "depends_on": [],
      "merge_strategy": "squash",
      "risk_score": 0.12
    }
  ]
}
```

## Safety Rules

- ALWAYS run `--dry-run` before `--run-plan` unless the user explicitly says to skip
- Warn if the branch has uncommitted changes
- If a step fails, use `--check-plan` to see progress, then `--step N` to retry
- Never read source files for structural understanding — use `--json` output

## Supported Languages

Python, C, C++, Java, Kotlin, Go, Rust, JavaScript, TypeScript,
Gradle (Groovy + KTS), CMake, Buck2, BXL, Starlark/Bazel, Groovy.

Analysis chain: LSP (daemon) → tree-sitter → ast-grep → regex.
