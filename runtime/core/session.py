"""会话：标识、消息、分支、恢复与压缩。

Session 是交互事实（消息与事件），学习记忆另行存放（§5.5）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from runtime.core.events import new_id, utc_now
from runtime.core.message import Message

MAX_CONTEXT_MESSAGES = 60


@dataclass(slots=True)
class Session:
    """一次可恢复、可分支、可审计的连续交互上下文。"""

    session_id: str = field(default_factory=lambda: new_id("ses"))
    learner_id: str = "local"
    title: str = ""
    messages: list[Message] = field(default_factory=list)
    parent_id: str | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def append(self, message: Message) -> Message:
        self.messages.append(message)
        self.updated_at = utc_now()
        return message

    def extend(self, messages: list[Message]) -> None:
        for message in messages:
            self.append(message)

    def fork(self, *, title: str | None = None) -> "Session":
        """从当前会话派生一条新分支（消息在派生时点被复制）。"""
        return Session(
            learner_id=self.learner_id,
            title=title if title is not None else self.title,
            messages=list(self.messages),
            parent_id=self.session_id,
            metadata=dict(self.metadata),
        )

    def compact(self, *, keep: int = MAX_CONTEXT_MESSAGES) -> tuple[int, int]:
        """压缩历史消息，返回 (before, after)。

        只保留最近 ``keep`` 条；被压缩的消息由调用方写入 Storage 引用。
        """
        before = len(self.messages)
        if before <= keep:
            return before, before
        head = self.messages[:-keep] if keep > 0 else []
        dropped = self.messages[: len(self.messages) - keep]
        summary = Message(
            role="system",
            content=f"[已压缩 {len(dropped)} 条历史消息]",
            metadata={"compacted": True, "dropped": len(dropped)},
        )
        self.messages = ([summary] if head or dropped else []) + self.messages[len(dropped):]
        self.updated_at = utc_now()
        return before, len(self.messages)

    def context(self, *, limit: int = MAX_CONTEXT_MESSAGES) -> list[Message]:
        if limit <= 0 or len(self.messages) <= limit:
            return list(self.messages)
        return self.messages[-limit:]

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "learner_id": self.learner_id,
            "title": self.title,
            "parent_id": self.parent_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
            "messages": [m.to_dict() for m in self.messages],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Session":
        return cls(
            session_id=str(data.get("session_id") or new_id("ses")),
            learner_id=str(data.get("learner_id", "local")),
            title=str(data.get("title", "")),
            parent_id=data.get("parent_id"),
            created_at=str(data.get("created_at", "")) or utc_now(),
            updated_at=str(data.get("updated_at", "")) or utc_now(),
            metadata=dict(data.get("metadata") or {}),
            messages=[Message.from_dict(item) for item in data.get("messages") or []],
        )


class SessionStore(Protocol):
    """会话持久化接口。"""

    def save(self, session: Session) -> None: ...

    def load(self, session_id: str) -> Session | None: ...

    def list(self, learner_id: str | None = None) -> list[str]: ...


class InMemorySessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def save(self, session: Session) -> None:
        self._sessions[session.session_id] = session

    def load(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def list(self, learner_id: str | None = None) -> list[str]:
        return [
            s.session_id
            for s in self._sessions.values()
            if learner_id is None or s.learner_id == learner_id
        ]