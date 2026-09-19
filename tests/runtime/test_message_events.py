"""Message 与 Event 契约测试（§5.1 / §5.4）。"""
from __future__ import annotations

import asyncio

from runtime.core.events import EventBus, EventType, RuntimeEvent, redact
from runtime.core.message import Message, Role, ToolCall
from runtime.storage.base import InMemoryEventStore


# ---------- Message ----------
def test_message_defaults_and_roundtrip():
    """消息应自动补全 id/时间，并能无损序列化往返。"""
    message = Message(Role.USER, "什么是递归？")
    assert message.message_id.startswith("msg_")
    assert message.created_at

    restored = Message.from_dict(message.to_dict())
    assert restored.role == Role.USER
    assert restored.content == "什么是递归？"
    assert restored.message_id == message.message_id


def test_message_role_accepts_plain_string():
    """允许直接用字符串构造，内部统一成枚举。"""
    assert Message("assistant", "ok").role == Role.ASSISTANT


def test_message_provider_dict_carries_tool_calls():
    """OpenAI 兼容协议要求 arguments 为 JSON 字符串。"""
    call = ToolCall(name="search_textbook", arguments={"query": "递归", "top_k": 3}, id="call_1")
    message = Message.assistant("", [call])
    payload = message.to_provider_dict()

    assert payload["role"] == "assistant"
    assert payload["tool_calls"][0]["id"] == "call_1"
    assert payload["tool_calls"][0]["function"]["name"] == "search_textbook"
    assert '"query"' in payload["tool_calls"][0]["function"]["arguments"]


def test_tool_message_carries_call_id():
    """tool 消息必须能回到对应的调用，否则模型无法关联结果。"""
    message = Message.tool("检索结果", "call_1", "search_textbook")
    assert message.to_provider_dict()["tool_call_id"] == "call_1"


# ---------- 脱敏 ----------
def test_redact_masks_sensitive_fields_recursively():
    """敏感字段落盘前必须脱敏（§13.2）。"""
    payload = {
        "api_key": "sk-real-key",
        "nested": {"authorization": "Bearer x", "text": "正常内容"},
        "items": [{"token": "abc"}],
    }
    cleaned = redact(payload)
    assert cleaned["api_key"] == "***redacted***"
    assert cleaned["nested"]["authorization"] == "***redacted***"
    assert cleaned["nested"]["text"] == "正常内容"
    assert cleaned["items"][0]["token"] == "***redacted***"


# ---------- Event ----------
def test_event_roundtrip_and_defaults():
    event = RuntimeEvent(EventType.MODEL_COMPLETED.value, {"model": "fake"})
    assert event.event_id.startswith("evt_")
    assert event.timestamp
    restored = RuntimeEvent.from_dict(event.to_dict())
    assert restored.type == "model.completed"
    assert restored.payload == {"model": "fake"}


def test_event_bus_assigns_sequence_and_redacts():
    """发布事件时应落盘、分配递增 sequence，并脱敏。"""
    store = InMemoryEventStore()
    bus = EventBus(store=store)
    first = bus.emit(EventType.SESSION_STARTED, {"api_key": "sk-x"}, session_id="s1")
    second = bus.emit(EventType.AGENT_STARTED, {}, session_id="s1")

    assert first.sequence == 1
    assert second.sequence == 2
    assert first.payload["api_key"] == "***redacted***"


def test_event_store_is_idempotent_by_event_id():
    """同一 event_id 重复写入不得产生第二条记录（§18.2 幂等要求）。"""
    store = InMemoryEventStore()
    event = RuntimeEvent(EventType.TOOL_COMPLETED.value, {}, session_id="s1")
    stored = store.append(event)
    again = store.append(event)

    assert stored.sequence == again.sequence
    assert len(store.list("s1")) == 1


def test_event_bus_replay_supports_reconnect():
    """流式重连按 sequence 恢复，避免重复渲染。"""
    store = InMemoryEventStore()
    bus = EventBus(store=store)
    for index in range(3):
        bus.emit(EventType.MODEL_STREAM_DELTA, {"text": str(index)}, session_id="s1")

    replayed = list(bus.replay("s1", from_sequence=1))
    assert [event.payload["text"] for event in replayed] == ["1", "2"]


def test_event_bus_subscribe_and_unsubscribe():
    received: list[str] = []
    bus = EventBus()
    unsubscribe = bus.subscribe(lambda event: received.append(event.type))
    bus.emit(EventType.SESSION_ENDED, {}, session_id="s1")
    unsubscribe()
    bus.emit(EventType.SESSION_ENDED, {}, session_id="s1")

    assert received == ["session.ended"]


async def test_event_bus_stream_filters_by_session():
    """SSE 订阅只应收到目标会话的事件。"""
    bus = EventBus()
    stream = bus.open_stream("s1")
    bus.emit(EventType.AGENT_STARTED, {}, session_id="s2")
    bus.emit(EventType.AGENT_STARTED, {}, session_id="s1")
    bus.close_stream(stream)

    received = [event async for event in stream]
    assert len(received) == 1
    assert received[0].session_id == "s1"


def test_stream_subscription_close_is_idempotent():
    """重复 close 不应抛错（API 断开连接时会调用）。"""
    bus = EventBus()
    stream = bus.open_stream("s1")
    bus.close_stream(stream)
    bus.close_stream(stream)

    async def consume() -> list:
        return [event async for event in stream]

    assert asyncio.run(consume()) == []