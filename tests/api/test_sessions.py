"""会话接入测试（DESIGNv0.4 §8 / §7.2 / §18.2）。

覆盖：
- POST /sessions 创建会话，重复 session_id 按恢复处理且不改写 learner_id；
- POST /sessions/{id}/messages 返回 SSE，帧内含 delta 与 done，
  data 为 RuntimeEvent dict 形状，sequence 单调递增；
- trace_id 通过 ``settings.trace_id_header`` 透传；
- GET /sessions/{id}/events 可按 from_sequence 回放（断线重连不重复渲染）；
- 不存在的会话返回结构化 404，而不是 500。

测试用 FakeProvider + 内存 SQLite，不触网。
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from api.app import create_app
from api.deps import get_runtime_service
from runtime.providers.fake import FakeProvider
from runtime.service import RuntimeService
from runtime.storage.sqlite_store import SqliteDatabase

#: RuntimeEvent 的 dict 形状（§18.2），缺少任一字段前端就无法重连对账
EVENT_FIELDS = (
    "event_id",
    "session_id",
    "trace_id",
    "sequence",
    "type",
    "payload",
    "source",
    "timestamp",
)


def build_service() -> RuntimeService:
    """构造不触网的 RuntimeService。"""
    return RuntimeService(provider=FakeProvider(), db=SqliteDatabase(":memory:"))


def build_client(service: RuntimeService) -> TestClient:
    """注入 RuntimeService 并返回 TestClient。"""
    app = create_app(runtime=service)
    app.dependency_overrides[get_runtime_service] = lambda: service
    return TestClient(app)


def parse_sse(body: str) -> list[dict]:
    """把 SSE 文本解析成 [{"frame": 事件名, "id": sequence, "event": 事件 dict}]。"""
    frames: list[dict] = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        name, data, frame_id = "", "", ""
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[len("event: ") :]
            elif line.startswith("data: "):
                data = line[len("data: ") :]
            elif line.startswith("id: "):
                frame_id = line[len("id: ") :]
        if data:
            frames.append({"frame": name, "id": frame_id, "event": json.loads(data)})
    return frames


def create_session(client: TestClient, learner_id: str = "stu-001", session_id: str = "") -> str:
    """创建一个会话并返回 session_id。"""
    response = client.post("/sessions", json={"learner_id": learner_id, "session_id": session_id})
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


# ================= 会话创建 =================
def test_create_and_resume_session_keeps_learner():
    """创建会话返回 201；同一 session_id 再请求按恢复处理，不改写 learner_id。"""
    with build_client(build_service()) as client:
        first = client.post("/sessions", json={"learner_id": "stu-001", "session_id": "sess-fixed"})
        assert first.status_code == 201
        assert first.json()["session_id"] == "sess-fixed"
        assert first.json()["learner_id"] == "stu-001"
        assert first.json()["message_count"] == 0

        again = client.post("/sessions", json={"learner_id": "stu-001", "session_id": "sess-fixed"})
        assert again.status_code == 201
        assert again.json()["created_at"] == first.json()["created_at"]  # 仍是同一会话

        # 换一个 learner_id 接管同一会话必须被拒绝（§17.3 用户级隔离）
        conflict = client.post("/sessions", json={"learner_id": "stu-002", "session_id": "sess-fixed"})
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "learner_mismatch"


# ================= SSE 流式 =================
def test_message_stream_contains_delta_and_done():
    """POST messages 返回 SSE：含 delta 与 done，事件形状为 RuntimeEvent dict。"""
    with build_client(build_service()) as client:
        session_id = create_session(client)
        response = client.post(
            f"/sessions/{session_id}/messages",
            json={
                "session_id": session_id,
                "learner_id": "stu-001",
                "request_id": "req-0001",
                "content": "什么是导数？",
            },
        )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")

        frames = parse_sse(response.text)
        names = [frame["frame"] for frame in frames]
        assert "delta" in names, response.text
        assert "done" in names, response.text

        events = [frame["event"] for frame in frames]
        for event in events:
            assert set(EVENT_FIELDS) <= set(event), event  # 形状完整
            assert event["session_id"] == session_id
            assert event["type"]  # 事件类型非空
        assert events[-1]["type"] == "agent.turn.completed"

        # 文本增量与 FakeProvider 的回显一致（证明流式内容确实来自模型事件）
        deltas = [e for e in events if e["type"] == "model.stream.delta"]
        assert deltas and "导数" in "".join(e["payload"]["text"] for e in deltas)

        # sequence 单调递增，前端可据此断线重连且不重复渲染
        sequences = [event["sequence"] for event in events]
        assert sequences == sorted(set(sequences)) and len(sequences) > 1
        # SSE 的 id 行就是 sequence
        assert frames[0]["id"] == str(events[0]["sequence"])


def test_trace_id_header_is_passed_through():
    """请求头 trace_id 存在时作为本轮 trace_id，全部事件都归属于它。"""
    with build_client(build_service()) as client:
        session_id = create_session(client)
        response = client.post(
            f"/sessions/{session_id}/messages",
            json={"content": "再讲一遍"},
            headers={"x-trace-id": "trace-api-test-1"},
        )

        events = [frame["event"] for frame in parse_sse(response.text)]
        assert events
        assert {event["trace_id"] for event in events} == {"trace-api-test-1"}


# ================= 事件回放 =================
def test_events_replay_by_sequence():
    """GET events 能回放事件；用 latest_sequence 再查不会重复返回（断线重连）。"""
    with build_client(build_service()) as client:
        session_id = create_session(client)
        client.post(f"/sessions/{session_id}/messages", json={"content": "什么是积分？"})

        replay = client.get(f"/sessions/{session_id}/events", params={"from_sequence": 0})
        assert replay.status_code == 200
        payload = replay.json()
        types = [event["type"] for event in payload["events"]]

        assert "session.started" in types
        assert "model.stream.delta" in types
        assert "agent.turn.completed" in types
        assert payload["latest_sequence"] == max(e["sequence"] for e in payload["events"])
        for event in payload["events"]:
            assert set(EVENT_FIELDS) <= set(event)

        # 已渲染到 latest_sequence 后重连：不应再拿到重复事件
        resumed = client.get(
            f"/sessions/{session_id}/events",
            params={"from_sequence": payload["latest_sequence"]},
        )
        assert resumed.json()["events"] == []
        assert resumed.json()["latest_sequence"] == payload["latest_sequence"]


# ================= 会话不存在 =================
def test_unknown_session_returns_structured_404():
    """会话不存在时返回结构化 404（session_not_found），不能是 500。"""
    with build_client(build_service()) as client:
        missing = client.post("/sessions/no-such-session/messages", json={"content": "你好"})
        assert missing.status_code == 404
        assert missing.json()["detail"]["code"] == "session_not_found"

        replay = client.get("/sessions/no-such-session/events")
        assert replay.status_code == 404
        assert replay.json()["detail"]["code"] == "session_not_found"