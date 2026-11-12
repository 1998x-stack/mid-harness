"""
Example 04: Post-hooks for observing and logging tool results.

Demonstrates:
- Post-hook observing successful tool results
- Post-hook observing failed tool results via tool:error
- Building an audit trail with post-hooks
- Handler exceptions not breaking the chain
"""

import asyncio
from mid_harness._hooks import HookSystem, HookContext

hs = HookSystem()

# ── Audit trail collector ──
audit_log = []


def audit_success(ctx):
    audit_log.append(
        {
            "tool": ctx.data["name"],
            "result": ctx.data.get("result", "")[:50],
            "status": "success",
        }
    )


def audit_error(ctx):
    audit_log.append(
        {
            "tool": ctx.data["name"],
            "error": ctx.data.get("error", "")[:50],
            "status": "error",
        }
    )


hs.register("tool:post_execute", audit_success, hook_type="post")
hs.register("tool:error", audit_error, hook_type="post")

# ── Token usage tracker ──
token_usage = {"total": 0}


def track_tokens(ctx):
    usage = ctx.data.get("usage", {})
    if usage:
        token_usage["total"] += usage.get("input_tokens", 0)


hs.register("llm:post_call", track_tokens, hook_type="post")


async def main():
    # ── Test 1: Post-hook observes successful tool result ──
    ctx1 = HookContext(
        "tool:post_execute",
        agent=None,
        data={
            "name": "read_file",
            "args": {"path": "/tmp/test.txt"},
            "result": "file contents here...",
            "call_id": "c1",
        },
    )
    await hs.emit_post("tool:post_execute", ctx1)
    assert len(audit_log) == 1
    assert audit_log[0]["status"] == "success"
    assert "file contents" in audit_log[0]["result"]
    print(f"Test 1 - Audit log: {audit_log[0]}")
    print("✓ PASS: Post-hook observed success")

    # ── Test 2: Error hook observes failed tool result ──
    ctx2 = HookContext(
        "tool:error",
        agent=None,
        data={
            "name": "delete_file",
            "args": {"path": "/protected/file"},
            "error": "Permission denied",
            "call_id": "c2",
        },
    )
    await hs.emit_post("tool:error", ctx2)
    assert len(audit_log) == 2
    assert audit_log[1]["status"] == "error"
    assert "Permission denied" in audit_log[1]["error"]
    print(f"Test 2 - Audit log: {audit_log[1]}")
    print("✓ PASS: Error hook observed failure")

    # ── Test 3: LLM post-call tracking ──
    ctx3 = HookContext(
        "llm:post_call",
        agent=None,
        data={"response": "streamed", "messages": [], "usage": {"input_tokens": 1500}},
    )
    await hs.emit_post("llm:post_call", ctx3)
    assert token_usage["total"] == 1500
    print(f"Test 3 - Token usage: {token_usage['total']}")
    print("✓ PASS: LLM post-call hook tracked tokens")

    # ── Test 4: Handler exception doesn't break chain ──
    def bad_observer(ctx):
        raise RuntimeError("oops")

    hs.register("tool:post_execute", bad_observer, hook_type="post")
    second_called = []
    hs.register(
        "tool:post_execute", lambda ctx: second_called.append(True), hook_type="post"
    )

    ctx4 = HookContext(
        "tool:post_execute",
        agent=None,
        data={"name": "echo", "args": {}, "result": "ok", "call_id": "c4"},
    )
    await hs.emit_post("tool:post_execute", ctx4)
    assert second_called == [True]
    print("✓ PASS: Handler exception does not break chain")
    print(f"\nFull audit log ({len(audit_log)} entries):")
    for entry in audit_log:
        print(
            f"  [{entry['status']}] {entry['tool']}: {entry.get('result', entry.get('error', ''))}"
        )
    print("\nAll post-hook tests passed!")


asyncio.run(main())
