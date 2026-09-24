"""Evaluate frozen A/B run events without copying prompts or model output."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "evaluation" / "dev_cases.json"


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path.name}")
    return value


def _valid_locator(ref: Any) -> bool:
    if not isinstance(ref, dict):
        return False
    return bool(ref.get("document_id") and ref.get("chunk_id") and ref.get("page") is not None)


def _safe_failure_reason(cell: dict[str, Any], events: list[dict[str, Any]]) -> str | None:
    """Map only fixed failure enums into the exported audit; never copy messages."""
    allowed = {
        "auth_failed", "region_or_permission_blocked", "rate_limited", "timeout",
        "connection_failed", "upstream_error", "not_found", "missing_credential",
        "capability_missing", "model_truncated", "empty_model_response",
    }
    for event in reversed(events):
        if event.get("type") not in {"agent.failed", "model.failed"}:
            continue
        payload = event.get("payload") or {}
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        details = error.get("details") if isinstance(error.get("details"), dict) else {}
        kind = details.get("kind") or error.get("kind")
        if isinstance(kind, str) and kind in allowed:
            return kind
        # Legacy agent.failed records may only have a fixed symbolic message.
        message = error.get("message")
        if isinstance(message, str) and message in allowed:
            return message
    failure = cell.get("failure") if isinstance(cell.get("failure"), dict) else {}
    fallback = failure.get("reason_code") or failure.get("code")
    if isinstance(fallback, str) and fallback in allowed | {"runtime_error", "turn_timeout"}:
        return "timeout" if fallback == "turn_timeout" else fallback
    return "runtime_error" if cell.get("status") == "failed" else None


def _evidence_check_failures(events: list[dict[str, Any]]) -> tuple[int, int]:
    """Count evidence decisions, treating explicit non-generative gaps as valid.

    A teaching decision that explicitly reports an evidence gap has no citation
    by design. Likewise, a pedagogy decision may safely ask or reflect when the
    evidence gate is closed. Those outcomes pass only when the same trace did
    not call the model and did not attach unsupported locators.
    """
    evidence_failures = 0
    evidence_checked = 0
    for event in events:
        kind = event.get("type")
        payload = event.get("payload") or {}
        trace_id = event.get("trace_id")
        refs = payload.get("evidence_refs") or payload.get("citations") or []
        reasons = payload.get("reason_codes") or []
        model_requested = any(
            item.get("trace_id") == trace_id
            and item.get("type") in {"model.requested", "model.completed"}
            for item in events
        )
        if kind == "pedagogy.decision":
            sufficient = payload.get("evidence_sufficient")
            if sufficient is False:
                evidence_checked += 1
                explicit_gap = "insufficient_evidence" in reasons
                safe_refusal = payload.get("action") in {"ask", "reflect", "evidence_gap"}
                if refs or model_requested or not (explicit_gap or safe_refusal):
                    evidence_failures += 1
            elif sufficient is True:
                evidence_checked += 1
                if not isinstance(refs, list) or not refs or not all(_valid_locator(ref) for ref in refs):
                    evidence_failures += 1
        elif kind == "teaching.decision":
            evidence_checked += 1
            explicit_gap = (
                payload.get("action") == "evidence_gap"
                and "insufficient_evidence" in reasons
            )
            if explicit_gap:
                if refs or model_requested:
                    evidence_failures += 1
            elif not isinstance(refs, list) or not refs or not all(_valid_locator(ref) for ref in refs):
                evidence_failures += 1
    return evidence_checked, evidence_failures


def evaluate_run(home: Path, run_id: str, cases_path: Path = DEFAULT_CASES) -> dict[str, Any]:
    run_dir = home / "experiments" / "runs" / run_id
    run_summary = _read_json(run_dir / "summary.json")
    run_config = _read_json(run_dir / "run.json")
    cases_doc = _read_json(cases_path)
    cases = {str(row["case_id"]): row for row in cases_doc.get("cases", [])}
    raw_path = Path(str(run_summary.get("run_file") or run_dir / "results.jsonl"))
    if not raw_path.is_absolute():
        raw_path = run_dir / raw_path
    cells = [json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    db_path = home / "sessions.sqlite"
    uri = db_path.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'").fetchone():
            raise ValueError("Developer event store is missing the events table.")
        cell_reports: list[dict[str, Any]] = []
        findings: list[dict[str, Any]] = []
        group_counts: dict[str, Counter[str]] = {"A": Counter(), "B": Counter()}
        configuration_by_case: dict[str, dict[str, Any]] = {}
        session_configuration_mismatches: list[str] = []
        session_ids: set[str] = set()

        for cell in cells:
            case_id = str(cell.get("case_id") or "")
            group = str(cell.get("group") or "")
            cell_status = str(cell.get("status") or "unknown")
            if group in group_counts:
                group_counts[group][cell_status] += 1
            session_id = str(cell.get("session_id") or "")
            session_ids.add(session_id)
            experiment = dict(cell.get("experiment") or {})
            session_row = connection.execute("SELECT payload FROM sessions WHERE session_id=?", (session_id,)).fetchone()
            try:
                session_payload = json.loads(session_row["payload"] or "{}") if session_row else {}
            except (json.JSONDecodeError, TypeError):
                session_payload = {}
            runtime_experiment = dict((session_payload.get("metadata") or {}).get("experiment") or {})
            config_key = {key: runtime_experiment.get(key) for key in (
                "course_id", "provider_profile", "model", "policy_version", "retrieval", "sampling", "question_bank_version",
            )}
            if case_id:
                configuration_by_case.setdefault(case_id, {})[group] = config_key
            expected_session_config = {
                "course_id": run_config.get("course_id"),
                "provider_profile": run_config.get("provider_profile"),
                "model": run_config.get("model"),
                "policy_version": run_config.get("policy_version"),
                "retrieval": run_config.get("retrieval"),
                "sampling": run_config.get("sampling"),
                "question_bank_version": run_config.get("question_bank_version"),
            }
            session_config_ok = all(
                config_key.get(key) is not None
                and (expected_session_config[key] is None
                     or config_key.get(key) == expected_session_config[key])
                for key in expected_session_config
            )
            if not session_config_ok:
                session_configuration_mismatches.append(f"{case_id}:{group}")

            trace_ids = {str(item) for item in cell.get("trace_ids", []) if item}
            events = [dict(row) for row in connection.execute(
                "SELECT trace_id,type,payload,sequence FROM events WHERE session_id=? ORDER BY sequence", (session_id,)
            ) if str(row["trace_id"] or "") in trace_ids]
            decoded = []
            for event in events:
                try:
                    payload = json.loads(event.get("payload") or "{}")
                except json.JSONDecodeError:
                    payload = {}
                decoded.append({**event, "payload": payload if isinstance(payload, dict) else {}})

            turn_traces = {str(turn.get("trace_id") or "") for turn in cell.get("turns", []) if turn.get("trace_id")}
            terminal_events = [event for event in decoded if event["type"] == "agent.turn.completed"]
            terminal_ok = all(event["payload"].get("status") == "ok" for event in terminal_events)
            trace_coverage_ok = turn_traces <= {str(event["trace_id"] or "") for event in terminal_events}
            # A call is an issued provider request, whether it completes or
            # returns a structured model failure. Counting only completed
            # responses loses failed turns from the measured denominator.
            calls_by_trace = Counter(event["trace_id"] for event in decoded if event["type"] == "model.requested")
            requested_models = [event["payload"] for event in decoded if event["type"] == "model.requested"]
            recorded_calls = sum(int(turn.get("model_calls") or 0) for turn in cell.get("turns", []))
            event_calls = sum(calls_by_trace.values())

            decisions = [event for event in decoded if event["type"] == "pedagogy.decision"]
            teaching_decisions = [event for event in decoded if event["type"] == "teaching.decision"]
            actions = {str(event["payload"].get("action") or "") for event in decisions + teaching_decisions}
            evidence_checked, evidence_failures = _evidence_check_failures(decoded)
            mechanical_leak_flags = 0
            attempt_counts: list[int] = []
            for event in decisions:
                payload = event["payload"]
                if payload.get("answer_leaked") is True:
                    mechanical_leak_flags += 1
                if isinstance(payload.get("attempt_count"), int):
                    attempt_counts.append(payload["attempt_count"])

            expected = cases.get(case_id, {}).get("expected_action_family", []) if group == "B" else []
            if "evidence_gap" in expected:
                action_match = any(
                    event["payload"].get("evidence_sufficient") is False
                    and "insufficient_evidence" in (event["payload"].get("reason_codes") or [])
                    for event in decisions
                )
            elif cases.get(case_id, {}).get("category") == "hint_then_success":
                action_match = set(str(item) for item in expected) <= actions
            else:
                action_match = not expected or bool(actions.intersection(str(item) for item in expected))

            cell_experiment = experiment
            frozen_model_ok = all(
                str(item.get("provider_profile") or "") == str(cell_experiment.get("provider_profile") or "")
                and str(item.get("model") or "") == str(cell_experiment.get("model") or "")
                for item in requested_models
            )
            persistent_learning_events = sum(
                event["type"] in {"memory.read", "memory.write"}
                or event["type"].startswith("bkt.")
                or event["type"].startswith("learner.profile.")
                for event in decoded
            )
            fresh_case_state = group != "B" or all(value == 0 for value in attempt_counts)

            checks = {
                "cell_completed": cell_status == "completed",
                "turn_terminals_ok": terminal_ok and trace_coverage_ok,
                "model_call_count_matches": recorded_calls == event_calls,
                "requested_provider_matches_session": frozen_model_ok and session_config_ok,
                "evidence_locators_or_gap": evidence_checked > 0 and evidence_failures == 0,
                "mechanical_answer_guard": mechanical_leak_flags == 0,
                "no_cross_case_learning_state": persistent_learning_events == 0 and fresh_case_state,
                "expected_action_family": action_match,
            }
            failed = [name for name, passed in checks.items() if not passed]
            if failed:
                findings.append({
                    "case_id": case_id, "group": group,
                    "trace_ids": sorted(trace_ids), "failed_checks": failed,
                    "reason": "One or more deterministic acceptance checks did not pass.",
                })
            safe_failure_reason = _safe_failure_reason(cell, decoded)
            cell_reports.append({
                "case_id": case_id, "group": group, "status": cell_status,
                "failure_reason_code": safe_failure_reason,
                "trace_count": len(trace_ids), "turn_count": len(cell.get("turns") or []),
                "model_calls_recorded": recorded_calls, "model_calls_in_events": event_calls,
                "persistent_learning_events": persistent_learning_events,
                "decision_count": len(decisions), "observed_actions": sorted(actions),
                "evidence_decisions_checked": evidence_checked,
                "evidence_locator_failures": evidence_failures,
                "attempt_count_values": sorted(set(attempt_counts)),
                "checks": checks,
            })

        duplicate_cells = len({(row.get("case_id"), row.get("group")) for row in cells}) != len(cells)
        expected_case_ids = set(cases)
        observed_by_case = {case_id: groups for case_id, groups in configuration_by_case.items()}
        missing_pairs = sorted(
            f"{case_id}:{group}" for case_id in expected_case_ids for group in ("A", "B")
            if not any(row.get("case_id") == case_id and row.get("group") == group for row in cells)
        )
        config_mismatches = [case_id for case_id, config in observed_by_case.items()
                             if "A" in config and "B" in config and config["A"] != config["B"]]
        config_values = {
            key: sorted({json.dumps((row.get("experiment") or {}).get(key), ensure_ascii=False, sort_keys=True)
                         for row in cells})
            for key in ("course_id", "provider_profile", "model", "policy_version", "retrieval", "sampling", "question_bank_version")
        }
        expected_profile = str(run_config.get("provider_profile") or run_summary.get("provider_profile") or "")
        expected_model = str(run_config.get("model") or run_summary.get("model") or "")
        expected_course_ids = {str((row.get("experiment") or {}).get("course_id") or "") for row in cells}
        requested_config_mismatches = [
            row["case_id"] for row in cell_reports if not row["checks"]["requested_provider_matches_session"]
        ]
        matrix_ok = len(cells) == 80 and not missing_pairs and not duplicate_cells
        config_ok = (
            all(len(values) <= 1 for values in config_values.values())
            and not config_mismatches and not requested_config_mismatches
            and not session_configuration_mismatches
            and len(expected_course_ids) == 1 and "" not in expected_course_ids
            and bool(expected_profile) and bool(expected_model)
            and str(run_config.get("policy_version") or "") not in {"", "unknown"}
            and isinstance(run_config.get("retrieval"), dict)
            and isinstance(run_config.get("sampling"), dict)
            and bool(run_config.get("git", {}).get("commit") not in {None, "unavailable"})
            and len(str(run_config.get("git", {}).get("working_tree_diff_sha256") or "")) == 64
        )
        session_isolation_ok = len(session_ids) == len(cells) and all(session_ids)
        if not matrix_ok:
            findings.append({"failed_checks": ["matrix_complete"], "missing_pairs": missing_pairs,
                             "duplicate_cells": duplicate_cells, "reason": "A/B matrix is incomplete or duplicated."})
        if not config_ok:
            findings.append({"failed_checks": ["frozen_configuration"], "case_ids": config_mismatches,
                             "requested_model_mismatches": requested_config_mismatches,
                             "session_configuration_mismatches": session_configuration_mismatches,
                             "reason": "A/B or run-wide provider, course, policy, retrieval, or sampling configuration is incomplete or differs."})
        if not session_isolation_ok:
            findings.append({"failed_checks": ["session_isolation"],
                             "reason": "Each case/group must use its own session."})

        passed_cells = sum(not any(not ok for ok in report["checks"].values()) for report in cell_reports)
        status = "passed" if matrix_ok and config_ok and session_isolation_ok and not findings else "needs_attention"
        return {
            "schema_version": "deepprof-acceptance-audit-v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id,
            "sample_type": run_summary.get("sample_type", "constructed_developer_fixture"),
            "provider_profile": run_summary.get("provider_profile"),
            "model": run_summary.get("model"),
            "status": status,
            "matrix": {"planned": 80, "observed": len(cells), "completed": sum(v.get("status") == "completed" for v in cells),
                       "missing_pairs": missing_pairs, "duplicate_cells": duplicate_cells,
                       "groups": {group: dict(sorted(counts.items())) for group, counts in group_counts.items()}},
            "frozen_configuration": {"passed": config_ok, "unique_values": config_values,
                                     "run_policy_version": run_config.get("policy_version"),
                                     "course_id_recovered_from_session_records": run_config.get("course_id") is None and len(expected_course_ids) == 1,
                                     "observed_course_ids": sorted(expected_course_ids),
                                     "run_retrieval": run_config.get("retrieval"),
                                     "run_sampling": run_config.get("sampling"),
                                     "case_mismatches": config_mismatches,
                                     "requested_model_mismatches": requested_config_mismatches,
                                     "session_configuration_mismatches": session_configuration_mismatches,
                                     "run_fingerprint_available": bool(
                                         run_config.get("git", {}).get("commit") not in {None, "unavailable"}
                                         and len(str(run_config.get("git", {}).get("working_tree_diff_sha256") or "")) == 64
                                     )},
            "session_isolation": {"passed": session_isolation_ok, "unique_sessions": len(session_ids),
                                  "observed_attempt_counts": sorted({n for row in cell_reports for n in row["attempt_count_values"]})},
            "automated_checks": {"cells_passed": passed_cells, "cells_failed": len(cell_reports) - passed_cells,
                                 "evidence_locator_or_gap_failures": sum(row["evidence_locator_failures"] for row in cell_reports),
                                 "mechanical_answer_guard_flags": sum(not row["checks"]["mechanical_answer_guard"] for row in cell_reports),
                                 "cross_case_learning_state_failures": sum(not row["checks"]["no_cross_case_learning_state"] for row in cell_reports),
                                 "requested_provider_mismatches": sum(not row["checks"]["requested_provider_matches_session"] for row in cell_reports),
                                 "expected_action_mismatches": sum(not row["checks"]["expected_action_family"] for row in cell_reports),
                                 "terminal_or_trace_failures": sum(not row["checks"]["turn_terminals_ok"] for row in cell_reports),
                                 "model_call_count_mismatches": sum(not row["checks"]["model_call_count_matches"] for row in cell_reports),
                                 "failure_reason_codes": dict(sorted(Counter(
                                     row["failure_reason_code"] for row in cell_reports if row["failure_reason_code"]
                                 ).items()))},
            "human_review": {"teacher_scoring": "pending_human_review",
                             "semantic_leakage_labels": "pending_human_review",
                             "citation_support_labels": "pending_human_review"},
            "findings": findings,
            "cells": cell_reports,
            "privacy": {"model_outputs_in_report": False, "student_inputs_in_report": False,
                        "source_text_in_report": False, "stored_in_source_data": "aggregate and trace metadata only"},
        }
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True, help="Local developer data directory")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate_run(args.home.expanduser().resolve(), args.run_id, args.cases.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "run_id": report["run_id"],
                      "cells_passed": report["automated_checks"]["cells_passed"],
                      "cells_failed": report["automated_checks"]["cells_failed"],
                      "output": str(args.output)}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
