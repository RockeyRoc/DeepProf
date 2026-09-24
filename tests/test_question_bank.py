from __future__ import annotations

import pytest

from evaluation.question_bank import grade, inventory, public_question, verified_questions


def test_grading_requires_both_source_and_rubric_review():
    question = {
        "item_id": "q1",
        "source_review_status": "verified",
        "grading_review_status": "verified",
        "grading": {"method": "exact_normalized_match", "accepted_answers": ["A^k[i,j]"]},
    }
    assert grade(question, "Ａ^k[i,j]")["correct"] is True
    question["grading_review_status"] = "pending"
    assert grade(question, "A^k[i,j]") == {
        "correct": None, "grading_source": "manual_pending", "confidence": 0.0,
    }


def test_pending_reference_is_not_issued_or_counted_as_verified():
    bank = {"version": "v1", "questions": [
        {"item_id": "ready", "source_review_status": "verified", "prompt": "ready"},
        {"item_id": "pending", "source_review_status": "verified", "runtime_eligible": False, "prompt": "pending"},
    ]}
    assert [item["item_id"] for item in verified_questions(bank)] == ["ready"]


def test_public_question_drops_answer_key_page_reference():
    public = public_question({
        "item_id": "q1", "prompt": "Question",
        "source_ref": {"question_pdf_page": 20, "answer_key_pdf_page": 189, "printed_answer_page": 182},
    })
    assert public["source_ref"] == {"question_pdf_page": 20}


def test_inventory_only_counts_reviewed_exact_rules():
    bank = {"version": "v1", "teacher_approval": "pending", "questions": [
        {"item_id": "q1", "module": "tree", "source_review_status": "verified",
         "grading_review_status": "verified",
         "grading": {"method": "exact_normalized_match", "accepted_answers": ["1"]}},
        {"item_id": "q2", "module": "tree", "source_review_status": "verified",
         "grading_review_status": "pending",
         "grading": {"method": "exact_normalized_match", "accepted_answers": ["2"]}},
    ]}
    result = inventory(bank)
    assert result["automatically_graded_count"] == 1
    assert result["teacher_approval"] == "pending"
