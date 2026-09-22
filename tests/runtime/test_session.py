"""Session 与 EventStore：分支、压缩、幂等与 sequence 单调性。"""

from __future__ import annotations

import pytest

from runtime.core.events import InMemoryEventStore, RuntimeEvent, new_id
from runtime.core.message import Message
from runtime.core.session import InMemorySessionStore, Session
from runtime.storage.sqlite_store import SqliteEventStore, SqliteSessionStore


def test_append_and_context_order():
    session = Session()
    session.append(Message(role="user", content="a"))
    session.append(Message(role="assistant", content="b"))
    assert [m.content for m in session.context()] == ["a", "b"]


def test_fork_copies_messages_and_records_parent():
    session = Session(title="主线")
    session.append(Message(role="user", content="a"))
    forked = session.fork(title="分支")

    assert forked.parent_id == session.session_id
    assert forked.session_id != session.session_id
    assert [m.content for m in forked.messages] == ["a"]

    # 分支互不影响
    forked.append(Message(role="assistant", content="b"))
    assert len(session.messages) == 1


def test_compact_keeps_recent_and_summarizes():
    session = Session()
    for index in range(10):
        session.append(Message(role="user", content=f"m{index}"))

    before, after = session.compact(keep=3)
    assert before == 10
    assert after == 4  # 1 条摘要 + 3 条保留
    assert session.messages[0].metadata.get("compacted") is True
    assert [m.content for m in session.messages[-3:]] == ["m7", "m8", "m9"]


def test_compact_is_noop_when_short():
    session = Session()
    session.append(Message(role="user", content="a"))
    assert session.compact(keep=10) == (1, 1)


def test_session_round_trip():
    session = Session(title="t")
    session.append(Message(role="user", content="a"))
    restored = Session.from_dict(session.to_dict())
    assert restored.session_id == session.session_id
    assert restored.messages[0].content == "a"


def test_message_rejects_unknown_role():
    with pytest.raises(ValueError):
        Message(role="wizard")


# ---- EventStore ----

def test_sequence_is_monotonic_per_session():
    store = InMemoryEventStore()
    store.append(RuntimeEvent(type="a", session_id="s1"))
    store.append(RuntimeEvent(type="b", session_id="s1"))
    store.append(RuntimeEvent(type="c", session_id="s2"))
    assert [e.sequence for e in store.replay("s1")] == [1, 2]
    assert [e.sequence for e in store.replay("s2")] == [1]


def test_replay_from_sequence_supports_reconnect():
    store = InMemoryEventStore()
    for index in range(5):
        store.append(RuntimeEvent(type=f"e{index}", session_id="s1"))
    replayed = store.replay("s1", from_sequence=3)
    assert [e.sequence for e in replayed] == [4, 5]
    assert store.last_sequence("s1") == 5


def test_duplicate_event_id_is_idempotent():
    store = InMemoryEventStore()
    event = RuntimeEvent(type="a", session_id="s1", event_id="evt-fixed")
    first = store.append(event)
    second = store.append(RuntimeEvent(type="a", session_id="s1", event_id="evt-fixed"))
    assert first.sequence == second.sequence == 1
    assert len(store.replay("s1")) == 1


def test_event_envelope_has_required_fields():
    event = RuntimeEvent(type="session.started", session_id="s1", trace_id="t1").to_dict()
    for field in ("event_id", "session_id", "trace_id", "sequence", "type", "payload", "source", "timestamp"):
        assert field in event


# ---- SQLite 持久化 ----

def test_sqlite_event_store_idempotent_and_ordered(tmp_path):
    store = SqliteEventStore.open(str(tmp_path / "e.sqlite"))
    try:
        fixed = "evt-fixed"
        store.append(RuntimeEvent(type="a", session_id="s1", event_id=fixed))
        store.append(RuntimeEvent(type="a", session_id="s1", event_id=fixed))
        store.append(RuntimeEvent(type="b", session_id="s1", event_id=new_id("evt")))

        events = store.replay("s1")
        assert [e.type for e in events] == ["a", "b"]
        assert [e.sequence for e in events] == [1, 2]
        assert store.last_sequence("s1") == 2
    finally:
        store.close()


def test_sqlite_event_payload_round_trip(tmp_path):
    store = SqliteEventStore.open(str(tmp_path / "e.sqlite"))
    try:
        store.append(
            RuntimeEvent(
                type="model.completed",
                payload={"usage": {"total_tokens": 7}, "model": "m"},
                session_id="s1",
                trace_id="t1",
            )
        )
        event = store.replay("s1")[0]
        assert event.payload["usage"]["total_tokens"] == 7
        assert event.trace_id == "t1"
    finally:
        store.close()


def test_sqlite_session_store_round_trip(tmp_path):
    store = SqliteSessionStore.open(str(tmp_path / "s.sqlite"))
    try:
        session = Session(learner_id="L1", title="t")
        session.append(Message(role="user", content="a"))
        store.save(session)

        restored = store.load(session.session_id)
        assert restored is not None
        assert restored.learner_id == "L1"
        assert restored.messages[0].content == "a"
        assert store.list("L1") == [session.session_id]
        assert store.list("other") == []
    finally:
        store.close()


def test_sqlite_session_save_is_upsert(tmp_path):
    store = SqliteSessionStore.open(str(tmp_path / "s.sqlite"))
    try:
        session = Session(title="first")
        store.save(session)
        session.title = "second"
        session.append(Message(role="user", content="x"))
        store.save(session)

        assert store.load(session.session_id).title == "second"
        assert store.list() == [session.session_id]
    finally:
        store.close()


def test_sqlite_stores_survive_reopen(tmp_path):
    path = str(tmp_path / "s.sqlite")
    store = SqliteSessionStore.open(path)
    session = Session(title="persisted")
    store.save(session)
    store.close()

    reopened = SqliteSessionStore.open(path)
    try:
        assert reopened.load(session.session_id).title == "persisted"
    finally:
        reopened.close()


def test_memory_store_is_learner_isolated():
    from runtime.memory.base import MemoryRecord
    from runtime.memory.sqlite_memory import SqliteMemoryStore

    store = SqliteMemoryStore(path=":memory:")
    store.write(
        [
            MemoryRecord(learner_id="A", scope="long_term", content="A 的记录", source="test", confidence=0.5),
            MemoryRecord(learner_id="B", scope="long_term", content="B 的记录", source="test", confidence=0.5),
        ]
    )
    assert [r.content for r in store.read({"learner_id": "A"})] == ["A 的记录"]
    assert [r.content for r in store.read({"learner_id": "B"})] == ["B 的记录"]