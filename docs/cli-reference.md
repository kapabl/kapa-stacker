# CLI Reference

All commands support `--json` (structured JSON) and `--brief` (compact,
token-efficient for AI agents) output modes unless noted otherwise.

## Daemon

```bash
kapa-cortex daemon start          # start daemon, boot LSP servers, listen on socket
kapa-cortex daemon stop           # stop running daemon
kapa-cortex daemon status         # show daemon health + index stats
```

Query commands auto-start the daemon if it's not running. The daemon
boots one LSP server per detected language and keeps them warm for
sub-200ms queries.

## Indexing

```bash
kapa-cortex index                 # index current directory
kapa-cortex index /path/to/repo   # index a specific directory
kapa-cortex reindex               # re-index all files in the database
kapa-cortex reindex src/auth.rs src/db.rs   # re-index specific files
```

The index is stored in `.cortex-cache/index.db` (SQLite). It contains
symbols, imports, dependency edges, call graphs, and file hashes for
incremental updates.

## Symbol Queries

### defs — Find definitions

```bash
kapa-cortex defs MyClass                   # all definitions of MyClass
kapa-cortex defs MyClass::method           # scoped by class/namespace
kapa-cortex defs solveConstraints --json   # JSON output
kapa-cortex defs solveConstraints --brief  # compact output
```

Returns: FQN, kind (function/class/struct/...), file, line, scope.

### inspect — Symbol deep dive

```bash
kapa-cortex inspect MyClass::method        # signature, callers, callees, overrides
kapa-cortex inspect btDynamicsWorld::solveConstraints --brief
```

Returns: definition location, source signature, callers (who calls this),
callees (what this calls), overrides (same name in different scopes).

### refs — Find references (via LSP)

```bash
kapa-cortex refs MyClass::method           # all references via LSP
kapa-cortex refs foo bar baz               # batch: multiple symbols
kapa-cortex refs MyClass::method --brief   # grouped by file
```

Requires a running LSP server for the target language. Returns file + line
for every reference, grouped by file.

### symbols — List symbols in a file

```bash
kapa-cortex symbols src/auth.rs            # all symbols defined in this file
kapa-cortex symbols src/auth.rs --brief    # compact, skips locals/parameters
```

Returns: name, kind, line, scope for each symbol.

## Dependency Queries

### deps — Forward dependencies

```bash
kapa-cortex deps src/auth.rs               # what this file imports/includes
kapa-cortex deps src/auth.rs --json
```

Returns: transitive list of files that this file depends on.

### rdeps — Reverse dependencies (impact)

```bash
kapa-cortex rdeps src/auth.rs              # what breaks if this file changes
kapa-cortex rdeps MyClass::method          # what breaks if this symbol changes
kapa-cortex rdeps src/auth.rs --brief
```

Accepts both file paths and symbol names. For files: shows direct and
transitive dependents. For symbols: shows callers and affected files.

### hotspots — Risk ranking

```bash
kapa-cortex hotspots                       # top 20 riskiest files
kapa-cortex hotspots --limit 50            # top 50
kapa-cortex hotspots --brief
```

Ranks files by score = complexity * dependents. High score = high risk
if changed.

## Call Graph

### trace — Call path between symbols

```bash
kapa-cortex trace source_fn target_fn      # find call path
kapa-cortex trace Foo::init Bar::cleanup --brief
```

Returns: ordered list of function hops from source to target.

## Branch Analysis

### analyze — Propose stacked PRs

```bash
kapa-cortex analyze                        # analyze current branch vs auto-detected base
kapa-cortex analyze --base develop         # custom base branch
kapa-cortex analyze --max-files 5          # max files per PR (default: 3)
kapa-cortex analyze --max-lines 400        # max lines per PR (default: 200)
kapa-cortex analyze --json
kapa-cortex analyze --brief
```

Groups changed files into stacked PRs. Docs go first. Test files stay
with their implementation. Splits by module, respects file/line limits.
Each PR gets a title (from diff analysis), risk level, merge strategy,
and dependency chain.

### extract — Pull files into a PR branch

```bash
kapa-cortex extract "gradle files"                          # match by keyword
kapa-cortex extract "src/core/ changes"                     # match by path prefix
kapa-cortex extract "*.bxl"                                 # match by glob
kapa-cortex extract "auth changes" --branch pr/auth-refactor  # create branch
kapa-cortex extract "test files" --base develop --json
```

Matches files using glob patterns, path prefixes, and keyword detection.
Automatically pulls in paired test files. With `--branch`, creates a new
git branch containing only the matched files.

## Buck2 Build System

```bash
kapa-cortex buck2 targets                  # list all targets
kapa-cortex buck2 targets --rule rust_library   # filter by rule type
kapa-cortex buck2 owner src/main.rs        # find which target owns a file
kapa-cortex buck2 deps //app:lib           # target dependencies
kapa-cortex buck2 rdeps //app:lib          # reverse target dependencies
```

Parses TARGETS files using the starlark_syntax crate. Buck2 subcommands
support `--brief` output mode.

## MCP Server

```bash
kapa-cortex mcp                            # start MCP stdio server
```

See [mcp.md](mcp.md) for protocol details and integration instructions.

## Utilities

```bash
kapa-cortex status                         # daemon health + index stats (JSON)
kapa-cortex install-skill                  # install Claude Code skill to ~/.claude/
```
