"""
Example 01: Basic hook registration, priority ordering, and hook types.

Demonstrates:
- Registering guard, pre, and post hooks
- Priority ordering (higher priority runs first)
- User hooks before skill hooks at same priority
- FIFO tiebreaker for same priority and source
"""

import asyncio
from mid_harness._hooks import HookSystem, HookContext

hs = HookSystem()
order = []

# ── Register hooks with different priorities ──
hs.register(
    "tool:post_execute",
    lambda ctx: order.append("priority-0"),
    hook_type="post",
    priority=0,
)
hs.register(
    "tool:post_execute",
    lambda ctx: order.append("priority-50"),
    hook_type="post",
    priority=50,
)
hs.register(
    "tool:post_execute",
    lambda ctx: order.append("priority-10"),
    hook_type="post",
    priority=10,
)

# ── Register user vs skill hooks at same priority ──
hs.register(
    "tool:post_execute",
    lambda ctx: order.append("skill-hook"),
    hook_type="post",
    priority=5,
    source="skill:audit",
)
hs.register(
    "tool:post_execute",
    lambda ctx: order.append("user-hook"),
    hook_type="post",
    priority=5,
    source="user",
)


async def main():
    ctx = HookContext("tool:post_execute", agent=None, data={"name": "test"})
    await hs.emit_post("tool:post_execute", ctx)
    print(f"Hook execution order: {order}")
    print(
        "Expected: ['priority-50', 'priority-10', 'user-hook', 'skill-hook', 'priority-0']"
    )
    assert order == [
        "priority-50",
        "priority-10",
        "user-hook",
        "skill-hook",
        "priority-0",
    ]
    print("✓ PASS: Priority ordering correct")

    # ── FIFO tiebreaker ──
    hs2 = HookSystem()
    fifo_order = []
    hs2.register(
        "tool:post_execute",
        lambda ctx: fifo_order.append("first"),
        hook_type="post",
        source="user",
    )
    hs2.register(
        "tool:post_execute",
        lambda ctx: fifo_order.append("second"),
        hook_type="post",
        source="user",
    )
    ctx2 = HookContext("tool:post_execute", agent=None, data={})
    await hs2.emit_post("tool:post_execute", ctx2)
    print(f"FIFO order: {fifo_order}")
    assert fifo_order == ["first", "second"]
    print("✓ PASS: FIFO tiebreaker correct")

    # ── Async handler support ──
    async_results = []

    async def async_handler(ctx):
        async_results.append("async")

    hs.register("tool:post_execute", async_handler, hook_type="post")
    ctx3 = HookContext("tool:post_execute", agent=None, data={})
    await hs.emit_post("tool:post_execute", ctx3)
    print(f"Async handler result: {async_results}")
    assert "async" in async_results
    print("✓ PASS: Async handler supported")
    print("\nAll basic hook tests passed!")


asyncio.run(main())
