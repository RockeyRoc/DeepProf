"""Run the approved, isolated M3 live-model experiment and BKT calibration."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evaluation.bkt_calibration import run_calibration
from evaluation.m3_acceptance import PROMPT_VERSION, _prompt_fingerprint, _score_templates
from models.learner.bkt import DEFAULT_PARAMETERS, INITIAL_PARAMETERS
from scripts.build_m3_abc_experiment_report import build_report, export_run_bundle
from scripts.export_m3_live_pilot_aggregate import build_aggregate
from scripts.run_m3_live_pilot import (LIVE_CASE_VERSION, SOURCE_CASES, repository_fingerprint,
    run as run_live)

PROJECT = Path(__file__).resolve().parents[1]
WORKSPACE = PROJECT.parent
SOURCE_HOME = WORKSPACE / "开发者测试.deepprof"
SOURCE_DATABASE = SOURCE_HOME / "sessions.sqlite"
PREFLIGHT_CASES = ["DSDEV-001", "DSDEV-016", "DSDEV-018", "DSDEV-020"]
PROVIDER_PROFILE = "deepseek"
MODEL = "deepseek-flash"
MAX_OUTPUT_TOKENS = 4096
PREFLIGHT_BUDGET = 12
MAIN_BUDGET = 120
RAG_BUDGET = 136
HISTORICAL_PROVIDER_REQUESTS = 24
PREVIOUSLY_AUTHORIZED_NEW_REQUESTS = 328
ADDITIONAL_PROVIDER_REQUESTS_AUTHORIZED = 99
APPROVED_TOTAL_REQUEST_BUDGET = 451
MAX_AUTHORIZED_PREFLIGHT_ROUNDS = 8
DEFAULT_PROVIDER_NETWORK_MODE = "inherited"
GROUPS = ("A", "B", "C")
RAG_CONDITIONS = (
    ("retrieval-on_constraint-on", {"retrieval_enabled": True, "evidence_constraint": True}),
    ("retrieval-on_constraint-off", {"retrieval_enabled": True, "evidence_constraint": False}),
    ("retrieval-off_constraint-on", {"retrieval_enabled": False, "evidence_constraint": True}),
    ("retrieval-off_constraint-off", {"retrieval_enabled": False, "evidence_constraint": False}),
)
RAG_CONDITION_REQUEST_BUDGETS = {
    "retrieval-on_constraint-on": 40,
    "retrieval-on_constraint-off": 40,
    "retrieval-off_constraint-on": 0,
    "retrieval-off_constraint-off": 40,
}
PROXY_ENVIRONMENT_KEYS = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "all_proxy",
)


def _campaign_environment(base: dict[str, str] | None = None, *,
                          direct_provider_network: bool = False) -> tuple[dict[str, str], list[str]]:
    """Build an isolated run environment, optionally removing inherited proxies.

    Direct networking is opt-in and limited to the campaign subprocess. It can
    test whether an inherited host proxy is causing provider transport errors.
    """
    env = dict(os.environ if base is None else base)
    removed: list[str] = []
    if direct_provider_network:
        for key in PROXY_ENVIRONMENT_KEYS:
            if key in env:
                removed.append(key)
                env.pop(key, None)
    return env, removed


def _provider_budget_state(research_root: Path, raw_root: Path | None = None) -> dict[str, Any]:
    run_records: dict[str, dict[str, Any]] = {}
    summary_roots = [research_root, *([raw_root] if raw_root else [])]
    for root in summary_roots:
        for summary_path in sorted(root.rglob("summary.json")) if root.exists() else []:
            # pytest fixtures may deliberately create incomplete files named
            # summary.json. They are not experiment runs and must not enter
            # the cumulative Provider-call ledger.
            is_raw_experiment_root = (
                raw_root is not None and root.resolve() == Path(raw_root).resolve())
            if is_raw_experiment_root and any(
                part.casefold() in {"pytest-temp", ".pytest_cache"}
                for part in summary_path.relative_to(root).parts
            ):
                continue
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            run_id = str(summary.get("run_id") or "")
            calls = (summary.get("token_usage") or {}).get("model_calls")
            if calls is None:
                calls = (summary.get("limits") or {}).get("actual_provider_requests")
            if not run_id or not isinstance(calls, int) or calls < 0:
                raise RuntimeError(f"invalid_provider_call_count:{summary_path}")
            record = {"run_id": run_id, "status": str(summary.get("status") or "unknown"), "calls": calls,
                      "phase": str((summary.get("sample") or {}).get("phase") or ""),
                      "finished_at": str(summary.get("finished_at") or summary.get("started_at") or ""),
                      "source_fingerprint": (summary.get("frozen_configuration") or {}).get("source_fingerprint")}
            previous = run_records.get(run_id)
            if previous and previous["calls"] != calls:
                raise RuntimeError(f"provider_budget_summary_mismatch:{run_id}")
            if not previous or (not previous.get("source_fingerprint") and record.get("source_fingerprint")):
                run_records[run_id] = record

    # Raw runs may have submitted a provider request and stopped before the
    # aggregate summary was exported. Count completed cells and pending request
    # reservations conservatively so an interrupted run cannot refund budget.
    if raw_root and raw_root.exists():
        for runs_directory in raw_root.rglob("runs"):
            if any(part.casefold() in {"pytest-temp", ".pytest_cache"}
                   for part in runs_directory.relative_to(raw_root).parts):
                continue
            for run_dir in runs_directory.iterdir():
                manifest_path = run_dir / "manifest.json"
                if not run_dir.is_dir() or not manifest_path.is_file():
                    continue
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                run_id = str(manifest.get("run_id") or run_dir.name)
                cell_calls = 0
                cells = {path.stem: json.loads(path.read_text(encoding="utf-8"))
                         for path in (run_dir / "cells").glob("*.json")}
                cell_calls += sum(max(0, int(row.get("model_calls") or 0)) for row in cells.values())
                pending_calls = 0
                for pending_path in (run_dir / "pending").glob("*.json"):
                    pending = json.loads(pending_path.read_text(encoding="utf-8"))
                    if pending_path.stem not in cells:
                        pending_calls += max(0, int(pending.get("request_budget_reservation") or 0))
                previous = run_records.get(run_id) or {}
                actual_calls = max(int(previous.get("actual_calls", previous.get("calls", 0))), cell_calls)
                run_records[run_id] = {"run_id": run_id,
                    "status": str(previous.get("status") or manifest.get("status") or "running"),
                    "calls": actual_calls + pending_calls, "actual_calls": actual_calls,
                    "unresolved_request_reservation": pending_calls,
                    "phase": str(previous.get("phase") or manifest.get("phase") or ""),
                    "finished_at": str(previous.get("finished_at") or manifest.get("finished_at")
                                        or manifest.get("started_at") or manifest.get("created_at") or ""),
                    "source_fingerprint": previous.get("source_fingerprint") or manifest.get("source_fingerprint")}

    records = list(run_records.values())
    # A preflight blocked before the first actual Provider request is a local
    # configuration diagnostic, not a consumed model-preflight round.
    preflights = [row for row in records if row["phase"] == "preflight" and row["calls"] > 0]

    def _preflight_order(row: dict[str, Any]) -> tuple[float, str]:
        timestamp = str(row.get("finished_at") or "")
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            return parsed.timestamp(), str(row["run_id"])
        except (TypeError, ValueError, OverflowError):
            match = re.search(r"20\d{6}T\d{6}Z", str(row["run_id"]))
            if match:
                try:
                    return datetime.strptime(match.group(0), "%Y%m%dT%H%M%SZ").replace(
                        tzinfo=timezone.utc).timestamp(), str(row["run_id"])
                except ValueError:
                    pass
            return 0.0, str(row["run_id"])

    preflights.sort(key=_preflight_order)
    return {"provider_calls": sum(int(row["calls"]) for row in records),
            "unresolved_provider_request_reservation": sum(
                int(row.get("unresolved_request_reservation") or 0) for row in records),
            "preflight_runs": preflights,
            "failed_preflight_runs": [row for row in preflights if row["status"] != "completed"]}


def _preflight_run_record(research_root: Path, raw_root: Path | None, run_id: str) -> dict[str, Any] | None:
    """Resolve an approved preflight from the cumulative ledger."""
    ledger = _provider_budget_state(research_root, raw_root)
    return next((row for row in ledger["preflight_runs"] if row["run_id"] == run_id), None)


def _validate_campaign_budget(research_root: Path, *, raw_root: Path | None = None, preflight_fix: str | None = None,
                              current_fingerprint: dict[str, Any] | None = None,
                              new_preflight_attempt: bool = False,
                              resume_preflight: bool = False) -> dict[str, Any]:
    state = _provider_budget_state(research_root, raw_root)
    preflights = state["preflight_runs"]
    latest_preflight = preflights[-1] if preflights else None
    if (new_preflight_attempt and len(preflights) >= MAX_AUTHORIZED_PREFLIGHT_ROUNDS) or (
            not resume_preflight and
            len(preflights) >= MAX_AUTHORIZED_PREFLIGHT_ROUNDS and
            (not latest_preflight or latest_preflight["status"] != "completed")):
        raise RuntimeError("preflight_round_limit_reached")
    if not resume_preflight and latest_preflight and latest_preflight["status"] != "completed":
        previous = latest_preflight
        if not preflight_fix or not preflight_fix.strip():
            raise RuntimeError("preflight_retry_requires_located_fix_description")
        current = current_fingerprint if current_fingerprint is not None else repository_fingerprint(PROJECT)
        if not previous.get("source_fingerprint") or previous["source_fingerprint"] == current:
            raise RuntimeError("preflight_retry_requires_code_fingerprint_change")
    remaining = APPROVED_TOTAL_REQUEST_BUDGET - int(state["provider_calls"])
    # The original authorization covered two historical and two new preflights.
    # This amendment adds two bounded preflights. A passing preflight is reused
    # for campaign gating, so reserve only a remaining preflight slot plus the
    # frozen formal matrix and RAG ceiling. Count every call across all batches.
    latest_passed = bool(preflights and preflights[-1]["status"] == "completed")
    remaining_campaign_preflights = (0 if latest_passed or resume_preflight else min(
        1, MAX_AUTHORIZED_PREFLIGHT_ROUNDS - len(preflights)))
    required_reservation = (remaining_campaign_preflights * PREFLIGHT_BUDGET
                            + MAIN_BUDGET + RAG_BUDGET)
    if remaining < required_reservation:
        raise RuntimeError(f"provider_request_budget_insufficient:{remaining}<{required_reservation}")
    return {"provider_calls_before_campaign": int(state["provider_calls"]),
            "remaining_provider_requests": remaining,
            "preflight_rounds_before_campaign": len(preflights),
            "required_campaign_reservation": required_reservation,
            "unresolved_provider_request_reservation": int(
                state.get("unresolved_provider_request_reservation") or 0),
            "preflight_fix_description": preflight_fix.strip() if preflight_fix else ""}


def _copy_runtime_configuration(home: Path, *, max_output_tokens: int = MAX_OUTPUT_TOKENS,
                                profile_id: str | None = None,
                                thinking_parameter: str | None = None) -> dict[str, Any]:
    module_spec = importlib.util.spec_from_file_location(
        "_m3_windows_secret_check", PROJECT / "runtime" / "providers" / "windows_secrets.py")
    if not module_spec or not module_spec.loader:
        raise RuntimeError("windows_secret_store_unavailable")
    secret_module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(secret_module)
    WindowsDpapiSecretStore = secret_module.WindowsDpapiSecretStore

    profiles_path = SOURCE_HOME / "providers.json"
    if not profiles_path.is_file():
        raise FileNotFoundError("provider_profiles_missing")
    raw = json.loads(profiles_path.read_text(encoding="utf-8"))
    selected_profile_id = profile_id or PROVIDER_PROFILE
    profile = next((row for row in raw.get("profiles", [])
                    if row.get("profile_id") == selected_profile_id and row.get("enabled")), None)
    if not profile or str(profile.get("default_model") or "") != MODEL:
        raise RuntimeError("configured_deepseek_flash_profile_not_found")
    credential_path = SOURCE_HOME / "credentials" / "dpapi.json"
    available = bool(WindowsDpapiSecretStore(credential_path).get(str(profile.get("api_key_ref") or "")))
    if not available:
        raise RuntimeError("deepseek_credential_not_available; no model request sent")

    campaign_profile = _research_profile(profile, max_output_tokens=max_output_tokens,
                                         thinking_parameter=thinking_parameter)

    home.mkdir(parents=True, exist_ok=True)
    isolated_profiles = dict(raw)
    isolated_profiles["profiles"] = [campaign_profile]
    (home / "providers.json").write_text(json.dumps(isolated_profiles, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (home / "config.json").write_text(json.dumps({"provider_roles": {
        "tutor.default": {"profile_id": selected_profile_id, "model": MODEL}}}, indent=2) + "\n", encoding="utf-8")
    if credential_path.is_file():
        target = home / "credentials" / "dpapi.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(credential_path, target)
    course_source = SOURCE_HOME / "course" / "question_bank.json"
    if course_source.is_file():
        course_target = home / "course" / "question_bank.json"
        course_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(course_source, course_target)
    capabilities = dict(campaign_profile.get("capabilities") or {})
    model_capabilities = dict(campaign_profile.get("model_capabilities") or {}).get(MODEL) or {}
    return {"stream": bool(capabilities.get("stream")),
            "provider_profile_id": selected_profile_id,
            "api_mode": str(campaign_profile.get("api_mode") or "auto"),
            "reasoning": bool(capabilities.get("reasoning")),
            "reasoning_mode": str(model_capabilities.get("reasoning_mode") or "unknown"),
            "thinking_parameter": str(model_capabilities.get("thinking_parameter") or ""),
            "thinking_enabled": False,
            "max_output_tokens": int(model_capabilities.get("max_output_tokens") or 0)}


def _research_profile(profile: dict[str, Any], *, max_output_tokens: int = MAX_OUTPUT_TOKENS,
                      thinking_parameter: str | None = None) -> dict[str, Any]:
    """Return a research-only Profile with empirically verified stream support."""
    result = dict(profile)
    capabilities = dict(profile.get("capabilities") or {})
    if "stream" in capabilities and not bool(capabilities["stream"]):
        raise RuntimeError("configured_provider_profile_explicitly_disables_stream")
    # This DeepSeek model/profile produced ten complete `finish_reason=stop`
    # generations in the preserved first preflight. Do not write this claim to
    # the user's global provider profile.
    capabilities["stream"] = True
    capabilities["reasoning"] = True
    result["capabilities"] = capabilities
    result["vendor_id"] = "deepseek"
    model_capabilities = dict(profile.get("model_capabilities") or {})
    model_caps = dict(model_capabilities.get(MODEL) or {})
    if str(model_caps.get("reasoning_mode") or "").lower() not in {"", "unknown", "toggle"}:
        raise RuntimeError("configured_deepseek_model_reasoning_mode_is_not_toggle")
    selected_thinking_parameter = thinking_parameter or str(model_caps.get("thinking_parameter") or "")
    if selected_thinking_parameter not in {"", "thinking.type"}:
        raise RuntimeError("configured_deepseek_model_thinking_parameter_is_not_verified")
    if max_output_tokens not in {4096, 8192}:
        raise ValueError("max_output_tokens_must_be_4096_or_8192")
    model_caps.update({"reasoning_mode": "toggle", "thinking_parameter": selected_thinking_parameter or "thinking.type",
                       "max_output_tokens": max_output_tokens})
    model_capabilities[MODEL] = model_caps
    result["model_capabilities"] = model_capabilities
    if thinking_parameter == "thinking.type":
        result["api_mode"] = "chat_completions"
    return result


def _validate_source_index() -> dict[str, int]:
    if not SOURCE_DATABASE.is_file():
        raise FileNotFoundError("source_library_database_missing")
    import sqlite3

    connection = sqlite3.connect(f"file:{SOURCE_DATABASE.as_posix()}?mode=ro", uri=True, timeout=8)
    try:
        resources = int(connection.execute(
            "SELECT COUNT(DISTINCT r.resource_id) FROM library_resources r "
            "JOIN library_chunks c ON c.resource_id=r.resource_id "
            "WHERE r.status='active' AND r.type='textbook' AND r.course_id=? "
            "AND (r.visibility='public' OR r.owner_id='local') AND c.reliable=1",
            ("ds.c_language.v1",)).fetchone()[0])
        chunks = int(connection.execute(
            "SELECT COUNT(*) FROM library_chunks c JOIN library_resources r ON r.resource_id=c.resource_id "
            "WHERE r.status='active' AND r.type='textbook' AND r.course_id=? "
            "AND (r.visibility='public' OR r.owner_id='local') AND c.reliable=1",
            ("ds.c_language.v1",)).fetchone()[0])
    finally:
        connection.close()
    if not resources or not chunks:
        raise RuntimeError("course_textbook_index_missing; no model request sent")
    return {"active_course_textbooks": resources, "reliable_indexed_chunks": chunks}


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _experiment_config_payload(*, provider_network_mode: str | None = None) -> dict[str, Any]:
    network_mode = (DEFAULT_PROVIDER_NETWORK_MODE if provider_network_mode is None
                    else provider_network_mode)
    if network_mode not in {"inherited", "direct"}:
        raise ValueError("invalid_provider_network_mode")
    return {"schema_version": "deepprof-m3-abc-experiment-config-v5",
        "provider_profile": PROVIDER_PROFILE, "provider_vendor": "deepseek", "model": MODEL,
        "provider_network_mode": network_mode,
        "thinking_enabled": False, "temperature": 0.3, "max_output_tokens": MAX_OUTPUT_TOKENS,
        "max_retries": 0, "model_fallback": False,
        "preflight": {"cases": PREFLIGHT_CASES, "groups": list(GROUPS), "max_new_rounds": 1,
            "max_requests_per_round": PREFLIGHT_BUDGET},
        "case_version": LIVE_CASE_VERSION,
        "source_case_sha256": hashlib.sha256(SOURCE_CASES.read_bytes()).hexdigest(),
        "formal_ab": {"case_count": 40, "groups": list(GROUPS), "max_requests": MAIN_BUDGET},
        "rag_ablation": {"case_count": 40, "groups": ["C"], "condition_count": 4,
            "condition_cells": 160, "max_requests": RAG_BUDGET,
            "request_ceiling_basis": "40 retrieval-off evidence-required cells must make zero generation calls; 136 leaves 16 calls above the 120-call maximum for the other conditions",
            "conditions": [{"retrieval_enabled": True, "evidence_constraint": True},
                {"retrieval_enabled": True, "evidence_constraint": False},
                {"retrieval_enabled": False, "evidence_constraint": True},
                {"retrieval_enabled": False, "evidence_constraint": False}]},
        "cumulative_budget": {"historical_provider_requests": HISTORICAL_PROVIDER_REQUESTS,
            "previously_authorized_new_requests": PREVIOUSLY_AUTHORIZED_NEW_REQUESTS,
            "additional_provider_requests_authorized": ADDITIONAL_PROVIDER_REQUESTS_AUTHORIZED,
            "authorized_new_requests": (APPROVED_TOTAL_REQUEST_BUDGET - HISTORICAL_PROVIDER_REQUESTS),
            "authorized_total_provider_requests": APPROVED_TOTAL_REQUEST_BUDGET,
            "historical_preflight_rounds": 2, "previously_used_preflight_rounds": 2,
            "additional_preflight_rounds_authorized": 2,
            "maximum_preflight_rounds": MAX_AUTHORIZED_PREFLIGHT_ROUNDS,
            "preflight_rounds_before_post_vpn_batch": 5,
            "preflight_rounds_authorized_for_post_vpn_batch": 2,
            "authorization_amendments": [
                {"previous_total": 328, "additional_requests": 24, "new_total": 352,
                 "purpose": "one preflight baseline and one bounded direct-network trial"},
                {"previous_total": 352, "additional_requests": 99, "new_total": 451,
                 "purpose": "post-VPN baseline and one route trial, fresh formal matrix, and conditional RAG ceiling"},
            ]},
        "post_vpn_batch": {"previous_campaign_id": "m3-abc-bkt-20260928T115710Z-d62ca6",
            "baseline_preflight_run_id": "m3-preflight-20260928T124619Z-5fe6b54a",
            "network_trial_route": "direct", "max_iterations": 1,
            "restart_authorized": True, "failed_cells_may_be_repeated_in_new_campaign": True},
        "final_vpn_optimized_batch": {"prior_preflight_run_id": "m3-preflight-20260928T131141Z-2a312717",
            "network_mode": network_mode, "max_new_rounds": 1,
            "remaining_request_allocation": {"preflight": PREFLIGHT_BUDGET,
                "formal_ab": MAIN_BUDGET, "rag_ablation_ceiling": RAG_BUDGET},
            "preflight_must_pass_before_formal": True,
            "formal_matrix_must_pass_before_rag": True},
        "prompt_version": PROMPT_VERSION, "prompt_sha256": _prompt_fingerprint(),
        "bkt_config_hash": INITIAL_PARAMETERS.config_hash}


def _experiment_config_hash(*, provider_network_mode: str | None = None) -> str:
    config = _experiment_config_payload(provider_network_mode=provider_network_mode)
    return hashlib.sha256(json.dumps(config, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write an experiment artifact atomically, creating its parent directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _write_experiment_config(path: Path, *, provider_network_mode: str | None = None) -> dict[str, Any]:
    config = _experiment_config_payload(provider_network_mode=provider_network_mode)
    config["config_sha256"] = _experiment_config_hash(provider_network_mode=provider_network_mode)
    _write_json(path, config)
    return config


def _preflight_pointer_path(raw_research_root: Path) -> Path:
    return raw_research_root / "latest-autoresearch-preflight.json"


def _incomplete_preflight_candidate(raw_research_root: Path, *,
                                    provider_network_mode: str | None = None) -> dict[str, Any] | None:
    """Return a safely resumable run, or fail closed when prior cleanup is unknown."""
    pointer_path = _preflight_pointer_path(raw_research_root)
    if not pointer_path.is_file():
        return None
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    state = str(pointer.get("state") or "")
    if state in {"running", "cleanup_unconfirmed"}:
        raise RuntimeError("preflight_resume_blocked_gateway_cleanup_unconfirmed")
    if state != "interrupted":
        return None
    if pointer.get("isolation_cleanup") != "isolated_gateway_stopped":
        raise RuntimeError("preflight_resume_blocked_gateway_cleanup_unconfirmed")
    run_id = str(pointer.get("run_id") or "")
    if not re.fullmatch(r"m3-preflight-[A-Za-z0-9T-]+-[a-f0-9]{8}", run_id):
        raise RuntimeError("preflight_resume_pointer_invalid")
    raw_campaign = (raw_research_root / run_id).resolve()
    if raw_research_root.resolve() not in raw_campaign.parents:
        raise RuntimeError("preflight_resume_pointer_invalid")
    manifest_path = raw_campaign / "runs" / run_id / "manifest.json"
    if not manifest_path.is_file():
        return None
    if pointer.get("experiment_config_sha256") != _experiment_config_hash(
            provider_network_mode=provider_network_mode):
        raise RuntimeError("preflight_resume_config_mismatch")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("run_id") != run_id or manifest.get("phase") != "preflight"
            or manifest.get("provider_profile") != PROVIDER_PROFILE or manifest.get("model") != MODEL):
        raise RuntimeError("preflight_resume_manifest_mismatch")
    return pointer


def run_preflight_measurement(*, direct_provider_network: bool | None = None) -> dict[str, Any]:
    """Run one bounded preflight and keep every artifact outside the Git tree."""
    if direct_provider_network is None:
        direct_provider_network = DEFAULT_PROVIDER_NETWORK_MODE == "direct"
    network_mode = "direct" if direct_provider_network else "inherited"
    research_root = PROJECT / "docs" / "experiments" / "m3-abc-research"
    raw_research_root = SOURCE_HOME / "experiments" / "m3-abc-research"
    pointer_path = _preflight_pointer_path(raw_research_root)
    resume_pointer = _incomplete_preflight_candidate(raw_research_root,
                                                       provider_network_mode=network_mode)
    resuming = resume_pointer is not None
    if resuming and resume_pointer.get("provider_network_mode") != network_mode:
        raise RuntimeError("preflight_resume_config_mismatch:provider_network_mode")
    budget_state = _validate_campaign_budget(research_root, raw_root=raw_research_root,
        preflight_fix=None if resuming else "Run the one final preflight authorized after the user optimized "
            "the VPN path. Reuse the inherited network environment because the previous direct-mode trial "
            "removed no proxy variables; keep model, prompt, cases, temperature, token limit, retries, "
            "fallback, and scoring frozen.",
        new_preflight_attempt=not resuming, resume_preflight=resuming)
    run_id = (str(resume_pointer["run_id"]) if resuming else
        f"m3-preflight-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}")
    raw_campaign = raw_research_root / run_id
    run_root = raw_campaign / "runs"
    runtime_home = raw_campaign / "runtime"
    experiment_config_path = raw_campaign / "experiment-config.json"
    if resuming:
        config = json.loads(experiment_config_path.read_text(encoding="utf-8"))
        saved_config = dict(config)
        declared_hash = saved_config.pop("config_sha256", None)
        payload_hash = hashlib.sha256(json.dumps(saved_config, ensure_ascii=False, sort_keys=True,
            separators=(",", ":")).encode("utf-8")).hexdigest()
        if declared_hash != _experiment_config_hash(provider_network_mode=network_mode) or payload_hash != declared_hash:
            raise RuntimeError("preflight_resume_config_mismatch")
    else:
        raw_campaign.mkdir(parents=True, exist_ok=False)
        run_root.mkdir(parents=True, exist_ok=False)
        _write_experiment_config(experiment_config_path, provider_network_mode=network_mode)

    preflight_dir = run_root / run_id
    raw_summary_path = preflight_dir / "summary.json"
    previous_cleanup = (str(resume_pointer.get("isolation_cleanup")) if resuming else "")
    summary: dict[str, Any] | None = None
    if resuming and raw_summary_path.is_file():
        # The matrix finished before interruption. Finalize its already-written
        # summary without opening a Gateway or issuing a duplicate request.
        summary = json.loads(raw_summary_path.read_text(encoding="utf-8"))
        verified_capabilities = dict(resume_pointer.get("provider_capabilities") or {})
        removed_proxies = list(resume_pointer.get("provider_network_removed_proxy_names") or [])
        cleanup_status = previous_cleanup
    else:
        _validate_source_index()
        verified_capabilities = _copy_runtime_configuration(runtime_home)
        database_path = runtime_home / "sessions.sqlite"
        port = _free_loopback_port()
        api_url = f"http://127.0.0.1:{port}"
        env, removed_proxies = _campaign_environment(direct_provider_network=direct_provider_network)
        env.update({"DEEPPROF_HOME": str(runtime_home), "DEEPPROF_SQLITE_PATH": str(database_path),
            "DEEPPROF_API_HOST": "127.0.0.1", "DEEPPROF_API_PORT": str(port),
            "DEEPPROF_LLM_MAX_TOKENS": str(MAX_OUTPUT_TOKENS), "DEEPPROF_LLM_MAX_RETRIES": "0",
            "DEEPPROF_STREAM_INCLUDE_USAGE": "true"})
        environment_keys = ("DEEPPROF_HOME", "DEEPPROF_SQLITE_PATH", "DEEPPROF_API_HOST",
            "DEEPPROF_API_PORT", "DEEPPROF_LLM_MAX_TOKENS", "DEEPPROF_LLM_MAX_RETRIES",
            "DEEPPROF_STREAM_INCLUDE_USAGE")
        previous_environment = {key: os.environ.get(key) for key in environment_keys}
        logfile = raw_campaign / "gateway.log"
        command_log = raw_campaign / "preflight-command.log"
        gateway = None
        cleanup_status = "gateway_not_started"
        redirected = io.StringIO()
        _write_json(pointer_path, {"schema_version": "deepprof-autoresearch-preflight-pointer-v1",
            "state": "running", "run_id": run_id,
            "experiment_config_sha256": _experiment_config_hash(provider_network_mode=network_mode),
            "provider_network_mode": network_mode,
            "provider_network_removed_proxy_names": removed_proxies,
            "provider_capabilities": verified_capabilities,
            "isolation_cleanup": "pending"})
        with logfile.open("ab") as log, command_log.open("w", encoding="utf-8") as command_output:
            popen_kwargs: dict[str, Any] = {"cwd": PROJECT, "env": env, "stdout": log,
                "stderr": subprocess.STDOUT}
            if os.name == "nt":
                popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                with contextlib.redirect_stdout(redirected), contextlib.redirect_stderr(redirected):
                    gateway = subprocess.Popen([sys.executable, "-m", "uvicorn", "api.app:app",
                        "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"], **popen_kwargs)
                    os.environ.update({key: env[key] for key in environment_keys})
                    if _wait_gateway(gateway, api_url, logfile).get("status") != "ok":
                        raise RuntimeError("isolated_gateway_health_check_failed")
                    summary = run_live(api_url=api_url, run_id=run_id, output_root=run_root,
                        library_index_from=None if resuming else SOURCE_DATABASE,
                        case_count=len(PREFLIGHT_CASES), case_ids=PREFLIGHT_CASES, phase="preflight",
                        provider_profile=PROVIDER_PROFILE, model=MODEL, max_output_tokens=MAX_OUTPUT_TOKENS,
                        request_budget=PREFLIGHT_BUDGET, bkt_snapshot=INITIAL_PARAMETERS.to_dict(),
                        resume=resuming)
            finally:
                try:
                    if gateway is not None:
                        if gateway.poll() is None:
                            gateway.terminate()
                        try:
                            gateway.wait(timeout=8)
                        except subprocess.TimeoutExpired:
                            gateway.kill()
                            gateway.wait(timeout=3)
                        cleanup_status = "isolated_gateway_stopped" if gateway.poll() is not None else "gateway_stop_unconfirmed"
                finally:
                    for key, value in previous_environment.items():
                        if value is None:
                            os.environ.pop(key, None)
                        else:
                            os.environ[key] = value
                    command_output.write(redirected.getvalue())
                    _write_json(pointer_path, {"schema_version": "deepprof-autoresearch-preflight-pointer-v1",
                        "state": "interrupted" if cleanup_status == "isolated_gateway_stopped" else "cleanup_unconfirmed",
                        "run_id": run_id,
                        "experiment_config_sha256": _experiment_config_hash(provider_network_mode=network_mode),
                        "provider_network_mode": network_mode,
                        "provider_network_removed_proxy_names": removed_proxies,
                        "provider_capabilities": verified_capabilities,
                        "isolation_cleanup": cleanup_status})
    if summary is None:
        raise RuntimeError("preflight_summary_missing")
    if cleanup_status != "isolated_gateway_stopped":
        raise RuntimeError("isolated_gateway_cleanup_unconfirmed")
    passed, reason = _preflight_passed(preflight_dir, summary)
    cells = [json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((preflight_dir / "cells").glob("*.json"))]
    successful = sum(_preflight_cell_passed(row) for row in cells)
    if not passed:
        # A matrix, isolation, or manifest consistency failure is itself one
        # failed preflight observation, even when all individual files look good.
        successful = min(successful, 11)
    metric = 1.0 - successful / 12
    checkpoint = {"provider_calls_before_preflight": budget_state["provider_calls_before_campaign"],
        "preflight_rounds_before_preflight": budget_state["preflight_rounds_before_campaign"],
        "unresolved_request_reservation_before_preflight": int(
            budget_state.get("unresolved_provider_request_reservation") or 0),
        "authorized_total_provider_requests": APPROVED_TOTAL_REQUEST_BUDGET,
        "authorized_new_preflight_round_ceiling": max(0, MAX_AUTHORIZED_PREFLIGHT_ROUNDS
            - int(budget_state["preflight_rounds_before_campaign"])),
        "experiment_config_sha256": _experiment_config_hash(provider_network_mode=network_mode)}
    summary.update({"status": "completed" if passed else "completed_with_failures",
        "preflight_passed": passed, "preflight_stop_reason": reason,
        "preflight_failure_rate": metric, "preflight_budget_checkpoint": checkpoint,
        "provider_capabilities": verified_capabilities,
        "provider_network_mode": "direct" if direct_provider_network else "inherited",
        "provider_network_removed_proxy_names": removed_proxies,
        "isolation_cleanup": cleanup_status})
    _write_json(preflight_dir / "summary.json", summary)
    _write_json(raw_campaign / "preflight-budget.json", checkpoint)
    state_after = _provider_budget_state(research_root, raw_research_root)
    budget_ledger = {"schema_version": "deepprof-provider-request-budget-v1",
        "authorized_total_provider_requests": APPROVED_TOTAL_REQUEST_BUDGET,
        "experiment_config_sha256": _experiment_config_hash(provider_network_mode=network_mode),
        "provider_requests_before_campaign": budget_state["provider_calls_before_campaign"],
        "actual_provider_requests": int((summary.get("token_usage") or {}).get("model_calls") or 0),
        "provider_requests_after_campaign": state_after["provider_calls"],
        "remaining_after_campaign": max(0, APPROVED_TOTAL_REQUEST_BUDGET - state_after["provider_calls"]),
        "preflight_rounds_before_campaign": budget_state["preflight_rounds_before_campaign"],
        "preflight_rounds_used": len(state_after["preflight_runs"]),
        "authorized_new_preflight_request_ceiling": min(2, max(0, MAX_AUTHORIZED_PREFLIGHT_ROUNDS
            - int(budget_state["preflight_rounds_before_campaign"]))) * PREFLIGHT_BUDGET,
        "unresolved_request_reservation_before_campaign": budget_state.get(
            "unresolved_provider_request_reservation", 0),
        "unresolved_request_reservation_after_campaign": state_after[
            "unresolved_provider_request_reservation"],
        "preflight": int((summary.get("token_usage") or {}).get("model_calls") or 0),
        "formal_ab": 0, "rag_ablation": 0,
        "status": "preflight_passed" if passed else "stopped_after_preflight",
        "isolation_cleanup": cleanup_status,
        "stop_reason": "" if passed else reason}
    summary["preflight_budget_after"] = {key: budget_ledger[key] for key in (
        "provider_requests_after_campaign", "remaining_after_campaign", "preflight_rounds_used",
        "unresolved_request_reservation_after_campaign", "status")}
    _write_json(preflight_dir / "summary.json", summary)
    _write_json(raw_campaign / "provider-request-budget.json", budget_ledger)
    _write_json(pointer_path, {"schema_version": "deepprof-autoresearch-preflight-pointer-v1",
        "state": "completed" if passed else "stopped_after_preflight", "run_id": run_id,
        "experiment_config_sha256": _experiment_config_hash(provider_network_mode=network_mode),
        "provider_network_mode": network_mode,
        "provider_network_removed_proxy_names": removed_proxies,
        "provider_capabilities": verified_capabilities, "isolation_cleanup": cleanup_status})
    if state_after["provider_calls"] > APPROVED_TOTAL_REQUEST_BUDGET:
        raise RuntimeError("cumulative_provider_request_budget_exceeded_after_preflight")
    return {"run_id": run_id, "preflight_failure_rate": metric, "passed": passed,
        "reason": reason, "actual_provider_requests": int(
            (summary.get("token_usage") or {}).get("model_calls") or 0),
        "completed_generations": successful, "planned_cells": 12,
        "provider_network_mode": "direct" if direct_provider_network else "inherited",
        "provider_requests_cumulative": state_after["provider_calls"],
        "provider_requests_remaining": APPROVED_TOTAL_REQUEST_BUDGET - state_after["provider_calls"],
        "isolation_cleanup": cleanup_status, "resumed": resuming, "artifacts": str(raw_campaign)}


def _health(api_url: str) -> dict[str, Any]:
    request = urllib.request.Request(api_url.rstrip("/") + "/health", headers={"Accept": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=2) as response:
        return json.loads(response.read().decode("utf-8"))


def _wait_gateway(process: subprocess.Popen[Any], api_url: str, logfile: Path) -> dict[str, Any]:
    deadline = time.monotonic() + 45
    last_error = "gateway_startup_timeout"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("isolated_gateway_exited_before_ready; see local gateway log")
        try:
            return _health(api_url)
        except (OSError, urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = type(exc).__name__
            time.sleep(0.5)
    raise RuntimeError(f"isolated_gateway_not_ready:{last_error}; see {logfile}")


def _preflight_cell_passed(row: dict[str, Any]) -> bool:
    diagnostics = row.get("provider_diagnostics") if isinstance(row.get("provider_diagnostics"), dict) else {}
    outcomes = diagnostics.get("outcomes") if isinstance(diagnostics.get("outcomes"), list) else []
    requests = diagnostics.get("requests") if isinstance(diagnostics.get("requests"), list) else []
    return bool(
        row.get("model_calls") == 1
        and diagnostics.get("request_count") == 1
        and len(requests) == 1
        and requests[0].get("provider_profile") == PROVIDER_PROFILE
        and requests[0].get("model") == MODEL
        and len(outcomes) == 1
        and outcomes[0].get("status") == "completed"
        and outcomes[0].get("finish_reason") == "stop"
        and row.get("evaluation_status") == "completed"
        and row.get("application_status") == "completed"
        and row.get("status") == "completed"
        and str(row.get("response_text") or "").strip()
    )


def _preflight_passed(run_dir: Path, summary: dict[str, Any]) -> tuple[bool, str]:
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    cells = [json.loads(path.read_text(encoding="utf-8")) for path in sorted((run_dir / "cells").glob("*.json"))]
    if len(cells) != 12:
        return False, f"preflight_incomplete_cells:{len(cells)}/12"
    expected = {(case_id, group) for case_id in PREFLIGHT_CASES for group in GROUPS}
    observed = {(str(row.get("case_id") or ""), str(row.get("group") or "")) for row in cells}
    if observed != expected:
        return False, "preflight_case_group_matrix_mismatch"
    if len({str(row.get("session_id") or "") for row in cells}) != 12:
        return False, "preflight_session_isolation_mismatch"
    called_by_group = {group: 0 for group in GROUPS}
    for row in cells:
        calls = int(row.get("model_calls") or 0)
        called_by_group[str(row.get("group"))] = called_by_group.get(str(row.get("group")), 0) + calls
        if not _preflight_cell_passed(row):
            return False, "preflight_requires_12_single_call_complete_generations"
    if any(called_by_group[group] != 4 for group in GROUPS):
        return False, "preflight_did_not_generate_in_every_case_and_group"
    used = int(summary.get("token_usage", {}).get("model_calls") or 0)
    if used != 12 or used > PREFLIGHT_BUDGET or used > int(manifest.get("call_budget", {}).get("authorized_provider_requests") or 0):
        return False, "preflight_request_budget_or_count_mismatch"
    if manifest.get("groups") != list(GROUPS) or manifest.get("phase") != "preflight":
        return False, "preflight_manifest_configuration_mismatch"
    return True, "preflight_passed"


def _group_c_learner_estimate_failure(learner: dict[str, Any], config_hash: str) -> str | None:
    """Validate the public /sessions/{id}/learner response snapshot for group C."""
    if learner.get("status") != "ok" or learner.get("experiment_group") != "C":
        return "learner_estimate_unavailable_or_wrong_group"
    if learner.get("config_hash") != config_hash:
        return "learner_estimate_config_hash_mismatch"
    estimates = learner.get("estimates")
    if not isinstance(estimates, list) or len(estimates) != 1:
        return "learner_estimate_concept_count_mismatch"
    estimate = estimates[0]
    if (estimate.get("status") != "available" or estimate.get("course_id") != "ds.c_language.v1"
            or estimate.get("model_type") != "bkt"):
        return "learner_estimate_model_or_course_mismatch"
    if estimate.get("config_hash") != config_hash:
        return "learner_estimate_config_hash_mismatch"
    if int(estimate.get("evidence_count") or 0) != 3:
        return "learner_estimate_evidence_count_mismatch"
    return None


def _main_integration_passed(run_dir: Path, summary: dict[str, Any], parameters: dict[str, Any]) -> tuple[bool, str]:
    if int(summary.get("observed_cells") or 0) != 120:
        return False, f"formal_matrix_incomplete:{summary.get('observed_cells', 0)}/120"
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    if int(summary.get("token_usage", {}).get("model_calls") or 0) > MAIN_BUDGET:
        return False, "formal_request_budget_exceeded"
    if manifest.get("bkt_config_hash") != parameters.get("config_hash"):
        return False, "formal_manifest_bkt_hash_mismatch"
    if manifest.get("groups") != list(GROUPS) or manifest.get("planned_cases") != 40:
        return False, "formal_matrix_manifest_mismatch"
    if manifest.get("generation_limit", {}).get("max_output_tokens") != MAX_OUTPUT_TOKENS or manifest.get(
            "generation_limit", {}).get("max_retries") != 0:
        return False, "formal_matrix_generation_configuration_mismatch"
    all_cells = [json.loads(path.read_text(encoding="utf-8"))
                 for path in sorted((run_dir / "cells").glob("main-*.json"))]
    if len(all_cells) != 120:
        return False, f"formal_cell_artifacts_incomplete:{len(all_cells)}/120"
    if len({str(cell.get("session_id") or "") for cell in all_cells}) != 120 or len(
            {str(cell.get("learner_id") or "") for cell in all_cells}) != 120:
        return False, "formal_session_or_learner_isolation_mismatch"
    if sum(max(0, int(cell.get("model_calls") or 0)) for cell in all_cells) != int(
            summary.get("token_usage", {}).get("model_calls") or 0):
        return False, "formal_provider_call_count_mismatch"
    valid_no_generation = {"insufficient_evidence", "deterministic_policy_fallback",
                           "policy_selected_no_generation"}
    for cell in all_cells:
        if cell.get("application_status") != "completed":
            return False, "formal_matrix_application_terminal_failed"
        group = str(cell.get("group") or "")
        calls = int(cell.get("model_calls") or 0)
        if group not in GROUPS or calls not in {0, 1}:
            return False, "formal_matrix_cell_call_count_or_group_mismatch"
        diagnostic = cell.get("provider_diagnostics") if isinstance(cell.get("provider_diagnostics"), dict) else {}
        requests = diagnostic.get("requests") if isinstance(diagnostic.get("requests"), list) else []
        outcomes = diagnostic.get("outcomes") if isinstance(diagnostic.get("outcomes"), list) else []
        if diagnostic.get("request_count") != calls or len(requests) != calls:
            return False, "formal_matrix_provider_trace_count_mismatch"
        if calls and (requests[0].get("provider_profile") != PROVIDER_PROFILE
                      or requests[0].get("model") != MODEL):
            return False, "formal_matrix_provider_model_mismatch"
        frozen = cell.get("frozen_config") or {}
        sampling = frozen.get("sampling") or {}
        if (frozen.get("provider_profile") != PROVIDER_PROFILE or frozen.get("model") != MODEL
                or sampling.get("temperature") != 0.3
                or sampling.get("thinking_enabled") is not False
                or sampling.get("require_explicit_thinking_mode") is not True
                or sampling.get("max_output_tokens") != MAX_OUTPUT_TOKENS):
            return False, "formal_matrix_frozen_sampling_mismatch"
        evaluation = str(cell.get("evaluation_status") or "unknown")
        if cell.get("status") != "completed":
            if evaluation not in {"completed", "not_applicable"}:
                return False, f"formal_matrix_generation_not_complete:{evaluation}"
            return False, "formal_matrix_cell_terminal_failed"
        if evaluation == "not_applicable":
            if calls or cell.get("no_generation_reason") not in valid_no_generation:
                return False, "formal_matrix_no_generation_reason_missing_or_unknown"
        elif evaluation != "completed":
            return False, f"formal_matrix_generation_not_complete:{evaluation}"
        elif (calls != 1 or len(outcomes) != 1 or outcomes[0].get("status") != "completed"
              or outcomes[0].get("finish_reason") != "stop"
              or not str(cell.get("response_text") or "").strip()):
            return False, "formal_matrix_generation_trace_incomplete"
        observations = cell.get("bkt_observations") or []
        estimate = cell.get("learner_estimate") or {}
        if group in {"A", "B"} and (observations or estimate.get("status") != "group_disabled"):
            return False, "formal_group_ab_must_not_write_bkt"
    for path in sorted((run_dir / "cells").glob("main-*-C.json")):
        cell = json.loads(path.read_text(encoding="utf-8"))
        observations = cell.get("bkt_observations") or []
        frozen = cell.get("frozen_config") or {}
        learner = cell.get("learner_estimate") or {}
        if frozen.get("bkt_config_hash") != parameters.get("config_hash"):
            return False, "formal_group_C_parameter_snapshot_mismatch"
        if len(observations) != 3 or any(not row.get("eligible") for row in observations):
            return False, "formal_group_C_attempt_isolation_or_eligibility_mismatch"
        estimate_failure = _group_c_learner_estimate_failure(learner, str(parameters.get("config_hash") or ""))
        if estimate_failure:
            return False, f"formal_group_C_{estimate_failure}"
    return True, "formal_integration_passed"


def _rag_integration_passed(run_dir: Path, summary: dict[str, Any], parameters: dict[str, Any], *,
                            evidence_options: dict[str, bool],
                            max_output_tokens: int = MAX_OUTPUT_TOKENS) -> tuple[bool, str]:
    if int(summary.get("observed_cells") or 0) != 40:
        return False, f"rag_matrix_incomplete:{summary.get('observed_cells', 0)}/40"
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("groups") != ["C"] or manifest.get("planned_cases") != 40:
        return False, "rag_manifest_matrix_mismatch"
    generation_limit = manifest.get("generation_limit") or {}
    if (generation_limit.get("max_output_tokens") != max_output_tokens
            or generation_limit.get("max_retries") != 0):
        return False, "rag_manifest_generation_configuration_mismatch"
    if manifest.get("bkt_config_hash") != parameters.get("config_hash"):
        return False, "rag_manifest_bkt_hash_mismatch"
    cells = [json.loads(path.read_text(encoding="utf-8"))
             for path in sorted((run_dir / "cells").glob("*.json"))]
    if len(cells) != 40:
        return False, "rag_cell_artifacts_incomplete"
    if len({str(cell.get("session_id") or "") for cell in cells}) != 40 or len(
            {str(cell.get("learner_id") or "") for cell in cells}) != 40:
        return False, "rag_session_or_learner_isolation_mismatch"
    total_calls = 0
    for cell in cells:
        if cell.get("group") != "C" or cell.get("application_status") != "completed":
            return False, "rag_application_terminal_failed"
        evaluation = str(cell.get("evaluation_status") or "unknown")
        if cell.get("status") != "completed":
            if evaluation not in {"completed", "not_applicable"}:
                return False, f"rag_generation_not_complete:{evaluation}"
            return False, "rag_cell_terminal_failed"
        calls = int(cell.get("model_calls") or 0)
        total_calls += calls
        if calls not in {0, 1}:
            return False, "rag_cell_multiple_provider_calls"
        diagnostic = cell.get("provider_diagnostics") if isinstance(cell.get("provider_diagnostics"), dict) else {}
        requests = diagnostic.get("requests") if isinstance(diagnostic.get("requests"), list) else []
        outcomes = diagnostic.get("outcomes") if isinstance(diagnostic.get("outcomes"), list) else []
        if diagnostic.get("request_count") != calls or len(requests) != calls:
            return False, "rag_provider_trace_count_mismatch"
        if calls and (requests[0].get("provider_profile") != PROVIDER_PROFILE or requests[0].get("model") != MODEL):
            return False, "rag_provider_model_mismatch"
        frozen = cell.get("frozen_config") or {}
        sampling = frozen.get("sampling") or {}
        if (frozen.get("m3_evidence_options") != evidence_options
                or frozen.get("bkt_config_hash") != parameters.get("config_hash")
                or sampling.get("thinking_enabled") is not False
                or sampling.get("require_explicit_thinking_mode") is not True
                or sampling.get("max_output_tokens") != max_output_tokens):
            return False, "rag_frozen_configuration_mismatch"
        if evidence_options == {"retrieval_enabled": False, "evidence_constraint": True}:
            if calls or cell.get("evaluation_status") != "not_applicable" or cell.get(
                    "no_generation_reason") != "insufficient_evidence":
                return False, "rag_no_retrieval_with_constraint_must_make_zero_calls"
        elif cell.get("evaluation_status") == "completed":
            if (calls != 1 or len(outcomes) != 1 or outcomes[0].get("status") != "completed"
                    or outcomes[0].get("finish_reason") != "stop"
                    or not str(cell.get("response_text") or "").strip()):
                return False, "rag_generation_trace_incomplete"
        elif evaluation == "not_applicable":
            if calls or cell.get("no_generation_reason") not in {
                    "insufficient_evidence", "deterministic_policy_fallback", "policy_selected_no_generation"}:
                return False, "rag_no_generation_reason_missing_or_unknown"
        else:
            return False, f"rag_generation_not_complete:{cell.get('evaluation_status')}"
        observations = cell.get("bkt_observations") or []
        estimate = cell.get("learner_estimate") or {}
        if (len(observations) != 3 or any(not row.get("eligible") for row in observations)
                or _group_c_learner_estimate_failure(
                    estimate, str(parameters.get("config_hash") or ""))):
            return False, "rag_group_c_bkt_snapshot_mismatch"
    if total_calls != int(summary.get("token_usage", {}).get("model_calls") or 0) or total_calls > 40:
        return False, "rag_provider_request_budget_or_count_mismatch"
    return True, "rag_integration_passed"


def _refresh_existing_reports(campaign_id: str, report_path: Path) -> None:
    relative = report_path.relative_to(PROJECT / "docs" / "experiments").as_posix()
    update = ("\n\n## 2026-09-27 后续批次\n\n"
              f"A/B/C 真实模型实验与 BKT 开发校准的最新独立批次见[本次报告]({relative})。"
              "既有运行的分母、原始结果和结论边界均保留；新旧批次不合并。\n")
    for path in (PROJECT / "docs" / "experiments" / "M3-live-pilot.md",
                 PROJECT / "docs" / "experiments" / "M1-M3-实验报告.md"):
        text = path.read_text(encoding="utf-8")
        marker = f"<!-- latest-m3-abc:{campaign_id} -->"
        if marker not in text:
            path.write_text(text.rstrip() + update + marker + "\n", encoding="utf-8")


def _write_provider_budget_report(path: Path, budget_state: dict[str, Any], *,
                                  preflight_requests: int, formal_requests: int = 0,
                                  rag_condition_requests: dict[str, int] | None = None,
                                  provider_network_mode: str | None = None,
                                  status: str, stop_reason: str = "",
                                  unresolved_reservations_after: int = 0) -> dict[str, Any]:
    rag = {str(key): int(value) for key, value in (rag_condition_requests or {}).items()}
    rag_requests = sum(rag.values())
    actual = int(preflight_requests) + int(formal_requests) + rag_requests
    before = int(budget_state["provider_calls_before_campaign"])
    unresolved_before = int(budget_state.get("unresolved_provider_request_reservation") or 0)
    unresolved_after = max(0, int(unresolved_reservations_after))
    new_unresolved = max(0, unresolved_after - unresolved_before)
    after = before + actual + new_unresolved
    artifact = {
        "schema_version": "deepprof-provider-request-budget-v1",
        "authorized_total_provider_requests": APPROVED_TOTAL_REQUEST_BUDGET,
        "experiment_config_sha256": _experiment_config_hash(
            provider_network_mode=provider_network_mode),
        "provider_requests_before_campaign": before,
        "provider_requests_after_campaign": after,
        "unresolved_request_reservation_before_campaign": unresolved_before,
        "unresolved_request_reservation_after_campaign": unresolved_after,
        "remaining_before_campaign": int(budget_state["remaining_provider_requests"]),
        "remaining_after_campaign": max(0, APPROVED_TOTAL_REQUEST_BUDGET - after),
        "preflight_rounds_before_campaign": int(budget_state["preflight_rounds_before_campaign"]),
        "preflight_rounds_used": int(budget_state["preflight_rounds_before_campaign"]) + (1 if preflight_requests else 0),
        "authorized_new_preflight_request_ceiling": min(
            2, max(0, MAX_AUTHORIZED_PREFLIGHT_ROUNDS
                  - int(budget_state["preflight_rounds_before_campaign"]))) * PREFLIGHT_BUDGET,
        "reserved_maximum": {"preflight": PREFLIGHT_BUDGET, "formal_ab": MAIN_BUDGET,
                             "rag_ablation": RAG_BUDGET},
        "actual_provider_requests": actual,
        "preflight": int(preflight_requests),
        "formal_ab": int(formal_requests),
        "rag_ablation": rag_requests,
        "rag_conditions": rag,
        "status": status,
        "stop_reason": stop_reason,
    }
    path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return artifact


def _raw_run_progress(run_dir: Path) -> dict[str, int]:
    cells_dir = run_dir / "cells"
    pending_dir = run_dir / "pending"
    cells = {path.stem: json.loads(path.read_text(encoding="utf-8"))
             for path in cells_dir.glob("*.json")} if cells_dir.is_dir() else {}
    actual = sum(max(0, int(row.get("model_calls") or 0)) for row in cells.values())
    reserved = 0
    if pending_dir.is_dir():
        for path in pending_dir.glob("*.json"):
            if path.stem in cells:
                continue
            pending = json.loads(path.read_text(encoding="utf-8"))
            reserved += max(0, int(pending.get("request_budget_reservation") or 0))
    return {"actual": actual, "unresolved_reservation": reserved, "used_or_reserved": actual + reserved}


def _find_raw_run(raw_research_root: Path, run_id: str) -> Path:
    matches = list(raw_research_root.glob(f"*/runs/{run_id}/manifest.json"))
    if len(matches) != 1:
        raise RuntimeError("raw_run_artifact_missing_or_ambiguous")
    return matches[0].parent


def _selected_course_parameters() -> dict[str, Any]:
    selected = INITIAL_PARAMETERS.to_dict()
    selected["selection_status"] = "course_default_retained_teacher_review_pending"
    return {**selected, "config_hash": _parameters_hash(selected)}


def _validate_campaign_resume_budget(research_root: Path, raw_research_root: Path, *,
                                     campaign_id: str, docs_root: Path,
                                     preflight_run_id: str) -> dict[str, Any]:
    state = _provider_budget_state(research_root, raw_research_root)
    if state["provider_calls"] > APPROVED_TOTAL_REQUEST_BUDGET:
        raise RuntimeError("cumulative_provider_request_budget_exceeded_before_resume")
    preflight_raw = _find_raw_run(raw_research_root, preflight_run_id)
    preflight_summary_path = preflight_raw / "summary.json"
    remaining_for_campaign = 0
    if preflight_summary_path.is_file():
        preflight_summary = json.loads(preflight_summary_path.read_text(encoding="utf-8"))
        passed, _ = _preflight_passed(preflight_raw, preflight_summary)
    else:
        passed = False
        progress = _raw_run_progress(preflight_raw)
        remaining_for_campaign = max(0, PREFLIGHT_BUDGET - progress["used_or_reserved"])
    if passed:
        main_id = f"{campaign_id}-main"
        main_dir = raw_research_root / campaign_id / "runs" / main_id
        main_summary_path = main_dir / "summary.json"
        if main_summary_path.is_file():
            main_summary = json.loads(main_summary_path.read_text(encoding="utf-8"))
            main_passed, _ = _main_integration_passed(main_dir, main_summary, _selected_course_parameters())
        else:
            main_passed = False
            progress = _raw_run_progress(main_dir)
            remaining_for_campaign += max(0, MAIN_BUDGET - progress["used_or_reserved"])
        if main_passed:
                for condition, _ in RAG_CONDITIONS:
                    rag_id = f"{campaign_id}-rag-{condition}"
                    rag_dir = raw_research_root / campaign_id / "runs" / rag_id
                    if (rag_dir / "summary.json").is_file():
                        continue
                    progress = _raw_run_progress(rag_dir)
                    request_budget = RAG_CONDITION_REQUEST_BUDGETS[condition]
                    remaining_for_campaign += max(0, request_budget - progress["used_or_reserved"])
        elif main_summary_path.is_file():
            # A completed formal matrix that failed a frozen gate terminates
            # this campaign; it does not reserve or start a RAG phase.
            remaining_for_campaign = 0
        else:
            remaining_for_campaign += RAG_BUDGET
    remaining_total = APPROVED_TOTAL_REQUEST_BUDGET - state["provider_calls"]
    if remaining_total < remaining_for_campaign:
        raise RuntimeError(f"provider_request_budget_insufficient_on_resume:{remaining_total}<{remaining_for_campaign}")
    return {"provider_calls_before_campaign": state["provider_calls"],
        "remaining_provider_requests": remaining_total,
        "unresolved_provider_request_reservation": state["unresolved_provider_request_reservation"],
        "preflight_rounds_before_campaign": len(state["preflight_runs"]),
        "required_campaign_reservation": remaining_for_campaign,
        "preflight_fix_description": "", "resume_campaign": True}


def run_campaign(*, skip_tests: bool = False, preflight_fix: str | None = None,
                 direct_provider_network: bool | None = None,
                 approved_preflight_run_id: str = "",
                 resume_campaign_id: str = "") -> dict[str, Any]:
    if direct_provider_network is None:
        direct_provider_network = DEFAULT_PROVIDER_NETWORK_MODE == "direct"
    network_mode = "direct" if direct_provider_network else "inherited"
    resuming = bool(resume_campaign_id)
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    campaign_id = resume_campaign_id or f"m3-abc-bkt-{now}-{uuid.uuid4().hex[:6]}"
    if not re.fullmatch(r"m3-abc-bkt-[A-Za-z0-9T-]{8,48}", campaign_id):
        raise ValueError("invalid_campaign_id")
    research_root = PROJECT / "docs" / "experiments" / "m3-abc-research"
    raw_research_root = SOURCE_HOME / "experiments" / "m3-abc-research"
    docs_root = research_root / campaign_id
    raw_root = raw_research_root / campaign_id
    if resuming:
        if not docs_root.is_dir() or not raw_root.is_dir():
            raise FileNotFoundError("campaign_artifacts_missing_for_resume")
        campaign_state_path = docs_root / "campaign-state.json"
        campaign_state = json.loads(campaign_state_path.read_text(encoding="utf-8"))
        if campaign_state.get("campaign_id") != campaign_id:
            raise RuntimeError("campaign_resume_id_mismatch")
        frozen_config = json.loads((docs_root / "experiment-config.json").read_text(encoding="utf-8"))
        if frozen_config.get("config_sha256") != _experiment_config_hash(
                provider_network_mode=network_mode):
            raise RuntimeError("resume_config_mismatch:experiment-config")
        approved_preflight_run_id = str(campaign_state.get("approved_preflight_run_id") or "")
        preflight_run_id = str(campaign_state.get("preflight_run_id") or "")
        if not preflight_run_id:
            raise RuntimeError("campaign_preflight_identity_missing")
        budget_state = _validate_campaign_resume_budget(research_root, raw_research_root,
            campaign_id=campaign_id, docs_root=docs_root, preflight_run_id=preflight_run_id)
        baseline = campaign_state.get("budget_baseline") or {}
        budget_state.update({
            "provider_calls_before_campaign": int(baseline.get("provider_calls_before_campaign",
                budget_state["provider_calls_before_campaign"])),
            "preflight_rounds_before_campaign": int(baseline.get("preflight_rounds_before_campaign",
                budget_state["preflight_rounds_before_campaign"])),
            "unresolved_provider_request_reservation": int(baseline.get(
                "unresolved_provider_request_reservation", budget_state["unresolved_provider_request_reservation"])),
        })
        campaign_state.update({"status": "resuming", "last_resumed_at": datetime.now(timezone.utc).isoformat()})
        _write_json(campaign_state_path, campaign_state)
    else:
        budget_state = _validate_campaign_budget(research_root, raw_root=raw_research_root,
                                                 preflight_fix=preflight_fix)
        preflight_run_id = approved_preflight_run_id or f"{campaign_id}-preflight"
        campaign_state = {"schema_version": "deepprof-m3-abc-campaign-state-v1",
            "campaign_id": campaign_id, "status": "initialized",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "approved_preflight_run_id": approved_preflight_run_id,
            "preflight_run_id": preflight_run_id,
            "experiment_config_sha256": _experiment_config_hash(
                provider_network_mode=network_mode),
            "budget_baseline": {"provider_calls_before_campaign": budget_state["provider_calls_before_campaign"],
                "preflight_rounds_before_campaign": budget_state["preflight_rounds_before_campaign"],
                "unresolved_provider_request_reservation": budget_state[
                    "unresolved_provider_request_reservation"]}}
        docs_root.mkdir(parents=True, exist_ok=False)
        raw_root.mkdir(parents=True, exist_ok=False)
        _write_experiment_config(docs_root / "experiment-config.json",
                                 provider_network_mode=network_mode)
        _write_json(docs_root / "campaign-state.json", campaign_state)
        _write_json(raw_research_root / "latest-campaign.json", {
            "campaign_id": campaign_id, "updated_at": campaign_state["created_at"],
            "resume_hint": f"--resume-campaign-id {campaign_id}"})
    runtime_home = raw_root / "runtime"
    run_root = raw_root / "runs"
    pytest_root = raw_root / "pytest-temp"
    raw_root.mkdir(parents=True, exist_ok=True)
    run_root.mkdir(parents=True, exist_ok=True)
    print(f"M3 campaign {campaign_id} {'resuming' if resuming else 'initialized'}", flush=True)
    if skip_tests:
        raise ValueError("--skip-tests is disabled for an experiment that may promote product defaults")

    test_command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                    f"--basetemp={pytest_root}"]
    print("Running full Python regression before BKT calibration…", flush=True)
    tests = subprocess.run(test_command, cwd=PROJECT, check=False)
    if tests.returncode != 0:
        raise RuntimeError(f"python_regression_failed_exit_{tests.returncode}; live requests were not started")

    calibration_dir = docs_root / "bkt-calibration"
    calibration = run_calibration(calibration_dir, compatible_tests_passed=True, promote=True)
    candidate_path = calibration_dir / "bkt-candidate-parameters.json"
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    # The calibration here is based on constructed developer fixtures. Keep the
    # product's frozen course defaults even when those fixtures pass research metrics.
    selected_parameters = INITIAL_PARAMETERS.to_dict()
    selected_parameters["selection_status"] = "course_default_retained_teacher_review_pending"
    selected_parameters = {**selected_parameters,
                           "config_hash": _parameters_hash(selected_parameters)}
    selected_path = docs_root / "bkt-selected-parameters.json"
    selected_path.write_text(json.dumps(selected_parameters, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    index_summary = _validate_source_index()
    verified_provider_capabilities = _copy_runtime_configuration(runtime_home)
    runtime_home.mkdir(parents=True, exist_ok=True)
    database_path = runtime_home / "sessions.sqlite"
    port = _free_loopback_port()
    api_url = f"http://127.0.0.1:{port}"
    env, removed_proxy_environment = _campaign_environment(
        direct_provider_network=direct_provider_network)
    env.update({"DEEPPROF_HOME": str(runtime_home), "DEEPPROF_SQLITE_PATH": str(database_path),
                "DEEPPROF_API_HOST": "127.0.0.1", "DEEPPROF_API_PORT": str(port),
                "DEEPPROF_LLM_MAX_TOKENS": str(MAX_OUTPUT_TOKENS),
                "DEEPPROF_LLM_MAX_RETRIES": "0", "DEEPPROF_STREAM_INCLUDE_USAGE": "true"})
    environment_keys = ("DEEPPROF_HOME", "DEEPPROF_SQLITE_PATH", "DEEPPROF_API_HOST",
                        "DEEPPROF_API_PORT", "DEEPPROF_LLM_MAX_TOKENS",
                        "DEEPPROF_LLM_MAX_RETRIES", "DEEPPROF_STREAM_INCLUDE_USAGE")
    previous_environment = {key: os.environ.get(key) for key in environment_keys}
    logfile = raw_root / "gateway.log"
    log = logfile.open("ab")
    popen_kwargs: dict[str, Any] = {"cwd": PROJECT, "env": env, "stdout": log, "stderr": subprocess.STDOUT}
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    gateway = None
    preflight_summary = None
    main_summary = None
    preflight_calls = 0
    formal_calls = 0
    rag_summaries: dict[str, dict[str, Any]] = {}
    rag_exports: dict[str, dict[str, Any]] = {}
    stopped_reason = ""
    promotion_applied = False
    try:
        gateway = subprocess.Popen([sys.executable, "-m", "uvicorn", "api.app:app", "--host", "127.0.0.1",
                                    "--port", str(port), "--log-level", "warning"], **popen_kwargs)
        os.environ.update({key: env[key] for key in environment_keys})
        campaign_state.update({"status": "gateway_starting", "active_phase": "preflight",
            "experiment_config_sha256": _experiment_config_hash(
                provider_network_mode=network_mode)})
        _write_json(docs_root / "campaign-state.json", campaign_state)
        health = _wait_gateway(gateway, api_url, logfile)
        if health.get("status") != "ok":
            raise RuntimeError("isolated_gateway_health_check_failed")
        (docs_root / "source-validation.json").write_text(json.dumps({
            "course_id": "ds.c_language.v1", "provider_profile": PROVIDER_PROFILE, "model": MODEL,
            "source_index": index_summary, "max_output_tokens": MAX_OUTPUT_TOKENS,
            "temperature": 0.3, "max_retries": 0, "authorized_preflight_requests": PREFLIGHT_BUDGET,
            "authorized_main_requests": MAIN_BUDGET, "authorized_rag_ablation_requests": RAG_BUDGET,
            "authorized_total_provider_requests": APPROVED_TOTAL_REQUEST_BUDGET,
            "single_campaign_reserved_upper_bound": (2 * PREFLIGHT_BUDGET + MAIN_BUDGET + RAG_BUDGET),
            "provider_network": {
                "mode": "direct" if direct_provider_network else "inherited",
                "removed_proxy_environment_variable_names": removed_proxy_environment,
            },
            "provider_capabilities_verified_for_isolated_run": verified_provider_capabilities,
            "preflight_run_id": preflight_run_id,
            "experiment_config_sha256": _experiment_config_hash(
                provider_network_mode=network_mode),
            **budget_state,
            "compatible_python_regression": "passed",
            "bkt_research_metrics_eligible_on_constructed_fixtures": calibration["gates"]["research_metrics_eligible"],
            "curriculum_parameter_upgrade_allowed": False,
            "curriculum_parameter_upgrade_reason": "constructed fixtures are not course data; teacher approval pending"},
            ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        if approved_preflight_run_id:
            approved_record = _preflight_run_record(research_root, raw_research_root,
                                                    approved_preflight_run_id)
            if not approved_record or approved_record["status"] != "completed":
                raise RuntimeError("approved_preflight_not_in_cumulative_ledger_or_not_complete")
            preflight_raw = _find_raw_run(raw_research_root, approved_preflight_run_id)
            preflight_summary = json.loads((preflight_raw / "summary.json").read_text(encoding="utf-8"))
            passed, reason = _preflight_passed(preflight_raw, preflight_summary)
            if not passed:
                raise RuntimeError(f"approved_preflight_failed_gate:{reason}")
            checkpoint_path = preflight_raw.parents[1] / "preflight-budget.json"
            if checkpoint_path.is_file():
                checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                if checkpoint.get("experiment_config_sha256") != _experiment_config_hash(
                        provider_network_mode=network_mode):
                    raise RuntimeError("approved_preflight_config_hash_mismatch")
                budget_state = {**budget_state,
                    "provider_calls_before_campaign": int(checkpoint["provider_calls_before_preflight"]),
                    "remaining_provider_requests": APPROVED_TOTAL_REQUEST_BUDGET
                        - int(checkpoint["provider_calls_before_preflight"]),
                    "preflight_rounds_before_campaign": int(checkpoint["preflight_rounds_before_preflight"]),
                    "unresolved_provider_request_reservation": int(
                        checkpoint.get("unresolved_request_reservation_before_preflight") or 0)}
        else:
            preflight_raw = run_root / preflight_run_id
            if (preflight_raw / "summary.json").is_file():
                preflight_summary = json.loads((preflight_raw / "summary.json").read_text(encoding="utf-8"))
            else:
                preflight_summary = run_live(api_url=api_url, run_id=preflight_run_id, output_root=run_root,
                    library_index_from=SOURCE_DATABASE, case_count=len(PREFLIGHT_CASES), case_ids=PREFLIGHT_CASES,
                    phase="preflight", provider_profile=PROVIDER_PROFILE, model=MODEL,
                    max_output_tokens=MAX_OUTPUT_TOKENS, request_budget=PREFLIGHT_BUDGET,
                    bkt_snapshot=selected_parameters, resume=(preflight_raw / "manifest.json").is_file())
        preflight_export = export_run_bundle(preflight_raw, docs_root / "preflight")
        if _provider_budget_state(research_root, raw_research_root)["provider_calls"] > APPROVED_TOTAL_REQUEST_BUDGET:
            raise RuntimeError("cumulative_provider_request_budget_exceeded_after_preflight")
        preflight_passed, stopped_reason = _preflight_passed(preflight_raw, preflight_summary)
        campaign_state.update({"status": "preflight_passed" if preflight_passed else "stopped_after_preflight",
            "active_phase": "formal_ab" if preflight_passed else "completed",
            "preflight_passed": preflight_passed})
        _write_json(docs_root / "campaign-state.json", campaign_state)
        if not preflight_passed:
            preflight_calls = int((preflight_summary.get("token_usage") or {}).get("model_calls") or 0)
            budget_after = _provider_budget_state(research_root, raw_research_root)
            _write_provider_budget_report(docs_root / "provider-request-budget.json", budget_state,
                preflight_requests=preflight_calls, provider_network_mode=network_mode,
                status="stopped_after_preflight", stop_reason=stopped_reason,
                unresolved_reservations_after=budget_after["unresolved_provider_request_reservation"])
            report_path = build_report(docs_root, campaign_id=campaign_id, preflight=preflight_export, main=None,
                calibration=calibration, selected_parameters=selected_parameters,
                promotion_applied=False, stopped_reason=stopped_reason)
            _refresh_existing_reports(campaign_id, report_path)
            return {"campaign_id": campaign_id, "status": "stopped_after_preflight",
                    "reason": stopped_reason, "preflight": preflight_export,
                    "promotion_applied": False, "report": str(report_path)}

        main_id = f"{campaign_id}-main"
        main_raw = run_root / main_id
        if (main_raw / "summary.json").is_file():
            main_summary = json.loads((main_raw / "summary.json").read_text(encoding="utf-8"))
        else:
            main_summary = run_live(api_url=api_url, run_id=main_id, output_root=run_root,
                library_index_from=SOURCE_DATABASE,
                supersedes_run_id=str(preflight_summary.get("run_id") or approved_preflight_run_id), case_count=40,
                phase="main", provider_profile=PROVIDER_PROFILE, model=MODEL,
                max_output_tokens=MAX_OUTPUT_TOKENS, request_budget=MAIN_BUDGET,
                bkt_snapshot=selected_parameters, resume=(main_raw / "manifest.json").is_file())
        main_export = export_run_bundle(main_raw, docs_root / "main")
        main_cells = [json.loads(path.read_text(encoding="utf-8"))
                      for path in sorted((main_raw / "cells").glob("main-*.json"))]
        rating_paths = _score_templates(main_raw, main_cells) if len(main_cells) == 120 else []
        _write_json(raw_root / "blind-rating-status.json", {
            "status": "pending_human_review" if rating_paths else "not_created_incomplete_formal_matrix",
            "rater_count": len(rating_paths),
            "dimensions": ["accuracy", "clarity", "coherence", "engagement", "naturalness",
                "personalization_relevance", "action_appropriateness", "answer_leakage", "citation_support",
                "uncertainty_handling", "citation_evidence_sufficient"],
            "rating_packet_paths": [Path(path).relative_to(main_raw).as_posix() for path in rating_paths]})
        if _provider_budget_state(research_root, raw_research_root)["provider_calls"] > APPROVED_TOTAL_REQUEST_BUDGET:
            raise RuntimeError("cumulative_provider_request_budget_exceeded_after_formal_matrix")
        integration_passed, integration_reason = _main_integration_passed(main_raw, main_summary, selected_parameters)
        if calibration["gates"]["curriculum_promotion_eligible"] and integration_passed:
            _activate_default(candidate, calibration_dir / "bkt-calibration.json")
            promotion_applied = True
        else:
            stopped_reason = integration_reason if not integration_passed else "curriculum_data_and_teacher_approval_required"

        if not integration_passed:
            campaign_state.update({"status": "stopped_after_formal_matrix", "active_phase": "completed",
                "formal_matrix_passed": False, "stop_reason": stopped_reason})
            _write_json(docs_root / "campaign-state.json", campaign_state)
            preflight_calls = int((preflight_summary.get("token_usage") or {}).get("model_calls") or 0)
            formal_calls = int((main_summary.get("token_usage") or {}).get("model_calls") or 0)
            budget_after = _provider_budget_state(research_root, raw_research_root)
            _write_provider_budget_report(docs_root / "provider-request-budget.json", budget_state,
                preflight_requests=preflight_calls, formal_requests=formal_calls,
                provider_network_mode=network_mode,
                status="stopped_after_formal_matrix", stop_reason=stopped_reason,
                unresolved_reservations_after=budget_after["unresolved_provider_request_reservation"])
            report_path = build_report(docs_root, campaign_id=campaign_id, preflight=preflight_export,
                main=main_export, calibration=calibration, selected_parameters=selected_parameters,
                promotion_applied=False, stopped_reason=stopped_reason)
            _refresh_existing_reports(campaign_id, report_path)
            return {"campaign_id": campaign_id, "status": "stopped_after_formal_matrix",
                "reason": stopped_reason, "preflight": preflight_export, "main": main_export,
                "promotion_applied": False, "report": str(report_path)}

        campaign_state.update({"status": "formal_matrix_passed", "active_phase": "rag_ablation",
            "formal_matrix_passed": True})
        _write_json(docs_root / "campaign-state.json", campaign_state)
        for condition, evidence_options in RAG_CONDITIONS:
            rag_id = f"{campaign_id}-rag-{condition}"
            rag_raw = run_root / rag_id
            campaign_state.update({"active_phase": f"rag:{condition}"})
            _write_json(docs_root / "campaign-state.json", campaign_state)
            if (rag_raw / "summary.json").is_file():
                rag_summaries[condition] = json.loads((rag_raw / "summary.json").read_text(encoding="utf-8"))
            else:
                rag_summaries[condition] = run_live(api_url=api_url, run_id=rag_id,
                    output_root=run_root, library_index_from=None, case_count=40,
                    phase=f"rag-{condition}", provider_profile=PROVIDER_PROFILE, model=MODEL,
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                    request_budget=RAG_CONDITION_REQUEST_BUDGETS[condition],
                    bkt_snapshot=selected_parameters, groups=("C",),
                    m3_evidence_options=evidence_options, resume=(rag_raw / "manifest.json").is_file())
            rag_exports[condition] = export_run_bundle(rag_raw, docs_root / "rag-ablation" / condition)
            if _provider_budget_state(research_root, raw_research_root)["provider_calls"] > APPROVED_TOTAL_REQUEST_BUDGET:
                raise RuntimeError("cumulative_provider_request_budget_exceeded_during_rag_ablation")
            rag_passed, rag_reason = _rag_integration_passed(rag_raw, rag_summaries[condition],
                selected_parameters, evidence_options=evidence_options)
            if not rag_passed:
                stopped_reason = rag_reason
                campaign_state.update({"status": "stopped_after_rag_guard", "active_phase": condition,
                    "stop_reason": rag_reason})
                _write_json(docs_root / "campaign-state.json", campaign_state)
                raise RuntimeError(f"rag_integration_failed:{rag_reason}")
        preflight_calls = int((preflight_summary.get("token_usage") or {}).get("model_calls") or 0)
        formal_calls = int((main_summary.get("token_usage") or {}).get("model_calls") or 0)
        rag_calls = {key: int((row.get("token_usage") or {}).get("model_calls") or 0)
                     for key, row in rag_summaries.items()}
        total_requests = preflight_calls + formal_calls + sum(rag_calls.values())
        if total_requests > APPROVED_TOTAL_REQUEST_BUDGET:
            raise RuntimeError("campaign_provider_request_budget_exceeded")
        budget_after = _provider_budget_state(research_root, raw_research_root)
        _write_provider_budget_report(docs_root / "provider-request-budget.json", budget_state,
            preflight_requests=preflight_calls, formal_requests=formal_calls,
            rag_condition_requests=rag_calls, provider_network_mode=network_mode,
            status=str(main_summary.get("status") or "unknown"),
            stop_reason=stopped_reason,
            unresolved_reservations_after=budget_after["unresolved_provider_request_reservation"])
        report_path = build_report(docs_root, campaign_id=campaign_id, preflight=preflight_export, main=main_export,
            calibration=calibration, selected_parameters=selected_parameters, rag=rag_exports,
            promotion_applied=promotion_applied, stopped_reason=stopped_reason)
        _refresh_existing_reports(campaign_id, report_path)
        campaign_state.update({"status": "completed", "active_phase": "completed",
            "finished_at": datetime.now(timezone.utc).isoformat(), "rag_conditions_completed": list(rag_summaries)})
        _write_json(docs_root / "campaign-state.json", campaign_state)
        return {"campaign_id": campaign_id, "status": main_summary.get("status"),
                "preflight": preflight_export, "main": main_export,
                "promotion_applied": promotion_applied, "promotion_reason": stopped_reason,
                "report": str(report_path)}
    except BaseException as exc:
        if docs_root.exists():
            if campaign_state.get("status") != "stopped_after_rag_guard":
                campaign_state["status"] = "failed_or_interrupted"
            campaign_state.update({"active_phase": "stopped", "stop_reason_code": type(exc).__name__})
            _write_json(docs_root / "campaign-state.json", campaign_state)
            (docs_root / "campaign-stop.json").write_text(json.dumps({
                "status": "stopped", "reason_code": type(exc).__name__,
                "preflight_run_id": preflight_summary.get("run_id") if preflight_summary else None,
                "formal_run_id": main_summary.get("run_id") if main_summary else None},
                ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            if "budget_state" in locals():
                if preflight_summary is None:
                    try:
                        prior_preflight_raw = _find_raw_run(raw_research_root, preflight_run_id)
                        if (prior_preflight_raw / "summary.json").is_file():
                            preflight_summary = json.loads((prior_preflight_raw / "summary.json").read_text(encoding="utf-8"))
                        else:
                            preflight_calls = _raw_run_progress(prior_preflight_raw)["actual"]
                    except (OSError, RuntimeError, json.JSONDecodeError):
                        pass
                preflight_calls = max(preflight_calls, int(
                    (preflight_summary or {}).get("token_usage", {}).get("model_calls") or 0))
                formal_calls = int((main_summary or {}).get("token_usage", {}).get("model_calls") or 0)
                if not formal_calls:
                    formal_run_dir = run_root / f"{campaign_id}-main"
                    if formal_run_dir.is_dir():
                        formal_calls = _raw_run_progress(formal_run_dir)["actual"]
                rag_calls: dict[str, int] = {}
                for condition, _ in RAG_CONDITIONS:
                    rag_summary = rag_summaries.get(condition)
                    rag_dir = run_root / f"{campaign_id}-rag-{condition}"
                    if rag_summary is None and (rag_dir / "summary.json").is_file():
                        rag_summary = json.loads((rag_dir / "summary.json").read_text(encoding="utf-8"))
                    rag_calls[condition] = int((rag_summary or {}).get("token_usage", {}).get("model_calls") or 0)
                    if not rag_calls[condition] and rag_dir.is_dir():
                        rag_calls[condition] = _raw_run_progress(rag_dir)["actual"]
                budget_after = _provider_budget_state(research_root, raw_research_root)
                _write_provider_budget_report(docs_root / "provider-request-budget.json", budget_state,
                    preflight_requests=preflight_calls, formal_requests=formal_calls,
                    rag_condition_requests=rag_calls, provider_network_mode=network_mode,
                    status="stopped_after_exception",
                    stop_reason=f"campaign_stopped_{type(exc).__name__}",
                    unresolved_reservations_after=budget_after["unresolved_provider_request_reservation"])
            if "calibration" in locals() and not (docs_root / "M3-A-B-C-真实模型与BKT校准报告.md").exists():
                preflight_doc = json.loads((docs_root / "preflight" / "summary.json").read_text(encoding="utf-8")) \
                    if (docs_root / "preflight" / "summary.json").is_file() else None
                main_doc = json.loads((docs_root / "main" / "summary.json").read_text(encoding="utf-8")) \
                    if (docs_root / "main" / "summary.json").is_file() else None
                rag_docs = {path.parent.name: json.loads(path.read_text(encoding="utf-8"))
                    for path in docs_root.glob("rag-ablation/*/summary.json")}
                failure_label = f"campaign_stopped_{type(exc).__name__}"
                build_report(docs_root, campaign_id=campaign_id, preflight=preflight_doc, main=main_doc,
                    calibration=calibration, selected_parameters=selected_parameters,
                    promotion_applied=False, stopped_reason=failure_label, rag=rag_docs or None)
        raise
    finally:
        cleanup_status = "gateway_not_started" if gateway is None else (
            "gateway_stopped" if gateway.poll() is not None else "gateway_stop_unconfirmed")
        try:
            if gateway is not None:
                if gateway.poll() is None:
                    gateway.terminate()
                try:
                    gateway.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    gateway.kill()
                    gateway.wait(timeout=3)
                cleanup_status = "gateway_stopped" if gateway.poll() is not None else "gateway_stop_unconfirmed"
        finally:
            log.close()
            for key, value in previous_environment.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        campaign_state["isolation_cleanup"] = cleanup_status
        campaign_state["updated_at"] = datetime.now(timezone.utc).isoformat()
        if campaign_state.get("status") in {"gateway_starting", "resuming", "preflight_passed",
                "formal_matrix_passed"}:
            campaign_state["status"] = "interrupted_or_failed"
        if gateway is not None and cleanup_status != "gateway_stopped":
            campaign_state["status"] = "gateway_cleanup_failed"
        _write_json(docs_root / "campaign-state.json", campaign_state)
        if gateway is not None and cleanup_status != "gateway_stopped":
            raise RuntimeError("isolated_gateway_cleanup_unconfirmed")


def _parameters_hash(snapshot: dict[str, Any]) -> str:
    from models.learner.bkt import BKTParameters
    return BKTParameters(p_l0=float(snapshot["p_l0"]), p_t=float(snapshot["p_t"]),
        p_g=float(snapshot["p_g"]), p_s=float(snapshot["p_s"]), source=str(snapshot.get("source") or "snapshot"),
        model_version=str(snapshot.get("model_version") or "bkt-four-parameter-dev-v1"),
        fitted=bool(snapshot.get("fitted", False)), teacher_review=str(snapshot.get("teacher_review") or "pending")).config_hash


def _activate_default(candidate: dict[str, Any], calibration_path: Path) -> None:
    report = json.loads(calibration_path.read_text(encoding="utf-8"))
    gates = report.get("gates") or {}
    if not (gates.get("curriculum_promotion_eligible") and
            candidate.get("teacher_review") == "approved" and gates.get("compatible_tests_passed")):
        raise RuntimeError("bkt_promotion_gates_not_met")
    source = PROJECT / "models" / "learner" / "bkt.py"
    contents = source.read_text(encoding="utf-8")
    marker = "DEFAULT_PARAMETERS = INITIAL_PARAMETERS"
    if contents.count(marker) != 1:
        raise RuntimeError("default_parameter_activation_target_changed")
    replacement = (
        "DEFAULT_PARAMETERS = BKTParameters("
        f"p_l0={float(candidate['p_l0']):.2f}, p_t={float(candidate['p_t']):.2f}, "
        f"p_g={float(candidate['p_g']):.2f}, p_s={float(candidate['p_s']):.2f}, "
        f"source={str(candidate['source'])!r}, model_version={str(candidate['model_version'])!r}, "
        "fitted=True, teacher_review='pending')"
    )
    temporary = source.with_suffix(".py.bkt-tmp")
    temporary.write_text(contents.replace(marker, replacement, 1), encoding="utf-8")
    temporary.replace(source)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--preflight-fix", help="Describe the located and implemented fix before a second preflight")
    parser.add_argument("--preflight-only", action="store_true",
                        help="Run one bounded preflight into the isolated raw evidence directory")
    parser.add_argument("--approved-preflight-run-id", default="",
                        help="Reuse the successful autoresearch preflight for formal-matrix gating")
    parser.add_argument("--resume-campaign-id", default="",
                        help="Resume an existing isolated campaign without resending completed or pending cells")
    parser.add_argument("--direct-provider-network", action="store_true", default=None,
                        help="Remove inherited HTTP(S)_PROXY variables from the isolated campaign only")
    args = parser.parse_args()
    try:
        if args.preflight_only:
            if args.skip_tests or args.approved_preflight_run_id or args.resume_campaign_id:
                raise ValueError("preflight_only_does_not_accept_campaign_options")
            result = run_preflight_measurement(direct_provider_network=args.direct_provider_network)
        else:
            result = run_campaign(skip_tests=args.skip_tests, preflight_fix=args.preflight_fix,
                direct_provider_network=args.direct_provider_network,
                approved_preflight_run_id=args.approved_preflight_run_id,
                resume_campaign_id=args.resume_campaign_id)
    except Exception as exc:
        # Provider/HTTP exception messages can contain unscreened response text
        # or endpoint details. Keep terminal output to an allowlisted class.
        print(f"M3 campaign stopped: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
