"""Run a bounded 12-case M3 pilot against an explicitly configured live Provider.

This runner uses constructed developer fixtures, the real Gateway teaching path,
real retrieval and the real Attempt/BKT store for group C. It is not a human
study and its expected-action checks are not teacher annotations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.m3_acceptance import PROMPT_VERSION, _case_state, _derived_cases, _prompt_fingerprint
from evaluation.dev_cases import CASE_VERSION
from api.sessions import _concept_for_text, _concept_name
from graph.education.bindings import ACTION_BINDINGS
from graph.education.contracts import POLICY_VERSION
from models.learner.bkt import DEFAULT_PARAMETERS
from models.learner.store import SqliteLearnerStore
from runtime.core.session import Session
from runtime.storage.sqlite_store import SqliteSessionStore
from scripts.source_fingerprint import repository_fingerprint

SOURCE_CASES = ROOT / "evaluation" / "dev_cases.json"
LIVE_CASE_VERSION = "ds-m3-live-pilot-v1"
GROUPS = ("A", "B", "C")
PLANNED_CASES = 12
PLANNED_CELLS = PLANNED_CASES * len(GROUPS)
MAX_MODEL_CALLS = PLANNED_CELLS
INPUT_USD_PER_MILLION_PEAK = 0.30  # current official DeepSeek Flash cache-miss peak rate
OUTPUT_USD_PER_MILLION_PEAK = 1.20


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def request_json(base: str, path: str, *, method: str = "GET", payload: Any = None,
                 timeout: float = 30) -> Any:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(base.rstrip("/") + path, data=body, method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Never persist provider/error response bodies; they may include sensitive text.
        raise RuntimeError(f"http_{exc.code}") from None
    except (urllib.error.URLError, TimeoutError) as exc:
        reason = getattr(exc, "reason", None)
        kind = type(reason).__name__ if reason is not None else type(exc).__name__
        raise RuntimeError(f"gateway_transport_{kind}") from None


def select_cases(cases: list[dict[str, Any]], count: int = PLANNED_CASES) -> list[dict[str, Any]]:
    """Select a deterministic category-balanced sample in source ordering."""
    if count < 1 or count > len(cases):
        raise ValueError("invalid_case_count")
    by_category: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for case in cases:
        by_category.setdefault(str(case.get("category") or "uncategorized"), []).append(case)
    selected: list[dict[str, Any]] = []
    round_index = 0
    while len(selected) < count:
        added = False
        for rows in by_category.values():
            if round_index < len(rows):
                selected.append(rows[round_index])
                added = True
                if len(selected) == count:
                    break
        if not added:
            break
        round_index += 1
    if len(selected) != count:
        raise ValueError("case_sample_incomplete")
    return selected


def live_cases() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    source = json.loads(SOURCE_CASES.read_text(encoding="utf-8"))
    derived = _derived_cases(source)
    chosen = select_cases(derived)
    for case in chosen:
        case["m3_case_version"] = LIVE_CASE_VERSION
        case["attempt_history"] = [dict(item) for item in case.get("attempt_history") or []]
    return source, chosen


def _attempt_history(store: SqliteLearnerStore, case: dict[str, Any], *, run_id: str,
                     learner_id: str, session_id: str) -> list[dict[str, Any]]:
    observations = []
    for index, attempt in enumerate(case.get("attempt_history") or []):
        result = store.record_attempt(
            attempt_id=f"{run_id}-{case['case_id']}-{learner_id}-seed-{index + 1}",
            learner_id=learner_id, session_id=session_id,
            trace_id=f"seed-{run_id}-{case['case_id']}-{index + 1}",
            course_id="ds.c_language.v1", item_id=str(attempt["item_id"]),
            concept_id=str(case["concept_id"]), bank_version=LIVE_CASE_VERSION,
            correct=bool(attempt["correct"]), hint_count=0,
            grading_source="exact_normalized_match", confidence=1.0,
            parameters=DEFAULT_PARAMETERS, bkt_enabled=True, concept_unambiguous=True,
        )
        observations.append({"predicted_correct": result.get("predicted_correct"),
            "correct": result.get("correct"), "eligible": result.get("eligible"),
            "skip_reason": result.get("skip_reason"), "replayed": result.get("replayed", False)})
    return observations


def _safe_events(events: list[dict[str, Any]], trace_id: str) -> list[dict[str, Any]]:
    keep = {
        "agent.turn.started", "agent.turn.completed", "agent.failed", "model.requested",
        "model.completed", "model.failed", "pedagogy.decision", "teaching.decision",
        "teaching.turn.completed", "pedagogy.node.entered", "pedagogy.node.exited",
    }
    return [{"type": str(event.get("type") or ""), "sequence": event.get("sequence"),
             "trace_id": str(event.get("trace_id") or ""), "payload": dict(event.get("payload") or {})}
            for event in events if event.get("trace_id") == trace_id and event.get("type") in keep]


def _failure(events: list[dict[str, Any]], trace_id: str) -> str:
    safe = {"auth_failed", "region_or_permission_blocked", "rate_limited", "timeout",
            "connection_failed", "upstream_error", "not_found", "missing_credential",
            "capability_missing", "model_truncated", "empty_model_response"}
    for event in reversed(events):
        if event.get("trace_id") != trace_id or event.get("type") not in {"agent.failed", "model.failed"}:
            continue
        error = (event.get("payload") or {}).get("error") or {}
        details = error.get("details") or {}
        kind = str(details.get("kind") or error.get("kind") or "")
        return kind if kind in safe else str(error.get("code") or "provider_error")
    return "turn_failed"


def _usage(events: list[dict[str, Any]], trace_id: str) -> tuple[dict[str, int], int, list[str]]:
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    observed = False
    calls = 0
    actions: list[str] = []
    for event in events:
        if event.get("trace_id") != trace_id:
            continue
        kind = event.get("type")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if kind == "model.requested":
            calls += 1
        if kind in {"model.completed", "model.failed"}:
            raw = payload.get("usage") or ((payload.get("error") or {}).get("details") or {}).get("usage") or {}
            if isinstance(raw, dict):
                for key in totals:
                    value = raw.get(key)
                    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                        totals[key] += int(value)
                        observed = True
        if kind in {"pedagogy.decision", "teaching.decision"}:
            action = payload.get("action")
            if action:
                actions.append(str(action))
    return (totals if observed else {}), calls, actions


def _generation_judgment(events: list[dict[str, Any]], trace_id: str) -> str:
    """Classify Provider output independently of the app's turn terminal."""
    relevant = [event for event in events if event.get("trace_id") == trace_id]
    requested = any(event.get("type") == "model.requested" for event in relevant)
    if not requested:
        return "not_applicable"
    terminals = [event for event in relevant if event.get("type") in {"model.completed", "model.failed"}]
    reasons: set[str] = set()
    for event in terminals:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        reason = payload.get("finish_reason")
        if not reason:
            error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            details = error.get("details") if isinstance(error.get("details"), dict) else {}
            reason = details.get("finish_reason")
        if reason:
            reasons.add(str(reason))
    if "length" in reasons:
        return "truncated"
    if any(event.get("type") == "model.completed" for event in terminals):
        return "completed" if "stop" in reasons else "incomplete"
    if any(event.get("type") == "model.failed" for event in terminals):
        return "provider_failed"
    return "unknown"


def _locator_counts(events: list[dict[str, Any]], trace_id: str) -> tuple[int, int, str]:
    for event in reversed(events):
        if event.get("trace_id") == trace_id and event.get("type") in {"teaching.turn.completed", "teaching.decision"}:
            refs = (event.get("payload") or {}).get("evidence_refs") or []
            return sum(bool(item.get("document_id") and item.get("chunk_id") and item.get("page") is not None)
                       for item in refs if isinstance(item, dict)), len(refs), str((event.get("payload") or {}).get("action") or "")
    return 0, 0, ""


def _counts(cells: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [cell for cell in cells if cell.get("phase") == "main"]
    groups = {}
    for group in GROUPS:
        members = [row for row in rows if row.get("group") == group]
        groups[group] = {"planned": PLANNED_CASES,
            "completed": sum(row.get("status") == "completed" for row in members),
            "failed": sum(row.get("status") == "failed" for row in members),
            "blocked": sum(row.get("status") == "blocked" for row in members),
            "application_completed": sum(row.get("application_status", row.get("status")) == "completed" for row in members),
            "application_failed": sum(row.get("application_status", row.get("status")) != "completed" for row in members),
            "truncated_generations": sum(row.get("evaluation_status") == "truncated" for row in members),
            "model_calls": sum(int(row.get("model_calls") or 0) for row in members),
            "prompt_tokens": sum(int((row.get("usage") or {}).get("prompt_tokens") or 0) for row in members),
            "completion_tokens": sum(int((row.get("usage") or {}).get("completion_tokens") or 0) for row in members),
            "locatable_references": sum(int(row.get("locatable_references") or 0) for row in members),
            "references": sum(int(row.get("reference_count") or 0) for row in members)}
    return groups


def _summary(run_id: str, config: dict[str, Any], cells: list[dict[str, Any]]) -> dict[str, Any]:
    groups = _counts(cells)
    rows = [cell for cell in cells if cell.get("phase") == "main"]
    prompt_tokens = sum(row["prompt_tokens"] for row in groups.values())
    completion_tokens = sum(row["completion_tokens"] for row in groups.values())
    model_calls = sum(row["model_calls"] for row in groups.values())
    usage_missing_calls = sum(bool(row.get("usage_missing")) for row in rows)
    computed_cells = [row for row in rows if row.get("application_status", row.get("status")) == "completed"]
    matched = sum(bool(row.get("action_family_match")) for row in computed_cells)
    decisions = [row for row in rows if row.get("action")]
    total_observed = len(rows)
    estimated_cost = None
    cost_status = ("no_provider_inference" if model_calls == 0 else
                   "missing_usage" if usage_missing_calls else "reported_tokens_peak_rate_estimate")
    if model_calls and not usage_missing_calls:
        estimated_cost = round(prompt_tokens * INPUT_USD_PER_MILLION_PEAK / 1_000_000
                               + completion_tokens * OUTPUT_USD_PER_MILLION_PEAK / 1_000_000, 8)
    planned = PLANNED_CELLS
    evaluation_counts = {key: sum(row.get("evaluation_status") == key for row in rows)
                         for key in ("completed", "truncated", "provider_failed", "incomplete", "not_applicable", "unknown")}
    app_completed = sum(row.get("application_status", row.get("status")) == "completed" for row in rows)
    app_failed = total_observed - app_completed
    summary = {
        "schema_version": "deepprof-m3-live-pilot-v1", "run_id": run_id,
        "generated_at": _now(), "status": "completed" if total_observed == planned and not any(
            row.get("status") != "completed" for row in rows) else "completed_with_failures" if total_observed == planned else "incomplete",
        "provider_profile": config.get("provider_profile"), "model": config.get("model"),
        "sample_type": "constructed_developer_fixture", "human_subjects": False,
        "case_count": PLANNED_CASES, "planned_cells": planned, "observed_cells": total_observed,
        "completed_cells": sum(row.get("status") == "completed" for row in rows),
        "failed_cells": sum(row.get("status") == "failed" for row in rows),
        "application_terminal": {"completed": app_completed, "failed": app_failed,
            "description": "Runtime terminal state before Provider truncation is applied to evaluation status."},
        "generation_judgment": {"cell_outcomes": evaluation_counts, "truncation_is_failure": True,
            "description": "finish_reason=length is a failed generation even when the application turn completed."},
        "groups": groups,
        "metrics": {"developer_expected_action_match": {"numerator": matched, "denominator": len(computed_cells),
            "value": matched / len(computed_cells) if computed_cells else None,
            "status": "computed; developer fixture labels only" if computed_cells else "not_computable_zero_denominator"},
            "decision_completeness": {"numerator": len(decisions), "denominator": total_observed,
                "value": len(decisions) / total_observed if total_observed else None,
                "status": "computed" if total_observed else "not_computable_zero_denominator"},
            "locatable_reference_rate": {"numerator": sum(g["locatable_references"] for g in groups.values()),
                "denominator": sum(g["references"] for g in groups.values()),
                "value": (sum(g["locatable_references"] for g in groups.values()) /
                          sum(g["references"] for g in groups.values())) if sum(g["references"] for g in groups.values()) else None,
                "status": "computed" if sum(g["references"] for g in groups.values()) else "not_computable_zero_denominator"},
            "provider_generation_completion": {"numerator": evaluation_counts["completed"], "denominator": model_calls,
                "value": evaluation_counts["completed"] / model_calls if model_calls else None,
                "status": "model.completed with finish_reason=stop only" if model_calls else "not_computable_zero_denominator"},
            "provider_generation_truncation": {"numerator": evaluation_counts["truncated"], "denominator": model_calls,
                "value": evaluation_counts["truncated"] / model_calls if model_calls else None,
                "status": "failed truncated generations" if model_calls else "not_computable_zero_denominator"}},
        "token_usage": {"model_calls": model_calls, "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens, "missing_usage_cells": usage_missing_calls},
        "provider_inference": {"status": "observed" if model_calls else "none; generation was not reached",
            "reason": "see per-cell actions and event evidence"},
        "price": {"configured_in_deepprof": False, "estimate_usd": estimated_cost,
            "estimate_status": cost_status,
            "method": "reported tokens × current official DeepSeek Flash peak cache-miss rates; not provider invoice",
            "input_usd_per_million": INPUT_USD_PER_MILLION_PEAK,
            "output_usd_per_million": OUTPUT_USD_PER_MILLION_PEAK,
            "source": "https://api-docs.deepseek.com/quick_start/pricing/"},
        "bkt_prediction": {"status": "not_interpreted_as_model_validity",
            "synthetic_attempt_observations": sum(len(row.get("bkt_observations") or []) for row in rows if row.get("group") == "C")},
        "teacher_review": "pending", "human_ratings": "not_collected",
        "student_learning_effect": "not_measured", "source_fingerprint": config.get("source_fingerprint"),
        "raw_evidence_path": config.get("run_dir"),
        "failed_case_ids": sorted(row["case_id"] for row in rows if row.get("status") != "completed"),
    }
    return summary


def _cell(run_id: str, case: dict[str, Any], group: str, *, base: str, run_dir: Path,
          stores: tuple[SqliteSessionStore, SqliteLearnerStore], database: Path,
          current_calls: int) -> tuple[dict[str, Any], int]:
    sessions, learners = stores
    case_id = str(case["case_id"])
    cell_id = f"main-{case_id}-{group}"
    cell_path = run_dir / "cells" / f"{cell_id}.json"
    if cell_path.exists():
        prior = json.loads(cell_path.read_text(encoding="utf-8"))
        return prior, current_calls + int(prior.get("model_calls") or 0)

    learner_id = f"m3-live-{run_id}-{case_id}-{group}"
    pending_path = run_dir / "pending" / f"{cell_id}.json"
    pending = json.loads(pending_path.read_text(encoding="utf-8")) if pending_path.exists() else {}
    if pending.get("session_id"):
        session_id = str(pending["session_id"])
    else:
        command_id = f"{run_id}-{cell_id}-new"
        accepted = request_json(base, "/commands", method="POST", timeout=30, payload={
            "command_id": command_id, "client_id": "m3-live-pilot", "surface": "cli",
            "learner_id": learner_id, "type": "session.new", "payload": {
                "session_mode": "study", "group": group, "course_id": "ds.c_language.v1",
                "experiment_run": True, "title": f"M3 live {case_id} {group}"}})
        session_id = str(accepted.get("session_id") or "")
        if not session_id:
            raise RuntimeError("session_id_missing")
        pending = {"cell_id": cell_id, "case_id": case_id, "group": group,
                   "session_id": session_id, "learner_id": learner_id,
                   "created_at": _now(), "state": "session_created"}
        _write_json(pending_path, pending)

    session = sessions.load(session_id)
    if session is None:
        raise RuntimeError("session_not_found_in_isolated_database")
    state = _case_state(case)
    session.metadata.update(state)
    sessions.save(session)
    bkt_observations = _attempt_history(learners, case, run_id=run_id,
                                        learner_id=learner_id, session_id=session_id) if group == "C" else []
    if group == "C":
        learner_view = request_json(base, f"/sessions/{urllib.parse.quote(session_id, safe='')}/learner?concept_id={urllib.parse.quote(str(case['concept_id']), safe='')}")
        returned = (learner_view.get("estimates") or [{}])[0]
        if int(returned.get("evidence_count") or 0) != len(bkt_observations):
            raise RuntimeError("bkt_seed_verification_failed")
    else:
        learner_view = {"status": "group_disabled", "estimates": []}

    command_id = f"{run_id}-{cell_id}-turn"
    before_sequence = int(pending.get("before_sequence") or 0)
    trace_id = str(pending.get("trace_id") or "")
    if not trace_id:
        start_sequence_events = request_json(base, f"/replay/sessions/{urllib.parse.quote(session_id, safe='')}/events")
        before_sequence = max((int(event.get("sequence") or 0) for event in start_sequence_events), default=0)
        pending.update({"command_id": command_id, "before_sequence": before_sequence,
                        "state": "turn_pending", "turn_started_at": pending.get("turn_started_at") or _now()})
        _write_json(pending_path, pending)
        accepted = request_json(base, "/commands", method="POST", timeout=30, payload={
            "command_id": command_id, "client_id": "m3-live-pilot", "surface": "cli",
            "learner_id": learner_id, "session_id": session_id, "type": "message.send",
            "payload": {"content": str((case.get("user_turns") or [""])[0]), "requested_action": "auto"}})
        trace_id = str(accepted.get("trace_id") or "")
        if not trace_id:
            raise RuntimeError("turn_trace_id_missing")
        pending.update({"trace_id": trace_id, "state": "turn_running"})
        _write_json(pending_path, pending)

    deadline = time.monotonic() + 190
    turn_events: list[dict[str, Any]] = []
    terminal = None
    while time.monotonic() < deadline:
        all_events = request_json(base, f"/replay/sessions/{urllib.parse.quote(session_id, safe='')}/events?from_sequence={before_sequence + 1}")
        turn_events = [event for event in all_events if event.get("trace_id") == trace_id]
        terminal = next((event for event in reversed(turn_events) if event.get("type") == "agent.turn.completed"), None)
        if terminal:
            break
        time.sleep(0.5)
    elapsed_ms = round((time.monotonic() - (deadline - 190)) * 1000, 2)
    usage, model_calls, actions = _usage(turn_events, trace_id)
    if not terminal:
        status = "failed"
        failure = "turn_timeout"
        try:
            request_json(base, "/commands", method="POST", timeout=15, payload={
                "command_id": f"{run_id}-{cell_id}-timeout-cancel", "client_id": "m3-live-pilot",
                "surface": "cli", "learner_id": learner_id, "session_id": session_id,
                "type": "turn.cancel", "payload": {}})
        except Exception:
            pass
    else:
        status = "completed" if (terminal.get("payload") or {}).get("status") == "ok" else "failed"
        failure = "" if status == "completed" else _failure(turn_events, trace_id)
    application_status = status
    application_failure = failure
    evaluation_status = _generation_judgment(turn_events, trace_id)
    if evaluation_status in {"truncated", "provider_failed", "incomplete", "unknown"}:
        status = "failed"
        failure = {"truncated": "model_truncated", "provider_failed": "provider_error",
                   "incomplete": "incomplete_model_response", "unknown": "model_terminal_missing"}[evaluation_status]
    messages = request_json(base, f"/sessions/{urllib.parse.quote(session_id, safe='')}/messages?limit=1000")
    assistant_row = next((row for row in reversed(messages) if row.get("role") == "assistant"), {})
    assistant = str(assistant_row.get("content") or "")
    assistant_metadata = assistant_row.get("metadata") if isinstance(assistant_row.get("metadata"), dict) else {}
    refs = [item for item in assistant_metadata.get("evidence_refs", []) if isinstance(item, dict)]
    locator_count = sum(bool(item.get("document_id") and item.get("chunk_id") and item.get("page") is not None)
                        for item in refs)
    reference_count = len(refs)
    _, _, event_action = _locator_counts(turn_events, trace_id)
    action = str(assistant_metadata.get("action") or event_action or (actions[-1] if actions else ""))
    frozen = dict(session.metadata.get("experiment") or {})
    case_row = {
        "cell_id": cell_id, "phase": "main", "case_id": case_id, "case_version": LIVE_CASE_VERSION,
        "category": case.get("category"), "sample_type": "constructed_developer_fixture", "group": group,
        "session_id": session_id, "learner_id": learner_id, "trace_id": trace_id,
        "status": status, "application_status": application_status,
        "evaluation_status": evaluation_status, "elapsed_ms": elapsed_ms, "model_calls": model_calls,
        "usage": usage, "usage_missing": model_calls > 0 and not usage,
        "action": action, "developer_expected_action_family": list(case.get("allowed_variants") or []),
        "action_family_match": action in list(case.get("allowed_variants") or []),
        "locatable_references": locator_count, "reference_count": reference_count,
        "bkt_observations": bkt_observations, "learner_estimate": learner_view,
        "frozen_config": {"provider_profile": frozen.get("provider_profile"), "model": frozen.get("model"),
            "policy_version": frozen.get("policy_version"), "bkt_config_hash": frozen.get("bkt_config_hash"),
            "question_bank_version": frozen.get("question_bank_version"), "retrieval": frozen.get("retrieval"),
            "sampling": frozen.get("sampling"), "case_version": LIVE_CASE_VERSION,
            "prompt_version": PROMPT_VERSION, "prompt_sha256": _prompt_fingerprint(),
            "source_fingerprint": repository_fingerprint(ROOT)},
        "failure_reason": failure, "application_failure_reason": application_failure,
        "response_text": assistant,
    }
    _write_json(run_dir / "events" / f"{cell_id}.json", {"cell_id": cell_id, "events": _safe_events(turn_events, trace_id)})
    _write_json(cell_path, case_row)
    pending.update({"state": "terminal", "terminal_status": application_status,
                    "evaluation_status": evaluation_status, "cell_status": status, "finished_at": _now()})
    _write_json(pending_path, pending)
    return case_row, current_calls + model_calls


def run(*, api_url: str, run_id: str | None = None, resume: bool = False,
        output_root: Path | None = None, library_index_from: Path | None = None,
        supersedes_run_id: str | None = None) -> dict[str, Any]:
    base = api_url.rstrip("/")
    host = urllib.parse.urlsplit(base).hostname
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("live_pilot_requires_loopback_gateway")
    home = Path(os.environ.get("DEEPPROF_HOME", "~/.deepprof")).expanduser().resolve()
    database = Path(os.environ.get("DEEPPROF_SQLITE_PATH", str(home / "sessions.sqlite"))).expanduser().resolve()
    output = (output_root or home / "experiments" / "m3-live-pilot" / "runs").resolve()
    source, cases = live_cases()
    case_bytes = json.dumps(cases, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    health = request_json(base, "/health")
    selection = request_json(base, "/providers/default")
    profiles = request_json(base, "/providers")
    profile = next((row for row in profiles if row.get("profile_id") == selection.get("profile_id")), None)
    if not profile or profile.get("protocol") not in {"openai_compatible", "native"} or not profile.get("has_secret"):
        raise RuntimeError("provider_profile_or_secret_unavailable; no model request sent")
    if selection.get("profile_id") in {"mock", "fake", "offline"}:
        raise RuntimeError("live_pilot_rejects_fake_provider")
    if not database.is_file():
        raise RuntimeError("isolated_gateway_database_missing")
    run_id = run_id or "m3-live-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    run_dir = output / run_id
    manifest_path = run_dir / "manifest.json"
    if manifest_path.exists() and not resume:
        raise FileExistsError("live_pilot_run_exists; pass --resume only for the exact frozen configuration")
    if resume and not manifest_path.exists():
        raise FileNotFoundError("live_pilot_resume_run_not_found")
    run_dir.mkdir(parents=True, exist_ok=True)
    if manifest_path.exists() and library_index_from:
        raise ValueError("resume_does_not_copy_library_index")
    library_snapshot = (_copy_library_index(library_index_from, database) if library_index_from
                        else _library_index_snapshot(database))
    if not library_snapshot["active_course_textbooks"]:
        raise RuntimeError("no_active_course_textbook_index; no model request sent")
    frozen_retrieval = _freeze_retrieval_results(base, cases)
    bkt_source = DEFAULT_PARAMETERS.to_dict()
    corpus_hash = _corpus_fingerprint(home / "library")
    config = {
        "schema_version": "deepprof-m3-live-pilot-config-v1", "run_id": run_id,
        "started_at": _now(), "status": "running", "provider_profile": selection.get("profile_id"),
        "model": selection.get("model"), "fake_provider_used": False, "paid_model_used": True,
        "provider_probe_performed": False, "provider_has_secret": True,
        "case_version": LIVE_CASE_VERSION, "source_case_version": source.get("version", CASE_VERSION),
        "source_case_sha256": _sha256(SOURCE_CASES), "selected_cases_sha256": hashlib.sha256(case_bytes).hexdigest(),
        "selected_cases": [{"case_id": case["case_id"], "category": case.get("category")} for case in cases],
        "selection_method": "deterministic category round-robin; first user turn per case",
        "sample_type": "constructed_developer_fixture", "human_subjects": False,
        "groups": list(GROUPS), "planned_cells": PLANNED_CELLS,
        "supersedes_run_id": supersedes_run_id,
        "course_id": "ds.c_language.v1", "question_bank_version": "", "question_bank_sha256": None,
        "policy_version": POLICY_VERSION, "bkt_parameters": bkt_source,
        "bkt_config_hash": DEFAULT_PARAMETERS.config_hash,
        "strategy_bindings_sha256": hashlib.sha256(json.dumps(ACTION_BINDINGS, ensure_ascii=False,
            sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
        "prompt_version": PROMPT_VERSION, "prompt_sha256": _prompt_fingerprint(),
        "sampling": {"temperature": 0.3}, "generation_limit": {"max_output_tokens": int(os.environ.get("DEEPPROF_LLM_MAX_TOKENS", "4096")),
            "max_retries": int(os.environ.get("DEEPPROF_LLM_MAX_RETRIES", "2"))},
        "retrieval": {"top_k": 5, "chunk_size": 800, "chunk_overlap": 120,
            "corpus_dir_sha256": corpus_hash, "uses_local_retrieval": True,
            "index_sha256": library_snapshot["sha256"],
            "indexed_resource_count": library_snapshot["resource_count"],
            "indexed_chunk_count": library_snapshot["chunk_count"],
            "active_course_textbooks": library_snapshot["active_course_textbooks"],
            "fixed_result_signatures": frozen_retrieval},
        "call_budget": {"planned_cells": PLANNED_CELLS, "hard_stop_before_cell_when_calls_reach": MAX_MODEL_CALLS,
            "one_turn_per_cell": True, "stop_run_if_any_cell_uses_multiple_model_calls": True},
        "price_configured": False, "reported_token_cost_estimate_only": True,
        "environment": {"python": sys.version.split()[0], "os": sys.platform},
        "source_fingerprint": repository_fingerprint(ROOT), "database_path": str(database),
        "run_dir": str(run_dir), "raw_response_policy": "local_only; aggregate repository output redacted",
    }
    bank_path = home / "course" / "question_bank.json"
    if bank_path.is_file():
        bank = json.loads(bank_path.read_text(encoding="utf-8"))
        config["question_bank_version"] = str(bank.get("version") or "")
        config["question_bank_sha256"] = _sha256(bank_path)
    if manifest_path.exists():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        keys = set(config) - {"started_at", "status"}
        changed = sorted(key for key in keys if existing.get(key) != config.get(key))
        if changed:
            raise ValueError("resume_config_mismatch:" + ",".join(changed))
        config = existing
    else:
        _write_json(run_dir / "cases.json", {"version": LIVE_CASE_VERSION,
            "source_version": source.get("version"), "sample_type": "constructed_developer_fixture",
            "human_subjects": False, "cases": cases})
        _write_json(manifest_path, config)

    cells_dir = run_dir / "cells"
    events_dir = run_dir / "events"
    for directory in (cells_dir, events_dir, run_dir / "pending"):
        directory.mkdir(parents=True, exist_ok=True)
    # Freeze local retrieval content before first provider request.
    if not resume:
        _write_json(manifest_path, config)
    elif config.get("retrieval", {}).get("corpus_dir_sha256") != _corpus_fingerprint(home / "library"):
        raise ValueError("resume_config_mismatch:retrieval_corpus")
    elif config.get("retrieval", {}).get("index_sha256") != library_snapshot["sha256"]:
        raise ValueError("resume_config_mismatch:retrieval_index")

    sessions = SqliteSessionStore.open(str(database))
    learners = SqliteLearnerStore.open(database)
    current_calls = 0
    try:
        for case in cases:
            for group in GROUPS:
                cell_id = f"main-{case['case_id']}-{group}"
                target = cells_dir / f"{cell_id}.json"
                if target.exists():
                    existing = json.loads(target.read_text(encoding="utf-8"))
                    current_calls += int(existing.get("model_calls") or 0)
                    continue
                if current_calls >= MAX_MODEL_CALLS:
                    break
                row, current_calls = _cell(run_id, case, group, base=base, run_dir=run_dir,
                    stores=(sessions, learners), database=database, current_calls=current_calls)
                print(f"[{len(list(cells_dir.glob('*.json'))):02d}/{PLANNED_CELLS}] {row['case_id']} {group}: {row['status']} · calls {current_calls}", flush=True)
                if int(row.get("model_calls") or 0) > 1:
                    config["status"] = "stopped_call_budget_guard"
                    config["stop_reason"] = "cell_used_multiple_model_calls"
                    _write_json(manifest_path, config)
                    break
            if current_calls >= MAX_MODEL_CALLS or config.get("status") == "stopped_call_budget_guard":
                break
    finally:
        sessions.close()
        learners.close()

    rows = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(cells_dir.glob("*.json"))]
    summary = _summary(run_id, config, rows)
    config["status"] = summary["status"] if config.get("status") == "running" else config["status"]
    config["finished_at"] = _now()
    config["completed_cells"] = len(rows)
    config["model_call_count"] = current_calls
    _write_json(run_dir / "summary.json", summary)
    _write_json(manifest_path, config)
    return summary


def _corpus_fingerprint(library_dir: Path) -> str | None:
    if not library_dir.is_dir():
        return None
    digest = hashlib.sha256()
    for path in sorted(item for item in library_dir.rglob("*") if item.is_file()):
        digest.update(str(path.relative_to(library_dir)).replace("\\", "/").encode("utf-8"))
        digest.update(b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError:
            return None
        digest.update(b"\0")
    return digest.hexdigest()


def _library_index_snapshot(database: Path) -> dict[str, Any]:
    if not database.is_file():
        return {"sha256": None, "resource_count": 0, "chunk_count": 0, "active_course_textbooks": 0}
    connection = sqlite3.connect(database)
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        digest = hashlib.sha256()
        counts: dict[str, int] = {}
        for table in ("library_resources", "library_documents", "library_chunks", "library_operations"):
            if table not in tables:
                counts[table] = 0
                continue
            rows = connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
            counts[table] = len(rows)
            digest.update(table.encode("utf-8") + b"\0")
            for row in rows:
                digest.update(json.dumps(list(row), ensure_ascii=False, default=str,
                    separators=(",", ":")).encode("utf-8"))
                digest.update(b"\0")
        active = 0
        if {"library_resources", "library_chunks"}.issubset(tables):
            active = int(connection.execute(
                "SELECT COUNT(DISTINCT r.resource_id) FROM library_resources r "
                "JOIN library_chunks c ON c.resource_id=r.resource_id "
                "WHERE r.status='active' AND r.type='textbook' AND r.course_id=? "
                "AND (r.visibility='public' OR r.owner_id='local') AND c.reliable=1",
                ("ds.c_language.v1",)).fetchone()[0])
        return {"sha256": digest.hexdigest(), "resource_count": counts.get("library_resources", 0),
                "chunk_count": counts.get("library_chunks", 0),
                "active_course_textbooks": active, "table_counts": counts}
    finally:
        connection.close()


def _copy_library_index(source: Path, target: Path) -> dict[str, Any]:
    source = source.expanduser().resolve()
    target = target.expanduser().resolve()
    if source == target:
        raise ValueError("library_source_must_be_distinct_from_isolated_database")
    if not source.is_file() or not target.is_file():
        raise FileNotFoundError("library_source_or_target_database_missing")
    connection = sqlite3.connect(target, timeout=30, uri=True)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("ATTACH DATABASE ? AS library_source", (source.as_uri() + "?mode=ro",))
        source_tables = {row[0] for row in connection.execute(
            "SELECT name FROM library_source.sqlite_master WHERE type='table' AND name LIKE 'library_%'")}
        target_tables = {row[0] for row in connection.execute(
            "SELECT name FROM main.sqlite_master WHERE type='table' AND name LIKE 'library_%'")}
        order = ("library_resources", "library_documents", "library_chunks", "library_operations")
        tables = [name for name in order if name in source_tables and name in target_tables]
        for table in tables:
            current = int(connection.execute(f'SELECT COUNT(*) FROM main."{table}"').fetchone()[0])
            if current:
                raise ValueError("isolated_database_library_index_already_populated")
        connection.execute("BEGIN IMMEDIATE")
        for table in tables:
            source_columns = [row[1] for row in connection.execute(f'PRAGMA library_source.table_info("{table}")')]
            target_columns = [row[1] for row in connection.execute(f'PRAGMA main.table_info("{table}")')]
            if source_columns != target_columns:
                raise ValueError(f"library_schema_mismatch:{table}")
            connection.execute(f'INSERT INTO main."{table}" SELECT * FROM library_source."{table}"')
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        try:
            connection.execute("DETACH DATABASE library_source")
        except sqlite3.Error:
            pass
        connection.close()
    snapshot = _library_index_snapshot(target)
    if not snapshot["active_course_textbooks"]:
        raise RuntimeError("no_active_course_textbook_index; no model request sent")
    return snapshot


def _retrieval_queries(case: dict[str, Any]) -> dict[str, str]:
    """Mirror the production group's retrieval inputs without sending a model request."""
    user_input = str((case.get("user_turns") or [""])[0])
    state = _case_state(case)
    active_quiz = state.get("active_quiz")
    concept_id, concept = _concept_for_text(user_input, "ds.c_language.v1")
    if isinstance(active_quiz, dict) and active_quiz.get("concept_id"):
        concept_id = str(active_quiz["concept_id"])
        concept = _concept_name(concept_id, "ds.c_language.v1") or concept
    # Group A retrieves directly from the prompt. B/C first assess the prompt plus
    # resolved concept, exactly as graph.education.nodes.assess does.
    pedagogical_query = " ".join(part for part in (user_input.strip(), concept.strip()) if part) or concept
    return {"A": user_input, "B": pedagogical_query, "C": pedagogical_query}


def _freeze_retrieval_results(base: str, cases: list[dict[str, Any]]) -> dict[str, Any]:
    frozen: dict[str, Any] = {}
    for case in cases:
        group_results: dict[str, Any] = {}
        for group, query in _retrieval_queries(case).items():
            path = "/library/search?" + urllib.parse.urlencode({
                "query": query, "top_k": 5, "course_id": "ds.c_language.v1",
                "status": "active", "owner_id": "local"})
            result = request_json(base, path)
            evidence = result.get("evidence") if isinstance(result.get("evidence"), list) else []
            group_results[group] = {
                "query_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
                "status": str(result.get("status") or "unknown"),
                "result_count": len(evidence),
                "locatable_count": sum(bool(row.get("document_id") and row.get("chunk_id") and row.get("page") is not None)
                                        for row in evidence if isinstance(row, dict)),
                "references": [{"document_id": str(row.get("document_id") or ""),
                    "chunk_id": str(row.get("chunk_id") or ""), "page": row.get("page"),
                    "text_sha256": hashlib.sha256(str(row.get("text") or "").encode("utf-8")).hexdigest()}
                    for row in evidence if isinstance(row, dict)],
            }
        frozen[str(case["case_id"])] = group_results
    return frozen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="required confirmation that a real Provider may be used")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--library-index-from", type=Path)
    parser.add_argument("--supersedes-run-id", default="")
    args = parser.parse_args()
    if not args.live:
        print("Pass --live to confirm this real Provider pilot.", file=sys.stderr)
        return 2
    try:
        summary = run(api_url=args.api_url, run_id=args.run_id or None,
                      resume=args.resume, output_root=args.output_root,
                      library_index_from=args.library_index_from,
                      supersedes_run_id=args.supersedes_run_id or None)
    except Exception as exc:
        print(f"M3 live pilot did not complete: {type(exc).__name__}:{exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary.get("status") in {"completed", "completed_with_failures"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
