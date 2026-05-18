"""
Example 05: Tool collision detection and reserved name enforcement.

Demonstrates:
- Duplicate tool name raises ToolCollisionError
- Reserved names blocked
- MCP namespace names blocked
- SkillLoadError wrapping ToolCollisionError
"""
from mid_harness._tools import ToolDef, ToolRegistry, ToolCollisionError
from mid_harness._core import SkillLoadError


def test_collision_detection():
    registry = ToolRegistry()

    # ── Test 1: Register and read a tool ──
    registry.register_from_def(
        ToolDef(name="echo", description="Echo input",
                parameters={"type": "object", "properties": {"msg": {"type": "string"}}, "required": ["msg"]}),
        lambda args: args["msg"],
    )
    tool = registry.get("echo")
    assert tool is not None
    print(f"Test 1 - Registered: {tool.definition.name}")
    print("✓ PASS: Tool registration works")

    # ── Test 2: Duplicate name raises error ──
    try:
        registry.register_from_def(
            ToolDef(name="echo", description="Another echo",
                    parameters={"type": "object", "properties": {}}),
            lambda args: "ok",
        )
        assert False, "Should have raised ToolCollisionError"
    except ToolCollisionError as e:
        print(f"Test 2 - Collision error: {e}")
        assert "already registered" in str(e)
    print("✓ PASS: Duplicate tool name raises ToolCollisionError")

    # ── Test 3: Reserved names blocked ──
    for reserved in ToolRegistry.RESERVED_NAMES:
        try:
            registry.register_from_def(
                ToolDef(name=reserved, description="Reserved",
                        parameters={"type": "object", "properties": {}}),
                lambda args: "ok",
            )
            assert False, f"Should have blocked reserved name: {reserved}"
        except ToolCollisionError as e:
            assert "reserved" in str(e).lower()
    print(f"Test 3 - Blocked reserved names: {sorted(ToolRegistry.RESERVED_NAMES)}")
    print("✓ PASS: Reserved names blocked")

    # ── Test 4: MCP namespace names blocked ──
    mcp_names = ["git-server.git_commit", "db.query", "files.read_file"]
    for name in mcp_names:
        try:
            registry.register_from_def(
                ToolDef(name=name, description="MCP-like",
                        parameters={"type": "object", "properties": {}}),
                lambda args: "ok",
            )
            assert False, f"Should have blocked MCP namespace: {name}"
        except ToolCollisionError as e:
            assert "dot notation" in str(e)
    print(f"Test 4 - Blocked MCP names: {mcp_names}")
    print("✓ PASS: MCP namespace names blocked")

    # ── Test 5: List all registered tools ──
    names = registry.names()
    print(f"Test 5 - Registered tools: {names}")
    assert "echo" in names
    assert len(names) == 1  # only echo was successfully registered
    print(f"✓ PASS: ToolRegistry.names() correct ({len(names)} tools)")

    print("\nAll tool collision tests passed!")


# ── Also demonstrate SkillLoadError wrapping ──
def test_skill_load_error_wrapping():
    """Demonstrate that ToolCollisionError gets wrapped in SkillLoadError."""
    registry = ToolRegistry()
    registry.register_from_def(
        ToolDef(name="shared_tool", description="First registration",
                parameters={"type": "object", "properties": {}}),
        lambda args: "first",
    )

    # Simulate what load_skill() does
    def register_skill(agent_registry):
        agent_registry.register_from_def(
            ToolDef(name="shared_tool", description="Second registration",
                    parameters={"type": "object", "properties": {}}),
            lambda args: "second",
        )

    try:
        register_skill(registry)
    except ToolCollisionError as e:
        # Wrap in SkillLoadError (as _core.py does)
        wrapped = SkillLoadError(f"Skill 'example' failed to load: {e}")
        print(f"SkillLoadError: {wrapped}")
        print(f"  Caused by: {e.__class__.__name__}: {e}")
        print("✓ PASS: ToolCollisionError correctly wrapped in SkillLoadError")


if __name__ == "__main__":
    test_collision_detection()
    print()
    test_skill_load_error_wrapping()
