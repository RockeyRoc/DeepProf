"""SQLite persistence for resource metadata, documents, chunks and audit rows.

This module intentionally deals in dictionaries so Runtime does not import the
product-level ``library`` package.  The library service owns domain conversion.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from runtime.core.events import new_id, utc_now
from runtime.storage.migrations import connect


class RetrievalIndexProfileMismatch(RuntimeError):
    """A query was about to compare vectors from an incompatible index profile."""


def _bm25_terms(text: str) -> list[str]:
    normalized = str(text or "").lower()
    terms = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", normalized)
    output: list[str] = []
    for term in terms:
        if re.fullmatch(r"[\u4e00-\u9fff]+", term):
            output.extend(term[index:index + 2] for index in range(max(1, len(term) - 1)))
            if len(term) == 1:
                output.append(term)
        else:
            output.append(term)
    return output


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
        retrieval_profile: dict[str, Any] | None = None,
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
            "SELECT c.*, r.title, r.source_url, r.course_id, r.tags, r.visibility, r.owner_id, "
            "(SELECT lo.parameters FROM library_operations lo WHERE lo.resource_id=c.resource_id "
            "AND lo.action='index' AND lo.status='success' ORDER BY lo.created_at DESC LIMIT 1) AS index_parameters "
            "FROM library_chunks c JOIN library_resources r ON r.resource_id = c.resource_id "
            f"WHERE {' AND '.join(clauses)} ORDER BY c.resource_id, c.ordinal",
            params,
        ).fetchall()
        required_tags = {str(tag) for tag in tags if str(tag)}
        hits: list[dict[str, Any]] = []
        mismatched_profiles: set[str] = set()
        saw_compatible_profile = False
        for row in rows:
            row_tags = set(json.loads(row["tags"] or "[]"))
            if required_tags and not required_tags.issubset(row_tags):
                continue
            vector = [float(value) for value in json.loads(row["vector"] or "[]")]
            if retrieval_profile:
                try:
                    indexed_parameters = json.loads(row["index_parameters"] or "{}")
                except (TypeError, json.JSONDecodeError):
                    indexed_parameters = {}
                stored_profile = indexed_parameters.get("retrieval_profile") if isinstance(indexed_parameters, dict) else None
                if isinstance(stored_profile, dict):
                    stored_id = str(stored_profile.get("config_id") or "")
                else:
                    # Rows written before the profile field existed are known to
                    # use the project's default 384-dim hashing encoder only.
                    is_legacy_default = (
                        retrieval_profile.get("embedding_model") == "hashing-char-token-v1"
                        and retrieval_profile.get("embedding_dimension") == 384
                        and retrieval_profile.get("chunking_version") == "page-char-overlap-v1:size=800:overlap=120"
                        and retrieval_profile.get("reranker_version") == "none"
                        and len(vector) == 384
                    )
                    stored_id = str(retrieval_profile.get("config_id") or "") if is_legacy_default else "legacy-unversioned"
                if stored_id != str(retrieval_profile.get("config_id") or ""):
                    mismatched_profiles.add(stored_id or "missing-profile")
                    continue
                if len(vector) != int(retrieval_profile.get("embedding_dimension") or 0):
                    raise RetrievalIndexProfileMismatch("retrieval_index_dimension_mismatch")
                saw_compatible_profile = True
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
        if retrieval_profile and mismatched_profiles and not saw_compatible_profile:
            raise RetrievalIndexProfileMismatch("retrieval_index_profile_mismatch:" + ",".join(sorted(mismatched_profiles)))
        return hits[: max(1, min(int(top_k), 50))]

    def search_bm25(
        self,
        query: str,
        *,
        top_k: int = 50,
        course_id: str | None = None,
        resource_type: str | None = None,
        status: str = "active",
        tags: Iterable[str] = (),
        owner_id: str = "local",
        k1: float = 1.2,
        b: float = 0.75,
    ) -> list[dict[str, Any]]:
        """Offline BM25 over eligible frozen chunks; no vector score threshold applies."""
        if status != "active" or not _bm25_terms(query):
            return []
        clauses = ["r.status = ?", "(r.visibility = 'public' OR r.owner_id = ?)", "c.reliable = 1"]
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
            f"WHERE {' AND '.join(clauses)} ORDER BY c.resource_id, c.ordinal", params).fetchall()
        required_tags = {str(tag) for tag in tags if str(tag)}
        documents: list[tuple[sqlite3.Row, dict[str, int], int]] = []
        document_frequency: dict[str, int] = {}
        for row in rows:
            row_tags = set(json.loads(row["tags"] or "[]"))
            if required_tags and not required_tags.issubset(row_tags):
                continue
            counts: dict[str, int] = {}
            for term in _bm25_terms(row["text"]):
                counts[term] = counts.get(term, 0) + 1
            documents.append((row, counts, sum(counts.values())))
            for term in counts:
                document_frequency[term] = document_frequency.get(term, 0) + 1
        if not documents:
            return []
        query_terms = set(_bm25_terms(query))
        average_length = sum(length for _, _, length in documents) / len(documents) or 1.0
        hits: list[dict[str, Any]] = []
        for row, counts, length in documents:
            score_value = 0.0
            for term in query_terms:
                frequency = counts.get(term, 0)
                if not frequency:
                    continue
                df = document_frequency.get(term, 0)
                inverse = math.log(1.0 + (len(documents) - df + 0.5) / (df + 0.5))
                denominator = frequency + k1 * (1.0 - b + b * length / average_length)
                score_value += inverse * frequency * (k1 + 1.0) / denominator
            if score_value <= 0:
                continue
            hits.append({"document_id": row["document_id"], "chunk_id": row["chunk_id"],
                "resource_id": row["resource_id"], "course_id": row["course_id"],
                "page": int(row["page"]), "printed_page": row["printed_page"],
                "chapter": row["chapter"] or row["section"] or "", "ordinal": int(row["ordinal"]),
                "section": row["section"], "text": row["text"],
                "source": row["source_url"] or row["title"], "score": score_value,
                "retrieval_stage_scores": {"bm25": score_value}})
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
                index_parameters = {"chunks": len(chunks), "media_type": media_type}
                if isinstance(operation.get("parameters"), dict):
                    for key in ("retrieval_profile", "chunk_size", "chunk_overlap", "chunking_strategy"):
                        if key in operation["parameters"]:
                            index_parameters[key] = operation["parameters"][key]
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
                        json.dumps(index_parameters, ensure_ascii=False),
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
