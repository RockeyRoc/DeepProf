"""Shared reviewed-question issuance and reliable scoring service."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evaluation.question_bank import QuestionBankError, grade, load_bank, next_question, public_question
from models.learner.bkt import DEFAULT_PARAMETERS, parameters_from_snapshot
from models.learner.store import SqliteLearnerStore
from runtime.core.events import RuntimeEvent


COURSE_MANIFEST = Path(__file__).resolve().parents[1] / "data" / "courses" / "data_structures_c" / "manifest.json"


class QuizService:
    def __init__(self, runtime: Any, learner_store: SqliteLearnerStore) -> None:
        self.runtime = runtime
        self.learner_store = learner_store

    async def issue(self, session_id: str, *, trace_id: str, client_id: str | None = None,
                    surface: str | None = None, concept_id: str = "", difficulty: int | None = None) -> dict[str, Any]:
        session = self.runtime.get_session(session_id)
        experiment = dict(session.metadata.get("experiment") or {})
        bank = load_bank()
        frozen_version = str(experiment.get("question_bank_version") or "")
        version = str(bank.get("version") or "")
        if not frozen_version or frozen_version != version:
            raise QuestionBankError("question_bank_version_mismatch", "题库版本与会话快照不一致，请新建会话。")
        active = session.metadata.get("active_quiz")
        if isinstance(active, dict) and active.get("item_id"):
            if str(active.get("bank_version") or "") != version:
                raise QuestionBankError("question_bank_version_mismatch", "当前题目版本已变化，请新建会话。")
            question = next((row for row in bank["questions"] if row.get("item_id") == active["item_id"]), None)
            if question is None or question.get("source_review_status") != "verified" or question.get("runtime_eligible", True) is False:
                raise QuestionBankError("question_bank_version_mismatch", "当前题目已不可用，请新建会话。")
        else:
            concept_id = concept_id or str((session.metadata.get("teaching_state") or {}).get("concept_id") or "")
            index = int(session.metadata.get("quiz_position") or 0)
            question = next_question(bank, concept_id=concept_id, position=index, difficulty=difficulty)
            session.metadata["quiz_position"] = index + 1
            scored_concept = str(question.get("scored_concept_id") or "")
            session.metadata["active_quiz"] = {
                "item_id": str(question["item_id"]), "bank_version": version,
                "issued_at": trace_id, "hint_count": 0, "concept_id": scored_concept,
                "attempt_count": 0, "wrong_streak": 0,
            }
            session.metadata["teaching_state"] = {
                "concept_id": scored_concept, "hint_level": 0, "turn_count": 0,
                "attempt_count": 0, "wrong_streak": 0, "last_answer_correct": None, "misconceptions": [],
            }
            self.runtime.save_session(session)
        exposed = public_question(question)
        await self.runtime.emit(RuntimeEvent(type="quiz.issued", payload={
            "item_id": str(question["item_id"]), "concept_ids": list(question.get("concept_ids") or []),
            "difficulty": question.get("difficulty"), "question_type": str(question.get("question_type") or ""),
        }, session_id=session_id, trace_id=trace_id, source="gateway", client_id=client_id, surface=surface).to_dict())
        return {"status": "ok", "question": exposed, "result_data": exposed,
                "bank_version": version, "scoring": "manual" if
                (question.get("grading") or {}).get("method") != "exact_normalized_match" else "exact"}

    async def answer(self, session_id: str, *, attempt_id: str, trace_id: str, learner_id: str,
                     item_id: str, answer: str, client_id: str | None = None,
                     surface: str | None = None) -> dict[str, Any]:
        session = self.runtime.get_session(session_id)
        if learner_id and learner_id != session.learner_id:
            raise QuestionBankError("learner_identity_mismatch", "学习者身份与会话不一致。")
        experiment = dict(session.metadata.get("experiment") or {})
        group = str(experiment.get("group") or "B").upper()
        parameters = parameters_from_snapshot(experiment.get("bkt"))
        active = session.metadata.get("active_quiz")
        if not isinstance(active, dict) or not active.get("item_id"):
            raise QuestionBankError("quiz_not_active", "请先运行 /quiz。")
        if item_id != str(active.get("item_id")):
            raise QuestionBankError("quiz_item_mismatch", "提交的题目与当前会话题目不一致。")
        if not answer.strip() or len(answer) > 20_000:
            raise QuestionBankError("invalid_answer", "答案不能为空且不能超过 20000 个字符。")
        bank = load_bank()
        version = str(bank.get("version") or "")
        if not experiment.get("question_bank_version") or str(experiment["question_bank_version"]) != version \
                or str(active.get("bank_version") or "") != version:
            raise QuestionBankError("question_bank_version_mismatch", "题库版本与会话快照不一致，请新建会话。")
        question = next((row for row in bank["questions"] if str(row.get("item_id")) == item_id), None)
        if question is None:
            raise QuestionBankError("question_not_found", "题目不存在。")
        scored = grade(question, answer)
        concept_id = str(question.get("scored_concept_id") or "")
        concepts = [str(item) for item in question.get("concept_ids") or [] if str(item)]
        attempt = self.learner_store.record_attempt(
            attempt_id=attempt_id, learner_id=session.learner_id, session_id=session_id, trace_id=trace_id,
            course_id=str(experiment.get("course_id") or ""), item_id=item_id, concept_id=concept_id,
            bank_version=version, concept_ids=concepts, correct=scored["correct"],
            hint_count=int(active.get("hint_count") or 0),
            grading_source=str(scored["grading_source"]), confidence=float(scored["confidence"]),
            parameters=parameters, bkt_enabled=group == "C", concept_unambiguous=(len(concepts) == 1 and concepts[0] == concept_id),
        )
        attempt_count = int(active.get("attempt_count") or 0) + 1
        prior_wrong_streak = int(active.get("wrong_streak") or 0)
        wrong_streak = (0 if scored["correct"] is True else
                        prior_wrong_streak + 1 if scored["correct"] is False else prior_wrong_streak)
        teaching = dict(session.metadata.get("teaching_state") or {})
        teaching.update({"concept_id": concept_id, "attempt_count": attempt_count, "wrong_streak": wrong_streak,
                         "last_answer_correct": scored["correct"],
                         "misconceptions": ([] if scored["correct"] is True else
                                             ["incorrect_quiz_response"] if scored["correct"] is False else
                                             list(teaching.get("misconceptions") or []))})
        session.metadata["teaching_state"] = teaching
        active.update({"attempt_count": attempt_count, "wrong_streak": wrong_streak})
        session.metadata["active_quiz"] = None if scored["correct"] is True else active
        session.metadata["last_quiz_result"] = {"item_id": item_id, "correct": bool(scored["correct"]), "trace_id": trace_id}
        self.runtime.save_session(session)
        await self.flush_events()
        event_type = "quiz.scored" if isinstance(scored["correct"], bool) else "quiz.review_pending"
        await self.runtime.emit(RuntimeEvent(type=event_type, payload={
            "item_id": item_id, "scored_concept_id": concept_id, "correct": scored["correct"],
            "grading_source": str(scored["grading_source"]), "confidence": float(scored["confidence"]),
            "bkt_status": (attempt.get("learner_estimate") or {}).get("status") if group == "C" else "group_disabled",
        }, session_id=session_id, trace_id=trace_id, source="gateway", client_id=client_id, surface=surface).to_dict())
        return {"item_id": item_id, "correct": scored["correct"], "pending_review": scored["correct"] is None,
                "scored_concept_id": concept_id,
                "grading_source": str(scored["grading_source"]), "confidence": float(scored["confidence"]),
                "bank_version": version, "attempt": attempt,
                "learner_estimate": attempt.get("learner_estimate") if group == "C" else None,
                "bkt_update": "updated" if attempt.get("eligible") else str(attempt.get("skip_reason") or "group_disabled")}

    async def issue_from_tool(self, arguments: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        try:
            return await self.issue(str(ctx.get("session_id") or ""), trace_id=str(ctx.get("trace_id") or ""),
                                    client_id=ctx.get("client_id"), surface=ctx.get("surface"),
                                    concept_id=str(arguments.get("concept_id") or ""),
                                    difficulty=int(arguments["difficulty"]) if arguments.get("difficulty") is not None else None)
        except QuestionBankError as exc:
            return {"status": "error", "error": {"code": exc.code, "message": str(exc)}}

    async def flush_events(self) -> None:
        for row in self.learner_store.pending_events():
            await self.runtime.emit(RuntimeEvent(
                event_id=str(row["event_id"]), type=str(row["type"]), payload=dict(row["payload"]),
                session_id=str(row["session_id"]), trace_id=str(row["trace_id"]),
                source="learner", timestamp=str(row["timestamp"]),
            ).to_dict())
            self.learner_store.mark_event_delivered(str(row["event_id"]))


__all__ = ["QuizService"]
