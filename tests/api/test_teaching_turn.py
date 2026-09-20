"""教学策略图链路测试（§7.2 主会话链路 / §9.1 依赖方向）。

覆盖: UI / API → Pedagogical Graph → RuntimePort → RuntimeService：
- 一轮教学请求能跑通图，SSE 推出教学事件与收尾帧；
- 收尾帧给出交前端的三件套（response_text / action / emotion，§16.3）；
- 教学状态（turn_count 等）跨轮延续并随会话落盘；
- 整个链路的事件都挂同一个 trace_id，可用 GET /events 复盘。

模型用 FakeProvider（确定性、不触网）：图必须容忍"模型只回自然语言"，
不能要求模型输出严格 JSON，否则换模型就会崩。
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from config import settings
from tests.conftest import make_client, make_service


def _sse_frames(body: str) -> list[dict]:
    """把 SSE 响应解析成帧列表：``{"event": 名称, "data": RuntimeEvent dict}``。"""
    frames: list[dict] = []
    for block in body.strip().split("\n\n"):
        if not block.strip():
            continue
        name, data = "", {}
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[len("event: ") :]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: ") :])
        frames.append({"event": name, "data": data})
    return frames


def _new_session(client: TestClient, learner_id: str = "learner_a") -> str:
    response = client.post("/sessions", json={"learner_id": learner_id})
    assert response.status_code == 201
    return response.json()["session_id"]


def test_teaching_turn_runs_graph_and_returns_frontend_payload():
    service = make_service(with_sandbox=True)
    client = make_client(service)
    session_id = _new_session(client)

    response = client.post(
        f"/sessions/{session_id}/teaching-turn",
        json={"content": "老师，我不太理解梯度下降"},
        headers={settings.trace_id_header: "trace_teach_1"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = _sse_frames(response.text)

    # 图自己的事件（节点进出 / 决策）必须出现在流里，且都挂同一个 trace_id
    graph_events = [
        frame for frame in frames if str(frame["data"].get("type", "")).startswith("pedagogy.")
    ]
    assert graph_events, "教学图没有产生 pedagogy.* 事件"
    assert all(frame["data"]["trace_id"] == "trace_teach_1" for frame in frames)

    # 收尾帧：交前端的三件套（§16.3）
    final = frames[-1]
    assert final["event"] == "done"
    payload = final["data"]["payload"]
    assert payload["response_text"]
    assert payload["action"] in {"teach", "ask", "hint", "correct", "test", "reflect"}
    assert "emotion" in payload
    assert payload["source"] == "deepprof.api"  # 该帧由 API 合成、不入事件库

    # SSE 的 id 行即 sequence，必须全程单调递增：前端据此断线重连不重复渲染（§18.2）
    sequences = [frame["data"]["sequence"] for frame in frames]
    assert sequences == sorted(set(sequences)) and len(sequences) == len(frames)

    # 会话消息落盘：学生输入与本轮回复都记进会话
    session = service.session_store.load(session_id)
    assert [message.role for message in session.messages] == ["user", "assistant"]
    assert session.messages[0].content == "老师，我不太理解梯度下降"


def test_teaching_state_continues_across_turns_and_is_replayable():
    service = make_service(with_sandbox=True)
    client = make_client(service)
    session_id = _new_session(client)
    url = f"/sessions/{session_id}/teaching-turn"

    first = _sse_frames(client.post(url, json={"content": "什么是导数？"}).text)[-1]
    second = _sse_frames(client.post(url, json={"content": "那它和极限有什么关系？"}).text)[-1]

    # 教学状态跨轮延续（§6.4）：轮次递增而不是每轮重新开始
    assert second["data"]["payload"]["turn_count"] > first["data"]["payload"]["turn_count"]

    # 事件可用回放接口复盘
    replay = client.get(f"/sessions/{session_id}/events").json()
    types = [event["type"] for event in replay["events"]]
    assert any(name.startswith("pedagogy.") for name in types)

    # 教学决策事件只记依据与长度，不复制学生正文（§6.4：避免图状态隐私复制）。
    # 整条会话轨迹（含工具参数、模型增量）是审计事实，导出时才脱敏（§13.2）。
    pedagogy_events = [e for e in replay["events"] if e["type"].startswith("pedagogy.")]
    assert pedagogy_events
    assert not any(
        "什么是导数" in json.dumps(event, ensure_ascii=False) for event in pedagogy_events
    )

    session = service.session_store.load(session_id)
    assert len(session.messages) == 4


def test_teaching_turn_rejects_unknown_session():
    client = make_client(make_service(with_sandbox=True))

    response = client.post("/sessions/sess_missing/teaching-turn", json={"content": "在吗"})

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "session_not_found"