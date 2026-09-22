"""SQLite 版记忆存储：按 learner_id 隔离，支持撤回。"""

from __future__ import annotations

import json
import sqlite3

from runtime.memory.base import MemoryRecord
from runtime.storage.migrations import connect_memory


class SqliteMemoryStore:
    def __init__(self, connection: sqlite3.Connection | None = None, path: str | None = None) -> None:
        if connection is not None:
            self._conn = connection
        elif path:
            self._conn = connect_memory() if path == ":memory:" else _connect_file(path)
        else:
            self._conn = connect_memory()

    def write(self, records: list[MemoryRecord]) -> int:
        written = 0
        for record in records:
            self._conn.execute(
                "INSERT OR REPLACE INTO memory_records (record_id, learner_id, scope, content, source,"
                " confidence, revocable, expires_at, concept_ids, tags, metadata, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.record_id,
                    record.learner_id,
                    record.scope,
                    record.content,
                    record.source,
                    record.confidence,
                    1 if record.revocable else 0,
                    record.expires_at,
                    json.dumps(record.concept_ids, ensure_ascii=False),
                    json.dumps(record.tags, ensure_ascii=False),
                    json.dumps(record.metadata, ensure_ascii=False, default=str),
                    record.created_at,
                ),
            )
            written += 1
        self._conn.commit()
        return written

    def read(self, query: dict) -> list[MemoryRecord]:
        clauses = ["learner_id = ?"]
        args: list[object] = [query.get("learner_id", "local")]
        if query.get("scope"):
            clauses.append("scope = ?")
            args.append(query["scope"])
        if query.get("record_ids"):
            placeholders = ",".join("?" for _ in query["record_ids"])
            clauses.append(f"record_id IN ({placeholders})")
            args.extend(query["record_ids"])
        limit = int(query.get("limit") or 20)
        sql = (
            "SELECT * FROM memory_records WHERE "
            + " AND ".join(clauses)
            + " ORDER BY created_at DESC LIMIT ?"
        )
        args.append(limit)
        rows = self._conn.execute(sql, args).fetchall()
        records = [_row_to_record(row) for row in rows]
        concepts = set(query.get("concept_ids") or [])
        if concepts:
            records = [r for r in records if concepts & set(r.concept_ids)]
        return records

    def delete(self, learner_id: str, record_id: str) -> bool:
        cursor = self._conn.execute(
            "DELETE FROM memory_records WHERE learner_id = ? AND record_id = ?", (learner_id, record_id)
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def close(self) -> None:
        self._conn.close()


def _connect_file(path: str) -> sqlite3.Connection:
    from runtime.storage.migrations import connect

    return connect(path)


def _row_to_record(row: sqlite3.Row) -> MemoryRecord:
    return MemoryRecord(
        record_id=row["record_id"],
        learner_id=row["learner_id"],
        scope=row["scope"],
        content=row["content"],
        source=row["source"],
        confidence=float(row["confidence"]),
        revocable=bool(row["revocable"]),
        expires_at=row["expires_at"],
        concept_ids=json.loads(row["concept_ids"] or "[]"),
        tags=json.loads(row["tags"] or "[]"),
        metadata=json.loads(row["metadata"] or "{}"),
        created_at=row["created_at"],
    )