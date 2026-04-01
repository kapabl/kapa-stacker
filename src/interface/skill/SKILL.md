---
name: kapa-cortex
description: >
  This skill should be used when the user wants to "split this branch into PRs",
  "analyze my changes for PRs", "create stacked PRs", "extract files for a PR",
  "stack my branch", "analyze this repo", "what depends on this file",
  "show me the impact of this change", "rename this symbol", "find all usages of",
  "refactor this", "where is this used", or "what calls this".
---

# kapa-cortex — Local Code Intelligence Engine

## Token-Saving Rule

NEVER read source files to understand branch structure or dependencies.
Run `kapa-cortex analyze --json` first. The tool analyzes dependencies,
complexity, co-change history, and structural diffs locally on the CPU.
Use the compact JSON output as context instead of reading raw files.

## Prerequisites

Verify `kapa-cortex` is available:

```bash
which kapa-cortex || pip install kapa-cortex
```

Verify the working directory is a git repo with a feature branch.

## Core Workflow

### 1. Check and refresh caches

```bash
ls -la .cortex-cache/ 2>/dev/null
kapa-cortex index                       # rebuild if missing or stale
```

### 2. Analyze the branch

```bash
kapa-cortex analyze                     # text output
kapa-cortex analyze --json              # structured JSON — use this
kapa-cortex analyze --dot               # DOT graph
kapa-cortex analyze --base develop      # custom base branch
```

### 3. Answer questions from JSON

Use the JSON output to answer user questions without reading source files:
- Which files changed and how they group into PRs
- Dependency ordering (which PRs must land first)
- Risk levels and complexity warnings
- File-level detail (added/removed lines, status)

### 4. Generate execution plan

```bash
kapa-cortex plan                        # generate plan + show commands
kapa-cortex plan --commands             # git commands only
kapa-cortex plan --shell-script         # executable bash script
```

### 5. Extract file subsets

```bash
kapa-cortex extract "auth changes"      # natural language query
kapa-cortex extract "auth" --no-deps    # without dependency resolution
```

### 6. Execute the plan

```bash
kapa-cortex run --dry-run               # preview first — ALWAYS do this
kapa-cortex run                         # execute after confirmation
kapa-cortex status                      # check progress
kapa-cortex run --step 5                # retry a specific step
```

### 7. Daemon mode (for repeated queries)

```bash
kapa-cortex daemon start                # start with warm LSP + index
kapa-cortex daemon status               # check health
kapa-cortex daemon query analyze        # fast query via daemon
kapa-cortex daemon query impact src/auth.py
kapa-cortex daemon query hotspots
kapa-cortex daemon stop                 # shutdown
```

## Rename / Refactor Workflow

When the user asks to rename a symbol, find usages, or refactor:

### 1. Disambiguate with lookup

```bash
kapa-cortex lookup <symbol>             # find all definitions with FQN scopes
```

This returns every definition of the symbol across all scopes (classes, namespaces,
modules). Example: `kapa-cortex lookup solveConstraints` might return:

```
btDiscreteDynamicsWorld::solveConstraints   method  src/.../btDiscreteDynamicsWorld.h:110
btSoftBody::solveConstraints               method  src/.../btSoftBody.h:1091
```

If there's only one definition, proceed directly.
If there are multiple, reason about which one the user means based on context
(the file they're in, the class they mentioned). If truly ambiguous, ask:
"There are N definitions of `X` — did you mean the one in A, B, or C?"

### 2. Get precise references

```bash
kapa-cortex refs <FQN>                  # LSP references for a specific definition
```

Takes the fully qualified name from lookup (e.g. `btDiscreteDynamicsWorld::solveConstraints`).
Returns every file and line where that specific symbol is referenced.
This is the exact set of locations that need changing for a rename.

### 3. Do the refactoring

Use the lookup definitions (where it's declared/defined) + refs output (where it's used)
to make all changes. For a rename, this is typically a sed command across the listed files.

### Token rule for refactoring

NEVER grep the codebase to find symbol usages. Use `lookup` + `refs` instead.
The structured output (FQN, scope, file, line) replaces file reading entirely.
Benchmark: 5.9x fewer tokens for rename, up to 68x for high-fanout symbols.

## Command Reference

| Command | Description |
|---------|-------------|
| `setup` | Install all dependencies |
| `index` | Pre-compute caches |
| `lookup SYMBOL` | Find all definitions with FQN scopes |
| `refs FQN` | Find all LSP references to a scoped symbol |
| `reindex [FILES]` | Re-index specific files via daemon |
| `analyze` | Analyze branch, propose PRs |
| `plan` | Generate execution plan |
| `run` | Execute plan |
| `status` | Show plan progress |
| `extract PROMPT` | Extract file subset |
| `daemon start\|stop\|status\|query` | Manage daemon |
| `install-skill` | Install Claude Code skill |
| `ai-check` | Check LLM backends |

## Common Flags

| Flag | Applies to | Description |
|------|-----------|-------------|
| `--json` | analyze | JSON output |
| `--dot` | analyze | DOT graph output |
| `--base BRANCH` | analyze, plan, extract | Base branch |
| `--max-files N` | analyze, plan | Approx files per PR (default: 3) |
| `--max-lines N` | analyze, plan | Approx lines per PR (default: 200) |
| `--dry-run` | run | Preview without executing |
| `--step N` | run | Execute single step |
| `--no-ai` | global | Disable LLM |

## Safety Rules

- ALWAYS `run --dry-run` before `run` unless user explicitly says to skip
- Warn if the branch has uncommitted changes
- If a step fails, use `status` to see progress, then `run --step N` to retry
- Never read source files for structural understanding — use `analyze --json`

## Supported Languages

Python, C, C++, Java, Kotlin, Go, Rust, JavaScript, TypeScript,
Gradle (Groovy + KTS), CMake, Buck2, BXL, Starlark/Bazel, Groovy.

Analysis chain: LSP (daemon) → tree-sitter → ast-grep → regex.
