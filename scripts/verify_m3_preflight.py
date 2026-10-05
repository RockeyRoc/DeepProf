"""Measure a frozen M3 preflight, reusing an exact baseline when possible."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from typing import Any

from scripts import run_m3_abc_research as research


def _load_recorded_baseline(run_id: str) -> tuple[dict[str, Any], str]:
    docs_root = research.PROJECT / "docs" / "experiments" / "m3-abc-research"
    raw_root = research.SOURCE_HOME / "experiments" / "m3-abc-research"
    run_dir = raw_root / run_id / "runs" / run_id
    summary_path = run_dir / "summary.json"
    config_path = raw_root / run_id / "experiment-config.json"
    if not summary_path.is_file() or not config_path.is_file():
        raise RuntimeError("recorded_preflight_baseline_missing")

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary.get("run_id") != run_id:
        raise RuntimeError("recorded_preflight_baseline_identity_mismatch")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    declared_hash = config.pop("config_sha256", None)
    payload_hash = hashlib.sha256(json.dumps(config, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    if declared_hash != payload_hash:
        raise RuntimeError("recorded_preflight_baseline_config_corrupt")
    route = str(config.get("provider_network_mode") or "")
    if route not in {"inherited", "direct"} or summary.get("provider_network_mode") != route:
        raise RuntimeError("recorded_preflight_baseline_route_mismatch")

    record = research._preflight_run_record(docs_root, raw_root, run_id)
    if not record or int(record.get("calls") or 0) != research.PREFLIGHT_BUDGET:
        raise RuntimeError("recorded_preflight_baseline_not_in_cumulative_ledger")
    passed, reason = research._preflight_passed(run_dir, summary)
    cells = [json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((run_dir / "cells").glob("*.json"))]
    successful = sum(research._preflight_cell_passed(row) for row in cells)
    if not passed:
        successful = min(successful, 11)
    metric = 1.0 - successful / 12
    stored_metric = summary.get("preflight_failure_rate")
    if stored_metric is not None and float(stored_metric) != metric:
        raise RuntimeError("recorded_preflight_baseline_metric_mismatch")
    state = research._provider_budget_state(docs_root, raw_root)
    return ({"run_id": run_id, "preflight_failure_rate": metric,
        "passed": passed, "reason": reason, "actual_provider_requests": int(record["calls"]),
        "completed_generations": successful, "planned_cells": 12,
        "provider_network_mode": route,
        "provider_requests_cumulative": state["provider_calls"],
        "provider_requests_remaining": max(0, research.APPROVED_TOTAL_REQUEST_BUDGET
                                            - state["provider_calls"]),
        "isolation_cleanup": str(summary.get("isolation_cleanup") or "unknown"),
        "reused_recorded_baseline": True}, route)


def measure(baseline_run_id: str = "") -> dict[str, Any]:
    current_route = research.DEFAULT_PROVIDER_NETWORK_MODE
    if baseline_run_id:
        baseline, baseline_route = _load_recorded_baseline(baseline_run_id)
        if current_route == baseline_route:
            return baseline
    result = research.run_preflight_measurement(
        direct_provider_network=(current_route == "direct"))
    result["reused_recorded_baseline"] = False
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-run-id", default="",
        help="Reuse the exact recorded run at baseline; a changed network route triggers one new measurement")
    args = parser.parse_args()
    try:
        result = measure(args.baseline_run_id)
    except Exception as exc:
        print(json.dumps({"preflight_failure_rate": 1.0,
                          "runner_error_type": type(exc).__name__}, sort_keys=True))
        print(f"preflight runner stopped before a complete measurement: {type(exc).__name__}",
              file=sys.stderr)
        return 1
    print(json.dumps({"preflight_failure_rate": result["preflight_failure_rate"],
        "run_id": result["run_id"], "passed": result["passed"],
        "completed_generations": result["completed_generations"],
        "planned_cells": result["planned_cells"],
        "provider_network_mode": result["provider_network_mode"],
        "actual_provider_requests": result["actual_provider_requests"],
        "provider_requests_cumulative": result["provider_requests_cumulative"],
        "provider_requests_remaining": result["provider_requests_remaining"],
        "reused_recorded_baseline": result["reused_recorded_baseline"],
        "isolation_cleanup": result["isolation_cleanup"]}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
