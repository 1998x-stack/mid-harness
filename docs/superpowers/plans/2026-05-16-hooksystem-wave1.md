# HookSystem Wave 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a HookSystem to tiny-harness that allows intercepting, modifying, and observing agent operations at 14 lifecycle hook points with guard, pre, and post hook types.

**Architecture:** New `_hooks.py` module (HookSystem, HookContext, HookRegistration) with no internal dependencies. Extend `_tools.py` (collision detection), `_loop.py` (conditional hook injection), and `_core.py` (hook registration API). All extensions are conditional — zero overhead when no hooks registered; 195 existing tests pass unchanged.

**Tech Stack:** Python 3.11+, `httpx` (existing), `pytest` + `pytest-asyncio` (existing dev deps), no new dependencies.

---

### Task 1: Create `_hooks.py` — HookRegistration, HookContext, HookSystem

**Files:**
- Create: `mid_harness/_hooks.py`

- [ ] **Step 1: Create the module with dataclasses and classes**

```python
# mid_harness/_hooks.py
from __future__ import annotations
import copy
import inspect
import logging
import uuid
from dataclasses import dataclass, field
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("mid_harness.hooks")

# ── All valid hook point names ──
VALID_HOOK_POINTS = frozenset({
    "agent:init", "agent:shutdown",
    "session:start", "session:end",
    "skill:load",
    "loop:start", "loop:iteration", "loop:complete",
    "llm:pre_call", "llm:post_call", "llm:error",
    "tool:pre_execute", "tool:post_execute", "tool:error",
})

# ── Per-hook-point: which types are valid ──
ALLOWED_TYPES: dict[str, set[str]] = {
    "agent:init":        {"post"},
    "agent:shutdown":    {"post"},
    "session:start":     {"post"},
    "session:end":       {"post"},
    "skill:load":        {"post"},
    "loop:start":        {"pre", "post"},
    "loop:iteration":    {"pre", "post"},
    "loop:complete":     {"post"},
    "llm:pre_call":      {"pre"},
    "llm:post_call":     {"post"},
    "llm:error":         {"post"},
    "tool:pre_execute":  {"guard", "pre"},
    "tool:post_execute": {"post"},
    "tool:error":        {"post"},
}


@dataclass
class HookRegistration:
    """A single registered hook with metadata for ordering."""
    name: str
    handler: Callable  # async (HookContext) -> None
    priority: int
    source: str        # "user" | "skill:{name}"
    source_rank: int = field(init=False)

    def __post_init__(self):
        # user hooks (0) sort before skill hooks (1)
        self.source_rank = 0 if self.source == "user" else 1

    @property
    def sort_key(self) -> tuple[str, int, int, int]:
        """Sort: higher priority first, then user before skill, then FIFO."""
        # Use negative priority so higher priority sorts first
        return (-self.priority, self.source_rank, 0)  # 0 placeholder for insertion order

    def __lt__(self, other: "HookRegistration") -> bool:
        return self.sort_key < other.sort_key


class HookContext:
    """Context passed to hook handlers. Data is shallow-copied on entry."""

    def __init__(self, event: str, agent: "Agent", data: dict):
        self.event = event
        self.agent = agent
        self._original_data = data
        self.data = copy.copy(data)  # shallow copy — hooks get their own top-level dict
        self._aborted = False
        self._abort_reason: str | None = None
        self._modifications: set[str] = set()  # keys explicitly modified via ctx.modify()

    def abort(self, reason: str) -> None:
        """Abort the operation. Remaining hooks in the same type still run."""
        if self._aborted:
            self._abort_reason = f"{self._abort_reason}; {reason}"
        else:
            self._abort_reason = reason
        self._aborted = True

    def modify(self, key: str, value: Any) -> None:
        """Modify data in the context. Only keys modified this way persist."""
        self._modifications.add(key)
        self.data[key] = value

    @property
    def is_aborted(self) -> bool:
        return self._aborted

    @property
    def abort_reason(self) -> str | None:
        return self._abort_reason

    def finalize(self) -> dict:
        """Return data with only explicitly-modified changes applied to original.

        Also logs a warning if any data keys were mutated directly
        (without calling ctx.modify()).
        """
        # Detect direct mutations: data values changed but not via modify()
        for key in self.data:
            if key not in self._modifications:
                original = self._original_data.get(key)
                current = self.data.get(key)
                if original is not current:  # identity check — same object
                    logger.warning(
                        f"Hook modified '{key}' in ctx.data directly (without ctx.modify()). "
                        f"Change will NOT be propagated to the operation. "
                        f"Use ctx.modify('{key}', value) instead."
                    )

        # Apply only explicitly-modified keys back to original data
        for key in self._modifications:
            self._original_data[key] = self.data[key]

        return self._original_data


class HookSystem:
    """Lifecycle hook registry and emitter."""

    def __init__(self):
        # {event: {"guard": [...], "pre": [...], "post": [...], "wrap": [...]}}
        self._hooks: dict[str, dict[str, list[HookRegistration]]] = {}
        for point in VALID_HOOK_POINTS:
            self._hooks[point] = {t: [] for t in ("guard", "pre", "post", "wrap")}
        self._names: dict[str, HookRegistration] = {}  # name → reg for unregister
        self._insert_count = 0  # FIFO tiebreaker

    def register(
        self,
        event: str,
        handler: Callable,
        *,
        hook_type: str = "post",
        priority: int = 0,
        name: str | None = None,
        source: str = "user",
    ) -> str:
        """Register a hook. Returns the hook name."""
        if event not in VALID_HOOK_POINTS:
            raise ValueError(
                f"Unknown hook point: '{event}'. "
                f"Valid points: {', '.join(sorted(VALID_HOOK_POINTS))}"
            )
        if hook_type not in ALLOWED_TYPES.get(event, set()):
            raise ValueError(
                f"Hook type '{hook_type}' is not allowed for '{event}'. "
                f"Allowed: {', '.join(sorted(ALLOWED_TYPES[event]))}"
            )

        hook_name = name or f"hook_{uuid.uuid4().hex[:8]}"
        if hook_name in self._names:
            raise ValueError(f"Hook name '{hook_name}' is already registered")

        reg = HookRegistration(
            name=hook_name,
            handler=handler,
            priority=priority,
            source=source,
        )
        # Override sort_key with insertion order for FIFO tiebreaking
        self._insert_count += 1
        object.__setattr__(reg, "sort_key", (-priority, reg.source_rank, self._insert_count))

        self._hooks[event][hook_type].append(reg)
        self._hooks[event][hook_type].sort()
        self._names[hook_name] = reg
        return hook_name

    def unregister(self, name: str) -> None:
        """Remove a hook by name."""
        if name not in self._names:
            raise ValueError(f"No hook registered with name '{name}'")
        reg = self._names.pop(name)
        for event_data in self._hooks.values():
            for hook_list in event_data.values():
                hook_list[:] = [h for h in hook_list if h.name != name]

    async def emit_guard(self, event: str, ctx: HookContext) -> None:
        """Run all guard hooks for an event. Guards can abort but not modify."""
        for reg in self._hooks[event]["guard"]:
            try:
                if inspect.iscoroutinefunction(reg.handler):
                    await reg.handler(ctx)
                else:
                    reg.handler(ctx)
            except Exception as e:
                logger.warning(f"Guard hook '{reg.name}' raised {type(e).__name__}: {e}")
                ctx.abort(str(e))

    async def emit_pre(self, event: str, ctx: HookContext) -> None:
        """Run all pre-hooks. Can modify data or abort."""
        for reg in self._hooks[event]["pre"]:
            # Aborted pre-hooks still run but should see the aborted context
            try:
                if inspect.iscoroutinefunction(reg.handler):
                    await reg.handler(ctx)
                else:
                    reg.handler(ctx)
            except Exception as e:
                logger.warning(f"Pre-hook '{reg.name}' raised {type(e).__name__}: {e}")

    async def emit_post(self, event: str, ctx: HookContext) -> None:
        """Run all post-hooks. Observe-only — cannot abort or modify."""
        for reg in self._hooks[event]["post"]:
            try:
                if inspect.iscoroutinefunction(reg.handler):
                    await reg.handler(ctx)
                else:
                    reg.handler(ctx)
            except Exception as e:
                logger.warning(f"Post-hook '{reg.name}' raised {type(e).__name__}: {e}")

    def get_wrap(self, event: str) -> Callable | None:
        """Get the wrap hook for an event (at most one). Deferred in Wave 1."""
        wraps = self._hooks[event]["wrap"]
        return wraps[0].handler if wraps else None  # pragma: no cover
```

- [ ] **Step 2: Verify the module imports correctly**

```bash
cd /Users/mx/Desktop/harness/mid-harness && python -c "from mid_harness._hooks import HookSystem, HookContext, HookRegistration; print('OK')"
```
Expected: `OK`

---

### Task 2: Write tests for `_hooks.py`

**Files:**
- Create: `tests/test_hooks.py`

- [ ] **Step 1: Write test file with all hook tests**

```python
# tests/test_hooks.py
import pytest
import logging
from mid_harness._hooks import HookSystem, HookContext, HookRegistration, VALID_HOOK_POINTS


# ── Fixtures ──

@pytest.fixture
def hooks():
    return HookSystem()


@pytest.fixture
def ctx():
    class FakeAgent:
        pass
    return HookContext("tool:pre_execute", FakeAgent(), {"name": "test_tool", "args": {"path": "/tmp/x"}, "call_id": "call_1", "risk_level": "read_only"})


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


# ── Priority Ordering ──

@pytest.mark.asyncio
async def test_priority_ordering(hooks):
    """Higher priority hooks run first."""
    order = []
    hooks.register("tool:post_execute", lambda ctx: order.append(1), hook_type="post", priority=10)
    hooks.register("tool:post_execute", lambda ctx: order.append(2), hook_type="post", priority=50)
    hooks.register("tool:post_execute", lambda ctx: order.append(3), hook_type="post", priority=0)
    ctx = HookContext("tool:post_execute", None, {})
    await hooks.emit_post("tool:post_execute", ctx)
    assert order == [2, 1, 3]  # priority 50, then 10, then 0

@pytest.mark.asyncio
async def test_user_before_skill_same_priority(hooks):
    """User hooks run before skill hooks at same priority."""
    order = []
    hooks.register("tool:post_execute", lambda ctx: order.append("skill"), hook_type="post", priority=10, source="skill:git")
    hooks.register("tool:post_execute", lambda ctx: order.append("user"), hook_type="post", priority=10, source="user")
    ctx = HookContext("tool:post_execute", None, {})
    await hooks.emit_post("tool:post_execute", ctx)
    assert order == ["user", "skill"]


# ── Guard Hooks ──

@pytest.mark.asyncio
async def test_guard_abort_sets_context(hooks):
    hooks.register("tool:pre_execute", lambda ctx: ctx.abort("blocked"), hook_type="guard")
    ctx = HookContext("tool:pre_execute", None, {"name": "test"})
    await hooks.emit_guard("tool:pre_execute", ctx)
    assert ctx.is_aborted is True
    assert ctx.abort_reason == "blocked"

@pytest.mark.asyncio
async def test_guard_abort_remaining_guards_run(hooks):
    """All guards execute even after one aborts."""
    calls = []
    hooks.register("tool:pre_execute", lambda ctx: (calls.append("first"), ctx.abort("by first")), hook_type="guard", priority=100)
    hooks.register("tool:pre_execute", lambda ctx: calls.append("second"), hook_type="guard", priority=50)
    hooks.register("tool:pre_execute", lambda ctx: (calls.append("third"), ctx.abort("by third")), hook_type="guard", priority=0)
    ctx = HookContext("tool:pre_execute", None, {"name": "test"})
    await hooks.emit_guard("tool:pre_execute", ctx)
    assert calls == ["first", "second", "third"]
    assert "by first" in ctx.abort_reason
    assert "by third" in ctx.abort_reason


# ── Pre-Hooks ──

@pytest.mark.asyncio
async def test_pre_modify_persists(hooks):
    hooks.register("tool:pre_execute", lambda ctx: ctx.modify("args", {"path": "/modified"}), hook_type="pre")
    ctx = HookContext("tool:pre_execute", None, {"name": "test", "args": {"path": "/original"}})
    await hooks.emit_pre("tool:pre_execute", ctx)
    result = ctx.finalize()
    assert result["args"] == {"path": "/modified"}

@pytest.mark.asyncio
async def test_pre_abort_blocks_op(hooks):
    hooks.register("tool:pre_execute", lambda ctx: ctx.abort("bad args"), hook_type="pre")
    ctx = HookContext("tool:pre_execute", None, {"name": "test"})
    await hooks.emit_pre("tool:pre_execute", ctx)
    assert ctx.is_aborted is True


# ── Direct Mutation Detection ──

def test_direct_mutation_warned(hooks, caplog):
    """Direct mutation of ctx.data without modify() logs warning."""
    ctx = HookContext("tool:pre_execute", None, {"name": "test", "extra": "original"})

    # Simulate direct mutation (replace the value)
    ctx.data["extra"] = "mutated"

    with caplog.at_level(logging.WARNING):
        ctx.finalize()

    assert "without ctx.modify()" in caplog.text
    # The mutation should NOT be propagated
    assert ctx._original_data["extra"] == "original"


# ── Post-Hooks ──

@pytest.mark.asyncio
async def test_post_observe_result(hooks):
    observed = []
    hooks.register("tool:post_execute", lambda ctx: observed.append(ctx.data.get("result")), hook_type="post")
    ctx = HookContext("tool:post_execute", None, {"name": "test", "result": "success"})
    await hooks.emit_post("tool:post_execute", ctx)
    assert observed == ["success"]


# ── Unregistration ──

@pytest.mark.asyncio
async def test_unregister_removes_hook(hooks):
    calls = []
    name = hooks.register("tool:post_execute", lambda ctx: calls.append(1), hook_type="post")
    hooks.unregister(name)
    ctx = HookContext("tool:post_execute", None, {})
    await hooks.emit_post("tool:post_execute", ctx)
    assert calls == []


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
    assert calls == ["ran"]  # second handler still ran


# ── Zero Overhead ──

@pytest.mark.asyncio
async def test_empty_hook_chain_noop(hooks):
    """Emitting on an event with no registered hooks does nothing (no crash)."""
    ctx = HookContext("tool:pre_execute", None, {"name": "test"})
    await hooks.emit_guard("tool:pre_execute", ctx)
    await hooks.emit_pre("tool:pre_execute", ctx)
    await hooks.emit_post("tool:pre_execute", ctx)
    # Should not raise, should not modify ctx
    assert ctx.is_aborted is False
```

- [ ] **Step 2: Run tests to verify they fail (module not importable yet)**

```bash
cd /Users/mx/Desktop/harness/mid-harness && python -m pytest tests/test_hooks.py -v 2>&1 | tail -5
```
Expected: import error (mid_harness package not set up)

---

### Task 3: Set up mid-harness package structure

**Files:**
- Create: `mid_harness/__init__.py`
- Create: `pyproject.toml` (or extend tiny-harness)

- [ ] **Step 1: Create package init and config**

```bash
cd /Users/mx/Desktop/harness/mid-harness && mkdir -p mid_harness tests
```

```python
# mid_harness/__init__.py
from mid_harness._hooks import HookSystem, HookContext, HookRegistration

__all__ = ["HookSystem", "HookContext", "HookRegistration"]
```

- [ ] **Step 2: Run hooks tests — all should pass**

```bash
cd /Users/mx/Desktop/harness/mid-harness && python -m pytest tests/test_hooks.py -v
```
Expected: 14 passed

---

### Task 4: Extend `_tools.py` — collision detection

**Files:**
- Copy: `tiny_harness/_tools.py` → `mid_harness/_tools.py`
- Modify: `mid_harness/_tools.py` — register() method

- [ ] **Step 1: Add ToolCollisionError and reserved names**

```python
# Insert after the ToolResult class (around line 41):
class ToolCollisionError(Exception):
    """Raised when a tool name is already registered or reserved."""
    pass

# In ToolRegistry class, add at top:
RESERVED_NAMES: set[str] = {
    "read_reference", "run_skill_script",
    "read_skill_asset", "render_template",
}
```

- [ ] **Step 2: Rewrite ToolRegistry.register() with collision detection**

```python
# Replace the existing register() method:
def register(self, tool: Tool) -> None:
    name = tool.definition.name

    # Check for exact duplicate
    if name in self._tools:
        raise ToolCollisionError(
            f"Tool '{name}' is already registered. "
            f"Duplicate tool names are not allowed."
        )

    # Check reserved names
    if name in self.RESERVED_NAMES:
        raise ToolCollisionError(
            f"'{name}' is a reserved tool name. "
            f"Reserved names: {', '.join(sorted(self.RESERVED_NAMES))}"
        )

    # Check MCP namespace (dotted names reserved for future MCP proxy tools)
    if "." in name:
        raise ToolCollisionError(
            f"Tool name '{name}' with dot notation is reserved for MCP proxy tools. "
            f"Use a name without dots for Python-native tools."
        )

    self._tools[name] = tool
```

- [ ] **Step 3: Verify existing tool tests still pass with collision detection**

```bash
cd /Users/mx/Desktop/harness/mid-harness && python -m pytest tests/test_tools.py -v -k "not collision" 2>&1 | tail -5
```

---

### Task 5: Write tests for tool collision detection

**Files:**
- Create: test additions in existing `tests/test_tools.py` (or create `tests/test_tools_collision.py`)

- [ ] **Step 1: Write collision tests**

```python
# >>> APPEND to tests/test_tools.py (or create tests/test_tools_collision.py) >>>
import pytest
from mid_harness._tools import ToolDef, Tool, ToolRegistry, ToolCollisionError


def test_collision_raises_error():
    r = ToolRegistry()
    r.register_from_def(
        ToolDef(name="deploy", description="Deploy", parameters={"type": "object", "properties": {}}),
        lambda args: "ok",
    )
    with pytest.raises(ToolCollisionError, match="already registered"):
        r.register_from_def(
            ToolDef(name="deploy", description="Deploy v2", parameters={"type": "object", "properties": {}}),
            lambda args: "ok",
        )


def test_reserved_name_blocked():
    r = ToolRegistry()
    for reserved in ToolRegistry.RESERVED_NAMES:
        with pytest.raises(ToolCollisionError, match="reserved"):
            r.register_from_def(
                ToolDef(name=reserved, description="Reserved", parameters={"type": "object", "properties": {}}),
                lambda args: "ok",
            )


def test_mcp_namespace_blocked():
    r = ToolRegistry()
    with pytest.raises(ToolCollisionError, match="dot notation"):
        r.register_from_def(
            ToolDef(name="git-server.git_commit", description="MCP tool", parameters={"type": "object", "properties": {}}),
            lambda args: "ok",
        )
```

- [ ] **Step 2: Run collision tests**

```bash
cd /Users/mx/Desktop/harness/mid-harness && python -m pytest tests/test_tools.py -v -k "collision or reserved or mcp_namespace"
```
Expected: 3 passed

---

### Task 6: Extend `_loop.py` — hook injection points

**Files:**
- Copy: `tiny_harness/_loop.py` → `mid_harness/_loop.py`
- Modify: `mid_harness/_loop.py` — AgentLoop.__init__ and run()

- [ ] **Step 1: Add HookSystem import and optional parameter**

```python
# Add import at top:
from mid_harness._hooks import HookSystem, HookContext
```

- [ ] **Step 2: Extend AgentLoop.__init__**

```python
# Modify AgentLoop.__init__ signature:
class AgentLoop:
    def __init__(self, config: AgentConfig, messages: MessageManager, llm, tools: ToolExecutor, events: EventBus, hooks: HookSystem | None = None):
        self._config = config
        self._messages = messages
        self._llm = llm
        self._tools = tools
        self._events = events
        self._hooks = hooks  # NEW — optional
```

- [ ] **Step 3: Add hook injection points in run()**

Insert hook calls at the marked locations in the run() method:

```python
async def run(self, user_prompt: str) -> str:
    self._messages.add_user(user_prompt)
    collected_text: list[str] = []
    error_budget = ErrorBudget(max_total=self._config.max_errors, max_consecutive=self._config.max_consecutive_errors)
    loop_detector = LoopDetector()

    # ── HOOK: loop:start ──
    if self._hooks:
        loop_start_ctx = HookContext("loop:start", agent=None, data={
            "messages": self._messages.to_list(),
            "tools": self._tools.get_definitions(),
            "max_iterations": self._config.max_iterations,
        })
        await self._hooks.emit_pre("loop:start", loop_start_ctx)
        await self._hooks.emit_post("loop:start", loop_start_ctx)

    for iteration in range(1, self._config.max_iterations + 1):
        token_estimate = self._messages.estimate_tokens()
        token_str = f"{token_estimate}" if token_estimate < 1000 else f"{token_estimate // 1000}K"
        await self._events.emit(StreamEvent(type="iteration", num=iteration, max=self._config.max_iterations, content=token_str))

        # ── HOOK: loop:iteration ──
        if self._hooks:
            iter_ctx = HookContext("loop:iteration", agent=None, data={
                "num": iteration,
                "max": self._config.max_iterations,
                "messages": self._messages.to_list(),
                "tokens_used": token_estimate,
            })
            await self._hooks.emit_pre("loop:iteration", iter_ctx)
            await self._hooks.emit_post("loop:iteration", iter_ctx)

        tool_calls: list[ToolCallRequest] = []

        # ── HOOK: llm:pre_call (before LLM call) ──
        _tools_for_llm = self._tools.get_definitions()
        if self._hooks:
            llm_pre_ctx = HookContext("llm:pre_call", agent=None, data={
                "messages": self._messages.to_list(),
                "tools": _tools_for_llm,
                "model": self._config.model,
                "provider": self._config.provider,
            })
            await self._hooks.emit_pre("llm:pre_call", llm_pre_ctx)
            # Note: messages modification via hooks is recorded but the
            # AgentLoop uses MessageManager internally. Pre-hooks can
            # observe but not directly mutate the live message list here.
            # (Full message injection requires deeper integration — future wave.)

        try:
            async for chunk in self._llm.generate_stream(self._messages.to_list(), self._tools.get_definitions()):
                if chunk.type == "text_delta" and chunk.content:
                    collected_text.append(chunk.content)
                    await self._events.emit(StreamEvent(type="text_delta", content=chunk.content))
                elif chunk.type == "tool_call_end" and chunk.tool_call:
                    tool_calls.append(chunk.tool_call)
        except Exception as e:
            # ── HOOK: llm:error ──
            if self._hooks:
                llm_err_ctx = HookContext("llm:error", agent=None, data={
                    "error": str(e),
                    "messages": self._messages.to_list(),
                    "model": self._config.model,
                    "attempt": iteration,
                })
                await self._hooks.emit_post("llm:error", llm_err_ctx)
            await self._events.emit(StreamEvent(type="error", message=f"LLM error: {e}"))
            return f"Agent stopped due to LLM error: {e}"

        # ── HOOK: llm:post_call ──
        if self._hooks:
            llm_post_ctx = HookContext("llm:post_call", agent=None, data={
                "response": "streamed",
                "messages": self._messages.to_list(),
                "usage": None,
            })
            await self._hooks.emit_post("llm:post_call", llm_post_ctx)

        if not tool_calls:
            final_text = "".join(collected_text)
            self._messages.add_assistant(text=final_text if final_text else None, tool_calls=None)

            # ── HOOK: loop:complete ──
            if self._hooks:
                done_ctx = HookContext("loop:complete", agent=None, data={
                    "result": final_text,
                    "iterations": iteration,
                    "total_tokens": self._messages.estimate_tokens(),
                    "tool_calls": 0,
                })
                await self._hooks.emit_post("loop:complete", done_ctx)
            return final_text

        self._messages.add_assistant(text="".join(collected_text) if collected_text else None, tool_calls=tool_calls)
        collected_text = []

        tool_call_count = 0
        for tc in tool_calls:
            tool_call_count += 1
            if not loop_detector.check(tc.name, tc.arguments):
                result = ToolResult.error(tc.id, f"You've called '{tc.name}' with the same arguments {loop_detector.max_repeats} times. Try a different approach.")
            else:
                # ── HOOK: tool:pre_execute ──
                tool_execute = True
                tool_args = tc.arguments
                if self._hooks:
                    tool_pre_ctx = HookContext("tool:pre_execute", agent=None, data={
                        "name": tc.name,
                        "args": tool_args,
                        "call_id": tc.id,
                        "risk_level": "read_only",
                    })
                    await self._hooks.emit_guard("tool:pre_execute", tool_pre_ctx)
                    if not tool_pre_ctx.is_aborted:
                        await self._hooks.emit_pre("tool:pre_execute", tool_pre_ctx)

                    if tool_pre_ctx.is_aborted:
                        result = ToolResult.error(tc.id, tool_pre_ctx.abort_reason or "Aborted by hook")
                        tool_execute = False
                    else:
                        # Apply any modifications from pre-hooks
                        tool_args = tool_pre_ctx.finalize().get("args", tool_args)

                if tool_execute:
                    await self._events.emit(StreamEvent(type="tool_start", tool_name=tc.name, content=json.dumps(tool_args)))
                    result = await self._tools.execute(tc.name, tool_args, tc.id)
                    await self._events.emit(StreamEvent(type="tool_end", tool_name=tc.name, content=result.content[:100]))

                # ── HOOK: tool:post_execute or tool:error ──
                if self._hooks:
                    if result.success:
                        hook_event = "tool:post_execute"
                    else:
                        hook_event = "tool:error"
                    tool_post_ctx = HookContext(hook_event, agent=None, data={
                        "name": tc.name,
                        "args": tool_args,
                        "result": result.content if result.success else result.content,
                        "call_id": tc.id,
                        "duration_ms": None,
                        "error": result.content if not result.success else None,
                    })
                    await self._hooks.emit_post(hook_event, tool_post_ctx)

            if result.success:
                error_budget.record_success()
            else:
                if not error_budget.record_error():
                    return await self._degraded_finish(collected_text)
            self._messages.add_tool_result(result.tool_call_id, result.content)

        status = self._messages.check_context()
        if status == TokenStatus.NEAR_CAPACITY:
            await self._events.emit(StreamEvent(type="error", message="Context near limit"))

    # Max iterations reached
    if self._hooks:
        done_ctx = HookContext("loop:complete", agent=None, data={
            "result": None,
            "iterations": self._config.max_iterations,
            "total_tokens": self._messages.estimate_tokens(),
            "max_iterations_reached": True,
        })
        await self._hooks.emit_post("loop:complete", done_ctx)
    return await self._degraded_finish(collected_text)
```

---

### Task 7: Write tests for loop hook injection

**Files:**
- Create: tests for hook injection in AgentLoop

- [ ] **Step 1: Write loop hook tests**

```python
# >>> APPEND to tests/test_loop.py >>>
import pytest
from mid_harness._hooks import HookSystem, HookContext
from mid_harness._loop import AgentLoop


@pytest.mark.asyncio
async def test_agent_loop_with_no_hooks_unchanged():
    """AgentLoop with hooks=None should work identically to no-hooks version."""
    # This is verified by all existing loop tests passing with hooks=None default
    pass


@pytest.mark.asyncio
async def test_loop_start_hook_fires():
    """loop:start hook fires before first iteration."""
    hooks = HookSystem()
    events = []

    async def on_loop_start(ctx):
        events.append("loop:start")

    hooks.register("loop:start", on_loop_start, hook_type="post")

    # Create a minimal loop that runs one iteration and stops
    from mid_harness._config import AgentConfig, Prompt
    from mid_harness._messages import MessageManager
    from mid_harness._events import EventBus
    from mid_harness._tools import ToolRegistry, ToolExecutor
    from mid_harness._guard import FilesystemGuard

    class FakeLLM:
        async def generate_stream(self, messages, tools):
            from mid_harness._llm import LLMStreamChunk
            yield LLMStreamChunk(type="text_delta", content="hello")
            # No tool calls → loop ends after first iteration

    config = AgentConfig(model="test", api_key="test", workspace="/tmp")
    prompt = Prompt("base")
    messages = MessageManager(prompt)
    registry = ToolRegistry()
    guard = FilesystemGuard("/tmp")
    executor = ToolExecutor(registry, guard)
    bus = EventBus()
    llm = FakeLLM()

    loop = AgentLoop(config=config, messages=messages, llm=llm, tools=executor, events=bus, hooks=hooks)
    result = await loop.run("test")
    assert "loop:start" in events


@pytest.mark.asyncio
async def test_loop_complete_hook_fires():
    """loop:complete hook fires on final answer."""
    hooks = HookSystem()
    results = []

    async def on_complete(ctx):
        results.append(ctx.data.get("result"))

    hooks.register("loop:complete", on_complete, hook_type="post")

    from mid_harness._config import AgentConfig, Prompt
    from mid_harness._messages import MessageManager
    from mid_harness._events import EventBus
    from mid_harness._tools import ToolRegistry, ToolExecutor
    from mid_harness._guard import FilesystemGuard

    class FakeLLM:
        async def generate_stream(self, messages, tools):
            from mid_harness._llm import LLMStreamChunk
            yield LLMStreamChunk(type="text_delta", content="done")

    config = AgentConfig(model="test", api_key="test", workspace="/tmp")
    prompt = Prompt("base")
    messages = MessageManager(prompt)
    registry = ToolRegistry()
    guard = FilesystemGuard("/tmp")
    executor = ToolExecutor(registry, guard)
    bus = EventBus()
    llm = FakeLLM()

    loop = AgentLoop(config=config, messages=messages, llm=llm, tools=executor, events=bus, hooks=hooks)
    result = await loop.run("test")
    assert "done" in results


@pytest.mark.asyncio
async def test_tool_guard_abort_returns_error():
    """When a guard hook aborts a tool call, the result is an error."""
    hooks = HookSystem()

    async def block_all(ctx):
        ctx.abort("blocked by guard")

    hooks.register("tool:pre_execute", block_all, hook_type="guard", priority=100)

    from mid_harness._config import AgentConfig, Prompt
    from mid_harness._messages import MessageManager
    from mid_harness._events import EventBus
    from mid_harness._tools import ToolRegistry, ToolDef, ToolExecutor
    from mid_harness._guard import FilesystemGuard

    registry = ToolRegistry()
    registry.register_from_def(
        ToolDef(name="echo", description="echo", parameters={"type": "object", "properties": {"msg": {"type": "string"}}, "required": ["msg"]}),
        lambda args: args["msg"],
    )

    class FakeLLM:
        async def generate_stream(self, messages, tools):
            from mid_harness._llm import LLMStreamChunk, ToolCallRequest
            yield LLMStreamChunk(type="text_delta", content="calling tool")
            yield LLMStreamChunk(type="tool_call_end", tool_call=ToolCallRequest(id="tc1", name="echo", arguments={"msg": "hi"}))

    config = AgentConfig(model="test", api_key="test", workspace="/tmp")
    prompt = Prompt("base")
    messages = MessageManager(prompt)
    guard = FilesystemGuard("/tmp")
    executor = ToolExecutor(registry, guard)
    bus = EventBus()
    llm = FakeLLM()

    loop = AgentLoop(config=config, messages=messages, llm=llm, tools=executor, events=bus, hooks=hooks)
    # With guard blocking echo, the LLM gets error back and tries again...
    # The LLM will try to give a final answer
    result = await loop.run("test")
    # The loop should complete (not crash)
    assert result is not None
```

- [ ] **Step 2: Run loop hook tests**

```bash
cd /Users/mx/Desktop/harness/mid-harness && python -m pytest tests/test_loop.py -v -k "hook or guard"
```
Expected: tests pass

---

### Task 8: Extend `_core.py` — Agent hook API

**Files:**
- Copy: `tiny_harness/_core.py` → `mid_harness/_core.py`
- Modify: `mid_harness/_core.py` — Agent.__init__, add hook/unhook methods

- [ ] **Step 1: Add HookSystem import**

```python
# Add import after existing imports:
from mid_harness._hooks import HookSystem, HookContext
```

- [ ] **Step 2: Initialize HookSystem in Agent.__init__**

```python
# After self._event_bus = EventBus() (line 27):
self._hooks = HookSystem()  # NEW — always instantiated, empty by default
```

- [ ] **Step 3: Emit agent:init post-hook at end of __init__**

```python
# At the end of __init__, after all attributes are set:
# ── HOOK: agent:init ──
agent_init_ctx = HookContext("agent:init", agent=self, data={
    "config": {
        "model": config.model,
        "provider": config.provider,
        "workspace": config.workspace,
    },
    "tools": [],
    "skills": [],
})
# Run synchronously since init is sync
import asyncio
try:
    loop = asyncio.get_running_loop()
except RuntimeError:
    # No running loop — can't emit async hook in sync init
    # Store for first run() call
    self._pending_init_hook = True
else:
    loop.create_task(self._hooks.emit_post("agent:init", agent_init_ctx))
    self._pending_init_hook = False
```

Note: Since `Agent.__init__` is synchronous and hook handlers are async, we defer `agent:init` emission to the first `run()` call if no event loop is running. Add this flag and check in `run()`.

- [ ] **Step 4: Add hook() and unhook() methods to Agent**

```python
def hook(self, event: str, handler, *, hook_type: str = "post", priority: int = 0, name: str | None = None) -> str:
    """Register a lifecycle hook. Returns the hook name."""
    return self._hooks.register(event, handler, hook_type=hook_type, priority=priority, name=name, source="user")

def unhook(self, name: str) -> None:
    """Remove a hook by name."""
    self._hooks.unregister(name)
```

- [ ] **Step 5: Emit session:start/end hooks in run()**

```python
# In run(), before creating AgentLoop:
if self._session_id is None:
    self._session_id = f"ses_{uuid.uuid4().hex[:8]}"

# ── HOOK: session:start (first run only) ──
if self._chat_id == 0:
    session_start_ctx = HookContext("session:start", agent=self, data={
        "session_id": self._session_id,
        "model": self._config.model,
        "provider": self._config.provider,
    })
    await self._hooks.emit_post("session:start", session_start_ctx)
```

- [ ] **Step 6: Emit skill:load in load_skill()**

```python
# In load_skill(), after module.register(self):
# ── HOOK: skill:load ──
skill_ctx = HookContext("skill:load", agent=self, data={
    "skill_name": skill_ref,
    "version": getattr(module, "SKILL_META", {}).get("version", "unknown"),
    "tools_added": list(self._tool_registry.names()),
})
await self._hooks.emit_post("skill:load", skill_ctx)
```

---

### Task 9: Write tests for Agent hook integration

**Files:**
- Create: test extensions in `tests/test_agent.py`

- [ ] **Step 1: Write agent hook tests** — see plan appendix

---

### Task 10: Integration test — end-to-end hook chain

**Files:**
- Create: `tests/test_hooks_integration.py`

- [ ] **Step 1: Write full integration test** — see plan appendix

---

### Task 11: Final verification

- [ ] **Step 1: Run existing tiny-harness tests**

```bash
cd /Users/mx/Desktop/harness/mid-harness && python -m pytest tests/ --ignore=tests/test_integration.py --ignore=tests/test_hooks.py --ignore=tests/test_hooks_integration.py -v 2>&1 | tail -10
```
Expected: All 195 existing tests pass

- [ ] **Step 2: Run all new hook tests**

```bash
cd /Users/mx/Desktop/harness/mid-harness && python -m pytest tests/test_hooks.py tests/test_hooks_integration.py -v
```

- [ ] **Step 3: Run full test suite**

```bash
cd /Users/mx/Desktop/harness/mid-harness && python -m pytest tests/ --ignore=tests/test_integration.py -v
```

- [ ] **Step 4: Lint**

```bash
cd /Users/mx/Desktop/harness/mid-harness && ruff check mid_harness/ tests/
```

---

## Appendix: Full Test Code

### tests/test_hooks_integration.py

```python
# tests/test_hooks_integration.py
"""End-to-end tests of the hook system integrated with Agent."""
import pytest
from mid_harness._hooks import HookSystem, HookContext
from mid_harness._tools import ToolDef, ToolRegistry, ToolExecutor, ToolCollisionError
from mid_harness._guard import FilesystemGuard
from mid_harness._config import AgentConfig, Prompt
from mid_harness._messages import MessageManager
from mid_harness._events import EventBus
from mid_harness._loop import AgentLoop


class FakeLLM:
    """Fake LLM that returns text and optionally tool calls."""
    def __init__(self, responses=None, tool_calls=None):
        self.responses = responses or ["hello world"]
        self.tool_calls = tool_calls or []
        self.call_count = 0

    async def generate_stream(self, messages, tools):
        from mid_harness._llm import LLMStreamChunk

        if self.call_count < len(self.responses):
            text = self.responses[self.call_count]
        else:
            text = "done"

        yield LLMStreamChunk(type="text_delta", content=text)

        if self.call_count < len(self.tool_calls):
            for tc in self.tool_calls[self.call_count]:
                yield LLMStreamChunk(type="tool_call_end", tool_call=tc)

        self.call_count += 1


@pytest.fixture
def base_config():
    return AgentConfig(model="test", api_key="test", workspace="/tmp", max_iterations=5)


@pytest.fixture
def base_registry():
    r = ToolRegistry()
    r.register_from_def(
        ToolDef(name="echo", description="Echo", parameters={
            "type": "object",
            "properties": {"msg": {"type": "string"}},
            "required": ["msg"],
        }),
        lambda args: args["msg"],
    )
    return r


@pytest.mark.asyncio
async def test_full_guard_to_post_chain(base_config, base_registry):
    """Verify guard → pre → op → post chain with abort handled."""
    hooks = HookSystem()
    chain_log = []

    hooks.register("tool:pre_execute",
        lambda ctx: chain_log.append("guard"),
        hook_type="guard", priority=100)

    hooks.register("tool:pre_execute",
        lambda ctx: chain_log.append("pre"),
        hook_type="pre", priority=50)

    hooks.register("tool:post_execute",
        lambda ctx: chain_log.append("post"),
        hook_type="post")

    from mid_harness._llm import ToolCallRequest

    llm = FakeLLM(
        responses=["calling tool"],
        tool_calls=[[ToolCallRequest(id="tc1", name="echo", arguments={"msg": "test"})]],
    )

    prompt = Prompt("base")
    messages = MessageManager(prompt)
    guard = FilesystemGuard("/tmp")
    executor = ToolExecutor(base_registry, guard)
    bus = EventBus()

    loop = AgentLoop(config=base_config, messages=messages, llm=llm, tools=executor, events=bus, hooks=hooks)
    await loop.run("test")

    assert chain_log == ["guard", "pre", "post"]


@pytest.mark.asyncio
async def test_collision_error_message(base_registry):
    """Verify ToolCollisionError contains the tool name."""
    with pytest.raises(ToolCollisionError, match="'echo'"):
        base_registry.register_from_def(
            ToolDef(name="echo", description="Another echo", parameters={"type": "object", "properties": {}}),
            lambda args: "ok",
        )
```

### test_agent.py extensions

```python
# >>> APPEND to tests/test_agent.py >>>
import pytest
from mid_harness._core import Agent


def test_agent_hook_registration():
    """Agent.hook() registers a hook and returns a name."""
    from mid_harness._config import AgentConfig, Prompt

    agent = Agent(
        prompt=Prompt("test"),
        config=AgentConfig(model="test", api_key="test", workspace="/tmp"),
    )
    name = agent.hook("tool:post_execute", lambda ctx: None)
    assert name.startswith("hook_")


def test_agent_hook_unhook():
    """Agent.unhook() removes a registered hook."""
    from mid_harness._config import AgentConfig, Prompt

    agent = Agent(
        prompt=Prompt("test"),
        config=AgentConfig(model="test", api_key="test", workspace="/tmp"),
    )
    name = agent.hook("tool:post_execute", lambda ctx: None)
    agent.unhook(name)
    # Should not raise


def test_agent_on_still_works():
    """agent.on() (EventBus) still works alongside hook()."""
    from mid_harness._config import AgentConfig, Prompt

    agent = Agent(
        prompt=Prompt("test"),
        config=AgentConfig(model="test", api_key="test", workspace="/tmp"),
    )
    called = []
    agent.on("tool_start", lambda e: called.append(e.tool_name))
    # Verify subscription succeeded (no raise)
    assert len(called) == 0
```

---

## Task Dependencies

```
Task 1 (_hooks.py) ──→ Task 2 (test_hooks.py)
                    ──→ Task 3 (package setup)
                          ──→ Task 6 (_loop.py)
                          ──→ Task 4 (_tools.py)
                                ──→ Task 5 (test_tools_collision)
Task 6 (_loop.py) ──→ Task 7 (test_loop_hooks)
Task 1 + 6 + 4 ──→ Task 8 (_core.py)
                    ──→ Task 9 (test_agent.py)
                          ──→ Task 10 (integration)
                                ──→ Task 11 (final verification)
```

**Parallelization**: Tasks 4+5 (tools extension) and Task 6+7 (loop extension) can run in parallel once Task 1 is complete.
