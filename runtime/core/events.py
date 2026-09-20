"""Runtime 事件模型与事件总线（DESIGNv0.4 §5.4）。

事件采用追加写入（append-only），每条携带
event_id / session_id / trace_id / sequence / type / payload / source / timestamp，
敏感字段在落盘前脱敏（§13.2）。
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Iterator

from .message import new_id, utc_now

if TYPE_CHECKING:  # 只用于类型标注，避免与 storage 层循环导入
    from ..storage.base import EventStore


class EventType(str, Enum):
    """最小事件集合（§5.4）。

    PLUGIN_INSTALLED / PLUGIN_UNINSTALLED 为生命周期补齐项，
    其余与设计文档一一对应。
    """

    # Session
    SESSION_STARTED = "session.started"
    SESSION_RESUMED = "session.resumed"
    SESSION_COMPACTED = "session.compacted"
    SESSION_ENDED = "session.ended"
    # Agent
    AGENT_STARTED = "agent.started"
    AGENT_TURN_STARTED = "agent.turn.started"
    AGENT_TURN_COMPLETED = "agent.turn.completed"
    AGENT_CANCELLED = "agent.cancelled"
    AGENT_FAILED = "agent.failed"
    # Model
    MODEL_REQUESTED = "model.requested"
    MODEL_STREAM_DELTA = "model.stream.delta"
    MODEL_COMPLETED = "model.completed"
    MODEL_FAILED = "model.failed"
    # Tool
    TOOL_REQUESTED = "tool.requested"
    TOOL_APPROVED = "tool.approved"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"
    # Memory
    MEMORY_READ = "memory.read"
    MEMORY_WRITE = "memory.write"
    # Plugin
    PLUGIN_INSTALLED = "plugin.installed"
    PLUGIN_APPROVED = "plugin.approved"
    PLUGIN_STARTED = "plugin.started"
    PLUGIN_FAILED = "plugin.failed"
    PLUGIN_STOPPED = "plugin.stopped"
    PLUGIN_UNINSTALLED = "plugin.uninstalled"
    # Pedagogy
    PEDAGOGY_NODE_ENTERED = "pedagogy.node.entered"
    PEDAGOGY_DECISION = "pedagogy.decision"
    PEDAGOGY_NODE_EXITED = "pedagogy.node.exited"
    # 作答事实（Attempt，§18.2 教育组 → 数据组）：随事件落盘后由数据组消费，
    # 用于 BKT / IRT 的输入；契约字段见 models/learner/attempt.py。
    PEDAGOGY_ATTEMPT = "pedagogy.attempt"


# 落盘前需要脱敏的字段名（大小写不敏感，子串匹配）
_SENSITIVE_PATTERN = re.compile(
    r"(api[_-]?key|authorization|password|passwd|secret|token|credential"
    r"|id[_-]?card|phone|mobile|real[_-]?name)",
    re.IGNORECASE,
)
_REDACTED = "***redacted***"

# 名字里含敏感词、但只是计数或配置的字段，脱敏会破坏用量与成本统计
_NOT_SENSITIVE_KEYS = frozenset(
    {
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "max_tokens",
        "tokens",
        "token_count",
    }
)


def redact(value: Any) -> Any:
    """递归脱敏：命中敏感字段名的值替换为占位符。

    递归处理 dict / list，其它类型原样返回。
    """
    if isinstance(value, dict):
        return {
            key: (
                _REDACTED
                if _SENSITIVE_PATTERN.search(str(key))
                and str(key).lower() not in _NOT_SENSITIVE_KEYS
                else redact(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return [redact(item) for item in value]
    return value


def _type_value(event_type: Any) -> str:
    """把事件类型规范成明文 value（枚举取 value，其余转字符串）。

    统一入口：``str(枚举)`` 在 Python 3.11+ 会得到 ``"EventType.X"``，
    落盘再读回就破坏事件类型的可比性。构造时与 ``to_dict()`` 必须用同一套规则，
    否则「构造后 type 又被赋成枚举」的事件会序列化出不一致的形状。
    """
    return event_type.value if isinstance(event_type, Enum) else str(event_type)


@dataclass
class RuntimeEvent:
    """一条 Runtime / 教学事件。"""

    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    event_id: str = ""
    session_id: str = ""
    trace_id: str = ""
    sequence: int = 0
    source: str = ""
    timestamp: str = ""

    def __post_init__(self) -> None:
        # 统一存明文值：str(枚举) 在 Python 3.11+ 会得到 "EventType.X"，
        # 落盘再读回就会破坏事件类型的可比性。
        self.type = _type_value(self.type)
        if not self.event_id:
            self.event_id = new_id("evt")
        if not self.timestamp:
            self.timestamp = utc_now()

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "session_id": self.session_id,
            "trace_id": self.trace_id,
            "sequence": self.sequence,
            "type": _type_value(self.type),
            "payload": self.payload,
            "source": self.source,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RuntimeEvent":
        return cls(
            type=data["type"],
            payload=dict(data.get("payload") or {}),
            event_id=data.get("event_id", ""),
            session_id=data.get("session_id", ""),
            trace_id=data.get("trace_id", ""),
            sequence=int(data.get("sequence", 0)),
            source=data.get("source", ""),
            timestamp=data.get("timestamp", ""),
        )


class StreamSubscription:
    """按会话订阅事件的异步流。

    用于 API 的 SSE 推送：事件到达即入队，消费方 async for 读取。
    """

    def __init__(self, session_id: str | None = None, maxsize: int = 512) -> None:
        self.session_id = session_id
        self._queue: asyncio.Queue[RuntimeEvent | None] = asyncio.Queue(maxsize=maxsize)
        self._closed = False

    def _push(self, event: RuntimeEvent) -> None:
        if self._closed:
            return
        if self.session_id and event.session_id != self.session_id:
            return
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            # 消费端过慢时丢弃最旧事件，保证生产端不被阻塞
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(event)
            except (asyncio.QueueEmpty, asyncio.QueueFull):  # pragma: no cover
                pass

    def close(self) -> None:
        """结束流：__anext__ 在消费完剩余事件后抛 StopAsyncIteration。"""
        self._closed = True
        try:
            self._queue.put_nowait(None)
        except asyncio.QueueFull:  # pragma: no cover
            pass

    def __aiter__(self) -> "StreamSubscription":
        return self

    async def __anext__(self) -> RuntimeEvent:
        event = await self._queue.get()
        if event is None:
            raise StopAsyncIteration
        return event


class EventBus:
    """事件总线：publish / subscribe / replay。

    写入顺序：脱敏 → （可选）落盘并分配 sequence → 通知订阅者。
    """

    def __init__(self, store: "EventStore | None" = None, source: str = "deepprof.runtime") -> None:
        self._store = store
        self._source = source
        self._listeners: list[Callable[[RuntimeEvent], None]] = []
        self._streams: list[StreamSubscription] = []
        self._sequences: dict[str, int] = {}
        self._local: list[RuntimeEvent] = []

    # ---------- 发布 ----------
    def emit(
        self,
        event_type: str,
        payload: dict | None = None,
        *,
        session_id: str = "",
        trace_id: str = "",
        source: str = "",
    ) -> RuntimeEvent:
        """构造并发布一条事件。"""
        return self.publish(
            RuntimeEvent(
                type=event_type,
                payload=payload or {},
                session_id=session_id,
                trace_id=trace_id,
                source=source or self._source,
            )
        )

    def publish(self, event: RuntimeEvent) -> RuntimeEvent:
        """发布事件并返回落盘后的最终形态（含 sequence）。"""
        event.payload = redact(event.payload)
        if not event.source:
            event.source = self._source
        if self._store is not None:
            event = self._store.append(event)
        elif not event.sequence:
            event.sequence = self._next_sequence(event.session_id)
        if self._store is None:
            self._local.append(event)
        for listener in list(self._listeners):
            listener(event)
        for stream in list(self._streams):
            stream._push(event)
        return event

    def _next_sequence(self, session_id: str) -> int:
        current = self._sequences.get(session_id, 0) + 1
        self._sequences[session_id] = current
        return current

    # ---------- 订阅 ----------
    def subscribe(self, listener: Callable[[RuntimeEvent], None]) -> Callable[[], None]:
        """注册同步监听器，返回取消订阅函数。"""
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def open_stream(self, session_id: str | None = None) -> StreamSubscription:
        """打开异步事件流（API 的 SSE / WebSocket 用）。"""
        stream = StreamSubscription(session_id)
        self._streams.append(stream)
        return stream

    def close_stream(self, stream: StreamSubscription) -> None:
        stream.close()
        if stream in self._streams:
            self._streams.remove(stream)

    # ---------- 回放 ----------
    def replay(self, session_id: str, from_sequence: int = 0) -> Iterator[RuntimeEvent]:
        """按 sequence 顺序回放某个会话的事件（流式重连用）。"""
        if self._store is not None:
            yield from (
                event
                for event in self._store.list(session_id)
                if event.sequence > from_sequence
            )
            return
        yield from (
            event
            for event in self._local
            if event.session_id == session_id and event.sequence > from_sequence
        )