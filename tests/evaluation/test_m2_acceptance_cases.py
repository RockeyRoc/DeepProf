from __future__ import annotations

import json
from pathlib import Path


def test_m2_acceptance_set_is_local_constructed_and_points_to_existing_checks():
    root = Path(__file__).resolve().parents[2]
    package = json.loads((root / "evaluation" / "m2_dev_cases.json").read_text(encoding="utf-8"))
    assert package["human_subjects"] is False
    assert package["requires_network"] is False
    assert package["requires_paid_model"] is False
    assert len(package["cases"]) >= 8
    assert all((root / row["test"]).is_file() for row in package["cases"])
