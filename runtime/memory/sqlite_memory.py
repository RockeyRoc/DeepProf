"""SQLite 记忆存储实现（D-3）。

与 EventStore 共用同一个 SqliteDatabase，但独立成表，
保证 Session 轨迹与学情记忆分离（§17.3）。
"""
from __future__ import annotations

import json
from typing import Any

from ..core.message import utc_now
from ..storage.sqlite_store import SqliteDatabase
from .base import MemoryQuery, MemoryRecord


class SqliteMemoryStore:
    """memory_records 表的读写。"""

    def __init__(self, db: SqliteDatabase) -> None:
        self._db = db

    def write(self, records: list[MemoryRecord]) -> list[str]:
        """按 record_id 幂等写入（重复写入覆盖同一条，不产生副本）。"""
        with self._db.transaction() as conn:
            for record in records:
                conn.execute(
                    """
                    INSERT INTO memory_records
                        (record_id, learner_id, memory_type, key, content, source,
                         confidence, created_at, updated_at, expires_at, revocable,
                         deleted, model_version, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(record_id) DO UPDATE SET
                        content = excluded.content,
                        key = excluded.key,
                        source = excluded.source,
                        confidence = excluded.confidence,
                        updated_at = excluded.updated_at,
                        expires_at = excluded.expires_at,
                        revocable = excluded.revocable,
                        deleted = excluded.deleted,
                        model_version = excluded.model_version,
                        metadata = excluded.metadata
                    """,
                    (
                        record.record_id,
                        record.learner_id,
                        record.memory_type.value,
                        record.key,
                        record.content,
                        record.source,
                        record.confidence,
                        record.created_at,
                        record.updated_at,
                        record.expires_at,
                        int(record.revocable),
                        int(record.deleted),
                        record.model_version,
                        json.dumps(record.metadata, ensure_ascii=False),
                    ),
                )
        return [record.record_id for record in records]

    def read(self, query: MemoryQuery) -> list[MemoryRecord]:
        sql = "SELECT * FROM memory_records WHERE learner_id = ?"
        params: list[Any] = [query.learner_id]
        if query.memory_type:
            sql += " AND memory_type = ?"
            params.append(query.memory_type.value)
        if query.key:
            sql += " AND key LIKE ?"
            params.append(f"%{query.key}%")
        if query.text:
            sql += " AND content LIKE ?"
            params.append(f"%{query.text}%")
        if not query.include_deleted:
            sql += " AND deleted = 0"
        if not query.include_expired:
            # 空 expires_at 表示不自动过期
            sql += " AND (expires_at = '' OR expires_at > ?)"
            params.append(utc_now())
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(query.limit)
        with self._db.transaction() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_record(row) for row in rows]

    def delete(self, record_ids: list[str]) -> int:
        """软删除：保留来源与版本便于审计，仅置 deleted 标记。"""
        if not record_ids:
            return 0
        placeholders = ",".join("?" for _ in record_ids)
        with self._db.transaction() as conn:
            cursor = conn.execute(
                f"UPDATE memory_records SET deleted = 1, updated_at = ? "
                f"WHERE record_id IN ({placeholders}) AND deleted = 0",
                [utc_now(), *record_ids],
            )
        return int(cursor.rowcount or 0)

    def purge_expired(self, now: str = "") -> int:
        """物理清理已过期记录：过期数据不再保留，符合最小必要原则。"""
        moment = now or utc_now()
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM memory_records WHERE expires_at != '' AND expires_at <= ?",
                (moment,),
            )
        return int(cursor.rowcount or 0)


def _row_to_record(row: Any) -> MemoryRecord:
    return MemoryRecord.from_dict(
        {
            "record_id": row["record_id"],
            "learner_id": row["learner_id"],
            "memory_type": row["memory_type"],
            "key": row["key"],
            "content": row["content"],
            "source": row["source"],
            "confidence": row["confidence"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "expires_at": row["expires_at"],
            "revocable": bool(row["revocable"]),
            "deleted": bool(row["deleted"]),
            "model_version": row["model_version"],
            "metadata": json.loads(row["metadata"]),
        }
    )