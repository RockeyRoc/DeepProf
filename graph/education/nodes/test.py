"""Disabled quiz node: report the missing question bank without inventing content."""

from __future__ import annotations

from typing import Any

from runtime.core.ports import RuntimePort

from ..contracts import PedagogicalDecision
from ..policies import ACTION_TEST, EMOTION_BY_ACTION
from ..state import PedagogyState
from . import dispatch, emit_decision, emit_entered, emit_exited

NODE = "test"


async def test(state: PedagogyState, port: RuntimePort) -> dict[str, Any]:
    concept = str(state.get("current_concept") or "")
    await emit_entered(port, state, NODE, concept=concept, configured=False)
    result = await dispatch(port, state, PedagogicalDecision(
        action=ACTION_TEST,
        concept=concept,
        require_student_reply=False,
        reason="从题库中选择已审核题目并按固定版本记录题目",
        params={"concept_id": str(state.get("current_concept_id") or ""), "difficulty": state.get("test_difficulty")},
        reason_codes=["reviewed_quiz_issue"],
    ))
    question = result.metadata.get("result_data") if isinstance(result.metadata.get("result_data"), dict) else {}
    await emit_decision(port, state, NODE, ACTION_TEST, "从固定题库选择已审核题目",
                        reason_codes=["reviewed_quiz_issue"] if question else ["question_bank_gap"], configured=bool(question),
                        capability=result.capability, capability_status=result.status)
    await emit_exited(port, state, NODE, response_text=result.content, citations=[], action=ACTION_TEST,
                      question_available=bool(question))
    return {"action": ACTION_TEST, "response_text": result.content, "quiz_question": question,
            "emotion": EMOTION_BY_ACTION[ACTION_TEST], "citations": [],
            "strategy_note": "bank:issued" if question else "bank:unavailable"}


__all__ = ["NODE", "test"]
