"""SQLite persistence for resource metadata, documents, chunks and audit rows.

This module intentionally deals in dictionaries so Runtime does not import the
product-level ``library`` package.  The library service owns domain conversion.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from runtime.core.events import new_id, utc_now
from runtime.storage.migrations import connect


class SqliteResourceStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection
        self._lock = threading.RLock()
        self._conn.execute("PRAGMA foreign_keys = ON")

    @classmethod
    def open(cls, path: str | Path) -> "SqliteResourceStore":
        return cls(connect(path))

    def close(self) -> None:
        self._conn.close()

    def get(self, resource_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM library_resources WHERE resource_id = ?", (resource_id,)
        ).fetchone()
        return _resource_row(row) if row else None

    def find_by_hash(self, content_hash: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM library_resources WHERE content_hash = ? ORDER BY created_at",
            (content_hash,),
        ).fetchall()
        return [_resource_row(row) for row in rows]

    def list_resources(
        self,
        *,
        course_id: str | None = None,
        resource_type: str | None = None,
        status: str | None = None,
        owner_id: str | None = None,
        tags: Iterable[str] = (),
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if course_id:
            clauses.append("course_id = ?")
            params.append(course_id)
        if resource_type:
            clauses.append("type = ?")
            params.append(resource_type)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if owner_id:
            clauses.append("(visibility = 'public' OR owner_id = ?)")
            params.append(owner_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM library_resources{where} ORDER BY updated_at DESC", params
        ).fetchall()
        required_tags = {str(tag) for tag in tags if str(tag)}
        result = []
        for row in rows:
            record = _resource_row(row)
            if required_tags and not required_tags.issubset(set(record["tags"])):
                continue
            result.append(record)
        return result

    def preview(self, resource_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT chunk_id, document_id, resource_id, page, printed_page, chapter, reliable, ordinal, section, text "
            "FROM library_chunks WHERE resource_id = ? ORDER BY page, ordinal LIMIT ?",
            (resource_id, max(1, min(limit, 1000))),
        ).fetchall()
        return [dict(row) for row in rows]

    def search(
        self,
        query_vector: list[float],
        *,
        score: Callable[[list[float], list[float]], float],
        top_k: int = 5,
        course_id: str | None = None,
        resource_type: str | None = None,
        status: str = "active",
        tags: Iterable[str] = (),
        owner_id: str = "local",
        min_score: float = 0.10,
    ) -> list[dict[str, Any]]:
        # Draft and archived resources are never eligible textbook evidence.
        # Keep the status argument explicit for callers while preserving that
        # invariant even if a client asks for a non-active status.
        if status != "active":
            return []
        clauses = [
            "r.status = ?",
            "(r.visibility = 'public' OR r.owner_id = ?)",
            "c.reliable = 1",
        ]
        params: list[Any] = [status, owner_id]
        if course_id:
            clauses.append("r.course_id = ?")
            params.append(course_id)
        if resource_type:
            clauses.append("r.type = ?")
            params.append(resource_type)
        rows = self._conn.execute(
            "SELECT c.*, r.title, r.source_url, r.course_id, r.tags, r.visibility, r.owner_id "
            "FROM library_chunks c JOIN library_resources r ON r.resource_id = c.resource_id "
            f"WHERE {' AND '.join(clauses)} ORDER BY c.resource_id, c.ordinal",
            params,
        ).fetchall()
        required_tags = {str(tag) for tag in tags if str(tag)}
        hits: list[dict[str, Any]] = []
        for row in rows:
            row_tags = set(json.loads(row["tags"] or "[]"))
            if required_tags and not required_tags.issubset(row_tags):
                continue
            vector = [float(value) for value in json.loads(row["vector"] or "[]")]
            similarity = float(score(query_vector, vector))
            if similarity < min_score:
                continue
            hits.append(
                {
                    "document_id": row["document_id"],
                    "chunk_id": row["chunk_id"],
                    "resource_id": row["resource_id"],
                    "course_id": row["course_id"],
                    "page": int(row["page"]),
                    "printed_page": row["printed_page"],
                    "chapter": row["chapter"] or row["section"] or "",
                    "ordinal": int(row["ordinal"]),
                    "section": row["section"],
                    "text": row["text"],
                    "source": row["source_url"] or row["title"],
                    "score": similarity,
                }
            )
        hits.sort(key=lambda item: (-item["score"], item["resource_id"], item["ordinal"]))
        return hits[: max(1, min(int(top_k), 50))]

    def ingest(
        self,
        resource: dict[str, Any],
        *,
        filename: str,
        media_type: str,
        page_count: int | None = None,
        chunks: list[dict[str, Any]],
        operation: dict[str, Any],
    ) -> None:
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                self._conn.execute(
                    "INSERT INTO library_resources "
                    "(resource_id, document_id, course_id, type, title, tags, source_type, source_url, "
                    "license, content_hash, status, owner_id, visibility, version_of, created_by, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        resource["resource_id"], resource["document_id"], resource.get("course_id"),
                        resource["type"], resource.get("title", ""), json.dumps(resource.get("tags", []), ensure_ascii=False),
                        resource["source_type"], resource["source_url"], resource.get("license", ""),
                        resource["hash"], resource.get("status", "draft"), resource.get("owner_id", "local"),
                        resource.get("visibility", "private"), resource.get("version_of"),
                        resource.get("created_by", "local"), resource["created_at"], resource["updated_at"],
                    ),
                )
                self._conn.execute(
                    "INSERT INTO library_documents (document_id, resource_id, filename, media_type, page_count) "
                    "VALUES (?,?,?,?,?)",
                    (resource["document_id"], resource["resource_id"], filename, media_type, int(page_count or len(chunks))),
                )
                self._conn.executemany(
                    "INSERT INTO library_chunks "
                    "(chunk_id, document_id, resource_id, page, printed_page, chapter, reliable, ordinal, section, text, vector, indexed_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    [
                        (
                            chunk["chunk_id"], chunk["document_id"], chunk["resource_id"], chunk["page"],
                            chunk.get("printed_page"), chunk.get("chapter", ""), int(bool(chunk.get("reliable", True))),
                            chunk["ordinal"], chunk.get("section", ""), chunk["text"],
                            json.dumps(chunk.get("vector", [])), utc_now(),
                        )
                        for chunk in chunks
                    ],
                )
                self._conn.execute(
                    "INSERT INTO library_operations "
                    "(operation_id, action, resource_id, source_url, actor_id, status, error, parameters, content_hash, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        operation.get("operation_id", new_id("op")), operation.get("action", "import"),
                        resource["resource_id"], operation.get("source_url", resource["source_url"]),
                        operation.get("actor_id", "local"), operation.get("status", "success"),
                        operation.get("error", ""), json.dumps(operation.get("parameters", {}), ensure_ascii=False),
                        resource["hash"], operation.get("created_at", utc_now()),
                    ),
                )
                self._conn.execute(
                    "INSERT INTO library_operations "
                    "(operation_id, action, resource_id, source_url, actor_id, status, error, parameters, content_hash, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        new_id("op"),
                        "index",
                        resource["resource_id"],
                        resource["source_url"],
                        operation.get("actor_id", "local"),
                        "success",
                        "",
                        json.dumps(
                            {"chunks": len(chunks), "media_type": media_type},
                            ensure_ascii=False,
                        ),
                        resource["hash"],
                        utc_now(),
                    ),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def record_operation(self, operation: dict[str, Any]) -> None:
        self._conn.execute(
            "INSERT INTO library_operations "
            "(operation_id, action, resource_id, source_url, actor_id, status, error, parameters, content_hash, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                operation.get("operation_id", new_id("op")), operation.get("action", "import"),
                operation.get("resource_id"), operation.get("source_url", ""), operation.get("actor_id", "local"),
                operation.get("status", "success"), operation.get("error", ""),
                json.dumps(operation.get("parameters", {}), ensure_ascii=False), operation.get("hash", ""),
                operation.get("created_at", utc_now()),
            ),
        )
        self._conn.commit()

    def activate(self, resource_id: str) -> dict[str, Any] | None:
        with self._lock:
            self._conn.execute(
                "UPDATE library_resources SET status = 'active', updated_at = ? WHERE resource_id = ?",
                (utc_now(), resource_id),
            )
            self._conn.commit()
        return self.get(resource_id)


def _resource_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "resource_id": row["resource_id"],
        "document_id": row["document_id"],
        "course_id": row["course_id"],
        "type": row["type"],
        "title": row["title"],
        "tags": list(json.loads(row["tags"] or "[]")),
        "source_type": row["source_type"],
        "source_url": row["source_url"],
        "license": row["license"],
        "hash": row["content_hash"],
        "status": row["status"],
        "owner_id": row["owner_id"],
        "visibility": row["visibility"],
        "version_of": row["version_of"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


__all__ = ["SqliteResourceStore"]
