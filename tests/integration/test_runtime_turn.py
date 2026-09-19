"""RuntimeService 端到端测试（§16.1 负责人验收项）。

验收基准：
1. 一条带 trace_id 的请求完成 模型→工具→事件 闭环；
2. 重启可恢复（会话与轨迹都能从 SQLite 读回）；
3. 失败与取消可观测；
4. describe() 等自述接口不泄露密钥。
"""
from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import BaseModel, Field

from config import settings
from runtime.core.events import EventType
from runtime.providers.base import ModelChunk, ModelRequest, Provider, ProviderCapabilities
from runtime.providers.fake import FakeProvider
from runtime.providers.openai_compat import OpenAICompatProvider
from runtime.sandbox.policy import SandboxPolicy
from runtime.service import RuntimeService
from runtime.tools.base import FunctionTool, ToolResult


class EchoInput(BaseModel):
    text: str = Field(min_length=1)


async def _echo(arguments: EchoInput, ctx) -> ToolResult:
    return ToolResult.success(f"echo: {arguments.text}", text=arguments.text)


class HangingProvider(Provider):
    """产出第一个增量后一直挂起，用于验证"取消可观测"。"""

    name = "hanging"

    def __init__(self) -> None:
        self.started = asyncio.Event()

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(name=self.name)

    async def generate(self, request: ModelRequest):
        return await self.stream(request).__anext__()  # pragma: no cover

    async def stream(self, request: ModelRequest):
        self.started.set()
        yield ModelChunk(delta="先说一半")
        await asyncio.sleep(3600)


class BoomProvider(Provider):
    name = "boom"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(name=self.name)

    async def generate(self, request: ModelRequest):
        from runtime.core.errors import ProviderError

        raise ProviderError("模型服务不可用", provider=self.name)

    async def stream(self, request: ModelRequest):
        from runtime.core.errors import ProviderError

        raise ProviderError("模型服务不可用", provider=self.name)
        yield  # pragma: no cover - 仅为让它成为异步生成器


def _service(db, provider, **overrides) -> RuntimeService:
    service = RuntimeService(
        provider=provider,
        db=db,
        sandbox=SandboxPolicy.from_settings(),
        **overrides,
    )
    service.register_tool(
        FunctionTool(name="echo", description="回显文本", input_model=EchoInput, handler=_echo)
    )
    return service


async def _drain(agen) -> list:
    return [chunk async for chunk in agen]


# ---------- 1. 模型 → 工具 → 事件 闭环 ----------
async def test_run_turn_completes_model_tool_event_loop(db):
    provider = FakeProvider(
        script=[
            {
                "content": "",
                "tool_calls": [{"name": "echo", "arguments": {"text": "递归"}, "id": "call_1"}],
            },
            {"content": "我们用递归来理解回显结果：echo: 递归"},
        ]
    )
    service = _service(db, provider)
    session = service.create_session(learner_id="learner_a")

    chunks = await _drain(service.run_turn(session, "请回显 递归", trace_id="trace_1"))

    assert chunks[-1].kind == "done"
    assert session.messages[-1].content.startswith("我们用递归")
    # 会话落盘：新连接读回的消息数与内存一致
    assert len(service.session_store.load(session.session_id).messages) == len(session.messages)

    events = service.events_by_trace("trace_1")
    types = [event.type for event in events]
    for expected in (
        EventType.AGENT_STARTED.value,
        EventType.MODEL_REQUESTED.value,
        EventType.TOOL_REQUESTED.value,
        EventType.TOOL_STARTED.value,
        EventType.TOOL_COMPLETED.value,
        EventType.MODEL_COMPLETED.value,
        EventType.AGENT_TURN_COMPLETED.value,
    ):
        assert expected in types
    assert types.count(EventType.MODEL_REQUESTED.value) == 2
    assert all(event.trace_id == "trace_1" for event in events)
    # sequence 在同一会话内单调递增，保证可回放
    sequences = [event.sequence for event in service.replay(session.session_id)]
    assert sequences == sorted(sequences) and len(set(sequences)) == len(sequences)


# ---------- 2. 重启可恢复 ----------
async def test_session_and_trace_survive_restart(db):
    provider = FakeProvider(script=[{"content": "先画一个状态转移图"}])
    service = _service(db, provider)
    session = service.create_session(learner_id="learner_a")
    await _drain(service.run_turn(session, "SQL 注入怎么防", trace_id="trace_restart"))

    # 模拟进程重启：同一个库文件上重新装配 Runtime
    restarted = _service(db, FakeProvider())
    resumed = restarted.open_session(session.session_id)

    assert resumed.learner_id == "learner_a"
    assert [m.content for m in resumed.messages if m.role == "user"] == ["SQL 注入怎么防"]
    assert resumed.messages[-1].content == "先画一个状态转移图"
    # 轨迹也能恢复，且包含重启前的那次请求
    assert restarted.events_by_trace("trace_restart")
    assert any(
        event.type == EventType.SESSION_RESUMED.value
        for event in restarted.replay(session.session_id)
    )


async def test_replay_after_disconnect_resumes_from_sequence(db):
    """SSE 断线重连：按 from_sequence 增量回放，不重复推送。"""
    service = _service(db, FakeProvider(script=[{"content": "回答"} for _ in range(2)]))
    session = service.create_session(learner_id="learner_a")
    await _drain(service.run_turn(session, "第一问", trace_id="trace_a"))

    all_events = list(service.replay(session.session_id))
    assert all_events[:2][0].type == EventType.SESSION_STARTED.value
    last_seq = all_events[3].sequence
    tail = list(service.replay(session.session_id, from_sequence=last_seq))

    assert tail and all(event.sequence > last_seq for event in tail)
    assert [event.event_id for event in tail] == [
        event.event_id for event in all_events[4:]
    ]


# ---------- 3. 失败与取消可观测 ----------
async def test_failure_is_observable(db):
    service = _service(db, BoomProvider())
    session = service.create_session(learner_id="learner_a")

    chunks = await _drain(service.run_turn(session, "在吗", trace_id="trace_fail"))

    assert chunks[-1].kind == "error"
    assert chunks[-1].error["code"] == "provider_failed"
    types = [event.type for event in service.events_by_trace("trace_fail")]
    assert EventType.MODEL_FAILED.value in types
    assert EventType.AGENT_FAILED.value in types
    # 失败也要落盘，便于复盘
    assert service.session_store.load(session.session_id) is not None


async def test_generate_port_reports_truncated_empty_answer(db):
    """教学图链路（RuntimePort.generate）同样不能把截断空答案当成 done（§16.1）。

    能力层经 runtime/capabilities.py 的 collect_stream 消费本端口，
    只有 ``error`` 才能让它按失败处理；
    若这里照常返回 ``done``，讲解能力会把空串当成正常讲解交给学生。
    """
    provider = FakeProvider(script=[{"content": "", "finish_reason": "length"}])
    service = _service(db, provider)
    session = service.create_session(learner_id="learner_a")
    ctx = {"session_id": session.session_id, "trace_id": "trace_truncated"}

    chunks = await _drain(
        service.generate({"messages": [{"role": "user", "content": "什么是递归"}]}, ctx)
    )

    assert chunks[-1]["type"] == "error"
    assert chunks[-1]["error"]["code"] == "model_truncated"
    assert not any(chunk.get("type") == "done" for chunk in chunks)
    # 轨迹保留事实：模型确实返回过，只是 finish_reason=length 且正文为空
    types = [event.type for event in service.events_by_trace("trace_truncated")]
    assert EventType.MODEL_COMPLETED.value in types


async def test_cancellation_is_observable(db):
    provider = HangingProvider()
    service = _service(db, provider)
    session = service.create_session(learner_id="learner_a")

    received: list = []

    async def consume() -> None:
        async for chunk in service.run_turn(session, "慢慢说", trace_id="trace_cancel"):
            received.append(chunk)

    task = asyncio.create_task(consume())
    await asyncio.wait_for(provider.started.wait(), timeout=5)
    for _ in range(200):  # 等第一个增量被消费，再中断
        if received:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert "".join(chunk.text for chunk in received) == "先说一半"
    types = [event.type for event in service.events_by_trace("trace_cancel")]
    assert EventType.AGENT_CANCELLED.value in types
    # 取消后会话仍然落盘，用户回来能看到自己说过什么
    assert service.session_store.load(session.session_id) is not None


# ---------- 4. 自述接口不泄露密钥 ----------
async def test_describe_exposes_capabilities_without_secrets(db):
    secret = "sk-secret-sentinel-1234567890"
    provider = OpenAICompatProvider(
        api_key=secret, base_url="https://api.deepseek.com", model="deepseek-chat", name="deepseek"
    )
    service = _service(db, provider)

    snapshot = service.describe()
    dumped = json.dumps(snapshot, ensure_ascii=False, default=str)

    assert secret not in dumped
    assert snapshot["provider"] == "deepseek"
    assert "echo" in snapshot["tools"]
    assert snapshot["sandbox"]["allowed_roots"]
    assert settings.runtime_source == "deepprof.runtime"