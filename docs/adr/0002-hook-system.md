# ADR 002: Hook System as Separate Subsystem

**Date**: 2026-05-16
**Status**: Proposed

## Context

tiny-harness has an EventBus for streaming events: `text_delta`, `tool_start`, `tool_end`, `iteration`, `error`. Handlers observe these events but cannot intervene.

For mid-harness, we need:
- **Pre-execution intervention**: Validate or modify tool args before execution
- **Operation abort**: Block dangerous operations
- **Result transformation**: Modify tool results before they reach the LLM
- **Cross-cutting concerns**: Token tracking, auditing, caching — without modifying core loop code

Two approaches considered:

**Model A — Extend EventBus**: Add `abort()` and `modify()` to the existing EventBus. Pre/post events fire at each lifecycle point. One unified event system.

**Model B — Separate HookSystem**: A new subsystem with explicit hook types (guard/pre/post/wrap), priority ordering, and a HookContext API. EventBus remains for streaming UX only.

## Decision

**Model B — Separate HookSystem.** Hooks are a lifecycle control mechanism with active intervention capabilities. Events are a passive observation mechanism for streaming. Different purposes, different APIs.

## Rationale

1. **Separation of concerns**: EventBus is for UX (streaming text, tool call indicators). HookSystem is for control flow (validation, transformation, abort). Mixing them creates confusion about which handlers can modify state and which are read-only.

2. **Rich hook context**: `HookContext` provides `abort()`, `modify()`, access to the agent, and event-specific data. EventBus provides a frozen `StreamEvent` dataclass. Adding `abort()` to `StreamEvent` would break its immutable, fire-and-forget design.

3. **Priority ordering**: Hooks need deterministic execution order (guards before transforms before observers). EventBus subscribers should be order-independent. Adding priority to EventBus adds complexity without benefit.

4. **Hook types are contracts**: Guard hooks guarantee they run first and can only abort. Pre-hooks can modify but not abort after guards pass. Post-hooks can only observe. These contracts make hook behavior predictable and auditable.

5. **Explicit over implicit**: `agent.hook("tool:pre_execute", handler)` makes it clear this is a lifecycle hook with intervention capabilities. `agent.on("tool_start", handler)` makes it clear this is an observer.

## Consequences

- Two event systems exist: EventBus (streaming UX) and HookSystem (lifecycle control). They serve different purposes and should not be confused.
- Hooks must be registered with `agent.hook()`, not `agent.on()`. Different APIs for different concerns.
- Skills declare hooks in `skill.md` under `hooks:`, not as event subscriptions.
- The agent loop has explicit hook injection points. Each hook point has defined types (guard/pre/post/wrap) that constrain what handlers can do.
- Hook handlers are async by default; sync handlers are wrapped.

## Alternatives Considered

### A. Unify EventBus and HookSystem
One system for everything. Events have `can_abort` and `can_modify` flags. Rejected because the mental model is confusing: "is this an event I observe or a hook I control?" The answer depends on context, not the API.

### B. Decorator-only hooks
Hooks registered only via decorators on tool handlers. Rejected because it couples hook logic to tool implementations. Cross-cutting hooks (auditing, token tracking) shouldn't live inside tool code.

### C. Middleware pattern (like Express/WSGI)
A single middleware chain for all requests. Rejected because different hook points have different context data — a `tool:pre_execute` context has tool args; an `llm:pre_call` context has messages. A unified middleware chain would need complex type discrimination.

### D. Plugin system
Full plugin architecture with enable/disable, configuration, and discovery. Rejected as over-engineered for mid-harness. Hooks provide the needed extensibility without the plugin lifecycle complexity.
