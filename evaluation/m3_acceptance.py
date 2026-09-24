"""Run resumable, deterministic M3 framework checks with a local Fake Provider.

The generated cases and evidence are constructed software fixtures. This runner
does not contact a paid model or use learner data; its report is an engineering
check, not a learning-effect result or teacher approval.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from api.app import create_app
from config.settings import Settings
from evaluation.dev_cases import CASE_VERSION
from graph.education.bindings import ACTION_BINDINGS
from graph.education.contracts import POLICY_VERSION
from models.learner.bkt import DEFAULT_PARAMETERS, update
from runtime.testing import FAKE_PROFILE_ID, make_service
from scripts.source_fingerprint import repository_fingerprint
from skills import register_default_skills
from tools.retrieval import build_search_textbook_tool

ROOT = Path(__file__).resolve().parents[1]
SOURCE_CASES = ROOT / "evaluation" / "dev_cases.json"
M3_CASE_VERSION = "ds-m3-offline-v1"
GROUPS = ("A", "B", "C")
ABLATION = ((True, True), (True, False), (False, True), (False, False))
FAKE_REPLY = "让我们先明确题目中的条件，再逐步检查你的推理，可以吗？"
PROMPT_VERSION = "education-prompts-source-fingerprint-v1"
PROMPT_FILES = ("api/sessions.py", "graph/education/nodes/ask.py", "graph/education/nodes/teach.py",
                "graph/education/nodes/hint.py", "graph/education/nodes/correct.py",
                "runtime/capabilities.py")
FROZEN_CONFIG_KEYS = (
    "case_version", "source_case_sha256", "m3_case_sha256", "sample_type", "human_subjects",
    "provider_profile", "model", "fake_provider_used", "paid_model_used", "network_required",
    "course_id", "question_bank_version", "policy_version", "strategy_bindings_sha256",
    "prompt_version", "prompt_sha256", "bkt_parameters", "bkt_config_hash", "sampling", "retrieval",
    "experiment_design", "source_fingerprint",
)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _prompt_fingerprint(root: Path = ROOT) -> str:
    digest = hashlib.sha256()
    for relative in PROMPT_FILES:
        path = root / relative
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _validate_resume_config(existing: dict[str, Any], requested: dict[str, Any]) -> None:
    for key in FROZEN_CONFIG_KEYS:
        if existing.get(key) != requested.get(key):
            raise ValueError(f"resume_config_mismatch:{key}")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for attempt in range(6):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(0.05 * (attempt + 1))


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _derived_cases(source: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for original in source["cases"]:
        case = dict(original)
        category = str(case.get("category") or "")
        if category == "consecutive_errors":
            outcomes = [False, False, False]
        elif category == "hint_then_success":
            outcomes = [True, True, False]
        else:
            outcomes = [True, False, True]
        case.update({
            "m3_case_version": M3_CASE_VERSION,
            "source_case_version": source["version"],
            "sample_type": "constructed_developer_fixture",
            "attempt_history": [
                {"item_id": f"{case['case_id']}-ATT-{index + 1}", "correct": correct,
                 "hint_count": 0, "grading_source": "exact_normalized_match", "confidence": 1.0}
                for index, correct in enumerate(outcomes)
            ],
            "retrieval_fixture": {
                "document_id": f"fixture-doc-{case['concept_id']}",
                "chunk_id": f"fixture-chunk-{case['case_id']}",
                "page": int(case.get("repeat") or 1),
                "chapter": "Constructed developer fixture",
                "source": "constructed_fixture; not a textbook citation",
                "text": f"Constructed evidence fixture for {case['concept_id']}; not source material.",
            },
            "allowed_variants": list(case.get("expected_action_family") or []),
        })
        rows.append(case)
    return rows


def _case_state(case: dict[str, Any]) -> dict[str, Any]:
    category = str(case.get("category") or "")
    state: dict[str, Any] = {
        "concept_id": case["concept_id"], "hint_level": 0, "turn_count": 0,
        "attempt_count": 0, "wrong_streak": 0, "last_answer_correct": None,
        "misconceptions": [],
    }
    active_quiz: dict[str, Any] | None = None
    if category == "consecutive_errors":
        state.update({"attempt_count": 2, "wrong_streak": 2,
                      "misconceptions": ["开发者固定错误状态"]})
    elif category == "hint_then_success":
        state.update({"attempt_count": 1, "hint_level": 1, "last_answer_correct": False})
        active_quiz = {"concept_id": case["concept_id"], "item_id": f"{case['case_id']}-ACTIVE",
                       "hint_count": 1}
    elif category == "misconception":
        state.update({"attempt_count": 1, "wrong_streak": 1,
                      "misconceptions": ["开发者构造的概念误解"]})
    elif category == "low_progress":
        state.update({"turn_count": 5, "hint_level": 2, "attempt_count": 1})
    return {"teaching_state": state, "active_quiz": active_quiz}


def _seed_bkt(store: Any, case: dict[str, Any], learner_id: str, session_id: str,
              phase: str) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for index, attempt in enumerate(case["attempt_history"]):
        result = store.record_attempt(
            attempt_id=f"{phase}-{case['case_id']}-{learner_id}-{index}", learner_id=learner_id,
            session_id=session_id, trace_id=f"seed-{phase}-{index}", course_id="ds.c_language.v1",
            item_id=str(attempt["item_id"]), concept_id=str(case["concept_id"]),
            bank_version=M3_CASE_VERSION, correct=bool(attempt["correct"]),
            hint_count=int(attempt["hint_count"]), grading_source=str(attempt["grading_source"]),
            confidence=float(attempt["confidence"]), parameters=DEFAULT_PARAMETERS,
            bkt_enabled=True, concept_unambiguous=True,
        )
        observations.append({"predicted_correct": result["predicted_correct"],
                             "correct": bool(attempt["correct"]),
                             "eligible": bool(result["eligible"]), "skip_reason": result["skip_reason"]})
    return observations


def _make_offline_app(tmp_root: Path, evidence_by_learner: dict[str, dict[str, Any]]):
    settings = Settings(sqlite_path=str(tmp_root / "runtime.sqlite"),
                        library_dir=str(tmp_root / "library"), sandbox_allowlist=[str(tmp_root)])
    provider_requests: list[dict[str, Any]] = []

    def fake_reply(request: dict[str, Any]) -> dict[str, Any]:
        provider_requests.append({"model": request.get("model"), "message_count": len(request.get("messages") or [])})
        return {"content": FAKE_REPLY, "usage": {"prompt_tokens": 23, "completion_tokens": 12, "total_tokens": 35}}

    service = make_service(bindings=ACTION_BINDINGS, script=[fake_reply], settings=settings)
    register_default_skills(service.skills)
    app = create_app(service=service, settings=settings)

    def fixed_search(query: str, *, top_k: int = 5, course_id: str | None = None,
                     owner_id: str | None = None, **_: Any) -> dict[str, Any]:
        fixture = evidence_by_learner.get(str(owner_id or "")) or {}
        if not fixture.get("retrieval_enabled", True):
            return {"evidence": [], "status": "insufficient_evidence"}
        evidence = dict(fixture.get("evidence") or {})
        if not evidence:
            return {"evidence": [], "status": "insufficient_evidence"}
        return {"evidence": [evidence], "status": "success"}

    service.library.search = fixed_search
    service.tools.register(build_search_textbook_tool(service.library.search))
    app.state.m3_evidence_options = {}
    service.m3_provider_requests = provider_requests
    service.m3_evidence_by_learner = evidence_by_learner
    return app, service


def _wait_for_turn(service: Any, session_id: str, trace_id: str, timeout: float = 10.0) -> list[dict[str, Any]]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        events = service.history(session_id)
        if any(event.get("type") == "agent.turn.completed" and event.get("trace_id") == trace_id for event in events):
            return events
        time.sleep(0.005)
    raise TimeoutError("offline_turn_timeout")


def _action(events: list[dict[str, Any]], trace_id: str) -> str:
    for event in reversed(events):
        if event.get("trace_id") != trace_id:
            continue
        if event.get("type") == "teaching.turn.completed":
            return str((event.get("payload") or {}).get("action") or "")
        if event.get("type") == "teaching.decision":
            return str((event.get("payload") or {}).get("action") or "")
    return ""


def _event_refs(events: list[dict[str, Any]], trace_id: str) -> list[dict[str, Any]]:
    for event in reversed(events):
        if event.get("trace_id") == trace_id and event.get("type") in {"teaching.turn.completed", "teaching.decision"}:
            refs = (event.get("payload") or {}).get("evidence_refs") or []
            return [dict(item) for item in refs if isinstance(item, dict)]
    return []


def _run_cell(client: TestClient, service: Any, app: Any, case: dict[str, Any], *, run_id: str,
              phase: str, group: str, options: dict[str, bool], replay_of: str = "") -> tuple[dict[str, Any], list[dict[str, Any]]]:
    case_id = str(case["case_id"])
    suffix = f"{phase}-{case_id}-{group}"
    if phase == "rag_ablation":
        suffix += f"-r{int(options.get('retrieval_enabled', True))}-e{int(options.get('evidence_constraint', True))}"
    learner_id = f"m3-{run_id}-{suffix}"
    created = client.post("/commands", json={"command_id": f"new-{suffix}", "learner_id": learner_id,
        "type": "session.new", "payload": {"session_mode": "study", "group": group,
            "course_id": "ds.c_language.v1", "experiment_run": True,
            "title": f"M3 {phase} {case_id} {group}"}})
    if created.status_code != 200:
        raise RuntimeError(f"session_create_failed:{created.status_code}:{created.text[:240]}")
    session_id = str(created.json().get("session_id") or "")
    if not session_id:
        raise RuntimeError("offline_session_id_missing")
    session = service.get_session(session_id)
    seeded_state = _case_state(case)
    session.metadata.update(seeded_state)
    service.save_session(session)
    observations = _seed_bkt(service.learner_store, case, learner_id, session_id, phase) if group == "C" else []
    app.state.m3_evidence_options[session_id] = dict(options)
    evidence_by_learner = getattr(service, "m3_evidence_by_learner")
    evidence_by_learner[learner_id] = {"retrieval_enabled": options.get("retrieval_enabled", True),
                                       "evidence": dict(case["retrieval_fixture"])}

    command_id = f"turn-{suffix}"
    response = client.post("/commands", json={"command_id": command_id, "learner_id": learner_id,
        "type": "message.send", "session_id": session_id,
        "payload": {"content": str((case.get("user_turns") or [""])[0]), "requested_action": "auto"}})
    if response.status_code != 200:
        raise RuntimeError(f"turn_start_failed:{response.status_code}:{response.text[:240]}")
    receipt = response.json()
    events = _wait_for_turn(service, session_id, str(receipt.get("trace_id") or ""))
    terminal = next((event for event in reversed(events) if event.get("type") == "agent.turn.completed"
                     and event.get("trace_id") == receipt.get("trace_id")), {})
    action = _action(events, str(receipt.get("trace_id") or ""))
    refs = _event_refs(events, str(receipt.get("trace_id") or ""))
    completed = str((terminal.get("payload") or {}).get("status") or "") == "ok"
    assistant = next((message.content for message in reversed(service.get_session(session_id).messages)
                      if message.role == "assistant"), "")
    frozen = dict(service.get_session(session_id).metadata.get("experiment") or {})
    returned_estimate = None
    if group == "C":
        returned_estimate = service.learner_store.get_estimate(learner_id, "ds.c_language.v1",
                                                                str(case["concept_id"]), DEFAULT_PARAMETERS)
    row = {
        "cell_id": suffix, "phase": phase, "case_id": case_id, "case_version": M3_CASE_VERSION,
        "sample_type": "constructed_developer_fixture", "group": group, "session_id": session_id,
        "learner_id": learner_id, "trace_id": str(receipt.get("trace_id") or ""),
        "replay_of": replay_of, "options": dict(options), "status": "completed" if completed else "failed",
        "action": action, "expected_action_family": list(case.get("expected_action_family") or []),
        "action_family_match": action in list(case.get("allowed_variants") or []),
        "evidence_refs": refs, "locatable_reference_count": sum(
            bool(ref.get("document_id") and ref.get("chunk_id") and ref.get("page") is not None) for ref in refs),
        "model_calls": sum(event.get("type") == "model.requested" and event.get("trace_id") == receipt.get("trace_id") for event in events),
        "bkt_observations": observations, "learner_estimate": returned_estimate,
        "frozen_config": {"provider_profile": frozen.get("provider_profile"), "model": frozen.get("model"),
            "policy_version": frozen.get("policy_version"), "bkt_config_hash": frozen.get("bkt_config_hash"),
            "retrieval": frozen.get("retrieval"), "sampling": frozen.get("sampling"),
            "question_bank_version": frozen.get("question_bank_version"), "course_id": "ds.c_language.v1",
            "prompt_version": PROMPT_VERSION, "prompt_sha256": _prompt_fingerprint(),
            "strategy_bindings_sha256": _sha256_bytes(json.dumps(
                ACTION_BINDINGS, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))},
        "routing": {"session_mode": "study", "turn_mode": "study", "experiment_run": bool(frozen.get("experiment_run"))},
        "failure_reason": "" if completed else _failure_code(events, str(receipt.get("trace_id") or "")),
        "response_text": assistant,
    }
    return row, events


def _failure_code(events: list[dict[str, Any]], trace_id: str) -> str:
    for event in events:
        if event.get("trace_id") == trace_id and event.get("type") == "agent.failed":
            error = (event.get("payload") or {}).get("error") or {}
            return str(error.get("code") or "agent_failed")
    return "turn_failed"


def _valid_locator(ref: dict[str, Any]) -> bool:
    return bool(ref.get("document_id") and ref.get("chunk_id") and ref.get("page") is not None)


def _ratio(numerator: int, denominator: int) -> dict[str, Any]:
    return {"numerator": numerator, "denominator": denominator,
            "value": numerator / denominator if denominator else None,
            "status": "computed" if denominator else "not_computable"}


def _auc(labels: list[bool], scores: list[float]) -> float | None:
    positive = [score for label, score in zip(labels, scores) if label]
    negative = [score for label, score in zip(labels, scores) if not label]
    if not positive or not negative:
        return None
    wins = sum(1.0 if pos > neg else 0.5 if pos == neg else 0.0 for pos in positive for neg in negative)
    return wins / (len(positive) * len(negative))


def _bkt_metrics(cells: list[dict[str, Any]]) -> dict[str, Any]:
    observations = [item for cell in cells for item in cell.get("bkt_observations", []) if item.get("eligible")]
    labels = [bool(item["correct"]) for item in observations]
    scores = [float(item["predicted_correct"]) for item in observations]
    count = len(labels)
    if not count:
        return {"n": 0, "auc": {"value": None, "status": "not_computable"},
                "brier": _ratio(0, 0), "log_loss": _ratio(0, 0), "ece_10_bins": _ratio(0, 0),
                "positive_count": 0, "negative_count": 0}
    brier = sum((score - int(label)) ** 2 for score, label in zip(scores, labels))
    epsilon = 1e-12
    loss = sum(-(int(label) * math.log(max(epsilon, score)) + (1 - int(label)) * math.log(max(epsilon, 1 - score)))
               for score, label in zip(scores, labels))
    ece = 0.0
    for index in range(10):
        bucket = [(label, score) for label, score in zip(labels, scores)
                  if index / 10 <= score < (index + 1) / 10 or (index == 9 and score == 1)]
        if bucket:
            confidence = sum(score for _, score in bucket) / len(bucket)
            accuracy = sum(label for label, _ in bucket) / len(bucket)
            ece += len(bucket) / count * abs(confidence - accuracy)
    auc = _auc(labels, scores)
    return {"n": count, "auc": {"value": auc, "status": "computed" if auc is not None else "not_computable_single_class"},
            "brier": {"numerator": brier, "denominator": count, "value": brier / count, "status": "computed"},
            "log_loss": {"numerator": loss, "denominator": count, "value": loss / count, "status": "computed"},
            "ece_10_bins": {"numerator": ece, "denominator": count, "value": ece, "status": "computed"},
            "positive_count": sum(labels), "negative_count": count - sum(labels)}


def _metrics(cells: list[dict[str, Any]], replay_cells: list[dict[str, Any]],
             main_cases: list[dict[str, Any]]) -> dict[str, Any]:
    main = [row for row in cells if row.get("phase") == "main"]
    ablation = [row for row in cells if row.get("phase") == "rag_ablation"]
    completed = [row for row in main if row.get("status") == "completed"]
    analyzed = [row for row in main + ablation if row.get("status") == "completed"]
    expected = _ratio(sum(bool(row.get("action_family_match")) for row in analyzed), len(analyzed))
    ref_rows = [row for row in analyzed if row.get("evidence_refs")]
    locatable = sum(_valid_locator(ref) for row in ref_rows for ref in row["evidence_refs"])
    reference_count = sum(len(row["evidence_refs"]) for row in ref_rows)
    citation = _ratio(locatable, reference_count)
    decision_events = [event for row in analyzed for event in row.get("safe_events", [])]
    # The runner's retained event snapshots are intentionally small and redacted.
    decision_rows = [event for event in decision_events if event.get("type") in {"pedagogy.decision", "teaching.decision"}]
    complete_decisions = sum(bool((event.get("payload") or {}).get("action")
                                  and ((event.get("payload") or {}).get("policy_version")
                                       or (event.get("payload") or {}).get("reason_codes"))) for event in decision_rows)
    replay_by_key = {(row["case_id"], row["group"]): row for row in replay_cells}
    comparable = [row for row in cells if row.get("phase") == "main" and (row["case_id"], row["group"]) in replay_by_key]
    replay_agree = sum(row.get("action") == replay_by_key[(row["case_id"], row["group"])].get("action") for row in comparable)
    return {
        "sample_type": "constructed_developer_fixture",
        "main_matrix": {"planned": 120, "observed": len(main), "completed": len(completed),
                        "groups": {group: sum(row.get("group") == group and row.get("status") == "completed" for row in main) for group in GROUPS},
                        "unique_sessions": len({row.get("session_id") for row in main if row.get("session_id")})},
        "rag_ablation_matrix": {"planned": 160, "observed": len(ablation),
            "completed": sum(row.get("status") == "completed" for row in ablation),
            "factors": ["retrieval_enabled", "evidence_constraint"]},
        "replay_matrix": {"planned": 120, "observed": len(replay_cells),
                          "completed": sum(row.get("status") == "completed" for row in replay_cells)},
        "metrics": {
            "expected_action_family_match": expected,
            "locatable_citation_rate": citation,
            "decision_completeness": _ratio(complete_decisions, len(decision_rows)),
            "replay_decision_agreement": _ratio(replay_agree, len(comparable)),
            "bkt_prediction": _bkt_metrics(main),
            "evidence_gap_behavior": {"retrieval_absent_constraint_on_blocks": sum(
                row.get("phase") == "rag_ablation" and row.get("options") == {"retrieval_enabled": False, "evidence_constraint": True}
                and row.get("action") == "reflect" for row in cells),
                "retrieval_absent_constraint_off_generated": sum(
                row.get("phase") == "rag_ablation" and row.get("options") == {"retrieval_enabled": False, "evidence_constraint": False}
                and row.get("model_calls", 0) > 0 for row in cells)},
        },
        "human_review": {"rater_1": "pending_human_review", "rater_2": "pending_human_review",
                         "answer_leakage": "pending_human_review", "citation_support": "pending_human_review",
                         "agreement": "pending_human_review"},
        "teacher_approval": "pending",
        "real_provider_experiment": "not_run",
        "student_learning_effect": "not_measured",
        "case_manifest_count": len(main_cases),
    }


def _score_templates(run_dir: Path, main_cells: list[dict[str, Any]]) -> list[str]:
    entries = [{"blind_id": hashlib.sha256(f"{row['case_id']}|{row['group']}".encode()).hexdigest()[:14],
                "case_id": row["case_id"], "sample_type": row["sample_type"],
                "response_text": row.get("response_text") or "", "action_appropriateness": None,
                "answer_leakage": None, "citation_support": None, "uncertainty_handling": None, "notes": ""}
               for row in main_cells if row.get("phase") == "main"]
    files: list[str] = []
    for index, seed in ((1, 613), (2, 907)):
        ordered = list(entries)
        random.Random(seed).shuffle(ordered)
        target = run_dir / "scoring" / f"rater-{index:02d}.json"
        _write_json(target, {"schema_version": "m3-blind-rating-v1", "rater_id": f"rater-{index:02d}",
            "group_labels_hidden": True, "scale": {"action_appropriateness": "1-5", "answer_leakage": "true|false",
                "citation_support": "1-5", "uncertainty_handling": "1-5"}, "items": ordered})
        files.append(str(target))
    return files


def run_m3(*, output_dir: str | Path = ROOT / "docs" / "experiments" / "m3-offline",
           run_id: str | None = None, resume: bool = False) -> dict[str, Any]:
    out = Path(output_dir).resolve()
    source = _read_json(SOURCE_CASES)
    cases = _derived_cases(source)
    if len(cases) != 40:
        raise ValueError("M3 offline suite requires exactly 40 source cases")
    source_bytes = SOURCE_CASES.read_bytes()
    case_bytes = (json.dumps(cases, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
    run_id = run_id or datetime.now(timezone.utc).strftime("m3-offline-%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    run_dir = out / run_id
    manifest_path = run_dir / "manifest.json"
    cases_path = run_dir / "cases.json"
    rows_dir = run_dir / "cells"
    events_dir = run_dir / "events"
    for directory in (rows_dir, events_dir):
        directory.mkdir(parents=True, exist_ok=True)
    config = {
        "run_id": run_id, "case_version": M3_CASE_VERSION, "source_case_version": source["version"],
        "source_case_sha256": _sha256_bytes(source_bytes), "m3_case_sha256": _sha256_bytes(case_bytes),
        "sample_type": "constructed_developer_fixture", "human_subjects": False,
        "provider_profile": FAKE_PROFILE_ID, "model": "fake-model", "fake_provider_used": True,
        "paid_model_used": False, "network_required": False, "course_id": "ds.c_language.v1",
        "question_bank_version": "not_configured; synthetic Attempts use M3 fixture IDs",
        "policy_version": POLICY_VERSION, "bkt_parameters": DEFAULT_PARAMETERS.to_dict(),
        "bkt_config_hash": DEFAULT_PARAMETERS.config_hash,
        "strategy_bindings_sha256": _sha256_bytes(json.dumps(
            ACTION_BINDINGS, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")),
        "prompt_version": PROMPT_VERSION, "prompt_sha256": _prompt_fingerprint(),
        "sampling": {"temperature": 0.3}, "retrieval": {"top_k": 5, "fixture_set": M3_CASE_VERSION},
        "experiment_design": {"main": "40 cases x A/B/C", "rag_ablation": "40 cases x C x 2 x 2",
                              "decision_replay": "same case/group in fresh isolated session"},
        "source_fingerprint": repository_fingerprint(ROOT),
    }
    if manifest_path.exists():
        if not resume:
            raise FileExistsError(f"M3 run already exists; pass --resume --run-id {run_id}")
        existing = _read_json(manifest_path)
        _validate_resume_config(existing, config)
        config = existing
    else:
        if resume:
            raise FileNotFoundError(f"M3 run does not exist: {run_id}")
        _write_json(cases_path, {"version": M3_CASE_VERSION, "source_version": source["version"],
                                 "sample_type": "constructed_developer_fixture", "cases": cases})
        config = {**config, "status": "running", "started_at": datetime.now(timezone.utc).isoformat(),
                  "completed_cell_count": 0, "completed_units": []}
        _write_json(manifest_path, config)

    old_home = os.environ.get("DEEPPROF_HOME")
    with tempfile.TemporaryDirectory(prefix="deepprof-m3-") as temporary:
        temp_root = Path(temporary)
        os.environ["DEEPPROF_HOME"] = str(temp_root / "home")
        evidence_by_learner: dict[str, dict[str, Any]] = {}
        app, service = _make_offline_app(temp_root, evidence_by_learner)
        app.state.m3_evidence_options = {}
        try:
            with TestClient(app) as client:
                units: list[tuple[str, dict[str, Any], str, dict[str, bool], str]] = []
                for case in cases:
                    for group in GROUPS:
                        units.append(("main", case, group, {"retrieval_enabled": True, "evidence_constraint": True}, ""))
                for case in cases:
                    for group in GROUPS:
                        units.append(("replay", case, group, {"retrieval_enabled": True, "evidence_constraint": True},
                                      f"main-{case['case_id']}-{group}"))
                for case in cases:
                    for retrieval_enabled, evidence_constraint in ABLATION:
                        units.append(("rag_ablation", case, "C", {"retrieval_enabled": retrieval_enabled,
                            "evidence_constraint": evidence_constraint}, ""))
                for phase, case, group, options, replay_of in units:
                    cell_id = f"{phase}-{case['case_id']}-{group}"
                    if phase == "rag_ablation":
                        cell_id += f"-r{int(options.get('retrieval_enabled', True))}-e{int(options.get('evidence_constraint', True))}"
                    target = rows_dir / f"{cell_id}.json"
                    if target.exists() and _read_json(target).get("status") == "completed":
                        continue
                    try:
                        row, events = _run_cell(client, service, app, case, run_id=run_id, phase=phase,
                                                group=group, options=options, replay_of=replay_of)
                    except Exception as exc:
                        row = {"cell_id": cell_id, "phase": phase, "case_id": case["case_id"],
                               "sample_type": "constructed_developer_fixture", "group": group,
                               "status": "failed", "failure_reason": type(exc).__name__ + ":" + str(exc)[:240],
                               "options": options, "replay_of": replay_of, "model_calls": 0,
                               "evidence_refs": [], "bkt_observations": []}
                        events = []
                    safe_events = [{"type": str(event.get("type") or ""), "trace_id": str(event.get("trace_id") or ""),
                                    "payload": dict(event.get("payload") or {})} for event in events]
                    row["safe_events"] = [event for event in safe_events if event["type"] in {
                        "pedagogy.decision", "teaching.decision", "teaching.turn.completed"}]
                    _write_json(target, row)
                    _write_json(events_dir / f"{cell_id}.json", {
                        "cell_id": cell_id, "sample_type": "constructed_developer_fixture", "events": safe_events})
                    config["completed_cell_count"] = len(list(rows_dir.glob("*.json")))
                    config["completed_units"] = sorted(path.stem for path in rows_dir.glob("*.json"))
                    _write_json(manifest_path, config)
        finally:
            # SQLite connections keep Windows handles to the temporary DBs.
            # Close every app/runtime store before TemporaryDirectory removes
            # the offline database tree (the API app does not own a lifespan
            # hook for these stores yet).
            stores = [
                getattr(app.state, "command_store", None),
                getattr(app.state, "learner_store", None),
                getattr(service, "events", None),
                getattr(service, "sessions", None),
                getattr(getattr(service, "library", None), "store", None),
            ]
            closed: set[int] = set()
            for store in stores:
                close = getattr(store, "close", None)
                if callable(close) and id(store) not in closed:
                    close()
                    closed.add(id(store))
            if old_home is None:
                os.environ.pop("DEEPPROF_HOME", None)
            else:
                os.environ["DEEPPROF_HOME"] = old_home

    all_rows = [_read_json(path) for path in sorted(rows_dir.glob("*.json"))]
    main_cells = [row for row in all_rows if row.get("phase") == "main"]
    replay_cells = [row for row in all_rows if row.get("phase") == "replay"]
    ablation_cells = [row for row in all_rows if row.get("phase") == "rag_ablation"]
    metric_rows = main_cells + ablation_cells
    report = _metrics(metric_rows, replay_cells, cases)
    expected = {"main": 120, "replay": 120, "rag_ablation": 160}
    observed = {"main": len(main_cells), "replay": len(replay_cells), "rag_ablation": len(ablation_cells)}
    all_complete = observed == expected and all(row.get("status") == "completed" for row in all_rows)
    isolated = len({row.get("session_id") for row in all_rows if row.get("session_id")}) == len(all_rows)
    report.update({
        "schema_version": "deepprof-m3-offline-v1", "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(), "provider": "explicit local Fake Provider",
        "status": "framework_passed_local_offline" if all_complete and isolated else "needs_attention",
        "m2_engineering_status": "passed; see M2 offline acceptance record",
        "real_provider_experiment": "not_run", "teacher_review": "pending",
        "learning_effect": "not_measured", "input_counts": {"planned": expected, "observed": observed},
        "all_cells_completed": all_complete, "session_isolation_passed": isolated,
        "failed_cells": [{"cell_id": row.get("cell_id"), "reason": row.get("failure_reason")}
                         for row in all_rows if row.get("status") != "completed"],
        "cell_directory": str(rows_dir.resolve()), "event_directory": str(events_dir.resolve()),
        "source_fingerprint": config.get("source_fingerprint"),
    })
    _write_json(run_dir / "summary.json", report)
    score_paths = _score_templates(run_dir, main_cells)
    report["blind_scoring_templates"] = score_paths
    report["rater_audit"] = "Use --audit-scores with this run directory after both templates are filled."
    _write_json(run_dir / "summary.json", report)
    config.update({"status": report["status"], "finished_at": datetime.now(timezone.utc).isoformat(),
                  "completed_cell_count": len(all_rows), "planned_cell_count": sum(expected.values())})
    _write_json(manifest_path, config)
    return report


def audit_scores(run_dir: str | Path) -> dict[str, Any]:
    root = Path(run_dir).resolve()
    left = _read_json(root / "scoring" / "rater-01.json")
    right = _read_json(root / "scoring" / "rater-02.json")

    def _items_by_id(data: dict[str, Any], rater_name: str) -> dict[str, dict[str, Any]]:
        if data.get("schema_version") != "m3-blind-rating-v1":
            raise ValueError(f"invalid_rating_schema:{rater_name}")
        if data.get("group_labels_hidden") is not True:
            raise ValueError(f"group_labels_not_hidden:{rater_name}")
        raw_items = data.get("items")
        if not isinstance(raw_items, list):
            raise ValueError(f"invalid_rating_items:{rater_name}")
        result: dict[str, dict[str, Any]] = {}
        for item in raw_items:
            if not isinstance(item, dict) or not str(item.get("blind_id") or "").strip():
                raise ValueError(f"invalid_blind_id:{rater_name}")
            blind_id = str(item["blind_id"])
            if blind_id in result:
                raise ValueError(f"duplicate_blind_id:{rater_name}:{blind_id}")
            result[blind_id] = item
        return result

    left_by_id = _items_by_id(left, "rater-01")
    right_by_id = _items_by_id(right, "rater-02")
    if set(left_by_id) != set(right_by_id):
        raise ValueError("rater_item_ids_mismatch")
    fields = ("action_appropriateness", "answer_leakage", "citation_support", "uncertainty_handling")
    result: dict[str, Any] = {"schema_version": "m3-rater-agreement-v1", "run_id": root.name, "fields": {}}
    rating_scales: dict[str, set[Any] | type] = {
        "action_appropriateness": {1, 2, 3, 4, 5}, "answer_leakage": bool,
        "citation_support": {1, 2, 3, 4, 5}, "uncertainty_handling": {1, 2, 3, 4, 5},
    }
    for rater_name, items_by_id in (("rater-01", left_by_id), ("rater-02", right_by_id)):
        for item in items_by_id.values():
            for field, allowed in rating_scales.items():
                value = item.get(field)
                if value is None:
                    continue
                if allowed is bool:
                    valid = isinstance(value, bool)
                else:
                    valid = not isinstance(value, bool) and value in allowed
                if not valid:
                    raise ValueError(f"invalid_rating:{rater_name}:{field}:{item['blind_id']}")
    for field in fields:
        pairs = [(left_by_id[key].get(field), right_by_id[key].get(field)) for key in sorted(left_by_id)]
        rated = [(a, b) for a, b in pairs if a is not None and b is not None]
        n = len(rated)
        observed = sum(a == b for a, b in rated)
        if not n:
            result["fields"][field] = {"n": 0, "raw_agreement": None, "cohen_kappa": None,
                                       "status": "pending_human_review"}
            continue
        categories = set(value for pair in rated for value in pair)
        expected = sum(sum(a == category for a, _ in rated) / n * sum(b == category for _, b in rated) / n
                       for category in categories)
        agreement = observed / n
        kappa = (agreement - expected) / (1 - expected) if expected < 1 else (1.0 if agreement == 1 else 0.0)
        result["fields"][field] = {"n": n, "raw_agreement": agreement, "cohen_kappa": kappa,
                                   "status": "computed"}
    _write_json(root / "rater-agreement.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "docs" / "experiments" / "m3-offline")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--audit-scores", type=Path)
    args = parser.parse_args()
    if args.audit_scores:
        result = audit_scores(args.audit_scores)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    report = run_m3(output_dir=args.output_dir, run_id=args.run_id or None, resume=args.resume)
    print(json.dumps({key: report[key] for key in (
        "run_id", "status", "input_counts", "all_cells_completed", "session_isolation_passed",
        "real_provider_experiment", "teacher_review", "learning_effect", "blind_scoring_templates",
    )}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["status"] == "framework_passed_local_offline" else 1)


if __name__ == "__main__":
    main()
