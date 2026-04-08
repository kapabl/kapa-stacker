# MCP Server

kapa-cortex includes a built-in MCP (Model Context Protocol) server. This
lets AI agents like Claude Code, Cursor, Windsurf, or any MCP-compatible
client query the code index directly — definitions, references, impact
analysis, call graphs — without reading source files.

## Starting the Server

```bash
kapa-cortex mcp
```

This starts a JSON-RPC 2.0 server on stdio (stdin/stdout). The server
auto-starts the daemon and index if they're not already running.

## Integration with Claude Code

Add to your Claude Code MCP config (`~/.claude/settings.json` or project
`.mcp.json`):

```json
{
  "mcpServers": {
    "kapa-cortex": {
      "command": "kapa-cortex",
      "args": ["mcp"]
    }
  }
}
```

Or install the Claude Code skill instead (auto-triggers on code questions):

```bash
kapa-cortex install-skill
```

## Integration with Cursor / Windsurf / Other MCP Clients

Any MCP client that supports stdio transport can use kapa-cortex. Point it
at the binary with the `mcp` argument:

```json
{
  "command": "/path/to/kapa-cortex",
  "args": ["mcp"],
  "transport": "stdio"
}
```

## Protocol

- **Transport**: stdio (JSON-RPC 2.0, one JSON object per line)
- **Protocol version**: 2024-11-05
- **Server info**: `kapa-cortex` v0.6.0

### Lifecycle

```
Client                          Server
  |  initialize                    |
  |------------------------------->|
  |  {"protocolVersion": ...}      |
  |<-------------------------------|
  |  notifications/initialized     |
  |------------------------------->|
  |  tools/list                    |
  |------------------------------->|
  |  {"tools": [...]}              |
  |<-------------------------------|
  |  tools/call {name, arguments}  |
  |------------------------------->|
  |  {"content": [...]}            |
  |<-------------------------------|
```

## Available Tools

### defs
Find all definitions of a symbol, scoped by class/namespace.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| symbol | string | yes | Symbol name (bare or FQN like `MyClass::method`) |

**Example request:**
```json
{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
  "name": "defs", "arguments": {"symbol": "solveConstraints"}
}}
```

### inspect
Inspect a symbol: signature, callers, callees, overrides.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| symbol | string | yes | Symbol name |

### refs
Find all references to a symbol via LSP.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| symbol | string | yes | Symbol name |

### rdeps
Reverse dependencies: what files or callers break if this target changes.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| target | string | yes | File path or symbol name |

### deps
Forward dependencies: what this file imports/includes.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| target | string | yes | File path |

### trace
Trace the call path between two symbols.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| source | string | yes | Source symbol |
| target | string | yes | Target symbol |

### symbols
List all symbols defined in a file.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| file | string | yes | File path |

### hotspots
Rank files by risk (complexity * dependents).

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| limit | integer | no | Max results (default 20) |

### status
Index stats and daemon health. No parameters.

## Response Format

All tools return content as a text block containing JSON:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "content": [
      {"type": "text", "text": "{\"query\": \"lookup\", \"symbol\": \"foo\", ...}"}
    ]
  }
}
```

On error:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "content": [{"type": "text", "text": "Symbol not found: foo"}],
    "isError": true
  }
}
```

## How It Works

The MCP server is a thin JSON-RPC wrapper around the daemon. Each tool
call maps to a daemon query action:

| MCP Tool | Daemon Action |
|----------|--------------|
| defs | lookup |
| inspect | explain |
| refs | refs |
| rdeps | impact |
| deps | deps |
| trace | trace |
| symbols | symbols |
| hotspots | hotspots |
| status | status |

The daemon maintains warm LSP connections and an in-memory SQLite index,
so queries return in under 200ms.
