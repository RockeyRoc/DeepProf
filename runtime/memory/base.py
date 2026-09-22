"""记忆记录的边界定义。

任何长期记忆写入都必须包含来源、置信度、过期策略与可撤回标记（§5.5）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from runtime.core.errors import RuntimeFailure
from runtime.core.events import new_id, utc_now

SCOPE_WORKING = "working"
SCOPE_LONG_TERM = "long_term"
SCOPE_EPISODIC = "episodic"
SCOPE_AFFECTIVE = "affective"

SCOPES = (SCOPE_WORKING, SCOPE_LONG_TERM, SCOPE_EPISODIC, SCOPE_AFFECTIVE)


@dataclass(slots=True)
class MemoryRecord:
    """一条可审计、可撤回的学习记忆。"""

    learner_id: str
    scope: str
    content: str
    source: str
    confidence: float
    revocable: bool = True
    expires_at: str | None = None
    concept_ids: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    record_id: str = field(default_factory=lambda: new_id("mem"))
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "learner_id": self.learner_id,
            "scope": self.scope,
            "content": self.content,
            "source": self.source,
            "confidence": self.confidence,
            "revocable": self.revocable,
            "expires_at": self.expires_at,
            "concept_ids": list(self.concept_ids),
            "tags": list(self.tags),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryRecord":
        return cls(
            learner_id=str(data.get("learner_id", "")),
            scope=str(data.get("scope", SCOPE_WORKING)),
            content=str(data.get("content", "")),
            source=str(data.get("source", "")),
            confidence=float(data.get("confidence", 0.0)),
            revocable=bool(data.get("revocable", True)),
            expires_at=data.get("expires_at"),
            concept_ids=list(data.get("concept_ids") or []),
            tags=list(data.get("tags") or []),
            metadata=dict(data.get("metadata") or {}),
            record_id=str(data.get("record_id") or new_id("mem")),
            created_at=str(data.get("created_at") or utc_now()),
        )


def validate_record(record: MemoryRecord) -> None:
    """写入前校验：来源、置信度、过期策略、可撤回标记缺一不可。"""
    problems: list[str] = []
    if not record.learner_id:
        problems.append("learner_id")
    if record.scope not in SCOPES:
        problems.append("scope")
    if not record.source:
        problems.append("source")
    if not 0.0 <= record.confidence <= 1.0:
        problems.append("confidence")
    if not record.revocable and not record.expires_at:
        problems.append("expires_at")
    if problems:
        raise RuntimeFailure(
            f"invalid memory record: 缺少/非法字段 {problems}",
            details={"kind": "invalid_memory_record", "fields": problems},
        )


class MemoryStore(Protocol):
    def write(self, records: list[MemoryRecord]) -> int: ...

    def read(self, query: dict[str, Any]) -> list[MemoryRecord]: ...

    def delete(self, learner_id: str, record_id: str) -> bool: ...