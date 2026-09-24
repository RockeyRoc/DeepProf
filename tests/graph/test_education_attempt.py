from __future__ import annotations

import asyncio

from graph.education.bindings import ACTION_BINDINGS
from graph.education.builder import run_teaching_turn
from graph.education.nodes.test import test as quiz_node
from graph.education.policies import QUIZ_NOT_CONFIGURED_TEXT
from graph.education.state import new_state
from runtime.testing import FakeRuntime


def test_quiz_node_reports_missing_bank_without_generating_replacement():
    port = FakeRuntime(
        skill_results={"quiz": {"status": "ok", "item": {"stem": "must not be used"}}},
        action_bindings=ACTION_BINDINGS,
    )
    result = asyncio.run(quiz_node(new_state(current_concept="线性表"), port))
    assert result["response_text"] == QUIZ_NOT_CONFIGURED_TEXT
    calls = port.calls_of("invoke_skill")
    assert len(calls) == 1 and calls[0]["name"] == "quiz"
    assert port.calls_of("generate") == []
    assert "pedagogy.attempt" not in port.event_types()


def test_supplied_grading_fields_do_not_create_persistent_attempts():
    port = FakeRuntime(
        skill_results={"rag": {"status": "ok", "evidence": [{"document_id": "d1", "chunk_id": "c1", "page": 2, "text": "线性表证据"}]}},
        action_bindings=ACTION_BINDINGS,
    )
    result = asyncio.run(run_teaching_turn(port, {
        "session_id": "s1", "trace_id": "t1", "learner_id": "local",
        "course_id": "ds.c_language.v1", "current_concept": "线性表",
        "user_input": "我的答案是线性表", "attempt_count": 3,
        "item_id": "synthetic-only", "last_answer_correct": True,
    }))
    assert result["action"] == "test"
    assert result["response_text"] == QUIZ_NOT_CONFIGURED_TEXT
    assert "pedagogy.attempt" not in port.event_types()
    assert "memory.write" not in port.event_types()
