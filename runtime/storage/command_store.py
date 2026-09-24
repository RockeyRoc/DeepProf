"""Durable idempotency receipts for Gateway commands."""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any

from runtime.core.events import utc_now
from runtime.storage.migrations import connect


class SqliteCommandStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection
        self._lock = threading.Lock()

    @classmethod
    def open(cls, path: str) -> "SqliteCommandStore":
        return cls(connect(path))

    def get(self, command_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM command_receipts WHERE command_id = ?", (command_id,)).fetchone()
        if row is None:
            return None
        return {"command_id": row["command_id"], "trace_id": row["trace_id"], "session_id": row["session_id"],
                "status": row["status"], "result": json.loads(row["result"] or "{}")}

    def reserve(self, command_id: str, trace_id: str) -> bool:
        now = utc_now()
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO command_receipts (command_id,trace_id,status,result,created_at,updated_at) VALUES (?,?, 'processing','{}',?,?)",
                    (command_id, trace_id, now, now),
                )
                self._conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def complete(self, command_id: str, *, status: str, session_id: str | None, result: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE command_receipts SET status=?,session_id=?,result=?,updated_at=? WHERE command_id=?",
                (status, session_id, json.dumps(result, ensure_ascii=False, default=str), utc_now(), command_id),
            )
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()

