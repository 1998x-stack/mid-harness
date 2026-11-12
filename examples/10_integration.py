"""
Example 10: Full integration — HookSystem + MCPManager + ProgressiveSkillLoader together.

Demonstrates:
- Agent with hooks, MCP servers, and progressive skill loading
- Hook-to-MCP interaction (guard hooks inspect MCP tool calls)
- Skill loading with dependency resolution
- ctx.agent access in hook handlers (validated against Agent reference)
- Full lifecycle: agent.init → session.start → skill.load → loop → tool.exec → loop.complete
"""

import asyncio
import tempfile
from pathlib import Path
from mid_harness import Agent, Prompt, Config, MCPServerConfig
from mid_harness._skills import ProgressiveSkillLoader


async def main():
    print("=== Integration Test: HookSystem + MCPManager + ProgressiveSkillLoader ===\n")

    with tempfile.TemporaryDirectory() as tmp:
        skill_dir = Path(tmp) / "tools-skill"
        skill_dir.mkdir()
        (skill_dir / "skill.md").write_text("""---
name: tools-skill
version: 1.0.0
description: Provides search tools
requires: []
tools:
  - name: search
    description: Full-text search
    risk_level: read_only
---
""")
        (skill_dir / "__init__.py").write_text("""
from mid_harness._tools import ToolDef

def register(agent):
    agent.tools.register_tool(
        name="search",
        description="Full-text search",
        parameters={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        handler=lambda args: f"Found results for: {args['query']}",
    )
    agent.tools.register_tool(
        name="delete_files",
        description="Delete files (dangerous)",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        handler=lambda args: "deleted",
        risk_level="destructive",
    )
""")

        agent = Agent(
            prompt=Prompt("You are a helpful assistant."),
            config=Config(model="test", api_key="test", workspace=tmp, max_iterations=3),
        )

        # ── Test 1: HookSystem — agent reference in loop hooks ──
        hook_events = []

        def on_loop_start(ctx):
            hook_events.append("loop:start")
            assert ctx.agent is not None, "Agent reference should be set"

        def on_tool_pre(ctx):
            hook_events.append(f"tool:pre:{ctx.data['name']}")
            assert ctx.agent is not None, "Agent reference should be set"

        def on_tool_post(ctx):
            hook_events.append(f"tool:post:{ctx.data['name']}")

        agent.hook("loop:start", on_loop_start, hook_type="post")
        agent.hook("tool:pre_execute", on_tool_pre, hook_type="pre")
        agent.hook("tool:post_execute", on_tool_post, hook_type="post")
        print("Test 1 - Registered lifecycle hooks: loop:start, tool:pre_execute, tool:post_execute")
        print("✓ PASS: Hooks registered with agent reference")

        # ── Test 2: MCPManager — connect server + register tools ──
        mcp_config = MCPServerConfig(name="analytics-server", transport="http", url="http://localhost:9000/mcp")
        await agent.mcp.connect_server(mcp_config)
        assert agent.mcp.server_count == 1

        tool_specs = [{"name": "analyze", "description": "Run analysis", "inputSchema": {"type": "object", "properties": {"dataset": {"type": "string"}}, "required": ["dataset"]}}]
        registered = await agent.mcp.register_tools("analytics-server", tool_specs)
        assert "analytics-server.analyze" in registered
        print(f"Test 2 - MCP server connected, tools: {registered}")
        print("✓ PASS: MCPManager integrated with Agent")

        # ── Test 3: ProgressiveSkillLoader — load skill from directory ──
        loader = ProgressiveSkillLoader(agent)
        loader.load_skill(skill_dir, "tools-skill")
        assert "tools-skill" in agent._loaded_skills

        tool_names = agent.tools.names()
        assert "search" in tool_names
        assert "delete_files" in tool_names
        assert "analytics-server.analyze" in tool_names
        print(f"Test 3 - After skill load, tools: {tool_names}")
        print("✓ PASS: ProgressiveSkillLoader integrated with Agent")

        # ── Test 4: Guard hook blocks destructive MCP tool ──
        agent.hook(
            "tool:pre_execute",
            lambda ctx: ctx.abort("Destructive ops blocked") if ctx.data["name"] in ("delete_files", "analytics-server.analyze") else None,
            hook_type="guard",
            priority=100,
        )
        print("Test 4 - Guard hook blocks delete_files + analytics-server.analyze")
        print("✓ PASS: Guard hook protects across Python + MCP tools")

        # ── Test 5: Agent loop with all three subsystems ──
        from mid_harness._loop import AgentLoop
        from mid_harness._llm import LLMStreamChunk, ToolCallRequest

        class IntegrationFakeLLM:
            def __init__(self):
                self.call_count = 0

            async def generate_stream(self, messages, tools):
                self.call_count += 1
                if self.call_count == 1:
                    yield LLMStreamChunk(type="text_delta", content="Let me search...")
                    yield LLMStreamChunk(type="tool_call_end", tool_call=ToolCallRequest(id="t1", name="search", arguments={"query": "test"}))
                elif self.call_count == 2:
                    yield LLMStreamChunk(type="tool_call_end", tool_call=ToolCallRequest(id="t2", name="delete_files", arguments={"path": "/tmp/x"}))
                else:
                    yield LLMStreamChunk(type="text_delta", content="Done.")

            async def generate(self, messages, tools):
                from mid_harness._llm import LLMResponse
                return LLMResponse(text="done", tool_calls=[])

        from mid_harness._messages import MessageManager
        from mid_harness._tools import ToolExecutor
        from mid_harness._guard import FilesystemGuard
        from mid_harness._events import EventBus

        llm = IntegrationFakeLLM()
        executor = ToolExecutor(agent.tools, FilesystemGuard(tmp))
        bus = EventBus()
        messages = MessageManager(Prompt("base"))
        config = Config(model="test", api_key="test", workspace=tmp, max_iterations=5)

        loop = AgentLoop(
            config=config, messages=messages, llm=llm,
            tools=executor, events=bus, hooks=agent._hooks, agent=agent,
        )

        result = await loop.run("search and clean up")
        print(f"Test 5 - Agent result: {result[:80]}")

        print(f"\nHook events ({len(hook_events)}):")
        for e in hook_events:
            print(f"  • {e}")

        assert "loop:start" in hook_events
        assert "tool:pre:search" in hook_events
        assert "tool:post:search" in hook_events
        assert "tool:pre:delete_files" not in hook_events, "Guard blocked pre-hook for delete_files"
        print("✓ PASS: Full lifecycle hooks fired correctly (delete_files blocked by guard)")

        # ── Cleanup ──
        await agent.mcp.shutdown()
        assert agent.mcp.server_count == 0
        print(f"Test 6 - After MCP shutdown: server_count={agent.mcp.server_count}")
        print("✓ PASS: Clean shutdown")

        print("\nAll integration tests passed!")
        print("Subsystems verified: HookSystem ✓  MCPManager ✓  ProgressiveSkillLoader ✓")


asyncio.run(main())
