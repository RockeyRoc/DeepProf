"""Export a privacy-filtered aggregate from a local M3 live-pilot run.

Raw prompts, retrieval text, answers, session identifiers, learner identifiers,
and event payload text remain in the local experiment directory. This exporter
copies only an explicit allowlist of counts and non-text metadata.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

GROUPS = ("A", "B", "C")
SAFE_CODE = re.compile(r"^[a-zA-Z0-9_.-]{1,64}$")
INPUT_PEAK_CACHE_HIT = 0.006
INPUT_PEAK_CACHE_MISS = 0.30
OUTPUT_PEAK = 1.20
INPUT_OFFPEAK_CACHE_HIT = INPUT_PEAK_CACHE_HIT / 2
INPUT_OFFPEAK_CACHE_MISS = INPUT_PEAK_CACHE_MISS / 2
OUTPUT_OFFPEAK = OUTPUT_PEAK / 2
PRICE_SOURCE = "https://api-docs.deepseek.com/quick_start/pricing/"


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_code(value: Any) -> str:
    text = str(value or "")
    return text if SAFE_CODE.fullmatch(text) else "redacted_code"


def _number(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else 0


def _event_usage(event: dict[str, Any]) -> dict[str, int]:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    raw = payload.get("usage")
    if not isinstance(raw, dict):
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        details = error.get("details") if isinstance(error.get("details"), dict) else {}
        raw = details.get("usage")
    if not isinstance(raw, dict):
        return {}
    return {
        "prompt_tokens": _number(raw.get("prompt_tokens")),
        "completion_tokens": _number(raw.get("completion_tokens")),
        "total_tokens": _number(raw.get("total_tokens")),
        "cache_hit_tokens": _number(raw.get("prompt_cache_hit_tokens")),
        "cache_miss_tokens": _number(raw.get("prompt_cache_miss_tokens")),
    }


def _event_finish_reason(payload: dict[str, Any]) -> str:
    """Read finish_reason from success or normalized provider-error payloads."""
    value = payload.get("finish_reason")
    if not value:
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        details = error.get("details") if isinstance(error.get("details"), dict) else {}
        value = details.get("finish_reason")
    return str(value or "")


def _evaluation_status(cell_status: str, requested: list[dict[str, Any]],
                       terminals: list[dict[str, Any]], finish_reasons: list[str]) -> str:
    """Judge output usability separately from the application turn terminal."""
    if "length" in finish_reasons:
        return "failed_truncated"
    if not requested:
        return "not_applicable" if cell_status == "completed" else "application_failed"
    if any(event.get("type") == "model.completed" for event in terminals):
        if "stop" in finish_reasons:
            return "completed"
        return "failed_incomplete"
    if any(event.get("type") == "model.failed" for event in terminals):
        return "failed_provider"
    return "unknown"


def _safe_cell(cell: dict[str, Any], events: list[dict[str, Any]], max_tokens: int) -> dict[str, Any]:
    requested = [event for event in events if event.get("type") == "model.requested"]
    terminals = [event for event in events if event.get("type") in {"model.completed", "model.failed"}]
    finish_reasons: list[str] = []
    model_error_codes: list[str] = []
    totals = {key: 0 for key in ("prompt_tokens", "completion_tokens", "total_tokens", "cache_hit_tokens", "cache_miss_tokens")}
    usage_events = 0
    for event in terminals:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        finish_reason = _event_finish_reason(payload)
        if finish_reason in {"stop", "length"}:
            finish_reasons.append(str(finish_reason))
        if event.get("type") == "model.failed":
            error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            model_error_codes.append(_safe_code(error.get("code") or error.get("kind")))
        usage = _event_usage(event)
        if usage:
            usage_events += 1
            for key, value in usage.items():
                totals[key] += value

    cell_status = str(cell.get("status") or "unknown")
    if not requested:
        outcome = "not_called"
    elif "length" in finish_reasons:
        outcome = "finish_reason_length"
    elif any(event.get("type") == "model.completed" for event in terminals):
        outcome = "completed"
    elif any(event.get("type") == "model.failed" for event in terminals):
        outcome = "provider_failed"
    else:
        outcome = "unknown"
    evaluation_status = _evaluation_status(cell_status, requested, terminals, finish_reasons)

    action = str(cell.get("action") or "")
    return {
        "case_id": str(cell.get("case_id") or ""),
        "group": str(cell.get("group") or ""),
        "terminal_status": cell_status,
        "evaluation_status": evaluation_status,
        "provider_requests": len(requested),
        "provider_outcome": outcome,
        "finish_reason": ";".join(sorted(set(finish_reasons))),
        "provider_error_codes": ";".join(sorted(set(model_error_codes))),
        "prompt_tokens": totals["prompt_tokens"],
        "completion_tokens": totals["completion_tokens"],
        "total_tokens": totals["total_tokens"],
        "cache_hit_tokens": totals["cache_hit_tokens"],
        "cache_miss_tokens": totals["cache_miss_tokens"],
        "usage_events": usage_events,
        "usage_missing": bool(requested) and usage_events == 0,
        "reported_completion_over_configured_limit": totals["completion_tokens"] > max_tokens,
        "action": action,
        "developer_fixture_action_match": bool(cell.get("action_family_match")),
        "locatable_references": _number(cell.get("locatable_references")),
        "reference_count": _number(cell.get("reference_count")),
        "synthetic_attempt_seeds": len(cell.get("bkt_observations") or []),
        "elapsed_ms": float(cell.get("elapsed_ms") or 0),
        "terminal_failure_code": _safe_code(cell.get("failure_reason")) if cell_status != "completed" else "",
        "evaluation_failure_code": (
            "model_truncated" if evaluation_status == "failed_truncated" else
            (_safe_code(model_error_codes[0]) if model_error_codes else "provider_failed") if evaluation_status == "failed_provider" else
            "incomplete_model_response" if evaluation_status == "failed_incomplete" else
            "application_failure" if evaluation_status == "application_failed" else ""
        ),
    }


def build_aggregate(run_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    run_dir = run_dir.resolve()
    manifest = _read(run_dir / "manifest.json")
    run_summary = _read(run_dir / "summary.json")
    max_tokens = _number((manifest.get("generation_limit") or {}).get("max_output_tokens"))
    cell_rows: list[dict[str, Any]] = []
    unique_sessions: set[str] = set()
    unique_learners: set[str] = set()
    for cell_path in sorted((run_dir / "cells").glob("*.json")):
        cell = _read(cell_path)
        # IDs are used locally only for an isolation check; they are never exported.
        if cell.get("session_id"):
            unique_sessions.add(str(cell["session_id"]))
        if cell.get("learner_id"):
            unique_learners.add(str(cell["learner_id"]))
        event_path = run_dir / "events" / f"{cell.get('cell_id')}.json"
        event_blob = _read(event_path) if event_path.is_file() else {"events": []}
        events = event_blob.get("events") if isinstance(event_blob.get("events"), list) else []
        cell_rows.append(_safe_cell(cell, events, max_tokens))

    groups: dict[str, dict[str, Any]] = {}
    for group in GROUPS:
        members = [row for row in cell_rows if row["group"] == group]
        groups[group] = {
            "planned_cells": int((manifest.get("call_budget") or {}).get("planned_cells") or 0) // len(GROUPS),
            "observed_cells": len(members),
            "terminal_completed": sum(row["terminal_status"] == "completed" for row in members),
            "terminal_failed": sum(row["terminal_status"] != "completed" for row in members),
            "evaluation_completed": sum(row["evaluation_status"] == "completed" for row in members),
            "evaluation_truncated_failed": sum(row["evaluation_status"] == "failed_truncated" for row in members),
            "evaluation_failed_other": sum(row["evaluation_status"].startswith("failed_") and row["evaluation_status"] != "failed_truncated" for row in members) +
                                       sum(row["evaluation_status"] == "application_failed" for row in members),
            "evaluation_application_failed": sum(row["evaluation_status"] == "application_failed" for row in members),
            "evaluation_not_applicable": sum(row["evaluation_status"] == "not_applicable" for row in members),
            "evaluation_unknown": sum(row["evaluation_status"] == "unknown" for row in members),
            "cells_without_provider_call": sum(row["provider_requests"] == 0 for row in members),
            "provider_requests": sum(row["provider_requests"] for row in members),
            "provider_finish_length": sum(row["provider_outcome"] == "finish_reason_length" for row in members),
            "provider_completed_stop": sum(row["provider_outcome"] == "completed" and row["finish_reason"] == "stop" for row in members),
            "provider_failed_events": sum(bool(row["provider_error_codes"]) for row in members),
            "prompt_tokens": sum(row["prompt_tokens"] for row in members),
            "completion_tokens": sum(row["completion_tokens"] for row in members),
            "cache_hit_tokens": sum(row["cache_hit_tokens"] for row in members),
            "cache_miss_tokens": sum(row["cache_miss_tokens"] for row in members),
            "locatable_references": sum(row["locatable_references"] for row in members),
            "references": sum(row["reference_count"] for row in members),
            "synthetic_attempt_seeds": sum(row["synthetic_attempt_seeds"] for row in members),
        }

    prompt_tokens = sum(row["prompt_tokens"] for row in cell_rows)
    completion_tokens = sum(row["completion_tokens"] for row in cell_rows)
    cache_hit_tokens = sum(row["cache_hit_tokens"] for row in cell_rows)
    cache_miss_tokens = sum(row["cache_miss_tokens"] for row in cell_rows)
    peak_cost = (cache_hit_tokens * INPUT_PEAK_CACHE_HIT
                 + cache_miss_tokens * INPUT_PEAK_CACHE_MISS
                 + completion_tokens * OUTPUT_PEAK) / 1_000_000
    offpeak_cost = (cache_hit_tokens * INPUT_OFFPEAK_CACHE_HIT
                    + cache_miss_tokens * INPUT_OFFPEAK_CACHE_MISS
                    + completion_tokens * OUTPUT_OFFPEAK) / 1_000_000
    requested = sum(row["provider_requests"] for row in cell_rows)
    finish_counts = Counter(
        reason for row in cell_rows for reason in row["finish_reason"].split(";") if reason
    )
    retrieval_config = manifest.get("retrieval") or {}
    fixed = retrieval_config.get("fixed_result_signatures") or {}
    retrieval_by_group: dict[str, dict[str, int]] = {}
    for group in GROUPS:
        results = [case.get(group) or {} for case in fixed.values()]
        retrieval_by_group[group] = {
            "cases_with_locatable_results": sum(_number(result.get("locatable_count")) > 0 for result in results),
            "cases_without_locatable_results": sum(_number(result.get("locatable_count")) == 0 for result in results),
            "result_references": sum(_number(result.get("result_count")) for result in results),
        }

    selected = manifest.get("selected_cases") or []
    policy = manifest.get("policy_version")
    question_bank = {
        "version": manifest.get("question_bank_version") or None,
        "sha256": manifest.get("question_bank_sha256"),
    }
    reported_max = max((row["completion_tokens"] for row in cell_rows), default=0)
    evaluation_outcomes = {status: sum(row["evaluation_status"] == status for row in cell_rows)
                           for status in ("completed", "failed_truncated", "failed_incomplete",
                                          "failed_provider", "application_failed", "not_applicable", "unknown")}
    known_failures = sum(evaluation_outcomes[key] for key in
                         ("failed_truncated", "failed_incomplete", "failed_provider", "application_failed"))
    evaluation_run_status = ("incomplete" if evaluation_outcomes["unknown"] else
                             "completed_with_failures" if known_failures else "completed")
    aggregate = {
        "schema_version": "deepprof-m3-live-pilot-aggregate-v2",
        "run_id": str(manifest.get("run_id") or ""),
        "status": str(run_summary.get("status") or "unknown"),
        "status_basis": "application runtime terminal summary; see evaluation_run_status for truncation-aware judgment",
        "evaluation_run_status": evaluation_run_status,
        "started_at": manifest.get("started_at"),
        "finished_at": manifest.get("finished_at"),
        "provider": {"profile_id": manifest.get("provider_profile"), "model": manifest.get("model")},
        "sample": {"case_count": int(manifest.get("planned_cells") or 0) // len(GROUPS),
                   "observed_cells": len(cell_rows), "groups": list(GROUPS),
                   "type": str(manifest.get("sample_type") or "constructed_developer_fixture"),
                   "human_subjects": bool(manifest.get("human_subjects", False)),
                   "selected_case_ids": [str(item.get("case_id") or "") for item in selected]},
        "limits": {"max_provider_requests": int((manifest.get("call_budget") or {}).get("hard_stop_before_cell_when_calls_reach") or 0),
                   "actual_provider_requests": requested,
                   "max_requests_per_cell": 1,
                   "one_turn_per_cell": bool((manifest.get("call_budget") or {}).get("one_turn_per_cell")),
                   "max_output_tokens_requested": max_tokens,
                   "max_retries": int((manifest.get("generation_limit") or {}).get("max_retries") or 0),
                   "provider_probe_performed": bool(manifest.get("provider_probe_performed"))},
        "groups": groups,
        "evaluation_judgment": {
            "cell_outcomes": evaluation_outcomes,
            "truncation_is_failure": True,
            "rule": "A Provider finish_reason=length is a failed_truncated cell even if the application terminal_status is completed.",
        },
        "provider_output": {"finish_reason_counts": dict(finish_counts),
                            "model_failed_events": sum(group["provider_failed_events"] for group in groups.values()),
                            "usage_missing_calls": sum(row["usage_missing"] for row in cell_rows),
                            "maximum_reported_completion_tokens_in_one_cell": reported_max,
                            "cells_where_reported_completion_exceeded_requested_limit": sum(row["reported_completion_over_configured_limit"] for row in cell_rows)},
        "token_usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                        "total_tokens": prompt_tokens + completion_tokens,
                        "prompt_cache_hit_tokens": cache_hit_tokens,
                        "prompt_cache_miss_tokens": cache_miss_tokens},
        "price_estimate": {"configured_in_product": False,
                           "peak_rate_usd": round(peak_cost, 8),
                           "offpeak_rate_usd": round(offpeak_cost, 8),
                           "basis": "provider-reported cache-hit/miss and completion tokens × official DeepSeek Flash rates; not a provider invoice",
                           "rates_usd_per_million": {"input_cache_hit_peak": INPUT_PEAK_CACHE_HIT,
                               "input_cache_miss_peak": INPUT_PEAK_CACHE_MISS,
                               "output_peak": OUTPUT_PEAK,
                               "input_cache_hit_offpeak": INPUT_OFFPEAK_CACHE_HIT,
                               "input_cache_miss_offpeak": INPUT_OFFPEAK_CACHE_MISS,
                               "output_offpeak": OUTPUT_OFFPEAK},
                           "source": PRICE_SOURCE},
        "metrics": {
            "application_terminal_completion": {"numerator": sum(row["terminal_status"] == "completed" for row in cell_rows),
                                    "denominator": len(cell_rows),
                                    "status": "application terminal state; not a generation-success measure"},
            "provider_generation_completed": {"numerator": sum(row["evaluation_status"] == "completed" for row in cell_rows),
                                     "denominator": requested,
                                     "status": "only model.completed with finish_reason=stop counts as completed" if requested else "not_computable_zero_denominator"},
            "provider_finish_stop": {"numerator": finish_counts.get("stop", 0),
                                     "denominator": requested,
                                     "status": "model.completed responses with finish_reason=stop" if requested else "not_computable_zero_denominator"},
            "provider_finish_length": {"numerator": finish_counts.get("length", 0),
                                       "denominator": requested,
                                       "status": "failed truncated generations; excluded from completion" if requested else "not_computable_zero_denominator"},
            "developer_fixture_action_match": {
                "numerator": sum(row["developer_fixture_action_match"] for row in cell_rows if row["terminal_status"] == "completed"),
                "denominator": sum(row["terminal_status"] == "completed" for row in cell_rows),
                "status": "constructed developer fixture only; not teacher-rated or a model quality estimate"},
            "locatable_reference_rate": {
                "numerator": sum(row["locatable_references"] for row in cell_rows),
                "denominator": sum(row["reference_count"] for row in cell_rows),
                "status": "locator completeness only; does not assess semantic support" if sum(row["reference_count"] for row in cell_rows) else "not_computable_zero_denominator"},
        },
        "retrieval_preflight": retrieval_by_group,
        "isolation": {"unique_sessions": len(unique_sessions), "unique_learners": len(unique_learners),
                      "group_C_synthetic_attempt_seeds": groups["C"]["synthetic_attempt_seeds"],
                      "group_A_synthetic_attempt_seeds": groups["A"]["synthetic_attempt_seeds"],
                      "group_B_synthetic_attempt_seeds": groups["B"]["synthetic_attempt_seeds"]},
        "frozen_configuration": {
            "course_id": manifest.get("course_id"),
            "case_version": manifest.get("case_version"),
            "source_case_version": manifest.get("source_case_version"),
            "source_case_sha256": manifest.get("source_case_sha256"),
            "selected_cases_sha256": manifest.get("selected_cases_sha256"),
            "prompt_version": manifest.get("prompt_version"),
            "prompt_sha256": manifest.get("prompt_sha256"),
            "policy_version": policy,
            "bkt_config_sha256": manifest.get("bkt_config_hash"),
            "question_bank": question_bank,
            "temperature": (manifest.get("sampling") or {}).get("temperature"),
            "retrieval_index_sha256": retrieval_config.get("index_sha256"),
            "retrieval_corpus_sha256": retrieval_config.get("corpus_dir_sha256"),
            "indexed_chunk_count": retrieval_config.get("indexed_chunk_count"),
            "active_course_textbook_count": retrieval_config.get("active_course_textbooks"),
            "source_fingerprint": manifest.get("source_fingerprint"),
        },
        "review_status": {"teacher_review": "pending", "human_ratings": "not_collected",
                          "student_learning_effect": "not_measured", "m3_research_acceptance": "not_complete"},
        "raw_data_policy": "responses and full event evidence remain only in the local .deepprof run directory; this repository export contains no prompt, retrieval text, response text, session ID, learner ID, trace ID, or credential",
    }
    return aggregate, cell_rows


def export_run(run_dir: Path, csv_path: Path, json_path: Path) -> dict[str, Any]:
    aggregate, rows = build_aggregate(run_dir)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(rows[0]) if rows else ["case_id", "group", "terminal_status", "evaluation_status", "provider_requests", "provider_outcome"]
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    json_path.write_text(json.dumps(aggregate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return aggregate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path, help="local .deepprof run directory")
    parser.add_argument("--csv", dest="csv_path", type=Path, required=True, help="aggregate cell CSV output")
    parser.add_argument("--json", dest="json_path", type=Path, required=True, help="aggregate summary JSON output")
    args = parser.parse_args()
    result = export_run(args.run_dir, args.csv_path, args.json_path)
    print(json.dumps({"run_id": result["run_id"], "observed_cells": result["sample"]["observed_cells"],
                      "provider_requests": result["limits"]["actual_provider_requests"],
                      "raw_text_exported": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
