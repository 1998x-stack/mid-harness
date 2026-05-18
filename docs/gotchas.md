# Gotchas — mid-harness

Common pitfalls, surprising behaviors, and edge cases in mid-harness, plus inherited gotchas from tiny-harness.

---

## Hook System

### Hook handlers receive `ctx: HookContext`, NOT raw data

The HookSystem passes a single `HookContext` object to handlers. Access data via `ctx.data`, not directly:

```python
# ✅ Correct
agent.hook("tool:pre_execute", lambda ctx: print(ctx.data["name"]))

# ❌ Wrong — ctx is not a dict
agent.hook("tool:pre_execute", lambda ctx: print(ctx["name"]))

# ❌ Wrong — handler receives ctx, not args
agent.hook("tool:pre_execute", lambda args: print(args["name"]))

# ❌ Wrong — handler receives ctx, not event
agent.hook("tool:pre_execute", lambda event: print(event.tool_name))
```

**Runtime guard**: The HookSystem validates handler signatures at registration time. Zero-param handlers raise `ValueError`. Handlers with parameter named `args`, `event`, or `data` emit a warning. Functions with `*args` or `**kwargs` skip validation.

### `ctx.modify()` is required to persist changes

HookContext.data is shallow-copied on entry. Changes must go through `ctx.modify(key, value)`:

```python
# ✅ Correct — modification persists to the operation
def sanitize(ctx):
    args = dict(ctx.data["args"])
    args["path"] = safe_path(args["path"])
    ctx.modify("args", args)

# ❌ Wrong — change is silently discarded with a warning
def sanitize(ctx):
    ctx.data["args"]["path"] = safe_path(ctx.data["args"]["path"])
```

Direct mutations are detected at hook chain exit (identity check) and logged as warnings. The mutation is NOT propagated.

### Hook types are restricted per hook point

Not all hook types are valid for all hook points:

| Hook Type | Valid For |
|---|---|
| `guard` | `tool:pre_execute` only |
| `pre` | `loop:start`, `loop:iteration`, `llm:pre_call`, `tool:pre_execute` |
| `post` | All hook points |
| `wrap` | Deferred (post-v0.1) |

Registering an invalid type raises `ValueError`:

```python
# ❌ ValueError: Hook type 'guard' is not allowed for 'agent:init'
agent.hook("agent:init", handler, hook_type="guard")
```

### Guard hooks run even after abort

When a guard hook calls `ctx.abort()`, remaining guards in the chain still execute. This ensures all security checks apply, regardless of which one triggered the abort. Abort reasons concatenate:

```python
ctx.abort("check1 failed")  # abort_reason = "check1 failed"
# ... another guard runs ...
ctx.abort("check2 failed")  # abort_reason = "check1 failed; check2 failed"
```

### Hook handlers are exception-resilient

If a hook handler raises an exception:
- **Guard hooks**: Exception is caught, logged as warning, and becomes the abort reason
- **Pre/post hooks**: Exception is caught, logged as warning, chain continues

This ensures a single buggy hook doesn't break the entire agent.

### Sync and async handlers both work

```python
# ✅ Sync handler
agent.hook("tool:post_execute", lambda ctx: print(ctx.data["name"]))

# ✅ Async handler
async def log_to_db(ctx):
    await db.insert(ctx.data)
agent.hook("tool:post_execute", log_to_db)
```

The HookSystem detects async handlers via `inspect.iscoroutinefunction` and awaits them accordingly.

### Hook registration is eager — validates immediately

Invalid event names and hook types are rejected at registration time, not at emission time:

```python
# ❌ ValueError: Unknown hook point: 'bad:event'
agent.hook("bad:event", handler)

# ❌ ValueError: Hook type 'guard' is not allowed for 'agent:init'
agent.hook("agent:init", handler, hook_type="guard")
```

---

## Tool System

### Duplicate tool names raise `ToolCollisionError`

Unlike tiny-harness (which silently overwrote), mid-harness strictly enforces unique tool names:

```python
registry.register_from_def(ToolDef(name="deploy", ...), handler1)
registry.register_from_def(ToolDef(name="deploy", ...), handler2)
# ❌ ToolCollisionError: Tool 'deploy' is already registered
```

### Reserved tool names are blocked

Four names are reserved for auto-generated tools (future Wave 3): `read_reference`, `run_skill_script`, `read_skill_asset`, `render_template`.

### Dotted tool names are blocked (MCP namespace)

Names containing `.` (e.g., `git-server.git_commit`) are reserved for MCP proxy tools:

```python
# ❌ ToolCollisionError: Tool name with dot notation is reserved
registry.register_from_def(ToolDef(name="git.commit", ...), handler)
```

### Skill load failures raise `SkillLoadError`

`load_skill()` wraps `ToolCollisionError` in `SkillLoadError` with skill context:

```python
agent.load_skill("deployer")
# If deployer registers a tool that already exists:
# ❌ SkillLoadError: Skill 'deployer' failed to load: Tool 'deploy' is already registered
```

---

## Agent

### `agent.on()` and `agent.hook()` are separate systems

- `agent.on(event_type, handler)` → EventBus (streaming UX, passive observation)
- `agent.hook(event, handler, ...)` → HookSystem (lifecycle control, can modify/abort)

They have different APIs, different handler signatures, and different capabilities. Don't confuse them:

```python
# EventBus — passive, receives StreamEvent
agent.on("tool_start", lambda event: print(event.tool_name))

# HookSystem — active, receives HookContext, can modify/abort
agent.hook("tool:pre_execute", lambda ctx: ctx.abort("blocked"))
```

### `run()` and `run_stream()` both fire lifecycle hooks

Both `agent.run()` and `agent.run_stream()` emit `agent:init` (first call only), `session:start` (first call only), and all loop-level hooks. The `_ensure_lifecycle_hooks()` method guarantees consistent emission regardless of which method is called first.

### HookSystem is always instantiated but zero-overhead

The Agent always creates a `HookSystem` instance (`self._hooks = HookSystem()`), but no hooks are registered by default. The AgentLoop checks `if self._hooks:` before each injection point — when no hooks are registered, there's zero runtime overhead.

---

## Inherited from tiny-harness

These tiny-harness gotchas still apply to mid-harness:

### Tool handlers still receive `args: dict`, not `**kwargs`

```python
# ✅ Correct
def my_tool(args: dict) -> str:
    return args["path"]

# ❌ Wrong — will get "unexpected keyword argument" error
def my_tool(path: str) -> str:
    return path
```

### `agent.clear()` resets conversation but NOT hooks or tools

```python
agent.clear()  # clears messages, keeps tools, config, and hooks
```

### Conversation accumulates across `run()` calls

```python
await agent.run("My name is Alice")
await agent.run("What's my name?")  # LLM sees both prompts
```

### Error budget resets per `run()` call

Error budget (10 total / 3 consecutive) resets with each `agent.run()` call.

### `load_skill("files")` twice is a no-op

Skills track their loaded state by name. Duplicate loading is safe.

### Skill prompt sections are appended, never replaced

No way to remove a skill's prompt section without recreating the Agent.
