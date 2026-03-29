# Plan: kapa-cortex — Daemon + Skill + Full-Repo Intelligence

## Context

kapa-cortex is a local code intelligence engine. Today it runs as a CLI, paying parse/load costs on every invocation. Moving to a daemon model enables warm LSP servers, in-memory indexing, and sub-200ms queries. The Claude Code skill orchestrates the daemon for token-efficient analysis.

## Architecture

```
kapa-cortex daemon (long-running)
├── LSP clients (clangd, jdtls, pyright, gopls) — warm after first boot
├── In-memory index (symbols, imports, deps, co-change, complexity)
├── Cache layer (.cortex-cache/) — persisted to disk
├── Parser chain (tree-sitter → ast-grep → regex) — for build languages
└── Query interface (unix socket)

Clients:
├── CLI:   kapa-cortex --query "analyze"       → talks to daemon
├── CLI:   kapa-cortex --json                   → fallback if no daemon
├── Skill: Claude Code runs kapa-cortex --query → structured JSON back
└── Future: MCP server wrapper
```

## Layer Architecture (DDD)

The daemon requires a new **interface layer** — the API surface between kapa-cortex and its clients. This is not presentation (no human-facing output) and not infrastructure (not external I/O we consume — it's I/O we expose).

```
src/
  interface/           # NEW — daemon server, query protocol, MCP wrapper
    daemon/
      server.py        # Unix socket server, accept connections
      protocol.py      # JSON request/response schema
      query_router.py  # Route queries to application use cases
    mcp/               # Future: MCP server wrapper
  presentation/        # CLI — now a thin client
    cli.py             # Talks to daemon or calls use cases directly
    reporters/         # Text, JSON, DOT output formatting
  application/         # Use cases (unchanged)
    analyze_branch.py
    extract_files.py
    generate_plan.py
    execute_plan.py
  domain/              # Core logic (unchanged)
    entity/
    service/
    policy/
    port/
    factory/
  infrastructure/      # External I/O
    git/
    parsers/
    complexity/
    diff/
    llm/
    lsp/               # NEW — LSP client adapters
      lsp_manager.py   # Boot/manage clangd, jdtls, pyright, gopls
      clangd_client.py
      pyright_client.py
      gopls_client.py
      jdtls_client.py
    indexer/
    persistence/
```

### Dependency rules (extended)
- **Interface** imports from application and domain. Never from infrastructure.
- **Presentation** imports from interface (daemon client), application, and domain.
- **Application** imports from domain only.
- **Infrastructure** imports from domain (to implement interfaces).
- **Domain** imports nothing from other layers.

### Fallback strategy
- If daemon is running → CLI routes through interface layer (fast, LSP-assisted)
- If daemon is not running → CLI calls application layer directly (disk cache, parser chain, no LSP)
- Both paths produce identical JSON output

### LSP coverage

| Language | Daemon (LSP) | Standalone (parsers) |
|----------|-------------|---------------------|
| C++ | clangd — exact | tree-sitter → regex |
| Java | jdtls — exact | tree-sitter → regex |
| Python | pyright — exact | Python AST → regex |
| Go | gopls — exact | tree-sitter → regex |
| Kotlin | — | tree-sitter → regex |
| Rust | — | tree-sitter → regex |
| TypeScript/JS | — | tree-sitter → regex |
| Buck2 | — | ast-grep → regex |
| Bazel/Starlark | — | ast-grep → regex |
| BXL | — | regex |
| CMake | — | regex |
| Gradle Groovy/KTS | — | regex |
| Groovy | — | regex |

## Phase 1: Interface Layer + Daemon

### New files — interface/
- `src/interface/daemon/server.py` — unix socket server, accept connections, dispatch
- `src/interface/daemon/protocol.py` — JSON request/response schema, validation
- `src/interface/daemon/query_router.py` — route queries to application use cases
- `src/interface/daemon/client.py` — connect to daemon, send query, read response

### New files — infrastructure/lsp/
- `src/infrastructure/lsp/lsp_manager.py` — boot/manage/health-check LSP servers
- `src/infrastructure/lsp/clangd_client.py` — clangd wrapper
- `src/infrastructure/lsp/pyright_client.py` — pyright wrapper
- `src/infrastructure/lsp/gopls_client.py` — gopls wrapper
- `src/infrastructure/lsp/jdtls_client.py` — jdtls wrapper

### New domain port
- `src/domain/port/definition_resolver.py` — interface for "resolve this symbol to a file:line"
  - Implemented by LSP adapter (daemon mode) or fuzzy matcher (standalone mode)

### CLI changes
- `kapa-cortex --daemon` — start daemon, boot LSPs, listen on socket
- `kapa-cortex --daemon --stop` — stop running daemon
- `kapa-cortex --daemon --status` — show daemon status
- `kapa-cortex --query "analyze"` — send query to daemon
- All existing flags try daemon first, fall back to standalone

### Daemon protocol (JSON over unix socket)
```json
// Request
{"action": "analyze", "params": {"base": "master", "max_files": 3}}

// Response
{"status": "ok", "data": { /* same as --json output */ }}
```

## Phase 2: Claude Code Skill

### Files
```
.claude/skills/kapa-cortex/
├── SKILL.md                           # Triggers, workflow, token rules
├── references/
│   ├── cli-reference.md               # Full CLI flags
│   ├── json-schema.md                 # Output schemas
│   └── advanced-workflows.md          # Complex scenarios
└── scripts/
    └── check_daemon_status.sh         # Is daemon running?
```

### SKILL.md triggers
- "split this branch into PRs"
- "analyze my changes"
- "create stacked PRs"
- "extract files for a PR"
- "analyze this repo"
- "what depends on this file"

### Skill workflow
1. Check daemon: `kapa-cortex --daemon --status`
2. If not running, suggest: `kapa-cortex --daemon &`
3. Query: `kapa-cortex --query "analyze"` (or `--json` fallback)
4. Use structured JSON — never read source files for structure
5. For plan execution: query generate-plan → dry-run → confirm → run-plan

## Phase 3: Full-Repo Indexing

### Daemon-powered
- On start: index all source files into memory (parallel, tree-sitter + ctags + LSP)
- Incremental: update on file changes (git hooks or filesystem watch)
- Persist to `.cortex-cache/` on shutdown, reload on start

### New query commands
- `--query "impact src/foo.py"` — transitive dependents
- `--query "deps src/foo.py"` — full dependency chain
- `--query "hotspots"` — highest complexity + most dependents
- `--query "migration old_module new_module"` — trace impact

## Critical Source Files
- `src/domain/service/dependency_resolver.py` — where LSP edges feed in
- `src/infrastructure/parsers/import_dispatcher.py` — parser chain (standalone)
- `src/presentation/cli.py` — needs daemon/query flags
- `src/application/analyze_branch.py` — needs daemon-aware path
- `src/infrastructure/indexer/index_all.py` — current disk-based indexing

## Implementation Order

1. **Interface layer scaffold** — daemon server, client, protocol, query router
2. **Wire existing analysis through daemon** — same JSON output via socket
3. **LSP manager + clients** — boot clangd, jdtls, pyright, gopls
4. **DefinitionResolver port** — LSP adapter vs fuzzy matcher
5. **Wire LSP into dependency resolver** — higher-weight edges
6. **In-memory index** — full-repo, incremental
7. **Claude Code skill** — SKILL.md, references, scripts
8. **Full-repo queries** — impact, deps, hotspots, migration

## Verification
- `kapa-cortex --daemon` starts, `--status` shows LSPs running
- `kapa-cortex --query "analyze"` returns same JSON as `kapa-cortex --json`
- `kapa-cortex --json` still works without daemon (standalone fallback)
- `/kapa-cortex` in Claude Code loads the skill
- All tests passing throughout
