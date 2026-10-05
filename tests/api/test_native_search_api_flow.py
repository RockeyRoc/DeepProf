from __future__ import annotations

import json

import httpx
from fastapi.testclient import TestClient

from api.app import create_app
from api.sessions import _prepare_web_search, _run_chat_turn, _run_turn
from runtime.core.events import new_id
from runtime.core.session import Message
from runtime.providers.openai_compatible import OpenAICompatibleProvider
from runtime.providers.profiles import ProviderProfile
from runtime.testing import make_service


async def test_chat_uses_selected_qwen_native_search_api_and_persists_provider_sources():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        stream = "".join([
            'data: {"choices":[{"delta":{"content":"Based on fresh sources."},"finish_reason":null}]}\n\n',
            'data: {"choices":[],"search_info":{"search_results":[{"title":"Official source","url":"https://example.com/ref","content":"Live summary"}]}}\n\n',
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
            'data: [DONE]\n\n',
        ])
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=stream.encode())

    service = make_service()
    profile = ProviderProfile(
        profile_id="qwen-test", display_name="Qwen test", base_url="https://provider.invalid/v1",
        api_key_ref="provider:qwen-test", default_model="qwen-plus", models=["qwen-plus"],
        vendor_id="qwen", capabilities={},
    )
    service.router.secret_store.set(profile.api_key_ref, "sk-test")
    provider = OpenAICompatibleProvider(profile, service.router.secret_store, transport=httpx.MockTransport(handler))
    service.router.add(profile, provider)
    service.router.set_role("tutor.default", profile.profile_id, profile.default_model)

    with TestClient(create_app(service=service)) as client:
        status = client.get("/settings/web-search?profile_id=qwen-test&model=qwen-plus").json()
        assert status["status"] == "ready"
        assert status["supported"] is True

    session = service.new_session(learner_id="local", title="native search")
    session.metadata.update({"session_mode": "chat", "provider_profile": profile.profile_id,
                             "model": profile.default_model})
    text = "Search for current information"
    session.append(Message(role="user", content=text, metadata={"turn_mode": "chat"}))
    ctx = {"session_id": session.session_id, "trace_id": new_id("trc"),
           "provider_profile": profile.profile_id, "model": profile.default_model,
           "thinking_enabled": False, "web_search_mode": "auto"}
    ctx = await _prepare_web_search(service, session, text, ctx)
    answer, reasoning = await _run_chat_turn(service, session, ctx)

    assert answer == "Based on fresh sources."
    assert reasoning == ""
    assert seen["enable_search"] is True
    assert "forced_search" not in seen.get("search_options", {})
    search = ctx["web_search"]
    assert search["status"] == "completed"
    assert search["search_confirmed"] is True
    assert search["results"][0]["url"] == "https://example.com/ref"
    assert any(event["type"] == "web.search.completed" for event in service.history(session.session_id))


async def test_bailian_glm_responses_native_search_streams_official_sources():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        events = [
            {"type": "response.reasoning_summary_text.delta", "delta": "先核验公开来源。"},
            {"type": "response.output_text.delta", "delta": "GLM-5.2 的联网回答。"},
            {"type": "response.output_item.done", "item": {"type": "web_search_call", "action": {
                "sources": [{"title": "百炼搜索文档", "url": "https://help.aliyun.com/zh/model-studio/web-search"}],
            }}},
            {"type": "response.completed", "response": {"status": "completed", "usage": {"output_tokens": 20}}},
            "[DONE]",
        ]
        body = "".join(("data: " + (item if isinstance(item, str) else json.dumps(item)) + "\n\n") for item in events)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())

    service = make_service()
    profile = ProviderProfile(
        profile_id="bailian-glm", display_name="Bailian GLM", base_url="https://provider.invalid/v1",
        api_key_ref="provider:bailian-glm", default_model="glm-5.2", models=["glm-5.2"],
        vendor_id="qwen", capabilities={},
    )
    service.router.secret_store.set(profile.api_key_ref, "sk-test")
    provider = OpenAICompatibleProvider(profile, service.router.secret_store, transport=httpx.MockTransport(handler))
    service.router.add(profile, provider)

    session = service.new_session(learner_id="local", title="bailian glm responses search")
    session.metadata.update({"session_mode": "chat", "provider_profile": profile.profile_id,
                             "model": profile.default_model, "thinking_enabled": True,
                             "thinking_level": "max", "web_search_mode": "auto"})
    session.append(Message(role="user", content="GLM 最新信息", metadata={"turn_mode": "chat"}))
    ctx = {"session_id": session.session_id, "trace_id": new_id("trc"),
           "provider_profile": profile.profile_id, "model": profile.default_model,
           "thinking_enabled": True, "thinking_level": "max", "web_search_mode": "auto"}

    ctx = await _prepare_web_search(service, session, session.messages[-1].content, ctx)
    answer, reasoning = await _run_chat_turn(service, session, ctx)

    assert answer == "GLM-5.2 的联网回答。"
    assert reasoning == "先核验公开来源。"
    assert seen["path"] == "/v1/responses"
    assert seen["body"]["tools"] == [{"type": "web_search"}]
    assert seen["body"]["reasoning"] == {"effort": "max"}
    assert ctx["web_search"]["status"] == "completed"
    assert ctx["web_search"]["results"][0]["url"].endswith("web-search")


async def test_forced_native_search_failure_is_preserved_in_history_and_events():
    service = make_service()
    profile = ProviderProfile(
        profile_id="qwen-failing", display_name="Qwen test", base_url="https://provider.invalid/v1",
        api_key_ref="provider:qwen-failing", default_model="qwen-plus", models=["qwen-plus"],
        vendor_id="qwen", capabilities={},
    )

    class FailingProvider:
        profile_id = profile.profile_id
        protocol = "openai_compatible"

        def capabilities(self):
            return {"stream": True}

        async def stream(self, request, ctx):
            assert request["native_web_search"]["body"]["search_options"]["forced_search"] is True
            yield {"type": "error", "error": {
                "code": "provider_authentication_failed", "message": "invalid API key",
                "details": {"kind": "provider_authentication_failed"},
            }}

    service.router.secret_store.set(profile.api_key_ref, "sk-test")
    service.router.add(profile, FailingProvider())

    session = service.new_session(learner_id="local", title="forced native search failure")
    session.metadata.update({"session_mode": "chat", "provider_profile": profile.profile_id,
                             "model": profile.default_model})
    app_state = type("TurnState", (), {"active_turns": set(), "turn_tasks": {}})()
    await _run_turn(service, session, "Find current information", {
        "session_id": session.session_id, "trace_id": new_id("trc"), "thinking_enabled": False,
        "thinking_level": "", "thinking_budget": None, "web_search_mode": "always",
        "generation_config": {},
    }, app_state)

    saved = service.get_session(session.session_id)
    assert saved.messages[0].metadata["web_search"]["status"] == "failed"
    assert saved.messages[0].metadata["web_search"]["error"]["code"] == "provider_authentication_failed"
    failed = [event for event in service.history(session.session_id) if event["type"] == "web.search.failed"]
    assert failed and failed[-1]["payload"]["error"]["code"] == "provider_authentication_failed"


def test_ollama_search_credential_can_be_saved_probed_and_cleared(monkeypatch):
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    service = make_service()
    profile = ProviderProfile(
        profile_id="ollama-local", display_name="Ollama local", protocol="local",
        base_url="http://127.0.0.1:11434/v1", default_model="llama3.2", models=["llama3.2"],
        vendor_id="ollama", capabilities={},
    )
    service.router.add(profile)

    async def fake_search(query: str, api_key: str):
        assert query == "latest news today"
        assert api_key == "ollama-test-key"
        return [{"title": "Live result", "url": "https://example.com/live", "snippet": "Live summary"}]

    monkeypatch.setattr("api.web_search.ollama_web_search", fake_search)
    with TestClient(create_app(service=service)) as client:
        url = "/settings/web-search?profile_id=ollama-local&model=llama3.2"
        initial = client.get(url).json()
        assert initial["supported"] is True
        assert initial["requires_api_key"] is True
        assert initial["status"] == "provider_credential_missing"

        saved = client.put(url, json={"api_key": "ollama-test-key"}).json()
        assert saved["status"] == "ready"
        assert "ollama-test-key" not in json.dumps(saved)

        probe = client.post("/settings/web-search/probe", json={"profile_id": "ollama-local", "model": "llama3.2"})
        assert probe.status_code == 200
        assert probe.json()["search_confirmed"] is True
        assert probe.json()["sources"][0]["url"] == "https://example.com/live"

        cleared = client.put(url, json={"clear_key": True}).json()
        assert cleared["status"] == "provider_credential_missing"


async def test_ollama_auto_mode_uses_model_decision_and_ollama_sources(monkeypatch):
    service = make_service()
    profile = ProviderProfile(
        profile_id="ollama-chat", display_name="Ollama local", protocol="local",
        base_url="http://127.0.0.1:11434/v1", default_model="llama3.2", models=["llama3.2"],
        vendor_id="ollama", capabilities={},
    )
    seen: dict = {}

    class OllamaProvider:
        profile_id = profile.profile_id
        protocol = "local"

        def capabilities(self):
            return {"stream": True}

        async def generate(self, request, ctx):
            return {"content": "SEARCH\nQUERY: current Ollama release"}

        async def stream(self, request, ctx):
            seen["messages"] = request["messages"]
            yield {"type": "delta", "text": "Here is the latest release."}
            yield {"type": "finish", "finish_reason": "stop"}

    async def fake_search(query: str, api_key: str):
        seen["query"] = query
        seen["api_key"] = api_key
        return [{"title": "Official result", "url": "https://ollama.com/blog", "snippet": "New release"}]

    monkeypatch.setattr("api.sessions.ollama_web_search", fake_search)
    service.router.secret_store.set("web-search:ollama", "ollama-test-key")
    service.router.add(profile, OllamaProvider())
    session = service.new_session(learner_id="local", title="ollama native search")
    session.metadata.update({"session_mode": "chat", "provider_profile": profile.profile_id,
                             "model": profile.default_model})
    session.append(Message(role="user", content="What is the latest Ollama release?", metadata={"turn_mode": "chat"}))
    ctx = {"session_id": session.session_id, "trace_id": new_id("trc"),
           "provider_profile": profile.profile_id, "model": profile.default_model,
           "thinking_enabled": False, "web_search_mode": "auto"}

    ctx = await _prepare_web_search(service, session, session.messages[-1].content, ctx)
    answer, reasoning = await _run_chat_turn(service, session, ctx)

    assert answer == "Here is the latest release."
    assert reasoning == ""
    assert seen["query"] == "current Ollama release"
    assert seen["api_key"] == "ollama-test-key"
    assert any("Official result" in str(message) for message in seen["messages"])
    assert ctx["web_search"]["status"] == "completed"
    assert ctx["web_search"]["results"][0]["url"] == "https://ollama.com/blog"
