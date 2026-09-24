"""Versioned, local assessment material and deterministic grading helpers."""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

from config.paths import question_bank_file
from runtime.core.events import utc_now

BANK_VERSION = "ds-c-dev-v1"
AUTO_GRADE_METHOD = "exact_normalized_match"


class QuestionBankError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def load_bank(path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path) if path is not None else question_bank_file()
    if not target.is_file():
        raise QuestionBankError("question_bank_not_configured", "开发者题库尚未导入。请运行 deepprof course bank import。")
    try:
        bank = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QuestionBankError("question_bank_invalid", "题库文件无法读取；原文件未作修改。") from exc
    if not isinstance(bank, dict) or not isinstance(bank.get("questions"), list):
        raise QuestionBankError("question_bank_invalid", "题库结构无效：questions 必须为数组。")
    ids: set[str] = set()
    for row in bank["questions"]:
        if not isinstance(row, dict):
            raise QuestionBankError("question_bank_invalid", "题库中包含无效题目记录。")
        item_id = str(row.get("item_id") or "").strip()
        if not item_id or item_id in ids:
            raise QuestionBankError("question_bank_invalid", "题目 ID 缺失或重复。")
        ids.add(item_id)
    return bank


def verified_questions(bank: dict[str, Any], concept_id: str = "") -> list[dict[str, Any]]:
    result = [
        row for row in bank["questions"]
        if row.get("source_review_status") == "verified"
        and row.get("runtime_eligible", True) is not False
        and str(row.get("prompt") or "").strip()
        and (not concept_id or concept_id in (row.get("concept_ids") or []))
    ]
    return sorted(result, key=lambda row: str(row["item_id"]))


def public_question(question: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "item_id", "module", "concept_ids", "scored_concept_id", "question_type", "difficulty",
        "prompt", "choices", "answer_instruction", "source_ref",
    )
    result = {key: question[key] for key in keys if key in question}
    source = result.get("source_ref")
    if isinstance(source, dict):
        # Do not put answer-key locations in the issued-question payload.
        result["source_ref"] = {
            key: source[key]
            for key in ("source_title", "source_pdf_sha256", "question_pdf_page", "printed_question_page")
            if key in source
        }
    return result


def next_question(bank: dict[str, Any], *, concept_id: str = "", position: int = 0,
                  difficulty: int | None = None) -> dict[str, Any]:
    questions = verified_questions(bank, concept_id)
    if difficulty is not None:
        questions = [row for row in questions if int(row.get("difficulty") or 0) == int(difficulty)]
    if not questions:
        code = "question_difficulty_gap" if difficulty is not None else "question_review_required"
        note = (f"知识点 {concept_id} 没有已核对的难度 {difficulty} 题目。" if difficulty is not None
                else "没有已核对题目可供测验；OCR 候选项不会用于自动评分。")
        raise QuestionBankError(code, note)
    return questions[position % len(questions)]


def _normalize_answer(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = re.sub(r"\s+", "", text).strip().casefold()
    return text


def grade(question: dict[str, Any], answer: str) -> dict[str, Any]:
    rubric = question.get("grading") or {}
    if (question.get("source_review_status") != "verified"
            or question.get("grading_review_status") != "verified"
            or rubric.get("method") != AUTO_GRADE_METHOD):
        return {"correct": None, "grading_source": "manual_pending", "confidence": 0.0}
    accepted = rubric.get("accepted_answers")
    if not isinstance(accepted, list) or not accepted:
        return {"correct": None, "grading_source": "manual_pending", "confidence": 0.0}
    normalized = _normalize_answer(answer)
    correct = any(normalized == _normalize_answer(candidate) for candidate in accepted)
    return {"correct": correct, "grading_source": AUTO_GRADE_METHOD, "confidence": 1.0}


def record_attempt(connection: sqlite3.Connection, *, attempt_id: str, learner_id: str, session_id: str,
                   trace_id: str, item_id: str, concept_id: str, correct: bool, answer: str,
                   hint_count: int, grading_source: str, confidence: float, bank_version: str) -> dict[str, Any]:
    connection.execute(
        "INSERT OR IGNORE INTO attempts (attempt_id, learner_id, session_id, trace_id, item_id, "
        "scored_concept_id, is_correct, answer_value, hint_count, grading_source, confidence, bank_version, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (attempt_id, learner_id, session_id, trace_id, item_id, concept_id, int(correct), answer,
         hint_count, grading_source, confidence, bank_version, utc_now()),
    )
    connection.commit()
    row = connection.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
    return {key: row[key] for key in row.keys()} if row else {}


def inventory(bank: dict[str, Any]) -> dict[str, Any]:
    counts = Counter(str(row.get("module") or "unknown") for row in bank["questions"])
    statuses = Counter(str(row.get("source_review_status") or "unreviewed") for row in bank["questions"])
    return {
        "version": str(bank.get("version") or ""),
        "question_count": len(bank["questions"]),
        "verified_count": sum(row.get("source_review_status") == "verified" for row in bank["questions"]),
        "automatically_graded_count": sum(
            row.get("source_review_status") == "verified"
            and row.get("grading_review_status") == "verified"
            and (row.get("grading") or {}).get("method") == AUTO_GRADE_METHOD
            and bool((row.get("grading") or {}).get("accepted_answers"))
            for row in bank["questions"]
        ),
        "module_counts": dict(sorted(counts.items())),
        "review_counts": dict(sorted(statuses.items())),
        "teacher_approval": str(bank.get("teacher_approval") or "pending"),
        "provenance": dict(bank.get("source") or {}),
    }


__all__ = ["AUTO_GRADE_METHOD", "BANK_VERSION", "QuestionBankError", "grade", "inventory", "load_bank",
           "next_question", "public_question", "record_attempt", "verified_questions"]
