# mid_harness/_mcp.py
from __future__ import annotations

import logging
from dataclasses import dataclass

from mid_harness._tools import ToolDef, ToolCollisionError

logger = logging.getLogger("mid_harness.mcp")


class MCPConnectionError(Exception):
    pass


class MCPToolError(Exception):
    pass


@dataclass
class MCPServerConfig:
    name: str
    transport: str
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    cwd: str | None = None
    url: str | None = None
    headers: dict[str, str] | None = None
    auto_connect: bool = True
    reconnect_on_failure: bool = True
    health_check_interval_sec: int = 30
    timeout_sec: int = 10

    def __post_init__(self):
        if self.transport not in ("stdio", "http"):
            raise ValueError(f"transport must be 'stdio' or 'http', got '{self.transport}'")
        if self.transport == "stdio" and not self.command:
            raise ValueError("stdio transport requires 'command'")
        if self.transport == "http" and not self.url:
            raise ValueError("http transport requires 'url'")


class MCPToolProxy:
    def __init__(self, server_name: str, tool_spec: dict):
        self._server_name = server_name
        self._tool_name = tool_spec["name"]
        self._description = tool_spec.get("description", "")
        self._input_schema = tool_spec.get("inputSchema", {"type": "object", "properties": {}})

    @property
    def tool_name(self) -> str:
        return f"{self._server_name}.{self._tool_name}"

    def to_tool_def(self) -> ToolDef:
        return ToolDef(
            name=self.tool_name,
            description=f"[MCP:{self._server_name}] {self._description}",
            parameters=self._input_schema,
            risk_level="mcp",
        )

    async def __call__(self, args: dict) -> str:
        return f"[MCP] {self.tool_name}: server not connected"


class MCPManager:
    def __init__(self, agent):
        self._agent = agent
        self._servers: dict[str, dict] = {}
        self._tool_proxies: dict[str, MCPToolProxy] = {}

    async def connect_server(self, config: MCPServerConfig) -> bool:
        if config.name in self._servers:
            logger.warning("MCP server '%s' already connected, reconnecting", config.name)
            self._servers[config.name]["ref_count"] += 1
            return True

        self._servers[config.name] = {
            "config": config,
            "status": "connected",
            "ref_count": 1,
        }
        logger.info("MCP server '%s' connected (transport: %s)", config.name, config.transport)
        return True

    async def register_tools(self, server_name: str, tool_specs: list[dict]) -> list[str]:
        if server_name not in self._servers:
            raise MCPConnectionError(f"Server '{server_name}' is not connected")

        registered: list[str] = []
        for spec in tool_specs:
            proxy = MCPToolProxy(server_name, spec)
            tool_def = proxy.to_tool_def()
            try:
                self._agent.tools.register_mcp_proxy(tool_def, proxy)
                self._tool_proxies[tool_def.name] = proxy
                registered.append(tool_def.name)
            except ToolCollisionError as e:
                logger.warning("MCP tool '%s' collision: %s", tool_def.name, e)

        self._servers[server_name]["tools"] = registered
        logger.info(
            "Registered %d/%d tools for MCP server '%s'",
            len(registered), len(tool_specs), server_name,
        )
        return registered

    async def disconnect_server(self, name: str) -> None:
        if name not in self._servers:
            raise MCPConnectionError(f"Server '{name}' is not connected")

        self._servers[name]["ref_count"] -= 1
        if self._servers[name]["ref_count"] > 0:
            return

        tools = self._servers[name].get("tools", [])
        for tool_name in tools:
            self._tool_proxies.pop(tool_name, None)
        del self._servers[name]
        logger.info("MCP server '%s' disconnected (%d tools removed)", name, len(tools))

    async def health_check(self, name: str) -> bool:
        if name not in self._servers:
            return False
        return self._servers[name]["status"] == "connected"

    async def shutdown(self) -> None:
        for name in list(self._servers):
            await self.disconnect_server(name)
        logger.info("MCPManager shutdown complete")

    def list_servers(self) -> list[dict]:
        return [
            {
                "name": name,
                "transport": info["config"].transport,
                "status": info["status"],
                "tools": len(info.get("tools", [])),
                "ref_count": info["ref_count"],
            }
            for name, info in self._servers.items()
        ]

    @property
    def server_count(self) -> int:
        return len(self._servers)
