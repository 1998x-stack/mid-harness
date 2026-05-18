# ADR 003: MCP Client Integration

**Date**: 2026-05-16
**Status**: Proposed

## Context

tiny-harness tools are Python functions running in-process. This limits:
1. **Language choice**: Tools must be written in Python
2. **Process isolation**: All tools share the agent's memory and crash domain
3. **Ecosystem access**: Cannot use existing MCP servers from the growing ecosystem
4. **Distribution**: Tools must be installed as pip packages alongside the harness

The [Model Context Protocol](https://modelcontextprotocol.io) (MCP) is an emerging standard for connecting LLMs to external tools. Supporting MCP would let mid-harness use tools from any language, running in any process.

## Decision

**Build an MCP client into mid-harness.** MCP tools are wrapped as standard `ToolDef` objects and registered into the same `ToolRegistry` as Python tools. The agent loop treats all tools identically.

Two transport types supported:
- **stdio**: Spawn MCP server as a subprocess; communicate via stdin/stdout JSON-RPC
- **HTTP**: Connect to an HTTP MCP server with SSE streaming

## Rationale

1. **Unified tool model**: The agent loop doesn't distinguish between Python tools and MCP tools. Both are `(args: dict) -> str` handlers. This preserves the simplicity of the core loop.

2. **Proxy pattern**: MCP tools are wrapped as `MCPToolProxy` objects that conform to the `ToolDef` + handler interface. No changes to `ToolRegistry`, `ToolExecutor`, or `AgentLoop`.

3. **Process isolation**: MCP servers run in their own processes. A crash in a git MCP server doesn't affect the agent or other tools.

4. **Language freedom**: An MCP server can be written in any language (Node.js, Go, Rust, Python, etc.). The harness only cares about the JSON-RPC protocol.

5. **Ecosystem leverage**: The MCP ecosystem is growing rapidly. Supporting it lets mid-harness use existing MCP servers (PostgreSQL, Filesystem, GitHub, Slack, etc.) without writing Python wrappers.

6. **Declarative in skills**: Skills declare their MCP server requirements in `skill.md`. The loader spawns servers, discovers tools, and registers proxies automatically.

## Consequences

- `MCPManager` is a new subsystem managing MCP server lifecycle: spawn, health check, reconnect, shutdown.
- Tool names from MCP servers are prefixed with the server name (`git-server.git_commit`) to prevent collisions.
- MCP server failures must be handled gracefully: auto-reconnect, timeout on tool calls, fallback to error results.
- Skills load in phases: MCP bootstrap phase can fail independently without blocking the rest of the skill.
- Dev dependencies: MCP server tools (Node.js, npx, Python packages) must be available on the system. The harness doesn't install them.

## Alternatives Considered

### A. Native tools only (no MCP)
Keep tiny-harness's Python-only tool model. Rejected because it limits the ecosystem and requires reimplementing existing MCP servers in Python.

### B. MCP as a skill type only
MCP servers are a special "mcp_skill" type, separate from regular tools. Rejected because it creates two code paths in the agent loop. The proxy pattern is simpler.

### C. MCP server built into the harness
Run an MCP server inside the agent process. Rejected because it defeats process isolation — the whole point of MCP is external tool servers.

### D. gRPC/Thrift instead of MCP
Use a different RPC protocol. Rejected because MCP is purpose-built for LLM-tool interaction and has growing adoption. Building a custom protocol would require building an ecosystem from scratch.

### E. All tools via MCP (remove Python tool support)
Treat everything as an MCP server. Rejected because it adds process overhead for simple tools. A `read_file` Python handler is simpler and faster than spawning a subprocess.
