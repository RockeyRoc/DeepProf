from __future__ import annotations

import time
import json
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from api.replay import _safe_event
from config.settings import Settings
from graph.education.bindings import ACTION_BINDINGS
from graph.education.policies import GENERATE_TEMPERATURE
from runtime.testing import make_service
from skills import register_default_skills
from tools.retrieval import build_search_textbook_tool

QUERY = "DS-LIN-01 线性表具有相同特性数据元素的有限序列"
STUDENT_SECRET = "SecretStudentText-94"
LEAKING_MODEL_REPLY = "答案是线性表。你明白了吗？"
COURSE_ID = "ds.c_language.v1"


def _make_app(tmp_path: Path):
    settings = Settings(
        sqlite_path=str(tmp_path / "sessions.sqlite"),
        library_dir=str(tmp_path / "library"),
        sandbox_allowlist=[str(tmp_path)],
    )
    captured_requests: list[dict] = []
    def capture_request(request: dict) -> dict:
        captured_requests.append(dict(request))
        return {"content": LEAKING_MODEL_REPLY}

    service = make_service(
        bindings=ACTION_BINDINGS,
        script=[capture_request],
        settings=settings,
    )
    service.test_model_requests = captured_requests
    register_default_skills(service.skills)
    app = create_app(service=service)
    service.sandbox.allowed_dirs.append(tmp_path.resolve())
    source = tmp_path / "linear-list.md"
    source.write_text(f"# 线性表\n{QUERY}。", encoding="utf-8")
    service.library.import_path(
        source,
        metadata={
            "owner_id": "local",
            "course_id": COURSE_ID,
            "type": "textbook",
            "visibility": "public",
        },
        activate=True,
    )
    service.tools.register(build_search_textbook_tool(service.library.search))
    return app, service


def _send(client: TestClient, group: str) -> tuple[str, dict, list[dict]]:
    created = client.post(
        "/commands",
        json={
            "command_id": f"new-{group}",
            "type": "session.new",
            "payload": {"group": group, "course_id": COURSE_ID},
        },
    )
    assert created.status_code == 200, created.text
    session_id = created.json()["session_id"]
    text = f"{QUERY}。我的口令是 {STUDENT_SECRET}。"
    sent = client.post(
        "/commands",
        json={
            "command_id": f"send-{group}",
            "type": "message.send",
            "session_id": session_id,
            "payload": {"content": text},
        },
    )
    assert sent.status_code == 200, sent.text

    deadline = time.monotonic() + 5
    events: list[dict] = []
    while time.monotonic() < deadline:
        events = client.app.state.service.history(session_id)
        if any(item["type"] == "agent.turn.completed" for item in events):
            break
        time.sleep(0.01)
    assert any(item["type"] == "agent.turn.completed" for item in events), events
    assert events[-1]["payload"]["status"] == "ok"
    return session_id, sent.json(), events


def test_ab_paths_and_replay_are_separate_read_only_and_redacted(tmp_path: Path):
    app, service = _make_app(tmp_path)
    with TestClient(app) as client:
        a_id, a_receipt, a_events = _send(client, "A")
        b_id, b_receipt, b_events = _send(client, "B")

        assert a_receipt["trace_id"] and b_receipt["trace_id"]
        a_session = service.get_session(a_id)
        b_session = service.get_session(b_id)
        assert a_session.metadata["experiment"]["group"] == "A"
        assert b_session.metadata["experiment"]["group"] == "B"
        assert a_session.metadata["experiment"]["model"] == b_session.metadata["experiment"]["model"] == "fake-model"
        assert a_session.metadata["experiment"]["sampling"] == {"temperature": GENERATE_TEMPERATURE}
        assert [item["temperature"] for item in service.test_model_requests] == [GENERATE_TEMPERATURE, GENERATE_TEMPERATURE]

        assert any(e["type"] == "teaching.decision" and e["payload"]["group"] == "A" for e in a_events)
        assert not any(e["type"].startswith("pedagogy.") for e in a_events)
        assert any(e["type"] == "pedagogy.decision" for e in b_events)
        assert any(e["type"] == "teaching.turn.completed" for e in a_events + b_events)

        for sid, events in ((a_id, a_events), (b_id, b_events)):
            assert not any(e["type"] == "model.stream.delta" for e in events)
            assert LEAKING_MODEL_REPLY not in service.get_session(sid).messages[-1].content
            replay = client.get(f"/replay/sessions/{sid}")
            assert replay.status_code == 200
            safe = replay.text
            assert STUDENT_SECRET not in safe
            assert "具有相同特性数据元素的有限序列" not in safe
            assert LEAKING_MODEL_REPLY not in safe
            assert any(ref.get("page") == 1 for event in replay.json()["events"]
                       for ref in event["payload"].get("evidence_refs", []))
            assert client.post(f"/replay/sessions/{sid}").status_code == 405


def test_replay_exposes_only_allowlisted_failure_metadata_and_numeric_usage():
    safe = _safe_event({
        "type": "model.failed", "sequence": 3, "trace_id": "trace-safe",
        "payload": {"error": {"code": "model_truncated", "message": "secret provider response",
            "details": {"kind": "model_truncated", "finish_reason": "length",
                "usage": {"prompt_tokens": 41, "completion_tokens": 8, "total_tokens": 49,
                    "authorization": "sk-sensitive"}}}},
    })

    assert safe["payload"] == {
        "error": {"code": "model_truncated", "kind": "model_truncated"},
        "usage": {"prompt_tokens": 41, "completion_tokens": 8, "total_tokens": 49},
        "finish_reason": "length",
    }
    assert "secret provider response" not in json.dumps(safe)
    assert "sk-sensitive" not in json.dumps(safe)


def test_quiz_scoring_is_idempotent_answer_private_and_current_question_only(tmp_path: Path, monkeypatch):
    home = tmp_path / "dev-home"
    (home / "course").mkdir(parents=True)
    bank = {
        "version": "test-bank-v1", "teacher_approval": "pending",
        "questions": [{"item_id": "q1", "source_review_status": "verified", "module": "线性表",
            "grading_review_status": "verified",
            "concept_ids": ["DS-LIN-01"], "scored_concept_id": "DS-LIN-01", "question_type": "short_answer",
            "difficulty": 1, "prompt": "输出一个标记词", "answer_instruction": "请输入标记词",
            "grading": {"method": "exact_normalized_match", "accepted_answers": ["correct-secret"]},
            "source_ref": {"source": "fixture", "question_pdf_page": 1,
                "answer_key_pdf_page": 999, "printed_answer_page": 998}}],
    }
    (home / "course" / "question_bank.json").write_text(json.dumps(bank, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("DEEPPROF_HOME", str(home))
    app, service = _make_app(tmp_path)
    with TestClient(app) as client:
        created = client.post("/commands", json={"command_id": "quiz-session", "type": "session.new",
            "payload": {"group": "B", "course_id": COURSE_ID}}).json()
        session_id = created["session_id"]
        issued = client.post("/commands", json={"command_id": "quiz-issue", "type": "quiz.generate",
            "session_id": session_id, "payload": {}})
        assert issued.status_code == 200
        question = issued.json()["result"]["question"]
        assert question["item_id"] == "q1"
        assert "grading" not in question and "accepted_answers" not in json.dumps(question)
        assert "answer_key_pdf_page" not in json.dumps(question)

        wrong = {"command_id": "quiz-wrong-once", "type": "quiz.answer", "session_id": session_id,
            "payload": {"item_id": "q1", "answer": "wrong-secret"}}
        first = client.post("/commands", json=wrong)
        assert first.status_code == 200 and first.json()["result"]["correct"] is False
        duplicate = client.post("/commands", json=wrong)
        assert duplicate.json()["trace_id"] == first.json()["trace_id"]
        attempts = client.app.state.command_store._conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
        assert attempts == 1
        stored_concepts = client.app.state.command_store._conn.execute(
            "SELECT concept_ids FROM attempts WHERE item_id='q1'"
        ).fetchone()[0]
        assert json.loads(stored_concepts) == ["DS-LIN-01"]
        session = service.get_session(session_id)
        assert session.metadata["active_quiz"]["item_id"] == "q1"
        assert session.metadata["teaching_state"]["wrong_streak"] == 1
        assert session.metadata["teaching_state"]["last_answer_correct"] is False

        sent = client.post("/commands", json={"command_id": "quiz-followup-hint", "type": "message.send",
            "session_id": session_id, "payload": {"content": "再给我一个提示", "requested_action": "hint"}})
        assert sent.status_code == 200
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            events = service.history(session_id)
            if any(item["type"] == "agent.turn.completed" and item["trace_id"] == sent.json()["trace_id"] for item in events):
                break
            time.sleep(0.01)
        entered = [item for item in events if item["type"] == "pedagogy.node.entered" and item["trace_id"] == sent.json()["trace_id"]]
        assert entered and entered[0]["payload"]["attempt_count"] == 1
        decisions = [item for item in events if item["type"] == "pedagogy.decision" and item["trace_id"] == sent.json()["trace_id"]]
        assert decisions and decisions[0]["payload"]["wrong_streak"] == 1
        assert service.get_session(session_id).metadata["active_quiz"]["hint_count"] == 1

        second = client.post("/commands", json={"command_id": "quiz-correct", "type": "quiz.answer",
            "session_id": session_id, "payload": {"item_id": "q1", "answer": "correct-secret"}})
        assert second.status_code == 200 and second.json()["result"]["correct"] is True
        assert service.get_session(session_id).metadata["active_quiz"] is None
        assert service.get_session(session_id).metadata["teaching_state"]["last_answer_correct"] is True
        replay = client.get(f"/replay/sessions/{session_id}").text
        assert "wrong-secret" not in replay and "correct-secret" not in replay
        replay_json = client.get(f"/replay/sessions/{session_id}").json()
        attempt_events = [event for event in replay_json["events"]
                          if event["type"] == "pedagogy.attempt_skipped"]
        assert attempt_events and attempt_events[-1]["payload"]["concept_ids"] == ["DS-LIN-01"]
        attempts = client.app.state.command_store._conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
        assert attempts == 2


def _write_m2_bank(home: Path) -> None:
    questions = []
    for index, difficulty in enumerate((1, 1, 1, 2), start=1):
        questions.append({
            "item_id": f"q{index}", "source_review_status": "verified", "module": "线性表",
            "grading_review_status": "verified", "concept_ids": ["DS-LIN-01"],
            "scored_concept_id": "DS-LIN-01", "question_type": "short_answer", "difficulty": difficulty,
            "prompt": f"第 {index} 道练习", "answer_instruction": "请输入标记词",
            "grading": {"method": "exact_normalized_match", "accepted_answers": ["correct-secret"]},
            "source_ref": {"source": "fixture", "question_pdf_page": index},
        })
    (home / "course" / "question_bank.json").write_text(json.dumps({
        "version": "m2-bank-v1", "teacher_approval": "pending", "questions": questions,
    }, ensure_ascii=False), encoding="utf-8")


def test_c_group_persists_shared_bkt_and_test_node_issues_reviewed_question(tmp_path: Path, monkeypatch):
    home = tmp_path / "m2-home"
    (home / "course").mkdir(parents=True)
    _write_m2_bank(home)
    monkeypatch.setenv("DEEPPROF_HOME", str(home))
    app, service = _make_app(tmp_path)
    with TestClient(app) as client:
        session = client.post("/commands", json={"command_id": "m2-c-session", "type": "session.new",
            "learner_id": "shared-student", "payload": {"group": "C", "course_id": COURSE_ID}}).json()
        session_id = session["session_id"]
        for index in range(1, 4):
            issued = client.post("/commands", json={"command_id": f"m2-c-issue-{index}", "type": "quiz.generate",
                "session_id": session_id, "learner_id": "shared-student", "payload": {"concept_id": "DS-LIN-01"}})
            assert issued.status_code == 200, issued.text
            item_id = issued.json()["result"]["question"]["item_id"]
            answered = client.post("/commands", json={"command_id": f"m2-c-answer-{index}", "type": "quiz.answer",
                "session_id": session_id, "learner_id": "shared-student",
                "payload": {"item_id": item_id, "answer": "correct-secret"}})
            assert answered.status_code == 200 and answered.json()["result"]["bkt_update"] == "updated"

        estimate_response = client.get(f"/sessions/{session_id}/learner?concept_id=DS-LIN-01")
        assert estimate_response.status_code == 200
        estimate = estimate_response.json()["estimates"][0]
        assert estimate["status"] == "available" and estimate["evidence_count"] == 3
        assert estimate["mastery"] >= 0.85 and estimate["uncertainty_kind"].startswith("binary_entropy_bits")

        shared = client.post("/commands", json={"command_id": "m2-c-shared", "type": "session.new",
            "learner_id": "shared-student", "payload": {"group": "C", "course_id": COURSE_ID}}).json()
        assert client.get(f"/sessions/{shared['session_id']}/learner?concept_id=DS-LIN-01").json()["estimates"][0]["evidence_count"] == 3

        turned = client.post("/commands", json={"command_id": "m2-c-auto-test", "type": "message.send",
            "session_id": session_id, "learner_id": "shared-student",
            "payload": {"content": "DS-LIN-01 线性表的定义，我准备重新检查一次。"}})
        assert turned.status_code == 200
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            events = service.history(session_id)
            if any(event["type"] == "agent.turn.completed" and event["trace_id"] == turned.json()["trace_id"] for event in events):
                break
            time.sleep(0.01)
        decision = [event for event in events if event["type"] == "pedagogy.decision"
                    and event["trace_id"] == turned.json()["trace_id"] and event["payload"].get("node") == "assess"]
        assert decision and decision[-1]["payload"]["action"] == "test"
        assert service.get_session(session_id).metadata["active_quiz"]["item_id"] == "q4"

        original_get = service.learner_store.get_estimate
        original_list = service.learner_store.list_estimates
        def forbidden(*args, **kwargs):
            raise AssertionError("A/B must not read BKT estimates")
        service.learner_store.get_estimate = forbidden
        service.learner_store.list_estimates = forbidden
        try:
            b_session = client.post("/commands", json={"command_id": "m2-b-session", "type": "session.new",
                "learner_id": "shared-student", "payload": {"group": "B", "course_id": COURSE_ID}}).json()["session_id"]
            assert client.get(f"/sessions/{b_session}/learner").json()["status"] == "group_disabled"
            issued = client.post("/commands", json={"command_id": "m2-b-issue", "type": "quiz.generate",
                "session_id": b_session, "learner_id": "shared-student", "payload": {}}).json()
            scored = client.post("/commands", json={"command_id": "m2-b-answer", "type": "quiz.answer",
                "session_id": b_session, "learner_id": "shared-student",
                "payload": {"item_id": issued["result"]["question"]["item_id"], "answer": "correct-secret"}})
            assert scored.status_code == 200 and scored.json()["result"]["bkt_update"] == "group_disabled"
        finally:
            service.learner_store.get_estimate = original_get
            service.learner_store.list_estimates = original_list


def test_attempt_identity_is_session_owned_and_manual_pending_is_not_wrong(tmp_path: Path, monkeypatch):
    home = tmp_path / "identity-home"
    (home / "course").mkdir(parents=True)
    _write_m2_bank(home)
    bank_path = home / "course" / "question_bank.json"
    bank = json.loads(bank_path.read_text(encoding="utf-8"))
    bank["questions"][0]["grading_review_status"] = "pending"
    bank["questions"][0]["grading"] = {"method": "manual"}
    bank_path.write_text(json.dumps(bank, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("DEEPPROF_HOME", str(home))
    app, service = _make_app(tmp_path)
    with TestClient(app) as client:
        session = client.post("/commands", json={"command_id": "identity-session", "type": "session.new",
            "learner_id": "owner", "payload": {"group": "C", "course_id": COURSE_ID}}).json()["session_id"]
        issued = client.post("/commands", json={"command_id": "identity-issue", "type": "quiz.generate",
            "session_id": session, "learner_id": "owner", "payload": {"concept_id": "DS-LIN-01"}}).json()
        item = issued["result"]["question"]["item_id"]
        rejected = client.post("/commands", json={"command_id": "identity-mismatch", "type": "quiz.answer",
            "session_id": session, "learner_id": "other", "payload": {"item_id": item, "answer": "correct-secret"}})
        assert rejected.status_code == 409 and rejected.json()["error"]["code"] == "learner_identity_mismatch"
        pending = client.post("/commands", json={"command_id": "manual-pending", "type": "quiz.answer",
            "session_id": session, "payload": {"item_id": item, "answer": "private-answer"}})
        assert pending.status_code == 200 and pending.json()["result"]["pending_review"] is True
        assert pending.json()["result"]["correct"] is None
        attempt = service.learner_store._conn.execute("SELECT is_correct,answer_value,skip_reason FROM attempts").fetchone()
        assert tuple(attempt) == (None, "", "grading_unreliable")
        state = service.get_session(session).metadata["teaching_state"]
        assert state["wrong_streak"] == 0 and state["last_answer_correct"] is None
        assert any(event["type"] == "quiz.review_pending" for event in service.history(session))


def _wait_turn(service, session_id: str, trace_id: str) -> list[dict]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        events = service.history(session_id)
        if any(event["type"] == "agent.turn.completed" and event["trace_id"] == trace_id for event in events):
            return events
        time.sleep(0.01)
    raise AssertionError("turn did not complete")


def test_chat_session_uses_normal_chat_without_teaching_or_library(tmp_path: Path):
    app, service = _make_app(tmp_path)
    with TestClient(app) as client:
        created = client.post("/commands", json={"command_id": "chat-mode-new", "type": "session.new",
            "payload": {"session_mode": "chat"}}).json()
        session_id = created["session_id"]
        summary = client.get(f"/sessions/{session_id}").json()
        assert summary["session_mode"] == "chat" and summary["experiment_group"] == ""

        accepted = client.post("/commands", json={"command_id": "chat-mode-message", "type": "message.send",
            "session_id": session_id, "payload": {"content": "你好", "requested_action": "auto"}}).json()
        events = _wait_turn(service, session_id, accepted["trace_id"])
        assert any(event["type"] == "conversation.turn.completed" for event in events)
        assert not any(event["type"] in {"teaching.decision", "pedagogy.decision"} for event in events)
        assert service.test_model_requests

        second = client.post("/commands", json={"command_id": "chat-mode-message-2", "type": "message.send",
            "session_id": session_id, "payload": {"content": "继续刚才的话题", "requested_action": "auto"}}).json()
        second_events = _wait_turn(service, session_id, second["trace_id"])
        requests = service.test_model_requests
        assert len(requests) == 2
        history = requests[1]["messages"]
        assert any(message.get("role") == "user" and message.get("content") == "你好" for message in history)
        assert any(message.get("role") == "assistant" and message.get("content") == LEAKING_MODEL_REPLY for message in history)
        assert any(event["type"] == "conversation.turn.completed" for event in second_events)
        assert not any(event["type"] in {"teaching.decision", "pedagogy.decision"}
                       for event in events + second_events)
        transcript = client.get(f"/sessions/{session_id}/messages").json()
        assert transcript[-1]["metadata"]["turn_mode"] == "chat"
        replay = client.get(f"/replay/sessions/{session_id}").json()
        assert replay["session"]["session_mode"] == "chat" and replay["session"]["experiment_group"] == ""


def test_chat_session_requires_a_study_session_for_explicit_study(tmp_path: Path):
    app, service = _make_app(tmp_path)
    with TestClient(app) as client:
        session_id = client.post("/commands", json={"command_id": "chat-study-new", "type": "session.new",
            "payload": {"session_mode": "chat"}}).json()["session_id"]
        response = client.post("/commands", json={"command_id": "chat-study-turn", "type": "message.send",
            "session_id": session_id, "payload": {"content": "解释线性表", "requested_action": "study"}})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "study_session_required"
        assert session_id not in client.app.state.active_turns


def test_study_smalltalk_routes_to_chat_and_preserves_teaching_state(tmp_path: Path):
    app, service = _make_app(tmp_path)
    with TestClient(app) as client:
        created = client.post("/commands", json={"command_id": "study-smalltalk-new", "type": "session.new",
            "payload": {"group": "B", "course_id": COURSE_ID}}).json()
        session_id = created["session_id"]
        session = service.get_session(session_id)
        session.metadata["teaching_state"] = {"concept_id": "DS-LIN-01", "hint_level": 2, "turn_count": 3}
        session.metadata["active_quiz"] = {"concept_id": "DS-LIN-01", "hint_count": 1, "item_id": "q1"}
        service.save_session(session)

        accepted = client.post("/commands", json={"command_id": "study-smalltalk-message", "type": "message.send",
            "session_id": session_id, "payload": {"content": "你好", "requested_action": "auto"}}).json()
        events = _wait_turn(service, session_id, accepted["trace_id"])
        completed = [event for event in events if event["type"] == "conversation.turn.completed"]
        assert completed and completed[-1]["payload"]["routing_reason"] == "smalltalk_rule"
        current = service.get_session(session_id)
        assert current.metadata["teaching_state"] == {"concept_id": "DS-LIN-01", "hint_level": 2, "turn_count": 3}
        assert current.metadata["active_quiz"]["hint_count"] == 1

        chat = client.post("/commands", json={"command_id": "study-explicit-chat", "type": "message.send",
            "session_id": session_id,
            "payload": {"content": "再闲聊一会儿", "requested_action": "chat"}}).json()
        chat_events = _wait_turn(service, session_id, chat["trace_id"])
        completed = [event for event in chat_events if event["type"] == "conversation.turn.completed"]
        assert completed and completed[-1]["payload"]["routing_reason"] == "explicit_chat"
        current = service.get_session(session_id)
        assert current.metadata["teaching_state"] == {"concept_id": "DS-LIN-01", "hint_level": 2, "turn_count": 3}
        assert current.metadata["active_quiz"]["hint_count"] == 1

        study = client.post("/commands", json={"command_id": "study-explicit-study", "type": "message.send",
            "session_id": session_id,
            "payload": {"content": "请继续解释线性表", "requested_action": "study"}}).json()
        study_events = _wait_turn(service, session_id, study["trace_id"])
        completed_study = [event for event in study_events if event["type"] == "teaching.turn.completed"]
        assert completed_study


def test_experiment_session_rejects_explicit_chat_before_reserving_turn(tmp_path: Path):
    app, service = _make_app(tmp_path)
    with TestClient(app) as client:
        session_id = client.post("/commands", json={"command_id": "locked-new", "type": "session.new",
            "payload": {"group": "B", "course_id": COURSE_ID, "experiment_run": True}}).json()["session_id"]
        response = client.post("/commands", json={"command_id": "locked-chat", "type": "message.send",
            "session_id": session_id, "payload": {"content": "你好", "requested_action": "chat"}})
        assert response.status_code == 409 and response.json()["error"]["code"] == "experiment_chat_disabled"
        assert session_id not in client.app.state.active_turns


def test_learner_endpoint_uses_session_frozen_bkt_parameters(tmp_path: Path):
    from models.learner.bkt import BKTParameters

    app, service = _make_app(tmp_path)
    custom = BKTParameters(p_l0=0.50, p_t=0.05, p_g=0.15, p_s=0.05,
                           source="fixture custom; not fitted", model_version="bkt-custom-fixture-v1")
    with TestClient(app) as client:
        session_id = client.post("/commands", json={"command_id": "frozen-bkt-new", "type": "session.new",
            "learner_id": "frozen-student", "payload": {"group": "C", "course_id": COURSE_ID}}).json()["session_id"]
        session = service.get_session(session_id)
        session.metadata["experiment"]["bkt"] = custom.to_dict()
        session.metadata["experiment"]["bkt_config_hash"] = custom.config_hash
        service.save_session(session)
        for index, correct in enumerate((True, False, True)):
            client.app.state.learner_store.record_attempt(
                attempt_id=f"frozen-{index}", learner_id="frozen-student", session_id=session_id,
                trace_id=f"trace-{index}", course_id=COURSE_ID, item_id=f"frozen-q-{index}",
                concept_id="DS-LIN-01", bank_version="frozen-bank-v1", correct=correct,
                hint_count=0, grading_source="exact_normalized_match", confidence=1.0,
                parameters=custom)
        response = client.get(f"/sessions/{session_id}/learner?concept_id=DS-LIN-01")
        assert response.status_code == 200
        payload = response.json()
        estimate = payload["estimates"][0]
        assert payload["model_version"] == custom.model_version
        assert payload["config_hash"] == custom.config_hash
        assert estimate["model_version"] == custom.model_version and estimate["evidence_count"] == 3
