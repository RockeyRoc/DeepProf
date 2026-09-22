"""SQLite schema 与连接助手。MVP 数据库只放本机磁盘，不放网络共享目录。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS events (
    event_id   TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    trace_id   TEXT NOT NULL DEFAULT '',
    sequence   INTEGER NOT NULL,
    type       TEXT NOT NULL,
    payload    TEXT NOT NULL DEFAULT '{}',
    source     TEXT NOT NULL DEFAULT 'runtime',
    timestamp  TEXT NOT NULL,
    client_id  TEXT,
    surface    TEXT,
    audience   TEXT,
    UNIQUE (session_id, sequence)
);

CREATE INDEX IF NOT EXISTS idx_events_session ON events (session_id, sequence);
CREATE INDEX IF NOT EXISTS idx_events_trace ON events (trace_id);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    learner_id TEXT NOT NULL,
    title      TEXT NOT NULL DEFAULT '',
    parent_id  TEXT,
    payload    TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_learner ON sessions (learner_id);

CREATE TABLE IF NOT EXISTS memory_records (
    record_id   TEXT PRIMARY KEY,
    learner_id  TEXT NOT NULL,
    scope       TEXT NOT NULL,
    content     TEXT NOT NULL,
    source      TEXT NOT NULL,
    confidence  REAL NOT NULL,
    revocable   INTEGER NOT NULL DEFAULT 1,
    expires_at  TEXT,
    concept_ids TEXT NOT NULL DEFAULT '[]',
    tags        TEXT NOT NULL DEFAULT '[]',
    metadata    TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memory_learner ON memory_records (learner_id, scope);

CREATE TABLE IF NOT EXISTS library_resources (
    resource_id  TEXT PRIMARY KEY,
    document_id  TEXT NOT NULL,
    course_id    TEXT,
    type         TEXT NOT NULL,
    title        TEXT NOT NULL DEFAULT '',
    tags         TEXT NOT NULL DEFAULT '[]',
    source_type  TEXT NOT NULL,
    source_url   TEXT NOT NULL,
    license      TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'draft',
    owner_id     TEXT NOT NULL DEFAULT 'local',
    visibility   TEXT NOT NULL DEFAULT 'private',
    version_of   TEXT,
    created_by   TEXT NOT NULL DEFAULT 'local',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_library_resources_hash ON library_resources (content_hash);
CREATE INDEX IF NOT EXISTS idx_library_resources_course ON library_resources (course_id, status);
CREATE INDEX IF NOT EXISTS idx_library_resources_owner ON library_resources (owner_id, visibility);

CREATE TABLE IF NOT EXISTS library_documents (
    document_id TEXT PRIMARY KEY,
    resource_id TEXT NOT NULL,
    filename    TEXT NOT NULL,
    media_type  TEXT NOT NULL,
    page_count  INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (resource_id) REFERENCES library_resources(resource_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS library_chunks (
    chunk_id    TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    page        INTEGER NOT NULL,
    ordinal     INTEGER NOT NULL,
    section     TEXT NOT NULL DEFAULT '',
    text        TEXT NOT NULL,
    vector      TEXT NOT NULL,
    indexed_at  TEXT NOT NULL,
    FOREIGN KEY (document_id) REFERENCES library_documents(document_id) ON DELETE CASCADE,
    FOREIGN KEY (resource_id) REFERENCES library_resources(resource_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_library_chunks_resource ON library_chunks (resource_id, page, ordinal);

CREATE TABLE IF NOT EXISTS library_operations (
    operation_id TEXT PRIMARY KEY,
    action       TEXT NOT NULL,
    resource_id  TEXT,
    source_url   TEXT NOT NULL DEFAULT '',
    actor_id     TEXT NOT NULL DEFAULT 'local',
    status       TEXT NOT NULL,
    error        TEXT NOT NULL DEFAULT '',
    parameters   TEXT NOT NULL DEFAULT '{}',
    content_hash TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_library_operations_resource ON library_operations (resource_id, created_at);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    """打开（必要时创建）数据库并应用 schema。"""
    target = Path(path)
    if target.parent and str(target.parent) not in ("", "."):
        target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(target), check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    return connection


def connect_memory() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    return connection
