"""
Example 08: MCPManager lifecycle — server config, connect, register tools, disconnect.

Demonstrates:
- MCPServerConfig validation (stdio/http transport checks)
- MCPManager connect/disconnect/shutdown lifecycle
- MCPToolProxy wrapping and tool registration
- Reference counting for shared servers
- Health check and server listing
"""

import asyncio
from mid_harness import Agent, Prompt, Config, MCPServerConfig

agent = Agent(
    prompt=Prompt("You are a helpful assistant."),
    config=Config(model="test", api_key="test", workspace="/tmp"),
)

mcp = agent.mcp


def test_config_validation():
    print("=== MCP Config Validation ===")

    config = MCPServerConfig(name="git-server", transport="stdio", command="python", args=["-m", "git_mcp"])
    assert config.name == "git-server"
    assert config.transport == "stdio"
    print(f"Test 1 - stdio config: name={config.name}, transport={config.transport}")
    print("✓ PASS: stdio config valid")

    config2 = MCPServerConfig(name="db-server", transport="http", url="http://localhost:8080/mcp")
    assert config2.transport == "http"
    assert config2.url == "http://localhost:8080/mcp"
    print(f"Test 2 - http config: name={config2.name}, url={config2.url}")
    print("✓ PASS: http config valid")

    try:
        MCPServerConfig(name="bad", transport="gRPC", command="x")
        assert False, "Should have raised"
    except ValueError as e:
        print(f"Test 3 - invalid transport: {e}")
        assert "transport must be" in str(e)
    print("✓ PASS: invalid transport rejected")

    try:
        MCPServerConfig(name="bad2", transport="stdio")
        assert False, "Should have raised"
    except ValueError as e:
        print(f"Test 4 - stdio without command: {e}")
        assert "requires 'command'" in str(e)
    print("✓ PASS: stdio requires command")

    try:
        MCPServerConfig(name="bad3", transport="http")
        assert False, "Should have raised"
    except ValueError as e:
        print(f"Test 5 - http without url: {e}")
        assert "requires 'url'" in str(e)
    print("✓ PASS: http requires url")


async def test_connect_disconnect():
    print("\n=== MCP Connect/Disconnect ===")

    config = MCPServerConfig(name="git-server", transport="stdio", command="python", args=["-m", "git_mcp"])
    ok = await mcp.connect_server(config)
    assert ok
    assert mcp.server_count == 1
    assert await mcp.health_check("git-server")
    print(f"Test 6 - connected: server_count={mcp.server_count}, healthy={await mcp.health_check('git-server')}")
    print("✓ PASS: server connected and healthy")

    await mcp.disconnect_server("git-server")
    assert mcp.server_count == 0
    assert not await mcp.health_check("git-server")
    print(f"Test 7 - disconnected: server_count={mcp.server_count}, healthy={await mcp.health_check('git-server')}")
    print("✓ PASS: server disconnected")


async def test_tool_proxy_registration():
    print("\n=== MCP Tool Registration ===")

    config = MCPServerConfig(name="db-server", transport="http", url="http://localhost:3000/mcp")
    await mcp.connect_server(config)

    tool_specs = [
        {"name": "query", "description": "Run SQL query", "inputSchema": {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]}},
        {"name": "migrate", "description": "Apply migrations", "inputSchema": {"type": "object", "properties": {}}},
    ]
    registered = await mcp.register_tools("db-server", tool_specs)
    assert len(registered) == 2
    assert "db-server.query" in registered
    assert "db-server.migrate" in registered

    tool_names = agent.tools.names()
    for name in registered:
        assert name in tool_names, f"Expected '{name}' in tool registry"
    print(f"Test 8 - tools registered: {registered}")
    print(f"Test 8 - in ToolRegistry: {[n for n in tool_names if 'db-server' in n]}")
    print("✓ PASS: MCP tools registered in ToolRegistry")

    servers = mcp.list_servers()
    print(f"Test 9 - server list: {servers}")
    assert len(servers) == 1
    assert servers[0]["tools"] == 2
    print("✓ PASS: server listing correct")


async def test_ref_counting():
    print("\n=== MCP Reference Counting ===")

    config = MCPServerConfig(name="shared-server", transport="stdio", command="echo")
    await mcp.connect_server(config)
    assert mcp.server_count == 2

    await mcp.connect_server(MCPServerConfig(name="shared-server", transport="stdio", command="echo"))
    info = mcp.list_servers()
    shared = [s for s in info if s["name"] == "shared-server"][0]
    assert shared["ref_count"] == 2
    print(f"Test 10 - ref_count after 2 connects: {shared['ref_count']}")
    print("✓ PASS: ref_count increments")

    await mcp.disconnect_server("shared-server")
    info2 = mcp.list_servers()
    shared2 = [s for s in info2 if s["name"] == "shared-server"]
    assert len(shared2) == 1
    assert shared2[0]["ref_count"] == 1
    print(f"Test 11 - ref_count after disconnect: {shared2[0]['ref_count']}")
    print("✓ PASS: server stays connected until last ref releases")


async def test_shutdown():
    print("\n=== MCP Shutdown ===")

    await mcp.shutdown()
    assert mcp.server_count == 0
    assert mcp.list_servers() == []
    print(f"Test 12 - after shutdown: server_count={mcp.server_count}")
    print("✓ PASS: shutdown cleans all servers")


async def main():
    test_config_validation()
    await test_connect_disconnect()
    await test_tool_proxy_registration()
    await test_ref_counting()
    await test_shutdown()
    print("\nAll MCPManager tests passed!")


asyncio.run(main())
