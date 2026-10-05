from __future__ import annotations

import hashlib
import json

from scripts import run_m3_abc_research as research
from scripts import verify_m3_preflight as verifier


def _write_baseline(tmp_path, monkeypatch, *, route="inherited"):
    project = tmp_path / "project"
    raw_root = tmp_path / "isolated" / "experiments" / "m3-abc-research"
    run_id = "m3-preflight-20260928T124619Z-5fe6b54a"
    run_dir = raw_root / run_id / "runs" / run_id
    run_dir.mkdir(parents=True)
    docs_root = project / "docs" / "experiments" / "m3-abc-research"
    docs_root.mkdir(parents=True)
    monkeypatch.setattr(research, "PROJECT", project)
    monkeypatch.setattr(research, "SOURCE_HOME", tmp_path / "isolated")
    monkeypatch.setattr(research, "DEFAULT_PROVIDER_NETWORK_MODE", "inherited")

    config = research._experiment_config_payload(provider_network_mode=route)
    config["config_sha256"] = hashlib.sha256(json.dumps(config, ensure_ascii=False,
        sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    (raw_root / run_id / "experiment-config.json").write_text(
        json.dumps(config), encoding="utf-8")
    (run_dir / "manifest.json").write_text(json.dumps({
        "run_id": run_id, "phase": "preflight", "groups": list(research.GROUPS),
        "call_budget": {"authorized_provider_requests": 12},
    }), encoding="utf-8")
    (run_dir / "summary.json").write_text(json.dumps({
        "run_id": run_id, "status": "completed_with_failures",
        "sample": {"phase": "preflight"}, "token_usage": {"model_calls": 12},
        "preflight_failure_rate": 1.0, "provider_network_mode": route,
        "isolation_cleanup": "isolated_gateway_stopped",
    }), encoding="utf-8")
    return run_id


def test_verifier_reuses_exact_baseline_without_provider_calls(tmp_path, monkeypatch):
    run_id = _write_baseline(tmp_path, monkeypatch)
    called = []
    monkeypatch.setattr(research, "run_preflight_measurement",
        lambda **kwargs: called.append(kwargs))

    result = verifier.measure(run_id)

    assert result["run_id"] == run_id
    assert result["preflight_failure_rate"] == 1.0
    assert result["provider_network_mode"] == "inherited"
    assert result["reused_recorded_baseline"] is True
    assert called == []


def test_verifier_runs_only_the_direct_route_trial_when_hypothesis_changes(tmp_path, monkeypatch):
    run_id = _write_baseline(tmp_path, monkeypatch)
    monkeypatch.setattr(research, "DEFAULT_PROVIDER_NETWORK_MODE", "direct")
    calls = []
    monkeypatch.setattr(research, "run_preflight_measurement", lambda **kwargs: (
        calls.append(kwargs) or {"run_id": "direct-trial", "preflight_failure_rate": 0.0,
            "passed": True, "completed_generations": 12, "planned_cells": 12,
            "provider_network_mode": "direct", "actual_provider_requests": 12,
            "provider_requests_cumulative": 183, "provider_requests_remaining": 268,
            "isolation_cleanup": "isolated_gateway_stopped"}))

    result = verifier.measure(run_id)

    assert result["run_id"] == "direct-trial"
    assert result["provider_network_mode"] == "direct"
    assert result["reused_recorded_baseline"] is False
    assert calls == [{"direct_provider_network": True}]
