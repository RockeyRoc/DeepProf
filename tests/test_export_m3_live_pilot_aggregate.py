import json
from pathlib import Path
import csv

from scripts.export_m3_live_pilot_aggregate import export_run


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_exporter_keeps_model_text_and_identifiers_local(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    marker = "PRIVATE_MODEL_RESPONSE_MUST_NOT_EXPORT"
    _write(run_dir / "manifest.json", {
        "run_id": "unit-run", "status": "completed_with_failures", "planned_cells": 3,
        "groups": ["A", "B", "C"], "sample_type": "constructed_developer_fixture",
        "human_subjects": False, "generation_limit": {"max_output_tokens": 512, "max_retries": 0},
        "call_budget": {"planned_cells": 3, "hard_stop_before_cell_when_calls_reach": 3,
                        "one_turn_per_cell": True},
        "provider_profile": "deepseek", "model": "deepseek-flash",
        "fixed_result_signatures": {}, "retrieval": {"fixed_result_signatures": {}},
        "sampling": {"temperature": 0.3}, "source_fingerprint": {"commit": "deadbeef"},
    })
    _write(run_dir / "summary.json", {"status": "completed_with_failures"})
    cell = {
        "cell_id": "main-case-A", "case_id": "CASE-001", "group": "A", "status": "failed",
        "session_id": "private-session-id", "learner_id": "private-learner-id",
        "trace_id": "private-trace-id", "response_text": marker, "action": "ask",
        "action_family_match": True, "failure_reason": "runtime_error", "model_calls": 1,
        "usage": {"prompt_tokens": 3, "completion_tokens": 513}, "elapsed_ms": 42,
        "locatable_references": 0, "reference_count": 0, "bkt_observations": [],
    }
    _write(run_dir / "cells" / "main-case-A.json", cell)
    _write(run_dir / "events" / "main-case-A.json", {"events": [
        {"type": "model.requested", "payload": {"model": "deepseek-flash"}},
        {"type": "model.failed", "payload": {"finish_reason": "length", "error": {
            "code": "model_truncated", "details": {"usage": {
                "prompt_tokens": 3, "completion_tokens": 513, "total_tokens": 516,
                "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 3}}}}},
        {"type": "model.stream.delta", "payload": {"text": marker}},
    ]})

    csv_path, json_path = tmp_path / "out.csv", tmp_path / "out.json"
    result = export_run(run_dir, csv_path, json_path)
    exported = csv_path.read_text(encoding="utf-8-sig") + json_path.read_text(encoding="utf-8")

    assert marker not in exported
    assert "private-session-id" not in exported
    assert "private-learner-id" not in exported
    assert "private-trace-id" not in exported
    assert result["limits"]["max_output_tokens_requested"] == 512
    assert result["provider_output"]["cells_where_reported_completion_exceeded_requested_limit"] == 1
    assert result["groups"]["A"]["provider_finish_length"] == 1
    assert result["groups"]["A"]["terminal_failed"] == 1
    assert result["groups"]["A"]["evaluation_truncated_failed"] == 1
    assert result["token_usage"]["completion_tokens"] == 513


def test_truncation_fails_evaluation_even_when_application_terminal_completed(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write(run_dir / "manifest.json", {
        "run_id": "judgment-run", "planned_cells": 3,
        "generation_limit": {"max_output_tokens": 512, "max_retries": 0},
        "call_budget": {"planned_cells": 3, "hard_stop_before_cell_when_calls_reach": 3},
        "retrieval": {},
    })
    _write(run_dir / "summary.json", {"status": "completed_with_failures"})
    fixtures = [
        ("case-truncated", "A", "completed", [{"type": "model.requested", "payload": {}},
            {"type": "model.failed", "payload": {"error": {"code": "model_truncated",
                "details": {"finish_reason": "length"}}}}]),
        ("case-stop", "B", "completed", [{"type": "model.requested", "payload": {}},
            {"type": "model.completed", "payload": {"finish_reason": "stop"}}]),
        ("case-policy", "C", "completed", []),
    ]
    for case_id, group, status, events in fixtures:
        cell_id = f"{case_id}-{group}"
        _write(run_dir / "cells" / f"{cell_id}.json", {
            "cell_id": cell_id, "case_id": case_id, "group": group, "status": status,
        })
        _write(run_dir / "events" / f"{cell_id}.json", {"events": events})

    csv_path, json_path = tmp_path / "out.csv", tmp_path / "out.json"
    result = export_run(run_dir, csv_path, json_path)
    with csv_path.open(encoding="utf-8", newline="") as stream:
        rows = {row["case_id"]: row for row in csv.DictReader(stream)}

    assert rows["case-truncated"]["terminal_status"] == "completed"
    assert rows["case-truncated"]["evaluation_status"] == "failed_truncated"
    assert rows["case-truncated"]["evaluation_failure_code"] == "model_truncated"
    assert rows["case-stop"]["evaluation_status"] == "completed"
    assert rows["case-policy"]["evaluation_status"] == "not_applicable"
    assert result["evaluation_judgment"]["cell_outcomes"] == {
        "completed": 1, "failed_truncated": 1, "failed_incomplete": 0,
        "failed_provider": 0, "application_failed": 0, "not_applicable": 1, "unknown": 0,
    }
    assert result["metrics"]["application_terminal_completion"]["numerator"] == 3
    assert result["metrics"]["provider_generation_completed"]["numerator"] == 1
