"""Regression tests for complete score-grid evaluation (schema version 1.1.0)."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.prediction.domain import ShadowPredictionInput, ShadowPredictionResult
from app.prediction.scoring import PostMatchScoringService
from app.prediction.service import ShadowPredictionService, compute_score_matrix


def _prediction() -> ShadowPredictionResult:
    now = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)
    inp = ShadowPredictionInput(
        fixture_id=uuid4(),
        competition_code="E0",
        season_year="2026",
        home_team_id=uuid4(),
        away_team_id=uuid4(),
        kickoff_utc=now + timedelta(hours=3),
        prediction_time=now,
        feature_snapshot_id=uuid4(),
        feature_snapshot_fingerprint="test_score_grid_snapshot",
        history_fingerprint="test_score_grid_history",
        feature_count=251,
        feature_values=[0.5] * 251,
        feature_keys=[f"f_{i}" for i in range(251)],
    )
    result = ShadowPredictionService().predict(inp)
    assert isinstance(result, ShadowPredictionResult)
    return result


@pytest.mark.parametrize("home,away", [(0, 0), (2, 2), (1, 4)])
def test_actual_score_probability_is_not_limited_to_top_five(home: int, away: int):
    prediction = _prediction()
    assert not any(
        item["home_goals"] == home and item["away_goals"] == away
        for item in prediction.top_scorelines
    ), "Chosen regression scoreline must be absent from top-five"

    outcome = PostMatchScoringService().score_prediction(
        prediction, actual_home_goals=home, actual_away_goals=away
    )
    full_grid = compute_score_matrix(
        prediction.lambda_home, prediction.lambda_away, max_goals=10
    )

    assert outcome.actual_score_probability == round(full_grid[home][away], 5)
    assert outcome.actual_score_probability > 0
    assert outcome.top1_score_hit is False
    assert outcome.top3_score_hit is False
    assert outcome.evaluation_schema_version == "1.1.0"


def test_exact_score_scoring_preserves_canonical_prediction():
    prediction = _prediction()
    original_top_scores = [dict(item) for item in prediction.top_scorelines]
    original_probs = (prediction.prob_home, prediction.prob_draw, prediction.prob_away)

    PostMatchScoringService().score_prediction(
        prediction, actual_home_goals=2, actual_away_goals=2
    )

    assert prediction.top_scorelines == original_top_scores
    assert (prediction.prob_home, prediction.prob_draw, prediction.prob_away) == original_probs
