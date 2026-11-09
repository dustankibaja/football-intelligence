"""Structural challenger compatibility with actual research-only fixture DTOs."""
from datetime import date, timedelta

import numpy as np
import pytest

from app.research import historical_oos as research


def _fixture(index: int):
    teams = ("Arsenal", "Chelsea", "Liverpool", "Man City")
    home = teams[index % 4]
    away = teams[(index + 1) % 4]
    home_goals = index % 4
    away_goals = (index + 1) % 3
    outcome = 0 if home_goals > away_goals else 1 if home_goals == away_goals else 2
    return research.Fixture(
        league="E0",
        season="1920",
        match_date=date(2020, 1, 1) + timedelta(days=index),
        home=home, away=away,
        home_goals=home_goals,
        away_goals=away_goals,
        outcome=outcome,
    )


def test_structural_rows_have_research_identity_not_certified_251_keys():
    fixtures = [_fixture(i) for i in range(32)]
    x, y = research.build_pre_match_features(fixtures)
    rows = research._research_modeling_rows(
        fixtures, x, [{"league": "E0", "season": "1920", "sha256": "a" * 64}]
    )
    assert len(rows) == 32
    assert all(row.feature_set_key == "research_outcomes_20_v1" for row in rows)
    assert all(row.temporal_precision == "DATE_ONLY" for row in rows)
    assert len({row.match_id for row in rows}) == len(rows)
    assert len({row.feature_set_fingerprint for row in rows}) == len(rows)
    assert all(len(row.feature_values) == 20 for row in rows)
    assert all(row.targets["match_result_1x2_v1"] == int(y[i]) for i, row in enumerate(rows))


def test_real_m1_structural_research_fit_and_target_stripped_inference():
    fixtures = [_fixture(i) for i in range(36)]
    x, _ = research.build_pre_match_features(fixtures)
    rows = research._research_modeling_rows(
        fixtures, x, [{"league": "E0", "season": "1920", "sha256": "a" * 64}]
    )
    model = research._structural_model("M1_ATTACK_DEFENSE")
    model.fit(rows[:28])
    pred = research._structural_probabilities(model, rows, [28, 29, 30, 31])
    assert pred.shape == (4, 3)
    assert np.isfinite(pred).all()
    assert np.allclose(pred.sum(axis=1), 1, atol=1e-4)
    assert all(row.targets for row in rows)


def test_model_variants_retained_as_research_only():
    assert research._structural_model("M1_DECAY_365").time_decay_half_life_days == 365
    assert research._structural_model("M3_DIXON_COLES").optimize_rho is True
    with pytest.raises(ValueError, match="UNREGISTERED"):
        research._structural_model("FROZEN_CHAMPION")
