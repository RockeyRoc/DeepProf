"""存储抽象层（DESIGNv0.4 D-3）。

上层只依赖这里的抽象接口，不绑定具体数据库。
MVP 实现为服务器本地 SQLite（见 sqlite_store.py），
后续可替换为 Postgres 或远程仓储而无需改动 Runtime 与教学图。

ProfileStore 的行数据刻意用 dict 表达：学情模型（BKT/IRT/知识追踪）
仍在演进，抽象层只约定字段（§18.2），把 DTO 定义留给 models/learner。
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ..core.events import RuntimeEvent
from ..core.session import Session


@runtime_checkable
class EventStore(Protocol):
    """事件存储：追加式、按 event_id 幂等。"""

    def append(self, event: RuntimeEvent) -> RuntimeEvent:
        """写入事件并返回携带 sequence 的最终形态。

        event_id 重复时不得重复写入，返回已存在的事件。
        """

    def list(self, session_id: str) -> list[RuntimeEvent]:
        """按 sequence 升序返回某会话的全部事件。"""

    def get(self, event_id: str) -> RuntimeEvent | None:
        """按 event_id 查询单条事件。"""


@runtime_checkable
class SessionStore(Protocol):
    """会话存储：可恢复、可分支。"""

    def save(self, session: Session) -> None: ...

    def load(self, session_id: str) -> Session | None: ...

    def list_ids(self, learner_id: str = "") -> list[str]: ...

    def delete(self, session_id: str) -> None: ...


@runtime_checkable
class ProfileStore(Protocol):
    """学情画像存储（§18.2 LearnerEstimate 的持久化）。"""

    def save_estimates(self, estimates: list[dict[str, Any]]) -> int:
        """写入或覆盖学情估计，返回受影响行数。

        建议唯一键：learner_id + concept_id + model_type + model_version。
        """

    def read_estimates(
        self, learner_id: str, concept_id: str = ""
    ) -> list[dict[str, Any]]:
        """读取某学生的学情估计，可按知识点过滤。"""


class InMemoryEventStore:
    """内存事件存储：用于单元测试与 FakeRuntime。"""

    def __init__(self) -> None:
        self._events: dict[str, RuntimeEvent] = {}
        self._by_session: dict[str, list[RuntimeEvent]] = {}
        self._sequence = 0

    def append(self, event: RuntimeEvent) -> RuntimeEvent:
        existing = self._events.get(event.event_id)
        if existing is not None:
            return existing  # 幂等：同一 event_id 只写一次
        self._sequence += 1
        event.sequence = self._sequence
        self._events[event.event_id] = event
        self._by_session.setdefault(event.session_id, []).append(event)
        return event

    def list(self, session_id: str) -> list[RuntimeEvent]:
        return list(self._by_session.get(session_id, []))

    def get(self, event_id: str) -> RuntimeEvent | None:
        return self._events.get(event_id)


class InMemorySessionStore:
    """内存会话存储：用于单元测试与 FakeRuntime。"""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def save(self, session: Session) -> None:
        self._sessions[session.session_id] = Session.load(session.to_dict())

    def load(self, session_id: str) -> Session | None:
        stored = self._sessions.get(session_id)
        return Session.load(stored.to_dict()) if stored else None

    def list_ids(self, learner_id: str = "") -> list[str]:
        return [
            sid
            for sid, session in self._sessions.items()
            if not learner_id or session.learner_id == learner_id
        ]

    def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)


class InMemoryProfileStore:
    """内存画像存储：用于单元测试与 FakeRuntime。"""

    def __init__(self) -> None:
        self._rows: dict[tuple, dict[str, Any]] = {}

    @staticmethod
    def _key(row: dict[str, Any]) -> tuple:
        return (
            row.get("learner_id", ""),
            row.get("concept_id", ""),
            row.get("model_type", ""),
            row.get("model_version", ""),
        )

    def save_estimates(self, estimates: list[dict[str, Any]]) -> int:
        for row in estimates:
            self._rows[self._key(row)] = dict(row)
        return len(estimates)

    def read_estimates(
        self, learner_id: str, concept_id: str = ""
    ) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self._rows.values()
            if row.get("learner_id") == learner_id
            and (not concept_id or row.get("concept_id") == concept_id)
        ]