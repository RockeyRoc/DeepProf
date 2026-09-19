-- 0001_init.sql
-- DeepProf MVP 初始 schema（DESIGNv0.4 §17.3：MVP 数据库只保存在本机磁盘）
-- 原则：Session（交互事实）与 Memory（长期学情）分表，按 learner_id 隔离。

-- ---------- 事件轨迹：追加式、按 event_id 幂等 ----------
CREATE TABLE IF NOT EXISTS events (
    sequence   INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id   TEXT    NOT NULL UNIQUE,
    session_id TEXT    NOT NULL DEFAULT '',
    trace_id   TEXT    NOT NULL DEFAULT '',
    type       TEXT    NOT NULL,
    payload    TEXT    NOT NULL DEFAULT '{}',
    source     TEXT    NOT NULL DEFAULT '',
    timestamp  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_session ON events (session_id, sequence);
CREATE INDEX IF NOT EXISTS idx_events_trace   ON events (trace_id);

-- ---------- 会话：可恢复、可分支 ----------
CREATE TABLE IF NOT EXISTS sessions (
    session_id         TEXT PRIMARY KEY,
    learner_id         TEXT NOT NULL DEFAULT '',
    messages           TEXT NOT NULL DEFAULT '[]',
    metadata           TEXT NOT NULL DEFAULT '{}',
    created_at         TEXT NOT NULL DEFAULT '',
    updated_at         TEXT NOT NULL DEFAULT '',
    parent_session_id  TEXT NOT NULL DEFAULT '',
    compacted_summary  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_sessions_learner ON sessions (learner_id);

-- ---------- 记忆：五类记忆共用一表，带来源/置信度/过期/可撤回 ----------
CREATE TABLE IF NOT EXISTS memory_records (
    record_id     TEXT PRIMARY KEY,
    learner_id    TEXT NOT NULL,
    memory_type   TEXT NOT NULL,
    key           TEXT NOT NULL DEFAULT '',
    content       TEXT NOT NULL,
    source        TEXT NOT NULL DEFAULT '',
    confidence    REAL NOT NULL DEFAULT 0.5,
    created_at    TEXT NOT NULL DEFAULT '',
    updated_at    TEXT NOT NULL DEFAULT '',
    expires_at    TEXT NOT NULL DEFAULT '',
    revocable     INTEGER NOT NULL DEFAULT 1,
    deleted       INTEGER NOT NULL DEFAULT 0,
    model_version TEXT NOT NULL DEFAULT '',
    metadata      TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_memory_learner
    ON memory_records (learner_id, memory_type, deleted);

-- ---------- 学情画像：LearnerEstimate（§18.2） ----------
CREATE TABLE IF NOT EXISTS learner_estimates (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    learner_id     TEXT NOT NULL,
    concept_id     TEXT NOT NULL,
    model_type     TEXT NOT NULL,
    model_version  TEXT NOT NULL DEFAULT '',
    estimate       REAL NOT NULL,
    evidence_count INTEGER NOT NULL DEFAULT 0,
    uncertainty    REAL,
    updated_at     TEXT NOT NULL DEFAULT '',
    metadata       TEXT NOT NULL DEFAULT '{}',
    UNIQUE (learner_id, concept_id, model_type, model_version)
);
CREATE INDEX IF NOT EXISTS idx_estimates_learner
    ON learner_estimates (learner_id, concept_id);