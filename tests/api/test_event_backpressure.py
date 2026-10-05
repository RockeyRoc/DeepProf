from __future__ import annotations

import asyncio

import pytest

from api.events import MAX_SSE_QUEUE, sse_frames
from runtime.core.events import RuntimeEvent
from runtime.testing import make_service


@pytest.mark.asyncio
async def test_slow_sse_client_is_closed_and_reconnect_replays_every_persisted_event():
    service = make_service()
    session = service.new_session(learner_id="local", title="slow sse")
    stream = sse_frames(service, session.session_id, heartbeat_seconds=0.001)
    assert await anext(stream) == ": ping\n\n"

    expected = MAX_SSE_QUEUE + 2
    for index in range(expected):
        await service.emit(RuntimeEvent(type="test.event", session_id=session.session_id,
            trace_id="sse-trace", payload={"index": index}))

    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    assert service.performance_metrics["sse_queue_peak"] == MAX_SSE_QUEUE
    assert service.performance_metrics["sse_overflows"] == 1

    resumed = sse_frames(service, session.session_id, from_sequence=0)
    replayed = [await anext(resumed) for _ in range(expected)]
    assert [int(frame.split("\n", 1)[0].removeprefix("id: ")) for frame in replayed] == list(
        range(1, expected + 1))
    await resumed.aclose()
    await stream.aclose()
