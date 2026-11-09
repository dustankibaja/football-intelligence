"""Research-only statistical uncertainty and draw-calibration diagnostics.

Neither confidence intervals nor confidence thresholds establish betting profit.
Cluster unit is provider calendar date, because exact kickoff times may be absent.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np


@dataclass(frozen=True)
class ClusteredSkillInterval:
    improvement_log_loss: float
    confidence_interval_95: tuple[float, float]
    bootstrap_probability_positive: float
    unique_match_dates: int
    completed_matches: int
    bootstrap_iterations: int
    inference_scope: str = "RESEARCH_ONLY_VS_TRAINING_PRIOR"


def _validate_distribution(y, probs):
    labels = np.asarray(y, dtype=int)
    probabilities = np.asarray(probs, dtype=float)
    if (
        labels.ndim != 1
        or probabilities.shape != (len(labels), 3)
        or not len(labels)
        or np.any((labels < 0) | (labels > 2))
        or not np.isfinite(probabilities).all()
        or (probabilities < 0).any()
        or not np.allclose(probabilities.sum(axis=1), 1.0, atol=1e-6)
    ):
        raise ValueError("INVALID_STATISTICAL_SCORE_INPUT")
    return labels, probabilities


def date_clustered_log_loss_skill(
    labels,
    challenger_probabilities,
    baseline_probabilities,
    dates: list[date],
    *,
    n_bootstrap: int = 2000,
    seed: int = 20261109,
) -> ClusteredSkillInterval:
    """Positive skill = baseline loss minus challenger loss, clustered by date.

    Each bootstrap sample resamples WHOLE match-day groups with replacement.
    This captures correlation among games on the same date better than an
    independent-match bootstrap; it is NOT a guarantee of future transfer.
    """
    y, challenger = _validate_distribution(labels, challenger_probabilities)
    _, baseline = _validate_distribution(labels, baseline_probabilities)
    if len(dates) != len(y) or n_bootstrap < 200:
        raise ValueError("INVALID_DATE_CLUSTERS_OR_BOOTSTRAP_SIZE")
    unique_days = sorted(set(dates))
    if len(unique_days) < 20:
        raise ValueError("INSUFFICIENT_DISTINCT_MATCH_DATES")
    diff = np.log(np.maximum(challenger[np.arange(len(y)), y], 1e-12)) - np.log(
        np.maximum(baseline[np.arange(len(y)), y], 1e-12)
    )
    group_indices = [
        np.asarray([i for i, match_date in enumerate(dates) if match_date == day])
        for day in unique_days
    ]
    means = np.asarray([diff[group].sum() for group in group_indices], dtype=float)
    counts = np.asarray([len(group) for group in group_indices], dtype=int)

    random = np.random.default_rng(seed)
    sampled = random.integers(
        low=0, high=len(unique_days), size=(n_bootstrap, len(unique_days))
    )
    sampled_improvements = means[sampled].sum(axis=1) / counts[sampled].sum(axis=1)
    bounds = np.quantile(sampled_improvements, [0.025, 0.975])
    return ClusteredSkillInterval(
        improvement_log_loss=float(diff.mean()),
        confidence_interval_95=(float(bounds[0]), float(bounds[1])),
        bootstrap_probability_positive=float(np.mean(sampled_improvements > 0)),
        unique_match_dates=len(unique_days),
        completed_matches=len(y),
        bootstrap_iterations=n_bootstrap,
    )


def draw_probability_audit(labels, probabilities, *, n_bins=10) -> dict:
    """Audit draw frequencies/calibration without fitting on the held-out data."""
    y, p = _validate_distribution(labels, probabilities)
    if n_bins < 2:
        raise ValueError("INVALID_DRAW_BIN_COUNT")
    draw_probability = p[:, 1]
    observed = (y == 1).astype(float)
    table = []
    for index in range(n_bins):
        lower = index / n_bins
        upper = (index + 1) / n_bins
        mask = (draw_probability >= lower) & (
            draw_probability <= upper if index == n_bins - 1 else draw_probability < upper
        )
        count = int(mask.sum())
        table.append({
            "bin_low": lower,
            "bin_high": upper,
            "count": count,
            "mean_predicted_draw_probability": (
                float(np.mean(draw_probability[mask])) if count else None
            ),
            "observed_draw_frequency": float(np.mean(observed[mask])) if count else None,
        })
    return {
        "sample_size": len(y),
        "actual_draw_frequency": float(observed.mean()),
        "mean_predicted_draw_probability": float(draw_probability.mean()),
        "predicted_minus_observed": float(draw_probability.mean() - observed.mean()),
        "draw_binary_brier": float(np.mean((draw_probability - observed) ** 2)),
        "reliability_bins": table,
        "caution": "Descriptive out-of-sample draw audit, not a refitted calibration model.",
    }


def selective_skill_audit(labels, probabilities, baseline_probabilities, cutoff: float | None) -> dict:
    """Compare selected predictions against baseline on EXACTLY those matches."""
    y, p = _validate_distribution(labels, probabilities)
    _, prior = _validate_distribution(labels, baseline_probabilities)
    if cutoff is None:
        return {
            "threshold": None, "n_selected": 0, "coverage": 0.0,
            "status": "ABSTAIN_ALL_PREDECLARED_VALIDATION_POLICY",
        }
    if not 0 < cutoff <= 1:
        raise ValueError("INVALID_CONFIDENCE_THRESHOLD")
    mask = p.max(axis=1) >= cutoff
    n = int(mask.sum())
    if not n:
        return {
            "threshold": cutoff, "n_selected": 0,
            "coverage": 0.0, "status": "NO_MATCHES_PASSED_THRESHOLD",
        }
    selected_labels = y[mask]
    model_guesses = p[mask].argmax(axis=1)
    baseline_guesses = prior[mask].argmax(axis=1)
    correct = int((model_guesses == selected_labels).sum())
    base_correct = int((baseline_guesses == selected_labels).sum())
    accuracy = correct / n
    baseline_accuracy = base_correct / n

    # Wilson score interval; descriptive, no independence claim under date clustering.
    z = 1.959963984540054
    denominator = 1.0 + z * z / n
    center = (accuracy + z * z / (2 * n)) / denominator
    halfwidth = (
        z * np.sqrt(accuracy * (1 - accuracy) / n + z * z / (4 * n * n))
        / denominator
    )
    return {
        "threshold": cutoff,
        "n_selected": n,
        "coverage": n / len(y),
        "model_accuracy": accuracy,
        "same_fixtures_training_prior_accuracy": baseline_accuracy,
        "accuracy_difference_vs_training_prior": accuracy - baseline_accuracy,
        "model_correct": correct,
        "baseline_correct": base_correct,
        "wilson_95_interval": [float(center - halfwidth), float(center + halfwidth)],
        "status": "RESEARCH_DESCRIPTIVE_NOT_PROFIT_CLAIM",
    }
