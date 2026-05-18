# CONTEXT.md — mid-harness

## Canonical Terms

### Skill (broadened)

A packaged bundle that extends the Agent and its environment. A Skill contains:

- **Skill Capabilities** — what the Agent gains: tools, prompt augmentations, references (context documents), hooks, and MCP server connections. These are loaded into the Agent's runtime.
- **Skill Resources** — what ships alongside the Skill but is not loaded into the Agent: executable scripts and static assets (templates, icons, configs). These are available to the Agent via auto-generated tools (`run_skill_script`, `read_skill_asset`, `render_template`) but are not injected into context by default.

**Not to be confused with**: individual Tools (a Skill contains multiple Tools), MCP servers (a Skill may declare them, but the MCPManager spawns and manages them), or the Agent itself (which hosts Skills).

### Progressive Skill

A Skill packaged as a directory with `skill.md` (YAML frontmatter + markdown docs), `__init__.py` (backward-compatible `register(agent)`), and optional `references/`, `scripts/`, `assets/` subdirectories. Contrast with a **legacy skill** (a single Python file with `register(agent)`).

**Prompt generation**: The markdown body of `skill.md` (below the YAML frontmatter) becomes the Skill's prompt section by default. If a `prompt:` field is declared in the YAML frontmatter, it overrides the markdown body for prompt injection. This allows the same `skill.md` to serve as both human documentation and agent instruction, with an escape hatch for skills that need different content for each audience.

**Not to be confused with**: a Python package (which may or may not be a Skill — a Skill is specifically a package with a `register(agent)` function).

### Hook

A lifecycle interceptor registered at a named hook point (e.g., `tool:pre_execute`, `llm:pre_call`). Hooks have a type — guard, pre, post, or wrap — that determines what they can do: guard hooks can abort, pre-hooks can modify input, post-hooks can observe output, wrap hooks can replace an operation entirely.

**HookContext.data is shallow-copied on entry.** Hooks must use `ctx.modify(key, value)` to persist changes. Direct mutations to `ctx.data` values are detected at hook chain exit and logged as warnings — the mutation is not propagated to the operation. This prevents silent state corruption from accidental dict/list mutations.

**Not to be confused with**: EventBus subscribers (passive observers for streaming UX), or tool handlers (which execute, not intercept).

### Hook Point

A named lifecycle event where hooks can be registered. 12 active hook points in Wave 1: `agent:init`, `session:start`, `skill:load`, `loop:start`, `loop:iteration`, `loop:complete`, `llm:pre_call`, `llm:post_call`, `llm:error`, `tool:pre_execute`, `tool:post_execute`, `tool:error`.

**Reserved for future waves**: `agent:shutdown`, `session:end`, `skill:unload` — defined in design but not yet triggerable (no shutdown/close API, no session-end trigger, no skill unload mechanism).

**Tiebreaker**: When multiple hooks share the same type and priority, user hooks (`agent.hook()`) run before Skill hooks, and within the same source, hooks run in registration order (FIFO).

**Not to be confused with**: StreamEvent types (which are for UX streaming, not lifecycle control).

### MCP Server

An external process or HTTP service that exposes tools, resources, and prompts via the Model Context Protocol (JSON-RPC 2.0). The MCPManager spawns/connects to MCP servers, discovers their tools, and registers proxy handlers in the ToolRegistry.

**Not to be confused with**: the Agent's ToolRegistry (which holds all tools, both Python-native and MCP-proxied), or the Skill that declared the MCP server (Skills own the declaration; MCPManager owns the lifecycle).

**Lifecycle ownership**: MCP servers are owned by the Agent (via MCPManager), not by Skills. Skills declare requirements; the Agent provides. If two Skills declare the same MCP server, a single instance is shared (ref-counted). Unloading a Skill decrements the ref-count; the server shuts down only when no Skills need it. This keeps Skills declarative and gives the Agent full control over process resources.

### MCP Proxy Tool

A ToolDef + handler wrapper that delegates calls to an MCP server. The Agent loop treats MCP proxy tools identically to Python-native tools — it calls `handler(args)` and receives a `str` result.

**Not to be confused with**: the MCP tool definition on the server side (the proxy translates between mid-harness ToolDef and MCP tool schema).

### Tool Name Collision

mid-harness enforces strict tool name ownership:

- **Skill-to-skill collision**: If two Skills register the same tool name, the second load fails with a `SkillLoadError` (wrapping a `ToolCollisionError` from the ToolRegistry). No silent overwrite.
- **Reserved names**: Auto-generated tools (`read_reference`, `run_skill_script`, `read_skill_asset`, `render_template`) are reserved. Skills cannot register tools with these names.
- **MCP namespace**: Tool names matching the pattern `{server-name}.*` are reserved for MCP proxy tools. A Skill cannot register a Python tool that impersonates an MCP-proxied tool.

**Not to be confused with**: the legacy tiny-harness behavior (silent overwrite), or Python module name collisions (which are handled by import resolution separately).

### Reference

A context document bundled with a Skill in `references/`. References are loaded into the Agent's context (available via `read_reference` tool) but are NOT injected into the system prompt. This keeps the prompt lean while giving the Agent domain knowledge on demand.

**Boundary rule**: Use a Prompt section when the Agent must follow the rule on every call (behavioral constraints, safety rules, tool usage guidelines — typically hundreds of tokens). Use a Reference when the Agent should look up the knowledge on demand (domain knowledge, API docs, examples, schema references — typically thousands of tokens). Use neither when the content is for the human user (README, install guide).

**Not to be confused with**: Prompt sections (which ARE injected into the system prompt via `agent.prompt.append()`), or assets (static templates/configs, not context documents).

### Guard Hook

A hook of type `guard` that runs before all other hooks at a hook point. Guard hooks have a single job: allow or deny. They cannot modify data. They are the security boundary — if any guard hook aborts, the operation is blocked.

**Not to be confused with**: FilesystemGuard (which enforces workspace boundaries for file operations — a different mechanism with a similar name).

### Wrap Hook

A hook of type `wrap` that replaces an entire operation. The wrap hook receives the original operation as a callable and decides whether to call it, modify its inputs, or skip it entirely. Only one wrap hook per hook point (the outermost wins).

**Not to be confused with**: pre-hooks (which modify input but don't control whether the operation runs), or decorators (wrap hooks are registered via HookSystem, not Python syntax).

---

## Inherited Terms (from tiny-harness)

These terms retain their tiny-harness definitions unchanged:

| Term | Source | Definition |
|---|---|---|
| Agent | tiny | The complete runtime. Created, given tasks, discarded. |
| Agent Core | tiny | Loop + Messages + Config + LLMProvider + empty ToolRegistry. |
| Tool | tiny | External plugin registered into ToolRegistry. `(args: dict) -> str`. |
| Prompt | tiny | First-class artifact, separate from Config. `Prompt("base").append("section")`. |
| Conversation | tiny | The message array — the Agent's memory. |
| Session | tiny | Multiple `run()` calls sharing one Conversation. |
| Workspace | tiny | Root directory bounding filesystem authority. |
| ToolDef | tiny | Name, description, JSON Schema parameters, risk level. |
| ToolRegistry | tiny | `{name → (ToolDef, handler)}` — extended in mid to include MCP proxy handlers. |
| ToolExecutor | tiny | Validate → guard → execute → format → result. |
| EventBus | tiny | Passive streaming event emission. Retained for UX; HookSystem is separate. |
| FilesystemGuard | tiny | Workspace boundary enforcement. Not related to guard hooks. |
