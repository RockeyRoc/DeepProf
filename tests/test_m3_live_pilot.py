from __future__ import annotations

import json
import sqlite3

import pytest

from evaluation.dev_cases import CASE_VERSION
from scripts.run_m3_live_pilot import (PLANNED_CELLS, _copy_library_index, _retrieval_queries,
    _generation_judgment, _summary, live_cases, run, select_cases)
from runtime.storage.migrations import connect


def test_live_pilot_selects_twelve_cases_across_all_constructed_categories() -> None:
    source, cases = live_cases()

    assert source["version"] == CASE_VERSION
    assert len(cases) == 12
    assert len({case["case_id"] for case in cases}) == 12
    assert len({case["category"] for case in cases}) == 8
    assert all(case["m3_case_version"] == "ds-m3-live-pilot-v1" for case in cases)
    assert all(len(case["attempt_history"]) == 3 for case in cases)


def test_live_pilot_rejects_non_loopback_before_any_gateway_request() -> None:
    with pytest.raises(RuntimeError, match="loopback"):
        run(api_url="https://api.deepseek.com")


def test_group_retrieval_preflight_mirrors_production_queries() -> None:
    _, cases = live_cases()

    for case in cases:
        prompt = str(case["user_turns"][0])
        queries = _retrieval_queries(case)
        assert queries["A"] == prompt
        assert queries["B"] == queries["C"]
        assert queries["B"].startswith(prompt.strip())
        assert len(queries) == 3


def test_live_summary_marks_zero_denominators_as_not_computable() -> None:
    summary = _summary("run-test", {"provider_profile": "deepseek", "model": "deepseek-flash",
        "source_fingerprint": {}, "run_dir": "local-only"}, [])

    assert summary["planned_cells"] == PLANNED_CELLS
    assert summary["metrics"]["developer_expected_action_match"]["value"] is None
    assert summary["metrics"]["developer_expected_action_match"]["status"] == "not_computable_zero_denominator"
    assert summary["price"]["configured_in_deepprof"] is False
    assert summary["price"]["estimate_usd"] is None
    assert summary["price"]["estimate_status"] == "no_provider_inference"
    assert summary["provider_inference"]["status"].startswith("none;")


def test_generation_judgment_distinguishes_stop_truncation_and_no_call() -> None:
    truncated = [
        {"trace_id": "t", "type": "model.requested", "payload": {}},
        {"trace_id": "t", "type": "model.failed", "payload": {"error": {
            "code": "model_truncated", "details": {"finish_reason": "length"}}}},
    ]
    complete = [
        {"trace_id": "t", "type": "model.requested", "payload": {}},
        {"trace_id": "t", "type": "model.completed", "payload": {"finish_reason": "stop"}},
    ]
    assert _generation_judgment(truncated, "t") == "truncated"
    assert _generation_judgment(complete, "t") == "completed"
    assert _generation_judgment([], "t") == "not_applicable"


def test_live_summary_counts_truncation_as_failed_but_preserves_application_terminal() -> None:
    cells = [
        {"phase": "main", "case_id": "case-A", "group": "A", "status": "failed",
         "application_status": "completed", "evaluation_status": "truncated", "model_calls": 1,
         "usage": {}, "action": "ask", "action_family_match": True},
        {"phase": "main", "case_id": "case-B", "group": "B", "status": "completed",
         "application_status": "completed", "evaluation_status": "completed", "model_calls": 1,
         "usage": {}, "action": "teach", "action_family_match": True},
        {"phase": "main", "case_id": "case-C", "group": "C", "status": "completed",
         "application_status": "completed", "evaluation_status": "not_applicable", "model_calls": 0,
         "usage": {}, "action": "reflect", "action_family_match": True},
    ]
    for group in ("A", "B", "C"):
        for index in range(11):
            cells.append({"phase": "main", "case_id": f"extra-{group}-{index}", "group": group,
                "status": "completed", "application_status": "completed",
                "evaluation_status": "not_applicable", "model_calls": 0, "usage": {},
                "action": "reflect", "action_family_match": True})
    summary = _summary("judgment-test", {"source_fingerprint": {}, "run_dir": "local-only"}, cells)

    assert summary["status"] == "completed_with_failures"
    assert summary["completed_cells"] == 35
    assert summary["failed_cells"] == 1
    assert summary["application_terminal"] == {
        "completed": 36, "failed": 0,
        "description": "Runtime terminal state before Provider truncation is applied to evaluation status.",
    }
    assert summary["generation_judgment"]["cell_outcomes"]["truncated"] == 1
    assert summary["metrics"]["provider_generation_completion"]["numerator"] == 1
    assert summary["metrics"]["provider_generation_truncation"]["denominator"] == 2


def test_isolated_database_copies_only_library_index_tables(tmp_path) -> None:
    source = tmp_path / "source.sqlite"
    target = tmp_path / "isolated.sqlite"
    source_db = connect(source)
    source_db.execute("INSERT INTO library_resources "
        "(resource_id,document_id,course_id,type,title,tags,source_type,source_url,license,content_hash,status,"
        "owner_id,visibility,version_of,created_by,created_at,updated_at) "
        "VALUES ('res-1','doc-1','ds.c_language.v1','textbook','fixture','[]','local','fixture','',"
        "'hash','active','local','public',NULL,'test','2026-01-01','2026-01-01')")
    source_db.execute("INSERT INTO library_documents (document_id,resource_id,filename,media_type,page_count) "
                      "VALUES ('doc-1','res-1','fixture.md','text/markdown',1)")
    source_db.execute("INSERT INTO library_chunks "
        "(chunk_id,document_id,resource_id,page,printed_page,chapter,reliable,ordinal,section,text,vector,indexed_at) "
        "VALUES ('chunk-1','doc-1','res-1',1,NULL,'Fixture',1,1,'','fixture evidence','[]','2026-01-01')")
    source_db.execute("INSERT INTO sessions (session_id,learner_id,title,parent_id,payload,created_at,updated_at) "
                      "VALUES ('private-session','learner','private',NULL,'{}','2026-01-01','2026-01-01')")
    source_db.commit()
    source_db.close()
    target_db = connect(target)
    target_db.close()

    snapshot = _copy_library_index(source, target)

    assert snapshot["active_course_textbooks"] == 1
    copied = sqlite3.connect(target)
    try:
        assert copied.execute("SELECT COUNT(*) FROM library_chunks").fetchone()[0] == 1
        assert copied.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert copied.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 0
    finally:
        copied.close()
