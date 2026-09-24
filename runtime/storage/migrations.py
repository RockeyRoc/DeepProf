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

CREATE TABLE IF NOT EXISTS command_receipts (
    command_id TEXT PRIMARY KEY,
    trace_id   TEXT NOT NULL,
    session_id TEXT,
    status     TEXT NOT NULL,
    result     TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attempts (
    attempt_id TEXT PRIMARY KEY,
    learner_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    trace_id TEXT NOT NULL DEFAULT '',
    item_id TEXT NOT NULL,
    scored_concept_id TEXT NOT NULL,
    concept_ids TEXT NOT NULL DEFAULT '[]',
    is_correct INTEGER CHECK (is_correct IN (0, 1) OR is_correct IS NULL),
    answer_value TEXT NOT NULL DEFAULT '',
    hint_count INTEGER NOT NULL DEFAULT 0,
    grading_source TEXT NOT NULL,
    confidence REAL NOT NULL,
    bank_version TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_attempts_learner_concept ON attempts (learner_id, scored_concept_id, created_at);
CREATE INDEX IF NOT EXISTS idx_attempts_session ON attempts (session_id, created_at);

CREATE TABLE IF NOT EXISTS learner_estimates (
    learner_id TEXT NOT NULL,
    course_id TEXT NOT NULL,
    concept_id TEXT NOT NULL,
    model_version TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    parameters TEXT NOT NULL,
    mastery REAL NOT NULL CHECK (mastery >= 0 AND mastery <= 1),
    evidence_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (learner_id, course_id, concept_id, model_version, config_hash)
);

CREATE TABLE IF NOT EXISTS bkt_observations (
    attempt_id TEXT PRIMARY KEY,
    learner_id TEXT NOT NULL,
    course_id TEXT NOT NULL,
    concept_id TEXT NOT NULL,
    predicted_correct REAL NOT NULL,
    mastery_before REAL NOT NULL,
    mastery_after REAL NOT NULL,
    evidence_count INTEGER NOT NULL,
    model_version TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS learner_event_outbox (
    event_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    delivered_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_learner_outbox_pending ON learner_event_outbox (delivered_at, created_at);

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
    printed_page INTEGER,
    chapter     TEXT NOT NULL DEFAULT '',
    reliable    INTEGER NOT NULL DEFAULT 1,
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
    chunk_columns = {row["name"] for row in connection.execute("PRAGMA table_info(library_chunks)")}
    for name, declaration in (
        ("printed_page", "INTEGER"),
        ("chapter", "TEXT NOT NULL DEFAULT ''"),
        ("reliable", "INTEGER NOT NULL DEFAULT 1"),
    ):
        if name not in chunk_columns:
            connection.execute(f"ALTER TABLE library_chunks ADD COLUMN {name} {declaration}")
    _upgrade_attempts(connection)
    connection.commit()
    return connection


def connect_memory() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    _upgrade_attempts(connection)
    return connection


def _upgrade_attempts(connection: sqlite3.Connection) -> None:
    attempt_columns = {row["name"] for row in connection.execute("PRAGMA table_info(attempts)")}
    for name, declaration in (
        ("course_id", "TEXT NOT NULL DEFAULT ''"),
        ("concept_ids", "TEXT NOT NULL DEFAULT '[]'"),
        ("eligible", "INTEGER NOT NULL DEFAULT 0"),
        ("skip_reason", "TEXT NOT NULL DEFAULT 'legacy_unreviewed'"),
    ):
        if name not in attempt_columns:
            connection.execute(f"ALTER TABLE attempts ADD COLUMN {name} {declaration}")
    connection.commit()
    table_sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='attempts'"
    ).fetchone()[0]
    if "is_correct integer not null" in str(table_sql).lower():
        # Older installations required a boolean score, which made it impossible
        # to preserve a manual-pending Attempt without falsely labeling it wrong.
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute("DROP INDEX IF EXISTS idx_attempts_learner_concept")
            connection.execute("DROP INDEX IF EXISTS idx_attempts_session")
            connection.execute("DROP INDEX IF EXISTS idx_attempts_first_eligible")
            connection.execute('''CREATE TABLE attempts_m2 (
                attempt_id TEXT PRIMARY KEY, learner_id TEXT NOT NULL, session_id TEXT NOT NULL,
                trace_id TEXT NOT NULL DEFAULT '', item_id TEXT NOT NULL, scored_concept_id TEXT NOT NULL,
                concept_ids TEXT NOT NULL DEFAULT '[]',
                is_correct INTEGER CHECK (is_correct IN (0, 1) OR is_correct IS NULL),
                answer_value TEXT NOT NULL DEFAULT '', hint_count INTEGER NOT NULL DEFAULT 0,
                grading_source TEXT NOT NULL, confidence REAL NOT NULL, bank_version TEXT NOT NULL,
                created_at TEXT NOT NULL, course_id TEXT NOT NULL DEFAULT '', eligible INTEGER NOT NULL DEFAULT 0,
                skip_reason TEXT NOT NULL DEFAULT 'legacy_unreviewed'
            )''')
            columns = ("attempt_id,learner_id,session_id,trace_id,item_id,scored_concept_id,concept_ids,is_correct,answer_value,"
                       "hint_count,grading_source,confidence,bank_version,created_at,course_id,eligible,skip_reason")
            connection.execute(f"INSERT INTO attempts_m2 ({columns}) SELECT {columns} FROM attempts")
            connection.execute("DROP TABLE attempts")
            connection.execute("ALTER TABLE attempts_m2 RENAME TO attempts")
            connection.execute("CREATE INDEX idx_attempts_learner_concept ON attempts (learner_id, scored_concept_id, created_at)")
            connection.execute("CREATE INDEX idx_attempts_session ON attempts (session_id, created_at)")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_attempts_first_eligible "
        "ON attempts (learner_id, course_id, item_id, bank_version) WHERE eligible = 1"
    )
    connection.commit()
