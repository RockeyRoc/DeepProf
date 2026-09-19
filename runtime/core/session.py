"""Session 模型（DESIGNv0.4 §5.1）。

职责：会话标识、分支、恢复、上下文窗口与持久化。
最小接口：load / append / fork / compact。

注意边界（§4.4）：Session 只保存"交互事实"，
学情与长期记忆走 Memory，两者通过 session_id / trace_id 关联。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .message import Message, new_id, utc_now


@dataclass
class Session:
    """一次连续交互上下文。"""

    session_id: str = ""
    learner_id: str = ""
    messages: list[Message] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    parent_session_id: str = ""  # fork 来源，便于回溯分支
    compacted_summary: str = ""  # 被压缩掉的早期上下文摘要

    def __post_init__(self) -> None:
        if not self.session_id:
            self.session_id = new_id("sess")
        now = utc_now()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now

    # ---------- 消息 ----------
    def append(self, message: Message) -> Message:
        """追加一条消息并刷新 updated_at。"""
        self.messages.append(message)
        self.updated_at = utc_now()
        return message

    def extend(self, messages: list[Message]) -> None:
        for message in messages:
            self.append(message)

    def last_user_message(self) -> Message | None:
        for message in reversed(self.messages):
            if message.role == "user":
                return message
        return None

    # ---------- 生命周期 ----------
    @classmethod
    def load(cls, data: dict) -> "Session":
        """从持久化数据恢复。"""
        return cls(
            session_id=data["session_id"],
            learner_id=data.get("learner_id", ""),
            messages=[Message.from_dict(m) for m in data.get("messages", [])],
            metadata=dict(data.get("metadata") or {}),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            parent_session_id=data.get("parent_session_id", ""),
            compacted_summary=data.get("compacted_summary", ""),
        )

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "learner_id": self.learner_id,
            "messages": [m.to_dict() for m in self.messages],
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "parent_session_id": self.parent_session_id,
            "compacted_summary": self.compacted_summary,
        }

    def compact(self, keep_last: int = 12, summary: str = "") -> "Session":
        """压缩上下文：保留最近 keep_last 条消息，其余折叠为摘要。

        大型历史不写回消息列表，只保留摘要文本，避免上下文无限膨胀（§6.4）。
        """
        if keep_last < 1 or len(self.messages) <= keep_last:
            return self
        dropped = self.messages[:-keep_last]
        self.messages = self.messages[-keep_last:]
        self.compacted_summary = summary or _summarize(dropped, self.compacted_summary)
        self.updated_at = utc_now()
        return self

    def fork(self, session_id: str = "", learner_id: str = "") -> "Session":
        """复制出一个新分支（探索不同教学策略时使用）。"""
        return Session(
            session_id=session_id,
            learner_id=learner_id or self.learner_id,
            messages=[Message.from_dict(m.to_dict()) for m in self.messages],
            metadata=dict(self.metadata),
            parent_session_id=self.session_id,
            compacted_summary=self.compacted_summary,
        )


def _summarize(dropped: list[Message], previous: str) -> str:
    """生成压缩摘要。

    MVP 只做确定性截断摘要，不调用模型：模型摘要需要额外一次
    Provider 调用，留待 MVP-1 由 Runtime 通过 Provider 注入实现。
    """
    lines = [previous] if previous else []
    for message in dropped:
        role = message.role.value if hasattr(message.role, "value") else str(message.role)
        text = (message.content or "").strip().replace("\n", " ")
        if not text:
            continue
        lines.append(f"{role}: {text[:60]}")
    return "\n".join(lines[-40:])