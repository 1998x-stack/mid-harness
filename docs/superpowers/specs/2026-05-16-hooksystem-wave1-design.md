# mid-harness Wave 1 Implementation Spec

**Date**: 2026-05-16
**Status**: Draft
**Scope**: HookSystem core — lifecycle hooks for agent operations

## 1. Overview

Add a HookSystem to tiny-harness that allows intercepting, modifying, and observing agent operations at 14 lifecycle hook points (of 15 total; `skill:unload` deferred). The HookSystem is minimally invasive — existing code paths are unchanged when no hooks are registered.

### Non-Goals (deferred to later waves)
- MCP integration (Wave 2)
- Progressive skill loader with skill.md (Wave 3)
- Script/asset auto-generated tools (Wave 3)
- skill:unload hook point (post-v0.1)
- Wrap hooks (post-v0.1 — guard/pre/post only in Wave 1)

## 2. Architecture

### Module Changes

```
mid_harness/
├── _hooks.py              NEW — HookSystem, HookContext, HookRegistration
├── _core.py               EXTENDED — agent.hook(), HookSystem init
├── _loop.py               EXTENDED — 10 hook injection points
├── _tools.py              EXTENDED — collision detection, reserved names
├── (all other files)      UNCHANGED
```

### Dependency Graph (no cycles)

```
_hooks.py (standalone, no internal deps)
  ↓
_core.py → _hooks.py
  ↓
_loop.py → _hooks.py (optional HookSystem param)
  ↓
_tools.py (standalone, extended collision detection)
```

### Hook Types

| Type | Runs | Can Modify | Can Abort | Can Replace |
|---|---|---|---|---|
| guard | Before all others | No | Yes | No |
| pre | After guards, before op | Yes | Yes | No |
| post | After operation | No (observe only) | No | No |
| wrap | Replaces operation | N/A | N/A | Yes (deferred) |

### Hook Points Active in Wave 1

| # | Hook Point | Types | data keys |
|---|---|---|---|
| 1 | `agent:init` | post | `{config, tools, skills}` |
| 2 | `agent:shutdown` | post | `{session_id, total_iterations, total_tokens}` |
| 3 | `session:start` | post | `{session_id, model, provider}` |
| 4 | `session:end` | post | `{session_id, turns, total_tokens}` |
| 5 | `skill:load` | post | `{skill_name, version, tools_added}` |
| 6 | `loop:start` | pre, post | `{messages, tools, max_iterations}` |
| 7 | `loop:iteration` | pre, post | `{num, max, messages, tokens_used}` |
| 8 | `loop:complete` | post | `{result, iterations, total_tokens, tool_calls}` |
| 9 | `llm:pre_call` | pre | `{messages, tools, model, provider}` |
| 10 | `llm:post_call` | post | `{response, messages, usage}` |
| 11 | `llm:error` | post | `{error, messages, model, attempt}` |
| 12 | `tool:pre_execute` | guard, pre | `{name, args, call_id, risk_level}` |
| 13 | `tool:post_execute` | post | `{name, args, result, call_id, duration_ms}` |
| 14 | `tool:error` | post | `{name, args, error, call_id}` |

### Priority Tiebreaker

When multiple hooks share the same type and priority:
1. User hooks (`agent.hook()`) run before Skill hooks
2. Within same source, hooks run in registration order (FIFO)

## 3. New Module: `_hooks.py`

### Classes

```python
@dataclass
class HookRegistration:
    name: str
    handler: Callable  # async (HookContext) -> None
    priority: int
    source: str        # "user" | "skill:{name}"


class HookContext:
    event: str
    agent: "Agent"
    data: dict          # shallow-copied on entry

    def abort(self, reason: str) -> None
    def modify(self, key: str, value: Any) -> None


class HookSystem:
    def __init__(self)
    def register(self, event: str, handler: Callable, *,
                 hook_type: str = "post", priority: int = 0,
                 name: str | None = None, source: str = "user") -> str
    def unregister(self, name: str) -> None
    async def emit_guard(self, event: str, ctx: HookContext) -> None
    async def emit_pre(self, event: str, ctx: HookContext) -> None
    async def emit_post(self, event: str, ctx: HookContext) -> None
    def get_wrap(self, event: str) -> Callable | None   # deferred
```

### HookContext.data Semantics

- `ctx.data` is shallow-copied from the operation's data on entry
- Hooks MUST use `ctx.modify(key, value)` to persist changes
- Direct mutations to `ctx.data` values are detected at chain exit
- Mutated-but-not-modified values are logged as warnings and NOT propagated
- This prevents silent state corruption from accidental mutations

### Execution Chain

```
For tool:pre_execute (has guard + pre types):
  1. Create HookContext with shallow-copied data
  2. Run guard hooks in priority order (user→skill→FIFO)
     - If any guard calls ctx.abort(): mark _aborted
     - Remaining guards still run (all guards contribute to abort context)
  3. If _aborted: skip pre-hooks and operation, return error
  4. Run pre-hooks in priority order
     - ctx.modify() persists changes
     - ctx.abort() blocks operation
  5. If _aborted: skip operation, return error
  6. Execute operation with modified args
  7. On success: run post-hooks
  8. On failure: run error hooks
```

## 4. Extended Module: `_tools.py`

### Collision Detection

- **Skill-to-skill**: second registration with same name → `ToolCollisionError`
- **Reserved names**: `read_reference`, `run_skill_script`, `read_skill_asset`, `render_template` → blocked
- **MCP namespace**: names matching `{string}.{string}` → blocked (future-proofing)

### Backward Compatibility

- `register(Tool(...))` now raises on collision instead of silent overwrite
- `register_from_def()` and `register_tool()` same behavior
- Existing tests that rely on silent overwrite need updating

## 5. Extended Module: `_loop.py`

### AgentLoop Changes

- New optional `hooks: HookSystem | None` parameter in `__init__`
- When `hooks is None`: identical behavior to tiny-harness (zero overhead)
- When `hooks is not None`: conditional hook calls at 10 injection points

### Injection Points

```python
# loop:start — emit before first iteration
# loop:iteration — emit at each iteration start
# llm:pre_call — emit before LLM API call (can modify messages)
# llm:post_call — emit after LLM API call
# llm:error — emit on LLM API error
# tool:pre_execute — guard + pre hooks before tool execution
# tool:post_execute — post hooks after successful tool execution
# tool:error — post hooks after failed tool execution
# loop:complete — emit on final answer
```

## 6. Extended Module: `_core.py`

### Agent Changes

```python
class Agent:
    def __init__(self, prompt, config, store=None):
        # ... existing init ...
        self._hooks = HookSystem()  # always instantiated, empty by default
        # emit agent:init post-hook after full initialization

    def hook(self, event, handler, *, hook_type="post", priority=0, name=None) -> str:
        return self._hooks.register(event, handler, hook_type=hook_type,
                                     priority=priority, name=name, source="user")

    def unhook(self, name: str) -> None:
        self._hooks.unregister(name)

    # load_skill extended: emit skill:load post-hook
    # run/run_stream: emit session:start on first call, session:end on shutdown
    # AgentLoop created with self._hooks
```

## 7. Testing

### test_hooks.py (new, ~15 tests)

| Test | What it verifies |
|---|---|
| `test_register_valid_event` | Registration with valid event name succeeds |
| `test_register_invalid_event` | Registration with unknown event → ValueError |
| `test_priority_ordering` | Higher priority hooks run first |
| `test_user_before_skill` | User hooks before skill hooks at same priority |
| `test_guard_abort_blocks_op` | Guard abort → operation skipped, abort reason returned |
| `test_guard_abort_remaining_guards_run` | All guards run even after abort |
| `test_pre_modify_persists` | ctx.modify() changes survive to operation |
| `test_pre_abort_blocks_op` | Pre-hook abort blocks operation |
| `test_direct_mutation_warned` | Direct dict mutation → warning logged, not propagated |
| `test_post_observe_result` | Post-hook sees operation result |
| `test_unregister_removes_hook` | Unregistration removes hook from chain |
| `test_duplicate_name_rejected` | Same hook name twice → ValueError |
| `test_handler_exception_chain_continues` | Handler raises → caught, chain continues |
| `test_empty_hook_chain_zero_overhead` | No hooks → conditional skipped |
| `test_wrap_replaces_operation` | (deferred) |

### test_loop.py (extended, ~6 tests)

| Test | What it verifies |
|---|---|
| `test_loop_start_hook` | loop:start fires before first iteration |
| `test_loop_iteration_hook` | loop:iteration fires each iteration |
| `test_llm_pre_call_modifies_messages` | llm:pre_call pre-hook changes reach LLM |
| `test_llm_post_call_observes_response` | llm:post_call sees LLM response |
| `test_tool_guard_abort_returns_error` | Guard abort → error returned as tool result |
| `test_tool_post_execute_transforms_result` | Post-hook can transform tool result |

### test_tools.py (extended, ~3 tests)

| Test | What it verifies |
|---|---|
| `test_collision_raises_error` | Duplicate tool name → ToolCollisionError |
| `test_reserved_name_blocked` | Reserved name registration → ToolCollisionError |
| `test_mcp_namespace_blocked` | dotted name registration → ToolCollisionError |

## 8. Success Criteria

- [ ] All 195 existing tiny-harness tests pass unchanged
- [ ] ~24 new tests pass (15 hooks + 6 loop + 3 tools)
- [ ] ~400 lines of new code (not counting tests)
- [ ] Zero performance overhead when no hooks registered
- [ ] `agent.on()` and `agent.events` APIs unchanged
- [ ] `agent.load_skill()` works with legacy skill format
- [ ] Ruff lint clean on all changed files
- [ ] HookContext shallow-copy warning fires on direct mutation

## 9. Dependencies

- Python 3.11+ (same as tiny-harness)
- `httpx` (same as tiny-harness)
- No new dependencies
- `pytest`, `pytest-asyncio` for testing (same as tiny-harness dev deps)
