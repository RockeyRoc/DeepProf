"""SQLite 存储测试（D-3 / §17.3 / §18.2）。"""
from __future__ import annotations

from pathlib import Path

from config import settings
from runtime.core.events import EventType, RuntimeEvent
from runtime.core.message import Message
from runtime.core.session import Session
from runtime.memory.base import MemoryQuery, MemoryRecord, MemoryType
from runtime.memory.sqlite_memory import SqliteMemoryStore
from runtime.storage.migrations import apply_migrations
from runtime.storage.sqlite_store import (
    SqliteDatabase,
    SqliteEventStore,
    SqliteProfileStore,
    SqliteSessionStore,
)


def test_event_append_is_idempotent(db):
    """重复 event_id 不重复写入（重复事件不重复记分）。"""
    store = SqliteEventStore(db)
    event = RuntimeEvent(EventType.TOOL_COMPLETED.value, {"tool": "x"}, session_id="s1")

    first = store.append(event)
    second = store.append(event)

    assert first.sequence == second.sequence
    assert len(store.list("s1")) == 1


def test_events_ordered_by_sequence_and_queryable_by_trace(db):
    """同一会话按 sequence 有序；同一次请求可按 trace_id 汇总（可审计）。"""
    store = SqliteEventStore(db)
    for index in range(3):
        store.append(
            RuntimeEvent(
                EventType.MODEL_STREAM_DELTA.value,
                {"text": str(index)},
                session_id="s1",
                trace_id="trace_a",
            )
        )
    store.append(
        RuntimeEvent(EventType.AGENT_STARTED.value, {}, session_id="s2", trace_id="trace_b")
    )

    assert [event.payload["text"] for event in store.list("s1")] == ["0", "1", "2"]
    assert len(store.list_by_trace("trace_a")) == 3
    assert len(store.list_by_trace("trace_b")) == 1


def test_session_survives_restart(tmp_path):
    """重启后会话可恢复（MVP-1 验收项）。"""
    path = tmp_path / "restart.db"
    session = Session(learner_id="learner_a")
    session.append(Message.user("上次聊到递归"))

    first_db = SqliteDatabase(path)
    SqliteSessionStore(first_db).save(session)
    first_db.close()

    second_db = SqliteDatabase(path)
    try:
        restored = SqliteSessionStore(second_db).load(session.session_id)
    finally:
        second_db.close()

    assert restored is not None
    assert restored.learner_id == "learner_a"
    assert restored.messages[0].content == "上次聊到递归"


def test_session_store_save_is_upsert(db):
    """同一 session 多次保存只保留最新状态。"""
    store = SqliteSessionStore(db)
    session = Session(learner_id="learner_a")
    store.save(session)
    session.append(Message.user("追加"))
    store.save(session)

    assert store.list_ids("learner_a") == [session.session_id]
    assert len(store.load(session.session_id).messages) == 1


def test_session_delete_and_missing_load(db):
    store = SqliteSessionStore(db)
    session = Session(learner_id="learner_a")
    store.save(session)
    store.delete(session.session_id)

    assert store.load(session.session_id) is None
    assert store.list_ids() == []


def test_migrations_are_idempotent(db):
    """迁移重复执行不得报错，也不应重复应用。"""
    assert apply_migrations(db.connection) == []


def test_memory_records_are_isolated_per_learner(db):
    """一个学生不能读到另一个学生的记忆（§17.3 用户级隔离）。"""
    store = SqliteMemoryStore(db)
    store.write(
        [
            MemoryRecord(
                learner_id="learner_a",
                memory_type=MemoryType.LONG_TERM,
                content="对递归的理解偏弱",
                source="session:s1",
            ),
            MemoryRecord(
                learner_id="learner_b",
                memory_type=MemoryType.LONG_TERM,
                content="已掌握递归",
                source="session:s2",
            ),
        ]
    )

    assert len(store.read(MemoryQuery(learner_id="learner_a"))) == 1
    assert store.read(MemoryQuery(learner_id="learner_a"))[0].content == "对递归的理解偏弱"


def test_memory_expiry_is_filtered_by_default(db):
    """过期记忆默认读不到，但可显式包含（保留审计）。"""
    store = SqliteMemoryStore(db)
    store.write(
        [
            MemoryRecord(
                learner_id="learner_a",
                memory_type=MemoryType.WORKING,
                content="临时任务",
                source="session:s1",
                expires_at="2000-01-01T00:00:00+00:00",
            )
        ]
    )

    assert store.read(MemoryQuery(learner_id="learner_a")) == []
    assert len(store.read(MemoryQuery(learner_id="learner_a", include_expired=True))) == 1


def test_memory_forget_keeps_row_for_audit(db):
    """软删除：默认读不到，但记录仍在库中可审计。"""
    store = SqliteMemoryStore(db)
    record = MemoryRecord(
        learner_id="learner_a", memory_type=MemoryType.AFFECTIVE, content="喜欢类比", source="s1"
    )
    store.write([record])

    assert store.delete([record.record_id]) == 1
    assert store.read(MemoryQuery(learner_id="learner_a")) == []
    assert len(store.read(MemoryQuery(learner_id="learner_a", include_deleted=True))) == 1


def test_memory_type_filter_accepts_enum_and_string(db):
    """memory_type 既可传枚举也可传字符串，两种写法都要能过滤到同一条。"""
    store = SqliteMemoryStore(db)
    store.write(
        [
            MemoryRecord(
                learner_id="learner_a",
                memory_type=MemoryType.WORKING,
                content="本周目标",
                source="session:s1",
            )
        ]
    )

    assert len(store.read(MemoryQuery(learner_id="learner_a", memory_type=MemoryType.WORKING))) == 1
    assert len(store.read(MemoryQuery(learner_id="learner_a", memory_type="working"))) == 1
    assert store.read(MemoryQuery(learner_id="learner_a", memory_type=MemoryType.LONG_TERM)) == []


def test_profile_store_upserts_by_model_version(db):
    """同一知识点同一模型重复写入应覆盖而不是新增行。"""
    store = SqliteProfileStore(db)
    base = {
        "learner_id": "learner_a",
        "concept_id": "concept_recursion",
        "model_type": "BKT",
        "model_version": "0.1.0",
        "evidence_count": 3,
        "uncertainty": 0.4,
        "updated_at": "2026-09-17T00:00:00+00:00",
    }
    store.save_estimates([{**base, "estimate": 0.3}])
    store.save_estimates([{**base, "estimate": 0.55, "evidence_count": 5}])

    rows = store.read_estimates("learner_a", "concept_recursion")
    assert len(rows) == 1
    assert rows[0]["estimate"] == 0.55
    assert rows[0]["evidence_count"] == 5


def test_sqlite_path_defaults_to_user_home(tmp_path, monkeypatch):
    """配置留空时默认落在用户数据目录（~/.deepprof/sessions），不依赖 CWD / 安装目录。

    显式传入（含本地 .env 覆盖）的相对路径仍相对项目根（开发模式行为不变），
    因此这里先清空 settings 里的值再验证默认解析。
    """
    monkeypatch.setattr(settings, "sqlite_path", "")
    monkeypatch.setenv("DEEPPROF_HOME", str(tmp_path / "home"))
    default = SqliteDatabase()
    try:
        assert default.path == str(tmp_path / "home" / "sessions" / "deepprof.db")
        assert Path(default.path).parent.is_dir()  # 目录已自动创建
    finally:
        default.close()

    # 相对路径显式传入：相对项目根（与旧语义一致）
    relative = SqliteDatabase(tmp_path / "explicit.db")
    try:
        assert relative.path.endswith("explicit.db")
    finally:
        relative.close()