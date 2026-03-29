# kapa-cortex

Local code intelligence engine — stacked PRs, repo analysis, dependency graphs.

Analyzes code dependencies across 15+ languages using tree-sitter, ast-grep,
ctags, lizard, difftastic, and co-change history. Splits feature branches into
small PRs (~3 files, ~200 lines), generates git commands, and uses a local LLM
(ollama) for smarter grouping. Runs as a CLI, daemon, or Claude Code skill.

## Install

```bash
pip install -e .

# Now use it anywhere:
kapa-cortex --help
```

Or without installing:

```bash
pip install networkx
python kapa-cortex.py --help
```

## Quick Start

```bash
kapa-cortex index                       # pre-compute caches
kapa-cortex analyze                     # see proposed stacked PRs
kapa-cortex analyze --json              # JSON output
kapa-cortex plan                        # generate git commands
kapa-cortex run --dry-run               # preview execution
kapa-cortex run                         # execute the plan
kapa-cortex status                      # check progress
kapa-cortex analyze --base develop      # custom base branch
```

## Daemon Mode

Start once, query many times — keeps LSP servers warm and index in memory:

```bash
kapa-cortex daemon start                # boots pyright, clangd, gopls, jdtls, rust-analyzer
kapa-cortex daemon status               # check health
kapa-cortex daemon query analyze        # fast query
kapa-cortex daemon query impact src/auth.py
kapa-cortex daemon query hotspots
kapa-cortex daemon stop
```

## Extract Specific Changes

Pull a subset of files into a separate PR branch using natural language:

```bash
kapa-cortex extract "gradle init-script files"
kapa-cortex extract "src/core/ changes"
kapa-cortex extract "all CMakeLists.txt changes"
kapa-cortex extract "python test files"
kapa-cortex extract "the authentication refactor"
```

## Claude Code Skill

Install as a Claude Code skill for token-efficient analysis:

```bash
kapa-cortex install-skill
```

Claude Code will auto-trigger on phrases like "split this branch into PRs",
"analyze my changes", or "what depends on this file".

## Output Formats

```bash
kapa-cortex analyze --json              # JSON
kapa-cortex analyze --dot               # DOT graph
kapa-cortex plan --commands             # git commands only
kapa-cortex plan --shell-script > stack.sh  # bash script
```

## AI Mode

AI is **on by default** using ollama. If ollama isn't running, it silently
falls back to rule-based analysis. No API keys needed.

```bash
kapa-cortex setup                # install all deps
kapa-cortex setup --minimal      # smallest model (~1.6 GB)
kapa-cortex ai-check             # check backends
kapa-cortex analyze --no-ai      # disable AI for a single run
```

## Risk & Complexity Labels

The analysis shows human-readable warnings. Raw scores stay in `--json` output.

### Risk (per PR, 0.0 – 1.0)

Based on: structural code lines (30%), cyclomatic complexity (30%),
cross-PR dependencies (20%), language diversity (20%).

| Score | Label | What it means |
|-------|-------|---------------|
| 0.0 – 0.2 | Low | Small, simple, safe to merge |
| 0.2 – 0.5 | Moderate | Normal review needed |
| 0.5 – 0.7 | **High** | Careful review — shows warning with reasons |
| 0.7 – 1.0 | **Critical** | Split further or get senior review |

### Complexity (per PR, cyclomatic total)

| Score | Label | What it means |
|-------|-------|---------------|
| 0 – 5 | Simple | Straightforward code |
| 5 – 15 | Moderate | Some branching logic |
| 15 – 30 | **Complex** | Shows warning — consider careful review |
| 30+ | **Very complex** | Shows warning — consider refactoring |

Warnings only appear for High/Critical risk and Complex/Very complex PRs.
Low and Moderate PRs show no warnings — clean output.

## Supported Languages

Python, C, C++, Java, Kotlin, Go, Rust, JavaScript, TypeScript,
Gradle (Groovy + KTS), CMake, Buck2, BXL, Starlark/Bazel, Groovy.

Analysis chain: LSP (daemon) → tree-sitter → ast-grep → regex.

## Architecture (DDD + 4 Layers)

```
src/
  domain/          # Pure logic, zero external deps
  application/     # Use cases, orchestration
  infrastructure/  # Git, parsers, LSP, LLM, caches
  interface/       # CLI, daemon, reporters, skill
```

## Running Tests

```bash
python -m unittest discover -s tests -v    # all tests
python -m unittest discover -s tests/domain -v  # domain only (fast)
```
