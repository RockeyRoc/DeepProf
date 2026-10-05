"""Run the explicitly bounded, uniformly repaired M3 RAG ablation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from graph.education.bindings import ACTION_BINDINGS
from scripts import run_m3_abc_research as campaign
from scripts.build_m3_abc_experiment_report import export_run_bundle
from scripts import run_m3_live_pilot as live_pilot
from scripts.run_m3_live_pilot import run as run_live


PROJECT = Path(__file__).resolve().parents[1]
SOURCE_HOME = PROJECT.parent / "开发者测试.deepprof"
BASE_CAMPAIGN = "m3-abc-bkt-20260928T134542Z-a216df"
OUTPUT_TOKENS = 8192
MAX_NEW_REQUESTS = 124
REPAIR_PREFLIGHT_CASES = ("DSDEV-034", "DSDEV-001", "DSDEV-016", "DSDEV-018")
RAG_PRECHECK = {"retrieval_enabled": True, "evidence_constraint": True}
DEEPSEEK_CAPABILITY_DOC = "https://api-docs.deepseek.com/api/create-chat-completion/"
DEEPSEEK_MODELS_DOC = "https://api-docs.deepseek.com/api/list-models/"
FROZEN_SOURCE_DATABASE = (SOURCE_HOME / "experiments" / "m3-abc-research" / BASE_CAMPAIGN
                          / "runtime" / "sessions.sqlite")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _active_deepseek_profile() -> dict[str, Any]:
    profiles_path = SOURCE_HOME / "providers.json"
    raw = _read_json(profiles_path)
    candidates = [profile for profile in raw.get("profiles", [])
                  if profile.get("enabled") is True
                  and profile.get("vendor_id") == "deepseek"
                  and profile.get("default_model") == campaign.MODEL]
    config_path = SOURCE_HOME / "config.json"
    if config_path.is_file():
        config = _read_json(config_path)
        role = (config.get("provider_roles") or {}).get("tutor.default") or {}
        configured_id = role.get("profile_id")
        matching = [profile for profile in candidates if profile.get("profile_id") == configured_id]
        if matching:
            candidates = matching
    if len(candidates) != 1:
        raise RuntimeError("active_deepseek_profile_missing_or_ambiguous")
    profile = candidates[0]
    safe = {key: profile.get(key) for key in (
        "profile_id", "vendor_id", "base_url", "api_mode", "protocol", "default_model",
        "enabled", "capabilities")}
    safe["model_capabilities"] = (profile.get("model_capabilities") or {}).get(campaign.MODEL) or {}
    return {"profile_id": str(profile["profile_id"]),
            "profile_sha256": hashlib.sha256(json.dumps(
                safe, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")).hexdigest(),
            "base_url": str(profile.get("base_url") or ""),
            "model_capabilities": safe["model_capabilities"]}


def _probe_model_metadata(profile_id: str) -> dict[str, Any]:
    """Check credentials, connectivity, and DeepSeek's live model metadata without generation."""
    import importlib.util

    profiles = _read_json(SOURCE_HOME / "providers.json").get("profiles", [])
    profile = next((row for row in profiles if row.get("profile_id") == profile_id), None)
    if not profile or profile.get("vendor_id") != "deepseek" or profile.get("base_url") != "https://api.deepseek.com":
        raise RuntimeError("deepseek_profile_binding_invalid")
    spec = importlib.util.spec_from_file_location(
        "_m3_repair_secret_probe", PROJECT / "runtime" / "providers" / "windows_secrets.py")
    if not spec or not spec.loader:
        raise RuntimeError("windows_secret_store_unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    key = module.WindowsDpapiSecretStore(SOURCE_HOME / "credentials" / "dpapi.json").get(
        str(profile.get("api_key_ref") or ""))
    if not key:
        raise RuntimeError("deepseek_credential_not_available_no_generation_request_sent")
    request = urllib.request.Request(
        "https://api.deepseek.com/models", headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            if response.status != 200:
                raise RuntimeError(f"deepseek_model_metadata_http_{response.status}")
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"deepseek_model_metadata_unreachable:{type(exc).__name__}") from exc
    models = {str(row.get("id")): row for row in payload.get("data", []) if isinstance(row, dict)}
    model = models.get(campaign.MODEL)
    if not model:
        raise RuntimeError("deepseek_flash_not_listed_by_metadata_endpoint")
    max_output = int(model.get("max_output_tokens") or 0)
    if max_output < OUTPUT_TOKENS:
        raise RuntimeError("deepseek_flash_live_output_limit_below_8192")
    capabilities = model.get("api_capabilities") or {}
    chat = capabilities.get("chat_completions") or {}
    if chat and chat.get("thinking_toggle") is False:
        raise RuntimeError("deepseek_chat_completions_thinking_disable_not_supported")
    return {"status": "verified", "http_status": 200, "model_id": campaign.MODEL,
            "max_output_tokens": max_output, "context_window": model.get("context_window"),
            "endpoint": "https://api.deepseek.com/models", "documentation": DEEPSEEK_MODELS_DOC,
            "generation_requests": 0,
            "chat_completions_generation_parameters": {"max_tokens": OUTPUT_TOKENS,
                "thinking": {"type": "disabled"}, "reasoning_effort": None}}


def _validate_frozen_source_index(expected_sha256: str) -> dict[str, Any]:
    if not FROZEN_SOURCE_DATABASE.is_file():
        raise FileNotFoundError("frozen_source_index_database_missing")
    snapshot = live_pilot._library_index_snapshot(FROZEN_SOURCE_DATABASE)
    if snapshot.get("sha256") != expected_sha256:
        raise RuntimeError("frozen_source_index_fingerprint_mismatch")
    if snapshot.get("active_course_textbooks") != 1 or snapshot.get("chunk_count", 0) < 1:
        raise RuntimeError("frozen_source_index_incomplete")
    return {**snapshot, "database_path": str(FROZEN_SOURCE_DATABASE),
            "database_sha256": _sha256(FROZEN_SOURCE_DATABASE)}


def _validate_new_batch_budget(raw_root: Path, needed: int) -> dict[str, Any]:
    ledger = campaign._provider_budget_state(
        PROJECT / "docs" / "experiments" / "m3-abc-research", SOURCE_HOME / "experiments" / "m3-abc-research")
    repair_reserved = _remaining_repair_reservation(raw_root)
    progress = _repair_request_progress(raw_root)
    if progress["actual"] + progress["reserved"] > MAX_NEW_REQUESTS:
        raise RuntimeError("rag_repair_request_ceiling_exceeded")
    if ledger["provider_calls"] + ledger["unresolved_provider_request_reservation"] + max(needed, repair_reserved) > campaign.APPROVED_TOTAL_REQUEST_BUDGET:
        raise RuntimeError("provider_request_budget_insufficient_for_repair")
    repair_runs = raw_root / "runs"
    if repair_runs.exists():
        for pending_path in repair_runs.glob("*/pending/*.json"):
            pending = _read_json(pending_path)
            cell_path = pending_path.parent.parent / "cells" / pending_path.name
            if not cell_path.is_file():
                raise RuntimeError("repair_pending_cell_without_terminal_artifact_must_not_be_replayed")
            if pending.get("state") not in {"terminal", "cancelled"}:
                raise RuntimeError("repair_batch_has_unresolved_provider_request")
    return ledger


def _repair_request_progress(raw_root: Path) -> dict[str, int]:
    runs_root = raw_root / "runs"
    run_dirs = list(runs_root.glob(f"{raw_root.name}-preflight*")) if runs_root.exists() else []
    run_dirs.extend(runs_root / f"{raw_root.name}-rag-{condition}" for condition, _ in campaign.RAG_CONDITIONS)
    actual = reserved = 0
    for run_dir in run_dirs:
        if run_dir.is_dir():
            progress = campaign._raw_run_progress(run_dir)
            actual += progress["actual"]
            reserved += progress["unresolved_reservation"]
    return {"actual": actual, "reserved": reserved}


def _remaining_repair_reservation(raw_root: Path) -> int:
    runs_root = raw_root / "runs"
    preflight_dirs = list(runs_root.glob(f"{raw_root.name}-preflight*")) if runs_root.exists() else []
    if any((preflight_dir / "summary.json").is_file() for preflight_dir in preflight_dirs):
        remaining = 0
    else:
        preflight_used = preflight_reserved = 0
        for preflight_dir in preflight_dirs:
            progress = campaign._raw_run_progress(preflight_dir)
            preflight_used += progress["actual"]
            preflight_reserved += progress["unresolved_reservation"]
        remaining = max(0, 4 - preflight_used - preflight_reserved)
    for condition, _ in campaign.RAG_CONDITIONS:
        run_dir = runs_root / f"{raw_root.name}-rag-{condition}"
        if (run_dir / "summary.json").is_file():
            continue
        progress = campaign._raw_run_progress(run_dir)
        remaining += max(0, campaign.RAG_CONDITION_REQUEST_BUDGETS[condition] - progress["used_or_reserved"])
    if remaining > MAX_NEW_REQUESTS:
        raise RuntimeError("rag_repair_reserved_request_ceiling_exceeded")
    return remaining


def _validate_frozen_config(frozen: dict[str, Any]) -> None:
    saved = dict(frozen)
    declared_hash = saved.pop("config_sha256", None)
    actual_hash = hashlib.sha256(json.dumps(saved, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    if declared_hash != actual_hash:
        raise RuntimeError("rag_repair_frozen_config_hash_mismatch")


def _attest_cell_configuration(raw_run: Path, manifest: dict[str, Any], cell_path: Path,
                               cell: dict[str, Any], *, options: dict[str, bool],
                               parameters: dict[str, Any], repair_config_sha256: str | None) -> None:
    """Validate each terminal cell against its immutable run manifest and retain a hash sidecar.

    Older cells created before the session metadata carried the experiment hash have no cell-level
    value. They are accepted only when all observable frozen settings match the hashed run manifest;
    a sidecar ties the exact cell bytes to that batch hash without rewriting the raw response.
    """
    frozen = cell.get("frozen_config") or {}
    expected = {
        "provider_profile": manifest.get("provider_profile"),
        "model": manifest.get("model"),
        "bkt_config_hash": parameters.get("config_hash"),
        "m3_evidence_options": options,
    }
    for key, value in expected.items():
        if frozen.get(key) != value:
            raise RuntimeError(f"rag_repair_saved_cell_{key}_mismatch")
    if frozen.get("sampling", {}).get("max_output_tokens") != OUTPUT_TOKENS:
        raise RuntimeError("rag_repair_saved_cell_output_limit_mismatch")
    if frozen.get("prompt_sha256") != manifest.get("prompt_sha256"):
        raise RuntimeError("rag_repair_saved_cell_prompt_mismatch")
    if manifest.get("retrieval", {}).get("index_sha256") != _source_lineage(
            PROJECT / "docs" / "experiments" / "m3-abc-research",
            SOURCE_HOME / "experiments" / "m3-abc-research")["expected_index_sha256"]:
        raise RuntimeError("rag_repair_saved_manifest_index_mismatch")
    saved_hash = frozen.get("experiment_config_sha256")
    if saved_hash not in {None, repair_config_sha256}:
        raise RuntimeError("rag_repair_saved_cell_experiment_hash_mismatch")
    if not repair_config_sha256:
        raise RuntimeError("rag_repair_cell_experiment_hash_missing")
    if saved_hash == repair_config_sha256:
        return

    raw_root = raw_run.parent.parent
    sidecar = raw_root / "cell-config-attestations.json"
    payload = _read_json(sidecar) if sidecar.is_file() else {
        "schema_version": "deepprof-rag-repair-cell-config-attestations-v1",
        "repair_config_sha256": repair_config_sha256,
        "attestations": {},
    }
    if payload.get("repair_config_sha256") != repair_config_sha256:
        raise RuntimeError("rag_repair_attestation_batch_hash_mismatch")
    cell_rel = cell_path.relative_to(raw_root).as_posix()
    cell_digest = _sha256(cell_path)
    existing = (payload.get("attestations") or {}).get(cell_rel)
    attestation = {
        "cell_sha256": cell_digest,
        "run_manifest_sha256": _sha256(raw_run / "manifest.json"),
        "repair_config_sha256": repair_config_sha256,
        "verified_fields": sorted(expected) + ["sampling.max_output_tokens", "prompt_sha256"],
        "status": "manifest_attested_missing_cell_hash",
        "raw_cell_not_modified": True,
    }
    if existing and existing != attestation:
        raise RuntimeError("rag_repair_existing_cell_attestation_mismatch")
    payload.setdefault("attestations", {})[cell_rel] = attestation
    _write_json(sidecar, payload)


def _preflight_passed(run_dir: Path, summary: dict[str, Any], *,
                      expected_case_id: str | None = None) -> tuple[bool, str]:
    cells_dir = run_dir / "cells"
    cells = [_read_json(path) for path in sorted(cells_dir.glob("*.json"))]
    expected = {expected_case_id} if expected_case_id else set(REPAIR_PREFLIGHT_CASES)
    if len(cells) != len(expected) or {str(cell.get("case_id") or "") for cell in cells} != expected:
        return False, f"repair_preflight_matrix_incomplete:{len(cells)}/{len(expected)}"
    if any(cell.get("group") != "C" or cell.get("application_status") != "completed"
           for cell in cells):
        return False, "repair_preflight_application_failed"
    for cell in cells:
        if cell.get("frozen_config", {}).get("sampling", {}).get("max_output_tokens") != OUTPUT_TOKENS:
            return False, "repair_preflight_output_limit_mismatch"
        if cell.get("status") != "completed":
            return False, f"repair_preflight_cell_failed:{cell.get('case_id')}"
        if cell.get("evaluation_status") == "not_applicable":
            if cell.get("model_calls") or cell.get("no_generation_reason") not in {
                    "insufficient_evidence", "deterministic_policy_fallback", "policy_selected_no_generation"}:
                return False, "repair_preflight_unexpected_no_generation"
            continue
        diagnostic = cell.get("provider_diagnostics") or {}
        outcomes = diagnostic.get("outcomes") or []
        if (cell.get("evaluation_status") != "completed" or cell.get("model_calls") != 1
                or len(outcomes) != 1 or outcomes[0].get("status") != "completed"
                or outcomes[0].get("finish_reason") != "stop"
                or not str(cell.get("response_text") or "").strip()):
            return False, f"repair_preflight_generation_incomplete:{cell.get('case_id')}"
    if int(summary.get("observed_cells") or 0) != len(expected):
        return False, "repair_preflight_summary_mismatch"
    return True, "repair_preflight_passed"


def _source_lineage(research_root: Path, raw_research_root: Path) -> dict[str, Any]:
    base_docs = research_root / BASE_CAMPAIGN
    base_raw = raw_research_root / BASE_CAMPAIGN
    base_manifest = _read_json(base_raw / "runs" / f"{BASE_CAMPAIGN}-main" / "manifest.json")
    rag_manifest_path = (base_raw / "runs" / f"{BASE_CAMPAIGN}-rag-retrieval-on_constraint-on" / "manifest.json")
    rag_manifest = _read_json(rag_manifest_path)
    expected_index = str((rag_manifest.get("retrieval") or {}).get("index_sha256") or "")
    if len(expected_index) != 64:
        raise RuntimeError("base_rag_index_fingerprint_missing")
    return {
        "base_campaign_id": BASE_CAMPAIGN,
        "base_formal_run_id": f"{BASE_CAMPAIGN}-main",
        "base_formal_manifest_sha256": _sha256(base_raw / "runs" / f"{BASE_CAMPAIGN}-main" / "manifest.json"),
        "base_rag_run_id": str(rag_manifest.get("run_id") or ""),
        "base_rag_manifest_sha256": _sha256(rag_manifest_path),
        "expected_index_sha256": expected_index,
        "bkt_config_hash": str(base_manifest.get("bkt_config_hash") or ""),
        "course_id": "ds.c_language.v1",
        "model": campaign.MODEL,
        "provider_profile": campaign.PROVIDER_PROFILE,
        "prompt_sha256": str(base_manifest.get("prompt_sha256") or ""),
        "strategy_bindings_sha256": str(base_manifest.get("strategy_bindings_sha256") or ""),
    }


def _run(run_id: str, options: dict[str, bool], budget: int, *, api_url: str, run_root: Path,
         parameters: dict[str, Any], expected_index_sha256: str,
         provider_profile: str | None = None,
         repair_config_sha256: str | None = None,
         prompt_sha256_override: str | None = None,
         preflight_summary: dict[str, Any] | None = None) -> dict[str, Any]:
    selected_profile = provider_profile or campaign.PROVIDER_PROFILE
    raw = run_root / run_id
    manifest_path = raw / "manifest.json"
    if manifest_path.is_file():
        manifest = _read_json(manifest_path)
        expected_phase = "rag-repair-preflight" if preflight_summary is None else (
            f"rag-repair-{run_id.rsplit('-rag-', 1)[-1]}")
        retrieval = manifest.get("retrieval") or {}
        if (manifest.get("run_id") != run_id or manifest.get("phase") != expected_phase
                or manifest.get("provider_profile") != selected_profile
                or manifest.get("model") != campaign.MODEL
                or manifest.get("groups") != ["C"]
                or manifest.get("planned_cases") != (1 if preflight_summary is None else 40)
                or manifest.get("sampling", {}).get("max_output_tokens") != OUTPUT_TOKENS
                or manifest.get("generation_limit") != {"max_output_tokens": OUTPUT_TOKENS, "max_retries": 0}
                or manifest.get("bkt_config_hash") != parameters.get("config_hash")
                or manifest.get("experiment_config_sha256") != repair_config_sha256
                or manifest.get("call_budget", {}).get("authorized_provider_requests") != budget
                or manifest.get("strategy_bindings_sha256") != hashlib.sha256(json.dumps(ACTION_BINDINGS,
                    ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
                or manifest.get("supersedes_run_id") != (str(preflight_summary.get("run_id") or "")
                    if preflight_summary else None)
                or retrieval.get("index_sha256") != expected_index_sha256
                or {key: retrieval.get(key) for key in options} != options):
            raise RuntimeError("rag_repair_saved_run_configuration_mismatch")
        expected_prompt_hash = prompt_sha256_override or campaign._prompt_fingerprint()
        if manifest.get("prompt_sha256") != expected_prompt_hash:
            raise RuntimeError("rag_repair_saved_run_prompt_mismatch")
    if (raw / "summary.json").is_file():
        summary = _read_json(raw / "summary.json")
        if not manifest_path.is_file():
            raise RuntimeError("rag_repair_summary_without_manifest_must_not_be_reused")
        expected_cells = 1 if preflight_summary is None else 40
        if int(summary.get("observed_cells") or 0) != expected_cells:
            raise RuntimeError("rag_repair_saved_summary_matrix_incomplete")
    else:
        summary = run_live(
            api_url=api_url,
            run_id=run_id,
            output_root=run_root,
            library_index_from=(FROZEN_SOURCE_DATABASE
                                if preflight_summary is None and not manifest_path.is_file() else None),
            supersedes_run_id=(str(preflight_summary.get("run_id") or "") if preflight_summary else None),
            case_count=1 if preflight_summary is None else 40,
            case_ids=[run_id.removeprefix(f"{run_root.parent.name}-preflight-")] if preflight_summary is None else None,
            phase="rag-repair-preflight" if preflight_summary is None else f"rag-repair-{run_id.rsplit('-rag-', 1)[-1]}",
            provider_profile=selected_profile,
            model=campaign.MODEL,
            max_output_tokens=OUTPUT_TOKENS,
            request_budget=budget,
            bkt_snapshot=parameters,
            groups=("C",),
            m3_evidence_options=options,
            stop_on_failed_cell=(preflight_summary is None),
            experiment_config_sha256=repair_config_sha256,
            allow_source_fingerprint_change_on_resume=bool((raw / "manifest.json").is_file()),
            prompt_sha256_override=prompt_sha256_override,
            resume=(raw / "manifest.json").is_file(),
        )
    if manifest_path.is_file():
        manifest = _read_json(manifest_path)
        for path in (raw / "cells").glob("*.json"):
            cell = _read_json(path)
            _attest_cell_configuration(raw, manifest, path, cell, options=options,
                                        parameters=parameters,
                                        repair_config_sha256=repair_config_sha256)
            pending_path = raw / "pending" / path.name
            if pending_path.is_file() and _read_json(pending_path).get("state") not in {"terminal", "cancelled"}:
                raise RuntimeError("rag_repair_saved_cell_has_unresolved_request")
        if manifest.get("generation_limit", {}).get("max_retries") != 0:
            raise RuntimeError("rag_repair_saved_run_retry_configuration_mismatch")
    return summary


def run_repair(*, repair_id: str = "", resume_repair_id: str = "",
               direct_provider_network: bool = False) -> dict[str, Any]:
    if repair_id and resume_repair_id:
        raise ValueError("choose_new_or_resume_repair_not_both")
    research_root = PROJECT / "docs" / "experiments" / "m3-abc-research"
    raw_research_root = SOURCE_HOME / "experiments" / "m3-abc-research"
    repair_id = resume_repair_id or repair_id or (
        "m3-abc-ragfix-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6])
    if not re.fullmatch(r"m3-abc-ragfix-[A-Za-z0-9T-]{8,64}", repair_id):
        raise ValueError("invalid_repair_id")
    docs_root = research_root / repair_id
    raw_root = raw_research_root / repair_id
    run_root = raw_root / "runs"
    is_resume = bool(resume_repair_id)
    if is_resume:
        if not docs_root.is_dir() or not raw_root.is_dir():
            raise FileNotFoundError("rag_repair_artifacts_missing_for_resume")
        prior_state = _read_json(docs_root / "repair-state.json") if (docs_root / "repair-state.json").is_file() else {}
        if str(prior_state.get("status") or "").startswith("stopped_"):
            raise RuntimeError("rag_repair_terminally_stopped_batch_cannot_resume")
        frozen = _read_json(docs_root / "repair-config.json")
    else:
        if docs_root.exists() or raw_root.exists():
            raise FileExistsError("rag_repair_id_already_exists")
        docs_root.mkdir(parents=True)
        raw_root.mkdir(parents=True)
        active_profile = _active_deepseek_profile()
        frozen = {"schema_version": "deepprof-m3-rag-repair-v2", "repair_id": repair_id,
                  "created_at": datetime.now(timezone.utc).isoformat(), "status": "initialized",
                  "base_lineage": _source_lineage(research_root, raw_research_root),
                  "provider_profile": active_profile["profile_id"],
                  "provider_profile_sha256": active_profile["profile_sha256"],
                  "api_mode": "chat_completions", "thinking_parameter": "thinking.type",
                  "thinking_enabled": False,
                  "capability_documentation": DEEPSEEK_CAPABILITY_DOC,
                  "model_metadata_documentation": DEEPSEEK_MODELS_DOC,
                  "output_tokens": OUTPUT_TOKENS, "max_retries": 0, "provider_request_ceiling": MAX_NEW_REQUESTS,
                  "preflight_cases": list(REPAIR_PREFLIGHT_CASES), "conditions": [row[0] for row in campaign.RAG_CONDITIONS],
                  "condition_request_ceilings": campaign.RAG_CONDITION_REQUEST_BUDGETS,
                  "network_mode": "direct" if direct_provider_network else "inherited"}
        canonical = json.dumps(frozen, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        frozen["config_sha256"] = hashlib.sha256(canonical).hexdigest()
        _write_json(docs_root / "repair-config.json", frozen)
        _write_json(docs_root / "repair-state.json", {"repair_id": repair_id, "status": "initialized",
                    "active_phase": "preflight", "request_ceiling": MAX_NEW_REQUESTS})
    _validate_frozen_config(frozen)
    if frozen.get("output_tokens") != OUTPUT_TOKENS or frozen.get("max_retries") != 0:
        raise RuntimeError("rag_repair_frozen_generation_config_mismatch")
    if (frozen.get("api_mode") != "chat_completions"
            or frozen.get("thinking_parameter") != "thinking.type"
            or frozen.get("thinking_enabled") is not False):
        raise RuntimeError("rag_repair_thinking_configuration_mismatch")
    lineage = _source_lineage(research_root, raw_research_root)
    if lineage != frozen.get("base_lineage"):
        raise RuntimeError("rag_repair_base_lineage_changed")
    current_prompt_fingerprint = campaign._prompt_fingerprint()
    expected_prompt_fingerprint = str(lineage.get("prompt_sha256") or "")
    if current_prompt_fingerprint != expected_prompt_fingerprint:
        ledger = campaign._provider_budget_state(research_root, raw_research_root)
        progress = _repair_request_progress(raw_root)
        empty_preflight = {
            "run_id": f"{repair_id}-preflight-set", "status": "not_run",
            "observed_cells": 0, "sample": {"observed_cells": 0},
            "limits": {"actual_provider_requests": progress["actual"]},
            "token_usage": {"model_calls": progress["actual"]},
            "groups": {"C": {"evaluation_completed": 0, "evaluation_truncated_failed": 0,
                "evaluation_failed_other": 0, "evaluation_not_applicable": 0}},
        }
        reason = "frozen_prompt_fingerprint_mismatch_before_generation"
        _write_json(docs_root / "preflight-set-summary.json", empty_preflight)
        _write_json(docs_root / "provider-request-budget.json", {
            "schema_version": "deepprof-rag-repair-budget-v1", "base_campaign_id": BASE_CAMPAIGN,
            "repair_id": repair_id, "provider_calls_before_repair": ledger["provider_calls"] - progress["actual"],
            "provider_calls_at_stop": ledger["provider_calls"],
            "authorized_total_provider_requests": campaign.APPROVED_TOTAL_REQUEST_BUDGET,
            "repair_ceiling": MAX_NEW_REQUESTS, "repair_requests_used": progress["actual"],
            "repair_requests_remaining": max(0, MAX_NEW_REQUESTS - progress["actual"]),
            "unresolved_request_reservation": ledger["unresolved_provider_request_reservation"],
            "status": "stopped_before_generation", "generation_requests_sent_by_this_attempt": 0,
        })
        report_path = docs_root / "M3-RAG-修复消融报告.md"
        report_path.write_text(_build_report(repair_id, frozen, empty_preflight, [], ledger,
            stop_reason=reason), encoding="utf-8")
        _write_json(docs_root / "repair-state.json", {
            "repair_id": repair_id, "status": "stopped_prompt_fingerprint_mismatch",
            "active_phase": "stopped", "preflight_passed": False,
            "stop_reason_code": reason, "expected_prompt_sha256": expected_prompt_fingerprint,
            "current_runtime_prompt_fingerprint_sha256": current_prompt_fingerprint,
            "generation_requests_sent_by_this_attempt": 0, "completed_conditions": [],
            "report": str(report_path),
        })
        return {"repair_id": repair_id, "status": "stopped_before_generation",
                "reason": reason, "report": str(report_path)}
    active_profile = _active_deepseek_profile()
    if (active_profile["profile_id"] != frozen.get("provider_profile")
            or active_profile["profile_sha256"] != frozen.get("provider_profile_sha256")):
        raise RuntimeError("active_provider_configuration_changed_since_batch_freeze")

    before = _validate_new_batch_budget(raw_root, 0 if is_resume else MAX_NEW_REQUESTS)
    if before["unresolved_provider_request_reservation"]:
        raise RuntimeError("unresolved_provider_request_reservation_before_repair")
    metadata = _probe_model_metadata(str(frozen["provider_profile"]))
    verified_capabilities = campaign._copy_runtime_configuration(
        raw_root / "runtime", max_output_tokens=OUTPUT_TOKENS,
        profile_id=str(frozen["provider_profile"]), thinking_parameter="thinking.type")
    if verified_capabilities.get("api_mode") != "chat_completions":
        raise RuntimeError("deepseek_chat_completions_mode_not_bound")
    if verified_capabilities.get("max_output_tokens", 0) < OUTPUT_TOKENS:
        raise RuntimeError("provider_output_limit_not_verified_for_8192_tokens")
    source_index = _validate_frozen_source_index(lineage["expected_index_sha256"])
    runtime_home = raw_root / "runtime"
    runtime_home.mkdir(parents=True, exist_ok=True)
    database = runtime_home / "sessions.sqlite"
    port = campaign._free_loopback_port()
    api_url = f"http://127.0.0.1:{port}"
    env, removed_proxies = campaign._campaign_environment(direct_provider_network=direct_provider_network)
    env.update({"DEEPPROF_HOME": str(runtime_home), "DEEPPROF_SQLITE_PATH": str(database),
                "DEEPPROF_API_HOST": "127.0.0.1", "DEEPPROF_API_PORT": str(port),
                "DEEPPROF_LLM_MAX_TOKENS": str(OUTPUT_TOKENS), "DEEPPROF_LLM_MAX_RETRIES": "0",
                "DEEPPROF_STREAM_INCLUDE_USAGE": "true"})
    env_keys = ("DEEPPROF_HOME", "DEEPPROF_SQLITE_PATH", "DEEPPROF_API_HOST", "DEEPPROF_API_PORT",
                "DEEPPROF_LLM_MAX_TOKENS", "DEEPPROF_LLM_MAX_RETRIES", "DEEPPROF_STREAM_INCLUDE_USAGE")
    prior_env = {key: os.environ.get(key) for key in env_keys}
    logfile = (raw_root / "gateway.log").open("ab")
    popen_kwargs: dict[str, Any] = {"cwd": PROJECT, "env": env, "stdout": logfile, "stderr": subprocess.STDOUT}
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    gateway = subprocess.Popen([sys.executable, "-m", "uvicorn", "api.app:app", "--host", "127.0.0.1",
                                "--port", str(port), "--log-level", "warning"], **popen_kwargs)
    os.environ.update({key: env[key] for key in env_keys})
    results: dict[str, dict[str, Any]] = {}
    try:
        health = campaign._wait_gateway(gateway, api_url, logfile)
        if health.get("status") != "ok":
            raise RuntimeError("rag_repair_gateway_health_check_failed")
        _write_json(docs_root / "runtime-evidence.json", {
            "verified_provider_capabilities": verified_capabilities,
            "live_provider_model_metadata": metadata,
            "source_index": source_index,
            "removed_proxy_environment_variable_names": removed_proxies,
            "max_output_tokens_requested": OUTPUT_TOKENS,
            "max_retries": 0,
        })
        preflight_dirs: list[Path] = []
        preflight_export: dict[str, Any] = {
            "run_id": f"{repair_id}-preflight-set", "observed_cells": 0,
            "sample": {"observed_cells": 0},
            "limits": {"actual_provider_requests": 0}, "token_usage": {"model_calls": 0},
            "groups": {"C": {"evaluation_completed": 0, "evaluation_truncated_failed": 0,
                              "evaluation_failed_other": 0, "evaluation_not_applicable": 0}},
        }
        passed, reason = True, "repair_preflight_passed"
        for preflight_case in REPAIR_PREFLIGHT_CASES:
            preflight_id = f"{repair_id}-preflight-{preflight_case}"
            preflight_dir = run_root / preflight_id
            preflight_dirs.append(preflight_dir)
            one_case = _run(preflight_id, RAG_PRECHECK, 1, api_url=api_url, run_root=run_root,
                            parameters=campaign._selected_course_parameters(),
                            expected_index_sha256=lineage["expected_index_sha256"],
                            provider_profile=str(frozen["provider_profile"]),
                            repair_config_sha256=str(frozen["config_sha256"]),
                            prompt_sha256_override=str((frozen.get("base_lineage") or {}).get("prompt_sha256") or ""))
            export_run_bundle(preflight_dir, docs_root / "preflight" / preflight_case)
            passed, reason = _preflight_passed(preflight_dir, one_case, expected_case_id=preflight_case)
            preflight_export["observed_cells"] += int(one_case.get("observed_cells") or 0)
            preflight_export["sample"]["observed_cells"] = preflight_export["observed_cells"]
            preflight_export["limits"]["actual_provider_requests"] += int(
                (one_case.get("limits") or {}).get("actual_provider_requests") or 0)
            preflight_export["token_usage"]["model_calls"] += int(
                (one_case.get("token_usage") or {}).get("model_calls") or 0)
            one_group = (one_case.get("groups") or {}).get("C") or {}
            for metric in ("evaluation_completed", "evaluation_truncated_failed",
                           "evaluation_failed_other", "evaluation_not_applicable"):
                preflight_export["groups"]["C"][metric] += int(one_group.get(metric) or 0)
            if not passed:
                break
        preflight_id = str(preflight_export["run_id"])
        preflight_calls = sum(campaign._raw_run_progress(path)["actual"] for path in preflight_dirs)
        preflight_export["limits"]["actual_provider_requests"] = preflight_calls
        _write_json(docs_root / "preflight-set-summary.json", preflight_export)
        after_preflight = campaign._provider_budget_state(research_root, raw_research_root)
        _write_json(docs_root / "provider-request-budget.json", {
            "schema_version": "deepprof-rag-repair-budget-v1", "base_campaign_id": BASE_CAMPAIGN,
            "repair_id": repair_id, "provider_calls_before_repair": before["provider_calls"],
            "provider_calls_after_preflight": after_preflight["provider_calls"],
            "authorized_total_provider_requests": campaign.APPROVED_TOTAL_REQUEST_BUDGET,
            "repair_ceiling": MAX_NEW_REQUESTS, "repair_requests_used": preflight_calls,
            "repair_requests_remaining": MAX_NEW_REQUESTS - preflight_calls,
            "remaining_total_provider_requests": campaign.APPROVED_TOTAL_REQUEST_BUDGET - after_preflight["provider_calls"],
            "unresolved_request_reservation": after_preflight["unresolved_provider_request_reservation"],
            "preflight_requests": preflight_calls,
            "rag_condition_requests": {condition: 0 for condition, _ in campaign.RAG_CONDITIONS},
            "status": "preflight_passed" if passed else "stopped_after_preflight",
            "preflight_stop_reason": "" if passed else reason})
        _write_json(docs_root / "repair-state.json", {"repair_id": repair_id,
                    "status": "preflight_passed" if passed else "stopped_after_preflight",
                    "active_phase": "rag_ablation" if passed else "stopped", "preflight_passed": passed,
                    "stop_reason": reason, "preflight_run_id": preflight_id,
                    "preflight_provider_requests": preflight_calls,
                    "preflight_evaluation_run_status": preflight_export.get("evaluation_run_status")})
        if not passed:
            report_path = docs_root / "M3-RAG-修复消融报告.md"
            report_path.write_text(_build_report(repair_id, frozen, preflight_export, [], before,
                stop_reason=reason), encoding="utf-8")
            state = _read_json(docs_root / "repair-state.json")
            state["report"] = str(report_path)
            _write_json(docs_root / "repair-state.json", state)
            raise RuntimeError(f"rag_repair_preflight_failed:{reason}")

        actual_new = preflight_calls
        if actual_new > 4:
            raise RuntimeError("rag_repair_preflight_request_ceiling_exceeded")
        # The indexed materials and frozen experiment configuration are checked before the first 40-cell condition.
        for preflight_dir in preflight_dirs:
            observed_index = str((_read_json(preflight_dir / "manifest.json").get("retrieval") or {}).get("index_sha256") or "")
            if observed_index != lineage["expected_index_sha256"]:
                raise RuntimeError("rag_repair_library_index_changed_since_base_run")

        parameters = campaign._selected_course_parameters()
        for condition, evidence_options in campaign.RAG_CONDITIONS:
            _validate_new_batch_budget(raw_root, 0)
            phase_id = f"{repair_id}-rag-{condition}"
            _write_json(docs_root / "repair-state.json", {"repair_id": repair_id, "status": "running",
                        "active_phase": condition, "preflight_passed": True,
                        "request_ceiling": MAX_NEW_REQUESTS, "requests_used_so_far":
                            campaign._provider_budget_state(research_root, raw_research_root)["provider_calls"]
                            - before["provider_calls"]})
            result = _run(phase_id, evidence_options, campaign.RAG_CONDITION_REQUEST_BUDGETS[condition],
                          api_url=api_url, run_root=run_root, parameters=parameters,
                          expected_index_sha256=lineage["expected_index_sha256"],
                          provider_profile=str(frozen["provider_profile"]),
                          repair_config_sha256=str(frozen["config_sha256"]),
                          prompt_sha256_override=str((frozen.get("base_lineage") or {}).get("prompt_sha256") or ""),
                          preflight_summary=preflight_export)
            run_dir = run_root / phase_id
            passed, reason = campaign._rag_integration_passed(run_dir, result, parameters,
                evidence_options=evidence_options, max_output_tokens=OUTPUT_TOKENS)
            export = export_run_bundle(run_dir, docs_root / "rag-ablation" / condition)
            results[condition] = {"run_id": phase_id, "integration_passed": passed,
                                  "stop_reason": reason, "summary": export}
            used = preflight_calls + sum(
                campaign._raw_run_progress(run_root / str(row["run_id"]))["actual"]
                for row in results.values())
            if used > MAX_NEW_REQUESTS:
                raise RuntimeError("rag_repair_request_ceiling_exceeded")
            _write_json(docs_root / "provider-request-budget.json", {
                "schema_version": "deepprof-rag-repair-budget-v1", "base_campaign_id": BASE_CAMPAIGN,
                "provider_calls_before_repair": before["provider_calls"],
                "authorized_total_provider_requests": campaign.APPROVED_TOTAL_REQUEST_BUDGET,
                "repair_ceiling": MAX_NEW_REQUESTS, "repair_requests_used": used,
                "repair_requests_remaining": MAX_NEW_REQUESTS - used,
                "unresolved_request_reservation": campaign._provider_budget_state(
                    research_root, raw_research_root)["unresolved_provider_request_reservation"],
                "preflight_requests": preflight_calls,
                "rag_condition_requests": {name: campaign._raw_run_progress(run_root / str(row["run_id"]))["actual"]
                                            for name, row in results.items()},
                "status": "running" if passed else "stopped_after_condition_guard",
                "last_completed_condition": condition if passed else ""})
            if not passed:
                _write_json(docs_root / "repair-state.json", {"repair_id": repair_id,
                            "status": "stopped_after_rag_guard", "active_phase": condition,
                            "stop_reason": reason, "completed_conditions": list(results)})
                raise RuntimeError(f"rag_repair_integration_failed:{condition}:{reason}")

        condition_rows = []
        for condition, row in results.items():
            summary = row["summary"]
            condition_rows.append((condition, summary))
        report = _build_report(repair_id, frozen, preflight_export, condition_rows, before)
        report_path = docs_root / "M3-RAG-修复消融报告.md"
        report_path.write_text(report, encoding="utf-8")
        final_ledger = campaign._provider_budget_state(research_root, raw_research_root)
        used = preflight_calls + sum(campaign._raw_run_progress(run_root / str(row["run_id"]))["actual"]
                                     for row in results.values())
        _write_json(docs_root / "provider-request-budget.json", {
            "schema_version": "deepprof-rag-repair-budget-v1", "base_campaign_id": BASE_CAMPAIGN,
            "provider_calls_before_repair": before["provider_calls"], "provider_calls_after_repair": final_ledger["provider_calls"],
            "authorized_total_provider_requests": campaign.APPROVED_TOTAL_REQUEST_BUDGET,
            "repair_ceiling": MAX_NEW_REQUESTS, "repair_requests_used": used,
            "repair_requests_remaining": MAX_NEW_REQUESTS - used,
            "remaining_total_provider_requests": campaign.APPROVED_TOTAL_REQUEST_BUDGET - final_ledger["provider_calls"],
            "unresolved_request_reservation": final_ledger["unresolved_provider_request_reservation"],
            "preflight_requests": preflight_calls,
            "rag_condition_requests": {name: campaign._raw_run_progress(run_root / str(row["run_id"]))["actual"]
                                        for name, row in results.items()}, "status": "completed"})
        _write_json(docs_root / "repair-state.json", {"repair_id": repair_id, "status": "completed",
                    "active_phase": "completed", "preflight_passed": True,
                    "completed_conditions": list(results), "report": str(report_path),
                    "isolation_cleanup": "gateway_stopped"})
        return {"repair_id": repair_id, "status": "completed", "requests_used": used,
                "report": str(report_path), "conditions": list(results)}
    except BaseException as exc:
        if docs_root.is_dir():
            state_path = docs_root / "repair-state.json"
            state = _read_json(state_path) if state_path.is_file() else {}
            if state.get("status") not in {"stopped_after_rag_guard", "stopped_after_preflight"}:
                state.update({"repair_id": repair_id, "status": "failed_or_interrupted",
                              "active_phase": state.get("active_phase", "stopped"),
                              "stop_reason_code": type(exc).__name__, "stop_reason": str(exc),
                              "completed_conditions": list(results)})
                _write_json(state_path, state)
        raise
    finally:
        if gateway.poll() is None:
            gateway.terminate()
            try:
                gateway.wait(timeout=10)
            except subprocess.TimeoutExpired:
                gateway.kill()
                gateway.wait(timeout=10)
        logfile.close()
        for key in env_keys:
            if prior_env[key] is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prior_env[key]
        cleanup = "gateway_stopped" if gateway.poll() is not None else "gateway_cleanup_failed"
        state_path = docs_root / "repair-state.json"
        if state_path.is_file():
            state = _read_json(state_path)
            state["isolation_cleanup"] = cleanup
            _write_json(state_path, state)


def _build_report(repair_id: str, frozen: dict[str, Any], preflight: dict[str, Any],
                  conditions: list[tuple[str, dict[str, Any]]], before: dict[str, Any],
                  *, stop_reason: str = "") -> str:
    sample = preflight.get("sample") or {}
    preflight_cells = int(sample.get("observed_cells") or preflight.get("observed_cells") or 0)
    preflight_calls = int((preflight.get("limits") or {}).get("actual_provider_requests") or
                          (preflight.get("token_usage") or {}).get("model_calls") or 0)
    preflight_group = (preflight.get("groups") or {}).get("C") or {}
    preflight_completed = int(preflight_group.get("evaluation_completed") or 0)
    preflight_truncated = int(preflight_group.get("evaluation_truncated_failed") or 0)
    preflight_failed_other = int(preflight_group.get("evaluation_failed_other") or 0)
    preflight_no_generation = int(preflight_group.get("evaluation_not_applicable") or 0)
    lines = ["# M3 RAG 消融：8192-token 工程修复批次", "",
             f"修复批次：`{repair_id}`。基于 `{BASE_CAMPAIGN}`，原始证据和正式 A/B/C 120 格均保留。",
             "本报告只比较同一修复配置下的四个条件；与原 4096-token 条件不混算。", "",
             "## 冻结配置与预算", "",
             f"- 每格最大输出：{OUTPUT_TOKENS} tokens；重试：0。",
             f"- 修复前 Provider 总调用：{before['provider_calls']}；总授权上限：{campaign.APPROVED_TOTAL_REQUEST_BUDGET}。",
             f"- 本批次新增调用上限：{MAX_NEW_REQUESTS}（4 次预检＋最多 120 次条件调用）。",
             f"- 基线索引 SHA-256：`{frozen['base_lineage']['expected_index_sha256']}`。",
             f"- 预检：{preflight_cells}/4 格；实际调用：{preflight_calls}。", "",
             "## 预检门禁", "",
             "| 完整生成 | 截断 | 其他失败 | 规则性未调用 | Provider 请求 |", "|---:|---:|---:|---:|---:|",
             f"| {preflight_completed} | {preflight_truncated} | {preflight_failed_other} | {preflight_no_generation} | {preflight_calls} |", "",
             (f"预检失败：`{stop_reason}`；正式条件未启动。原始失败响应按失败保留。" if stop_reason else
              "预检通过，进入正式条件。"), "",
            "## 四条件结果", "", "| 条件 | 终态格 | 完整生成 | 截断 | 其他失败 | 规则性未调用 | Provider 请求 | 峰时估算 USD | 非峰时估算 USD |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for condition, summary in conditions:
        group = (summary.get("groups") or {}).get("C") or {}
        observed = int(group.get("observed_cells") or 0)
        complete = int(group.get("evaluation_completed") or 0)
        truncated = int(group.get("evaluation_truncated_failed") or 0)
        failed_other = int(group.get("evaluation_failed_other") or 0) + int(group.get("evaluation_unknown") or 0)
        calls = int(group.get("provider_requests") or 0)
        no_generation = int(group.get("evaluation_not_applicable") or 0)
        price = summary.get("price_estimate") or {}
        peak = price.get("peak_rate_usd", 0)
        offpeak = price.get("offpeak_rate_usd", 0)
        lines.append(f"| `{condition}` | {observed}/40 | {complete} | {truncated} | {failed_other} | {no_generation} | {calls} | {peak} | {offpeak} |")
    if not conditions:
        lines.append("| 未启动 | 0/40 | 0 | 0 | N/A | N/A | 0 | N/A | N/A |")
    lines += ["", "## 解释边界", "", "工程门禁确认调用、截断、配置和单格终态。它不构成教学质量结论。",
              "引用定位字段仍不证明语义支持；正式的逐主张引用审计另行报告。", "",
              f"配置哈希：`{frozen.get('config_sha256', '')}`。", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repair-id", default="", help="Create a new isolated repair batch")
    parser.add_argument("--resume-repair-id", default="", help="Resume the exact frozen repair batch")
    parser.add_argument("--direct-provider-network", action="store_true")
    args = parser.parse_args()
    try:
        result = run_repair(repair_id=args.repair_id, resume_repair_id=args.resume_repair_id,
                            direct_provider_network=args.direct_provider_network)
    except Exception as exc:
        print(f"M3 RAG repair stopped: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
