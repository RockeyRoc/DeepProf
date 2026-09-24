#!/usr/bin/env python3
"""Export redacted M2 and M3 v0.6.2 release-validation summaries."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
HISTORICAL_M3 = ROOT / "docs/experiments/m3-offline/m3-offline-20260924-final6"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def utc(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def export_m2(source: Path) -> dict[str, Any]:
    raw = read_json(source)
    command = raw.get("command", [])
    files = [str(item) for item in command if isinstance(item, str) and item.startswith("tests/")]
    summary = str(raw.get("summary", ""))
    total = int(summary.split(" passed", 1)[0]) if " passed" in summary else 0
    return {
        "record_id": "m2-focused-v0.6.2-release-revalidation",
        "acceptance_version": raw.get("acceptance_version"),
        "created_at_utc": utc(str(raw["created_at"])),
        "status": raw.get("status"),
        "sample_type": raw.get("sample_type"),
        "human_subjects": raw.get("human_subjects"),
        "requires_network": raw.get("requires_network"),
        "requires_paid_model": raw.get("requires_paid_model"),
        "denominator": {"passed": total, "total": total, "failures": len(raw.get("failure_cases", []))},
        "test_files": files,
        "overlap": "The focused 82-item set is a subset of the 318-item Python regression; do not add the counts.",
        "source_record_sha256": sha256(source),
    }


def export_m3(source: Path) -> dict[str, Any]:
    raw = read_json(source)
    return {
        "record_id": "m3-offline-v0.6.2-release-validation",
        "run_id": raw["run_id"],
        "generated_at_utc": utc(str(raw["generated_at"])),
        "provider": raw["provider"],
        "status": raw["status"],
        "sample_type": raw["sample_type"],
        "human_subjects": False,
        "paid_model_requests": 0,
        "main_matrix": raw["main_matrix"],
        "replay_matrix": raw["replay_matrix"],
        "ablation_matrix": raw["rag_ablation_matrix"],
        "case_manifest_count": raw["case_manifest_count"],
        "all_cells_completed": raw["all_cells_completed"],
        "metrics": raw["metrics"],
        "source_fingerprint": raw["source_fingerprint"],
        "source_summary_sha256": sha256(source),
    }


def export_historical_m3(summary_source: Path, manifest_source: Path) -> dict[str, Any]:
    summary = read_json(summary_source)
    manifest = read_json(manifest_source)
    summary_keys = (
        "schema_version", "run_id", "generated_at", "started_at", "sample_type", "provider",
        "status", "main_matrix", "replay_matrix", "rag_ablation_matrix", "metrics",
        "human_review", "teacher_approval", "real_provider_experiment", "student_learning_effect",
        "all_cells_completed", "session_isolation_passed", "failed_cells", "source_fingerprint",
    )
    config_keys = (
        "case_version", "source_case_version", "source_case_sha256", "m3_case_sha256",
        "sample_type", "human_subjects", "provider_profile", "model", "fake_provider_used",
        "paid_model_used", "network_required", "course_id", "policy_version", "bkt_parameters",
        "bkt_config_hash", "strategy_bindings_sha256", "prompt_version", "prompt_sha256",
        "sampling", "retrieval", "experiment_design", "source_fingerprint",
    )
    return {
        "summary": {key: summary[key] for key in summary_keys if key in summary},
        "frozen_configuration": {key: manifest[key] for key in config_keys if key in manifest},
        "source_summary_sha256": sha256(summary_source),
        "source_manifest_sha256": sha256(manifest_source),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--m2-input",
        type=Path,
        default=ROOT / ".release" / "v0.6.2-m2-revalidation.json",
    )
    parser.add_argument(
        "--m3-input",
        type=Path,
        default=ROOT / ".release" / "m3-v0.6.2-release-validation"
        / "m3-offline-v0.6.2-release-20260924" / "summary.json",
    )
    parser.add_argument("--historical-m3-summary", type=Path, default=HISTORICAL_M3 / "summary.json")
    parser.add_argument("--historical-m3-manifest", type=Path, default=HISTORICAL_M3 / "manifest.json")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "docs/experiments/source-data")
    args = parser.parse_args()
    required = (args.m2_input, args.m3_input, args.historical_m3_summary, args.historical_m3_manifest)
    if any(not path.is_file() for path in required):
        parser.error("the acceptance source summaries must all exist")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "v0.6.2-m2-revalidation-summary.json": export_m2(args.m2_input),
        "v0.6.2-m3-offline-summary.json": export_m3(args.m3_input),
        "M3-offline-summary-redacted.json": export_historical_m3(
            args.historical_m3_summary, args.historical_m3_manifest),
    }
    for name, payload in outputs.items():
        (args.output_dir / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps({"status": "ok", "outputs": sorted(outputs)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
