"""
Example 03: Pre-hooks for modifying operation input.

Demonstrates:
- Pre-hook modifying tool arguments before execution
- ctx.modify() persisting changes to the operation
- Direct mutation detection and warning
- Multiple pre-hooks chaining modifications
"""

import asyncio
import logging
from mid_harness._hooks import HookSystem, HookContext

logging.basicConfig(level=logging.WARNING)
hs = HookSystem()


# ── Pre-hook: sanitize file paths ──
def sanitize_paths(ctx):
    args = dict(ctx.data.get("args", {}))
    path = args.get("path", "")
    if ".." in path:
        args["path"] = path.replace("..", "")
        ctx.modify("args", args)


hs.register("tool:pre_execute", sanitize_paths, hook_type="pre", priority=50)


# ── Pre-hook: inject default timeout ──
def inject_defaults(ctx):
    args = dict(ctx.data.get("args", {}))
    if "timeout" not in args:
        args["timeout"] = 30
        ctx.modify("args", args)


hs.register("tool:pre_execute", inject_defaults, hook_type="pre", priority=10)


async def main():
    # ── Test 1: Path sanitization ──
    ctx = HookContext(
        "tool:pre_execute",
        agent=None,
        data={
            "name": "read_file",
            "args": {"path": "../../../etc/passwd"},
            "call_id": "c1",
        },
    )
    await hs.emit_pre("tool:pre_execute", ctx)
    result = ctx.finalize()
    print("Test 1 - Original path: ../../../etc/passwd")
    print(f"Test 1 - Sanitized:    {result['args']['path']}")
    assert ".." not in result["args"]["path"]
    print("✓ PASS: Path traversal sanitized")

    # ── Test 2: Default timeout injected ──
    ctx2 = HookContext(
        "tool:pre_execute",
        agent=None,
        data={"name": "run_command", "args": {"command": "ls"}, "call_id": "c2"},
    )
    await hs.emit_pre("tool:pre_execute", ctx2)
    result2 = ctx2.finalize()
    print(f"Test 2 - Args after pre-hooks: {result2['args']}")
    assert result2["args"]["timeout"] == 30
    print("✓ PASS: Default timeout injected")

    # ── Test 3: Direct mutation warning ──
    def bad_modifier(ctx):
        ctx.data["extra"] = "mutated directly"  # no ctx.modify()!

    hs.register("tool:pre_execute", bad_modifier, hook_type="pre")
    ctx3 = HookContext(
        "tool:pre_execute",
        agent=None,
        data={"name": "test", "args": {}, "call_id": "c3", "extra": "original"},
    )
    await hs.emit_pre("tool:pre_execute", ctx3)
    result3 = ctx3.finalize()
    print(f"Test 3 - After mutation: extra={result3['extra']} (should be 'original')")
    assert result3["extra"] == "original"
    print("✓ PASS: Direct mutation rejected, ctx.modify() required")

    # ── Test 4: Pre-hook abort blocks operation ──
    def validator(ctx):
        if not ctx.data.get("args", {}).get("message"):
            ctx.abort("message is required")

    hs.register("tool:pre_execute", validator, hook_type="pre", priority=100)
    ctx4 = HookContext(
        "tool:pre_execute",
        agent=None,
        data={"name": "send_message", "args": {}, "call_id": "c4"},
    )
    await hs.emit_pre("tool:pre_execute", ctx4)
    print(f"Test 4 - Aborted: {ctx4.is_aborted}, reason: {ctx4.abort_reason}")
    assert ctx4.is_aborted
    assert "message" in ctx4.abort_reason
    print("✓ PASS: Pre-hook abort works")
    print("\nAll pre-hook tests passed!")


asyncio.run(main())
