from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from runtime.core.events import RuntimeEvent
from runtime.core.message import Message
from runtime.core.session import Session
from runtime.providers.profiles import ProviderProfile
from runtime.testing import make_service


def _command(client: TestClient, command_id: str, command_type: str, *, session_id: str | None = None,
             payload: dict | None = None):
    return client.post("/commands", json={
        "command_id": command_id, "client_id": "test", "surface": "web", "learner_id": "local",
        "session_id": session_id, "type": command_type, "payload": payload or {},
    })


def test_provider_catalog_search_supports_chinese_and_english_aliases():
    service = make_service()
    with TestClient(create_app(service=service)) as client:
        assert [row["id"] for row in client.get("/providers/catalog?query=深度求索").json()] == ["deepseek"]
        assert [row["id"] for row in client.get("/providers/catalog?query=ollama").json()] == ["ollama"]
        assert {row["id"] for row in client.get("/providers/catalog?query=GLM").json()} == {"glm"}


def test_default_selection_uses_patch_without_shadowing_legacy_default_profile_id():
    service = make_service()
    profile = ProviderProfile(profile_id="default", base_url="https://example.invalid/v1", default_model="legacy-model")
    service.router.add(profile)
    with TestClient(create_app(service=service)) as client:
        response = client.put("/providers/default", json=profile.to_dict())
        assert response.status_code == 200
        assert response.json()["profile_id"] == "default"
        selected = client.patch("/providers/default", json={"profile_id": "default", "model": "legacy-model"})
        assert selected.status_code == 200
        assert client.get("/providers/default").json()["profile_id"] == "default"


def test_adding_a_model_to_another_profile_does_not_change_global_default():
    service = make_service()
    service.router.add(ProviderProfile(
        profile_id="alternate", base_url="https://example.invalid/v1", default_model="alt-a",
        models=["alt-a"], vendor_id="custom",
    ))
    with TestClient(create_app(service=service)) as client:
        response = client.put("/providers/alternate/models", json={
            "models": ["alt-a", "alt-b"], "default_model": "alt-b",
        })
        assert response.status_code == 200
        assert client.get("/providers/default").json()["profile_id"] == "fake"


def test_discovery_uses_temporary_key_and_classifies_auth_and_unsupported(monkeypatch):
    import api.providers as provider_api
    from runtime.core.errors import ProviderError

    service = make_service()
    with TestClient(create_app(service=service)) as client:
        async def unauthorized(_self):
            raise ProviderError("unauthorized", kind="auth_failed", status_code=401)

        monkeypatch.setattr(provider_api.OpenAICompatibleProvider, "list_models", unauthorized)
        payload = {"vendor_id": "deepseek", "base_url": "https://api.deepseek.com", "api_key": "temporary"}
        result = client.post("/providers/discover", json=payload).json()
        assert result["status"] == "authentication_failed"
        assert not any(profile.profile_id == "discovery" for profile in service.router.profiles())

        async def unsupported(_self):
            raise ProviderError("no model listing", kind="model_list_unsupported", status_code=404)

        monkeypatch.setattr(provider_api.OpenAICompatibleProvider, "list_models", unsupported)
        result = client.post("/providers/discover", json=payload).json()
        assert result["status"] == "unsupported"
        assert "手动填写" in result["message"]
        assert not any(profile.profile_id == "discovery" for profile in service.router.profiles())


def test_model_removal_requires_explicit_default_replacement_and_stays_unselected(monkeypatch, isolated_home):
    service = make_service()
    profile = ProviderProfile(
        profile_id="ds-test", display_name="DeepSeek Test", base_url="https://example.invalid/v1",
        api_key_ref="legacy:deepseek", default_model="model-a", models=["model-a", "model-b"],
        vendor_id="deepseek", model_capabilities={"model-a": {"reasoning_mode": "toggle"}},
    )
    service.router.secret_store.set(profile.api_key_ref, "secret-value")
    service.router.add(profile)
    app = create_app(service=service)
    with TestClient(app) as client:
        # Adding/editing a profile keeps the old key reference if the caller omits it.
        payload = profile.to_dict()
        payload.pop("api_key_ref")
        payload["api_key"] = None
        response = client.put("/providers/ds-test", json=payload)
        assert response.status_code == 200
        assert response.json()["api_key_ref"] == "legacy:deepseek"
        assert service.router.has_secret("ds-test")
        saved = json.loads((isolated_home / "providers.json").read_text(encoding="utf-8"))
        assert "secret-value" not in json.dumps(saved)

        assert client.patch("/providers/default", json={"profile_id": "ds-test", "model": "model-a"}).status_code == 200
        response = client.request("DELETE", "/providers/ds-test/models/model-a", json={})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "replacement_model_required"

        response = client.request("DELETE", "/providers/ds-test/models/model-a", json={"replacement_model": "model-b"})
        assert response.status_code == 200
        assert client.get("/providers/default").json()["model"] == "model-b"
        async def remote_models():
            return ["model-a", "model-b", "model-c"]
        monkeypatch.setattr(service.router.get("ds-test"), "list_models", remote_models)
        catalog = client.get("/providers/ds-test/model-catalog").json()
        assert next(row for row in catalog if row["id"] == "model-a")["selected"] is False
        # A fresh remote discovery does not make the removed model selected again.
        profiles = client.get("/providers").json()
        assert next(row for row in profiles if row["profile_id"] == "ds-test")["models"] == ["model-b"]

        response = client.request("DELETE", "/providers/ds-test", json={})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "replacement_provider_required"
        assert service.router.has_secret("ds-test")

        response = client.request("DELETE", "/providers/ds-test", json={"replacement_profile_id": "fake", "replacement_model": "fake-model"})
        assert response.status_code == 200
        assert not service.router.has_secret("ds-test")
        assert client.get("/providers/default").json()["profile_id"] == "fake"


def test_provider_deletion_is_rejected_while_its_session_is_generating():
    service = make_service()
    service.router.secret_store.set("test-provider-key", "secret-value")
    service.router.add(ProviderProfile(
        profile_id="test-provider", base_url="https://example.invalid/v1", api_key_ref="test-provider-key",
        default_model="model-a", models=["model-a"], vendor_id="deepseek",
    ))
    session = Session(title="正在生成", metadata={"session_mode": "chat", "provider_profile": "test-provider"})
    service.save_session(session)
    app = create_app(service=service)
    app.state.active_turns.add(session.session_id)

    with TestClient(app) as client:
        response = client.request("DELETE", "/providers/test-provider", json={})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "provider_busy"
    assert service.router.profile("test-provider").profile_id == "test-provider"
    assert service.router.has_secret("test-provider")


def test_session_rename_delete_only_affect_chat_and_detach_branches():
    service = make_service()
    app = create_app(service=service)
    root = Session(title="原对话", metadata={"session_mode": "chat"})
    root.append(Message(role="assistant", content="回答", metadata={"reasoning_content": "private reasoning"}))
    child = root.fork(title="分支")
    child.session_id = "child-session"
    service.save_session(root)
    service.save_session(child)
    service.events.append(RuntimeEvent(type="test.event", payload={"text": "event"}, session_id=root.session_id))

    study = Session(title="学习会话", metadata={"session_mode": "study"})
    service.save_session(study)
    with TestClient(app) as client:
        response = _command(client, "rename", "session.rename", session_id=root.session_id, payload={"title": "  新名称  "})
        assert response.status_code == 200
        assert client.get(f"/sessions/{root.session_id}").json()["title"] == "新名称"

        response = _command(client, "study-rename", "session.rename", session_id=study.session_id, payload={"title": "不允许"})
        assert response.status_code == 409

        response = _command(client, "delete", "session.delete", session_id=root.session_id)
        assert response.status_code == 200
        assert client.get("/sessions").json()
        assert service.sessions.load(root.session_id) is None
        assert service.sessions.load(child.session_id).parent_id is None
        assert service.events.replay(root.session_id) == []
        assert client.get(f"/sessions/{child.session_id}").json()["parent_id"] is None


def test_chat_thinking_stream_is_separate_and_persisted():
    service = make_service(script=[{"content": "正文", "reasoning_content": "推理"}])
    profile = service.router.profile("fake")
    profile.model_capabilities = {"fake-model": {"reasoning_mode": "toggle"}}
    with TestClient(create_app(service=service)) as client:
        created = _command(client, "new-chat", "session.new", payload={
            "session_mode": "chat", "provider_profile": "fake", "model": "fake-model", "thinking_enabled": True,
        })
        assert created.status_code == 200
        session_id = created.json()["session_id"]
        response = _command(client, "send-chat", "message.send", session_id=session_id, payload={"content": "问题"})
        assert response.status_code == 200
        deadline = time.monotonic() + 3
        while not any(event["type"] == "agent.turn.completed" for event in service.history(session_id)) and time.monotonic() < deadline:
            time.sleep(0.01)
        events = service.history(session_id)
        assert any(event["type"] == "model.stream.reasoning.delta" for event in events)
        assistant = next(message for message in service.get_session(session_id).messages if message.role == "assistant")
        assert assistant.content == "正文"
        assert assistant.metadata["reasoning_content"] == "推理"
        assert assistant.metadata["thinking_enabled"] is True


def test_chat_model_selection_rejects_unknown_model_without_creating_session():
    service = make_service()
    profile = service.router.profile("fake")
    profile.models = ["fake-model"]
    profile.model_selection_mode = "catalog"
    with TestClient(create_app(service=service)) as client:
        before = service.sessions.list()
        response = _command(client, "bad-new", "session.new", payload={
            "session_mode": "chat", "provider_profile": "fake", "model": "not-added",
        })
        assert response.status_code == 409
        assert service.sessions.list() == before
