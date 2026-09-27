"""消息级命令：编辑、重新生成、删除。

这三条命令会就地改写会话历史，所以测试同时盯住四件事：谁允许改写（只有普通对话、
且必须空闲）、改写之后的历史长什么样、有没有留下审计事件、以及被拒绝的请求是不是
真的一个字都没动。末尾补上 session.thinking.set / session.compact / session.fork
此前缺失的 API 层覆盖。
"""

from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient

from api.app import create_app
from runtime.core.message import Message
from runtime.core.session import Session
from runtime.testing import make_service


def _command(client: TestClient, command_id: str, command_type: str, *, session_id: str | None = None,
             payload: dict | None = None):
    return client.post("/commands", json={
        "command_id": command_id, "client_id": "test", "surface": "web", "learner_id": "local",
        "session_id": session_id, "type": command_type, "payload": payload or {},
    })


def _chat_session(*turns: tuple[str, str], title: str = "普通对话") -> Session:
    """构造一个绑定到测试 Provider 的 chat 会话。"""
    session = Session(title=title, metadata={"session_mode": "chat", "provider_profile": "fake", "model": "fake-model"})
    for role, content in turns:
        session.append(Message(role=role, content=content))
    return session


def _stored(service, session: Session) -> list[tuple[str, str]]:
    """从存储里读回历史。

    回合是后台任务跑的，写回的是它自己那个 Session 对象，所以断言一律以存储为准，
    不去依赖调用方手里那份引用。
    """
    return [(message.role, message.content) for message in service.get_session(session.session_id).messages]


def _events(service, session_id: str, event_type: str) -> list[dict]:
    return [event for event in service.history(session_id) if event["type"] == event_type]


def _wait_for_turn(service, session_id: str, *, timeout: float = 5.0) -> list[dict]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        events = service.history(session_id)
        if any(event["type"] == "agent.turn.completed" for event in events):
            return events
        time.sleep(0.01)
    raise AssertionError("回合未在超时前结束")


# --------------------------------------------------------------------------- 删除


def test_message_delete_drops_one_message_and_audits_without_leaking_text():
    service = make_service()
    session = _chat_session(("user", "第一问"), ("assistant", "第一答"), ("user", "第二问"))
    service.save_session(session)
    with TestClient(create_app(service=service)) as client:
        response = _command(client, "del", "message.delete", session_id=session.session_id, payload={"index": 1})
        assert response.status_code == 200
        assert response.json()["result"] == {"index": 1, "role": "assistant", "remaining": 2}

    assert _stored(service, session) == [("user", "第一问"), ("user", "第二问")]
    audits = _events(service, session.session_id, "message.deleted")
    assert [event["payload"] for event in audits] == [{"index": 1, "role": "assistant", "remaining": 2}]
    # 审计事件只记序号与角色：正文一个字都不能进事件流。
    assert "第一答" not in json.dumps(audits, ensure_ascii=False)


def test_replay_projects_message_audit_events_to_locator_only_fields():
    service = make_service()
    session = _chat_session(("user", "秘密正文-12345"), ("assistant", "另一段正文"))
    service.save_session(session)
    with TestClient(create_app(service=service)) as client:
        assert _command(client, "del", "message.delete", session_id=session.session_id,
                        payload={"index": 0}).status_code == 200
        response = client.get(f"/replay/sessions/{session.session_id}/events")

    assert response.status_code == 200
    deleted = next(event for event in response.json() if event["type"] == "message.deleted")
    assert deleted["payload"] == {"index": 0, "role": "user", "remaining": 1}
    assert "秘密正文" not in response.text


# --------------------------------------------------------------------------- 编辑


def test_message_edit_truncates_following_history_and_reruns_the_turn():
    service = make_service(script=[{"content": "改后的回答"}])
    session = _chat_session(("user", "问题一"), ("assistant", "回答一"), ("user", "问题二"), ("assistant", "回答二"))
    service.save_session(session)
    with TestClient(create_app(service=service)) as client:
        response = _command(client, "edit", "message.edit", session_id=session.session_id,
                            payload={"index": 2, "content": "  改后的问题  "})
        assert response.status_code == 200
        assert response.json()["result"] == {"index": 2, "dropped": 2}
        _wait_for_turn(service, session.session_id)

    # 被编辑那条之后的历史（问题二、回答二）必须整段丢掉，再接上新的这一轮。
    assert _stored(service, session) == [("user", "问题一"), ("assistant", "回答一"),
                                         ("user", "改后的问题"), ("assistant", "改后的回答")]
    assert [event["payload"] for event in _events(service, session.session_id, "message.edited")] == [
        {"index": 2, "dropped": 2}]


def test_message_edit_rejects_assistant_targets_and_blank_content_without_touching_history():
    service = make_service()
    session = _chat_session(("user", "问题一"), ("assistant", "回答一"))
    service.save_session(session)
    before = _stored(service, session)
    with TestClient(create_app(service=service)) as client:
        response = _command(client, "edit-assistant", "message.edit", session_id=session.session_id,
                            payload={"index": 1, "content": "篡改"})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "not_a_user_message"

        response = _command(client, "edit-blank", "message.edit", session_id=session.session_id,
                            payload={"index": 0, "content": "   "})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "message_content_required"

    # 被拒绝的请求不留痕：历史与事件流都不变。
    assert _stored(service, session) == before
    assert service.history(session.session_id) == []


def test_message_edit_requires_model_reselection_when_the_profile_is_gone():
    service = make_service()
    session = Session(title="失联会话",
                      metadata={"session_mode": "chat", "provider_profile": "已经删掉的服务", "model": "fake-model"})
    session.append(Message(role="user", content="问题"))
    service.save_session(session)
    with TestClient(create_app(service=service)) as client:
        response = _command(client, "edit", "message.edit", session_id=session.session_id,
                            payload={"index": 0, "content": "改写"})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "model_reselection_required"

    # 模型不可用时也不能截断历史——校验全部通过之前不许动会话。
    assert _stored(service, session) == [("user", "问题")]
    assert _events(service, session.session_id, "message.edited") == []


# ----------------------------------------------------------------------- 重新生成


def test_message_regenerate_replaces_the_last_answer_without_duplicating_the_question():
    service = make_service(script=[{"content": "重写的回答"}])
    session = _chat_session(("user", "唯一的问题"), ("assistant", "旧回答"))
    service.save_session(session)
    with TestClient(create_app(service=service)) as client:
        response = _command(client, "regen", "message.regenerate", session_id=session.session_id)
        assert response.status_code == 200
        assert response.json()["result"] == {"index": 1, "remaining": 1}
        _wait_for_turn(service, session.session_id)

    assert _stored(service, session) == [("user", "唯一的问题"), ("assistant", "重写的回答")]
    # 关键回归：重新生成不得把上一轮的提问再追加一次，否则上下文会重复。
    assert [message.content for message in service.get_session(session.session_id).messages].count("唯一的问题") == 1
    assert [event["payload"] for event in _events(service, session.session_id, "message.regenerated")] == [
        {"index": 1, "remaining": 1}]


def test_message_regenerate_only_accepts_the_trailing_answer():
    service = make_service()
    session = _chat_session(("user", "问题一"), ("assistant", "回答一"), ("user", "问题二"), ("assistant", "回答二"))
    service.save_session(session)
    orphan = _chat_session(("assistant", "没有提问的回答"), title="孤立回答")
    service.save_session(orphan)
    before = _stored(service, session)
    with TestClient(create_app(service=service)) as client:
        response = _command(client, "regen-old", "message.regenerate", session_id=session.session_id,
                            payload={"index": 1})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "not_the_last_message"

        response = _command(client, "regen-user", "message.regenerate", session_id=session.session_id,
                            payload={"index": 2})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "not_an_assistant_message"

        response = _command(client, "regen-orphan", "message.regenerate", session_id=orphan.session_id)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "no_preceding_user_message"

    assert _stored(service, session) == before
    assert _stored(service, orphan) == [("assistant", "没有提问的回答")]


# ------------------------------------------------------------------- 门禁与参数校验


def test_message_operations_are_rejected_on_study_sessions():
    service = make_service()
    study = Session(title="学习会话", metadata={"session_mode": "study"})
    study.append(Message(role="user", content="教学提问"))
    study.append(Message(role="assistant", content="教学回答"))
    service.save_session(study)
    with TestClient(create_app(service=service)) as client:
        for command_id, command_type, payload in (
            ("study-del", "message.delete", {"index": 0}),
            ("study-edit", "message.edit", {"index": 0, "content": "改写"}),
            ("study-regen", "message.regenerate", {}),
        ):
            response = _command(client, command_id, command_type, session_id=study.session_id, payload=payload)
            assert response.status_code == 409, command_type
            assert response.json()["error"]["code"] == "chat_session_required", command_type

    # 教学会话承载 M1–M3 的实验证据，历史一旦被改写就无法回溯。
    assert _stored(service, study) == [("user", "教学提问"), ("assistant", "教学回答")]


def test_message_operations_are_rejected_while_a_turn_is_active():
    service = make_service()
    session = _chat_session(("user", "问题"), ("assistant", "回答"))
    service.save_session(session)
    app = create_app(service=service)
    app.state.active_turns.add(session.session_id)
    before = _stored(service, session)
    with TestClient(app) as client:
        for command_id, command_type, payload in (
            ("busy-del", "message.delete", {"index": 0}),
            ("busy-edit", "message.edit", {"index": 0, "content": "改写"}),
            ("busy-regen", "message.regenerate", {}),
        ):
            response = _command(client, command_id, command_type, session_id=session.session_id, payload=payload)
            assert response.status_code == 409, command_type
            assert response.json()["error"]["code"] == "turn_already_active", command_type

    assert _stored(service, session) == before


def test_message_index_must_be_an_integer_inside_the_history():
    service = make_service()
    session = _chat_session(("user", "问题"), ("assistant", "回答"))
    service.save_session(session)
    with TestClient(create_app(service=service)) as client:
        for command_id, payload in (("idx-high", {"index": 5}), ("idx-negative", {"index": -1}),
                                    ("idx-text", {"index": "第二条"}), ("idx-missing", {})):
            response = _command(client, command_id, "message.delete", session_id=session.session_id, payload=payload)
            assert response.status_code == 422, payload
            assert response.json()["error"]["code"] == "invalid_message_index", payload

    assert _stored(service, session) == [("user", "问题"), ("assistant", "回答")]


def test_message_commands_are_wired_and_demand_a_session_id():
    """三条命令必须真的进网关分发，而不是落到 501 兜底里。"""
    service = make_service()
    with TestClient(create_app(service=service)) as client:
        for command_type in ("message.edit", "message.regenerate", "message.delete"):
            response = _command(client, f"no-session-{command_type}", command_type)
            assert response.status_code == 400, command_type
            assert "session_id" in response.text


# --------------------------------------------------------- 既有覆盖缺口（API 层）


def test_session_thinking_toggle_requires_confirmed_model_capability():
    service = make_service()
    profile = service.router.profile("fake")
    session = _chat_session(("user", "问题"), title="思考开关")
    service.save_session(session)
    with TestClient(create_app(service=service)) as client:
        # fake 模型没声明 reasoning_mode，开启思考必须被拒绝，而不是默默生效。
        response = _command(client, "think-on", "session.thinking.set", session_id=session.session_id,
                            payload={"enabled": True})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "thinking_capability_unknown"
        assert not session.metadata.get("thinking_enabled")

        profile.model_capabilities = {"fake-model": {"reasoning_mode": "toggle"}}
        response = _command(client, "think-on-toggle", "session.thinking.set", session_id=session.session_id,
                            payload={"enabled": True})
        assert response.status_code == 200
        assert response.json()["result"] == {"enabled": True, "reasoning_mode": "toggle"}

        response = _command(client, "think-off-toggle", "session.thinking.set", session_id=session.session_id,
                            payload={"enabled": False})
        assert response.status_code == 200
        assert response.json()["result"] == {"enabled": False, "reasoning_mode": "toggle"}

        # 固定思考的模型关不掉，但打开时会稳定为 True。
        profile.model_capabilities = {"fake-model": {"reasoning_mode": "always"}}
        response = _command(client, "think-off-always", "session.thinking.set", session_id=session.session_id,
                            payload={"enabled": False})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "thinking_always_on"

        response = _command(client, "think-on-always", "session.thinking.set", session_id=session.session_id,
                            payload={"enabled": True})
        assert response.status_code == 200
        assert response.json()["result"]["enabled"] is True


def test_session_compact_summarizes_the_head_and_keeps_recent_messages():
    service = make_service()
    session = _chat_session(title="长对话", *[("user", f"第{index}条") for index in range(10)])
    service.save_session(session)
    with TestClient(create_app(service=service)) as client:
        response = _command(client, "compact", "session.compact", session_id=session.session_id, payload={"keep": 3})
        assert response.status_code == 200
        assert response.json()["result"] == {"before": 10, "after": 4}

    assert [event["payload"] for event in _events(service, session.session_id, "session.compacted")] == [
        {"before": 10, "after": 4}]
    assert [message.content for message in service.get_session(session.session_id).messages[-3:]] == [
        "第7条", "第8条", "第9条"]


def test_session_fork_copies_history_and_returns_the_new_session():
    service = make_service()
    session = _chat_session(("user", "主线提问"), ("assistant", "主线回答"), title="主线")
    service.save_session(session)
    with TestClient(create_app(service=service)) as client:
        response = _command(client, "fork", "session.fork", session_id=session.session_id, payload={"title": "分支"})
        assert response.status_code == 200
        forked_id = response.json()["session_id"]
        assert forked_id != session.session_id
        forked = client.get(f"/sessions/{forked_id}").json()

    assert forked["title"] == "分支"
    assert forked["parent_id"] == session.session_id
    assert [message.content for message in service.get_session(forked_id).messages] == ["主线提问", "主线回答"]
    # 分支互不影响。
    assert _stored(service, session) == [("user", "主线提问"), ("assistant", "主线回答")]
