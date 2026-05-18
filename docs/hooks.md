# Hook System Design

**Lifecycle hooks for intercepting, modifying, and observing agent operations.**

## 1. Motivation

tiny-harness uses an EventBus for streaming — handlers observe events as they happen but cannot intervene. This works for UX but not for control:

```python
# tiny-harness: passive observation only
agent.on("tool_start", lambda e: print(f"Calling {e.tool_name}"))
agent.on("tool_end", lambda e: print(f"Done: {e.content[:80]}"))
# Cannot: validate args, abort execution, transform result
```

mid-harness hooks are **active interceptors** — they can:
- **Modify** data before an operation (pre-hooks)
- **Observe** results after an operation (post-hooks)
- **Abort** an operation before it runs (guard hooks)
- **Wrap** an operation entirely (wrap hooks — replaces the operation)

## 2. Hook Types

### 2.1 Pre-Hooks

Execute **before** the operation. Can modify input data.

```python
@agent.hook("tool:pre_execute")
async def validate_path(ctx):
    """Ensure file paths are within the workspace."""
    path = ctx.data.get("args", {}).get("path", "")
    if "../" in path:
        ctx.abort(f"Path traversal detected: {path}")
    # Can also modify: ctx.modify("args", sanitized_args)
```

**Pre-hook capabilities:**
- Read the operation's input data
- Modify the input data via `ctx.modify(key, value)`
- Abort the operation via `ctx.abort(reason)`
- Return a transformed context

### 2.2 Post-Hooks

Execute **after** the operation. Can observe output data. **Cannot abort** (the operation already happened).

```python
@agent.hook("tool:post_execute")
async def log_tool_result(ctx):
    """Log all tool results to a telemetry service."""
    result = ctx.data.get("result", {})
    await telemetry.record(
        tool=ctx.data.get("name"),
        success=result.get("success"),
        duration=ctx.data.get("duration_ms"),
    )
```

**Post-hook capabilities:**
- Read the operation's output data
- Transform the output via `ctx.modify(key, value)`
- Add metadata (timing, tags, etc.)
- **Cannot abort** — the operation completed

### 2.3 Guard Hooks

A specialized pre-hook that acts as a gatekeeper. Guard hooks run before pre-hooks and have a single job: allow or deny.

```python
@agent.hook("tool:pre_execute", hook_type="guard", priority=100)
async def block_destructive_ops(ctx):
    """Block all destructive operations in safe mode."""
    if agent.config.safe_mode and ctx.data.get("risk_level") == "destructive":
        ctx.abort("Destructive operations blocked in safe mode")
```

**Guard hook rules:**
- Run first (highest priority in the pre-hook chain)
- Cannot modify data — only allow or deny
- If any guard hook aborts, no pre-hooks or the operation run
- Guard hooks are the security boundary

### 2.4 Wrap Hooks

Replace an entire operation. The wrap hook receives the original operation as a callable and decides whether to call it, modify it, or replace it entirely.

```python
@agent.hook("tool:pre_execute", hook_type="wrap")
async def cached_tool_execution(ctx, original):
    """Cache tool results to avoid redundant calls."""
    cache_key = f"{ctx.data['name']}:{json.dumps(ctx.data['args'])}"
    cached = await cache.get(cache_key)
    if cached:
        return cached  # Skip the original operation

    result = await original()  # Call the original
    await cache.set(cache_key, result, ttl=300)
    return result
```

**Wrap hook capabilities:**
- Receive the original operation as a callable
- Can call the original, modify its inputs first, or skip it entirely
- Can transform the output
- Only one wrap hook per hook point (the outermost wins if multiple registered)

## 3. Hook Points (Lifecycle Events)

### 3.1 Agent Lifecycle

| Hook Point | Type | Triggers | Context Data |
|---|---|---|---|
| `agent:init` | post | After `Agent.__init__()` | `{config, tools: list, skills: list}` |
| `agent:shutdown` | post | Before Agent destruction | `{session_id, total_iterations, total_tokens}` |

```python
agent.hook("agent:init", lambda ctx: print(f"Agent ready with {len(ctx.data['tools'])} tools"))
agent.hook("agent:shutdown", lambda ctx: metrics.flush())
```

### 3.2 Session Lifecycle

| Hook Point | Type | Triggers | Context Data |
|---|---|---|---|
| `session:start` | post | Session begins | `{session_id, model, provider}` |
| `session:end` | post | Session ends (user exits or error) | `{session_id, turns, total_tokens}` |

```python
agent.hook("session:start", lambda ctx: logger.info(f"Session {ctx.data['session_id']} started"))
agent.hook("session:end", lambda ctx: save_session_summary(ctx.data))
```

### 3.3 Skill Lifecycle

| Hook Point | Type | Triggers | Context Data |
|---|---|---|---|
| `skill:load` | post | After skill registered | `{skill_name, version, tools_added, mcp_servers}` |
| `skill:unload` | pre | Before skill removed | `{skill_name, tools_to_remove}` |

```python
agent.hook("skill:load", lambda ctx: print(f"Loaded {ctx.data['skill_name']} v{ctx.data['version']}"))
agent.hook("skill:unload", lambda ctx: validate_can_unload(ctx.data['skill_name']))
```

### 3.4 Loop Lifecycle

| Hook Point | Type | Triggers | Context Data |
|---|---|---|---|
| `loop:start` | pre/post | Before/after loop begins | `{messages, tools, max_iterations}` |
| `loop:iteration` | pre/post | Each loop iteration | `{num, max, messages, tokens_used}` |
| `loop:complete` | post | Final answer ready | `{result, iterations, total_tokens, tool_calls}` |

```python
# Pre-hook: inject additional context before loop starts
agent.hook("loop:start", priority=10, hook_type="pre",
    handler=lambda ctx: inject_project_context(ctx))

# Post-hook: track iteration metrics
agent.hook("loop:iteration",
    handler=lambda ctx: print(f"Iteration {ctx.data['num']}/{ctx.data['max']}"))
```

### 3.5 LLM Lifecycle

| Hook Point | Type | Triggers | Context Data |
|---|---|---|---|
| `llm:pre_call` | pre | Before LLM API call | `{messages, tools, model, provider}` |
| `llm:post_call` | post | After LLM API call | `{response, messages, usage}` |
| `llm:error` | post | On LLM API error | `{error, messages, model, attempt}` |

```python
# Pre-hook: inject user-specific context into messages
agent.hook("llm:pre_call",
    handler=lambda ctx: ctx.modify("messages", add_user_context(ctx.data["messages"])))

# Post-hook: track token usage
agent.hook("llm:post_call",
    handler=lambda ctx: budget.track(ctx.data["usage"]))

# Error hook: alert on repeated failures
agent.hook("llm:error",
    handler=lambda ctx: alert_if_critical(ctx.data["error"], ctx.data["attempt"]))
```

### 3.6 Tool Lifecycle

| Hook Point | Type | Triggers | Context Data |
|---|---|---|---|
| `tool:pre_execute` | pre/guard/wrap | Before tool execution | `{name, args, call_id, risk_level, handler}` |
| `tool:post_execute` | post | After tool execution | `{name, args, result, call_id, duration_ms}` |
| `tool:error` | post | On tool execution error | `{name, args, error, call_id}` |

```python
# Guard: block operations outside workspace
agent.hook("tool:pre_execute", hook_type="guard", priority=100,
    handler=lambda ctx: guard_workspace(ctx))

# Pre-hook: sanitize file paths
agent.hook("tool:pre_execute", priority=50,
    handler=lambda ctx: sanitize_paths(ctx))

# Post-hook: log all tool results
agent.hook("tool:post_execute",
    handler=lambda ctx: audit.log(ctx.data["name"], ctx.data["result"]))
```

### 3.7 Complete Hook Point Reference

| # | Hook Point | Types Available | Abort | Modify | Wrap |
|---|---|---|---|---|---|
| 1 | `agent:init` | post | — | — | — |
| 2 | `agent:shutdown` | post | — | — | — |
| 3 | `session:start` | post | — | — | — |
| 4 | `session:end` | post | — | — | — |
| 5 | `skill:load` | post | — | — | — |
| 6 | `skill:unload` | pre | ✗ | — | — |
| 7 | `loop:start` | pre, post | ✗ | messages | — |
| 8 | `loop:iteration` | pre, post | ✗ (exit loop) | — | — |
| 9 | `loop:complete` | post | — | — | — |
| 10 | `llm:pre_call` | pre | ✗ | messages, tools | ✗ |
| 11 | `llm:post_call` | post | — | response | — |
| 12 | `llm:error` | post | — | — | — |
| 13 | `tool:pre_execute` | pre, guard, wrap | ✗ | args | ✗ |
| 14 | `tool:post_execute` | post | — | result | — |
| 15 | `tool:error` | post | — | — | — |

## 4. Hook API

### 4.1 Registering Hooks

```python
from mid_harness import Agent, Hook

# Method 1: Decorator
@agent.hook("tool:pre_execute")
async def validate_tool_args(ctx: HookContext):
    if not ctx.data.get("args"):
        ctx.abort("No arguments provided")

# Method 2: Explicit registration
def validate_tool_args(ctx: HookContext):
    ...

agent.hook("tool:pre_execute", handler=validate_tool_args, priority=50)

# Method 3: Named hooks (for unregistration)
agent.hook("tool:pre_execute", handler=validate_tool_args, name="my-validator")
agent.unhook("my-validator")

# Method 4: Guard hooks (security boundary)
agent.hook("tool:pre_execute", hook_type="guard", priority=100,
    handler=security_check)

# Method 5: Wrap hooks (replace operation)
agent.hook("tool:pre_execute", hook_type="wrap",
    handler=caching_wrapper)
```

### 4.2 HookContext API

```python
class HookContext:
    event: str              # Hook point name (e.g., "tool:pre_execute")
    agent: "Agent"          # Reference to the agent instance
    data: dict              # Event-specific payload (messages, args, result, etc.)
    _aborted: bool          # Internal: whether hook chain is aborted
    _abort_reason: str      # Internal: reason for abort

    def abort(self, reason: str) -> None:
        """Abort the operation. Subsequent hooks in the chain still run."""
        self._aborted = True
        self._abort_reason = reason

    def modify(self, key: str, value: Any) -> None:
        """Modify data in the context (pre-hooks only)."""
        self.data[key] = value
```

### 4.3 Hook Execution Order

Hooks execute in priority order within each type. The chain order is:

```
Guard hooks (highest priority, then descending)
  → Pre-hooks (highest priority, then descending)
    → [OPERATION EXECUTES] (unless aborted)
      → Post-hooks (highest priority, then descending)

Wrap hooks replace the entire chain:
Wrap hook (only one) → [wrap decides whether to call original]
```

**Priority values**: Higher = runs first. Default = 0. Range: -100 to 100.

```
Guard hooks:    100-50   (security, validation)
Pre-hooks:      49-0     (transformation, enrichment)
Default:        0
Post-hooks:     0-(-50)  (logging, telemetry)
Cleanup:        -50-(-100) (cleanup, metrics flush)
```

### 4.4 Abort Behavior

When a hook calls `ctx.abort(reason)`:

1. The current hook completes
2. Remaining hooks **in the same type** still execute (they may add context to the abort reason)
3. Hooks in the **next type** (e.g., pre → operation) do NOT execute
4. The operation is skipped
5. The abort reason is propagated to the caller

```python
# Example: two guard hooks
@agent.hook("tool:pre_execute", hook_type="guard", priority=100)
async def guard1(ctx):
    ctx.abort("Blocked by guard1")  # Abort is set

@agent.hook("tool:pre_execute", hook_type="guard", priority=90)
async def guard2(ctx):
    # This STILL runs even though guard1 aborted
    # Can add more context to the abort reason
    ctx.abort(f"{ctx._abort_reason} + blocked by guard2")
    # Result: "Blocked by guard1 + blocked by guard2"

# After all guards run, if _aborted is True:
# → No pre-hooks run, no operation executes
# → The abort reason is returned as the operation's error
```

### 4.5 Hook Registration in Skills

Skills declare hooks in their `skill.md` manifest:

```yaml
---
name: security-audit
version: 1.0.0

hooks:
  - event: tool:pre_execute
    handler: security_audit.validate_tool_call
    type: guard
    priority: 100

  - event: tool:post_execute
    handler: security_audit.log_tool_result
    type: post
    priority: 0
---
```

## 5. Integration with Agent Loop

### 5.1 Loop with Hook Injection

The agent loop injects hook calls at each lifecycle point. Hooks are called via `await` — they can be async.

```python
class AgentLoop:
    def __init__(self, ..., hooks: HookSystem):
        self._hooks = hooks

    async def run(self, prompt: str) -> str:
        # --- loop:start ---
        ctx = HookContext("loop:start", agent=self._agent, data={
            "messages": self._messages,
            "tools": self._tools.get_definitions(),
            "max_iterations": self._config.max_iterations,
        })
        await self._hooks.emit_pre("loop:start", ctx)
        # pre-hooks may have modified messages
        messages = ctx.data["messages"]

        await self._hooks.emit_post("loop:start", ctx)

        for iteration in range(self._config.max_iterations):
            # --- loop:iteration ---
            iter_ctx = HookContext("loop:iteration", agent=self._agent, data={
                "num": iteration + 1,
                "max": self._config.max_iterations,
                "messages": messages,
                "tokens_used": estimate_tokens(messages),
            })
            await self._hooks.emit_pre("loop:iteration", iter_ctx)
            await self._hooks.emit_post("loop:iteration", iter_ctx)

            # --- llm:pre_call ---
            llm_ctx = HookContext("llm:pre_call", agent=self._agent, data={
                "messages": messages,
                "tools": self._tools.get_definitions(),
                "model": self._config.model,
                "provider": self._config.provider,
            })
            await self._hooks.emit_pre("llm:pre_call", llm_ctx)

            if llm_ctx._aborted:
                return f"LLM call aborted: {llm_ctx._abort_reason}"

            messages = llm_ctx.data.get("messages", messages)
            tool_defs = llm_ctx.data.get("tools", self._tools.get_definitions())

            try:
                response = await self._llm.generate(messages, tool_defs)
            except Exception as e:
                # --- llm:error ---
                err_ctx = HookContext("llm:error", agent=self._agent, data={
                    "error": str(e),
                    "messages": messages,
                    "model": self._config.model,
                    "attempt": iteration + 1,
                })
                await self._hooks.emit_post("llm:error", err_ctx)
                continue

            # --- llm:post_call ---
            llm_post_ctx = HookContext("llm:post_call", agent=self._agent, data={
                "response": response,
                "messages": messages,
                "usage": getattr(response, "usage", None),
            })
            await self._hooks.emit_post("llm:post_call", llm_post_ctx)

            if response.text and not response.tool_calls:
                # --- loop:complete ---
                done_ctx = HookContext("loop:complete", agent=self._agent, data={
                    "result": response.text,
                    "iterations": iteration + 1,
                    "total_tokens": estimate_tokens(messages),
                    "tool_calls": self._tool_call_count,
                })
                await self._hooks.emit_post("loop:complete", done_ctx)
                return response.text

            messages.append(response.as_message())

            for tc in response.tool_calls:
                # --- tool:pre_execute ---
                tool_ctx = HookContext("tool:pre_execute", agent=self._agent, data={
                    "name": tc.name,
                    "args": tc.arguments,
                    "call_id": tc.id,
                    "risk_level": self._tools.get_risk_level(tc.name),
                    "handler": self._tools.get_handler(tc.name),
                })

                # Guard hooks first
                await self._hooks.emit_guard("tool:pre_execute", tool_ctx)
                if tool_ctx._aborted:
                    # Try wrap hooks even if guard aborted? No — skip.
                    pass
                else:
                    # Pre-hooks
                    await self._hooks.emit_pre("tool:pre_execute", tool_ctx)

                if tool_ctx._aborted:
                    result = ToolResult.error(tc.id, tool_ctx._abort_reason)
                else:
                    # Check for wrap hook
                    wrapper = self._hooks.get_wrap("tool:pre_execute")
                    if wrapper:
                        result = await wrapper(tool_ctx, lambda: self._tools.execute(
                            tool_ctx.data["name"],
                            tool_ctx.data["args"],
                            tc.id
                        ))
                    else:
                        try:
                            result = await self._tools.execute(
                                tool_ctx.data["name"],
                                tool_ctx.data["args"],
                                tc.id
                            )
                        except Exception as e:
                            # --- tool:error ---
                            err_ctx = HookContext("tool:error", agent=self._agent, data={
                                "name": tc.name,
                                "args": tc.arguments,
                                "error": str(e),
                                "call_id": tc.id,
                            })
                            await self._hooks.emit_post("tool:error", err_ctx)
                            result = ToolResult.error(tc.id, str(e))

                # --- tool:post_execute ---
                tool_post_ctx = HookContext("tool:post_execute", agent=self._agent, data={
                    "name": tc.name,
                    "args": tc.arguments,
                    "result": result,
                    "call_id": tc.id,
                    "duration_ms": None,  # Filled by executor
                })
                await self._hooks.emit_post("tool:post_execute", tool_post_ctx)
                result = tool_post_ctx.data.get("result", result)

                messages.append(tool_result(tc.id, result))

        # Max iterations reached — force final answer
        done_ctx = HookContext("loop:complete", agent=self._agent, data={
            "result": None,
            "iterations": self._config.max_iterations,
            "total_tokens": estimate_tokens(messages),
            "max_iterations_reached": True,
        })
        await self._hooks.emit_post("loop:complete", done_ctx)
        return "Max iterations reached without final answer."
```

### 5.2 HookSystem Implementation

```python
class HookSystem:
    def __init__(self):
        self._hooks: dict[str, dict[str, list[HookRegistration]]] = {
            hook_point: {"guard": [], "pre": [], "post": [], "wrap": []}
            for hook_point in ALL_HOOK_POINTS
        }

    def register(self, event: str, handler: Callable, *,
                 hook_type: str = "post", priority: int = 0,
                 name: str | None = None) -> str:
        """Register a hook. Returns hook name for unregistration."""
        if event not in self._hooks:
            raise ValueError(f"Unknown hook point: {event}")
        if hook_type not in self._hooks[event]:
            raise ValueError(f"Invalid hook type '{hook_type}' for '{event}'")

        name = name or f"hook_{uuid4().hex[:8]}"
        reg = HookRegistration(name=name, handler=handler, priority=priority)
        self._hooks[event][hook_type].append(reg)
        self._hooks[event][hook_type].sort(key=lambda r: -r.priority)
        return name

    def unregister(self, name: str) -> None:
        for event_types in self._hooks.values():
            for hook_list in event_types.values():
                hook_list[:] = [h for h in hook_list if h.name != name]

    async def emit_guard(self, event: str, ctx: HookContext) -> None:
        for reg in self._hooks[event]["guard"]:
            await reg.handler(ctx)

    async def emit_pre(self, event: str, ctx: HookContext) -> None:
        for reg in self._hooks[event]["pre"]:
            if ctx._aborted:
                break  # Still execute remaining pre-hooks but no point in modifying
            await reg.handler(ctx)

    async def emit_post(self, event: str, ctx: HookContext) -> None:
        for reg in self._hooks[event]["post"]:
            await reg.handler(ctx)

    def get_wrap(self, event: str) -> Callable | None:
        wraps = self._hooks[event]["wrap"]
        return wraps[0].handler if wraps else None
```

## 6. Use Cases

### 6.1 Security: Block Dangerous Operations

```python
@agent.hook("tool:pre_execute", hook_type="guard", priority=100)
async def block_destructive_in_production(ctx):
    if os.environ.get("ENV") == "production":
        if ctx.data.get("risk_level") == "destructive":
            ctx.abort(f"Destructive tool '{ctx.data['name']}' blocked in production")
```

### 6.2 Observability: Token Budget Tracking

```python
@agent.hook("llm:post_call")
async def track_token_usage(ctx):
    usage = ctx.data.get("usage", {})
    await metrics.increment("tokens.input", usage.get("input_tokens", 0))
    await metrics.increment("tokens.output", usage.get("output_tokens", 0))

@agent.hook("tool:post_execute")
async def track_tool_latency(ctx):
    await metrics.histogram("tool.duration_ms", ctx.data.get("duration_ms", 0),
        tags={"tool": ctx.data["name"], "success": ctx.data["result"].success})
```

### 6.3 Context Management: Automatic Compaction

```python
@agent.hook("loop:iteration", hook_type="pre")
async def compact_if_needed(ctx):
    messages = ctx.data["messages"]
    tokens = estimate_tokens(messages)
    if tokens > 150_000:  # Model's context window is 200K
        compacted = await context_compactor.compact(messages)
        ctx.modify("messages", compacted)
```

### 6.4 Caching: Tool Result Memoization

```python
@agent.hook("tool:pre_execute", hook_type="wrap")
async def cache_tool_results(ctx, original):
    key = f"{ctx.data['name']}:{hash_json(ctx.data['args'])}"
    cached = await cache_store.get(key)
    if cached:
        return cached

    result = await original()
    if result.success:
        await cache_store.set(key, result, ttl=300)
    return result
```

### 6.5 Auditing: Full Session Log

```python
@agent.hook("tool:post_execute")
async def audit_trail(ctx):
    await audit_db.insert({
        "session_id": ctx.agent.session_id,
        "timestamp": time.time(),
        "tool": ctx.data["name"],
        "args": ctx.data["args"],
        "result": ctx.data["result"].content[:500],
        "success": ctx.data["result"].success,
    })

@agent.hook("llm:post_call")
async def audit_llm_calls(ctx):
    await audit_db.insert({
        "session_id": ctx.agent.session_id,
        "timestamp": time.time(),
        "type": "llm_call",
        "usage": ctx.data.get("usage", {}),
    })
```

## 7. Design Decisions

| Decision | Rationale |
|---|---|
| Hooks run even after abort | Allows all guard hooks to contribute abort context. Security hooks should always run. |
| Priority ordering (higher = first) | Guards need to run before transforms. Default 0 is neutral. |
| Named hooks for unregistration | Skills need to clean up their hooks on unload. |
| Wrap hooks are singular | Multiple wraps create ambiguous execution order. One wrap per hook point. |
| `HookContext.data` is a plain dict | No magic attributes. Explicit keys. Easy to debug. |
| Async hooks only | All hook handlers are async. Sync handlers are wrapped. Consistency over convenience. |
| Hooks are fire-and-forget (no return values) | Modifications happen through `ctx.modify()`. Aborts through `ctx.abort()`. No complex return value semantics. |
| Separate HookSystem from EventBus | EventBus is for streaming UX. HookSystem is for lifecycle control. Different purposes, different APIs. |
