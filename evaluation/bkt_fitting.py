"""Small, deterministic four-parameter BKT EM fitter used by public-data research."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Protocol

from models.learner.bkt import BKTParameters, INITIAL_PARAMETERS, predict, update


class BKTSequenceLike(Protocol):
    concept_id: str
    outcomes: tuple[bool, ...]


@dataclass(frozen=True, slots=True)
class BKTFit:
    global_parameters: BKTParameters
    by_concept: dict[str, BKTParameters]
    concept_parameters: dict[str, BKTParameters]
    concept_counts: dict[str, int]
    iterations: int

    def summary(self) -> dict[str, object]:
        models = [self.global_parameters, *self.concept_parameters.values(), *self.by_concept.values()]
        boundaries = {}
        for name in ("p_l0", "p_t", "p_g", "p_s"):
            lower, upper = ((0.001, 0.999) if name == "p_l0" else (0.0, 0.5))
            values = [float(getattr(model, name)) for model in models]
            boundaries[name] = {"lower": lower, "upper": upper, "minimum": min(values),
                "maximum": max(values), "at_lower_count": sum(value <= lower for value in values),
                "at_upper_count": sum(value >= upper for value in values)}
        return {"global": {key: getattr(self.global_parameters, key) for key in ("p_l0", "p_t", "p_g", "p_s")},
                "concept_models": len(self.by_concept), "concept_counts": self.concept_counts,
                "shrinkage_concept_models": len(self.by_concept),
                "unshrunk_concept_models": len(self.concept_parameters),
                "iterations": self.iterations, "parameter_boundaries": boundaries,
                "maximum_p_g_plus_p_s": max(model.p_g + model.p_s for model in models),
                "models_at_p_g_plus_p_s_cap": sum(model.p_g + model.p_s >= 0.989999 for model in models)}


def _bounded(value: float, lower: float = 0.001, upper: float = 0.999) -> float:
    return max(lower, min(upper, value))


def _forward_transition(mastered: float, unmastered: float, p_t: float) -> tuple[float, float]:
    """Apply BKT's one-way learning transition after observing the response."""
    return mastered + unmastered * p_t, unmastered * (1.0 - p_t)


def _em(sequences: list[tuple[bool, ...]], initial: BKTParameters, *, max_iterations: int = 30,
        tolerance: float = 1e-7) -> tuple[BKTParameters, int]:
    """Baum–Welch fit with an observation then learning-transition chronology."""
    p_l0, p_t, p_g, p_s = initial.p_l0, initial.p_t, initial.p_g, initial.p_s
    if not sequences:
        return initial, 0
    for iteration in range(1, max_iterations + 1):
        init_known = init_total = 0.0
        guess_correct = guess_total = 0.0
        slip_wrong = slip_total = 0.0
        learn_count = unlearned_count = 0.0
        for outcomes in sequences:
            length = len(outcomes)
            if not length:
                continue
            # State 0 is mastered (correct with 1-slip); state 1 is
            # unmastered (correct with guess). p_l0 is initial mastery.
            emission = [[1.0 - p_s if result else p_s,
                         p_g if result else 1.0 - p_g] for result in outcomes]
            alpha = [[p_l0 * emission[0][0], (1.0 - p_l0) * emission[0][1]]]
            scales = [sum(alpha[0])]
            alpha[0] = [value / scales[0] for value in alpha[0]]
            for index in range(1, length):
                previous = alpha[-1]
                predicted_mastered, predicted_unmastered = _forward_transition(previous[0], previous[1], p_t)
                current = [predicted_mastered * emission[index][0],
                           predicted_unmastered * emission[index][1]]
                scale = max(1e-300, sum(current))
                scales.append(scale)
                alpha.append([value / scale for value in current])
            beta = [[0.0, 0.0] for _ in outcomes]
            beta[-1] = [1.0, 1.0]
            for index in range(length - 2, -1, -1):
                next_emission = emission[index + 1]
                beta[index] = [
                    next_emission[0] * beta[index + 1][0],
                    p_t * next_emission[0] * beta[index + 1][0] +
                    (1.0 - p_t) * next_emission[1] * beta[index + 1][1],
                ]
                normalization = max(1e-300, sum(alpha[index][state] * beta[index][state] for state in (0, 1)))
                beta[index] = [value / normalization for value in beta[index]]
            initial_norm = max(1e-300, sum(alpha[0][state] * beta[0][state] for state in (0, 1)))
            init_known += alpha[0][0] * beta[0][0] / initial_norm
            init_total += 1.0
            for index, outcome in enumerate(outcomes):
                norm = max(1e-300, sum(alpha[index][state] * beta[index][state] for state in (0, 1)))
                gamma_unmastered = alpha[index][1] * beta[index][1] / norm
                gamma_mastered = 1.0 - gamma_unmastered
                guess_total += gamma_unmastered
                slip_total += gamma_mastered
                if outcome:
                    guess_correct += gamma_unmastered
                else:
                    slip_wrong += gamma_mastered
            for index in range(length - 1):
                next_emission = emission[index + 1]
                previous = alpha[index]
                beta_next = beta[index + 1]
                learn = previous[1] * p_t * next_emission[0] * beta_next[0]
                remain_unmastered = previous[1] * (1.0 - p_t) * next_emission[1] * beta_next[1]
                remain_mastered = previous[0] * next_emission[0] * beta_next[0]
                transition_norm = max(1e-300, learn + remain_unmastered + remain_mastered)
                learn_count += learn / transition_norm
                unlearned_count += (learn + remain_unmastered) / transition_norm
        updated = (
            _bounded(init_known / init_total) if init_total else p_l0,
            _bounded(learn_count / unlearned_count, 0.0, 0.5) if unlearned_count else p_t,
            _bounded(guess_correct / guess_total, 0.0, 0.5) if guess_total else p_g,
            _bounded(slip_wrong / slip_total, 0.0, 0.5) if slip_total else p_s,
        )
        if updated[2] + updated[3] >= 0.99:
            scale = 0.99 / (updated[2] + updated[3])
            updated = (updated[0], updated[1], updated[2] * scale, updated[3] * scale)
        delta = max(abs(left - right) for left, right in zip((p_l0, p_t, p_g, p_s), updated))
        p_l0, p_t, p_g, p_s = updated
        if delta < tolerance:
            return _parameters((p_l0, p_t, p_g, p_s), f"ASSISTments EM fit; iterations={iteration}"), iteration
    return _parameters((p_l0, p_t, p_g, p_s), f"ASSISTments EM fit; iterations={max_iterations}"), max_iterations


def _parameters(values: tuple[float, float, float, float], source: str) -> BKTParameters:
    return BKTParameters(p_l0=values[0], p_t=values[1], p_g=values[2], p_s=values[3], source=source,
                         model_version="bkt-four-parameter-assistments-research-v1", fitted=True,
                         teacher_review="not_applicable_public_math_dataset")


def _multi_start(sequences: list[tuple[bool, ...]], *, max_iterations: int = 30) -> tuple[BKTParameters, int]:
    starts = (
        INITIAL_PARAMETERS,
        _parameters((0.1, 0.05, 0.2, 0.1), "research multistart"),
        _parameters((0.6, 0.15, 0.15, 0.2), "research multistart"),
        _parameters((0.9, 0.01, 0.05, 0.3), "research multistart"),
    )
    fitted = [_em(sequences, start, max_iterations=max_iterations) for start in starts]
    return min(fitted, key=lambda item: (_sequence_log_loss(sequences, item[0]),
                                         tuple(getattr(item[0], key) for key in ("p_l0", "p_t", "p_g", "p_s"))))


def _sequence_log_loss(sequences: Iterable[tuple[bool, ...]], parameters: BKTParameters) -> float:
    total = loss = 0
    from math import log
    for outcomes in sequences:
        for outcome, probability in zip(outcomes, predict_sequence(outcomes, parameters)):
            loss -= log(max(1e-12, probability if outcome else 1 - probability))
            total += 1
    return loss / total if total else math.inf


def _blend(global_model: BKTParameters, local: BKTParameters, count: int, shrinkage: float) -> BKTParameters:
    weight = count / (count + shrinkage) if shrinkage > 0 else 1.0
    values = [weight * getattr(local, name) + (1.0 - weight) * getattr(global_model, name)
              for name in ("p_l0", "p_t", "p_g", "p_s")]
    p_l0, p_t, p_g, p_s = values
    if p_g + p_s >= 0.99:
        scale = 0.99 / (p_g + p_s)
        p_g, p_s = p_g * scale, p_s * scale
    return _parameters((p_l0, p_t, p_g, p_s), f"hierarchical ASSISTments fit; n={count}; shrinkage={shrinkage:g}")


def fit_hierarchical(rows: Iterable[BKTSequenceLike], *, min_concept_attempts: int = 40,
                     shrinkage: float = 200.0) -> BKTFit:
    grouped: dict[str, list[tuple[bool, ...]]] = defaultdict(list)
    all_sequences: list[tuple[bool, ...]] = []
    for row in rows:
        if row.outcomes:
            grouped[row.concept_id].append(row.outcomes)
            all_sequences.append(row.outcomes)
    # Multi-start guards the global optimum; local models start from that shared
    # solution so per-concept estimation stays practical across cross-validation folds.
    global_parameters, iterations = _multi_start(all_sequences) if all_sequences else (INITIAL_PARAMETERS, 0)
    local: dict[str, BKTParameters] = {}
    concept_parameters: dict[str, BKTParameters] = {}
    counts: dict[str, int] = {}
    for concept, sequences in sorted(grouped.items()):
        count = sum(map(len, sequences))
        counts[concept] = count
        if count < min_concept_attempts:
            continue
        local_fit, local_iterations = _em(sequences, global_parameters, max_iterations=20)
        iterations = max(iterations, local_iterations)
        concept_parameters[concept] = local_fit
        local[concept] = _blend(global_parameters, local_fit, count, shrinkage)
    return BKTFit(global_parameters, local, concept_parameters, counts, iterations)


def predict_sequence(outcomes: Iterable[bool], parameters: BKTParameters) -> list[float]:
    mastery = parameters.p_l0
    predictions: list[float] = []
    for outcome in outcomes:
        probability, mastery = update(mastery, bool(outcome), parameters)
        predictions.append(probability)
    return predictions
