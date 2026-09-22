"""Memory 服务：写入校验、可撤回、事件记录。"""

from __future__ import annotations

import pytest

from runtime.core.errors import RuntimeFailure
from runtime.core.events import EventType
from runtime.memory.service import MemoryService
from runtime.memory.sqlite_memory import SqliteMemoryStore
from runtime.testing import make_service


def make_memory() -> MemoryService:
    return MemoryService(SqliteMemoryStore(path=":memory:"))


async def test_write_memory_returns_none():
    """契约冻结：write_memory 必须返回 None。"""
    service = make_service(memory=make_memory())
    result = await service.write_memory(
        [{"content": "掌握度 0.6", "scope": "long_term", "source": "diagnose", "confidence": 0.6}], {}
    )
    assert result is None


async def test_write_requires_source_confidence_scope():
    memory = make_memory()
    with pytest.raises(RuntimeFailure) as excinfo:
        await memory.write([{"content": "缺少来源", "scope": "long_term", "confidence": 0.5}], {})
    assert "source" in excinfo.value.details["fields"]


async def test_confidence_must_be_probability():
    memory = make_memory()
    with pytest.raises(RuntimeFailure):
        await memory.write(
            [{"content": "x", "scope": "long_term", "source": "s", "confidence": 1.7}], {}
        )


async def test_unknown_scope_rejected():
    memory = make_memory()
    with pytest.raises(RuntimeFailure):
        await memory.write([{"content": "x", "scope": "nonsense", "source": "s", "confidence": 0.5}], {})


async def test_non_revocable_requires_expiry():
    memory = make_memory()
    with pytest.raises(RuntimeFailure) as excinfo:
        await memory.write(
            [{"content": "x", "scope": "long_term", "source": "s", "confidence": 0.5, "revocable": False}], {}
        )
    assert "expires_at" in excinfo.value.details["fields"]


async def test_learner_id_comes_from_context():
    memory = make_memory()
    await memory.write(
        [{"content": "x", "scope": "long_term", "source": "s", "confidence": 0.5}],
        {"learner_id": "L9"},
    )
    records = await memory.read({}, {"learner_id": "L9"})
    assert len(records) == 1
    assert records[0]["learner_id"] == "L9"
    assert await memory.read({}, {"learner_id": "other"}) == []


async def test_memory_can_be_revoked():
    memory = make_memory()
    await memory.write(
        [{"content": "可撤回", "scope": "long_term", "source": "s", "confidence": 0.5}],
        {"learner_id": "L1"},
    )
    record_id = (await memory.read({}, {"learner_id": "L1"}))[0]["record_id"]
    assert memory.forget("L1", record_id) is True
    assert await memory.read({}, {"learner_id": "L1"}) == []
    assert memory.forget("L1", record_id) is False


async def test_memory_events_are_recorded_without_content():
    service = make_service(memory=make_memory())
    ctx = {"session_id": "s1", "trace_id": "t1", "learner_id": "L1"}
    await service.write_memory(
        [{"content": "学生的秘密原文", "scope": "long_term", "source": "s", "confidence": 0.5}], ctx
    )
    await service.read_memory({}, ctx)

    events = service.events.replay("s1")
    write_event = next(e for e in events if e.type == EventType.MEMORY_WRITE.value)
    assert write_event.payload == {"scope": "", "count": 1}
    assert "学生的秘密原文" not in repr(write_event.payload)

    read_event = next(e for e in events if e.type == EventType.MEMORY_READ.value)
    assert read_event.payload["count"] == 1


async def test_record_carries_audit_fields():
    memory = make_memory()
    await memory.write(
        [
            {
                "content": "x",
                "scope": "episodic",
                "source": "quiz",
                "confidence": 0.8,
                "tags": ["导数"],
                "concept_ids": ["c1"],
            }
        ],
        {"learner_id": "L1"},
    )
    record = (await memory.read({}, {"learner_id": "L1"}))[0]
    for field in ("record_id", "source", "confidence", "revocable", "expires_at", "created_at"):
        assert field in record
    assert record["tags"] == ["导数"]