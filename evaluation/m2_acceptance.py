"""Run the deterministic, local-only M2 acceptance suite and save its evidence."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config.paths import experiment_runs_dir
from models.learner.bkt import DEFAULT_PARAMETERS
from scripts.source_fingerprint import repository_fingerprint


ROOT = Path(__file__).resolve().parents[1]
CASE_FILE = Path(__file__).with_name("m2_dev_cases.json")
TEST_TARGETS = [
    "tests/models/test_bkt.py",
    "tests/models/test_learner_store.py",
    "tests/graph/test_education_routing.py",
    "tests/api/test_ab_and_replay.py",
    "tests/library/test_markitdown_converter.py",
    "tests/test_question_bank.py",
    "tests/test_contract_consistency.py",
    "tests/test_architecture_boundaries.py",
]


def _version(distribution: str) -> str:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return "not-installed"


def run_m2_acceptance(*, output: str | Path | None = None, root: Path = ROOT) -> dict[str, Any]:
    cases = json.loads(CASE_FILE.read_text(encoding="utf-8"))
    started = time.monotonic()
    # pytest.ini already enables quiet mode; adding a second -q suppresses the
    # collection summary that the acceptance record uses for its case count.
    command = [sys.executable, "-m", "pytest", *TEST_TARGETS]
    completed = subprocess.run(command, cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace")
    elapsed = round(time.monotonic() - started, 3)
    output_text = (completed.stdout + "\n" + completed.stderr).strip()
    failures = re.findall(r"(?m)^(?:FAILED|ERROR)\s+.+$", output_text)
    summary_lines = [line.strip() for line in output_text.splitlines()
                     if re.search(r"\d+ passed|\d+ failed|\d+ error", line)]
    report: dict[str, Any] = {
        "acceptance_version": cases["version"],
        "sample_type": cases["sample_type"],
        "human_subjects": cases["human_subjects"],
        "requires_network": cases["requires_network"],
        "requires_paid_model": cases["requires_paid_model"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "passed" if completed.returncode == 0 else "failed",
        "command": command,
        "elapsed_seconds": elapsed,
        "summary": summary_lines[-1] if summary_lines else "no pytest summary found",
        "failure_cases": failures,
        "cases": cases["cases"],
        "bkt_parameters": DEFAULT_PARAMETERS.to_dict(),
        "bkt_config_hash": DEFAULT_PARAMETERS.config_hash,
        "engineering_acceptance": "passed" if completed.returncode == 0 else "failed",
        "teacher_review": "pending",
        "bkt_calibration": "pending_teacher_approved_parameters_and_fit",
        "student_learning_effect": "not_measured",
        "interpretation": "Engineering checks do not establish teacher approval, calibrated parameters, or student learning effects.",
        "execution_environment": {
            "python": sys.version,
            "platform": sys.platform,
            "working_directory": str(root.resolve()),
        },
        "dependencies": {"markitdown": _version("markitdown"),
                          "rapidocr_onnxruntime": _version("rapidocr_onnxruntime"),
                          "pypdfium2": _version("pypdfium2")},
        "code_fingerprint": repository_fingerprint(root),
    }
    target = Path(output) if output is not None else experiment_runs_dir() / (
        "m2-offline-acceptance-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json"
    )
    report["report_path"] = str(target.resolve())
    report["pytest_output"] = output_text[-12_000:]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_m2_acceptance(output=args.output)
    print(json.dumps({key: report[key] for key in (
        "status", "summary", "failure_cases", "bkt_config_hash", "code_fingerprint", "report_path",
    )}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
