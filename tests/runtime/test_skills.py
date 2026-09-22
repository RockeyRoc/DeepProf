"""Skill 注册表契约：宽面只交给声明需要的 Skill（DESIGNv0.6 §4.4 / §6.4）。"""

from __future__ import annotations

import pytest

from runtime.core.errors import RuntimeFailure
from runtime.skills import FunctionSkill, SkillRegistry
from runtime.testing import RecordingHost


class HostedEcho:
    """声明需要宽面的 Skill：拿到 host 才能调模型/工具/记忆。"""

    uses_host = True
    name = "hosted"
    description = "需要宽面"

    async def invoke(self, input, ctx, host):
        return {"status": "ok", "has_host": host is not None, "concept": input.get("concept", "")}


async def test_hosted_skill_receives_runtime_host():
    registry = SkillRegistry()
    registry.register(HostedEcho())
    host = RecordingHost()

    result = await registry.invoke("hosted", {"concept": "极限"}, {}, host=host)

    assert result["has_host"] is True
    assert result["concept"] == "极限"


async def test_hosted_skill_without_host_fails_explicitly():
    """声明了宽面却没人给 host：必须显式失败，不能悄悄降级成“没有能力”。"""
    registry = SkillRegistry()
    registry.register(HostedEcho())

    with pytest.raises(RuntimeFailure) as excinfo:
        await registry.invoke("hosted", {}, {})

    assert excinfo.value.details["kind"] == "capability_missing"


async def test_plain_skill_is_called_without_host():
    """不需要宽面的 Skill 保持两参数签名，避免把宽面扩散给不该拿到的实现。"""
    registry = SkillRegistry()
    registry.register(FunctionSkill("plain", lambda payload, ctx: {"status": "ok", "echo": payload}))
    host = RecordingHost()

    result = await registry.invoke("plain", {"a": 1}, {}, host=host)

    assert result["echo"] == {"a": 1}


async def test_unknown_skill_is_structured_error():
    registry = SkillRegistry()
    with pytest.raises(RuntimeFailure) as excinfo:
        await registry.invoke("ghost", {}, {})
    assert excinfo.value.details["kind"] == "skill_not_found"
    assert excinfo.value.details["registered"] == []