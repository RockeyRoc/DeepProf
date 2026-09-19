"""记忆接口与策略（DESIGNv0.4 §5.5）。

Memory 是 Runtime 服务，不等同于 Session 日志：
- Session Log        事实轨迹，用于恢复、审计与回放（在 Session/SessionStore）
- Short-term Memory  当前任务内的临时信息
- Working Memory     当前任务与本周学习目标
- Long-term Memory   知识点掌握度、错误模式、学习偏好
- Episodic Memory    关键学习事件与干预结果
- Affective Memory   经用户授权保存的表达偏好与互动状态

写入纪律：任何长期记忆都必须带来源、置信度、过期策略与可撤回标记。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from ..core.message import new_id, utc_now


class MemoryType(str, Enum):
    """五类记忆。"""

    SHORT_TERM = "short_term"
    WORKING = "working"
    LONG_TERM = "long_term"
    EPISODIC = "episodic"
    AFFECTIVE = "affective"


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass
class MemoryRecord:
    """一条记忆记录。"""

    learner_id: str
    memory_type: MemoryType | str
    content: str
    key: str = ""
    source: str = ""  # 来源：哪次会话、哪个工具、哪份证据
    confidence: float = 0.5  # [0,1]
    record_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    expires_at: str = ""  # 空串表示不自动过期
    revocable: bool = True  # 用户可撤回/删除
    deleted: bool = False
    model_version: str = ""  # 由学情模型产生时记录版本
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.memory_type, MemoryType):
            self.memory_type = MemoryType(str(self.memory_type))
        if not self.record_id:
            self.record_id = new_id("mem")
        now = utc_now()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = self.created_at

    def is_expired(self, now: str = "") -> bool:
        """是否已过期（无 expires_at 视为不过期）。"""
        expiry = _parse_time(self.expires_at)
        if expiry is None:
            return False
        current = _parse_time(now) or datetime.now(timezone.utc)
        return current >= expiry

    def validate(self) -> None:
        """写入前校验；不通过抛 MemoryValidationError。"""
        from ..core.errors import MemoryValidationError

        if not self.learner_id:
            raise MemoryValidationError("记忆缺少 learner_id")
        if not self.content.strip():
            raise MemoryValidationError("记忆内容为空", record_id=self.record_id)
        if not self.source:
            raise MemoryValidationError(
                "记忆缺少来源，无法审计", record_id=self.record_id
            )
        if not 0.0 <= self.confidence <= 1.0:
            raise MemoryValidationError(
                "置信度必须在 [0,1]", record_id=self.record_id, confidence=self.confidence
            )
        if not isinstance(self.revocable, bool):
            raise MemoryValidationError("revocable 必须是布尔值", record_id=self.record_id)

    def to_dict(self) -> dict:
        return {
            "record_id": self.record_id,
            "learner_id": self.learner_id,
            "memory_type": self.memory_type.value,
            "key": self.key,
            "content": self.content,
            "source": self.source,
            "confidence": self.confidence,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
            "revocable": self.revocable,
            "deleted": self.deleted,
            "model_version": self.model_version,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MemoryRecord":
        return cls(
            learner_id=data.get("learner_id", ""),
            memory_type=data.get("memory_type", MemoryType.WORKING.value),
            content=data.get("content", ""),
            key=data.get("key", ""),
            source=data.get("source", ""),
            confidence=float(data.get("confidence", 0.5)),
            record_id=data.get("record_id", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            expires_at=data.get("expires_at", ""),
            revocable=bool(data.get("revocable", True)),
            deleted=bool(data.get("deleted", False)),
            model_version=data.get("model_version", ""),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass
class MemoryQuery:
    """记忆检索条件。"""

    learner_id: str
    memory_type: MemoryType | str | None = None
    key: str = ""
    text: str = ""
    limit: int = 20
    include_expired: bool = False
    include_deleted: bool = False

    def __post_init__(self) -> None:
        # 绑定表里 memory_type 是字符串（"long_term"），直接调用方可能传枚举；
        # 这里统一收敛成枚举，各 store 只取 .value，避免 str(枚举) 得到
        # "MemoryType.LONG_TERM" 而查不到数据。
        if self.memory_type is not None and not isinstance(self.memory_type, MemoryType):
            self.memory_type = MemoryType(str(self.memory_type))


@runtime_checkable
class MemoryStore(Protocol):
    """记忆持久化接口。"""

    def write(self, records: list[MemoryRecord]) -> list[str]:
        """写入（按 record_id 幂等），返回 record_id 列表。"""

    def read(self, query: MemoryQuery) -> list[MemoryRecord]: ...

    def delete(self, record_ids: list[str]) -> int:
        """软删除，返回受影响条数。"""

    def purge_expired(self, now: str = "") -> int:
        """清理过期记录，返回条数。"""


class InMemoryMemoryStore:
    """内存记忆存储：用于单元测试与 FakeRuntime。"""

    def __init__(self) -> None:
        self._records: dict[str, MemoryRecord] = {}

    def write(self, records: list[MemoryRecord]) -> list[str]:
        for record in records:
            self._records[record.record_id] = record
        return [record.record_id for record in records]

    def read(self, query: MemoryQuery) -> list[MemoryRecord]:
        wanted_type = query.memory_type.value if query.memory_type else ""
        results: list[MemoryRecord] = []
        for record in self._records.values():
            if record.learner_id != query.learner_id:
                continue
            if wanted_type and record.memory_type.value != wanted_type:
                continue
            if query.key and query.key not in record.key:
                continue
            if query.text and query.text not in record.content:
                continue
            if record.deleted and not query.include_deleted:
                continue
            if record.is_expired() and not query.include_expired:
                continue
            results.append(record)
        return results[: query.limit]

    def delete(self, record_ids: list[str]) -> int:
        count = 0
        for record_id in record_ids:
            record = self._records.get(record_id)
            if record is not None and not record.deleted:
                record.deleted = True
                count += 1
        return count

    def purge_expired(self, now: str = "") -> int:
        count = 0
        for record in self._records.values():
            if not record.deleted and record.is_expired(now):
                record.deleted = True
                count += 1
        return count