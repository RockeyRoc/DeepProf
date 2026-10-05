from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

from runtime.core.events import EventType
from runtime.testing import make_service
from runtime.providers.fake import FakeProvider
from runtime.providers.profiles import ProviderProfile
from runtime.providers.local import LocalProvider
from runtime.providers.openai_compatible import OpenAICompatibleProvider
from runtime.core.errors import ProviderError
from runtime.core.events import RuntimeEvent
from runtime.storage.sqlite_store import SqliteEventStore


def _sse(*chunks: dict) -> bytes:
    body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
    return (body + "data: [DONE]\n\n").encode()


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


@pytest.mark.asyncio
async def test_sqlite_event_writes_run_off_loop_and_remain_ordered(tmp_path, monkeypatch):
    service = make_service()
    store = SqliteEventStore.open(str(tmp_path / "events.sqlite"))
    service.events = store
    original_append = store.append

    def slow_append(event):
        time.sleep(0.005)
        return original_append(event)

    monkeypatch.setattr(store, "append", slow_append)
    ticks = 0
    async def heartbeat():
        nonlocal ticks
        for _ in range(10):
            ticks += 1
            await asyncio.sleep(0)

    await asyncio.gather(heartbeat(), *(service.emit(RuntimeEvent(type="test.event", session_id="sqlite-order",
        trace_id="trace", payload={"index": index})) for index in range(12)))
    events = service.history("sqlite-order")
    assert [item["sequence"] for item in events] == list(range(1, 13))
    assert ticks == 10
    assert service.performance_metrics["event_write_count"] == 12
    store.close()


async def test_streaming_fallback_retries_only_before_any_visible_output():
    service = make_service(script=[ProviderError("primary unavailable")])
    service.router.add(ProviderProfile(profile_id="backup", protocol="native", default_model="backup-model",
                                       capabilities={"stream": True}),
                       FakeProvider("backup", script=[{"content": "backup reply"}]))
    service.router.set_fallback("fake", ["backup"])

    frames = [frame async for frame in service.generate(
        {"role": "tutor.default", "messages": [{"role": "user", "content": "fixture"}]},
        {"session_id": "session-fallback", "trace_id": "trace-fallback"},
    )]

    assert "".join(frame["text"] for frame in frames if frame["type"] == "delta") == "backup reply"
    requests = [event.payload for event in service.events.replay("session-fallback")
                if event.type == EventType.MODEL_REQUESTED.value]
    assert [(row["provider_profile"], row.get("degraded_from")) for row in requests] == [
        ("fake", None), ("backup", "fake")]


async def test_selected_profile_uses_only_its_explicit_fallback_chain():
    service = make_service(script=[ProviderError("primary unavailable")])
    backup = FakeProvider("backup", script=[{"content": "backup reply"}])
    service.router.add(ProviderProfile(profile_id="backup", protocol="native", default_model="backup-model",
                                       capabilities={"stream": True}), backup)
    service.router.set_fallback("fake", ["backup"])

    frames = [frame async for frame in service.generate(
        {"role": "tutor.default", "messages": [{"role": "user", "content": "fixture"}]},
        {"session_id": "session-pinned-fallback", "trace_id": "trace-pinned-fallback",
         "provider_profile": "fake", "model": "fake-model"},
    )]

    assert "".join(frame["text"] for frame in frames if frame["type"] == "delta") == "backup reply"
    requests = [event.payload for event in service.events.replay("session-pinned-fallback")
                if event.type == EventType.MODEL_REQUESTED.value]
    assert requests[-1]["degraded_from"] == "fake"


async def test_experiment_context_disables_configured_provider_fallback():
    service = make_service(script=[ProviderError("primary unavailable")])
    backup = FakeProvider("backup", script=[{"content": "must not run"}])
    service.router.add(ProviderProfile(profile_id="backup", protocol="native", default_model="backup-model",
                                       capabilities={"stream": True}), backup)
    service.router.set_fallback("fake", ["backup"])

    frames = [frame async for frame in service.generate(
        {"role": "tutor.default", "messages": [{"role": "user", "content": "fixture"}]},
        {"session_id": "session-experiment", "trace_id": "trace-experiment", "experiment_run": True},
    )]

    assert frames[-1]["type"] == "error"
    assert backup._index == 0


@pytest.mark.parametrize(
    ("profile_id", "protocol", "vendor_id", "base_url"),
    [
        ("ollama", "local", "ollama", "http://127.0.0.1:11434/v1"),
        ("glm", "openai_compatible", "glm", "https://glm.example/v1"),
        ("deepseek", "openai_compatible", "deepseek", "https://deepseek.example/v1"),
    ],
)
async def test_undeclared_streaming_works_for_compatible_profiles_after_probe(
    profile_id: str, protocol: str, vendor_id: str, base_url: str
):
    """The real compatible adapters can attempt streaming when legacy profiles omit capabilities."""
    service = make_service()
    secret_ref = f"provider:{profile_id}"
    profile = ProviderProfile(
        profile_id=profile_id,
        protocol=protocol,
        vendor_id=vendor_id,
        base_url=base_url,
        api_key_ref="" if protocol == "local" else secret_ref,
        default_model="test-model",
        models=["test-model"],
        capabilities={},
        model_capabilities=({"test-model": {"reasoning_mode": "toggle",
            "thinking_parameter": "thinking.type"}} if vendor_id == "deepseek" else {}),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if not payload.get("stream"):
            return httpx.Response(200, json={"model": "test-model", "choices": [
                {"message": {"content": "pong"}, "finish_reason": "stop"},
            ]})
        assert payload["stream"] is True
        if protocol == "local":
            assert "Authorization" not in request.headers
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=_sse(
            {"choices": [{"delta": {"reasoning_content": "推理"}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": "兼容回复"}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ))

    transport = httpx.MockTransport(handler)
    if protocol == "local":
        provider = LocalProvider(profile, transport=transport)
    else:
        service.router.set_secret(secret_ref, "sk-test")
        provider = OpenAICompatibleProvider(profile, service.router.secret_store, transport=transport)
    service.router.add(profile, provider)
    service.router.set_role("tutor.default", profile_id, "test-model")

    # A successful non-streaming probe must leave stream capability undeclared.
    probe = await service.router.probe(profile_id, "test-model")
    assert probe["status"] == "ok"
    assert "stream" not in service.router.profile(profile_id).capabilities

    thinking_enabled = vendor_id == "deepseek"
    frames = [frame async for frame in service.generate(
        {"role": "tutor.default", "messages": [{"role": "user", "content": "测试"}]},
        {"session_id": f"session-{profile_id}", "trace_id": f"trace-{profile_id}",
         "thinking_enabled": thinking_enabled},
    )]
    assert "".join(frame["text"] for frame in frames if frame["type"] == "delta") == "兼容回复"
    reasoning = "".join(frame["text"] for frame in frames if frame["type"] == "reasoning_delta")
    assert reasoning == ("推理" if thinking_enabled else "")
    assert not any(frame["type"] == "error" for frame in frames)


async def test_explicit_stream_false_still_blocks_and_unknown_experiment_is_strict():
    service = make_service()
    requests: list[bool] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(True)
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=_sse({"choices": [{"delta": {"content": "no"}}]}))

    profile = ProviderProfile(profile_id="compatible", protocol="openai_compatible",
        base_url="https://example.invalid/v1", api_key_ref="provider:compatible", default_model="m",
        capabilities={"stream": False})
    service.router.set_secret("provider:compatible", "sk-test")
    service.router.add(profile, OpenAICompatibleProvider(profile, service.router.secret_store,
        transport=httpx.MockTransport(handler)))
    service.router.set_role("tutor.default", "compatible", "m")

    blocked = [frame async for frame in service.generate({"role": "tutor.default", "messages": []},
        {"session_id": "explicit-false", "trace_id": "trace-false"})]
    assert blocked[-1]["error"]["code"] == "provider_capability_missing"
    assert requests == []

    profile.capabilities.clear()
    experiment = [frame async for frame in service.generate({"role": "tutor.default", "messages": []},
        {"session_id": "unknown-experiment", "trace_id": "trace-experiment", "experiment_run": True})]
    assert experiment[-1]["error"]["code"] == "provider_capability_missing"
    assert requests == []
