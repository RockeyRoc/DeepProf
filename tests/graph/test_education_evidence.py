from __future__ import annotations

import asyncio

from graph.education.bindings import ACTION_BINDINGS
from graph.education.builder import run_teaching_turn
from graph.education.contracts import POLICY_VERSION
from runtime.testing import FakeRuntime

BASE_STATE = {
    "session_id": "sess_1",
    "learner_id": "learner_1",
    "trace_id": "trace_1",
    "course_id": "ds.c_language.v1",
    "current_concept": "线性表",
    "learning_goal": "理解线性表",
}
EVIDENCE = {
    "document_id": "doc1",
    "chunk_id": "doc1:p21:c4",
    "page": 42,
    "printed_page": 21,
    "chapter": "线性表",
    "section": "线性表",
    "source": "local://course/data-structures-c-programming.pdf",
    "text": "线性表是具有相同特性数据元素的有限序列。",
}


def make_port(evidence: list[dict] | None = None) -> FakeRuntime:
    rag = {"status": "ok", "skill": "rag", "count": len(evidence or []), "evidence": evidence or []}
    return FakeRuntime(
        replies=["基于教材片段的讲解。"],
        skill_results={"rag": rag, "socratic": {"status": "ok", "question": "这个序列的元素之间有什么关系？"}},
        action_bindings=ACTION_BINDINGS,
    )


def run_turn(port: FakeRuntime, **overrides):
    return asyncio.run(run_teaching_turn(port, {**BASE_STATE, **overrides}))


def test_evidence_gap_stops_before_generation():
    port = make_port()
    result = run_turn(port, user_input="请讲讲线性表")
    assert result["action"] == "reflect"
    assert "足够且可定位" in result["response_text"]
    assert result["citations"] == []
    assert port.calls_of("generate") == []


def test_explicit_syllabus_gap_stops_generation_even_with_locatable_hit():
    port = make_port([dict(EVIDENCE)])
    result = run_turn(port, user_input="教材中未出现的新图算法接口是什么？")

    assert result["action"] == "reflect"
    assert result["citations"] == []
    assert result["retrieved_evidence_refs"] == []
    assert port.calls_of("generate") == []
    decision = next(event["payload"] for event in port.events
                    if event["type"] == "pedagogy.decision" and event["payload"]["node"] == "assess")
    assert decision["evidence_sufficient"] is False
    assert decision["evidence_refs"] == []


def test_teach_returns_only_reliable_locator_fields():
    port = make_port([dict(EVIDENCE)])
    result = run_turn(port, user_input="请讲讲线性表")
    assert result["action"] == "teach"
    assert result["response_text"] == "基于教材片段的讲解。"
    assert result["citations"] == [{key: value for key, value in EVIDENCE.items() if key != "text"}]
    assert "text" not in result["retrieved_evidence_refs"][0]
    decision = next(event["payload"] for event in port.events
                    if event["type"] == "pedagogy.decision" and event["payload"]["node"] == "assess")
    assert decision["policy_version"] == POLICY_VERSION
    assert decision["reason_codes"]
    assert decision["evidence_refs"][0]["printed_page"] == 21


def test_ask_does_not_receive_legacy_cross_question_context():
    port = make_port([dict(EVIDENCE)])
    secret = "legacy learner memory must not reach the prompt"
    result = run_turn(port, user_input="我觉得线性表里的顺序很重要", memory_note=secret)
    assert result["action"] == "ask"
    assert secret not in repr(port.calls)
    assert "memory_note" not in result
    assert all(event["type"] not in {"memory.read", "memory.write"} for event in port.events)


def test_socratic_output_guard_rejects_answers_and_multi_sentence_output():
    from skills.socratic import validate_socratic_question

    valid = "这个序列的元素之间有什么关系？"
    assert validate_socratic_question(valid) == valid
    assert validate_socratic_question("答案是线性表。你明白了吗？") == ""
    assert validate_socratic_question("先记住线性表的定义。你觉得它有什么特点？") == ""
    assert validate_socratic_question("请考虑线性表的顺序关系。") == ""
