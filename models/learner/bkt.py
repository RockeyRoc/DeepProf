"""Versioned four-parameter Bayesian Knowledge Tracing baseline."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any


BKT_MODEL_VERSION = "bkt-four-parameter-dev-v1"
MIN_EVIDENCE = 3


@dataclass(frozen=True, slots=True)
class BKTParameters:
    p_l0: float = 0.20
    p_t: float = 0.10
    p_g: float = 0.20
    p_s: float = 0.10
    source: str = "development_initial_values; not fitted; teacher review pending"
    model_version: str = BKT_MODEL_VERSION

    def __post_init__(self) -> None:
        for name in ("p_l0", "p_t", "p_g", "p_s"):
            value = float(getattr(self, name))
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be in [0, 1]")
        if self.p_g == 1 or self.p_s == 1:
            raise ValueError("P(G) and P(S) must be less than 1")
        if not self.model_version.strip() or not self.source.strip():
            raise ValueError("BKT parameter provenance and version are required")

    def to_dict(self) -> dict[str, Any]:
        return {"p_l0": self.p_l0, "p_t": self.p_t, "p_g": self.p_g, "p_s": self.p_s,
                "source": self.source, "model_version": self.model_version,
                "fitted": False, "teacher_review": "pending"}

    @property
    def config_hash(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


DEFAULT_PARAMETERS = BKTParameters()


def parameters_from_snapshot(value: dict[str, Any] | None) -> BKTParameters:
    snapshot = dict(value or {})
    if not snapshot:
        return DEFAULT_PARAMETERS
    return BKTParameters(p_l0=float(snapshot["p_l0"]), p_t=float(snapshot["p_t"]),
                         p_g=float(snapshot["p_g"]), p_s=float(snapshot["p_s"]),
                         source=str(snapshot.get("source") or "snapshot"),
                         model_version=str(snapshot.get("model_version") or BKT_MODEL_VERSION))


def predict(mastery: float, parameters: BKTParameters = DEFAULT_PARAMETERS) -> float:
    _probability(mastery, "mastery")
    return mastery * (1.0 - parameters.p_s) + (1.0 - mastery) * parameters.p_g


def update(mastery: float, correct: bool, parameters: BKTParameters = DEFAULT_PARAMETERS) -> tuple[float, float]:
    """Return (pre-answer predicted correctness, post-transition mastery)."""
    predicted = predict(mastery, parameters)
    if correct:
        denominator = predicted
        posterior = mastery * (1.0 - parameters.p_s) / denominator if denominator else mastery
    else:
        denominator = 1.0 - predicted
        posterior = mastery * parameters.p_s / denominator if denominator else mastery
    return predicted, min(1.0, max(0.0, posterior + (1.0 - posterior) * parameters.p_t))


def initial_mastery(parameters: BKTParameters = DEFAULT_PARAMETERS) -> float:
    return parameters.p_l0


def binary_entropy(probability: float) -> float:
    _probability(probability, "probability")
    if probability in (0.0, 1.0):
        return 0.0
    return -(probability * math.log2(probability) + (1.0 - probability) * math.log2(1.0 - probability))


def _probability(value: float, label: str) -> None:
    if not math.isfinite(float(value)) or not 0.0 <= float(value) <= 1.0:
        raise ValueError(f"{label} must be a finite probability in [0, 1]")


__all__ = ["BKT_MODEL_VERSION", "BKTParameters", "DEFAULT_PARAMETERS", "MIN_EVIDENCE", "binary_entropy",
           "initial_mastery", "parameters_from_snapshot", "predict", "update"]
