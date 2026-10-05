from __future__ import annotations

import asyncio
import time

from fastapi.testclient import TestClient
import pytest

from api.app import create_app
from api.sessions import _run_deep_research
from runtime.core.events import new_id
from runtime.core.session import Session
from runtime.providers.fake import FakeProvider
from runtime.testing import make_service
from runtime.web_search import TAVILY_SEARCH_SECRET_REF, WebSearchError


def _deep_ctx(session: Session) -> dict:
    return {"session_id": session.session_id, "trace_id": new_id("trc"), "provider_profile": "fake",
            "model": "fake-model", "thinking_enabled": None, "thinking_mode": "default",
            "thinking_level": "", "thinking_budget": None, "client_id": "test", "surface": "web"}


def test_deep_research_produces_downloadable_report_with_deduplicated_verified_sources(monkeypatch):
    provider = FakeProvider("fake", script=[
        {"content": '{"queries":["official source about test topic"]}'},
        {"content": '{"queries":["official source about test topic"]}'},
        {"content": "## 摘要\n基于来源的结论 [S1]，另一个不实编号 [S99]。"},
    ])
    service = make_service(provider=provider)
    service.router.secret_store.set(TAVILY_SEARCH_SECRET_REF, "tvly-test-key")
    search_calls: list[str] = []
    extract_calls: list[list[str]] = []

    async def fake_search(query: str, api_key: str, *, client=None):
        assert api_key == "tvly-test-key"
        search_calls.append(query)
        return [
            {"title": "Primary source", "url": "https://Example.com/research/?utm_source=test#top",
             "snippet": "An official summary."},
            {"title": "Duplicate source", "url": "https://example.com/research/",
             "snippet": "A duplicate summary."},
        ]

    async def fake_extract(urls: list[str], api_key: str, *, client=None):
        assert api_key == "tvly-test-key"
        extract_calls.append(urls)
        return [{"url": urls[0], "content": "Full article text."}]

    monkeypatch.setattr("api.sessions.tavily_search", fake_search)
    monkeypatch.setattr("api.sessions.tavily_extract", fake_extract)

    with TestClient(create_app(service=service)) as client:
        created = client.post("/commands", json={"command_id": "research-session", "client_id": "test",
            "surface": "web", "learner_id": "local", "type": "session.new",
            "payload": {"session_mode": "chat", "provider_profile": "fake", "model": "fake-model",
                        "thinking_mode": "default"}})
        assert created.status_code == 200, created.text
        session_id = created.json()["session_id"]
        accepted = client.post("/commands", json={"command_id": "research-send", "client_id": "test",
            "surface": "web", "learner_id": "local", "session_id": session_id, "type": "message.send",
            "payload": {"content": "Research the test topic", "research": {"depth": "deep"}}})
        assert accepted.status_code == 200, accepted.text

        deadline = time.monotonic() + 5
        status = {}
        while time.monotonic() < deadline:
            status = client.get(f"/sessions/{session_id}").json().get("research_status") or {}
            if status.get("status") in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.01)
        assert status.get("status") == "completed", status
        history = client.get(f"/sessions/{session_id}/messages?limit=5").json()
        report_message = next(row for row in history if row["role"] == "assistant")
        research = report_message["metadata"]["research"]
        downloaded = client.get(f"/sessions/{session_id}/reports/{research['report_id']}.md")
        assert downloaded.status_code == 200
        assert downloaded.text == report_message["content"]
        assert "[S1]" in downloaded.text
        assert "[S99]" in downloaded.text
        assert "未核实引用 [S99]" in downloaded.text
        assert downloaded.text.count("https://example.com/research") == 1
        assert "已读取正文" in downloaded.text
        assert len(research["sources"]) == 1
        assert research["sources"][0]["source_type"] == "full_text"
        assert research["sources"][0]["provider"] == "Tavily Search"
        assert search_calls and len(search_calls) == 1
        assert extract_calls == [["https://example.com/research"]]
        progress = [event for event in service.history(session_id) if event["type"] == "research.progress"]
        assert any(event["payload"]["phase"] == "searching" for event in progress)
        assert progress[-1]["payload"]["status"] == "completed"


@pytest.mark.asyncio
async def test_deep_research_cancel_preserves_sources_already_collected(monkeypatch):
    provider = FakeProvider("fake", script=[
        {"content": '{"queries":["first query"]}'},
        {"content": '{"queries":["second query"]}'},
    ])
    service = make_service(provider=provider)
    service.router.secret_store.set(TAVILY_SEARCH_SECRET_REF, "tvly-test-key")
    second_search_started = asyncio.Event()
    search_index = 0

    async def fake_search(query: str, api_key: str, *, client=None):
        nonlocal search_index
        search_index += 1
        if search_index == 1:
            return [{"title": "First source", "url": "https://example.com/first", "snippet": "First summary."}]
        second_search_started.set()
        await asyncio.Event().wait()
        return []

    monkeypatch.setattr("api.sessions.tavily_search", fake_search)
    async def fake_extract(urls: list[str], api_key: str, *, client=None):
        return []
    monkeypatch.setattr("api.sessions.tavily_extract", fake_extract)
    session = Session(learner_id="local", title="cancel research")
    session.metadata.update({"session_mode": "chat", "provider_profile": "fake", "model": "fake-model"})
    ctx = _deep_ctx(session)
    task = asyncio.create_task(_run_deep_research(service, session, "Research cancellation", ctx))
    await asyncio.wait_for(second_search_started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    state = session.metadata["deep_research"]
    assert state["status"] == "cancelled"
    assert state["source_count"] == 1
    assert state["sources"][0]["url"] == "https://example.com/first"


@pytest.mark.asyncio
async def test_deep_research_requires_a_real_search_service_before_model_calls():
    provider = FakeProvider("fake", script=[{"content": "must not run"}])
    service = make_service(provider=provider)
    session = Session(learner_id="local", title="missing search")
    session.metadata.update({"session_mode": "chat", "provider_profile": "fake", "model": "fake-model"})
    with pytest.raises(WebSearchError):
        await _run_deep_research(service, session, "Research without search", _deep_ctx(session))
    assert provider._index == 0
