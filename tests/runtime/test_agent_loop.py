"""Agent 循环：流式、工具轮次、截断处理与事件轨迹。"""

from __future__ import annotations

import pytest

from runtime.core.agent import Agent
from runtime.core.errors import KIND_MODEL_TRUNCATED, ProviderError
from runtime.core.events import EventType
from runtime.core.message import Message
from runtime.core.session import InMemorySessionStore
from runtime.testing import RecordingHost, make_service


async def run_agent(service, request, ctx):
    agent = Agent(service, service.router, service.settings)
    return [frame async for frame in agent.run(request, ctx)]


def tool_call_step(name: str, arguments: dict, call_id: str = "call_1") -> dict:
    return {"content": "", "tool_calls": [{"id": call_id, "name": name, "arguments": arguments}], "finish_reason": "tool_calls"}


async def test_streaming_deltas_and_result():
    service = make_service(script=[{"content": "你好，同学"}])
    frames = await run_agent(service, {"messages": [{"role": "user", "content": "hi"}]}, {"session_id": "s1"})

    # 增量按块下发，拼接后应等于完整正文
    assert "".join(f["text"] for f in frames if f["type"] == "delta") == "你好，同学"
    result = frames[-1]
    assert result["type"] == "result"
    assert result["message"]["content"] == "你好，同学"
    assert result["status"] == "success"


async def test_events_are_written_in_order_with_trace_id():
    service = make_service(script=[{"content": "abc"}])
    await run_agent(service, {"messages": []}, {"session_id": "s1", "trace_id": "trc-9"})

    types = [event.type for event in service.events.replay("s1")]
    assert EventType.AGENT_STARTED.value in types
    assert EventType.MODEL_REQUESTED.value in types
    assert EventType.MODEL_STREAM_DELTA.value in types
    assert EventType.MODEL_COMPLETED.value in types
    assert types[-1] == EventType.AGENT_TURN_COMPLETED.value

    sequences = [event.sequence for event in service.events.replay("s1")]
    assert sequences == sorted(sequences)
    assert all(event.trace_id == "trc-9" for event in service.events.replay("s1"))


async def test_model_requested_records_profile_and_model_without_secret():
    service = make_service(script=[{"content": "x"}])
    await run_agent(service, {"messages": []}, {"session_id": "s1"})
    requested = [e for e in service.events.replay("s1") if e.type == EventType.MODEL_REQUESTED.value]
    payload = requested[0].payload
    assert set(payload) == {"role", "provider_profile", "model"}
    assert "key" not in repr(payload).lower()


async def test_truncated_empty_output_returns_structured_error():
    """空正文 + finish=length 必须返回 model_truncated，且不污染上下文。"""
    service = make_service(script=[{"content": "", "finish_reason": "length"}])
    frames = await run_agent(service, {"messages": []}, {"session_id": "s1"})

    error = frames[-1]["error"]
    assert error["code"] == KIND_MODEL_TRUNCATED
    assert error["details"]["kind"] == KIND_MODEL_TRUNCATED
    assert not any(frame["type"] == "result" for frame in frames)

    types = [event.type for event in service.events.replay("s1")]
    assert EventType.MODEL_FAILED.value in types
    assert types[-1] == EventType.AGENT_FAILED.value


async def test_empty_output_without_truncation_is_not_an_error():
    service = make_service(script=[{"content": ""}])
    frames = await run_agent(service, {"messages": []}, {"session_id": "s1"})
    assert frames[-1]["type"] == "result"
    assert frames[-1]["message"]["content"] == ""


async def test_provider_failure_is_reported_as_error_frames():
    service = make_service(script=[ProviderError("upstream down", kind="upstream_error")])
    frames = await run_agent(service, {"messages": []}, {"session_id": "s1"})
    assert frames[-1]["type"] == "error"
    assert frames[-1]["error"]["details"]["kind"] == "upstream_error"
    assert service.events.replay("s1")[-1].type == EventType.AGENT_FAILED.value


async def test_tool_call_round_trip_emits_tool_events():
    service = make_service(
        script=[
            tool_call_step("retrieve_evidence", {"q": "极限"}),
            {"content": "根据教材……"},
        ]
    )

    async def handler(arguments, ctx):
        return {"content": "命中 1 条", "evidence": [{"document_id": "d1", "page": 3}]}

    from runtime.tools.base import FunctionTool

    service.tools.register(
        FunctionTool(
            name="retrieve_evidence",
            handler=handler,
            parameters={"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
        )
    )

    frames = await run_agent(
        service, {"messages": [{"role": "user", "content": "什么是极限"}], "tools": []}, {"session_id": "s1"}
    )
    assert frames[-1]["message"]["content"] == "根据教材……"

    types = [event.type for event in service.events.replay("s1")]
    for expected in (
        EventType.TOOL_REQUESTED.value,
        EventType.TOOL_STARTED.value,
        EventType.TOOL_COMPLETED.value,
        EventType.AGENT_TURN_COMPLETED.value,
    ):
        assert expected in types


async def test_tool_failure_is_structured_and_loop_continues():
    service = make_service(
        script=[tool_call_step("ghost_tool", {}), {"content": "换个方式回答"}]
    )
    frames = await run_agent(service, {"messages": []}, {"session_id": "s1"})

    types = [event.type for event in service.events.replay("s1")]
    assert EventType.TOOL_FAILED.value in types
    assert frames[-1]["type"] == "result"


async def test_session_message_round_trip_after_turn():
    """一轮结束后助手消息应可落库并恢复。"""
    store = InMemorySessionStore()
    service = make_service(script=[{"content": "答复"}])
    service.sessions = store
    session = service.new_session()
    session.append(Message(role="user", content="问题"))

    frames = await run_agent(
        service,
        {"messages": [m.to_dict() for m in session.context()]},
        {"session_id": session.session_id},
    )
    session.append(Message.from_dict(frames[-1]["message"]))
    service.save_session(session)

    restored = store.load(session.session_id)
    assert restored is not None
    assert [m.content for m in restored.messages] == ["问题", "答复"]

async def test_guarded_generation_keeps_frames_internal_but_drops_delta_events():
    service = make_service(script=[{"content": "答案是线性表。你明白了吗？"}])
    frames = [
        frame async for frame in service.generate(
            {"role": "tutor.default", "messages": []},
            {"session_id": "guarded", "trace_id": "guarded-trace", "suppress_user_stream": True},
        )
    ]
    assert "".join(str(frame.get("text") or "") for frame in frames if frame.get("type") == "delta")
    events = service.events.replay("guarded")
    assert EventType.MODEL_COMPLETED.value in [event.type for event in events]
    assert EventType.MODEL_STREAM_DELTA.value not in [event.type for event in events]
