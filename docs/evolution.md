# Evolution Path: tiny → mid → mega

**How the harness family grows across three generations.**

## 1. The Harness Philosophy

All three harnesses share the same core philosophy from tiny-harness's `thoughts.md`:

> **"The agent harness is a while-loop with good bookkeeping."**

The harness provides **mechanical infrastructure** — it does no reasoning. The LLM does all the thinking. Each generation adds infrastructure capabilities without adding intelligence.

```
Generation 1: Make the loop work          (tiny-harness)
Generation 2: Make the loop extensible    (mid-harness)
Generation 3: Make the loop distributable (mega-harness)
```

## 2. tiny-harness (Generation 1)

**Goal**: Prove the core loop. One agent, one conversation, tools, done.

| Aspect | Decision |
|---|---|
| Scope | ~1,100 lines Python |
| Tools | JSON Schema, dict-based handler: `(args: dict) -> str` |
| Skills | Python modules with `register(agent)`. Prompt append-only. |
| Events | Passive EventBus for streaming only |
| Lifecycle | No hooks. System prompt + user prompt → run → result. |
| Extensibility | Import Python modules. That's it. |
| MCP | Not in scope |
| State | Session-scoped Conversation. Discarded at session end. |
| Providers | Anthropic, OpenAI, DeepSeek |
| CLI | One-shot + REPL + Rich TUI |

**What tiny-harness teaches us:**
1. The core loop is ~10 lines. Everything else is elaboration.
2. Tool error → feed back as tool result → LLM self-corrects. No retry logic needed.
3. Prompt design is as important as code.
4. Streaming is essential even for MVP.
5. Context grows monotonically — this is the fundamental scaling problem.

**What tiny-harness is NOT:**
- NOT a multi-agent system
- NOT a framework with plugin lifecycle
- NOT remote-tool-aware
- NOT sandboxed (FilesystemGuard is basic)

## 3. mid-harness (Generation 2)

**Goal**: Extensibility and ecosystem. Hooks for lifecycle control, MCP for remote tools, progressive skills for richer capabilities.

### 3.1 Why These Three Additions?

**Hooks**: tiny-harness's EventBus is passive — you can observe but not intervene. This works for streaming UX but falls short for:
- Validating tool arguments before execution (guard hooks)
- Transforming LLM responses (post-call hooks)
- Aborting dangerous tool calls (pre-execute hooks)
- Tracking token usage across calls (post-call hooks)
- Injecting context before each iteration (pre-iteration hooks)

Hooks make the harness extensible without modifying its core.

**MCP**: tiny-harness tools are Python functions in-process. This limits:
- Language choice (must be Python)
- Process isolation (tools share the agent's process)
- Tool distribution (must install Python packages)
- Existing ecosystems (can't use MCP servers from other projects)

MCP adds a standard protocol for tool servers, letting mid-harness connect to tools written in any language, running in any process.

**Progressive Skills**: tiny-harness skills are Python files with optional `SKILL_META` dict and an implicit `register(agent)` function. This works but:
- No standard manifest (each skill invents its own metadata)
- No way to bundle non-code resources (docs, templates, scripts)
- No dependency management between skills
- No MCP server declarations

Progressive skills add structured manifests (`skill.md` with YAML frontmatter), bundled resources (`references/`, `scripts/`, `assets/`), and explicit dependency/conflict declarations.

### 3.2 What mid-harness Adds

| Feature | tiny-harness | mid-harness |
|---|---|---|
| Hook system | — | 12 lifecycle hook points |
| Hook types | — | pre, post, guard, wrap |
| MCP client | — | stdio + HTTP transport |
| Skill format | Python file | Directory with skill.md |
| Skill manifest | Optional dict | Required YAML frontmatter |
| Skill resources | — | references/, scripts/, assets/ |
| MCP in skills | — | Declared in skill.md |
| Tool sources | Python only | Python + MCP proxy |
| Hook registration | — | agent.hook() + skill.md |
| Context enrichment | — | references/ → agent context |

### 3.3 What mid-harness Does NOT Add

These are deferred to mega-harness:
- Multi-agent orchestration (sub-agent spawning)
- Agent-to-agent communication
- Distributed execution (multiple machines)
- Skill marketplace / discovery service
- Cross-session memory / vector DB integration
- Plugin hot-reload (skills require agent restart)
- Sandboxed execution (Docker-based isolation)
- Observability dashboards (Prometheus/Grafana)
- Workflow/pipeline definition
- Model routing/failover

### 3.4 Migration Path from tiny-harness

Skills from tiny-harness work unchanged in mid-harness:

```python
# tiny-harness skill — works in mid-harness without modification
def register(agent):
    agent.tools.register_from_def(ToolDef(...), handler)
    agent._prompt.append(MY_PROMPT)

# mid-harness can also load this as a progressive skill
# by adding a skill.md manifest alongside the __init__.py
```

The `load_skill()` method detects whether the target is a directory (progressive) or file (legacy) and handles both.

## 4. mega-harness (Generation 3 — Future)

**Goal**: Distribution and ecosystem. Multi-agent orchestration, skill marketplace, production observability.

### 4.1 Planned Additions

| Feature | Description |
|---|---|
| **Multi-agent orchestration** | Sub-agent spawning, parallel dispatch, agent-to-agent messaging |
| **Agent mesh** | Agents communicate via message bus, share state, coordinate tasks |
| **Skill marketplace** | Discover, install, update skills from a registry |
| **Cross-session memory** | Vector DB-backed persistent memory across sessions |
| **Distributed execution** | Agents run on different machines, coordinated by a controller |
| **Sandbox isolation** | Docker/podman-based tool execution with resource limits |
| **Observability** | Prometheus metrics, tracing, structured logging, dashboards |
| **Workflow engine** | Define agent pipelines as DAGs with retry/fallback |
| **Model routing** | Route to different models based on task complexity or cost |
| **Auth/Authz** | Skill permissions, tool access control, API key management |
| **Plugin system** | Third-party hook plugins, MCP middleware, custom transports |

### 4.2 mega-harness Architecture (Sketch)

```
┌─────────────────────────────────────────────────────────────┐
│                    mega-harness Controller                    │
│                                                               │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────┐  │
│  │ Orchestrator│  │ Message Bus  │  │ Skill Registry     │  │
│  │             │  │ (NATS/Rabbit)│  │ (marketplace)      │  │
│  │ task DAG    │  │              │  │                    │  │
│  │ dispatch    │  └──────┬───────┘  └────────────────────┘  │
│  └──────┬──────┘         │                                    │
│         │                │                                    │
│    ┌────┴────┬───────────┼───────────┬────────────┐          │
│    ▼         ▼           ▼           ▼            ▼          │
│  ┌──────┐ ┌──────┐   ┌──────┐   ┌──────┐    ┌──────┐       │
│  │Agent │ │Agent │   │Agent │   │Agent │    │Agent │       │
│  │  A   │ │  B   │   │  C   │   │  D   │    │  E   │       │
│  │      │ │      │   │      │   │      │    │      │       │
│  │code  │ │review│   │test  │   │docs  │    │deploy│       │
│  └──────┘ └──────┘   └──────┘   └──────┘    └──────┘       │
│                                                               │
│  ┌──────────────────────────────────────────────────────┐    │
│  │              Shared Services                          │    │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────────────┐   │    │
│  │  │ Memory   │  │ Sandbox  │  │ Observability    │   │    │
│  │  │ (Vector  │  │ (Docker) │  │ (Prometheus/     │   │    │
│  │  │  DB)     │  │          │  │  Grafana)        │   │    │
│  │  └──────────┘  └──────────┘  └──────────────────┘   │    │
│  └──────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

### 4.3 When to Graduate from mid to mega

You need mega-harness when:
- A single agent's context window overflows regularly
- You need parallel task execution across multiple agents
- You're building a platform that others will extend with plugins
- You need production observability (metrics, tracing, alerting)
- You need sandboxed execution for security-sensitive workloads
- You're distributing agents across machines

You do NOT need mega-harness when:
- One agent with tools handles your use case
- You're the only developer extending the system
- Your skills are all Python-based
- You don't need multi-agent coordination

## 5. Design Constraints Across Generations

These constraints hold across all three generations:

1. **The harness does no reasoning** — the LLM is the intelligence
2. **Tools are tools** — Python, MCP, or otherwise, they're `(args) -> result`
3. **Skills are self-contained** — no implicit dependencies on other skills
4. **Explicit over implicit** — nothing magic, everything traceable
5. **Functions over classes** — classes only for state + behavior bundles
6. **Backward compatibility** — each generation loads skills from previous generations

## 6. Summary

| | tiny-harness | mid-harness | mega-harness |
|---|---|---|---|
| **Lines** | ~1,100 | ~3,000 | ~8,000+ |
| **Agents** | 1 | 1 | N (mesh) |
| **Tools** | Python | Python + MCP | Python + MCP + Remote |
| **Skills** | .py files | Directories + YAML | Packages + marketplace |
| **Hooks** | — | 12 lifecycle points | 12 + workflow hooks |
| **MCP** | — | Client | Client + Server |
| **Memory** | Conversation only | Conversation + references | Vector DB + cross-session |
| **Execution** | In-process | In-process + MCP subprocess | Docker sandboxes |
| **Distribution** | — | — | Multi-machine mesh |
| **Observability** | Console logging | Structured logging | Metrics + tracing + dashboards |
| **Ecosystem** | pip install | pip + MCP servers | Marketplace + plugins |
