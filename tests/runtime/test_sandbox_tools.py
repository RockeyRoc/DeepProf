"""Sandbox 与 Tool 权限/审批测试（§5.3 / D-5 / §13.3）。"""
from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from runtime.core.errors import PermissionDenied
from runtime.core.events import EventBus, EventType
from runtime.sandbox.policy import (
    PERM_FS_WRITE,
    PERM_NETWORK,
    ApprovalGate,
    SandboxPolicy,
)
from runtime.storage.base import InMemoryEventStore
from runtime.tools.base import FunctionTool, ToolContext, ToolResult
from runtime.tools.registry import ToolRegistry


class EchoInput(BaseModel):
    text: str = Field(min_length=1, description="要回显的文本")


async def _echo(arguments: EchoInput, ctx: ToolContext) -> ToolResult:
    return ToolResult.success(f"echo: {arguments.text}", text=arguments.text)


@pytest.fixture
def bus():
    return EventBus(store=InMemoryEventStore())


def _registry(sandbox: SandboxPolicy, bus: EventBus, approval: ApprovalGate | None = None):
    registry = ToolRegistry(sandbox=sandbox, bus=bus, approval=approval)
    registry.register(
        FunctionTool(
            name="echo",
            description="回显文本",
            input_model=EchoInput,
            handler=_echo,
        )
    )
    return registry


# ---------- Sandbox 路径 ----------
def test_sandbox_allows_whitelisted_root(sandbox):
    assert sandbox.is_allowed("data/course/ch1.pdf")
    assert sandbox.resolve("data/course/ch1.pdf").name == "ch1.pdf"


def test_sandbox_denies_outside_whitelist(sandbox):
    """默认拒绝越界：项目根、系统目录都不可写。"""
    assert not sandbox.is_allowed("DESIGN.md")
    with pytest.raises(PermissionDenied) as exc:
        sandbox.resolve("DESIGN.md", write=True)
    assert exc.value.code == "permission_denied"


def test_sandbox_denies_network_and_process_by_default():
    """网络与进程默认关闭（D-5 演示版）。"""
    policy = SandboxPolicy()
    with pytest.raises(PermissionDenied):
        policy.authorize({PERM_NETWORK})
    policy.authorize({PERM_FS_WRITE})  # 文件权限本身不需要额外开关


def test_sandbox_rejects_unknown_permission():
    with pytest.raises(PermissionDenied):
        SandboxPolicy().authorize({"super.user"})


# ---------- Tool 注册与执行 ----------
async def test_tool_schema_is_openai_compatible(sandbox, bus):
    """工具 schema 必须能直接交给模型使用。"""
    registry = _registry(sandbox, bus)
    schema = registry.schemas()[0]
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "echo"
    assert "text" in schema["function"]["parameters"]["properties"]


async def test_tool_execute_success_writes_audit_events(sandbox, bus):
    """成功路径要有 requested → started → completed 审计链。"""
    registry = _registry(sandbox, bus)
    result = await registry.execute(
        "echo", {"text": "你好"}, ToolContext(session_id="s1", trace_id="t1")
    )

    assert result.ok and result.data["text"] == "你好"
    types = [event.type for event in bus.replay("s1")]
    assert types == [
        EventType.TOOL_REQUESTED.value,
        EventType.TOOL_STARTED.value,
        EventType.TOOL_COMPLETED.value,
    ]
    assert all(event.trace_id == "t1" for event in bus.replay("s1"))


async def test_tool_unknown_returns_structured_failure(sandbox, bus):
    """模型可能幻觉出不存在的工具名，必须返回结构化失败而不是抛异常。"""
    registry = _registry(sandbox, bus)
    result = await registry.execute("not_exists", {}, ToolContext(session_id="s1"))

    assert not result.ok
    assert result.error["code"] == "tool_not_found"
    assert EventType.TOOL_FAILED.value in [e.type for e in bus.replay("s1")]


async def test_tool_validation_error_is_reported(sandbox, bus):
    """参数不合法时给出校验错误，让教学图能解释限制。"""
    registry = _registry(sandbox, bus)
    result = await registry.execute("echo", {}, ToolContext(session_id="s1"))

    assert not result.ok
    assert result.error["code"] == "tool_invalid_arguments"


async def test_tool_requiring_approval_is_blocked_then_allowed(sandbox, bus):
    """高风险工具默认被拒，显式审批后才执行（§13.3）。"""
    approval = ApprovalGate()
    registry = _registry(sandbox, bus, approval)
    registry.register(
        FunctionTool(
            name="danger",
            description="高风险操作",
            input_model=EchoInput,
            handler=_echo,
            requires_approval=True,
        )
    )

    blocked = await registry.execute("danger", {"text": "x"}, ToolContext(session_id="s1"))
    assert not blocked.ok
    assert blocked.error["code"] == "tool_approval_required"

    approval.approve("danger")
    allowed = await registry.execute("danger", {"text": "x"}, ToolContext(session_id="s1"))
    assert allowed.ok
    assert EventType.TOOL_APPROVED.value in [e.type for e in bus.replay("s1")]


async def test_tool_permission_is_denied_by_sandbox(bus):
    """工具声明的权限超出 Sandbox 授权范围时拒绝执行。"""
    sandbox = SandboxPolicy()  # 未开放网络
    registry = ToolRegistry(sandbox=sandbox, bus=bus)
    registry.register(
        FunctionTool(
            name="fetch",
            description="联网抓取",
            input_model=EchoInput,
            handler=_echo,
            permissions={PERM_NETWORK},
        )
    )

    result = await registry.execute("fetch", {"text": "x"}, ToolContext(session_id="s1"))
    assert not result.ok
    assert result.error["code"] == "permission_denied"


async def test_tool_internal_exception_is_normalized(sandbox, bus):
    """工具内部异常也要转成结构化失败，避免打断整轮对话。"""

    async def _boom(arguments: EchoInput, ctx: ToolContext) -> ToolResult:
        raise ValueError("内部错误")

    registry = ToolRegistry(sandbox=sandbox, bus=bus)
    registry.register(
        FunctionTool(name="boom", description="会抛错", input_model=EchoInput, handler=_boom)
    )

    result = await registry.execute("boom", {"text": "x"}, ToolContext(session_id="s1"))
    assert not result.ok
    assert result.error["code"] == "tool_execution_failed"


def test_registry_rejects_duplicate_tool_name(sandbox, bus):
    registry = _registry(sandbox, bus)
    with pytest.raises(ValueError):
        registry.register(
            FunctionTool(name="echo", description="重复", input_model=EchoInput, handler=_echo)
        )