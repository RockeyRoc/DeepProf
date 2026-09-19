"""Agent loop 测试：模型 → 工具 → 事件闭环（MVP-0/1 验收项 §16.1）。"""
from __future__ import annotations

from pydantic import BaseModel, Field

from runtime.core.agent import Agent
from runtime.core.events import EventBus, EventType
from runtime.core.message import Role
from runtime.core.ports import RuntimeContext
from runtime.core.session import Session
from runtime.providers.base import ModelRequest, Provider, ProviderCapabilities
from runtime.providers.fake import FakeProvider
from runtime.sandbox.policy import SandboxPolicy
from runtime.storage.base import InMemoryEventStore
from runtime.tools.base import FunctionTool, ToolResult
from runtime.tools.registry import ToolRegistry


class EchoInput(BaseModel):
    text: str = Field(min_length=1)


async def _echo(arguments: EchoInput, ctx) -> ToolResult:
    return ToolResult.success(f"echo: {arguments.text}", text=arguments.text)


class BoomProvider(Provider):
    """永远失败的 Provider，用于验证失败可观测（§7.3）。"""

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


def _build(provider, bus, *, max_turns: int = 8):
    registry = ToolRegistry(sandbox=SandboxPolicy(), bus=bus)
    registry.register(
        FunctionTool(name="echo", description="回显文本", input_model=EchoInput, handler=_echo)
    )
    return Agent(provider, registry, bus, sandbox=SandboxPolicy(), max_turns=max_turns)


def _context(session: Session, trace_id: str = "trace_abc") -> RuntimeContext:
    return RuntimeContext(
        session_id=session.session_id, learner_id=session.learner_id, trace_id=trace_id
    )


async def _collect(agent, session, ctx, **kwargs):
    return [chunk async for chunk in agent.run(session, ctx, **kwargs)]


async def test_agent_completes_model_tool_event_loop():
    """一条带 trace_id 的请求完成 模型→工具→事件 闭环。"""
    bus = EventBus(store=InMemoryEventStore())
    provider = FakeProvider(
        script=[
            {
                "content": "",
                "tool_calls": [{"name": "echo", "arguments": {"text": "你好"}, "id": "call_1"}],
            },
            {"content": "回显完成：你好"},
        ]
    )
    agent = _build(provider, bus)
    session = Session(learner_id="learner_a")
    ctx = _context(session)

    chunks = await _collect(agent, session, ctx, user_input="帮我回显 你好")

    # 1) 流式输出顺序：工具结果在前，最终答案在后
    kinds = [chunk.kind for chunk in chunks]
    assert "tool" in kinds
    assert kinds[-1] == "done"
    tool_chunk = next(chunk for chunk in chunks if chunk.kind == "tool")
    assert tool_chunk.tool_name == "echo" and tool_chunk.tool_ok
    assert chunks[-1].message.content == "回显完成：你好"

    # 2) 会话消息结构：user → assistant(带 tool_calls) → tool → assistant
    assert [m.role for m in session.messages] == [
        Role.USER,
        Role.ASSISTANT,
        Role.TOOL,
        Role.ASSISTANT,
    ]
    assert session.messages[1].tool_calls[0].name == "echo"

    # 3) 事件轨迹完整且都挂同一个 trace_id
    events = bus.replay(session.session_id)
    types = [event.type for event in events]
    for expected in (
        EventType.AGENT_STARTED.value,
        EventType.MODEL_REQUESTED.value,
        EventType.TOOL_REQUESTED.value,
        EventType.TOOL_COMPLETED.value,
        EventType.MODEL_COMPLETED.value,
        EventType.AGENT_TURN_COMPLETED.value,
    ):
        assert expected in types
    assert all(event.trace_id == "trace_abc" for event in events)
    # 两次模型往返：一次决定调用工具，一次给出答案
    assert types.count(EventType.MODEL_REQUESTED.value) == 2


async def test_agent_streams_text_deltas():
    """流式增量必须逐块产出，前端才能"边生成边显示"。"""
    bus = EventBus(store=InMemoryEventStore())
    provider = FakeProvider(script=[{"content": "苏格拉底式提问：你觉得递归的终止条件是什么？"}])
    agent = _build(provider, bus)
    session = Session(learner_id="learner_a")

    chunks = await _collect(agent, session, _context(session), user_input="什么是递归")

    deltas = "".join(chunk.text for chunk in chunks if chunk.kind == "delta")
    assert deltas == "苏格拉底式提问：你觉得递归的终止条件是什么？"
    assert any(e.type == EventType.MODEL_STREAM_DELTA.value for e in bus.replay(session.session_id))


async def test_agent_stops_at_max_turns():
    """模型反复要求工具调用时必须停下，不能无限循环（§7.3）。"""
    bus = EventBus(store=InMemoryEventStore())
    provider = FakeProvider(
        script=[
            {"tool_calls": [{"name": "echo", "arguments": {"text": str(i)}, "id": f"c{i}"}]}
            for i in range(5)
        ]
    )
    agent = _build(provider, bus, max_turns=2)
    session = Session(learner_id="learner_a")

    chunks = await _collect(agent, session, _context(session), user_input="loop")

    assert chunks[-1].kind == "error"
    assert chunks[-1].error["code"] == "agent_loop_limit"
    types = [event.type for event in bus.replay(session.session_id)]
    assert EventType.AGENT_FAILED.value in types


async def test_agent_reports_provider_failure():
    """Provider 失败要落成 model.failed / agent.failed 事件并返回错误块。"""
    bus = EventBus(store=InMemoryEventStore())
    agent = _build(BoomProvider(), bus)
    session = Session(learner_id="learner_a")

    chunks = await _collect(agent, session, _context(session), user_input="在吗")

    assert chunks[-1].kind == "error"
    assert chunks[-1].error["code"] == "provider_failed"
    types = [event.type for event in bus.replay(session.session_id)]
    assert EventType.MODEL_FAILED.value in types
    assert EventType.AGENT_FAILED.value in types


async def test_agent_injects_system_prompt_once():
    """system 提示只注入一次，重启恢复后不会重复堆叠。"""
    bus = EventBus(store=InMemoryEventStore())
    agent = _build(FakeProvider(), bus)
    session = Session(learner_id="learner_a")

    await _collect(agent, session, _context(session), user_input="第一问", system_prompt="你是 DeepProf")
    await _collect(agent, session, _context(session), user_input="第二问", system_prompt="你是 DeepProf")

    system_messages = [m for m in session.messages if m.role == Role.SYSTEM]
    assert len(system_messages) == 1


async def test_agent_can_run_without_tools():
    """纯文本轮次（如教学图自己决定不调工具）不应把工具描述塞给模型。"""
    bus = EventBus(store=InMemoryEventStore())
    provider = FakeProvider()
    agent = _build(provider, bus)
    session = Session(learner_id="learner_a")

    await _collect(agent, session, _context(session), user_input="随便聊聊", use_tools=False)

    assert provider.calls[-1].tools == []


async def test_agent_reports_truncated_empty_answer():
    """推理模型耗尽 token 预算时必须显式失败，不能静默返回空回复（§16.1）。"""
    bus = EventBus(store=InMemoryEventStore())
    provider = FakeProvider(script=[{"content": "", "finish_reason": "length"}])
    agent = _build(provider, bus)
    session = Session(learner_id="learner_a")

    chunks = await _collect(agent, session, _context(session), user_input="什么是递归")

    assert chunks[-1].kind == "error"
    assert chunks[-1].error["code"] == "model_truncated"
    types = [event.type for event in bus.replay(session.session_id)]
    assert EventType.MODEL_COMPLETED.value in types
    assert EventType.AGENT_FAILED.value in types
    # 空答案不得写进会话上下文，否则后续轮次会带着一条空 assistant 消息
    assert not any(message.role == Role.ASSISTANT for message in session.messages)