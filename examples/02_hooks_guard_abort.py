"""
Example 02: Guard hooks for blocking dangerous operations.

Demonstrates:
- Guard hook aborting tool execution
- Multiple guards all running even after abort
- Abort reason concatenation
- Handler exceptions becoming abort reasons
"""

import asyncio
from mid_harness._hooks import HookSystem, HookContext

hs = HookSystem()


# ── Security guard: block destructive operations ──
def security_guard(ctx):
    risk = ctx.data.get("risk_level", "")
    if risk == "destructive":
        ctx.abort(f"Destructive tool '{ctx.data['name']}' blocked")


hs.register("tool:pre_execute", security_guard, hook_type="guard", priority=100)

# ── Rate limiter guard ──
call_counts = {}


def rate_limit_guard(ctx):
    name = ctx.data["name"]
    call_counts[name] = call_counts.get(name, 0) + 1
    if call_counts[name] > 3:
        ctx.abort(f"Rate limit exceeded for '{name}' ({call_counts[name]} calls)")


hs.register("tool:pre_execute", rate_limit_guard, hook_type="guard", priority=50)


async def main():
    # ── Test 1: Normal operation passes ──
    ctx = HookContext(
        "tool:pre_execute",
        agent=None,
        data={
            "name": "read_file",
            "args": {"path": "/tmp/test.txt"},
            "call_id": "c1",
            "risk_level": "read_only",
        },
    )
    await hs.emit_guard("tool:pre_execute", ctx)
    print(f"Test 1 (read_only): aborted={ctx.is_aborted}")
    assert not ctx.is_aborted
    print("✓ PASS: Safe operations pass guards")

    # ── Test 2: Destructive operation blocked ──
    ctx2 = HookContext(
        "tool:pre_execute",
        agent=None,
        data={
            "name": "delete_file",
            "args": {"path": "/tmp/important.txt"},
            "call_id": "c2",
            "risk_level": "destructive",
        },
    )
    await hs.emit_guard("tool:pre_execute", ctx2)
    print(
        f"Test 2 (destructive): aborted={ctx2.is_aborted}, reason={ctx2.abort_reason}"
    )
    assert ctx2.is_aborted
    assert "Destructive" in ctx2.abort_reason
    print("✓ PASS: Destructive operations blocked by guard")

    # ── Test 3: Rate limit kicks in after 4 calls ──
    for i in range(4):
        ctx3 = HookContext(
            "tool:pre_execute",
            agent=None,
            data={
                "name": "api_call",
                "args": {},
                "call_id": f"r{i}",
                "risk_level": "read_only",
            },
        )
        await hs.emit_guard("tool:pre_execute", ctx3)
        if i < 3:
            assert not ctx3.is_aborted, f"Call {i + 1} should not be rate-limited"
        else:
            assert ctx3.is_aborted, f"Call {i + 1} should be rate-limited"
            assert "Rate limit" in ctx3.abort_reason
    print("✓ PASS: Rate limiting works correctly")

    # ── Test 4: Exception in guard becomes abort ──
    def fault_guard(ctx):
        raise ValueError("configuration error")

    hs.register("tool:pre_execute", fault_guard, hook_type="guard", priority=0)
    ctx4 = HookContext(
        "tool:pre_execute",
        agent=None,
        data={"name": "test", "args": {}, "call_id": "c4", "risk_level": "safe"},
    )
    await hs.emit_guard("tool:pre_execute", ctx4)
    print(f"Test 4 (exception): aborted={ctx4.is_aborted}, reason={ctx4.abort_reason}")
    assert ctx4.is_aborted
    assert "configuration error" in ctx4.abort_reason
    print("✓ PASS: Guard exceptions become aborts")
    print("\nAll guard hook tests passed!")


asyncio.run(main())
