"""SQLite 版事件与会话存储。

- 事件按 ``event_id`` 幂等：重复写入不产生新行；
- ``sequence`` 在同一 session 内单调递增，供 SSE 重连补发。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any

from runtime.core.events import RuntimeEvent, utc_now
from runtime.core.session import Session
from runtime.storage.migrations import connect


class SqliteEventStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection
        self._lock = threading.Lock()

    @classmethod
    def open(cls, path: str) -> "SqliteEventStore":
        return cls(connect(path))

    def append(self, event: RuntimeEvent) -> RuntimeEvent:
        with self._lock:
            if not event.event_id:
                from runtime.core.events import new_id

                event.event_id = new_id("evt")
            existing = self._conn.execute(
                "SELECT * FROM events WHERE event_id = ?", (event.event_id,)
            ).fetchone()
            if existing is not None:
                return _row_to_event(existing)
            if not event.timestamp:
                event.timestamp = utc_now()
            row = self._conn.execute(
                "SELECT MAX(sequence) AS seq FROM events WHERE session_id = ?", (event.session_id,)
            ).fetchone()
            event.sequence = int(row["seq"] or 0) + 1
            self._conn.execute(
                "INSERT INTO events (event_id, session_id, trace_id, sequence, type, payload, source,"
                " timestamp, client_id, surface, audience) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    event.event_id,
                    event.session_id,
                    event.trace_id,
                    event.sequence,
                    event.type,
                    json.dumps(event.payload, ensure_ascii=False, default=str),
                    event.source,
                    event.timestamp,
                    event.client_id,
                    event.surface,
                    event.audience,
                ),
            )
            self._conn.commit()
            return event

    def replay(self, session_id: str, from_sequence: int = 0) -> list[RuntimeEvent]:
        rows = self._conn.execute(
            "SELECT * FROM events WHERE session_id = ? AND sequence > ? ORDER BY sequence",
            (session_id, from_sequence),
        ).fetchall()
        return [_row_to_event(row) for row in rows]

    def last_sequence(self, session_id: str) -> int:
        row = self._conn.execute(
            "SELECT MAX(sequence) AS seq FROM events WHERE session_id = ?", (session_id,)
        ).fetchone()
        return int(row["seq"] or 0)

    def close(self) -> None:
        self._conn.close()


class SqliteSessionStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection

    @classmethod
    def open(cls, path: str) -> "SqliteSessionStore":
        return cls(connect(path))

    def save(self, session: Session) -> None:
        self._conn.execute(
            "INSERT INTO sessions (session_id, learner_id, title, parent_id, payload, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?)"
            " ON CONFLICT(session_id) DO UPDATE SET learner_id=excluded.learner_id, title=excluded.title,"
            " parent_id=excluded.parent_id, payload=excluded.payload, updated_at=excluded.updated_at",
            (
                session.session_id,
                session.learner_id,
                session.title,
                session.parent_id,
                json.dumps(session.to_dict(), ensure_ascii=False, default=str),
                session.created_at,
                session.updated_at,
            ),
        )
        self._conn.commit()

    def load(self, session_id: str) -> Session | None:
        row = self._conn.execute(
            "SELECT payload FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if row is None:
            return None
        return Session.from_dict(json.loads(row["payload"]))

    def list(self, learner_id: str | None = None) -> list[str]:
        if learner_id is None:
            rows = self._conn.execute("SELECT session_id FROM sessions ORDER BY updated_at DESC").fetchall()
        else:
            rows = self._conn.execute(
                "SELECT session_id FROM sessions WHERE learner_id = ? ORDER BY updated_at DESC",
                (learner_id,),
            ).fetchall()
        return [str(row["session_id"]) for row in rows]

    def close(self) -> None:
        self._conn.close()


def _row_to_event(row: sqlite3.Row) -> RuntimeEvent:
    payload: dict[str, Any] = json.loads(row["payload"] or "{}")
    return RuntimeEvent(
        type=row["type"],
        payload=payload,
        session_id=row["session_id"],
        trace_id=row["trace_id"],
        sequence=int(row["sequence"]),
        event_id=row["event_id"],
        source=row["source"],
        timestamp=row["timestamp"],
        client_id=row["client_id"],
        surface=row["surface"],
        audience=row["audience"],
    )