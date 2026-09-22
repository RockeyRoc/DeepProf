"""Gateway-level MVP-5 handoff checks; no real provider or network required."""

from fastapi.testclient import TestClient

from api.app import create_app
from runtime.testing import make_service


def _command(command_id: str, kind: str, *, surface: str = "cli", session_id: str | None = None, payload=None):
    return {
        "command_id": command_id,
        "client_id": surface,
        "surface": surface,
        "learner_id": "local",
        "session_id": session_id,
        "type": kind,
        "payload": payload or {},
    }


def test_desktop_session_can_be_resumed_by_cli_with_safe_transcript():
    service = make_service()
    with TestClient(create_app(service=service)) as client:
        created = client.post("/commands", json=_command("new", "session.new", surface="desktop", payload={"title": "handoff"})).json()
        session_id = created["session_id"]
        client.post("/commands", json=_command("resume", "session.resume", surface="cli", session_id=session_id))
        client.post("/commands", json=_command("ask", "message.send", surface="cli", session_id=session_id, payload={"content": "continue"}))
        client.post("/commands", json=_command("fork", "session.fork", surface="cli", session_id=session_id, payload={"title": "branch"}))
        transcript = client.get(f"/sessions/{session_id}/messages").json()
        assert [item["role"] for item in transcript] == ["user", "assistant"]
        assert all(set(item) <= {"index", "role", "content", "name"} for item in transcript)
        events = service.history(session_id)
        assert {event["surface"] for event in events} == {"desktop", "cli"}


def test_default_provider_binding_survives_new_runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPPROF_HOME", str(tmp_path / "home"))
    first = create_app()
    with TestClient(first) as client:
        response = client.put(
            "/providers/mock",
            json={"profile_id": "mock", "base_url": "http://127.0.0.1:11434/v1", "default_model": "mock-model"},
        )
        assert response.status_code == 200
    second = create_app()
    with TestClient(second) as client:
        selection = client.get("/providers/default").json()
        assert selection["profile_id"] == "mock"
