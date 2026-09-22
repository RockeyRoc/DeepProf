"""Tool / Skill 注册表与沙箱策略。"""

from __future__ import annotations

import pytest

from runtime.core.errors import RuntimeFailure
from runtime.sandbox.policy import SandboxPolicy
from runtime.skills import FunctionSkill, SkillRegistry
from runtime.tools.base import FunctionTool
from runtime.tools.registry import ToolRegistry


def make_tool(requires_approval: bool = False) -> FunctionTool:
    async def handler(arguments, ctx):
        return {"content": f"ok:{arguments['q']}"}

    return FunctionTool(
        name="retrieve_evidence",
        handler=handler,
        description="检索教材",
        parameters={
            "type": "object",
            "properties": {"q": {"type": "string"}},
            "required": ["q"],
        },
        requires_approval=requires_approval,
    )


async def test_tool_executes_with_valid_arguments():
    registry = ToolRegistry()
    registry.register(make_tool())
    assert (await registry.call("retrieve_evidence", {"q": "极限"}, {}))["content"] == "ok:极限"


async def test_unknown_tool_raises_structured_error():
    registry = ToolRegistry()
    with pytest.raises(RuntimeFailure) as excinfo:
        await registry.call("ghost", {}, {})
    assert excinfo.value.details["kind"] == "tool_not_found"
    assert excinfo.value.details["registered"] == []


async def test_missing_required_argument_is_rejected():
    registry = ToolRegistry()
    registry.register(make_tool())
    with pytest.raises(RuntimeFailure) as excinfo:
        await registry.call("retrieve_evidence", {}, {})
    assert excinfo.value.details["missing_fields"] == ["q"]


async def test_approval_required_blocks_execution():
    registry = ToolRegistry()
    registry.register(make_tool(requires_approval=True))
    with pytest.raises(RuntimeFailure) as excinfo:
        await registry.call("retrieve_evidence", {"q": "x"}, {})
    assert excinfo.value.details["kind"] == "approval_required"


async def test_approval_unblocks_execution():
    registry = ToolRegistry()
    registry.register(make_tool(requires_approval=True))
    result = await registry.call("retrieve_evidence", {"q": "x"}, {"approved_tools": ["retrieve_evidence"]})
    assert result["content"] == "ok:x"


def test_tool_schemas_are_openai_shaped():
    registry = ToolRegistry()
    registry.register(make_tool())
    schema = registry.schemas()[0]
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "retrieve_evidence"


# ---- Skills ----

async def test_skill_registry_invokes_by_name():
    registry = SkillRegistry()
    registry.register(FunctionSkill("socratic", lambda input, ctx: {"content": "追问"}, description="苏格拉底"))
    assert (await registry.invoke("socratic", {}, {}))["content"] == "追问"
    assert registry.names() == ["socratic"]


async def test_missing_skill_reports_registered_names():
    registry = SkillRegistry()
    with pytest.raises(RuntimeFailure) as excinfo:
        await registry.invoke("ghost", {}, {})
    assert excinfo.value.details["kind"] == "skill_not_found"


# ---- 沙箱 ----

def test_sandbox_allows_paths_under_home(isolated_home):
    from config.settings import Settings

    policy = SandboxPolicy.from_settings(Settings())
    assert policy.is_path_allowed(isolated_home / "library" / "a.pdf") is True


def test_sandbox_denies_paths_outside_allowlist(tmp_path, isolated_home):
    from config.settings import Settings

    policy = SandboxPolicy.from_settings(Settings())
    outside = tmp_path / "elsewhere"
    assert policy.is_path_allowed(outside) is False
    with pytest.raises(RuntimeFailure) as excinfo:
        policy.check_path(outside)
    assert excinfo.value.details["kind"] == "sandbox_denied"


def test_sandbox_domain_matching():
    policy = SandboxPolicy(allowed_dirs=[], allowed_domains=["example.edu"])
    assert policy.is_domain_allowed("example.edu") is True
    assert policy.is_domain_allowed("lib.example.edu") is True
    assert policy.is_domain_allowed("evil.com") is False