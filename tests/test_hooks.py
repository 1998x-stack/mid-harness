import pytest
import logging
from mid_harness._hooks import HookSystem, HookContext


@pytest.fixture
def hooks():
    return HookSystem()


@pytest.fixture
def ctx():
    class FakeAgent:
        pass

    return HookContext(
        "tool:pre_execute",
        FakeAgent(),
        {
            "name": "test_tool",
            "args": {"path": "/tmp/x"},
            "call_id": "call_1",
            "risk_level": "read_only",
        },
    )


# ── Registration ──


def test_register_valid_event(hooks):
    name = hooks.register("tool:pre_execute", lambda ctx: None, hook_type="guard")
    assert name.startswith("hook_")


def test_register_invalid_event(hooks):
    with pytest.raises(ValueError, match="Unknown hook point"):
        hooks.register("nonexistent:hook", lambda ctx: None)


def test_register_invalid_type(hooks):
    with pytest.raises(ValueError, match="not allowed"):
        hooks.register("agent:init", lambda ctx: None, hook_type="guard")


def test_register_with_custom_name(hooks):
    name = hooks.register("tool:post_execute", lambda ctx: None, name="my-custom-hook")
    assert name == "my-custom-hook"


# ── Priority Ordering ──


@pytest.mark.asyncio
async def test_priority_ordering(hooks):
    order = []
    hooks.register(
        "tool:post_execute", lambda ctx: order.append(1), hook_type="post", priority=10
    )
    hooks.register(
        "tool:post_execute", lambda ctx: order.append(2), hook_type="post", priority=50
    )
    hooks.register(
        "tool:post_execute", lambda ctx: order.append(3), hook_type="post", priority=0
    )
    ctx = HookContext("tool:post_execute", None, {})
    await hooks.emit_post("tool:post_execute", ctx)
    assert order == [2, 1, 3]


@pytest.mark.asyncio
async def test_user_before_skill_same_priority(hooks):
    order = []
    hooks.register(
        "tool:post_execute",
        lambda ctx: order.append("skill"),
        hook_type="post",
        priority=10,
        source="skill:git",
    )
    hooks.register(
        "tool:post_execute",
        lambda ctx: order.append("user"),
        hook_type="post",
        priority=10,
        source="user",
    )
    ctx = HookContext("tool:post_execute", None, {})
    await hooks.emit_post("tool:post_execute", ctx)
    assert order == ["user", "skill"]


@pytest.mark.asyncio
async def test_fifo_tiebreaker_same_source_and_priority(hooks):
    order = []
    hooks.register(
        "tool:post_execute",
        lambda ctx: order.append("first"),
        hook_type="post",
        priority=0,
        source="user",
    )
    hooks.register(
        "tool:post_execute",
        lambda ctx: order.append("second"),
        hook_type="post",
        priority=0,
        source="user",
    )
    ctx = HookContext("tool:post_execute", None, {})
    await hooks.emit_post("tool:post_execute", ctx)
    assert order == ["first", "second"]


# ── Guard Hooks ──


@pytest.mark.asyncio
async def test_guard_abort_sets_context(hooks):
    hooks.register(
        "tool:pre_execute", lambda ctx: ctx.abort("blocked"), hook_type="guard"
    )
    ctx = HookContext("tool:pre_execute", None, {"name": "test"})
    await hooks.emit_guard("tool:pre_execute", ctx)
    assert ctx.is_aborted is True
    assert ctx.abort_reason == "blocked"


@pytest.mark.asyncio
async def test_guard_abort_remaining_guards_run(hooks):
    calls = []
    hooks.register(
        "tool:pre_execute",
        lambda ctx: (calls.append("first"), ctx.abort("by first")),
        hook_type="guard",
        priority=100,
    )
    hooks.register(
        "tool:pre_execute",
        lambda ctx: calls.append("second"),
        hook_type="guard",
        priority=50,
    )
    hooks.register(
        "tool:pre_execute",
        lambda ctx: (calls.append("third"), ctx.abort("by third")),
        hook_type="guard",
        priority=0,
    )
    ctx = HookContext("tool:pre_execute", None, {"name": "test"})
    await hooks.emit_guard("tool:pre_execute", ctx)
    assert calls == ["first", "second", "third"]
    assert "by first" in ctx.abort_reason
    assert "by third" in ctx.abort_reason


# ── Pre-Hooks ──


@pytest.mark.asyncio
async def test_pre_modify_persists(hooks):
    hooks.register(
        "tool:pre_execute",
        lambda ctx: ctx.modify("args", {"path": "/modified"}),
        hook_type="pre",
    )
    ctx = HookContext(
        "tool:pre_execute", None, {"name": "test", "args": {"path": "/original"}}
    )
    await hooks.emit_pre("tool:pre_execute", ctx)
    result = ctx.finalize()
    assert result["args"] == {"path": "/modified"}


@pytest.mark.asyncio
async def test_pre_abort_blocks_op(hooks):
    hooks.register(
        "tool:pre_execute", lambda ctx: ctx.abort("bad args"), hook_type="pre"
    )
    ctx = HookContext("tool:pre_execute", None, {"name": "test"})
    await hooks.emit_pre("tool:pre_execute", ctx)
    assert ctx.is_aborted is True


@pytest.mark.asyncio
async def test_pre_multiple_aborts_concatenate(hooks):
    hooks.register(
        "tool:pre_execute", lambda ctx: ctx.abort("reason1"), hook_type="pre"
    )
    hooks.register(
        "tool:pre_execute", lambda ctx: ctx.abort("reason2"), hook_type="pre"
    )
    ctx = HookContext("tool:pre_execute", None, {"name": "test"})
    await hooks.emit_pre("tool:pre_execute", ctx)
    assert "reason1" in ctx.abort_reason
    assert "reason2" in ctx.abort_reason


# ── Direct Mutation Detection ──


def test_direct_mutation_warned(hooks, caplog):
    ctx = HookContext("tool:pre_execute", None, {"name": "test", "extra": "original"})
    ctx.data["extra"] = "mutated"
    with caplog.at_level(logging.WARNING):
        ctx.finalize()
    assert "without ctx.modify()" in caplog.text
    assert ctx._original_data["extra"] == "original"


def test_modify_persists_without_warning(hooks, caplog):
    ctx = HookContext("tool:pre_execute", None, {"name": "test", "extra": "original"})
    ctx.modify("extra", "changed")
    with caplog.at_level(logging.WARNING):
        result = ctx.finalize()
    assert "without ctx.modify()" not in caplog.text
    assert result["extra"] == "changed"


# ── Post-Hooks ──


@pytest.mark.asyncio
async def test_post_observe_result(hooks):
    observed = []
    hooks.register(
        "tool:post_execute",
        lambda ctx: observed.append(ctx.data.get("result")),
        hook_type="post",
    )
    ctx = HookContext("tool:post_execute", None, {"name": "test", "result": "success"})
    await hooks.emit_post("tool:post_execute", ctx)
    assert observed == ["success"]


# ── Unregistration ──


@pytest.mark.asyncio
async def test_unregister_removes_hook(hooks):
    calls = []
    name = hooks.register(
        "tool:post_execute", lambda ctx: calls.append(1), hook_type="post"
    )
    hooks.unregister(name)
    ctx = HookContext("tool:post_execute", None, {})
    await hooks.emit_post("tool:post_execute", ctx)
    assert calls == []


def test_unregister_nonexistent_raises(hooks):
    with pytest.raises(ValueError, match="No hook registered"):
        hooks.unregister("nonexistent")


# ── Duplicate Names ──


def test_duplicate_name_rejected(hooks):
    hooks.register("tool:post_execute", lambda ctx: None, name="my-hook")
    with pytest.raises(ValueError, match="already registered"):
        hooks.register("tool:post_execute", lambda ctx: None, name="my-hook")


# ── Handler Exception Resilience ──


@pytest.mark.asyncio
async def test_handler_exception_chain_continues(hooks):
    calls = []

    def handler1(ctx):
        raise RuntimeError("boom")

    def handler2(ctx):
        calls.append("ran")

    hooks.register("tool:post_execute", handler1, hook_type="post")
    hooks.register("tool:post_execute", handler2, hook_type="post")
    ctx = HookContext("tool:post_execute", None, {})
    await hooks.emit_post("tool:post_execute", ctx)
    assert calls == ["ran"]


@pytest.mark.asyncio
async def test_guard_handler_exception_becomes_abort(hooks):
    def guard_handler(ctx):
        raise RuntimeError("guard failed")

    hooks.register("tool:pre_execute", guard_handler, hook_type="guard")
    ctx = HookContext("tool:pre_execute", None, {"name": "test"})
    await hooks.emit_guard("tool:pre_execute", ctx)
    assert ctx.is_aborted is True
    assert "guard failed" in ctx.abort_reason


# ── Zero Overhead ──


@pytest.mark.asyncio
async def test_empty_hook_chain_noop(hooks):
    ctx = HookContext("tool:pre_execute", None, {"name": "test"})
    await hooks.emit_guard("tool:pre_execute", ctx)
    await hooks.emit_pre("tool:pre_execute", ctx)
    await hooks.emit_post("tool:pre_execute", ctx)
    assert ctx.is_aborted is False


# ── Async Handlers ──


@pytest.mark.asyncio
async def test_async_handler_supported(hooks):
    calls = []

    async def async_handler(ctx):
        calls.append("async_ran")

    hooks.register("tool:post_execute", async_handler, hook_type="post")
    ctx = HookContext("tool:post_execute", None, {})
    await hooks.emit_post("tool:post_execute", ctx)
    assert calls == ["async_ran"]


# ── Wrap hook (deferred, but structure exists) ──


def test_get_wrap_returns_none_when_empty(hooks):
    assert hooks.get_wrap("tool:pre_execute") is None


# ── Signature validation ──


def test_zero_param_handler_rejected(hooks):
    with pytest.raises(ValueError, match="at least one positional argument"):
        hooks.register("tool:post_execute", lambda: None)


def test_wrong_param_name_warns(hooks, caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        hooks.register("tool:post_execute", lambda args: None)
    assert "parameter is named 'args'" in caplog.text


def test_correct_param_name_no_warning(hooks, caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        hooks.register("tool:post_execute", lambda ctx: None)
    assert "parameter is named" not in caplog.text


def test_builtin_handler_skips_check(hooks):
    hooks.register("tool:post_execute", print)  # built-in, should not raise
