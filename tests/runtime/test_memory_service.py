"""MemoryService 测试（§5.5：来源、置信度、过期、可撤回）。"""
from __future__ import annotations

import pytest

from runtime.core.errors import MemoryValidationError
from runtime.core.events import EventBus, EventType
from runtime.memory.base import InMemoryMemoryStore, MemoryQuery, MemoryRecord, MemoryType
from runtime.memory.service import MemoryService, build_working_memory
from runtime.storage.base import InMemoryEventStore


@pytest.fixture
def bus():
    return EventBus(store=InMemoryEventStore())


@pytest.fixture
def service(bus):
    return MemoryService(InMemoryMemoryStore(), bus)


async def test_write_rejects_record_without_source(service):
    """无来源的学情结论不得进入长期记忆。"""
    with pytest.raises(MemoryValidationError) as exc:
        await service.write(
            [
                {
                    "learner_id": "learner_a",
                    "memory_type": MemoryType.LONG_TERM.value,
                    "content": "该生不会递归",
                    "confidence": 0.9,
                }
            ],
            {"session_id": "s1"},
        )
    assert exc.value.code == "memory_invalid_record"


async def test_write_rejects_invalid_confidence(service):
    """置信度必须落在 [0,1]，避免出现无法解释的"确定度"。"""
    with pytest.raises(MemoryValidationError):
        await service.write(
            [
                {
                    "learner_id": "learner_a",
                    "memory_type": MemoryType.LONG_TERM.value,
                    "content": "内容",
                    "source": "session:s1",
                    "confidence": 1.8,
                }
            ],
            {},
        )


async def test_write_fills_source_from_context(service, bus):
    """来源缺省时可从上下文补齐，并写入 memory.write 审计事件。"""
    record = build_working_memory("learner_a", "本周目标：掌握递归", source="")
    ids = await service.write([record.to_dict()], {"session_id": "s1", "trace_id": "t1"})

    assert len(ids) == 1
    writes = [e for e in bus.replay("s1") if e.type == EventType.MEMORY_WRITE.value]
    assert writes and writes[0].payload["record_id"] == ids[0]
    assert writes[0].payload["source"] == "session:s1"


async def test_read_emits_event_without_content(service, bus):
    """读事件只记录结构与数量，不把记忆正文写进轨迹（§13.2）。"""
    await service.write(
        [
            {
                "learner_id": "learner_a",
                "memory_type": MemoryType.EPISODIC.value,
                "content": "第三次提示后独立完成证明",
                "source": "session:s1",
            }
        ],
        {"session_id": "s1"},
    )
    results = await service.read({"learner_id": "learner_a"}, {"session_id": "s1", "trace_id": "t1"})

    assert results[0]["content"] == "第三次提示后独立完成证明"
    reads = [e for e in bus.replay("s1") if e.type == EventType.MEMORY_READ.value]
    assert "content" not in reads[0].payload
    assert reads[0].payload["count"] == 1


async def test_forget_is_revocable(service):
    """长期记忆可撤回（可查看、可纠正、可删除、可关闭）。"""
    ids = await service.write(
        [
            {
                "learner_id": "learner_a",
                "memory_type": MemoryType.AFFECTIVE.value,
                "content": "偏好用类比讲解",
                "source": "session:s1",
                "revocable": True,
            }
        ],
        {},
    )
    assert await service.forget(ids, {}) == 1
    assert await service.read({"learner_id": "learner_a"}, {}) == []


def test_inmemory_read_accepts_enum_memory_type():
    """InMemory 实现同样接受枚举条件（枚举不会被 str() 变成 "MemoryType.X"）。"""
    store = InMemoryMemoryStore()
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


def test_purge_expired_removes_rows():
    """过期数据物理清理，符合最小必要原则。"""
    store = InMemoryMemoryStore()
    service = MemoryService(store)
    store.write(
        [
            MemoryRecord(
                learner_id="learner_a",
                memory_type=MemoryType.WORKING,
                content="临时上下文",
                source="session:s1",
                expires_at="2000-01-01T00:00:00+00:00",
            )
        ]
    )
    assert service.purge_expired() == 1