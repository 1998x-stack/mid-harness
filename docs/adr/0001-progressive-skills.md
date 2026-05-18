# ADR 001: Progressive Skills Format

**Date**: 2026-05-16
**Status**: Proposed

## Context

tiny-harness skills are Python modules with a `register(agent)` function and an optional `SKILL_META` dict. This format works for simple tool bundles but has limitations:

1. **No standard manifest**: Each skill invents its own metadata structure. Tool discovery requires importing the module.
2. **No bundled resources**: Skills can only contain Python code. References, scripts, templates must be separate packages.
3. **No declarative MCP**: MCP servers must be configured externally; skills can't declare their server requirements.
4. **No hook declaration**: Skills register hooks imperatively in `register()`, making them invisible to tooling.
5. **No dependency management**: `requires` and `conflicts` fields exist in `SKILL_META` but are ad-hoc and inconsistent.

mid-harness needs a skill format that supports these capabilities while maintaining backward compatibility.

## Decision

**Introduce progressive skills: directories with `skill.md` (YAML frontmatter + markdown), `references/`, `scripts/`, and `assets/`.**

The `skill.md` file serves as the single source of truth for skill metadata, tool declarations, MCP server configs, hook registrations, and dependency information.

## Rationale

1. **Single manifest file**: One file to parse for skill metadata. Tooling reads `skill.md` without importing Python.
2. **YAML frontmatter**: Machine-readable metadata in a human-readable document. Standard pattern used by Jekyll, Hugo, Obsidian, and countless static site generators.
3. **Structured resources**: `references/`, `scripts/`, `assets/` are conventional directories with defined purposes. No guessing where templates or docs live.
4. **Declarative MCP and hooks**: Skills declare their infrastructure needs without executing code. Tooling can validate before loading.
5. **Backward compatibility**: `register(agent)` in `__init__.py` continues to work. Progressive skills add metadata without breaking existing skills.

## Consequences

- Skills must maintain both `skill.md` (manifest) and `__init__.py` (implementation). The manifest describes what the skill provides; the code provides it.
- `ProgressiveSkillLoader` must handle YAML parsing, validation, and phased loading with graceful degradation.
- Tooling (CLI, validators, linters) can now inspect skills without importing Python code.
- MCP servers are declared in the manifest and spawned by the loader — not imperatively in `register()`.
- Hook registration is declarative in the manifest, making skills' lifecycle behavior visible and audit-able.

## Alternatives Considered

### A. Keep Python-only format, add decorators
Use decorators like `@skill.tool(...)`, `@skill.mcp_server(...)` instead of a YAML manifest. Rejected because it requires importing the module to discover metadata, and decorators can't express complex configurations as cleanly as YAML.

### B. Separate manifest file (skill.yaml or skill.json)
Use a standalone YAML/JSON file instead of frontmatter in markdown. Rejected because it separates documentation from metadata, forcing developers to maintain two files and keeping them in sync.

### C. Python-based manifest (skill_config.py or pyproject.toml)
Use a Python file or TOML for the manifest. Rejected because YAML-in-markdown is more readable for mixed human/machine content and is a widely adopted pattern.

### D. Full declarative skills (no Python code)
Tools defined entirely in YAML with shell commands as handlers. Rejected because it limits expressiveness — complex tool logic requires code. Progressive skills retain Python for implementation while adding declarative metadata.
