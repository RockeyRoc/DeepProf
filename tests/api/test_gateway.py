"""Gateway API 测试：健康检查、命令、SSE 重连、Provider Settings。"""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from runtime.providers.profiles import ProviderProfile
from runtime.testing import make_service

BINDINGS = {
    "hint": {"capability": "render_template", "params": {"template": "第 ${level} 级提示"}},
    "teach": {"capability": "render_template", "params": {"template": "讲解"}},
}


@pytest.fixture
def client():
    service = make_service(bindings=BINDINGS, script=[{"content": "回答"}])
    app = create_app(service=service)
    with TestClient(app) as test_client:
        test_client.app_state_service = service
        yield test_client


def test_health_reports_assembly_state(client):
    payload = client.get("/health").json()
    assert payload["status"] == "ok"
    assert payload["contract_version"] == "1.4.0"
    assert payload["action_bindings"] == {"hint": "render_template", "teach": "render_template"}
    assert payload["providers"][0]["profile_id"] == "fake"


def test_health_exposes_unbound_actions_are_missing(client):
    """装配缺口必须可见，而不是静默兜底。"""
    payload = client.get("/health").json()
    assert "correct" not in payload["action_bindings"]


def test_new_session_command(client):
    response = client.post(
        "/commands",
        json={
            "command_id": "c1",
            "client_id": "cli",
            "surface": "cli",
            "learner_id": "L1",
            "type": "session.new",
            "payload": {"title": "线性代数"},
        },
    )
    assert response.status_code == 200
    session_id = response.json()["session_id"]
    assert session_id

    summary = client.get(f"/sessions/{session_id}").json()
    assert summary["title"] == "线性代数"
    assert summary["learner_id"] == "L1"


def test_unknown_command_type_is_rejected(client):
    response = client.post(
        "/commands",
        json={"command_id": "c1", "type": "teleport", "payload": {}},
    )
    assert response.status_code == 422


def test_command_requires_session_id(client):
    response = client.post(
        "/commands", json={"command_id": "c1", "type": "message.send", "payload": {"content": "hi"}}
    )
    assert response.status_code == 400


def test_message_turn_writes_session_and_events(client):
    session_id = client.post(
        "/commands", json={"command_id": "c1", "type": "session.new", "payload": {}}
    ).json()["session_id"]

    response = client.post(
        "/commands",
        json={
            "command_id": "c2",
            "type": "message.send",
            "session_id": session_id,
            "payload": {"content": "什么是极限"},
        },
    )
    assert response.status_code == 200

    # 事件经 Runtime 事件存储落库（SSE 端点无限流，测试里直接读存储）
    events = client.app_state_service.history(session_id)
    assert any(e["type"] == "model.stream.delta" for e in events)
    assert [e["sequence"] for e in events] == sorted(e["sequence"] for e in events)


def test_execute_returns_no_binding_for_unmapped_action(client):
    """未注入绑定的动作必须显式返回 no_binding（装配缺口要响亮）。"""
    import asyncio

    service = client.app_state_service
    result = asyncio.run(service.execute({"action": "correct"}, {}))
    assert result["status"] == "no_binding"
    assert result["content"] == ""


async def test_events_replay_from_sequence():
    """按 from_sequence 补发：只返回 sequence 更大的事件。"""
    service = make_service()
    session = service.new_session()
    for index in range(5):
        await service.emit(
            {
                "type": "model.stream.delta",
                "payload": {"text": str(index)},
                "session_id": session.session_id,
                "trace_id": "t1",
            }
        )

    all_events = service.history(session.session_id)
    tail = service.history(session.session_id, from_sequence=2)
    assert [e["sequence"] for e in tail] == [e["sequence"] for e in all_events if e["sequence"] > 2]
    assert service.last_sequence(session.session_id) == 5


async def test_sse_replays_history_then_streams_live():
    """SSE 帧：先补发历史，再推送实时事件；已消费的 sequence 不重复下发。"""
    from api.events import encode_sse, sse_frames

    service = make_service(script=[{"content": "回答"}])
    session = service.new_session()
    await service.emit(
        {"type": "session.started", "payload": {}, "session_id": session.session_id, "trace_id": "t1"}
    )

    frames = sse_frames(service, session.session_id, heartbeat_seconds=0.05)
    first = await anext(frames)
    assert first.startswith("event: session.started\n")
    assert json.loads(first.split("data: ", 1)[1])["sequence"] == 1

    # 新事件应实时到达
    await service.emit(
        {"type": "pedagogy.decision", "payload": {"action": "x"}, "session_id": session.session_id, "trace_id": "t1"}
    )
    second = await anext(frames)
    assert second.startswith("event: pedagogy.decision\n")
    assert json.loads(second.split("data: ", 1)[1])["sequence"] == 2

    await frames.aclose()


async def test_sse_reconnect_does_not_replay_consumed_events():
    """重连时按 from_sequence 补发：已看过的 sequence 不再出现。"""
    from api.events import sse_frames

    service = make_service()
    session = service.new_session()
    for index in range(3):
        await service.emit(
            {
                "type": "model.stream.delta",
                "payload": {"text": str(index)},
                "session_id": session.session_id,
                "trace_id": "t1",
            }
        )

    frames = sse_frames(service, session.session_id, from_sequence=2, heartbeat_seconds=0.05)
    replayed = await anext(frames)
    assert json.loads(replayed.split("data: ", 1)[1])["sequence"] == 3
    await frames.aclose()


async def test_sse_heartbeat_when_idle():
    from api.events import HEARTBEAT_FRAME, sse_frames

    service = make_service()
    session = service.new_session()
    frames = sse_frames(service, session.session_id, heartbeat_seconds=0.01)
    assert await anext(frames) == HEARTBEAT_FRAME
    await frames.aclose()


async def test_sse_unsubscribes_on_close():
    from api.events import sse_frames

    service = make_service()
    session = service.new_session()
    frames = sse_frames(service, session.session_id, heartbeat_seconds=0.01)
    await anext(frames)
    assert len(service._listeners) == 1  # noqa: SLF001
    await frames.aclose()
    assert service._listeners == []  # noqa: SLF001


def test_encode_sse_shape():
    from api.events import encode_sse

    frame = encode_sse({"type": "model.stream.delta", "payload": {"text": "你"}, "sequence": 1})
    assert frame.startswith("event: model.stream.delta\n")
    assert frame.endswith("\n\n")


def test_events_route_is_registered(client):
    spec = client.get("/openapi.json").json()
    assert "/sessions/{session_id}/events" in spec["paths"]


def test_events_for_unknown_session_is_404(client):
    response = client.get("/sessions/ghost/events")
    assert response.status_code == 404
    assert response.json()["error"]["details"]["kind"] == "session_not_found"


# ---- Provider Settings ----

def test_provider_upsert_hides_key(client):
    response = client.put(
        "/providers/mine",
        json={
            "profile_id": "mine",
            "display_name": "My Gateway",
            "base_url": "https://gateway.example.edu/v1",
            "api_key": "sk-super-secret",
            "default_model": "model-x",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert "api_key" not in payload
    assert "sk-super-secret" not in json.dumps(payload)
    assert payload["has_secret"] is True
    assert payload["api_key_ref"] == "provider:mine"


def test_provider_list_never_echoes_key(client):
    client.put(
        "/providers/mine",
        json={"profile_id": "mine", "base_url": "https://x.invalid/v1", "api_key": "sk-secret"},
    )
    listed = client.get("/providers")
    assert listed.status_code == 200
    assert "sk-secret" not in listed.text


def test_provider_probe_reports_structured_status(client):
    response = client.post("/providers/fake/probe", json={})
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"ok", "inconclusive", "failed"}
    assert "secret_env_var" in payload


def test_provider_probe_unknown_profile_is_404(client):
    assert client.post("/providers/ghost/probe", json={}).status_code == 404


def test_provider_profile_id_mismatch_is_rejected(client):
    response = client.put("/providers/a", json={"profile_id": "b", "base_url": "https://x.invalid"})
    assert response.status_code == 400


def test_gateway_does_not_expose_keys_in_openapi():
    """OpenAPI schema 里不能出现明文密钥字段名。"""
    service = make_service()
    app = create_app(service=service)
    with TestClient(app) as client:
        spec = client.get("/openapi.json").json()
    schemas = spec["components"]["schemas"]
    assert "api_key" not in schemas.get("ProviderProfileOut", {}).get("properties", {})
    assert "api_key" in schemas.get("ProviderProfileIn", {}).get("properties", {})