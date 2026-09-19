"""迁移执行器。

按文件名顺序执行 migrations/*.sql，已执行的版本记录在 schema_migrations 表，
重复启动不会重复执行（幂等）。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from config.paths import PROJECT_ROOT

MIGRATIONS_DIR = PROJECT_ROOT / "migrations"

_TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def available_migrations(directory: Path | None = None) -> list[Path]:
    """返回待执行的迁移文件（按文件名排序）。"""
    base = directory or MIGRATIONS_DIR
    if not base.exists():
        return []
    return sorted(base.glob("*.sql"))


def apply_migrations(conn: sqlite3.Connection, directory: Path | None = None) -> list[str]:
    """执行未应用的迁移，返回本次应用的版本列表。"""
    conn.execute(_TRACKING_TABLE)
    applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    executed: list[str] = []
    for path in available_migrations(directory):
        version = path.stem
        if version in applied:
            continue
        conn.executescript(path.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
        executed.append(version)
    conn.commit()
    return executed