# CLI Reference

## Analysis

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--base` | string | auto-detect | Base branch to diff against |
| `--max-files` | int | 3 | Maximum files per PR |
| `--max-lines` | int | 200 | Maximum code lines per PR |
| `--json` | flag | — | Output as structured JSON |
| `--visualize` | flag | — | Output as Graphviz DOT |
| `--dot-file` | string | — | Write DOT to file |
| `--show-base` | flag | — | Print detected base branch and exit |

## Plan Management

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--generate-plan` | flag | — | Create execution plan |
| `--check-plan` | flag | — | Show plan progress |
| `--run-plan` | flag | — | Execute plan |
| `--step` | int | — | Execute specific step only |
| `--dry-run` | flag | — | Preview without executing |
| `--print-commands` | flag | — | Show git commands |
| `--shell-script` | flag | — | Generate bash script |
| `--no-gh` | flag | — | Skip GitHub API calls |
| `--plan-file` | string | .cortex-plan.json | Plan file location |

## File Extraction

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--extract` | string | — | Natural language file query |
| `--extract-branch` | string | — | Branch name for extraction |
| `--no-deps` | flag | — | Skip dependency resolution |

## AI

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--no-ai` | flag | — | Disable local LLM |
| `--ai-backend` | choice | ollama | ollama, llama-cpp, or none |
| `--ai-model` | string | — | Specific model name |
| `--ai-pull` | flag | — | Auto-download missing models |
| `--ai-check` | flag | — | Show LLM backend status |

## Daemon

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--daemon` | flag | — | Start daemon server |
| `--daemon-stop` | flag | — | Stop running daemon |
| `--daemon-status` | flag | — | Show daemon status |
| `--query` | string | — | Send action to daemon |

## Setup

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--setup` | flag | — | Install all dependencies |
| `--setup-minimal` | flag | — | Setup with smallest LLM model |
| `--index` | flag | — | Pre-compute caches |

## Exit Codes

- `0` — success
- `1` — error (no changes found, extraction failed, plan step failed)
