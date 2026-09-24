"""Run the frozen 40-case A/B acceptance suite against a configured live Provider.

The harness records its complete raw evidence locally under DEEPPROF_HOME. It
never substitutes a fake provider and never runs automatically at startup.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from source_fingerprint import repository_fingerprint
except ImportError:  # test/package import path
    from scripts.source_fingerprint import repository_fingerprint

ROOT = Path(__file__).resolve().parents[1]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def home() -> Path:
    return Path(os.environ.get("DEEPPROF_HOME", "~/.deepprof")).expanduser().resolve()


def request_json(base: str, path: str, *, method: str = "GET", payload: Any = None, timeout: float = 30) -> Any:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(base.rstrip("/") + path, data=body, method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise RuntimeError(f"HTTP {exc.code} {path}: {detail}") from exc


def git_fingerprint() -> dict[str, Any]:
    return repository_fingerprint(ROOT)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def append_line(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def safe_failure(events: list[dict[str, Any]], terminal: dict[str, Any] | None = None) -> dict[str, Any]:
    """Keep actionable error codes while excluding provider messages and payload text."""
    failure_events = [event for event in events if event.get("type") in {"agent.failed", "model.failed"}]
    for event in reversed(failure_events):
        error = (event.get("payload") or {}).get("error") or {}
        details = error.get("details") or {}
        kind = str(details.get("kind") or error.get("kind") or "")
        allowed = {
            "auth_failed", "region_or_permission_blocked", "rate_limited", "timeout",
            "connection_failed", "upstream_error", "not_found", "missing_credential",
            "capability_missing", "model_truncated", "empty_model_response",
        }
        reason_code = kind if kind in allowed else ""
        return {
            "event_type": str(event.get("type") or ""),
            "code": str(error.get("code") or "runtime_error"),
            "kind": kind or "unknown",
            "http_status": details.get("http_status"),
            "retryable": details.get("retryable"),
            "reason_code": reason_code or str(error.get("code") or "runtime_error"),
        }
    status = str(((terminal or {}).get("payload") or {}).get("status") or "unknown")
    return {"event_type": "agent.turn.completed", "code": f"turn_terminal_{status}",
            "kind": "unknown", "http_status": None, "retryable": False,
            "reason_code": f"turn_terminal_{status}"}


def turn_observation(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Return aggregate turn telemetry only; never copy model or student text."""
    usage = {key: 0 for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
    usage_observed = False
    model_calls = sum(event.get("type") == "model.requested" for event in events)
    actions: list[str] = []
    model_failures: list[dict[str, str]] = []
    safe_failure_kinds = {
        "auth_failed", "region_or_permission_blocked", "rate_limited", "timeout",
        "connection_failed", "upstream_error", "not_found", "missing_credential",
        "capability_missing", "model_truncated", "empty_model_response",
    }
    for event in events:
        event_type = event.get("type")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if event_type in {"model.completed", "model.failed"}:
            raw_usage = payload.get("usage") or {}
            if isinstance(raw_usage, dict):
                for key in usage:
                    value = raw_usage.get(key)
                    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                        usage[key] += int(value)
                        usage_observed = True
        if event_type in {"pedagogy.decision", "teaching.decision"}:
            action = payload.get("action")
            if action:
                actions.append(str(action))
        if event_type == "model.failed":
            error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            kind = str(error.get("kind") or "")
            code = str(error.get("code") or "provider_error")
            model_failures.append({
                "code": code if code in {"provider_error", "runtime_error", *safe_failure_kinds} else "provider_error",
                "kind": kind if kind in safe_failure_kinds else "unknown",
            })
    if not usage_observed and model_calls:
        usage_value: dict[str, int] = {}
    else:
        usage_value = usage
    return {
        "usage": usage_value,
        "model_calls": model_calls,
        "actions": actions,
        "model_failures": model_failures,
    }


def run(args: argparse.Namespace) -> int:
    if not args.live:
        raise RuntimeError("Pass --live to confirm a real Provider acceptance run.")
    base = args.api_url.rstrip("/")
    host = urllib.parse.urlsplit(base).hostname
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("Acceptance runs may only send fixture data to a loopback Gateway.")
    root = home() / "experiments"
    root.mkdir(parents=True, exist_ok=True)
    cases_path = ROOT / "evaluation" / "dev_cases.json"
    if not cases_path.is_file():
        raise RuntimeError("Frozen case file is absent; run evaluation.dev_cases first.")
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    if len(cases.get("cases", [])) != 40:
        raise RuntimeError("Frozen case suite must contain exactly 40 cases.")

    try:
        health = request_json(base, "/health")
        selection = request_json(base, "/providers/default")
        profiles = request_json(base, "/providers")
        profile = next((p for p in profiles if p.get("profile_id") == selection.get("profile_id")), None)
        if not profile or profile.get("protocol") not in {"openai_compatible", "native"} or not profile.get("has_secret"):
            raise RuntimeError("真实 Provider 凭据未配置；请先在 deepprof /login 中手动配置 API Key。")
        probe = request_json(base, f"/providers/{urllib.parse.quote(selection['profile_id'], safe='')}/probe",
                             method="POST", payload={"model": selection.get("model")}, timeout=90)
        if probe.get("status") != "ok":
            raise RuntimeError(f"Provider 探测失败：{probe.get('kind') or probe.get('status')}: {probe.get('message') or ''}")
    except Exception as exc:
        blocker = {"checked_at": now(), "status": "blocked_before_run", "reason": str(exc),
                   "fake_provider_used": False, "data_home": str(home())}
        write_json(root / "acceptance-blocker.json", blocker)
        print(f"验收未启动：{exc}", file=sys.stderr)
        return 2

    guard_path = root / "acceptance-once.json"
    prior_guard = None
    if guard_path.exists():
        prior_guard = json.loads(guard_path.read_text(encoding="utf-8"))
        if not args.repeat_after_fix:
            raise RuntimeError(f"整批验收已启动过：{prior_guard.get('run_id')}。若修复后有新构建，请传入 --repeat-after-fix 并说明修复版本。")
        if len(args.repeat_after_fix.strip()) < 8:
            raise RuntimeError("--repeat-after-fix must identify the repair/version (at least 8 characters).")

    run_id = "run_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
    run_dir = root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    policy_source = (ROOT / "graph" / "education" / "contracts.py").read_text(encoding="utf-8")
    sampling_source = (ROOT / "graph" / "education" / "policies" / "__init__.py").read_text(encoding="utf-8")
    policy_match = re.search(r'^POLICY_VERSION\s*=\s*["\']([^"\']+)', policy_source, re.M)
    temperature_match = re.search(r"^GENERATE_TEMPERATURE\s*=\s*([0-9.]+)", sampling_source, re.M)
    policy_version = policy_match.group(1) if policy_match else "unknown"
    temperature = float(temperature_match.group(1)) if temperature_match else None
    config = {
        "run_id": run_id, "started_at": now(), "status": "running", "case_version": cases["version"],
        "case_count": 40, "groups": ["A", "B"], "matrix_size": 80, "sample_type": "constructed_developer_fixture",
        "health": health, "provider_profile": selection.get("profile_id"), "model": selection.get("model"),
        "course_id": "ds.c_language.v1",
        "provider_probe": {k: v for k, v in probe.items() if k != "secret_env_var"},
        "question_bank_version": "", "policy_version": policy_version, "sampling": {"temperature": temperature},
        "retrieval": {"top_k": 5, "chunk_size": 800, "chunk_overlap": 120},
        "git": git_fingerprint(), "repeat_after_fix": args.repeat_after_fix or None,
        "fake_provider_used": False, "price_configured": False, "estimated_cost": None,
        "case_file_sha256": file_sha256(cases_path), "curriculum_manifest_sha256": file_sha256(ROOT / "data" / "courses" / "data_structures_c" / "manifest.json"),
    }
    try:
        bank = json.loads((home() / "course" / "question_bank.json").read_text(encoding="utf-8"))
        config["question_bank_version"] = str(bank.get("version") or "")
        config["question_bank_sha256"] = file_sha256(home() / "course" / "question_bank.json")
    except (OSError, json.JSONDecodeError):
        pass
    write_json(run_dir / "run.json", config)
    write_json(guard_path, {"run_id": run_id, "started_at": config["started_at"], "status": "running"})
    completed: list[dict[str, Any]] = []
    print(f"真实 A/B 验收开始：{run_id} · 40 案例 × A/B · {selection.get('profile_id')}/{selection.get('model')}", flush=True)

    for case_index, case in enumerate(cases["cases"]):
        for group in ("A", "B"):
            result: dict[str, Any] = {"run_id": run_id, "case_id": case["case_id"], "case_version": cases["version"],
                "category": case["category"], "group": group, "status": "blocked", "started_at": now(),
                "trace_ids": [], "turns": [], "sample_type": "constructed_developer_fixture"}
            try:
                created = request_json(base, "/commands", method="POST", payload={
                    "command_id": "cmd_" + uuid.uuid4().hex, "client_id": "acceptance", "surface": "cli",
                    "session_id": None, "learner_id": f"dev-{run_id}", "type": "session.new",
                    "payload": {"group": group, "course_id": "ds.c_language.v1", "title": f"{run_id} {case['case_id']} {group}"}})
                session_id = str(created.get("session_id") or "")
                if not session_id:
                    raise RuntimeError(f"session_create_failed: {created}")
                result["session_id"] = session_id
                session = request_json(base, f"/sessions/{urllib.parse.quote(session_id, safe='')}")
                experiment = {"provider_profile": session.get("provider_profile"), "model": session.get("model"),
                              "group": session.get("experiment_group"), "course_id": session.get("course_id")}
                if experiment["provider_profile"] != selection.get("profile_id") or experiment["model"] != selection.get("model"):
                    raise RuntimeError(f"frozen_provider_mismatch: {experiment}")
                result["experiment"] = experiment
                result["status"] = "completed"
                for turn_index, user_text in enumerate(case["user_turns"]):
                    turn_started = time.monotonic()
                    trace_before = int(request_json(base, f"/sessions/{urllib.parse.quote(session_id, safe='')}").get("last_sequence") or 0)
                    accepted = request_json(base, "/commands", method="POST", payload={
                        "command_id": "cmd_" + uuid.uuid4().hex, "client_id": "acceptance", "surface": "cli",
                        "session_id": session_id, "learner_id": f"dev-{run_id}", "type": "message.send",
                        "payload": {"content": user_text,
                                    "requested_action": "hint" if "提示" in user_text and case["category"] == "hint_then_success" else ""}})
                    trace_id = str(accepted.get("trace_id") or "")
                    if not trace_id:
                        raise RuntimeError("turn_trace_id_missing")
                    result["trace_ids"].append(trace_id)
                    deadline = time.monotonic() + 190
                    events: list[dict[str, Any]] = []
                    terminal = None
                    while time.monotonic() < deadline:
                        all_events = request_json(base, f"/replay/sessions/{urllib.parse.quote(session_id, safe='')}/events?from_sequence={trace_before + 1}")
                        events = [event for event in all_events if event.get("trace_id") == trace_id]
                        terminal = next((event for event in reversed(events) if event.get("type") == "agent.turn.completed"), None)
                        if terminal:
                            break
                        time.sleep(0.5)
                    elapsed_ms = round((time.monotonic() - turn_started) * 1000, 2)
                    observation = turn_observation(events)
                    if not terminal:
                        result["failure"] = {"event_type": "turn_timeout", "code": "turn_timeout", "kind": "timeout",
                                             "http_status": None, "retryable": True, "reason_code": "timeout"}
                        result["turns"].append({
                            "turn_index": turn_index + 1, "trace_id": trace_id, "elapsed_ms": elapsed_ms,
                            **observation, "event_count": len(events), "terminal_status": "timeout",
                        })
                        result["elapsed_ms"] = round(float(result.get("elapsed_ms") or 0) + elapsed_ms, 2)
                        for key, value in observation["usage"].items():
                            result.setdefault("usage", {}).setdefault(key, 0)
                            result["usage"][key] += value
                        raise TimeoutError("turn_timeout")
                    if (terminal.get("payload") or {}).get("status") != "ok":
                        result["failure"] = safe_failure(events, terminal)
                        result["turns"].append({
                            "turn_index": turn_index + 1, "trace_id": trace_id, "elapsed_ms": elapsed_ms,
                            **observation, "event_count": len(events), "terminal_status": "failed",
                        })
                        result["elapsed_ms"] = round(float(result.get("elapsed_ms") or 0) + elapsed_ms, 2)
                        for key, value in observation["usage"].items():
                            result.setdefault("usage", {}).setdefault(key, 0)
                            result["usage"][key] += value
                        raise RuntimeError(str(result["failure"].get("reason_code") or "turn_terminal_failed"))
                    messages = request_json(base, f"/sessions/{urllib.parse.quote(session_id, safe='')}/messages?limit=1000")
                    assistant = [message for message in messages if message.get("role") == "assistant"]
                    output = assistant[-1].get("content", "") if assistant else ""
                    turn_result = {"turn_index": turn_index + 1, "trace_id": trace_id, "elapsed_ms": elapsed_ms,
                        **observation, "output": output, "output_chars": len(output),
                        "event_count": len(events), "terminal_status": "ok"}
                    result["turns"].append(turn_result)
                    result["elapsed_ms"] = round(float(result.get("elapsed_ms") or 0) + elapsed_ms, 2)
                    for key, value in observation["usage"].items():
                        result.setdefault("usage", {}).setdefault(key, 0)
                        result["usage"][key] += value
                    write_json(run_dir / "progress.json", {"run_id": run_id, "completed_cells": len(completed),
                        "current_case": case["case_id"], "current_group": group, "current_turn": turn_index + 1,
                        "trace_id": trace_id, "updated_at": now()})
                result["finished_at"] = now()
            except Exception as exc:
                result["status"] = "failed"
                if not isinstance(result.get("failure"), dict):
                    safe_code = str(exc).split(":", 1)[0].strip()
                    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", safe_code):
                        safe_code = "gateway_or_runtime_failure"
                    result["failure"] = {"event_type": "harness.failure", "code": safe_code,
                                          "kind": "runtime_error", "http_status": None,
                                          "retryable": False, "reason_code": safe_code}
                result["reason"] = f"{type(exc).__name__}: {result['failure'].get('reason_code', 'runtime_error')}"
                result["finished_at"] = now()
            append_line(run_dir / "results.jsonl", result)
            completed.append(result)
            write_json(guard_path, {"run_id": run_id, "started_at": config["started_at"],
                "status": "running", "completed_cells": len(completed)})
            print(f"[{len(completed):02d}/80] {case['case_id']} {group}: {result['status']}", flush=True)

    counts = {status: sum(item.get("status") == status for item in completed) for status in ("completed", "failed", "blocked")}
    all_cells_recorded = len(completed) == 80 and counts["blocked"] == 0
    run_status = "completed" if counts["failed"] == 0 and all_cells_recorded else (
        "completed_with_failures" if all_cells_recorded else "incomplete")
    summary = {"run_id": run_id, "finished_at": now(), "status": run_status,
        "matrix_size": 80, "counts": counts, "turn_count": sum(len(item.get("turns") or []) for item in completed),
        "model_call_count": sum(int(turn.get("model_calls") or 0) for item in completed for turn in item.get("turns", [])),
        "sample_type": "constructed_developer_fixture", "fake_provider_used": False,
        "teacher_scoring": "pending_human_review", "semantic_leakage_labels": "pending_human_review",
        "citation_support_labels": "pending_human_review", "run_file": str(run_dir / "results.jsonl"),
        "price_configured": False, "estimated_cost": None,
        "git": config["git"], "provider_profile": selection.get("profile_id"), "model": selection.get("model")}
    write_json(run_dir / "summary.json", summary)
    write_json(root / "latest-summary.json", summary)
    write_json(guard_path, {"run_id": run_id, "started_at": config["started_at"], "finished_at": summary["finished_at"],
        "status": summary["status"], "completed_cells": len(completed)})
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["status"] in {"completed", "completed_with_failures"} else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--repeat-after-fix", default="")
    args = parser.parse_args()
    try:
        return run(args)
    except Exception as exc:
        print(f"验收未完成：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
