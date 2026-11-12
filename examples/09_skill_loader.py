"""
Example 09: Progressive SkillLoader — manifest parsing, dependency validation, auto-tools.

Demonstrates:
- YAML frontmatter parsing from skill.md
- SkillManifest loading and validation
- Dependency/conflict checking
- ProgressiveSkillLoader loading flow
- Auto-generated tools (read_reference, run_skill_script, read_skill_asset)
"""

import tempfile
from pathlib import Path
from mid_harness._skills import (
    SkillManifest, load_manifest, ProgressiveSkillLoader,
    SkillNotFoundError, SkillDependencyError, SkillConflictError,
)
from mid_harness import Agent, Prompt, Config


def test_manifest_parsing():
    print("=== Skill Manifest Parsing ===")
    with tempfile.TemporaryDirectory() as tmp:
        skill_dir = Path(tmp) / "test-skill"
        skill_dir.mkdir()
        skill_md = skill_dir / "skill.md"
        skill_md.write_text("""---
name: test-skill
version: 1.2.0
description: A test skill for manifest parsing
author: test
requires:
  - files
conflicts:
  - dangerous-tool
tools:
  - name: echo
    description: Echo input
    risk_level: read_only
references:
  - path: references/api.md
    description: API docs
    load: auto
scripts:
  - path: scripts/setup.sh
    description: Setup
config:
  max_retries: 3
---
""")

        manifest = load_manifest(skill_dir)
        assert manifest.name == "test-skill"
        assert manifest.version == "1.2.0"
        assert manifest.description == "A test skill for manifest parsing"
        assert "files" in manifest.requires
        assert "dangerous-tool" in manifest.conflicts
        assert len(manifest.tools) == 1
        assert manifest.tools[0]["name"] == "echo"
        assert len(manifest.references) == 1
        assert len(manifest.scripts) == 1
        assert manifest.config["max_retries"] == 3

        print(f"Test 1 - name: {manifest.name}")
        print(f"Test 1 - version: {manifest.version}")
        print(f"Test 1 - requires: {manifest.requires}")
        print(f"Test 1 - conflicts: {manifest.conflicts}")
        print(f"Test 1 - tools: {manifest.tools}")
        print("✓ PASS: manifest parsed correctly")


def test_manifest_not_found():
    print("\n=== Skill Not Found ===")
    try:
        load_manifest(Path("/tmp/nonexistent-skill-xyz"))
        assert False, "Should have raised"
    except SkillNotFoundError as e:
        assert "skill.md not found" in str(e)
        print(f"Test 2 - error: {e}")
    print("✓ PASS: SkillNotFoundError raised")


def test_dependency_validation():
    print("\n=== Dependency Validation ===")
    agent = Agent(
        prompt=Prompt("test"), config=Config(model="test", api_key="test", workspace="/tmp"),
    )
    agent._loaded_skills = ["files", "shell"]

    loader = ProgressiveSkillLoader(agent)

    manifest_ok = SkillManifest(name="ok-skill", version="1.0", description="ok", requires=["files"])
    loader._validate(manifest_ok)
    print("Test 3 - satisfied dependency: no error")
    print("✓ PASS: satisfied dependency passes")

    manifest_missing = SkillManifest(name="bad-skill", version="1.0", description="bad", requires=["nonexistent"])
    try:
        loader._validate(manifest_missing)
        assert False, "Should have raised"
    except SkillDependencyError as e:
        assert "nonexistent" in str(e)
        print(f"Test 4 - missing dependency: {e}")
    print("✓ PASS: SkillDependencyError raised")

    manifest_conflict = SkillManifest(name="conflict-skill", version="1.0", description="x", conflicts=["shell"])
    try:
        loader._validate(manifest_conflict)
        assert False, "Should have raised"
    except SkillConflictError as e:
        assert "shell" in str(e)
        print(f"Test 5 - conflict detected: {e}")
    print("✓ PASS: SkillConflictError raised")


def test_auto_tools_registration():
    print("\n=== Auto-generated Tools ===")
    with tempfile.TemporaryDirectory() as tmp:
        skill_dir = Path(tmp) / "ref-skill"
        skill_dir.mkdir()
        (skill_dir / "skill.md").write_text("""---
name: ref-skill
version: 1.0.0
description: Test references
references:
  - path: references/test.md
    description: Test ref
    load: auto
---
""")
        (skill_dir / "__init__.py").write_text("def register(agent): pass\n")
        ref_dir = skill_dir / "references"
        ref_dir.mkdir()
        (ref_dir / "test.md").write_text("Hello from reference!")

        agent = Agent(
            prompt=Prompt("test"), config=Config(model="test", api_key="test", workspace="/tmp"),
        )
        existing = agent.tools.names()
        loader = ProgressiveSkillLoader(agent)
        loader._register_auto_tools(skill_dir)

        names = agent.tools.names()
        new_tools = set(names) - set(existing)
        print(f"Test 6 - new auto-tools: {new_tools}")
        assert "read_reference" in names
        assert "read_skill_asset" in names
        assert "run_skill_script" in names
        print("✓ PASS: Auto-generated tools registered")


def test_manifest_without_optional_fields():
    print("\n=== Minimal Manifest ===")
    with tempfile.TemporaryDirectory() as tmp:
        skill_dir = Path(tmp) / "minimal-skill"
        skill_dir.mkdir()
        (skill_dir / "skill.md").write_text("""---
name: minimal
version: 0.1.0
description: Minimal skill
---
""")

        manifest = load_manifest(skill_dir)
        assert manifest.name == "minimal"
        assert manifest.version == "0.1.0"
        assert manifest.requires == []
        assert manifest.hooks == []
        print(f"Test 7 - minimal manifest: {manifest.name} v{manifest.version}")
        print("✓ PASS: Minimal manifest parses correctly")


if __name__ == "__main__":
    test_manifest_parsing()
    test_manifest_not_found()
    test_dependency_validation()
    test_auto_tools_registration()
    test_manifest_without_optional_fields()
    print("\nAll SkillLoader tests passed!")
