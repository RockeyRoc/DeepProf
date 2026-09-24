from __future__ import annotations

import pytest

from runtime.core.events import EventType
from runtime.testing import make_service


@pytest.mark.parametrize(
    "finish_reason,expected_kind",
    [("length", "model_truncated"), ("stop", "empty_model_response")],
)
async def test_service_turns_empty_provider_content_into_structured_failure(
    finish_reason: str, expected_kind: str
):
    service = make_service(
        script=[{
            "content": "   ",
            "finish_reason": finish_reason,
            "usage": {"prompt_tokens": 31, "completion_tokens": 8, "total_tokens": 39},
        }]
    )

    frames = [frame async for frame in service.generate(
        {"role": "tutor.default", "messages": [{"role": "user", "content": "fixture"}]},
        {"session_id": "session-test", "trace_id": "trace-test"},
    )]

    assert frames[-1]["type"] == "error"
    assert frames[-1]["error"]["code"] == expected_kind
    assert frames[-1]["error"]["details"]["kind"] == expected_kind
    assert frames[-1]["error"]["details"]["usage"]["total_tokens"] == 39
    event_types = [event.type for event in service.events.replay("session-test")]
    assert event_types[0] == EventType.MODEL_REQUESTED.value
    assert event_types[-1] == EventType.MODEL_FAILED.value
    assert EventType.MODEL_COMPLETED.value not in event_types


async def test_service_completes_nonempty_provider_content():
    service = make_service(script=[{"content": "有依据的说明", "finish_reason": "stop"}])

    frames = [frame async for frame in service.generate(
        {"role": "tutor.default", "messages": [{"role": "user", "content": "fixture"}]},
        {"session_id": "session-test", "trace_id": "trace-test"},
    )]

    assert any(frame["type"] == "delta" for frame in frames)
    assert not any(frame["type"] == "error" for frame in frames)
    event_types = [event.type for event in service.events.replay("session-test")]
    assert event_types[0] == EventType.MODEL_REQUESTED.value
    assert event_types[-1] == EventType.MODEL_COMPLETED.value
    assert EventType.MODEL_STREAM_DELTA.value in event_types
