from __future__ import annotations

import hashlib
import json

import pytest

from scripts import run_m3_abc_research as campaign
from scripts import run_m3_rag_repair as repair


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_new_repair_reserves_four_preflight_and_only_generation_conditions(tmp_path):
    raw_root = tmp_path / "m3-abc-ragfix-test-20260929-a1b2c3"
    assert repair._remaining_repair_reservation(raw_root) == 124

    preflight = raw_root / "runs" / f"{raw_root.name}-preflight"
    _write(preflight / "summary.json", {"observed_cells": 4})
    assert repair._remaining_repair_reservation(raw_root) == 120


def test_provider_budget_exhaustion_prevents_repair_before_gateway(tmp_path, monkeypatch):
    raw_root = tmp_path / "m3-abc-ragfix-test-20260929-a1b2c3"
    monkeypatch.setattr(campaign, "_provider_budget_state", lambda *_args, **_kwargs: {
        "provider_calls": 451, "unresolved_provider_request_reservation": 0,
        "preflight_runs": [],
    })
    with pytest.raises(RuntimeError, match="provider_request_budget_insufficient_for_repair"):
        repair._validate_new_batch_budget(raw_root, needed=124)


def test_saved_summary_without_matching_manifest_is_never_reused(tmp_path, monkeypatch):
    run_id = "m3-abc-ragfix-test-20260929-a1b2c3-preflight"
    run_root = tmp_path / "runs"
    _write(run_root / run_id / "summary.json", {"observed_cells": 4})
    monkeypatch.setattr(repair, "run_live", lambda **_kwargs: pytest.fail("must not send or regenerate"))
    with pytest.raises(RuntimeError, match="summary_without_manifest"):
        repair._run(run_id, repair.RAG_PRECHECK, 4, api_url="http://127.0.0.1:1",
                    run_root=run_root, parameters=campaign._selected_course_parameters(),
                    expected_index_sha256="a" * 64)


def test_pending_request_without_cell_artifact_is_not_replayed(tmp_path, monkeypatch):
    raw_root = tmp_path / "m3-abc-ragfix-test-20260929-a1b2c3"
    pending = raw_root / "runs" / "run-1" / "pending" / "cell-1.json"
    _write(pending, {"state": "terminal", "request_budget_reservation": 1})
    monkeypatch.setattr(campaign, "_provider_budget_state", lambda *_args, **_kwargs: {
        "provider_calls": 300, "unresolved_provider_request_reservation": 0,
        "preflight_runs": [],
    })
    with pytest.raises(RuntimeError, match="pending_cell_without_terminal_artifact"):
        repair._validate_new_batch_budget(raw_root, needed=0)


def test_preflight_keeps_truncation_as_failure(tmp_path):
    run_dir = tmp_path / "preflight"
    cases = repair.REPAIR_PREFLIGHT_CASES
    for index, case_id in enumerate(cases):
        truncated = case_id == "DSDEV-034"
        _write(run_dir / "cells" / f"cell-{case_id}.json", {
            "case_id": case_id, "group": "C", "application_status": "completed",
            "status": "failed" if truncated else "completed",
            "evaluation_status": "failed_truncated" if truncated else "completed",
            "model_calls": 1, "response_text": "partial" if truncated else "complete",
            "frozen_config": {"sampling": {"max_output_tokens": 8192}},
            "provider_diagnostics": {"outcomes": [{"status": "completed",
                "finish_reason": "length" if truncated else "stop"}]},
        })
    passed, reason = repair._preflight_passed(run_dir, {"observed_cells": 4})
    assert passed is False
    assert reason == "repair_preflight_cell_failed:DSDEV-034"


def test_config_hash_covers_every_frozen_repair_setting():
    frozen = {"schema_version": "v1", "output_tokens": 8192, "max_retries": 0}
    frozen["config_sha256"] = hashlib.sha256(json.dumps(frozen, ensure_ascii=False,
        sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    repair._validate_frozen_config(frozen)
    frozen["output_tokens"] = 4096
    with pytest.raises(RuntimeError, match="frozen_config_hash_mismatch"):
        repair._validate_frozen_config(frozen)
