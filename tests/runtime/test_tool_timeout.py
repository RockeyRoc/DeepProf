"""工具执行超时护栏测试（DESIGNv0.4 §5.3 / §7.3）。

背景：ToolRegistry 之前直接 ``await tool.execute(...)``——异常能转结构化失败，
但**挂起**不在覆盖范围内。当时所有工具都是本地瞬时操作所以无感；真实教材
检索（向量库 HTTP）接入后，一次卡住的网络调用会把 Agent 的"模型—工具"循环
无限拖住，而它外部看起来只是"这轮没反应"。这与 Provider 修复前的 603 秒挂起
是同一类问题，因此在执行层补上同一类护栏。

要证明的命题：
1. 正常工具不受影响（护栏不能把快工具误伤）；
2. 挂起的工具在超时后被**中止**且返回结构化 ``tool_timeout``，不抛异常；
3. 超时上限由"工具自声明优先、否则全局默认"解析；
4. 超时后挂起的协程确实被取消（不是丢在后台继续跑）；
5. 取消（CancelledError）不被吞掉，SSE 的 finally 仍能正常收尾。
"""
from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel, Field

from config import settings
from runtime.core.events import EventBus, EventType
from runtime.sandbox.policy import SandboxPolicy
from runtime.storage.base import InMemoryEventStore
from runtime.tools.base import FunctionTool, Tool, ToolContext, ToolResult
from runtime.tools.registry import ToolRegistry


class SlowInput(BaseModel):
    text: str = Field(default="x", description="占位参数")


@pytest.fixture
def bus():
    return EventBus(store=InMemoryEventStore())


def _registry(bus: EventBus) -> ToolRegistry:
    return ToolRegistry(sandbox=SandboxPolicy(), bus=bus)


def _slow_tool(*, timeout_seconds: float | None = None, cancelled: list[str] | None = None):
    """构造一个永远不返回的工具；记录它是否真的被取消。"""

    async def _hang(arguments: SlowInput, ctx: ToolContext) -> ToolResult:
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            if cancelled is not None:
                cancelled.append("cancelled")
            raise
        return ToolResult.success("不该走到这里")

    return FunctionTool(
        name="hang",
        description="挂起的工具",
        input_model=SlowInput,
        handler=_hang,
        timeout_seconds=timeout_seconds,
    )


# ---------- 一、护栏不误伤正常工具 ----------
async def test_fast_tool_still_succeeds(bus):
    """快工具照常成功——护栏只对挂起生效，改动不能变成"所有工具都超时"。"""

    async def _fast(arguments: SlowInput, ctx: ToolContext) -> ToolResult:
        return ToolResult.success("ok", text=arguments.text)

    registry = _registry(bus)
    registry.register(
        FunctionTool(name="fast", description="快", input_model=SlowInput, handler=_fast)
    )

    result = await registry.execute("fast", {"text": "你好"}, ToolContext(session_id="s1"))
    assert result.ok and result.data["text"] == "你好"


async def test_fast_tool_finishes_well_under_default_timeout(bus):
    """默认上限必须远大于本地瞬时操作，不能把正常工具卡在边界上。"""
    assert settings.tool_timeout_seconds >= 5.0


# ---------- 二、超时被中止且结构化返回 ----------
async def test_hung_tool_returns_structured_timeout(bus):
    """挂起 → 结构化 tool_timeout（不是异常，也不是空结果）。"""
    registry = _registry(bus)
    registry.register(_slow_tool(timeout_seconds=0.05))

    result = await registry.execute("hang", {}, ToolContext(session_id="s1", trace_id="t1"))

    assert not result.ok
    assert result.error is not None
    assert result.error["code"] == "tool_timeout"
    assert "0.05" in result.error["message"]


async def test_timeout_is_audited_in_events(bus):
    """超时同样要有审计链：requested → started → failed，且带 timeout_seconds。"""
    registry = _registry(bus)
    registry.register(_slow_tool(timeout_seconds=0.05))

    await registry.execute("hang", {}, ToolContext(session_id="s1"))

    events = list(bus.replay("s1"))
    assert [e.type for e in events] == [
        EventType.TOOL_REQUESTED.value,
        EventType.TOOL_STARTED.value,
        EventType.TOOL_FAILED.value,
    ]
    failed = events[-1]
    assert failed.payload["error"]["code"] == "tool_timeout"
    assert failed.payload["timeout_seconds"] == 0.05
    # 没有 completed：超时不能被记成"成功"（否则轨迹看不出这轮为什么没结果）
    assert EventType.TOOL_COMPLETED.value not in [e.type for e in events]


async def test_hung_coroutine_is_actually_cancelled(bus):
    """挂起的协程必须被取消，而不是丢在后台继续跑（否则仍会泄漏连接与任务）。"""
    cancelled: list[str] = []
    registry = _registry(bus)
    registry.register(_slow_tool(timeout_seconds=0.05, cancelled=cancelled))

    await registry.execute("hang", {}, ToolContext(session_id="s1"))

    assert cancelled == ["cancelled"]


# ---------- 三、上限解析：工具自声明优先 ----------
async def test_tool_declared_timeout_overrides_settings_default(bus):
    """工具可以声明比全局更严的上限（联网检索该更快失败）。"""
    registry = _registry(bus)
    tool = _slow_tool(timeout_seconds=0.05)
    registry.register(tool)

    assert tool.effective_timeout() == 0.05


def test_default_timeout_comes_from_settings(bus):
    """未声明时落到 settings.tool_timeout_seconds（与 .env.example 保持一致）。"""
    tool = _slow_tool()
    assert tool.effective_timeout() == settings.tool_timeout_seconds


async def test_settings_default_is_honoured_when_tool_says_nothing(bus, monkeypatch):
    """改全局默认就改实际行为——证明走的确实是配置值而不是写死的数字。"""
    monkeypatch.setattr(settings, "tool_timeout_seconds", 0.05)
    registry = _registry(bus)
    registry.register(_slow_tool())  # 未声明 timeout_seconds

    result = await registry.execute("hang", {}, ToolContext(session_id="s1"))

    assert not result.ok
    assert result.error["code"] == "tool_timeout"


# ---------- 四、取消不被吞掉 ----------
async def test_cancellation_propagates_instead_of_becoming_timeout(bus):
    """外层取消（如 SSE 断开）必须向上传播，不能被误记成 tool_timeout。

    CancelledError 是 BaseException，不应落进 ``except Exception`` 分支；
    否则 SSE 的 finally 收尾会被"假装成功"的失败结果掩盖。
    """
    registry = _registry(bus)
    registry.register(_slow_tool(timeout_seconds=30))

    task = asyncio.ensure_future(
        registry.execute("hang", {}, ToolContext(session_id="s1"))
    )
    await asyncio.sleep(0.01)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


# ---------- 五、ToolTimeout 契约形状 ----------
def test_tool_timeout_is_a_declared_failure_code(bus):
    """失败码是调用方可分支的稳定标识，不能靠解析 message 判断。"""
    result = ToolResult.failure("tool_timeout", "工具超时")
    assert result.error["code"] == "tool_timeout"
    assert result.to_dict()["ok"] is False


def test_tool_base_declares_timeout_attribute():
    """基类要有 timeout_seconds，插件作者才知道可以声明（§5.3）。"""
    assert Tool.timeout_seconds is None