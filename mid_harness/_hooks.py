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


def _validate_handler_signature(handler: Callable) -> None:
    """Validate that a hook handler has a compatible signature.

    Hook handlers must accept exactly one positional argument (the HookContext).
    Common mistakes like accepting zero params, or naming the param 'args'/'event',
    are caught at registration time rather than silently failing at runtime.
    """
    try:
        sig = inspect.signature(handler)
    except (ValueError, TypeError):
        return  # built-ins like print, or C extensions — skip check

    params = list(sig.parameters.values())

    # Filter out *args, **kwargs, and default-only params from positional count
    positional = [
        p
        for p in params
        if p.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]

    has_varargs = any(
        p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
        for p in params
    )

    if has_varargs:
        return  # *args or **kwargs — accept any number of arguments, skip check

    if len(positional) == 0:
        raise ValueError(
            f"Hook handler must accept at least one positional argument (HookContext). "
            f"Got: {_format_sig(sig)}"
        )

    first_param = positional[0].name
    if first_param in ("args", "event", "data", "payload"):
        logger.warning(
            f"Hook handler parameter is named '{first_param}'. "
            f"Hook handlers receive a HookContext, not raw data. "
            f"Use 'ctx' or 'context' as the parameter name. "
            f"Signature: {_format_sig(sig)}"
        )


def _format_sig(sig: inspect.Signature) -> str:
    parts = []
    for name, p in sig.parameters.items():
        if p.kind == inspect.Parameter.VAR_POSITIONAL:
            parts.append(f"*{name}")
        elif p.kind == inspect.Parameter.VAR_KEYWORD:
            parts.append(f"**{name}")
        elif p.default is not inspect.Parameter.empty:
            parts.append(f"{name}={p.default!r}")
        else:
            parts.append(name)
    return f"({', '.join(parts)})"


# ── All valid hook point names ──
VALID_HOOK_POINTS = frozenset(
    {
        "agent:init",
        "session:start",
        "skill:load",
        "loop:start",
        "loop:iteration",
        "loop:complete",
        "llm:pre_call",
        "llm:post_call",
        "llm:error",
        "tool:pre_execute",
        "tool:post_execute",
        "tool:error",
    }
)

ALLOWED_TYPES: dict[str, set[str]] = {
    "agent:init": {"post"},
    "session:start": {"post"},
    "skill:load": {"post"},
    "loop:start": {"pre", "post"},
    "loop:iteration": {"pre", "post"},
    "loop:complete": {"post"},
    "llm:pre_call": {"pre"},
    "llm:post_call": {"post"},
    "llm:error": {"post"},
    "tool:pre_execute": {"guard", "pre"},
    "tool:post_execute": {"post"},
    "tool:error": {"post"},
}


@dataclass
class HookRegistration:
    """A single registered hook with metadata for ordering."""

    name: str
    handler: Callable  # async (HookContext) -> None
    priority: int
    source: str  # "user" | "skill:{name}"
    insertion_order: int = 0  # set by HookSystem for FIFO tiebreaking
    source_rank: int = field(init=False)

    def __post_init__(self):
        self.source_rank = 0 if self.source == "user" else 1

    @property
    def sort_key(self) -> tuple:
        return (-self.priority, self.source_rank, self.insertion_order)

    def __lt__(self, other: "HookRegistration") -> bool:
        return self.sort_key < other.sort_key


class HookContext:
    """Context passed to hook handlers. Data is shallow-copied on entry."""

    def __init__(self, event: str, agent, data: dict):
        self.event = event
        self.agent = agent
        self._original_data = data
        self.data = copy.copy(data)  # shallow copy — hooks get their own top-level dict
        self._aborted = False
        self._abort_reason: str | None = None
        self._modifications: set[str] = (
            set()
        )  # keys explicitly modified via ctx.modify()

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

    def clone(self, key: str) -> Any:
        """Return a deep copy of a data field for safe mutation.

        Use when you need to mutate nested structures inside a data field.
        Call clone(), mutate the copy, then pass to ctx.modify(key, copy).

        Example:
            args = ctx.clone("args")
            args["path"] = sanitize(args["path"])
            ctx.modify("args", args)
        """
        return copy.deepcopy(self.data.get(key))

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

        _validate_handler_signature(handler)

        hook_name = name or f"hook_{uuid.uuid4().hex[:8]}"
        if hook_name in self._names:
            raise ValueError(f"Hook name '{hook_name}' is already registered")

        self._insert_count += 1
        reg = HookRegistration(
            name=hook_name,
            handler=handler,
            priority=priority,
            source=source,
            insertion_order=self._insert_count,
        )

        self._hooks[event][hook_type].append(reg)
        self._hooks[event][hook_type].sort()
        self._names[hook_name] = reg
        return hook_name

    def unregister(self, name: str) -> None:
        """Remove a hook by name."""
        if name not in self._names:
            raise ValueError(f"No hook registered with name '{name}'")
        self._names.pop(name)
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
                logger.warning(
                    f"Guard hook '{reg.name}' raised {type(e).__name__}: {e}"
                )
                ctx.abort(str(e))

    async def emit_pre(self, event: str, ctx: HookContext) -> None:
        """Run all pre-hooks. Can modify data or abort."""
        for reg in self._hooks[event]["pre"]:
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
        return wraps[0].handler if wraps else None

    def list_hooks(self) -> list[dict]:
        """Return all registered hooks with metadata for debugging."""
        result = []
        for event, type_map in self._hooks.items():
            for hook_type, registrations in type_map.items():
                for reg in registrations:
                    result.append(
                        {
                            "name": reg.name,
                            "event": event,
                            "type": hook_type,
                            "priority": reg.priority,
                            "source": reg.source,
                        }
                    )
        return result
