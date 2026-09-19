"""记忆服务：读写编排 + 审计事件（DESIGNv0.4 §5.5 / §7.1）。

对教学图暴露 dict 形态（与 RuntimePort.read_memory / write_memory 一致），
内部转成 MemoryRecord 做校验与持久化。

隐私（§13.2）：事件里只记录 record_id / key / 数量，不把记忆正文写进轨迹。
"""
from __future__ import annotations

from typing import Any

from ..core.errors import MemoryValidationError
from ..core.events import EventBus, EventType
from .base import MemoryQuery, MemoryRecord, MemoryStore, MemoryType


class MemoryService:
    """五类记忆的统一出入口。"""

    # 长期记忆是"学情结论"，必须给出会话之外的明确来源；
    # 其余类型允许用 session:<id> 兜底（§5.5 写入纪律）。
    REQUIRE_EXPLICIT_SOURCE = frozenset({MemoryType.LONG_TERM})

    def __init__(self, store: MemoryStore, bus: EventBus | None = None) -> None:
        self._store = store
        self._bus = bus

    # ---------- 读 ----------
    async def read(self, query: dict, ctx: dict | None = None) -> list[dict]:
        """按条件读取记忆（只读，不改变掌握度）。"""
        context = ctx or {}
        record_query = MemoryQuery(
            learner_id=str(query.get("learner_id") or context.get("learner_id") or ""),
            memory_type=query.get("memory_type"),
            key=str(query.get("key") or ""),
            text=str(query.get("text") or ""),
            limit=int(query.get("limit") or 20),
            include_expired=bool(query.get("include_expired", False)),
            include_deleted=bool(query.get("include_deleted", False)),
        )
        records = self._store.read(record_query)
        self._emit(
            EventType.MEMORY_READ,
            {
                "learner_id": record_query.learner_id,
                "memory_type": record_query.memory_type.value if record_query.memory_type else "",
                "count": len(records),
                "record_ids": [r.record_id for r in records],
            },
            context,
        )
        return [record.to_dict() for record in records]

    # ---------- 写 ----------
    async def write(self, records: list[dict], ctx: dict | None = None) -> list[str]:
        """写入记忆。

        每条记录都会被校验：缺 learner_id / 来源 / 置信度越界 / 撤回标记非法
        都会抛 MemoryValidationError，避免"无来源的学情结论"进入长期记忆。
        """
        context = ctx or {}
        parsed: list[MemoryRecord] = []
        for raw in records:
            record = MemoryRecord.from_dict(raw)
            if not record.learner_id:
                record.learner_id = str(context.get("learner_id") or "")
            if not record.source:
                explicit = str(context.get("source") or "")
                if not explicit and record.memory_type in self.REQUIRE_EXPLICIT_SOURCE:
                    raise MemoryValidationError(
                        "长期记忆必须带明确来源，不能只依赖会话兜底",
                        record_id=record.record_id,
                        memory_type=record.memory_type.value,
                    )
                record.source = explicit or f"session:{context.get('session_id', '')}"
            record.validate()
            parsed.append(record)

        record_ids = self._store.write(parsed)
        for record in parsed:
            self._emit(
                EventType.MEMORY_WRITE,
                {
                    "learner_id": record.learner_id,
                    "record_id": record.record_id,
                    "memory_type": record.memory_type.value,
                    "key": record.key,
                    "confidence": record.confidence,
                    "source": record.source,
                    "expires_at": record.expires_at,
                    "revocable": record.revocable,
                    "model_version": record.model_version,
                },
                context,
            )
        return record_ids

    # ---------- 撤回与清理 ----------
    async def forget(
        self, record_ids: list[str], ctx: dict | None = None
    ) -> int:
        """用户可撤回：软删除指定记忆（§13.2 可纠正、可删除）。"""
        deleted = self._store.delete(record_ids)
        self._emit(
            EventType.MEMORY_WRITE,
            {"action": "forget", "count": deleted, "record_ids": record_ids},
            ctx or {},
        )
        return deleted

    def purge_expired(self, now: str = "") -> int:
        """清理过期记忆（可由定时任务调用）。"""
        return self._store.purge_expired(now)

    # ---------- 内部 ----------
    def _emit(self, event_type: EventType, payload: dict[str, Any], ctx: dict) -> None:
        if self._bus is None:
            return
        self._bus.emit(
            event_type,
            payload,
            session_id=str(ctx.get("session_id") or ""),
            trace_id=str(ctx.get("trace_id") or ""),
            source=str(ctx.get("source") or "deepprof.runtime.memory"),
        )


def build_working_memory(
    learner_id: str,
    content: str,
    *,
    source: str,
    key: str = "",
    confidence: float = 0.6,
    metadata: dict[str, Any] | None = None,
) -> MemoryRecord:
    """构造一条工作记忆（当前任务 / 本周目标）。"""
    return MemoryRecord(
        learner_id=learner_id,
        memory_type=MemoryType.WORKING,
        content=content,
        key=key,
        source=source,
        confidence=confidence,
        metadata=metadata or {},
    )


__all__ = [
    "MemoryService",
    "MemoryRecord",
    "MemoryQuery",
    "MemoryStore",
    "MemoryType",
    "MemoryValidationError",
    "build_working_memory",
]