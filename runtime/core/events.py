"""事件模型与追加式事件存储。

事件采用 append-only，携带 ``event_id / session_id / trace_id / sequence / type /
payload / source / timestamp``，可选 ``client_id / surface / audience``。
同一 session 内 ``sequence`` 单调递增，供 SSE 重连按序补发。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterator, Protocol

SOURCE_RUNTIME = "runtime"


class EventType(str, Enum):
    """最小事件集合（见 DESIGNv0.6 §5.4）。"""

    SESSION_STARTED = "session.started"
    SESSION_RESUMED = "session.resumed"
    SESSION_COMPACTED = "session.compacted"
    SESSION_ENDED = "session.ended"

    AGENT_STARTED = "agent.started"
    AGENT_TURN_STARTED = "agent.turn.started"
    AGENT_TURN_COMPLETED = "agent.turn.completed"
    AGENT_FAILED = "agent.failed"

    MODEL_REQUESTED = "model.requested"
    MODEL_STREAM_DELTA = "model.stream.delta"
    MODEL_COMPLETED = "model.completed"
    MODEL_FAILED = "model.failed"

    TOOL_REQUESTED = "tool.requested"
    TOOL_APPROVED = "tool.approved"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"

    MEMORY_READ = "memory.read"
    MEMORY_WRITE = "memory.write"

    LIBRARY_IMPORTED = "library.imported"
    LIBRARY_INDEXED = "library.indexed"

    PEDAGOGY_NODE_ENTERED = "pedagogy.node.entered"
    PEDAGOGY_DECISION = "pedagogy.decision"
    PEDAGOGY_NODE_EXITED = "pedagogy.node.exited"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


@dataclass(slots=True)
class RuntimeEvent:
    """一条 Runtime 事件。"""

    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    session_id: str = ""
    trace_id: str = ""
    sequence: int = 0
    event_id: str = ""
    source: str = SOURCE_RUNTIME
    timestamp: str = ""
    client_id: str | None = None
    surface: str | None = None
    audience: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "session_id": self.session_id,
            "trace_id": self.trace_id,
            "sequence": self.sequence,
            "type": self.type,
            "payload": dict(self.payload),
            "source": self.source,
            "timestamp": self.timestamp,
            "client_id": self.client_id,
            "surface": self.surface,
            "audience": self.audience,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RuntimeEvent":
        return cls(
            type=str(data.get("type", "")),
            payload=dict(data.get("payload") or {}),
            session_id=str(data.get("session_id", "")),
            trace_id=str(data.get("trace_id", "")),
            sequence=int(data.get("sequence", 0)),
            event_id=str(data.get("event_id", "")),
            source=str(data.get("source", SOURCE_RUNTIME)),
            timestamp=str(data.get("timestamp", "")),
            client_id=data.get("client_id"),
            surface=data.get("surface"),
            audience=data.get("audience"),
        )


class EventStore(Protocol):
    """事件存储接口：追加、回放、订阅。"""

    def append(self, event: RuntimeEvent) -> RuntimeEvent: ...

    def replay(self, session_id: str, from_sequence: int = 0) -> list[RuntimeEvent]: ...

    def last_sequence(self, session_id: str) -> int: ...


class InMemoryEventStore:
    """内存事件存储：幂等（按 event_id）、按 session 单调编号。"""

    def __init__(self) -> None:
        self._events: dict[str, list[RuntimeEvent]] = {}
        self._seen: set[str] = set()

    def append(self, event: RuntimeEvent) -> RuntimeEvent:
        if not event.event_id:
            event.event_id = new_id("evt")
        if event.event_id in self._seen:
            # 幂等：重复 event_id 不重复写入，返回已落库的事件
            for existing in self._events.get(event.session_id, []):
                if existing.event_id == event.event_id:
                    return existing
            return event
        if not event.timestamp:
            event.timestamp = utc_now()
        bucket = self._events.setdefault(event.session_id, [])
        event.sequence = (bucket[-1].sequence + 1) if bucket else 1
        bucket.append(event)
        self._seen.add(event.event_id)
        return event

    def replay(self, session_id: str, from_sequence: int = 0) -> list[RuntimeEvent]:
        return [e for e in self._events.get(session_id, []) if e.sequence > from_sequence]

    def last_sequence(self, session_id: str) -> int:
        bucket = self._events.get(session_id)
        return bucket[-1].sequence if bucket else 0

    def all(self) -> Iterator[RuntimeEvent]:
        for bucket in self._events.values():
            yield from bucket
