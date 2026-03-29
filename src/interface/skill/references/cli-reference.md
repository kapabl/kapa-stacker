# CLI Reference

## Commands

| Command | Description |
|---------|-------------|
| `setup` | Install all dependencies (ctags, ast-grep, scc, difftastic, ollama) |
| `setup --minimal` | Setup with smallest LLM model |
| `index` | Pre-compute caches (ctags, imports, co-change, complexity) |
| `analyze` | Analyze branch, propose stacked PRs |
| `plan` | Generate execution plan with git commands |
| `run` | Execute a generated plan |
| `status` | Show plan progress |
| `extract PROMPT` | Extract file subset using natural language |
| `daemon start` | Start daemon (warm LSPs, in-memory index) |
| `daemon stop` | Stop daemon |
| `daemon status` | Show daemon health |
| `daemon query ACTION` | Send query to daemon (analyze, impact, deps, hotspots) |
| `install-skill` | Install Claude Code skill |
| `ai-check` | Check LLM backend status |

## analyze

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--base` | string | auto-detect | Base branch to diff against |
| `--max-files` | int | 3 | Approximate files per PR |
| `--max-lines` | int | 200 | Approximate code lines per PR |
| `--json` | flag | — | JSON output |
| `--dot` | flag | — | DOT graph output |

## plan

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--base` | string | auto-detect | Base branch |
| `--max-files` | int | 3 | Approximate files per PR |
| `--max-lines` | int | 200 | Approximate code lines per PR |
| `--plan-file` | string | .cortex-plan.json | Plan file location |
| `--no-gh` | flag | — | Skip GitHub PR creation |
| `--commands` | flag | — | Print git commands only |
| `--shell-script` | flag | — | Output as bash script |

## run

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--plan-file` | string | .cortex-plan.json | Plan file location |
| `--step` | int | — | Execute specific step only |
| `--dry-run` | flag | — | Preview without executing |

## extract

| Argument | Type | Description |
|----------|------|-------------|
| `prompt` | string | Natural language description of files to extract |
| `--base` | string | Base branch |
| `--branch` | string | Branch name for extraction |
| `--no-deps` | flag | Skip dependency resolution |

## Global Flags

| Flag | Description |
|------|-------------|
| `--no-ai` | Disable local LLM |
| `--ai-backend` | Choose ollama, llama-cpp, or none |
| `--ai-model` | Specific model name |

## Exit Codes

- `0` — success
- `1` — error (no changes found, extraction failed, plan step failed)
