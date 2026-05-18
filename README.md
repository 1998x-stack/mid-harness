# mid-harness

**Extensible AI agent harness — hooks, MCP, and progressive skills on top of tiny-harness.**

mid-harness extends [tiny-harness](https://github.com/1998x-stack/tiny-harness) with three new subsystems:

- **Hook System** — lifecycle hooks (pre/post/guard/wrap) at 15 hook points for intercepting, modifying, and observing agent operations
- **MCP Integration** — Model Context Protocol client for connecting to remote tool servers written in any language
- **Progressive Skills** — skills as rich directories with `skill.md` (YAML frontmatter), `references/`, `scripts/`, and `assets/`

## Status

**Design phase.** This repository contains the architecture analysis and design documents. Implementation is pending.

## Documentation

| Document | Description |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Overall mid-harness architecture |
| [docs/evolution.md](docs/evolution.md) | Evolution path: tiny → mid → mega |
| [docs/hooks.md](docs/hooks.md) | Hook system design (15 lifecycle points) |
| [docs/mcp.md](docs/mcp.md) | MCP client integration (stdio + HTTP) |
| [docs/skills.md](docs/skills.md) | Progressive skills format (skill.md + directories) |
| [docs/adr/0001-progressive-skills.md](docs/adr/0001-progressive-skills.md) | ADR: Progressive skills format |
| [docs/adr/0002-hook-system.md](docs/adr/0002-hook-system.md) | ADR: Hook system as separate subsystem |
| [docs/adr/0003-mcp-integration.md](docs/adr/0003-mcp-integration.md) | ADR: MCP client integration |

## Quick Comparison

| Feature | tiny-harness | mid-harness |
|---|---|---|
| Agent loop | while-loop state machine | same + hook injection |
| Tools | Python handlers | Python + MCP proxy handlers |
| Skills | Python modules | Progressive directories + legacy compat |
| Events | Passive EventBus | EventBus + active HookSystem |
| MCP | — | stdio + HTTP client |
| Hooks | — | 15 lifecycle points, 4 hook types |
| References | — | Per-skill context documents |
| Scripts/Assets | — | Per-skill scripts and templates |
| Lines (target) | ~1,100 | ~3,000 |

## License

MIT
