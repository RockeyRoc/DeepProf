"""记忆服务：区分短期 / 工作 / 长期 / 事件 / 情感记忆。

只暴露服务接口，外部不直接触碰存储；写入一律经校验并产生 ``memory.write`` 事件。
"""

from __future__ import annotations

from typing import Any

from runtime.memory.base import MemoryRecord, MemoryStore, validate_record
from runtime.memory.sqlite_memory import SqliteMemoryStore

DEFAULT_LIMIT = 20


class MemoryService:
    def __init__(self, store: MemoryStore | None = None) -> None:
        self._store: MemoryStore = store or SqliteMemoryStore()

    @property
    def store(self) -> MemoryStore:
        return self._store

    async def write(self, records: list[dict[str, Any]], ctx: dict[str, Any]) -> None:
        """写入记忆。契约冻结：返回 ``None``。"""
        learner_id = str(ctx.get("learner_id") or "local")
        parsed = []
        for item in records:
            data = dict(item)
            data.setdefault("learner_id", learner_id)
            record = MemoryRecord.from_dict(data)
            validate_record(record)
            parsed.append(record)
        if parsed:
            self._store.write(parsed)

    async def read(self, query: dict[str, Any], ctx: dict[str, Any]) -> list[dict[str, Any]]:
        learner_id = str(ctx.get("learner_id") or query.get("learner_id") or "local")
        payload = dict(query)
        payload["learner_id"] = learner_id
        payload.setdefault("limit", DEFAULT_LIMIT)
        return [record.to_dict() for record in self._store.read(payload)]

    def forget(self, learner_id: str, record_id: str) -> bool:
        """撤回一条记忆（可撤回标记的落地入口）。"""
        return self._store.delete(learner_id, record_id)