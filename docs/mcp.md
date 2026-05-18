# MCP Integration Design

**Model Context Protocol client for connecting to remote tool servers.**

## 1. What is MCP?

The [Model Context Protocol](https://modelcontextprotocol.io) (MCP) is an open standard that enables LLM applications to communicate with external tools, data sources, and services through a unified protocol based on JSON-RPC 2.0.

```
┌──────────────┐          JSON-RPC 2.0          ┌──────────────┐
│  MCP Client  │ ◄──────────────────────────► │  MCP Server  │
│ (mid-harness)│    tools/list, tools/call      │  (any lang)  │
└──────────────┘                                └──────────────┘
```

**MCP servers expose:**
- **Tools**: Model-executable functions with JSON Schema parameters
- **Resources**: Structured data the model can read (files, DB records, APIs)
- **Prompts**: Pre-written prompt templates

**mid-harness acts as an MCP client**: it connects to MCP servers, discovers their tools, and proxies tool calls through the agent loop.

## 2. Architecture

```
┌──────────────────────────────────────────────────────────┐
│                     mid-harness Agent                      │
│                                                            │
│  ┌──────────────────────┐     ┌────────────────────────┐  │
│  │    MCPManager         │     │   ToolRegistry          │  │
│  │                       │     │                         │  │
│  │  ┌─────────────────┐  │     │  ┌───────────────────┐  │  │
│  │  │ MCPServer       │──┼─────┼─►│ read_file         │  │  │
│  │  │ "git-server"    │  │     │  │ (Python handler)  │  │  │
│  │  │ (subprocess)    │  │     │  └───────────────────┘  │  │
│  │  └─────────────────┘  │     │  ┌───────────────────┐  │  │
│  │  ┌─────────────────┐  │     │  │ git_log           │  │  │
│  │  │ MCPServer       │──┼─────┼─►│ (MCP proxy)       │  │  │
│  │  │ "db-server"     │  │     │  └───────────────────┘  │  │
│  │  │ (HTTP)          │  │     │  ┌───────────────────┐  │  │
│  │  └─────────────────┘  │     │  │ query_db          │  │  │
│  │                       │     │  │ (MCP proxy)       │  │  │
│  │  • spawn/connect      │     │  └───────────────────┘  │  │
│  │  • health check       │     │                         │  │
│  │  • reconnect          │     └────────────────────────┘  │
│  │  • tool discovery     │                                  │
│  │  • lifecycle          │                                  │
│  └──────────────────────┘                                  │
└──────────────────────────────────────────────────────────┘
```

**Key design principle**: MCP tools are wrapped as standard `ToolDef` objects and registered into the same `ToolRegistry` as Python tools. The agent loop treats all tools identically — it doesn't know or care whether a tool is local Python or remote MCP.

## 3. Transport Layer

mid-harness supports two MCP transport types:

### 3.1 stdio Transport

Spawns the MCP server as a child process and communicates via stdin/stdout using newline-delimited JSON-RPC.

```yaml
# skill.md
mcp_servers:
  - name: git-server
    transport: stdio
    command: python
    args: ["-m", "git_mcp_server"]
    env:
      GIT_WORKSPACE: "${WORKSPACE}"
    cwd: "${WORKSPACE}"
```

```python
# Internal: MCPServer spawns a subprocess
class StdioMCPServer(MCPServer):
    async def connect(self):
        self._process = await asyncio.create_subprocess_exec(
            self.config.command, *self.config.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, **self.config.env},
            cwd=self.config.cwd,
        )
        # Send initialize request
        await self._send_jsonrpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "clientInfo": {"name": "mid-harness", "version": "0.1.0"},
        })
        # Receive initialize response
        response = await self._read_jsonrpc()

        # Send initialized notification
        await self._send_jsonrpc("notifications/initialized", {})

        # Discover tools
        tools = await self._send_jsonrpc("tools/list", {})
        self._available_tools = tools["tools"]

    async def _send_jsonrpc(self, method: str, params: dict) -> dict:
        request = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
            "params": params,
        }
        self._process.stdin.write(json.dumps(request).encode() + b"\n")
        await self._process.stdin.drain()
        line = await self._process.stdout.readline()
        return json.loads(line.decode())

    async def disconnect(self):
        self._process.terminate()
        await self._process.wait()
```

### 3.2 HTTP Transport (SSE)

Connects to an MCP server running as an HTTP service. Uses Server-Sent Events (SSE) for server-to-client streaming and HTTP POST for client-to-server requests.

```yaml
# skill.md
mcp_servers:
  - name: db-server
    transport: http
    url: "http://localhost:8080/mcp"
    headers:
      Authorization: "Bearer ${DB_MCP_TOKEN}"
```

```python
class HTTPMCPServer(MCPServer):
    async def connect(self):
        self._client = httpx.AsyncClient(base_url=self.config.url)

        # Initialize via POST
        response = await self._client.post("/", json={
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "mid-harness", "version": "0.1.0"},
            },
        })
        init_result = response.json()

        # Open SSE stream for server-to-client messages
        self._sse = await self._client.stream("GET", "/sse")
        # ... handle SSE events ...

        # Discover tools
        tools_response = await self._client.post("/", json={
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/list",
            "params": {},
        })
        self._available_tools = tools_response.json()["tools"]
```

## 4. Tool Proxy Pattern

MCP tools are wrapped as Python `ToolDef` objects so the existing tool system handles them seamlessly.

### 4.1 Wrapping an MCP Tool

```python
class MCPToolProxy:
    """Wraps an MCP tool as a callable handler compatible with ToolDef."""

    def __init__(self, mcp_server: "MCPServer", tool_def: dict):
        self._server = mcp_server
        self._tool_name = tool_def["name"]
        self._tool_description = tool_def.get("description", "")
        self._input_schema = tool_def.get("inputSchema", {"type": "object"})

    async def __call__(self, args: dict) -> str:
        """Execute the tool via MCP and return the result as a string."""
        result = await self._server.call_tool(self._tool_name, args)
        return self._format_result(result)

    def to_tool_def(self) -> ToolDef:
        """Convert MCP tool schema to a ToolDef."""
        return ToolDef(
            name=self._tool_name,
            description=f"[MCP] {self._tool_description}",
            parameters=self._input_schema,
            risk_level="read_only",  # Default; can be overridden via metadata
        )
```

### 4.2 Registration Flow

```python
# In MCPManager.connect_server():
async def connect_server(self, server_config: MCPServerConfig) -> MCPServer:
    server = self._create_server(server_config)
    await server.connect()

    # Wrap each MCP tool and register it
    for mcp_tool in server.available_tools:
        proxy = MCPToolProxy(server, mcp_tool)
        self._agent.tools.register_from_def(
            proxy.to_tool_def(),
            handler=proxy,
        )

    self._servers.append(server)
    return server
```

### 4.3 Tool Naming Convention

To avoid name collisions between MCP tools and Python tools, MCP tools are prefixed with the server name:

```
Python tools:  read_file, write_file, list_directory
MCP tools:     git-server.git_log, git-server.git_commit
                db-server.query, db-server.migrate
```

The agent loop sees all of them as equals:

```
ToolRegistry:
  read_file         → Python handler
  write_file        → Python handler
  git-server.git_log → MCP proxy → git-server process
  git-server.git_commit → MCP proxy → git-server process
  db-server.query   → MCP proxy → db-server HTTP
```

## 5. MCPManager API

```python
class MCPManager:
    """Manages MCP server lifecycle and tool registration."""

    def __init__(self, agent: "Agent"):
        self._agent = agent
        self._servers: list[MCPServer] = []

    async def connect_server(self, config: MCPServerConfig) -> MCPServer:
        """Connect to an MCP server and register its tools."""
        ...

    async def disconnect_server(self, name: str) -> None:
        """Disconnect an MCP server and unregister its tools."""
        ...

    async def reconnect_server(self, name: str) -> MCPServer:
        """Reconnect a failed server."""
        ...

    async def health_check(self, name: str) -> bool:
        """Check if an MCP server is responsive."""
        ...

    async def shutdown(self) -> None:
        """Disconnect all servers."""
        ...

    @property
    def server_status(self) -> dict[str, str]:
        """Get status of all connected servers."""
        return {s.name: s.status for s in self._servers}
```

## 6. MCP Configuration

### 6.1 In skill.md (Recommended)

```yaml
---
name: postgres
version: 1.0.0

mcp_servers:
  - name: postgres-server
    transport: stdio
    command: npx
    args: ["-y", "@modelcontextprotocol/server-postgres"]
    env:
      DATABASE_URL: "${POSTGRES_URL}"
    auto_connect: true                # Connect on skill load
    reconnect_on_failure: true        # Auto-reconnect
    health_check_interval_sec: 30     # Periodic health checks

  - name: redis-server
    transport: http
    url: "http://localhost:3001/mcp"
    auto_connect: true
---
```

### 6.2 In Config (Programmatic)

```python
from mid_harness import Config, MCPServerConfig

config = Config(
    model="claude-sonnet-4-20250514",
    api_key="...",
    workspace=".",
    mcp_servers=[
        MCPServerConfig(
            name="git-server",
            transport="stdio",
            command="python",
            args=["-m", "git_mcp_server"],
            auto_connect=True,
        ),
        MCPServerConfig(
            name="db-server",
            transport="http",
            url="http://localhost:8080/mcp",
            auto_connect=True,
            headers={"Authorization": f"Bearer {os.environ['DB_MCP_TOKEN']}"},
        ),
    ],
)
```

### 6.3 MCPServerConfig Reference

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | `str` | ✓ | Unique server identifier |
| `transport` | `"stdio" \| "http"` | ✓ | Transport protocol |
| `command` | `str` | stdio only | Command to execute |
| `args` | `list[str]` | — | Command arguments |
| `env` | `dict[str, str]` | — | Environment variables (`${ENV_VAR}` supported) |
| `cwd` | `str` | — | Working directory |
| `url` | `str` | HTTP only | Server URL |
| `headers` | `dict[str, str]` | — | HTTP headers |
| `auto_connect` | `bool` | — | Connect on agent init (default: `true`) |
| `reconnect_on_failure` | `bool` | — | Auto-reconnect (default: `true`) |
| `health_check_interval_sec` | `int` | — | Health check interval (default: `30`) |
| `timeout_sec` | `int` | — | Connection timeout (default: `10`) |

## 7. Error Handling and Recovery

### 7.1 Connection Failures

```python
class MCPManager:
    async def connect_server(self, config: MCPServerConfig) -> MCPServer:
        try:
            server = self._create_server(config)
            await asyncio.wait_for(server.connect(), timeout=config.timeout_sec)
            return server
        except asyncio.TimeoutError:
            raise MCPConnectionError(f"Server '{config.name}' connection timed out")
        except Exception as e:
            if config.reconnect_on_failure:
                asyncio.create_task(self._retry_connect(config))
            raise MCPConnectionError(f"Server '{config.name}' failed: {e}")
```

### 7.2 Tool Call Failures

When an MCP tool call fails, the error is fed back as a tool result — just like Python tool failures. The LLM reads the error and self-corrects:

```
Tool 'git-server.git_commit' failed: Connection lost to git-server.
The server may have crashed. Try git-server.git_status to check.
→ LLM: "The git server appears to be down. Let me check its status..."
→ LLM calls: git-server.git_status
→ Response: "Server is offline. Attempting reconnect..."
```

### 7.3 Server Crashes

```python
class MCPServer:
    async def call_tool(self, name: str, args: dict) -> str:
        try:
            return await self._send_jsonrpc("tools/call", {
                "name": name,
                "arguments": args,
            })
        except (ConnectionError, ProcessLookupError) as e:
            if self.config.reconnect_on_failure:
                self._status = "reconnecting"
                await self.reconnect()
                return await self._send_jsonrpc("tools/call", {
                    "name": name,
                    "arguments": args,
                })
            raise MCPToolError(f"MCP server '{self.config.name}' is offline: {e}")
```

## 8. Integration with Skills

### 8.1 Skill Declares MCP Servers

A progressive skill declares its MCP dependencies in `skill.md`:

```yaml
---
name: postgres
version: 1.0.0
mcp_servers:
  - name: postgres-server
    transport: stdio
    command: npx
    args: ["-y", "@modelcontextprotocol/server-postgres"]
    env:
      DATABASE_URL: "${POSTGRES_URL}"
---
```

When the skill is loaded, mid-harness:
1. Parses the YAML frontmatter
2. Spawns each declared MCP server
3. Discovers available tools from each server
4. Registers them as `ToolDef` objects in the ToolRegistry

### 8.2 Skill Unload Cleans Up MCP

When a skill is unloaded:
1. All tools from that skill's MCP servers are unregistered
2. MCP servers spawned by that skill are disconnected
3. Other skills' MCP servers are unaffected

## 9. Design Decisions

| Decision | Rationale |
|---|---|
| MCP tools prefixed with server name | Prevents name collisions. `git-server.git_commit` vs `python-git.git_commit`. |
| MCP proxy pattern (wrapping as ToolDef) | Agent loop treats all tools identically. No special casing for MCP. |
| stdio as primary transport | Simpler than HTTP for single-machine use. No port conflicts. No auth needed. |
| HTTP supported for remote/deployed servers | Enables connecting to MCP servers on other machines or cloud services. |
| Auto-reconnect on failure | MCP servers are subprocesses — they crash. Auto-reconnect is essential UX. |
| Health check interval | Proactive detection of dead servers before the LLM tries to call them. |
| Environment variable expansion (`${VAR}`) | Allows MCP server config to reference agent's environment without hardcoding. |
| One MCPManager per Agent | Simplifies lifecycle. Kill all servers when agent shuts down. |
