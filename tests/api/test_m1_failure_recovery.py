"""故障注入验证：Provider 失败、取消与 Gateway 重启恢复。"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from config.settings import Settings
from graph.education.bindings import ACTION_BINDINGS
from runtime.core.errors import ProviderError
from runtime.providers.fake import FakeProvider
from runtime.storage.sqlite_store import SqliteEventStore, SqliteSessionStore
from runtime.testing import make_service
from skills import register_default_skills
from tools.retrieval import build_search_textbook_tool

COURSE_ID = "ds.c_language.v1"
QUERY = "线性表是具有相同特性数据元素的有限序列"


class BlockingProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__(profile_id="fake")
        self.started = threading.Event()
        self.cancelled = threading.Event()

    async def stream(self, request, ctx):
        self.started.set()
        try:
            await asyncio.sleep(60)
        finally:
            self.cancelled.set()
        yield {"type": "delta", "text": "late response"}
        yield {"type": "finish", "finish_reason": "stop"}


def _service(tmp_path: Path, *, provider=None, persistent: bool = False):
    settings = Settings(
        sqlite_path=str(tmp_path / "sessions.sqlite"),
        library_dir=str(tmp_path / "library"),
        sandbox_allowlist=[str(tmp_path)],
    )
    service = make_service(bindings=ACTION_BINDINGS, provider=provider, settings=settings)
    app = create_app(service=service)
    register_default_skills(service.skills)
    service.tools.register(build_search_textbook_tool(service.library.search))
    source = tmp_path / "linear-list.md"
    source.write_text(f"# 线性表\n{QUERY}。", encoding="utf-8")
    service.library.import_path(
        source,
        metadata={"owner_id": "local", "course_id": COURSE_ID, "type": "textbook", "visibility": "public"},
        activate=True,
    )
    if persistent:
        service.events = SqliteEventStore.open(str(settings.resolved_sqlite_path))
        service.sessions = SqliteSessionStore.open(str(settings.resolved_sqlite_path))
    return app, service


def _create_session(client: TestClient, group: str = "A") -> str:
    response = client.post(
        "/commands",
        json={
            "command_id": f"new-{group}-{time.monotonic_ns()}",
            "type": "session.new",
            "payload": {"group": group, "course_id": COURSE_ID},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["session_id"]


def _wait_terminal(client: TestClient, session_id: str, trace_id: str, timeout: float = 5.0) -> list[dict]:
    deadline = time.monotonic() + timeout
    events: list[dict] = []
    while time.monotonic() < deadline:
        events = client.app.state.service.history(session_id)
        if any(item["type"] == "agent.turn.completed" and item["trace_id"] == trace_id for item in events):
            return events
        time.sleep(0.01)
    pytest.fail(f"turn did not reach a terminal state for trace {trace_id}")


def test_provider_error_is_terminal_and_keeps_structured_failure(tmp_path: Path):
    app, service = _service(tmp_path, provider=FakeProvider(script=[ProviderError("injected", kind="upstream_error")]))
    with TestClient(app) as client:
        session_id = _create_session(client)
        response = client.post(
            "/commands",
            json={"command_id": "injected-failure", "type": "message.send", "session_id": session_id,
                  "payload": {"content": QUERY}},
        )
        assert response.status_code == 200
        events = _wait_terminal(client, session_id, response.json()["trace_id"])
        terminal = [event for event in events if event["type"] == "agent.turn.completed"][-1]
        failed = [event for event in events if event["type"] == "model.failed"]
        assert terminal["payload"]["status"] == "failed"
        assert failed and failed[-1]["payload"]["error"]["details"]["kind"] == "upstream_error"


def test_turn_cancel_persists_cancelled_terminal_and_releases_session(tmp_path: Path):
    provider = BlockingProvider()
    app, service = _service(tmp_path, provider=provider)
    with TestClient(app) as client:
        session_id = _create_session(client)
        sent = client.post(
            "/commands",
            json={"command_id": "slow-turn", "type": "message.send", "session_id": session_id,
                  "payload": {"content": QUERY}},
        )
        assert sent.status_code == 200
        assert provider.started.wait(2), "provider stream did not start"
        cancelled = client.post(
            "/commands",
            json={"command_id": "cancel-slow-turn", "type": "turn.cancel", "session_id": session_id, "payload": {}},
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancellation_requested"
        events = _wait_terminal(client, session_id, sent.json()["trace_id"])
        assert [event for event in events if event["type"] == "agent.turn.completed"][-1]["payload"]["status"] == "cancelled"
        session = service.get_session(session_id)
        assert session.metadata["active_turn"] is None
        assert session.metadata["last_turn"]["status"] == "cancelled"
        assert session_id not in client.app.state.active_turns
        assert provider.cancelled.wait(1)


def test_group_b_state_is_scoped_to_session_and_concept(tmp_path: Path):
    app, service = _service(tmp_path)
    with TestClient(app) as client:
        session_id = _create_session(client, "B")

        def send(command_id: str, content: str) -> None:
            response = client.post(
                "/commands",
                json={"command_id": command_id, "type": "message.send", "session_id": session_id,
                      "payload": {"content": content}},
            )
            assert response.status_code == 200, response.text
            _wait_terminal(client, session_id, response.json()["trace_id"])

        send("same-concept-1", "DS-LIN-01 请从头解释线性表。")
        first = service.get_session(session_id).metadata["teaching_state"]
        assert first["concept_id"] == "DS-LIN-01" and first["turn_count"] == 1

        send("same-concept-2", "DS-LIN-01 线性表的定义还包含哪些条件？")
        second = service.get_session(session_id).metadata["teaching_state"]
        assert second["concept_id"] == "DS-LIN-01" and second["turn_count"] == 2

        send("switch-concept", "DS-TREE-01 请问二叉树有哪些存储方式？")
        switched = service.get_session(session_id).metadata["teaching_state"]
        assert switched["concept_id"] == "DS-TREE-01" and switched["turn_count"] == 1

        other_session = _create_session(client, "B")
        assert service.get_session(other_session).metadata.get("teaching_state") is None


def _close_persistent_handles(app, service) -> None:
    for store in (service.events, service.sessions, app.state.command_store, service.library.store):
        close = getattr(store, "close", None)
        if close:
            close()


def test_pending_turn_is_marked_interrupted_after_sqlite_gateway_restart(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DEEPPROF_HOME", str(tmp_path / "developer-home"))
    app_before, service_before = _service(tmp_path, persistent=True)
    try:
        with TestClient(app_before) as client:
            session_id = _create_session(client, "B")
            session = service_before.get_session(session_id)
            session.metadata["active_turn"] = {"command_id": "lost-command", "trace_id": "trc-lost", "status": "running"}
            service_before.save_session(session)
    finally:
        _close_persistent_handles(app_before, service_before)

    app_after, service_after = _service(tmp_path, persistent=True)
    try:
        with TestClient(app_after) as client:
            response = client.post(
                "/commands",
                json={"command_id": "resume-after-restart", "type": "session.resume", "session_id": session_id,
                      "payload": {}},
            )
            assert response.status_code == 200
            events = service_after.history(session_id)
            interrupted = [event for event in events if event["type"] == "agent.turn.completed" and event["trace_id"] == "trc-lost"]
            assert interrupted and interrupted[-1]["payload"] == {"status": "interrupted", "reason_code": "gateway_restarted"}
            session = service_after.get_session(session_id)
            assert session.metadata["active_turn"] is None
            assert session.metadata["last_turn"]["status"] == "interrupted"
    finally:
        _close_persistent_handles(app_after, service_after)
