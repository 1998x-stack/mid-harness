"""
Example 07: End-to-end hook chain with FakeLLM agent loop.

Demonstrates full hook pipeline:
- Guard hooks blocking dangerous tools
- Pre-hooks modifying tool args
- Tool execution with modified args
- Post-hooks observing results
- Loop hooks tracking iteration state
- LLM hooks tracking API calls
"""

import asyncio
from mid_harness._hooks import HookSystem
from mid_harness._config import AgentConfig, Prompt
from mid_harness._messages import MessageManager
from mid_harness._events import EventBus
from mid_harness._tools import ToolRegistry, ToolDef, ToolExecutor
from mid_harness._guard import FilesystemGuard
from mid_harness._llm import LLMStreamChunk, ToolCallRequest
from mid_harness._loop import AgentLoop


# ── Fake LLM that returns tool calls ──
class FakeLLM:
    def __init__(self, dialogs=None):
        self.dialogs = dialogs or []
        self.call_index = 0

    async def generate_stream(self, messages, tools):
        if self.call_index >= len(self.dialogs):
            yield LLMStreamChunk(type="text_delta", content="done")
            return
        dialog = self.dialogs[self.call_index]
        self.call_index += 1
        if isinstance(dialog, str):
            yield LLMStreamChunk(type="text_delta", content=dialog)
        elif isinstance(dialog, list):
            yield LLMStreamChunk(type="text_delta", content="calling tools...")
            for tc in dialog:
                yield LLMStreamChunk(type="tool_call_end", tool_call=tc)

    async def generate(self, messages, tools):
        from mid_harness._llm import LLMResponse

        return LLMResponse(text="degraded finish", tool_calls=[])


async def main():
    # ── Setup ──
    hs = HookSystem()
    chain_log = []

    # Guard: block "delete" tool
    hs.register(
        "tool:pre_execute",
        lambda ctx: (
            ctx.abort("delete blocked") if ctx.data["name"] == "delete_file" else None
        ),
        hook_type="guard",
        priority=100,
    )

    # Pre-hook: add timestamp to all tool args
    def add_timestamp(ctx):
        args = dict(ctx.data.get("args", {}))
        args["_timestamp"] = 1234567890
        ctx.modify("args", args)

    hs.register("tool:pre_execute", add_timestamp, hook_type="pre", priority=50)

    # Post-hook: observe results
    hs.register(
        "tool:post_execute",
        lambda ctx: chain_log.append(
            f"post:{ctx.data['name']}:{ctx.data.get('result', '')[:20]}"
        ),
        hook_type="post",
    )
    hs.register(
        "tool:error",
        lambda ctx: chain_log.append(
            f"error:{ctx.data['name']}:{ctx.data.get('error', '')[:30]}"
        ),
        hook_type="post",
    )

    # Loop hooks
    hs.register(
        "loop:start", lambda ctx: chain_log.append("loop:start"), hook_type="post"
    )
    hs.register(
        "loop:iteration",
        lambda ctx: chain_log.append(f"iter:{ctx.data['num']}"),
        hook_type="post",
    )
    hs.register(
        "loop:complete", lambda ctx: chain_log.append("loop:complete"), hook_type="post"
    )

    # LLM hooks
    hs.register(
        "llm:pre_call", lambda ctx: chain_log.append("llm:pre"), hook_type="pre"
    )
    hs.register(
        "llm:post_call", lambda ctx: chain_log.append("llm:post"), hook_type="post"
    )

    # ── Register tools ──
    registry = ToolRegistry()

    def echo_handler(args):
        return args.get("msg", "no message")

    def write_handler(args):
        return f"wrote to {args.get('path', 'unknown')}"

    registry.register_from_def(
        ToolDef(
            name="echo",
            description="Echo",
            parameters={
                "type": "object",
                "properties": {"msg": {"type": "string"}},
                "required": ["msg"],
            },
        ),
        echo_handler,
    )
    registry.register_from_def(
        ToolDef(
            name="write_file",
            description="Write file",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
            risk_level="mutation",
        ),
        write_handler,
    )

    # ── Create AgentLoop with hooks ──
    config = AgentConfig(
        model="test", api_key="test", workspace="/tmp", max_iterations=5
    )
    prompt = Prompt("base")
    messages = MessageManager(prompt)
    guard = FilesystemGuard("/tmp")
    executor = ToolExecutor(registry, guard)
    bus = EventBus()

    # LLM that calls echo then stops
    llm = FakeLLM(
        dialogs=[
            [ToolCallRequest(id="tc1", name="echo", arguments={"msg": "hello world"})],
            "final answer",
        ]
    )

    loop = AgentLoop(
        config=config, messages=messages, llm=llm, tools=executor, events=bus, hooks=hs
    )

    # ── Run ──
    print("=== Running agent with full hook chain ===")
    result = await loop.run("test prompt")
    print(f"\nAgent result: {result}")

    # ── Report ──
    print(f"\nHook chain log ({len(chain_log)} events):")
    for entry in chain_log:
        print(f"  • {entry}")

    # ── Verify expected order ──
    expected_order = [
        "loop:start",
        "iter:1",
        "llm:pre",
        "llm:post",
        "post:echo:hello world",
        "iter:2",
        "llm:pre",
        "llm:post",
        "loop:complete",
    ]
    print(f"\nExpected order: {expected_order}")
    assert chain_log == expected_order, (
        f"Order mismatch:\nGot:      {chain_log}\nExpected: {expected_order}"
    )
    print("✓ PASS: Full hook chain order correct")

    # ── Test 2: Guard blocks delete ──
    chain_log.clear()
    llm2 = FakeLLM(
        dialogs=[
            [
                ToolCallRequest(
                    id="tc_del", name="delete_file", arguments={"path": "/tmp/x"}
                )
            ],
            "ok, can't delete",
        ]
    )
    config2 = AgentConfig(
        model="test", api_key="test", workspace="/tmp", max_iterations=5
    )
    prompt2 = Prompt("base")
    messages2 = MessageManager(prompt2)
    executor2 = ToolExecutor(registry, guard)
    bus2 = EventBus()
    loop2 = AgentLoop(
        config=config2,
        messages=messages2,
        llm=llm2,
        tools=executor2,
        events=bus2,
        hooks=hs,
    )
    result2 = await loop2.run("try delete")
    print("\n=== Test 2: Guard blocks delete ===")
    print(f"Result: {result2}")
    assert any("error:delete_file" in entry for entry in chain_log), (
        f"Expected error log, got: {chain_log}"
    )
    print(f"Hook chain: {chain_log}")
    print("✓ PASS: Guard blocked delete, error hook fired")

    print("\nAll end-to-end hook tests passed!")


asyncio.run(main())
