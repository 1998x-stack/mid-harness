# mid_harness/_skills.py
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

from mid_harness._tools import ToolDef

logger = logging.getLogger("mid_harness.skills")


class SkillNotFoundError(Exception):
    pass


class SkillDependencyError(Exception):
    pass


class SkillConflictError(Exception):
    pass


@dataclass
class SkillManifest:
    name: str
    version: str
    description: str
    author: str = "unknown"
    license: str = "MIT"
    homepage: str = ""
    requires: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    tools: list[dict] = field(default_factory=list)
    mcp_servers: list[dict] = field(default_factory=list)
    hooks: list[dict] = field(default_factory=list)
    references: list[dict] = field(default_factory=list)
    scripts: list[dict] = field(default_factory=list)
    config: dict = field(default_factory=dict)


def _parse_yaml_frontmatter(text: str) -> dict:
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return {}
    raw = m.group(1)
    lines = raw.split("\n")
    result: dict = {}
    current_key: str | None = None
    current_items: list = []
    current_config: dict = {}
    current_obj: dict = {}
    in_list = False
    in_config = False
    in_object_list = False
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            if in_list and current_key:
                result[current_key] = current_items
                current_items = []
                in_list = False
            if in_config:
                result[current_key] = current_config
                current_config = {}
                in_config = False
            if in_object_list and current_key:
                if current_obj:
                    current_items.append(current_obj)
                result[current_key] = current_items
                current_items = []
                current_obj = {}
                in_object_list = False
            key_match = re.match(r"^(\w[\w_]*)\s*:\s*(.*)", stripped)
            if key_match:
                current_key = key_match.group(1)
                value = key_match.group(2).strip()
                if value:
                    result[current_key] = _parse_value(value)
                    current_key = None
                else:
                    pass
        elif indent == 2 and current_key:
            item_match = re.match(r"^-\s+(.*)", stripped)
            if item_match:
                item_val = item_match.group(1).strip()
                kv = re.match(r"^(\w[\w_]*)\s*:\s*(.*)", item_val)
                if kv:
                    in_object_list = True
                    current_obj = {kv.group(1): _parse_value(kv.group(2).strip())}
                else:
                    in_list = True
                    current_items.append(_parse_value(item_val))
            else:
                if current_key == "config":
                    in_config = True
                    kv = re.match(r"^(\w[\w_]*)\s*:\s*(.*)", stripped)
                    if kv:
                        current_config[kv.group(1)] = _parse_value(kv.group(2).strip())
                else:
                    in_object_list = True
                    kv = re.match(r"^(\w[\w_]*)\s*:\s*(.*)", stripped)
                    if kv:
                        current_obj[kv.group(1)] = _parse_value(kv.group(2).strip())
        elif indent == 4 and current_key and in_object_list:
            kv = re.match(r"^(\w[\w_]*)\s*:\s*(.*)", stripped)
            if kv:
                current_obj[kv.group(1)] = _parse_value(kv.group(2).strip())
        i += 1
    if in_list and current_key:
        result[current_key] = current_items
    if in_config and current_key:
        result[current_key] = current_config
    if in_object_list and current_key:
        if current_obj:
            current_items.append(current_obj)
        result[current_key] = current_items
    return result


def _parse_value(v: str) -> Any:
    v = v.strip().strip('"').strip("'")
    if v == "[]":
        return []
    if v == "{}":
        return {}
    if v.lower() == "true":
        return True
    if v.lower() == "false":
        return False
    if v.lower() == "auto":
        return v
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v


def load_manifest(skill_dir: Path) -> SkillManifest:
    md_path = skill_dir / "skill.md"
    if not md_path.exists():
        raise SkillNotFoundError(f"skill.md not found in {skill_dir}")
    raw = md_path.read_text(encoding="utf-8")
    frontmatter = _parse_yaml_frontmatter(raw)
    return SkillManifest(
        name=frontmatter.get("name", skill_dir.name),
        version=frontmatter.get("version", "0.0.0"),
        description=frontmatter.get("description", ""),
        author=frontmatter.get("author", "unknown"),
        license=frontmatter.get("license", "MIT"),
        homepage=frontmatter.get("homepage", ""),
        requires=frontmatter.get("requires", []),
        conflicts=frontmatter.get("conflicts", []),
        tools=frontmatter.get("tools", []),
        mcp_servers=frontmatter.get("mcp_servers", []),
        hooks=frontmatter.get("hooks", []),
        references=frontmatter.get("references", []),
        scripts=frontmatter.get("scripts", []),
        config=frontmatter.get("config", {}),
    )


class ProgressiveSkillLoader:
    def __init__(self, agent):
        self._agent = agent

    def load_skill(self, skill_dir: Path, skill_ref: str) -> None:
        manifest = load_manifest(skill_dir)
        logger.info(
            "Loading progressive skill '%s' v%s from %s",
            manifest.name, manifest.version, skill_dir,
        )

        self._validate(manifest)

        module = self._import_module(skill_dir)
        if module and hasattr(module, "register"):
            module.register(self._agent)
        else:
            logger.warning("Skill '%s' has no register() function", manifest.name)

        self._register_auto_tools(skill_dir)
        self._register_hooks(manifest)
        self._agent._loaded_skills.append(manifest.name)

        self._emit_skill_loaded(manifest, skill_ref)

    def _validate(self, manifest: SkillManifest) -> None:
        missing = [d for d in manifest.requires if d not in self._agent._loaded_skills]
        if missing:
            raise SkillDependencyError(
                f"Skill '{manifest.name}' requires: {', '.join(missing)}"
            )
        conflicts = [c for c in manifest.conflicts if c in self._agent._loaded_skills]
        if conflicts:
            raise SkillConflictError(
                f"Skill '{manifest.name}' conflicts with: {', '.join(conflicts)}"
            )

    def _import_module(self, skill_dir: Path) -> ModuleType | None:
        init_path = skill_dir / "__init__.py"
        if not init_path.exists():
            return None
        import importlib.util

        spec = importlib.util.spec_from_file_location(skill_dir.name, init_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _register_auto_tools(self, skill_dir: Path) -> None:
        registry = self._agent.tools
        existing = set(registry.names())

        if "read_reference" not in existing:
            def _read_reference(args: dict) -> str:
                skill = args["skill"]
                path = args["path"]
                ref_dir = skill_dir.parent / skill / "references"
                ref_path = ref_dir / path
                if not ref_path.exists():
                    return f"Reference '{path}' not found in skill '{skill}'"
                return ref_path.read_text(encoding="utf-8")

            try:
                registry.register_mcp_proxy(
                    ToolDef(
                        name="read_reference",
                        description="Read a reference document from a loaded skill",
                        parameters={
                            "type": "object",
                            "properties": {
                                "skill": {"type": "string"},
                                "path": {"type": "string"},
                            },
                            "required": ["skill", "path"],
                        },
                        risk_level="read_only",
                    ),
                    _read_reference,
                )
            except Exception:
                pass

        if "run_skill_script" not in existing:
            def _run_script(args: dict) -> str:
                skill = args["skill"]
                script_path = args["path"]
                script_args = args.get("args", [])
                full_path = skill_dir.parent / skill / "scripts" / script_path
                if not full_path.exists():
                    return f"Script '{script_path}' not found in skill '{skill}'"
                try:
                    result = subprocess.run(
                        [str(full_path)] + list(script_args),
                        capture_output=True, text=True, timeout=30,
                    )
                    return result.stdout or result.stderr or "Script completed"
                except subprocess.TimeoutExpired:
                    return f"Script '{script_path}' timed out after 30s"
                except Exception as e:
                    return f"Script '{script_path}' failed: {e}"

            try:
                registry.register_mcp_proxy(
                    ToolDef(
                        name="run_skill_script",
                        description="Execute a script from a loaded skill",
                        parameters={
                            "type": "object",
                            "properties": {
                                "skill": {"type": "string"},
                                "path": {"type": "string"},
                                "args": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": ["skill", "path"],
                        },
                        risk_level="dangerous",
                    ),
                    _run_script,
                )
            except Exception:
                pass

        if "read_skill_asset" not in existing:
            def _read_asset(args: dict) -> str:
                skill = args["skill"]
                path = args["path"]
                asset_path = skill_dir.parent / skill / "assets" / path
                if not asset_path.exists():
                    return f"Asset '{path}' not found in skill '{skill}'"
                return asset_path.read_text(encoding="utf-8")

            try:
                registry.register_mcp_proxy(
                    ToolDef(
                        name="read_skill_asset",
                        description="Read an asset file from a loaded skill",
                        parameters={
                            "type": "object",
                            "properties": {
                                "skill": {"type": "string"},
                                "path": {"type": "string"},
                            },
                            "required": ["skill", "path"],
                        },
                        risk_level="read_only",
                    ),
                    _read_asset,
                )
            except Exception:
                pass

    def _register_hooks(self, manifest: SkillManifest) -> None:
        for hook_decl in manifest.hooks:
            try:
                self._agent.hook(
                    event=hook_decl["event"],
                    handler=_make_stub_handler(hook_decl),
                    hook_type=hook_decl.get("type", "post"),
                    priority=hook_decl.get("priority", 0),
                    source=f"skill:{manifest.name}",
                )
            except Exception as e:
                logger.warning(
                    "Failed to register hook '%s' for skill '%s': %s",
                    hook_decl.get("event", "?"), manifest.name, e,
                )

    def _emit_skill_loaded(self, manifest: SkillManifest, skill_ref: str) -> None:
        import asyncio

        try:
            loop = asyncio.get_running_loop()
            from mid_harness._hooks import HookContext

            skill_ctx = HookContext(
                "skill:load",
                agent=self._agent,
                data={
                    "skill_name": skill_ref,
                    "version": manifest.version,
                    "tools_added": self._agent.tools.names(),
                },
            )
            loop.create_task(self._agent._hooks.emit_post("skill:load", skill_ctx))
        except RuntimeError:
            logger.debug("No running event loop; skill:load hook deferred")


def _make_stub_handler(hook_decl: dict):
    event = hook_decl.get("event", "?")

    def stub(ctx):
        logger.debug("Hook handler for '%s' is a stub", event)

    return stub
