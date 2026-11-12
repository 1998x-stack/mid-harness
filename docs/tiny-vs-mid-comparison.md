# tiny-harness 与 mid-harness 对比分析

**mid-harness 在 tiny-harness 基础上新增了 Hook 系统、MCP 集成和渐进式技能三大子系统。**

---

## 1. 概述

tiny-harness 和 mid-harness 同属 AI Agent 基础设施家族（harness family），共享同一核心理念：

> **"The agent harness is a while-loop with good bookkeeping."**
> —— Agent 框架就是一个带良好记账的 while 循环。

两者的关系是 **演进（extension）而非重写（rewrite）**。mid-harness 直接复制（fork）了 tiny-harness 的核心源码并就地扩展，而非将其作为 pip 依赖。

| 维度 | tiny-harness | mid-harness |
|------|-------------|-------------|
| 定位 | 验证核心循环 | 可扩展生态 |
| 代号 | Generation 1 | Generation 2 |
| 哲学 | 让循环跑起来 | 让循环可扩展 |
| 代码规模 | ~1,100 行 Python | ~3,200 行 Python |
| 测试数量 | 195 | 211 |
| 发布时间 | 早期 | 2026 Q2（Wave 1） |

---

## 2. 项目关系

### 2.1 继承方式

mid-harness **不是** tiny-harness 的 pip 依赖。实现方式是将 tiny-harness 的核心源码直接复制到 `mid_harness/` 包中，在原位置扩展。这种"fork-and-extend"方式在实现计划中有明确记录：

> "Copy: `tiny_harness/_tools.py` → `mid_harness/_tools.py`"

这意味着 tiny-harness 的代码是 mid-harness 代码库中不可分割的一部分，而非通过依赖导入。

### 2.2 文件来源标识

mid-harness 源码文件头部保留了原始来源注释，清晰标记了哪些文件来自 tiny-harness：

**来自 tiny-harness（就地扩展）**：
- `_core.py` — 头部：`# tiny_harness/_core.py`
- `_tools.py` — 头部：`# tiny_harness/_tools.py`
- `_config.py` — 头部：`# tiny_harness/_config.py`
- `_events.py` — 头部：`# tiny_harness/_events.py`
- `_messages.py` — 头部：`# tiny_harness/_messages.py`
- `_llm.py` — 头部：`# tiny_harness/_llm.py`
- `_guard.py` — 头部：`# tiny_harness/_guard.py`
- `_loop.py` — 头部：`# mid_harness/_loop.py`（重写：加入 Hook 注入点）
- `tools/files.py`, `tools/shell.py`, `tools/search.py`
- `skills/files.py`, `skills/shell.py`, `skills/search.py`

**mid-harness 全新文件**：
- `_hooks.py` — HookSystem + HookContext
- `_mcp.py` — MCPManager + MCPServer（设计中）
- `_persist.py` — SessionStore（JSONL 会话持久化）

---

## 3. 架构对比

### 3.1 整体架构

```
tiny-harness:                    mid-harness:
─────────────────                ─────────────────────────────
Agent                             Agent
  ├── AgentLoop                     ├── HookSystem   ★ NEW
  ├── ToolRegistry                  ├── MCPManager   ★ NEW
  ├── ToolExecutor                  ├── SkillLoader  ★ NEW
  ├── LLMProvider                   ├── AgentLoop (extended: hook injection)
  ├── MessageManager                ├── ToolRegistry (extended: MCP proxy)
  ├── EventBus                      ├── ToolExecutor
  ├── FilesystemGuard               ├── LLMProvider (same)
  ├── Config                        ├── MessageManager (same)
  └── Prompt                        ├── EventBus (retained for streaming UX)
                                    ├── FilesystemGuard (same)
                                    ├── Config (same)
                                    ├── Prompt (same)
                                    └── SessionStore (new)
```

### 3.2 组件层级

mid-harness 的架构可以理解为三层结构：

```
┌──────────────────────────────────────────────────────┐
│                 mid-harness Agent                      │
│                                                        │
│  ┌─────────────────┐  ┌──────────────┐  ┌───────────┐ │
│  │   HookSystem    │  │  MCPManager  │  │SkillLoader│  │
│  │   (NEW)         │  │  (NEW)       │  │  (NEW)    │  │
│  └────────┬────────┘  └──────┬───────┘  └─────┬─────┘ │
│           │                  │                 │       │
│           ▼                  ▼                 ▼       │
│  ┌───────────────────────────────────────────────┐    │
│  │          Agent Core (from tiny-harness)        │    │
│  │                                                │    │
│  │  • AgentLoop (extended)                        │    │
│  │  • ToolRegistry (extended: MCP proxy)          │    │
│  │  • ToolExecutor (validate → guard → execute)   │    │
│  │  • LLMProvider (Anthropic/OpenAI/DeepSeek)     │    │
│  │  • MessageManager (conversation state)         │    │
│  │  • EventBus (streaming UX)                     │    │
│  │  • FilesystemGuard (workspace boundary)        │    │
│  └───────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────┘
```

---

## 4. 核心子系统对比

### 4.1 事件系统：EventBus → EventBus + HookSystem

tiny-harness 只有被动的 EventBus，用于流式 UI 输出：

```python
# tiny-harness: 只能被动观察
agent.on("tool_start", lambda e: print(f"Calling {e.tool_name}"))
# 无法验证参数、无法中止执行、无法转换结果
```

mid-harness 保留 EventBus（流式 UI）的同时，新增 HookSystem（主动拦截）：

```python
# mid-harness: 主动拦截，可以修改/中止/包裹操作
agent.hook("tool:pre_execute", hook_type="guard", priority=100,
    handler=lambda ctx: ctx.abort("危险操作被阻止"))
```

| 能力 | tiny-harness EventBus | mid-harness HookSystem |
|------|----------------------|------------------------|
| 观察事件 | ✓ | ✓ |
| 修改输入数据 | ✗ | ✓（pre-hook） |
| 中止操作 | ✗ | ✓（guard/pre-hook） |
| 包裹替换操作 | ✗ | ✓（wrap-hook，延迟） |
| 转换输出结果 | ✗ | ✓（post-hook） |
| 流式 UI 输出 | ✓ | ✓（保留 EventBus） |

### 4.2 Hook 生命周期覆盖

mid-harness 定义了 12 个 Hook 注入点，覆盖 Agent 完整生命周期：

| 阶段 | Hook 点 | 类型 | 可修改 | 可中止 |
|------|---------|------|--------|--------|
| Agent 初始化 | `agent:init` | post | — | — |
| 会话 | `session:start` | post | — | — |
| 技能加载 | `skill:load` | post | — | — |
| 循环开始 | `loop:start` | pre/post | messages | ✗ |
| 循环迭代 | `loop:iteration` | pre/post | — | ✗（退出循环）|
| 循环完成 | `loop:complete` | post | — | — |
| LLM 调用前 | `llm:pre_call` | pre | messages, tools | ✗ |
| LLM 调用后 | `llm:post_call` | post | response | — |
| LLM 错误 | `llm:error` | post | — | — |
| 工具执行前 | `tool:pre_execute` | guard/pre/wrap | args | ✗ |
| 工具执行后 | `tool:post_execute` | post | result | — |
| 工具错误 | `tool:error` | post | — | — |

### 4.3 工具系统：Python → Python + MCP

| 维度 | tiny-harness | mid-harness |
|------|-------------|-------------|
| 工具来源 | Python 进程内 | Python + MCP 代理 |
| 工具语言 | 仅 Python | 任意语言（通过 MCP） |
| 工具隔离 | 无（同进程） | MCP 服务器独立进程 |
| 工具分发 | pip install | pip + MCP 服务器 |
| 工具命名规范 | 无限制 | 点号保留给 MCP 代理 |
| 命名冲突处理 | 静默覆盖 | `ToolCollisionError` 异常 |
| 保留工具名 | 无 | `read_reference` 等 4 个 |

MCP 代理模式使 Agent 对工具来源无感知：

```
ToolRegistry
├── read_file          (Python 处理器)
├── write_file         (Python 处理器)
├── edit_file          (Python 处理器)
├── git_log            (MCP 代理 → git-server 进程)
├── git_commit         (MCP 代理 → git-server 进程)
└── query_db           (MCP 代理 → postgres-server HTTP)
```

### 4.4 技能系统：Python 文件 → 渐进式目录

```
tiny-harness:                    mid-harness:
──────────────────               ──────────────────────────
skills/git.py                    skills/git/
                                   ├── skill.md        ← YAML 清单 + 文档
                                   ├── __init__.py     ← register(agent)
                                   ├── references/     ← 上下文文档
                                   │   ├── api.md
                                   │   └── patterns.md
                                   ├── scripts/        ← 可执行脚本
                                   │   └── setup.sh
                                   ├── assets/         ← 静态资源
                                   │   ├── templates/
                                   │   └── prompts/
                                   └── tools/          ← 工具实现
                                       └── handlers.py
```

| 特性 | tiny-harness | mid-harness |
|------|-------------|-------------|
| 技能格式 | 单个 `.py` 文件 | 目录 + `skill.md` |
| 清单文件 | 可选 `SKILL_META` 字典 | 必需 YAML 前言 |
| 声明式配置 | 无 | 工具、MCP、Hook、依赖都在 YAML 中声明 |
| 上下文文档 | 无 | `references/` 提供领域知识 |
| 可执行脚本 | 无 | `scripts/` 暴露为工具 |
| 静态资源 | 无 | `assets/` 包含模板、配置 |
| MCP 声明 | 无 | 在 `skill.md` 中声明 MCP 服务器 |
| Hook 声明 | 无 | 在 `skill.md` 中声明生命周期钩子 |
| 依赖管理 | 无 | `requires:` / `conflicts:` 字段 |
| 向后兼容 | — | 完全兼容 `register(agent)` 模式 |

### 4.5 会话持久化

| 维度 | tiny-harness | mid-harness |
|------|-------------|-------------|
| 会话存储 | 无内置支持 | `SessionStore`（JSONL） |
| 会话恢复 | 不支持 | `agent.resume_session(id)` |
| 会话列表 | 不支持 | `agent.store.list_sessions()` |
| 导出 | 不支持 | `agent.store.export_session(id)` |

---

## 5. 功能矩阵对比

### 5.1 完整功能对比

| 功能 | tiny-harness | mid-harness | 说明 |
|------|:-----------:|:-----------:|------|
| Agent 循环 | ✓ | ✓ | while-loop 状态机 |
| 工具系统（JSON Schema） | ✓ | ✓ | `(args: dict) -> str` |
| 流式输出 | ✓ | ✓ | 文本增量 + 工具调用事件 |
| 被动事件总线 | ✓ | ✓ | 保留用于流式 UI |
| 工作区安全边界 | ✓ | ✓ | FilesystemGuard |
| LLM 提供商 | 3 个 | 3 个 | Anthropic/OpenAI/DeepSeek |
| 多轮对话 | ✓ | ✓ | `run()` 累积对话 |
| 错误预算 | ✓ | ✓ | 10 total / 3 consecutive |
| 循环检测 | ✓ | ✓ | 重复工具调用检测 |
| Token 估算 | ✓ | ✓ | 字符数 / 4 |
| CLI 交互式 REPL | ✓ | ✓ | 单次 + 对话模式 |
| TUI 富文本界面 | ✓ | ✓ | Rich TUI |
| 生命周期 Hook | ✗ | ✓ | 12 个注入点 |
| Guard Hook | ✗ | ✓ | 安全边界：allow/deny |
| Pre/Post Hook | ✗ | ✓ | 修改输入/观察输出 |
| MCP 客户端 | ✗ | ✓ | stdio + HTTP 传输 |
| MCP 工具代理 | ✗ | ✓ | 透明包装 |
| 渐进式技能 | ✗ | ✓ | 目录 + YAML 清单 |
| 技能引用文档 | ✗ | ✓ | `references/` 自动注入上下文 |
| 技能脚本/资源 | ✗ | ✓ | 暴露为内置工具 |
| 技能依赖管理 | ✗ | ✓ | `requires:` / `conflicts:` |
| 工具名冲突检测 | 静默覆盖 | 异常抛出 | `ToolCollisionError` |
| 保留工具名 | 无 | 4 个 | MCP 命名空间 |
| 会话持久化 | ✗ | ✓ | JSONL 格式 |
| 会话恢复 | ✗ | ✓ | `resume_session()` |
| Hook 签名验证 | ✗ | ✓ | 注册时校验参数签名 |
| Hook 弹性容错 | ✗ | ✓ | Hook 异常不中断 Agent |
| 向后兼容 | — | ✓ | tiny-harness 技能直接可用 |

### 5.2 未加入的功能（留给 mega-harness）

| 功能 | 说明 |
|------|------|
| 多 Agent 编排 | 子 Agent 生成、并行调度 |
| Agent 间通信 | 消息总线、状态共享 |
| 分布式执行 | 多机器 Agent mesh |
| 技能市场 | 技能发现、安装、更新 |
| 跨会话记忆 | 向量数据库持久记忆 |
| Docker 沙箱隔离 | 工具执行安全容器化 |
| 可观测性面板 | Prometheus/Grafana |
| 工作流引擎 | DAG 定义、重试/回退 |
| 模型路由 | 按复杂度/成本路由到不同模型 |

---

## 6. 代码规模对比

### 6.1 源代码行数

| 模块 | 行数 | 来源 |
|------|------|------|
| `_core.py` (Agent) | 407 | 扩展自 tiny |
| `_loop.py` (AgentLoop) | 303 | 重写：加入 Hook |
| `_llm.py` (LLM 提供商) | 469 | 来自 tiny，不变 |
| `_tools.py` (工具系统) | 236 | 扩展自 tiny |
| `_hooks.py` (Hook 系统) | 302 | **NEW** |
| `_persist.py` (持久化) | 126 | **NEW** |
| `_messages.py` (消息管理) | 77 | 来自 tiny |
| `_events.py` (事件总线) | 27 | 来自 tiny |
| `_config.py` (配置) | 28 | 来自 tiny |
| `_guard.py` (安全守卫) | 33 | 来自 tiny |
| `cli.py` (命令行) | 185 | 扩展自 tiny |
| `tui.py` (TUI 界面) | — | 扩展自 tiny |
| 工具模块（3 文件） | ~300 | 扩展自 tiny |
| 技能模块（3 文件） | ~250 | 扩展自 tiny |
| **合计** | **~3,200** | |

### 6.2 测试规模

| 项目 | 测试文件数 | 测试行数 | 测试用例数 |
|------|-----------|---------|-----------|
| tiny-harness | — | — | 195 |
| mid-harness | 18 | 2,516 | 211 |

---

## 7. API 变化

### 7.1 保持不变（向后兼容）

```python
# 以下 API 在 tiny-harness 和 mid-harness 中完全一致
from mid_harness import Agent, Prompt, Config, ToolDef

agent = Agent(prompt=Prompt("You are helpful."), config=Config(...))
agent.load_skill("files")  # tiny-harness 技能直接可用
result = await agent.run("Hello")
```

### 7.2 mid-harness 新增 API

```python
# Hook 系统
agent.hook("tool:pre_execute", handler, hook_type="guard", priority=100)
agent.hook("llm:post_call", handler, hook_type="post")
agent.unhook("my-hook")
agent.list_hooks()

# 会话持久化
agent.start_session()
agent.resume_session(session_id)
agent.store.list_sessions()
agent.store.export_session(session_id)
```

### 7.3 行为变化（破坏性）

| 变化 | tiny-harness | mid-harness |
|------|-------------|-------------|
| 重复工具名 | 静默覆盖 | `ToolCollisionError` |
| 技能加载失败 | 异常传播 | `SkillLoadError` 包装 |
| 带点号的工具名 | 允许 | `ToolCollisionError`（保留给 MCP） |
| 保留工具名 | 无 | `read_reference` 等 4 个 |

---

## 8. 技能迁移路径

### 8.1 零修改迁移

tiny-harness 的技能在 mid-harness 中无需任何修改即可使用：

```python
# tiny-harness 技能 —— 在 mid-harness 中直接可用
# skills/my_tool.py
def register(agent):
    agent.tools.register_from_def(ToolDef(name="my_tool", ...), handler)
    agent._prompt.append("Use my_tool for X.")

# mid-harness 直接加载
agent.load_skill("my_tool")  # 自动检测：单文件 → legacy 加载
```

### 8.2 渐进式升级

最小化升级：创建 `skill.md` 即可享受 mid-harness 新特性：

```bash
# 升级前
skills/my_tool.py

# 升级后
skills/my_tool/
├── skill.md        # 新增：YAML 清单
├── __init__.py     # 重命名：my_tool.py → __init__.py
├── references/     # 可选：上下文文档
└── assets/         # 可选：静态资源
```

`register(agent)` 函数保持不变，`skill.md` 在代码之上添加元数据。

---

## 9. 核心设计约束（跨代不变）

以下约束在 tiny-harness、mid-harness 和未来的 mega-harness 中都保持不变：

1. **框架不做推理** — LLM 是智能的来源
2. **工具就是工具** — 无论是 Python、MCP 还是其他方式，都是 `(args) -> result`
3. **技能自包含** — 不隐式依赖其他技能
4. **显式优于隐式** — 无魔法，一切可追踪
5. **函数优于类** — 仅在需要状态+行为捆绑时使用类
6. **向后兼容** — 每一代都能加载上一代的技能

---

## 10. 总结：何时选择哪个？

### 使用 tiny-harness 如果：

- 只需要单 Agent 循环 + 基础工具
- 技能都是 Python 实现
- 不需要生命周期拦截
- 不需要远程工具调用
- 追求极简（~1,100 行代码）

### 使用 mid-harness 如果：

- 需要 Hook 系统做安全拦截、审计、遥测
- 需要连接 MCP 工具服务器（用任意语言编写的工具）
- 需要结构化技能管理（清单、文档、脚本、依赖）
- 需要会话持久化和恢复
- 团队在开发多个技能，需要标准化格式

### 演进路径

```
tiny-harness ──→ mid-harness ──→ mega-harness
(核心跑通)      (可扩展)        (可分发)
  ~1,100 行      ~3,200 行      ~8,000+ 行
  1 Agent        1 Agent        N Agent Mesh
  Python 工具     Python+MCP     Python+MCP+Remote
  .py 技能       渐进式目录      市场+包
  无 Hook        12 个注入点     12 + 工作流 Hook
  无 MCP         MCP 客户端      MCP 客户端+服务器
```

---

*文档生成时间：2026-05-21*
*数据基于 mid-harness Wave 1 当前代码状态*
