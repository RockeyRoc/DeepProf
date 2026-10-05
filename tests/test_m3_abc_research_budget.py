from __future__ import annotations

import json
import sys

import pytest

from scripts import run_m3_abc_research as research
from scripts.run_m3_abc_research import (
    GROUPS,
    PREFLIGHT_CASES,
    _campaign_environment,
    _incomplete_preflight_candidate,
    _main_integration_passed,
    _preflight_passed,
    _rag_integration_passed,
    _preflight_run_record,
    _provider_budget_state,
    _research_profile,
    _selected_course_parameters,
    _validate_campaign_resume_budget,
    _validate_campaign_budget,
    _write_json,
    _write_provider_budget_report,
)


def _summary(root, run_id: str, phase: str, status: str, calls: int,
             fingerprint: dict | None = None, finished_at: str = "") -> None:
    directory = root / run_id
    directory.mkdir(parents=True)
    (directory / "summary.json").write_text(json.dumps({
        "run_id": run_id, "status": status,
        "finished_at": finished_at,
        "sample": {"phase": phase}, "token_usage": {"model_calls": calls},
        "frozen_configuration": {"source_fingerprint": fingerprint or {}},
    }), encoding="utf-8")


def test_versioned_experiment_config_records_authorized_budget_amendment_and_route():
    config = research._experiment_config_payload()

    assert config["schema_version"] == "deepprof-m3-abc-experiment-config-v5"
    assert config["provider_network_mode"] == research.DEFAULT_PROVIDER_NETWORK_MODE
    assert config["cumulative_budget"] == {
        "historical_provider_requests": 24,
        "previously_authorized_new_requests": 328,
        "additional_provider_requests_authorized": 99,
        "authorized_new_requests": 427,
        "authorized_total_provider_requests": 451,
        "historical_preflight_rounds": 2,
        "previously_used_preflight_rounds": 2,
        "additional_preflight_rounds_authorized": 2,
        "maximum_preflight_rounds": 8,
        "preflight_rounds_before_post_vpn_batch": 5,
        "preflight_rounds_authorized_for_post_vpn_batch": 2,
        "authorization_amendments": [
            {"previous_total": 328, "additional_requests": 24, "new_total": 352,
             "purpose": "one preflight baseline and one bounded direct-network trial"},
            {"previous_total": 352, "additional_requests": 99, "new_total": 451,
                 "purpose": "post-VPN baseline and one route trial, fresh formal matrix, and conditional RAG ceiling"},
        ],
    }
    assert config["preflight"]["max_new_rounds"] == 1
    assert config["rag_ablation"]["condition_cells"] == 160
    assert config["rag_ablation"]["max_requests"] == 136
    assert config["post_vpn_batch"]["restart_authorized"] is True
    assert config["post_vpn_batch"]["failed_cells_may_be_repeated_in_new_campaign"] is True
    assert config["final_vpn_optimized_batch"]["max_new_rounds"] == 1
    assert config["final_vpn_optimized_batch"]["remaining_request_allocation"] == {
        "preflight": 12, "formal_ab": 120, "rag_ablation_ceiling": 136}
    assert config["final_vpn_optimized_batch"]["preflight_must_pass_before_formal"] is True
    assert config["final_vpn_optimized_batch"]["formal_matrix_must_pass_before_rag"] is True
    assert research._experiment_config_hash(provider_network_mode="direct") != research._experiment_config_hash(
        provider_network_mode="inherited")
    assert research.RAG_CONDITION_REQUEST_BUDGETS["retrieval-off_constraint-on"] == 0


def test_approved_preflight_is_resolved_from_the_cumulative_ledger(tmp_path):
    root = tmp_path / "m3-abc-research"
    _summary(root, "approved-preflight", "preflight", "completed", 12)

    record = _preflight_run_record(root, None, "approved-preflight")

    assert record is not None
    assert record["run_id"] == "approved-preflight"
    assert record["status"] == "completed"
    assert record["calls"] == 12
    assert _preflight_run_record(root, None, "unknown") is None


def test_provider_budget_counts_prior_calls_and_requires_a_fixed_second_preflight(tmp_path):
    root = tmp_path / "m3-abc-research"
    previous_fingerprint = {"commit": "before", "working_tree_diff_sha256": "failed-run"}
    _summary(root, "first-preflight", "preflight", "completed_with_failures", 12, previous_fingerprint)
    _summary(root, "other-run", "main", "completed", 4)

    state = _provider_budget_state(root)
    assert state["provider_calls"] == 16
    assert len(state["failed_preflight_runs"]) == 1
    with pytest.raises(RuntimeError, match="preflight_retry_requires_located_fix_description"):
        _validate_campaign_budget(root, current_fingerprint={"commit": "after"})
    with pytest.raises(RuntimeError, match="preflight_retry_requires_code_fingerprint_change"):
        _validate_campaign_budget(root, preflight_fix="Investigated provider connection failure.",
                                  current_fingerprint=previous_fingerprint)
    allowed = _validate_campaign_budget(root, preflight_fix="Fixed provider connection transport.",
                                        current_fingerprint={"commit": "after"})
    assert allowed["remaining_provider_requests"] == 435


def test_campaign_budget_allows_final_preflight_with_the_451_request_ceiling(tmp_path):
    retryable = tmp_path / "retryable"
    _summary(retryable, "first-preflight", "preflight", "completed_with_failures", 12,
             {"commit": "before"})
    allowed = _validate_campaign_budget(retryable, preflight_fix="Fixed the reported provider transport failure.",
                                        current_fingerprint={"commit": "after"})
    assert allowed["remaining_provider_requests"] == 439
    assert allowed["preflight_rounds_before_campaign"] == 1

    historical = tmp_path / "historical"
    _summary(historical, "preflight-1", "preflight", "completed_with_failures", 12,
             {"commit": "before-1"})
    _summary(historical, "preflight-2", "preflight", "completed", 12,
             {"commit": "before-2"})
    reusable = _validate_campaign_budget(historical, current_fingerprint={"commit": "after"})
    assert reusable["remaining_provider_requests"] == 427
    assert reusable["preflight_rounds_before_campaign"] == 2

    root = tmp_path / "m3-abc-research"
    for index in range(8):
        _summary(root, f"preflight-{index}", "preflight", "completed_with_failures", 12,
                 {"commit": f"v{index}"})
    with pytest.raises(RuntimeError, match="preflight_round_limit_reached"):
        _validate_campaign_budget(root)

    seven_rounds = tmp_path / "seven-rounds"
    for index in range(7):
        _summary(seven_rounds, f"preflight-{index}", "preflight", "completed_with_failures", 12,
                 {"commit": f"v{index}"})
    final_round = _validate_campaign_budget(seven_rounds, new_preflight_attempt=True,
        preflight_fix="User optimized the VPN path; use inherited route once with all generation settings frozen.",
        current_fingerprint={"commit": "vpn-updated-final-preflight"})
    assert final_round["preflight_rounds_before_campaign"] == 7
    assert final_round["remaining_provider_requests"] == 367
    assert final_round["required_campaign_reservation"] == 268

    full_budget = tmp_path / "full-budget"
    _summary(full_budget, "historical-run", "main", "completed", 352)
    with pytest.raises(RuntimeError, match="preflight_round_limit_reached|provider_request_budget_insufficient"):
        _validate_campaign_budget(full_budget)


def test_campaign_can_reuse_a_passed_preflight_after_four_rounds_and_preserve_two_new_slots(tmp_path):
    root = tmp_path / "research"
    _summary(root, "preflight-1", "preflight", "completed_with_failures", 12)
    _summary(root, "preflight-2", "preflight", "completed_with_failures", 12)
    _summary(root, "preflight-3", "preflight", "completed", 12)
    _summary(root, "preflight-4", "preflight", "completed", 12)

    campaign = _validate_campaign_budget(root)

    assert campaign["provider_calls_before_campaign"] == 48
    assert campaign["remaining_provider_requests"] == 403
    assert campaign["preflight_rounds_before_campaign"] == 4
    assert campaign["required_campaign_reservation"] == 256
    assert campaign["unresolved_provider_request_reservation"] == 0
    assert _validate_campaign_budget(root, new_preflight_attempt=True)[
        "preflight_rounds_before_campaign"] == 4


def test_post_vpn_amendment_reserves_new_preflight_formal_and_rag_with_cumulative_history(tmp_path):
    root = tmp_path / "research"
    for index in range(5):
        _summary(root, f"preflight-{index + 1}", "preflight", "completed", 12)
    _summary(root, "previous-formal", "main", "completed_with_failures", 99)

    authorized = _validate_campaign_budget(root, new_preflight_attempt=True)

    assert authorized["provider_calls_before_campaign"] == 159
    assert authorized["remaining_provider_requests"] == 292
    assert authorized["preflight_rounds_before_campaign"] == 5
    assert authorized["required_campaign_reservation"] == 256
    assert authorized["unresolved_provider_request_reservation"] == 0


def test_budget_guard_orders_latest_preflight_by_run_time_not_directory_name(tmp_path):
    root = tmp_path / "research"
    _summary(root, "z-older-failure", "preflight", "completed_with_failures", 12,
             finished_at="2026-09-28T06:17:45+00:00")
    _summary(root, "a-newer-success", "preflight", "completed", 12,
             finished_at="2026-09-28T11:51:33+00:00")

    allowed = _validate_campaign_budget(root, new_preflight_attempt=True)

    assert allowed["preflight_rounds_before_campaign"] == 2
    assert allowed["required_campaign_reservation"] == 256


def test_interrupted_eighth_preflight_can_resume_without_authorizing_a_ninth(tmp_path):
    root = tmp_path / "research"
    for index in range(8):
        _summary(root, f"preflight-{index}", "preflight", "completed_with_failures", 12,
                 {"commit": f"v{index}"})

    resumed = _validate_campaign_budget(root, resume_preflight=True)

    assert resumed["preflight_rounds_before_campaign"] == 8
    assert resumed["required_campaign_reservation"] == 256
    with pytest.raises(RuntimeError, match="preflight_round_limit_reached"):
        _validate_campaign_budget(root, new_preflight_attempt=True)


def test_post_vpn_failed_baseline_reserves_one_direct_trial_plus_formal_and_rag_ceiling(tmp_path):
    root = tmp_path / "research"
    for index in range(5):
        _summary(root, f"preflight-{index + 1}", "preflight", "completed", 12,
            finished_at=f"2026-09-28T12:00:{index:02d}+00:00")
    _summary(root, "post-vpn-baseline", "preflight", "completed_with_failures", 12,
        {"commit": "baseline"}, finished_at="2026-09-28T12:10:00+00:00")
    _summary(root, "previous-formal", "main", "completed_with_failures", 99)

    trial = _validate_campaign_budget(root, new_preflight_attempt=True,
        preflight_fix="Route trial removes inherited proxies after the baseline recorded connection failures.",
        current_fingerprint={"commit": "direct-route-trial"})

    assert trial["provider_calls_before_campaign"] == 171
    assert trial["remaining_provider_requests"] == 280
    assert trial["preflight_rounds_before_campaign"] == 6
    assert trial["required_campaign_reservation"] == 268


def test_incomplete_preflight_pointer_requires_confirmed_cleanup_and_matching_config(tmp_path, monkeypatch):
    raw_root = tmp_path / "raw"
    run_id = "m3-preflight-20260928T060732Z-8e3b88d3"
    run_dir = raw_root / run_id / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(json.dumps({
        "run_id": run_id, "phase": "preflight", "provider_profile": "deepseek",
        "model": "deepseek-flash",
    }), encoding="utf-8")
    pointer_path = research._preflight_pointer_path(raw_root)
    pointer = {"state": "interrupted", "run_id": run_id,
        "isolation_cleanup": "isolated_gateway_stopped",
        "experiment_config_sha256": "matching-config"}
    pointer_path.write_text(json.dumps(pointer), encoding="utf-8")
    monkeypatch.setattr(research, "_experiment_config_hash", lambda **_kwargs: "matching-config")

    assert _incomplete_preflight_candidate(raw_root) == pointer

    pointer["state"] = "running"
    pointer_path.write_text(json.dumps(pointer), encoding="utf-8")
    with pytest.raises(RuntimeError, match="preflight_resume_blocked_gateway_cleanup_unconfirmed"):
        _incomplete_preflight_candidate(raw_root)

    pointer["state"] = "interrupted"
    pointer["experiment_config_sha256"] = "changed-config"
    pointer_path.write_text(json.dumps(pointer), encoding="utf-8")
    with pytest.raises(RuntimeError, match="preflight_resume_config_mismatch"):
        _incomplete_preflight_candidate(raw_root)


def test_provider_budget_deduplicates_exports_and_counts_unexported_request_reservations(tmp_path):
    docs = tmp_path / "docs" / "m3-abc-research"
    raw = tmp_path / "raw" / "m3-abc-research"
    _summary(docs, "preflight", "preflight", "completed_with_failures", 12,
             {"commit": "before"})
    exported_run = raw / "campaign-one" / "runs" / "preflight"
    exported_run.mkdir(parents=True)
    (exported_run / "manifest.json").write_text(json.dumps({"run_id": "preflight", "phase": "preflight"}), encoding="utf-8")
    crashed_run = raw / "campaign-two" / "runs" / "interrupted"
    (crashed_run / "pending").mkdir(parents=True)
    (crashed_run / "manifest.json").write_text(json.dumps({"run_id": "interrupted", "phase": "main"}), encoding="utf-8")
    (crashed_run / "pending" / "main-case-A.json").write_text(
        json.dumps({"state": "turn_running", "request_budget_reservation": 1}), encoding="utf-8")

    state = _provider_budget_state(docs, raw)

    assert state["provider_calls"] == 13
    assert state["unresolved_provider_request_reservation"] == 1
    assert len(state["preflight_runs"]) == 1


def test_provider_budget_reads_legacy_actual_request_count(tmp_path):
    root = tmp_path / "legacy"
    directory = root / "legacy-preflight"
    directory.mkdir(parents=True)
    (directory / "summary.json").write_text(json.dumps({
        "run_id": "legacy-preflight", "status": "completed_with_failures",
        "sample": {"phase": "preflight"}, "limits": {"actual_provider_requests": 12},
    }), encoding="utf-8")

    state = _provider_budget_state(root)

    assert state["provider_calls"] == 12
    assert state["failed_preflight_runs"][0]["calls"] == 12


def test_provider_budget_ignores_pytest_fixture_summaries(tmp_path):
    research = tmp_path / "research"
    raw = tmp_path / "raw"
    _summary(research, "first-preflight", "preflight", "completed_with_failures", 12,
             {"commit": "before"})
    fixture = raw / "campaign" / "pytest-temp" / "test_exporter" / "run"
    fixture.mkdir(parents=True)
    (fixture / "summary.json").write_text(json.dumps({"fixture": True}), encoding="utf-8")
    incomplete = raw / "campaign" / "pytest-temp" / "test_budget" / "runs" / "preflight"
    incomplete.mkdir(parents=True)
    (incomplete / "manifest.json").write_text(json.dumps({
        "run_id": "preflight", "phase": "preflight", "status": "running",
    }), encoding="utf-8")

    state = _provider_budget_state(research, raw)

    assert state["provider_calls"] == 12
    assert len(state["failed_preflight_runs"]) == 1


def test_no_call_preflight_is_diagnostic_and_does_not_use_provider_preflight_round(tmp_path):
    root = tmp_path / "research"
    _summary(root, "paid-preflight", "preflight", "completed_with_failures", 12,
             {"commit": "before"})
    _summary(root, "blocked-before-provider", "preflight", "completed_with_failures", 0,
             {"commit": "after"})

    state = _provider_budget_state(root)

    assert state["provider_calls"] == 12
    assert [row["run_id"] for row in state["preflight_runs"]] == ["paid-preflight"]
    assert [row["run_id"] for row in state["failed_preflight_runs"]] == ["paid-preflight"]


def test_research_profile_freezes_verified_stream_and_disabled_thinking_capabilities():
    profile = {
        "profile_id": "deepseek",
        "default_model": "deepseek-flash",
        "capabilities": {},
        "model_capabilities": {"deepseek-flash": {"reasoning_mode": "unknown"}},
    }

    campaign_profile = _research_profile(profile)

    assert campaign_profile is not profile
    assert campaign_profile["capabilities"] == {"stream": True, "reasoning": True}
    assert campaign_profile["vendor_id"] == "deepseek"
    assert campaign_profile["model_capabilities"]["deepseek-flash"] == {
        "reasoning_mode": "toggle", "thinking_parameter": "thinking.type",
        "max_output_tokens": 4096}
    assert profile["model_capabilities"]["deepseek-flash"]["reasoning_mode"] == "unknown"
    assert profile["capabilities"] == {}
    with pytest.raises(RuntimeError, match="explicitly_disables_stream"):
        _research_profile({**profile, "capabilities": {"stream": False}})


def test_stopped_campaign_budget_report_keeps_unused_phases_and_remaining_total(tmp_path):
    report = tmp_path / "provider-request-budget.json"
    artifact = _write_provider_budget_report(report, {
        "provider_calls_before_campaign": 24,
        "remaining_provider_requests": 427,
        "preflight_rounds_before_campaign": 2,
    }, preflight_requests=12, status="stopped_after_preflight",
       stop_reason="preflight_generation_not_complete_with_nonempty_response")

    assert json.loads(report.read_text(encoding="utf-8")) == artifact
    assert artifact["authorized_total_provider_requests"] == 451
    assert artifact["provider_requests_after_campaign"] == 36
    assert artifact["remaining_after_campaign"] == 415
    assert artifact["preflight_rounds_used"] == 3
    assert artifact["formal_ab"] == 0
    assert artifact["rag_ablation"] == 0
    assert artifact["schema_version"] == "deepprof-provider-request-budget-v1"
    assert len(artifact["experiment_config_sha256"]) == 64


def test_campaign_cli_does_not_print_unsanitized_exception_body(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["run_m3_abc_research", "--preflight-only"])

    def fail(**_kwargs):
        raise RuntimeError("unfiltered-provider-response-must-not-escape")

    monkeypatch.setattr(research, "run_preflight_measurement", fail)

    assert research.main() == 1
    output = capsys.readouterr()
    assert "RuntimeError" in output.err
    assert "unfiltered-provider-response-must-not-escape" not in output.err


def test_experiment_json_writer_creates_parents_and_atomically_writes_unicode(tmp_path):
    target = tmp_path / "isolated" / "experiment-config.json"

    _write_json(target, {"model": "deepseek-flash", "status": "预检"})

    assert json.loads(target.read_text(encoding="utf-8")) == {
        "model": "deepseek-flash", "status": "预检"}
    assert not target.with_name("experiment-config.json.tmp").exists()


def test_preflight_gate_requires_one_complete_real_call_for_each_isolated_cell(tmp_path):
    run_dir = tmp_path / "run"
    (run_dir / "cells").mkdir(parents=True)
    (run_dir / "manifest.json").write_text(json.dumps({
        "phase": "preflight", "groups": list(GROUPS),
        "call_budget": {"authorized_provider_requests": 12},
    }), encoding="utf-8")
    for case in PREFLIGHT_CASES:
        for group in GROUPS:
            row = {"case_id": case, "group": group, "session_id": f"{case}-{group}",
                "model_calls": 1, "application_status": "completed", "status": "completed",
                "evaluation_status": "completed", "response_text": "完成的回答",
                "provider_diagnostics": {"request_count": 1,
                    "requests": [{"provider_profile": "deepseek", "model": "deepseek-flash"}],
                    "outcomes": [{"status": "completed", "finish_reason": "stop"}]}}
            (run_dir / "cells" / f"{case}-{group}.json").write_text(json.dumps(row), encoding="utf-8")
    summary = {"token_usage": {"model_calls": 12}}
    assert _preflight_passed(run_dir, summary) == (True, "preflight_passed")

    damaged = json.loads((run_dir / "cells" / f"{PREFLIGHT_CASES[0]}-A.json").read_text(encoding="utf-8"))
    damaged.update({"model_calls": 0, "response_text": "", "evaluation_status": "not_applicable",
        "provider_diagnostics": {"request_count": 0, "outcomes": []}})
    (run_dir / "cells" / f"{PREFLIGHT_CASES[0]}-A.json").write_text(json.dumps(damaged), encoding="utf-8")
    assert _preflight_passed(run_dir, summary) == (
        False, "preflight_requires_12_single_call_complete_generations")


def test_formal_gate_checks_cell_generation_session_and_bkt_contracts(tmp_path):
    run_dir = tmp_path / "formal"
    cells_dir = run_dir / "cells"
    cells_dir.mkdir(parents=True)
    parameters = _selected_course_parameters()
    (run_dir / "manifest.json").write_text(json.dumps({
        "groups": ["A", "B", "C"], "planned_cases": 40,
        "bkt_config_hash": parameters["config_hash"],
        "generation_limit": {"max_output_tokens": 4096, "max_retries": 0},
    }), encoding="utf-8")
    for index in range(40):
        case_id = f"case-{index:02d}"
        for group in GROUPS:
            row = {"case_id": case_id, "group": group, "session_id": f"session-{case_id}-{group}",
                "learner_id": f"learner-{case_id}-{group}", "application_status": "completed",
                "status": "completed", "evaluation_status": "completed", "model_calls": 1,
                "response_text": "完整回答", "bkt_observations": [],
                "learner_estimate": {"status": "group_disabled"} if group in {"A", "B"} else {
                    "status": "ok", "experiment_group": "C", "course_id": "ds.c_language.v1",
                    "config_hash": parameters["config_hash"], "estimates": [{
                        "status": "available", "course_id": "ds.c_language.v1", "model_type": "bkt",
                        "evidence_count": 3, "config_hash": parameters["config_hash"]}]},
                "frozen_config": {"provider_profile": "deepseek", "model": "deepseek-flash",
                    "bkt_config_hash": parameters["config_hash"],
                    "sampling": {"temperature": 0.3, "max_output_tokens": 4096,
                        "thinking_enabled": False, "require_explicit_thinking_mode": True}},
                "provider_diagnostics": {"request_count": 1,
                    "requests": [{"provider_profile": "deepseek", "model": "deepseek-flash"}],
                    "outcomes": [{"status": "completed", "finish_reason": "stop"}]}}
            if group == "C":
                row["bkt_observations"] = [{"eligible": True} for _ in range(3)]
            (cells_dir / f"main-{case_id}-{group}.json").write_text(json.dumps(row), encoding="utf-8")
    summary = {"observed_cells": 120, "token_usage": {"model_calls": 120}}
    assert _main_integration_passed(run_dir, summary, parameters) == (True, "formal_integration_passed")

    row_path = cells_dir / "main-case-00-C.json"
    row = json.loads(row_path.read_text(encoding="utf-8"))
    row["learner_estimate"]["estimates"][0]["config_hash"] = "mismatched"
    row_path.write_text(json.dumps(row), encoding="utf-8")
    assert _main_integration_passed(run_dir, summary, parameters) == (
        False, "formal_group_C_learner_estimate_config_hash_mismatch")

    row["learner_estimate"]["estimates"][0]["config_hash"] = parameters["config_hash"]
    row["learner_estimate"]["estimates"][0]["evidence_count"] = 2
    row_path.write_text(json.dumps(row), encoding="utf-8")
    assert _main_integration_passed(run_dir, summary, parameters) == (
        False, "formal_group_C_learner_estimate_evidence_count_mismatch")

    row_path = cells_dir / "main-case-00-A.json"
    row = json.loads(row_path.read_text(encoding="utf-8"))
    row["bkt_observations"] = [{"eligible": True}]
    row_path.write_text(json.dumps(row), encoding="utf-8")
    assert _main_integration_passed(run_dir, summary, parameters) == (
        False, "formal_group_ab_must_not_write_bkt")


def test_campaign_resume_reserves_only_rag_conditions_allowed_provider_calls(tmp_path, monkeypatch):
    campaign_id = "m3-abc-bkt-20260928T134542Z-a216df"
    raw_root = tmp_path / "raw"
    docs_root = tmp_path / "docs" / campaign_id
    preflight = raw_root / "preflight" / "runs" / "approved-preflight"
    preflight.mkdir(parents=True)
    (preflight / "summary.json").write_text(json.dumps({"run_id": "approved-preflight"}), encoding="utf-8")
    main = raw_root / campaign_id / "runs" / f"{campaign_id}-main"
    (main / "cells").mkdir(parents=True)
    (main / "summary.json").write_text(json.dumps({"run_id": f"{campaign_id}-main"}), encoding="utf-8")
    monkeypatch.setattr(research, "_provider_budget_state", lambda *_args, **_kwargs: {
        "provider_calls": 294, "unresolved_provider_request_reservation": 0,
        "preflight_runs": [{"run_id": "approved-preflight", "status": "completed", "calls": 12}],
    })
    monkeypatch.setattr(research, "_find_raw_run", lambda *_args, **_kwargs: preflight)
    monkeypatch.setattr(research, "_preflight_passed", lambda *_args, **_kwargs: (True, "preflight_passed"))
    monkeypatch.setattr(research, "_main_integration_passed", lambda *_args, **_kwargs: (True, "formal_integration_passed"))

    state = _validate_campaign_resume_budget(research.PROJECT / "docs" / "experiments" / "m3-abc-research",
        raw_root, campaign_id=campaign_id, docs_root=docs_root, preflight_run_id="approved-preflight")

    assert state["remaining_provider_requests"] == 157
    assert state["required_campaign_reservation"] == 120


def test_rag_gate_reports_truncation_separately_from_application_failure(tmp_path):
    run_dir = tmp_path / "rag"
    cells_dir = run_dir / "cells"
    cells_dir.mkdir(parents=True)
    parameters = _selected_course_parameters()
    options = {"retrieval_enabled": True, "evidence_constraint": True}
    (run_dir / "manifest.json").write_text(json.dumps({
        "groups": ["C"], "planned_cases": 40, "bkt_config_hash": parameters["config_hash"],
        "generation_limit": {"max_output_tokens": 4096, "max_retries": 0},
    }), encoding="utf-8")
    for index in range(40):
        truncated = index == 0
        case_id = f"case-{index:02d}"
        row = {"case_id": case_id, "group": "C", "session_id": f"session-{case_id}",
            "learner_id": f"learner-{case_id}", "application_status": "completed",
            "status": "failed" if truncated else "completed",
            "evaluation_status": "failed_truncated" if truncated else "completed",
            "model_calls": 1, "response_text": "partial" if truncated else "complete",
            "no_generation_reason": "", "bkt_observations": [{"eligible": True} for _ in range(3)],
            "learner_estimate": {"status": "ok", "experiment_group": "C",
                "config_hash": parameters["config_hash"], "estimates": [{
                    "status": "available", "course_id": "ds.c_language.v1", "model_type": "bkt",
                    "evidence_count": 3, "config_hash": parameters["config_hash"]}]},
            "frozen_config": {"m3_evidence_options": options,
                "bkt_config_hash": parameters["config_hash"],
                "sampling": {"max_output_tokens": 4096, "thinking_enabled": False,
                    "require_explicit_thinking_mode": True}},
            "provider_diagnostics": {"request_count": 1,
                "requests": [{"provider_profile": "deepseek", "model": "deepseek-flash"}],
                "outcomes": [{"status": "completed", "finish_reason": "length" if truncated else "stop"}]}}
        (cells_dir / f"rag-{case_id}-C.json").write_text(json.dumps(row), encoding="utf-8")
    summary = {"observed_cells": 40, "token_usage": {"model_calls": 40}}

    assert _rag_integration_passed(run_dir, summary, parameters, evidence_options=options) == (
        False, "rag_generation_not_complete:failed_truncated")


def test_direct_provider_mode_removes_only_proxy_variables_from_child_environment():
    parent = {
        "HTTP_PROXY": "http://127.0.0.1:9",
        "https_proxy": "http://127.0.0.1:9",
        "NO_PROXY": "localhost,127.0.0.1",
        "DEEPPROF_HOME": "isolated-home",
    }

    direct, removed = _campaign_environment(parent, direct_provider_network=True)
    inherited, inherited_removed = _campaign_environment(parent)

    assert "HTTP_PROXY" not in direct
    assert "https_proxy" not in direct
    assert direct["NO_PROXY"] == parent["NO_PROXY"]
    assert direct["DEEPPROF_HOME"] == parent["DEEPPROF_HOME"]
    assert removed == ["HTTP_PROXY", "https_proxy"]
    assert inherited == parent
    assert inherited_removed == []
    assert parent["HTTP_PROXY"] == "http://127.0.0.1:9"
