"""Deterministic scientific diagnostics without claiming betting profitability."""
from datetime import date, timedelta

import numpy as np
import pytest

from app.research.uncertainty import (
    date_clustered_log_loss_skill,
    draw_probability_audit,
    selective_skill_audit,
)


def test_clustered_logloss_skill_reproducible_by_match_date():
    days = [date(2026, 1, 1) + timedelta(days=i // 2) for i in range(80)]
    y = np.asarray([i % 3 for i in range(80)], dtype=int)
    baseline = np.tile([0.4, 0.3, 0.3], (80, 1))
    challenger = np.full((80, 3), 0.15)
    challenger[np.arange(80), y] = 0.70

    a = date_clustered_log_loss_skill(y, challenger, baseline, days)
    b = date_clustered_log_loss_skill(y, challenger, baseline, days)
    assert a == b
    assert a.unique_match_dates == 40
    assert a.completed_matches == 80
    assert a.improvement_log_loss > 0
    assert a.confidence_interval_95[0] > 0
    assert a.bootstrap_probability_positive == 1.0


def test_bad_probability_or_unreliable_calendar_rejected():
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(25)]
    labels = np.asarray([0] * 25)
    baseline = np.tile([0.5, 0.25, 0.25], (25, 1))
    with pytest.raises(ValueError, match="INVALID_STATISTICAL_SCORE_INPUT"):
        date_clustered_log_loss_skill(labels, np.tile([0.9, 0.9, -0.8], (25, 1)), baseline, days)
    with pytest.raises(ValueError, match="INSUFFICIENT_DISTINCT_MATCH_DATES"):
        date_clustered_log_loss_skill(labels, baseline, baseline, days[:1] * 25)


def test_draw_frequency_audit_accounts_for_full_three_way_distribution():
    labels = [0, 1, 2, 1]
    probs = np.asarray([
        [0.6, 0.2, 0.2],
        [0.2, 0.5, 0.3],
        [0.3, 0.3, 0.4],
        [0.2, 0.4, 0.4],
    ])
    report = draw_probability_audit(labels, probs)
    assert report["sample_size"] == 4
    assert report["actual_draw_frequency"] == 0.5
    assert report["mean_predicted_draw_probability"] == 0.35
    assert abs(sum(b["count"] for b in report["reliability_bins"]) - 4) == 0
    assert report["draw_binary_brier"] > 0


def test_selective_skill_compares_on_exact_same_selected_games():
    y = [0, 1, 2, 1]
    p = np.asarray([
        [0.8, 0.1, 0.1],
        [0.2, 0.6, 0.2],
        [0.2, 0.2, 0.6],
        [0.35, 0.35, 0.30],
    ])
    prior = np.tile([0.45, 0.30, 0.25], (4, 1))
    audit = selective_skill_audit(y, p, prior, 0.6)
    assert audit["n_selected"] == 3
    assert audit["model_correct"] == 3
    assert audit["baseline_correct"] == 1
    assert audit["coverage"] == 0.75
    assert audit["model_accuracy"] == 1.0
    assert len(audit["wilson_95_interval"]) == 2
    assert selective_skill_audit(y, p, prior, None)["n_selected"] == 0
