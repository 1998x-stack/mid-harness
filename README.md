# mid-harness

**Extensible AI agent harness — hooks, MCP, and progressive skills on top of tiny-harness.**

> 中文一句话：在 tiny-harness 之上加入「生命周期 Hook、MCP 客户端、渐进式 Skills」三大子系统的可扩展执行环境（Generation 2）。约 3,000 行，让循环从「可用」进化为「可扩展」。

[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-212%20passed-brightgreen)](.)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

`mid-harness` 是 1998x-stack harness 家族的**第二代（Generation 2）**。沿袭 tiny-harness 的哲学——「agent harness 是一个记账良好的 while 循环」，在不增加任何智能的前提下，为循环注入三套基础设施能力：

1. **Hook System** —— 生命周期钩子（pre / post / guard / wrap），在 15 个 hook 点拦截、修改、观察 agent 运行
2. **MCP Integration** —— Model Context Protocol 客户端，连接任意语言编写的远程工具服务器（stdio + HTTP）
3. **Progressive Skills** —— skills 升级为富目录结构：`skill.md`（YAML frontmatter）+ `references/` + `scripts/` + `assets/`

> 三代哲学：`Gen1 让循环跑起来`（tiny）→ `Gen2 让循环可扩展`（mid）→ `Gen3 让循环可分发`（mega）。

## Quick Start（快速开始）

> 中文要点：CLI 与 tiny-harness 一致；默认 provider 为 deepseek、默认技能 files；`--tui` 可开 Rich 面板界面。

```bash
pip install mid-harness
export DEEPSEEK_API_KEY="sk-..."
mid-harness "Create hello.py"                      # 单次一次性执行
mid-harness --model claude-sonnet-4-20250514 --skills files   # 交互式会话
mid-harness --tui "解释这段代码"                     # Rich 可视化 TUI
```

```bash
# CLI 参数（来自 cli.py，实参验证）
mid-harness --model deepseek-chat --workspace . \
  --provider deepseek --api-base-url https://api.deepseek.com/v1 \
  --max-iterations 25 --skills files --api-key-env DEEPSEEK_API_KEY
```

## Core API（核心 API）

> 中文要点：在 tiny 的 `Agent / Prompt / Config / ToolDef` 之上，导出新增的 `HookSystem / HookContext / HookRegistration`。

```python
from mid_harness import Agent, Prompt, Config, HookSystem

agent = Agent(
    prompt=Prompt("You are a helpful assistant."),
    config=Config(model="deepseek-chat", api_key="...", workspace="."),
)

# 注册一个 loop 钩子：每次迭代前打印
async def on_iteration(ctx):
    print(f"[iteration] loop.iter = {ctx.data.get('iter')}")

agent.hooks = HookSystem()
agent.hooks.register("loop:iteration", on_iteration, hook_type="pre")
```

## Hook System（钩子系统）

- **12 个 hook 点**（`VALID_HOOK_POINTS`）：`agent:init` / `session:start` / `skill:load` / `loop:*` / `llm:*` / `tool:*`
- **4 种类型**（`ALLOWED_TYPES` 约束每点的可用类型）：
  - `guard` — 拦截并可 `abort(reason)` 中止执行（如 `tool:pre_execute`）
  - `pre` — 执行前观察/修改
  - `post` — 执行后观察
  - `wrap` — 包装原处理器
- **`HookContext`**：提供 `abort()` / `modify()` / `clone()` / `finalize()`/`is_aborted()` 等，实现链路内数据传递与中止语义
- **`HookSystem`**：`register()` / `unregister()` / `emit_pre()` / `emit_guard()` / `emit_post()` / `get_wrap()` / `list_hooks()`

设计细节见 `docs/hooks.md` 与 `docs/adr/0002-hook-system.md`。

## MCP Integration

通过 JSON-RPC 2.0 对接任意语言编写的 MCP server：

```
┌──────────────┐   JSON-RPC 2.0   ┌──────────────┐
│ MCP Client   │ ◄──────────────► │ MCP Server   │
│ (mid-harness)│  tools/list,call │ (any lang)   │
└──────────────┘                  └──────────────┘
```

- **stdio** 与 **HTTP（SSE）** 两种传输
- server 可暴露：`Tools`（模型可执行的函数，JSON Schema 参数）、`Resources`（可读数据）、`Prompts`（模板）

见 `docs/mcp.md` 与 `docs/adr/0003-mcp-integration.md`。

## Progressive Skills

Skills 从 tiny 的「Python 模块」升级为**富目录**：

```text
my-skill/
├── skill.md        # YAML frontmatter + 说明（元数据/入口）
├── references/     # 每技能独立上下文文档
├── scripts/        # 技能脚本/模板
└── assets/         # 静态资源
```

兼容 tiny 的旧格式。见 `docs/skills.md` 与 `docs/adr/0001-progressive-skills.md`。

## 与 tiny-harness 对比（Align with tiny）

| Feature | tiny-harness | mid-harness |
|---|---|---|
| Agent loop | while-loop 状态机 | 相同 + 钩子注入 |
| Tools | Python handlers | Python + MCP 代理 handlers |
| Skills | Python 模块 | 渐进式目录 + 旧版兼容 |
| Events | 被动 EventBus | EventBus + 主动 HookSystem |
| MCP | — | stdio + HTTP 客户端 |
| Hooks | — | 12 点 / 4 类型 |
| 代码规模 | ~1,100 | ~3,000 |

## The Harness Lineage（本系定位）

| 成员 | 阶段 | 一句话 |
|------|------|--------|
| `tiny-harness` | Gen 1 · 循环可用 | 最小基线：loop + tools + CLI |
| **mid-harness** | Gen 2 · 循环可扩展 | 加 hooks / MCP / 渐进式 skills |
| `effective-harness` | 生产 wrapper | 零配置实现 Anthropic twelve-factor agent |
| `mega-harness` | Gen 3 · 循环可分发（规划） | 更大全家桶 |

## Package Layout

```
mid_harness/
├── __init__.py          # 公共 API：Agent, Prompt, Config, ToolDef,
│                        #   HookSystem, HookContext, HookRegistration
├── _core.py             # Agent 编排、会话管理
├── _hooks.py            # HookSystem / HookContext（302 行）
├── _llm.py / _loop.py / _messages.py / _tools.py / _config.py
├── _guard.py            # FilesystemGuard 工作区边界
├── _persist.py / _events.py / tui.py / cli.py
├── tools/{files,shell,search}.py   # 内建工具
└── skills/{files,shell,search}.py  # 内建技能
```

## Development（开发）

```bash
git clone https://github.com/1998x-stack/mid-harness.git
cd mid-harness && pip install -e ".[dev]"
pytest tests/ --ignore=tests/test_integration.py   # 单元测试
# 集成测试需 API key
```

## Documentation

- [docs/architecture.md](docs/architecture.md)、[docs/evolution.md](docs/evolution.md)（tiny→mid→mega 演进）
- [docs/hooks.md](docs/hooks.md)、[docs/mcp.md](docs/mcp.md)、[docs/skills.md](docs/skills.md)
- [docs/adr/](docs/adr/) — 3 个架构决策记录（hooks / MCP / skills）
- [docs/gotchas.md](docs/gotchas.md)、[CONTEXT.md](CONTEXT.md)

## License

MIT