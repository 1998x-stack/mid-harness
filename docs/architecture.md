# mid-harness Architecture

**Evolution from tiny-harness: adding hooks, MCP, and progressive skills.**

## 1. Position in the Harness Family

```
tiny-harness (1,100 lines)     mid-harness (target: ~3,000 lines)     mega-harness (future)
─────────────────────────────  ─────────────────────────────────────  ──────────────────────
• Core agent loop              • Everything tiny has, plus:           • Distributed agents
• Tool system (JSON Schema)    • Lifecycle hook system                • Skill marketplace
• Basic skills (Python mods)   • MCP client integration               • Agent-to-agent comms
• 3 built-in skills            • Progressive skills (dirs + YAML)     • Multi-workspace
• Streaming events             • Skill references/scripts/assets      • Observability dash
• Single process               • Multi-process MCP servers            • Plugin ecosystem
• EventBus (passive)           • Active hooks (modify/abort/wrap)     • Sandboxed execution
```

mid-harness is **not** a rewrite. It extends tiny-harness's architecture with three new subsystems while preserving the core loop, tool system, and provider model.

## 2. Component Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                           mid-harness Agent                          │
│                                                                      │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────────┐  │
│  │  HookSystem  │  │  MCPManager  │  │  ProgressiveSkillLoader   │  │
│  │  (NEW)       │  │  (NEW)       │  │  (NEW)                    │  │
│  │              │  │              │  │                           │  │
│  │ • pre-hooks  │  │ • spawn svrs │  │ • parse skill.md YAML     │  │
│  │ • post-hooks │  │ • proxy tools│  │ • check deps/conflicts    │  │
│  │ • guard-hooks│  │ • reconnect  │  │ • load references/        │  │
│  │ • wrap-hooks │  │ • lifecycle  │  │ • expose scripts/assets   │  │
│  └──────┬───────┘  └──────┬───────┘  │ • load MCP if declared    │  │
│         │                 │           └────────────┬──────────────┘  │
│         │                 │                        │                 │
│         ▼                 ▼                        ▼                 │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                    Agent Core (from tiny-harness)              │   │
│  │                                                                │   │
│  │  ┌─────────────┐  ┌──────────────┐  ┌────────────────────┐   │   │
│  │  │ AgentLoop   │  │ ToolRegistry │  │ MessageManager     │   │   │
│  │  │ (extended)  │  │ (extended)   │  │                    │   │   │
│  │  │             │  │              │  │ • conversation[]   │   │   │
│  │  │ while-loop  │  │ • Python     │  │ • token counting   │   │   │
│  │  │ + hook      │  │   handlers   │  │ • context refs     │   │   │
│  │  │   injection  │  │ • MCP proxy  │  │   (from skills)   │   │   │
│  │  │   points     │  │   handlers   │  │                    │   │   │
│  │  └──────┬───────┘  └──────┬───────┘  └────────────────────┘   │   │
│  │         │                 │                                     │   │
│  │         ▼                 ▼                                     │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌────────────────────┐   │   │
│  │  │ LLMProvider  │  │ ToolExecutor │  │ FilesystemGuard    │   │   │
│  │  │              │  │              │  │                    │   │   │
│  │  │ • Anthropic  │  │ • validate   │  │ • workspace bounds │   │   │
│  │  │ • OpenAI     │  │ • guard      │  │ • path traversal   │   │   │
│  │  │ • DeepSeek   │  │ • execute    │  │ • risk levels      │   │   │
│  │  │ • MCP-aware  │  │ • format     │  │                    │   │   │
│  │  └──────────────┘  └──────────────┘  └────────────────────┘   │   │
│  │                                                                │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌────────────────────┐   │   │
│  │  │ EventBus     │  │ Config       │  │ Prompt             │   │   │
│  │  │              │  │              │  │                    │   │   │
│  │  │ • emit       │  │ • model      │  │ • sections[]       │   │   │
│  │  │ • subscribe  │  │ • max_iter   │  │ • references[]     │   │   │
│  │  └──────────────┘  └──────────────┘  └────────────────────┘   │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

## 3. Three New Subsystems

### 3.1 Hook System (`_hooks.py`)

Replaces the passive EventBus with active lifecycle hooks. Hooks can **modify**, **abort**, or **wrap** operations.

```
Hook Chain:  pre-hook₁ → pre-hook₂ → [OPERATION] → post-hook₁ → post-hook₂
                  │            │                          │            │
                  │            └─ can modify args         │            └─ can observe result
                  └─ can abort operation                  └─ can transform output
```

**Hook points** (12 lifecycle events):
| Phase | Hook Point | Type | Can Modify | Can Abort |
|---|---|---|---|---|
| Agent | `agent:init` | post | — | — |
| Agent | `agent:shutdown` | post | — | — |
| Session | `session:start` | post | — | — |
| Session | `session:end` | post | — | — |
| Skill | `skill:load` | post | — | — |
| Skill | `skill:unload` | pre | — | ✗ |
| Loop | `loop:start` | pre/post | messages | ✗ |
| Loop | `loop:iteration` | pre/post | — | ✗ (loop exit) |
| Loop | `loop:complete` | post | — | — |
| LLM | `llm:pre_call` | pre | messages, tools | ✗ |
| LLM | `llm:post_call` | post | response | — |
| LLM | `llm:error` | post | — | — |
| Tool | `tool:pre_execute` | pre | args | ✗ |
| Tool | `tool:post_execute` | post | result | — |
| Tool | `tool:error` | post | — | — |

See `docs/hooks.md` for full design.

### 3.2 MCP Integration (`_mcp.py`)

Model Context Protocol client for connecting to external tool servers. MCP servers run as subprocesses or HTTP servers, exposing tools/resources/prompts to the agent.

**Transport support:**
- **stdio**: Spawn a child process, communicate via stdin/stdout JSON-RPC
- **HTTP**: Connect to an HTTP server with SSE streaming

**Proxy pattern**: MCP tools are wrapped as native `ToolDef` objects so the agent loop treats them identically to Python tools.

```
ToolRegistry
├── read_file          (Python handler)
├── write_file         (Python handler)
├── git_log            (MCP proxy → git-server process)
├── git_commit         (MCP proxy → git-server process)
└── query_db           (MCP proxy → postgres-server process)
```

See `docs/mcp.md` for full design.

### 3.3 Progressive Skills (`_skills.py`)

Skills evolve from single Python files to rich directories with structured metadata, references, scripts, and assets.

**Skill directory structure:**
```
skills/git/
├── skill.md           ← YAML frontmatter + markdown docs (manifest)
├── __init__.py        ← register(agent) function (backward compat)
├── references/        ← Context docs loaded into agent
│   ├── api.md
│   └── patterns.md
├── scripts/           ← Executable scripts exposed to agent
│   └── setup.sh
├── assets/            ← Static resources exposed to agent
│   ├── templates/
│   └── prompts/
└── tools/             ← Tool implementations (optional)
    └── handlers.py
```

**skill.md YAML frontmatter** serves as the skill manifest: declares tools, MCP servers, hooks, dependencies, and metadata in a single parseable header.

See `docs/skills.md` for full design.

## 4. Agent Loop with Hooks Integrated

The extended agent loop with hook injection points:

```python
async def run(self, prompt: str) -> str:
    await self._hooks.emit("session:start", {...})
    await self._hooks.emit("loop:start", {"messages": messages, "tools": self._tools.get_definitions()})

    for iteration in range(self._config.max_iterations):
        await self._hooks.emit("loop:iteration", {"num": iteration, "messages": messages})

        # --- LLM call (with pre/post hooks) ---
        llm_context = await self._hooks.emit_pre("llm:pre_call", {
            "messages": messages,
            "tools": self._tools.get_definitions()
        })
        messages = llm_context.get("messages", messages)

        response = await self._llm.generate(messages, self._tools.get_definitions())

        await self._hooks.emit("llm:post_call", {
            "response": response,
            "messages": messages
        })

        # --- Tool execution (with pre/post hooks) ---
        if response.text and not response.tool_calls:
            break  # final answer

        for tc in response.tool_calls:
            tool_context = await self._hooks.emit_pre("tool:pre_execute", {
                "name": tc.name,
                "args": tc.arguments,
                "call_id": tc.id
            })
            if tool_context.get("aborted"):
                result = ToolResult.error(tc.id, tool_context["abort_reason"])
            else:
                result = await self._tools.execute(
                    tool_context.get("name", tc.name),
                    tool_context.get("args", tc.arguments),
                    tc.id
                )

            result = await self._hooks.emit_post("tool:post_execute", {
                "result": result,
                "name": tc.name
            })
            messages.append(tool_result(tc.id, result))

    await self._hooks.emit("loop:complete", {"result": response.text})
    await self._hooks.emit("session:end", {...})
    return response.text
```

## 5. Skill Loading Flow

```
agent.load_skill("git")
  │
  ├─ 1. Resolve skill path
  │     • tiny_harness.skills.git  (built-in, backward compat)
  │     • ./skills/git/             (local directory)
  │     • mid_harness_skill_git     (installed package)
  │
  ├─ 2. If directory → ProgressiveSkillLoader
  │     ├─ Parse skill.md YAML frontmatter
  │     ├─ Validate dependencies/conflicts
  │     ├─ Spawn MCP servers (if mcp_servers declared)
  │     ├─ Import __init__.py → call register(agent)
  │     ├─ Load references/ → agent.context.add_reference(...)
  │     ├─ Register hooks (if hooks declared)
  │     └─ Expose scripts/ and assets/ via tools
  │
  ├─ 3. If Python file → backward-compat load
  │     └─ import → module.register(agent)
  │
  └─ 4. Track in _loaded_skills
```

## 6. Package Layout

```
mid_harness/
├── __init__.py           # Public API: Agent, Prompt, Config, ToolDef, Hook
├── _core.py              # Agent class (extended: hooks, MCP, progressive skills)
├── _loop.py              # AgentLoop (extended: hook injection points)
├── _llm.py               # LLMProvider ABC + Anthropic/OpenAI/DeepSeek
├── _tools.py             # ToolRegistry + ToolExecutor + MCP proxy tools
├── _messages.py          # MessageManager + ContextReferences
├── _config.py            # AgentConfig + Prompt + SkillConfig
├── _events.py            # StreamEvent + EventBus (retained for streaming)
├── _guard.py             # FilesystemGuard
├── _hooks.py             # HookSystem, HookContext, HookChain (NEW)
├── _mcp.py               # MCPManager, MCPServer, MCPToolProxy (NEW)
├── _skills.py            # ProgressiveSkillLoader, SkillManifest (NEW)
├── _persist.py           # SessionStore
├── cli.py                # CLI (extended: --hooks, --mcp-config)
├── tui.py                # Rich TUI (extended: hook status indicators)
├── tools/
│   ├── files.py
│   ├── shell.py
│   └── search.py
└── skills/               # Progressive skill directories
    ├── files/
    │   ├── skill.md
    │   ├── __init__.py
    │   ├── references/
    │   ├── scripts/
    │   └── assets/
    ├── shell/
    │   └── ...
    └── search/
        └── ...
```

## 7. Design Principles (inherited and extended)

From tiny-harness:
- **Hooks = no reasoning**: Hooks are mechanical interceptors — they validate, transform, or observe. The LLM does the reasoning.
- **Tools are tools**: MCP tools and Python tools are interchangeable. The agent loop doesn't care.
- **Skills are self-contained**: A skill directory contains everything the skill needs. No implicit dependencies.
- **Backward compatibility**: All tiny-harness skill modules (`register(agent)` pattern) continue to work unchanged.

New for mid-harness:
- **Hooks are explicit**: No magic hook registration. Skills declare hooks in `skill.md`. Users register hooks via `agent.hook(...)`.
- **MCP is transparent**: The agent doesn't know which tools are local and which are remote.
- **Progressive loading**: Skills load in steps — manifest → tools → MCP → hooks → references. Each step can fail gracefully.
- **Context enrichment**: `references/` documents are loaded into agent context, giving skills domain knowledge without bloating the system prompt.

## 8. Comparison: tiny vs mid

| Feature | tiny-harness | mid-harness |
|---|---|---|
| Agent loop | while-loop state machine | same + hook integration |
| Tools | JSON Schema, Python handlers | same + MCP proxy handlers |
| Skills | Python module, `register(agent)` | same + progressive dirs + skill.md |
| Events | Passive StreamEvent emission | same + active HookSystem |
| MCP | — | stdio + HTTP client, auto-registration |
| Skill manifest | Optional `SKILL_META` dict | Required `skill.md` YAML frontmatter |
| References | — | `references/` loaded as agent context |
| Scripts | — | `scripts/` exposed via tool |
| Assets | — | `assets/` exposed via tool |
| Hook system | — | 12 lifecycle hook points, pre/post/guard/wrap |
| Skill loading | import → register | parse YAML → spawn MCP → register → load refs → hooks |
| Providers | Anthropic, OpenAI, DeepSeek | same |
| CLI | one-shot + REPL | same + hook status, MCP health |
| Lines of code | ~1,100 | ~3,000 (target) |
