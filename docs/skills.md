# Progressive Skills Design

**Skills as rich directories with YAML manifests, references, scripts, and assets.**

## 1. Evolution: Python File → Progressive Directory

tiny-harness skills are Python modules with a `register(agent)` function and an optional `SKILL_META` dict:

```python
# tiny-harness skill (backward compatible)
SKILL_META = {"name": "files", "version": "1.0.0", ...}

def register(agent):
    agent.tools.register_from_def(ToolDef(...), handler)
    agent._prompt.append(MY_PROMPT)
```

mid-harness retains backward compatibility with this format while introducing **progressive skills**: directories with structured metadata, bundled resources, and declarative configuration.

```
tiny-harness skill:                    mid-harness progressive skill:
─────────────────────                  ─────────────────────────────────
skills/files.py                        skills/files/
                                         ├── skill.md          ← YAML manifest + docs
                                         ├── __init__.py       ← register(agent) (backward compat)
                                         ├── references/       ← Context documents
                                         │   ├── api.md
                                         │   └── patterns.md
                                         ├── scripts/          ← Executable scripts
                                         │   └── setup.sh
                                         ├── assets/           ← Static resources
                                         │   ├── templates/
                                         │   └── prompts/
                                         └── tools/            ← Tool implementations (optional)
                                             └── handlers.py
```

## 2. skill.md — The Manifest

The `skill.md` file is the entry point for progressive skills. It combines a YAML frontmatter manifest with human-readable markdown documentation.

### 2.1 Complete skill.md Example

```markdown
---
# ─── Identity ───
name: git
version: 1.2.0
description: Git version control operations — commit, diff, log, branch management
author: mid-harness
license: MIT
homepage: https://github.com/mid-harness/skills-git

# ─── Dependencies ───
requires:
  - files
conflicts: []

# ─── Tools ───
tools:
  - name: git_log
    description: View commit history with formatting options
    risk_level: read_only
  - name: git_diff
    description: Show uncommitted changes or diff between revisions
    risk_level: read_only
  - name: git_status
    description: Show working tree status
    risk_level: read_only
  - name: git_commit
    description: Create a commit with a message
    risk_level: mutation
  - name: git_branch
    description: List, create, or delete branches
    risk_level: read_only
  - name: git_checkout
    description: Switch branches or restore files
    risk_level: mutation

# ─── MCP Servers ───
mcp_servers:
  - name: git-server
    transport: stdio
    command: python
    args: ["-m", "git_mcp_server"]
    auto_connect: true
    reconnect_on_failure: true

# ─── Hooks ───
hooks:
  - event: tool:pre_execute
    handler: git.hooks.validate_safety
    type: guard
    priority: 100
  - event: tool:post_execute
    handler: git.hooks.audit_command
    type: post
    priority: 0

# ─── References ───
references:
  - path: references/git-workflow.md
    description: Standard git workflow patterns
    load: auto                        # auto | on-demand | never
  - path: references/commit-conventions.md
    description: Commit message conventions
    load: auto

# ─── Scripts ───
scripts:
  - path: scripts/setup-git-hooks.sh
    description: Install git hooks for the workspace
  - path: scripts/analyze-repo.py
    description: Analyze repository statistics

# ─── Configuration ───
config:
  require_user_confirmation:
    - git_commit
    - git_checkout
    - git_push
  max_commit_message_length: 200
  default_branch: main
---

# Git Skill

## Overview

This skill provides git version control capabilities through both Python-native tools and optional MCP server integration.

## Tools

### git_log
View commit history. Supports `--oneline`, `--graph`, `--author`, and date range filters.

### git_diff
Show changes between commits, commit and working tree, etc.

### git_commit
Create a new commit with the staged changes. Requires a commit message.

## Usage Patterns

### Standard Workflow
1. `git_status` — check what's changed
2. `git_diff` — review the changes
3. `git_commit` — commit with a message

## Safety Guidelines

- Never force push to main/master
- Always review diffs before committing
- Create branches for new features
- Write meaningful commit messages
```

### 2.2 YAML Frontmatter Reference

| Section | Field | Type | Required | Description |
|---|---|---|---|---|
| **Identity** | `name` | `str` | ✓ | Unique skill name |
| | `version` | `str` | ✓ | SemVer version |
| | `description` | `str` | ✓ | Short description |
| | `author` | `str` | — | Author name/org |
| | `license` | `str` | — | SPDX license identifier |
| | `homepage` | `str` | — | Project URL |
| **Dependencies** | `requires` | `list[str]` | — | Skills this depends on |
| | `conflicts` | `list[str]` | — | Incompatible skills |
| **Tools** | `tools` | `list[ToolDecl]` | — | Tools provided (informational) |
| **MCP** | `mcp_servers` | `list[MCPServerDecl]` | — | MCP servers to spawn |
| **Hooks** | `hooks` | `list[HookDecl]` | — | Lifecycle hooks |
| **References** | `references` | `list[RefDecl]` | — | Context documents |
| **Scripts** | `scripts` | `list[ScriptDecl]` | — | Executable scripts |
| **Config** | `config` | `dict` | — | Skill-specific configuration |

### 2.3 Sub-Declarations

**ToolDecl:**
```yaml
tools:
  - name: git_log
    description: View commit history
    risk_level: read_only        # safe | read_only | mutation | destructive | dangerous
    require_confirmation: false  # Ask user before executing
```

**MCPServerDecl:**
```yaml
mcp_servers:
  - name: git-server
    transport: stdio             # stdio | http
    command: python
    args: ["-m", "git_mcp_server"]
    env:                         # Environment variables ($VAR supported)
      GIT_DIR: "${WORKSPACE}"
    auto_connect: true
    reconnect_on_failure: true
    health_check_interval_sec: 30
```

**HookDecl:**
```yaml
hooks:
  - event: tool:pre_execute
    handler: git.hooks.validate  # Python import path relative to skill
    type: guard                  # guard | pre | post | wrap
    priority: 100
```

**RefDecl:**
```yaml
references:
  - path: references/api.md
    description: API reference
    load: auto                   # auto (on skill load) | on-demand | never
    format: markdown             # markdown | text | yaml
```

**ScriptDecl:**
```yaml
scripts:
  - path: scripts/setup.sh
    description: Setup script
    executable: true
    args: ["--workspace", "${WORKSPACE}"]
```

## 3. Directory Structure Convention

### 3.1 references/ — Context Documents

Documents loaded into the agent's context. These give the agent domain knowledge without bloating the system prompt.

```
references/
├── api.md              # API reference for this skill's tools
├── patterns.md         # Common usage patterns and workflows
├── examples.md         # Real-world examples
├── troubleshooting.md  # Common issues and solutions
└── architecture.md     # Architecture decisions for complex skills
```

**Loading behavior:**
- `load: auto` — loaded into context on skill load (kept in `MessageManager.references`)
- `load: on-demand` — available via a tool (`read_reference(name)`) but not auto-loaded
- `load: never` — listed but not loaded (metadata only)

**How references are used:**
```python
# In ProgressiveSkillLoader:
for ref in manifest.references:
    if ref.load == "auto":
        content = (skill_dir / ref.path).read_text()
        agent.context.add_reference(
            name=f"{manifest.name}/{ref.path.name}",
            content=content,
            format=ref.format,
        )

# The agent can access references via a tool:
agent.tools.register_from_def(
    ToolDef(
        name="read_reference",
        description="Read a skill reference document.",
        parameters={...},
    ),
    handler=lambda args: agent.context.get_reference(args["name"]),
)
```

### 3.2 scripts/ — Executable Scripts

Scripts that the agent or user can execute. Exposed via a `run_skill_script` tool.

```
scripts/
├── setup.sh            # Environment setup
├── deploy.py           # Deployment script
├── analyze.py          # Code analysis
└── migrate.py          # Database migration
```

**How scripts are exposed:**
```python
# Auto-registered tool for scripts
agent.tools.register_from_def(
    ToolDef(
        name="run_skill_script",
        description="Execute a script from a loaded skill.",
        parameters={
            "type": "object",
            "properties": {
                "skill": {"type": "string", "description": "Skill name"},
                "script": {"type": "string", "description": "Script path relative to skill"},
                "args": {"type": "array", "items": {"type": "string"}, "description": "Arguments"},
            },
            "required": ["skill", "script"],
        },
        risk_level="dangerous",
    ),
    handler=run_skill_script_handler,
)
```

### 3.3 assets/ — Static Resources

Templates, prompts, configuration files, and other static resources.

```
assets/
├── templates/          # File templates
│   ├── python/
│   │   ├── module.py.tmpl
│   │   └── test.py.tmpl
│   ├── docker/
│   │   └── Dockerfile.tmpl
│   └── config/
│       └── pyproject.toml.tmpl
├── prompts/            # Additional prompt templates
│   ├── code-review.md
│   └── onboarding.md
├── icons/              # Icons and images
└── configs/            # Default configurations
    └── .editorconfig
```

**How assets are exposed:**
```python
# Auto-registered tool for asset access
agent.tools.register_from_def(
    ToolDef(
        name="read_skill_asset",
        description="Read an asset file from a loaded skill.",
        parameters={
            "type": "object",
            "properties": {
                "skill": {"type": "string"},
                "path": {"type": "string", "description": "Asset path relative to skill's assets/"},
            },
            "required": ["skill", "path"],
        },
        risk_level="read_only",
    ),
    handler=read_skill_asset_handler,
)

# Template rendering tool
agent.tools.register_from_def(
    ToolDef(
        name="render_template",
        description="Render a template from a skill's assets/templates/ with variables.",
        parameters={
            "type": "object",
            "properties": {
                "skill": {"type": "string"},
                "template": {"type": "string"},
                "variables": {"type": "object"},
            },
            "required": ["skill", "template"],
        },
        risk_level="read_only",
    ),
    handler=render_template_handler,
)
```

### 3.4 tools/ — Organized Tool Implementations

Optional subdirectory for organizing tool handler code separately from the skill's `__init__.py`.

```
tools/
├── __init__.py
├── handlers.py         # Tool handler functions
├── validators.py       # Argument validation
└── formatters.py       # Result formatting
```

```python
# skills/git/tools/handlers.py
async def git_log(args: dict) -> str:
    result = subprocess.run(["git", "log", "--oneline", "-10"], capture_output=True, text=True)
    return result.stdout

# skills/git/__init__.py
from .tools.handlers import git_log, git_diff, git_commit

def register(agent):
    agent.tools.register_from_def(ToolDef(...), git_log)
    agent.tools.register_from_def(ToolDef(...), git_diff)
    agent.tools.register_from_def(ToolDef(...), git_commit)
```

## 4. Progressive Loading

The `ProgressiveSkillLoader` loads a skill in phases, allowing partial success and graceful degradation.

### 4.1 Loading Phases

```
Phase 0: Discovery
  ├── Resolve skill path (directory, file, or package)
  ├── Check if already loaded
  └── Parse skill.md frontmatter (if directory)

Phase 1: Validation
  ├── Validate YAML structure
  ├── Check dependencies (requires[])
  ├── Check conflicts (conflicts[])
  └── Check tool name collisions

Phase 2: MCP Bootstrap
  ├── Spawn declared MCP servers
  ├── Wait for initialization
  └── Discover available tools

Phase 3: Tool Registration
  ├── Import __init__.py → call register(agent)
  ├── Register MCP proxy tools
  └── Register auto-generated tools (read_reference, run_skill_script, read_skill_asset, render_template)

Phase 4: Context Enrichment
  ├── Load references/ documents (load: auto)
  └── Append prompt section from register()

Phase 5: Hook Registration
  └── Register hooks declared in skill.md

Phase 6: Post-Load
  └── Emit skill:load hook
```

### 4.2 Error Handling Per Phase

| Phase | Failure Mode | Behavior |
|---|---|---|
| Discovery | Skill not found | Raise `SkillNotFoundError` |
| Validation | Missing dependency | Raise `SkillDependencyError` with missing skill name |
| Validation | Conflict detected | Raise `SkillConflictError` with conflicting skill name |
| MCP Bootstrap | Server fails to start | Log warning; continue without MCP tools |
| MCP Bootstrap | Timeout | Log warning; retry in background |
| Tool Registration | Handler import fails | Raise `SkillLoadError` with traceback |
| Context Enrichment | Reference file missing | Log warning; skip reference |
| Hook Registration | Hook handler missing | Log warning; skip hook |

### 4.3 Example: Graceful Degradation

```python
agent.load_skill("git")
# Phase 0-1: ✓ Discovery and validation pass
# Phase 2:   ⚠ MCP server failed to start (command not found)
#            → Git skill loads without MCP tools (uses Python handlers instead)
# Phase 3:   ✓ Python tools registered
# Phase 4:   ✓ References loaded
# Phase 5:   ✓ Hooks registered
# Phase 6:   ✓ Skill loaded (with warnings)
# Agent has: git_log, git_diff, git_commit (Python) but no MCP proxy tools
```

## 5. Backward Compatibility

### 5.1 Loading tiny-harness Skills

The `load_skill()` method detects whether the target is a directory or file:

```python
class Agent:
    def load_skill(self, skill_ref: str) -> None:
        resolved = self._resolve_skill_path(skill_ref)

        if resolved.is_dir() and (resolved / "skill.md").exists():
            # Progressive skill (mid-harness format)
            return self._load_progressive_skill(resolved)
        elif resolved.is_dir() and (resolved / "__init__.py").exists():
            # Legacy package (import + register)
            return self._load_legacy_skill(resolved)
        elif resolved.suffix == ".py":
            # Legacy single-file skill (import + register)
            return self._load_legacy_skill(resolved)
        else:
            raise SkillNotFoundError(f"Cannot load skill: {skill_ref}")
```

### 5.2 Converting a tiny-harness Skill to Progressive

Minimal conversion — add a `skill.md`:

```bash
# Before:
skills/git.py

# After:
skills/git/
├── skill.md        # NEW: YAML frontmatter with metadata
├── __init__.py     # RENAMED: git.py → __init__.py
├── references/     # NEW: optional
└── assets/         # NEW: optional
```

The `register(agent)` function in `__init__.py` is unchanged. The `skill.md` adds metadata without changing the implementation.

## 6. Skill Discovery and Listing

### 6.1 Listing Loaded Skills

```python
agent.list_skills()
# [
#   {"name": "files", "version": "2.0.0", "type": "progressive", "tools": 7, "mcp_servers": 0},
#   {"name": "git", "version": "1.2.0", "type": "progressive", "tools": 6, "mcp_servers": 1},
#   {"name": "shell", "version": "1.0.0", "type": "legacy", "tools": 1, "mcp_servers": 0},
# ]
```

### 6.2 Discovering Available Skills

```bash
mid-harness skills list
# Available skills:
#   files (progressive)  — File system operations (built-in)
#   shell  (legacy)       — Shell command execution (built-in)
#   search (legacy)       — Code search tools (built-in)
#   git    (progressive)  — Git version control (installed)
#   docker (progressive)  — Docker container management (local: ./skills/docker/)
```

### 6.3 Skill Info

```bash
mid-harness skills info git
# Name: git
# Version: 1.2.0
# Type: progressive
# Author: mid-harness
# Tools: git_log, git_diff, git_status, git_commit, git_branch, git_checkout
# MCP Servers: git-server (stdio)
# Dependencies: files
# References: git-workflow.md, commit-conventions.md
# Scripts: setup-git-hooks.sh
```

## 7. Skill Distribution

### 7.1 Local Skills

Skills in the local `skills/` directory are auto-discovered:

```
project/
├── skills/
│   ├── my-custom-skill/
│   │   ├── skill.md
│   │   └── __init__.py
│   └── another-skill/
│       ├── skill.md
│       └── __init__.py
└── mid-harness.toml
```

### 7.2 Installed Packages

Skills installed via pip using the naming convention `mid-harness-skill-{name}`:

```bash
pip install mid-harness-skill-git
pip install mid-harness-skill-docker
```

```python
# Resolved automatically
agent.load_skill("git")  # → mid_harness_skill_git package
```

### 7.3 Remote Skills (Future)

Git-based skill installation (mega-harness):

```bash
mid-harness skills install github.com/mid-harness/skills-git
mid-harness skills install https://gitlab.com/team/skill-cicd.git
```

## 8. Skill Development Workflow

### 8.1 Creating a New Progressive Skill

```bash
# Scaffold a new skill
mid-harness skills create my-skill

# Creates:
skills/my-skill/
├── skill.md           # Template with TODO markers
├── __init__.py        # Skeleton register() function
├── references/
│   └── .gitkeep
├── scripts/
│   └── .gitkeep
├── assets/
│   └── .gitkeep
└── tools/
    ├── __init__.py
    └── handlers.py    # Skeleton handler functions
```

### 8.2 Validating a Skill

```bash
mid-harness skills validate skills/my-skill/
# ✓ skill.md: YAML valid
# ✓ skill.md: required fields present (name, version, description)
# ✓ dependencies: 'files' is available
# ⚠ tools: 'my_tool' declared but not found in __init__.py
# ✓ __init__.py: has register() function
# ✓ references/: 2 files found
# Skill is valid (1 warning)
```

## 9. Design Decisions

| Decision | Rationale |
|---|---|
| `skill.md` as single manifest | One file to read, parse, and validate. No scattered JSON/YAML/TOML. |
| YAML frontmatter in markdown | Human-readable docs + machine-readable metadata in one file. Standard pattern (Jekyll, Hugo, Obsidian). |
| Backward compatible with `register(agent)` | All tiny-harness skills continue to work. Progressive skills add metadata without breaking existing code. |
| References loaded as context, not prompt | Keeps system prompt lean. References are available via tool, not injected into every message. |
| Scripts/Assets exposed via auto-generated tools | No special agent loop handling. Standard tool calls. |
| Separate `tools/` subdirectory optional | Simple skills can keep handlers in `__init__.py`. Complex skills can organize with subdirectories. |
| Graceful MCP degradation | A skill without MCP connection is still useful (Python fallback handlers). Don't block the skill for one MCP failure. |
| Phase-based loading | Each phase can fail independently. Partial success is better than total failure. |
