"""SQLite 存储实现（DESIGNv0.4 D-3 / §17.3）。

约束：
- 数据库只放本机磁盘，不放网络共享目录；
- 事件按 event_id 幂等，重复写入不重复记分；
- 事务 + WAL + busy_timeout 管理并发；
- Session 与学习记忆分表，按 learner_id 隔离。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from config import ensure_parent, settings

from ..core.events import RuntimeEvent
from ..core.session import Session
from .migrations import apply_migrations


class SqliteDatabase:
    """连接与 schema 的统一入口。

    单连接 + 可重入锁：MVP 写入量小，用锁把并发写入串行化，
    避免 SQLite 写锁竞争导致的 "database is locked"。
    """

    def __init__(self, path: str | Path = "", busy_timeout_ms: int | None = None) -> None:
        raw = str(path or settings.sqlite_path)
        self.is_memory = raw == ":memory:"
        self.path = raw if self.is_memory else str(ensure_parent(raw))
        self.busy_timeout_ms = (
            settings.sqlite_busy_timeout_ms if busy_timeout_ms is None else busy_timeout_ms
        )
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    @property
    def connection(self) -> sqlite3.Connection:
        """懒加载连接，首次访问时自动建表。"""
        with self._lock:
            if self._conn is None:
                conn = sqlite3.connect(
                    self.path,
                    timeout=self.busy_timeout_ms / 1000,
                    check_same_thread=False,
                )
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
                self._conn = conn
                apply_migrations(conn)
            return self._conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """事务上下文：正常提交，异常回滚。"""
        with self._lock:
            conn = self.connection
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None


class SqliteEventStore:
    """追加式事件存储。"""

    def __init__(self, db: SqliteDatabase) -> None:
        self._db = db

    def append(self, event: RuntimeEvent) -> RuntimeEvent:
        """写入事件；event_id 已存在时返回既有记录，不重复写入。"""
        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM events WHERE event_id = ?", (event.event_id,)
            ).fetchone()
            if row is not None:
                return _row_to_event(row)
            cursor = conn.execute(
                """
                INSERT INTO events
                    (event_id, session_id, trace_id, type, payload, source, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.session_id,
                    event.trace_id,
                    str(event.type),
                    json.dumps(event.payload, ensure_ascii=False),
                    event.source,
                    event.timestamp,
                ),
            )
            event.sequence = int(cursor.lastrowid or 0)
        return event

    def list(self, session_id: str) -> list[RuntimeEvent]:
        with self._db.transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE session_id = ? ORDER BY sequence",
                (session_id,),
            ).fetchall()
        return [_row_to_event(row) for row in rows]

    def get(self, event_id: str) -> RuntimeEvent | None:
        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM events WHERE event_id = ?", (event_id,)
            ).fetchone()
        return _row_to_event(row) if row else None

    def list_by_trace(self, trace_id: str) -> list[RuntimeEvent]:
        """按 trace_id 汇总一次请求的全部事件（可审计要求，§12）。"""
        with self._db.transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE trace_id = ? ORDER BY sequence",
                (trace_id,),
            ).fetchall()
        return [_row_to_event(row) for row in rows]


class SqliteSessionStore:
    """会话存储：重启后可恢复。"""

    def __init__(self, db: SqliteDatabase) -> None:
        self._db = db

    def save(self, session: Session) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO sessions (session_id, learner_id, messages, metadata,
                                      created_at, updated_at, parent_session_id,
                                      compacted_summary)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    learner_id = excluded.learner_id,
                    messages = excluded.messages,
                    metadata = excluded.metadata,
                    updated_at = excluded.updated_at,
                    parent_session_id = excluded.parent_session_id,
                    compacted_summary = excluded.compacted_summary
                """,
                (
                    session.session_id,
                    session.learner_id,
                    json.dumps([m.to_dict() for m in session.messages], ensure_ascii=False),
                    json.dumps(session.metadata, ensure_ascii=False),
                    session.created_at,
                    session.updated_at,
                    session.parent_session_id,
                    session.compacted_summary,
                ),
            )

    def load(self, session_id: str) -> Session | None:
        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        if row is None:
            return None
        return Session.load(
            {
                "session_id": row["session_id"],
                "learner_id": row["learner_id"],
                "messages": json.loads(row["messages"]),
                "metadata": json.loads(row["metadata"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "parent_session_id": row["parent_session_id"],
                "compacted_summary": row["compacted_summary"],
            }
        )

    def list_ids(self, learner_id: str = "") -> list[str]:
        with self._db.transaction() as conn:
            if learner_id:
                rows = conn.execute(
                    "SELECT session_id FROM sessions WHERE learner_id = ? ORDER BY updated_at DESC",
                    (learner_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT session_id FROM sessions ORDER BY updated_at DESC"
                ).fetchall()
        return [row["session_id"] for row in rows]

    def delete(self, session_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))


class SqliteProfileStore:
    """学情画像存储（§18.2 LearnerEstimate）。

    字段由数据组约定，这里只做持久化，不引入模型判断。
    """

    _COLUMNS = (
        "learner_id",
        "concept_id",
        "model_type",
        "model_version",
        "estimate",
        "evidence_count",
        "uncertainty",
        "updated_at",
        "metadata",
    )

    def __init__(self, db: SqliteDatabase) -> None:
        self._db = db

    def save_estimates(self, estimates: list[dict[str, Any]]) -> int:
        written = 0
        with self._db.transaction() as conn:
            for row in estimates:
                conn.execute(
                    """
                    INSERT INTO learner_estimates
                        (learner_id, concept_id, model_type, model_version,
                         estimate, evidence_count, uncertainty, updated_at, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(learner_id, concept_id, model_type, model_version)
                    DO UPDATE SET
                        estimate = excluded.estimate,
                        evidence_count = excluded.evidence_count,
                        uncertainty = excluded.uncertainty,
                        updated_at = excluded.updated_at,
                        metadata = excluded.metadata
                    """,
                    (
                        row.get("learner_id", ""),
                        row.get("concept_id", ""),
                        row.get("model_type", ""),
                        row.get("model_version", ""),
                        float(row.get("estimate", 0.0)),
                        int(row.get("evidence_count", 0)),
                        row.get("uncertainty"),
                        row.get("updated_at", ""),
                        json.dumps(row.get("metadata") or {}, ensure_ascii=False),
                    ),
                )
                written += 1
        return written

    def read_estimates(
        self, learner_id: str, concept_id: str = ""
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM learner_estimates WHERE learner_id = ?"
        params: list[Any] = [learner_id]
        if concept_id:
            sql += " AND concept_id = ?"
            params.append(concept_id)
        sql += " ORDER BY concept_id, model_type"
        with self._db.transaction() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            {**{k: row[k] for k in self._COLUMNS}, "metadata": json.loads(row["metadata"])}
            for row in rows
        ]


def _row_to_event(row: sqlite3.Row) -> RuntimeEvent:
    return RuntimeEvent(
        type=row["type"],
        payload=json.loads(row["payload"]),
        event_id=row["event_id"],
        session_id=row["session_id"],
        trace_id=row["trace_id"],
        sequence=int(row["sequence"]),
        source=row["source"],
        timestamp=row["timestamp"],
    )