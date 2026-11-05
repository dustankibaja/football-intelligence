"""Regression for the top-five exact-score scoring defect."""
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.features.manifest import get_mvp_a_feature_set_v1_manifest
from app.prediction.domain import ShadowPredictionInput
from app.prediction.frozen_inference import FrozenArtifactInference
from app.prediction.scoring import PostMatchScoringService
from app.prediction.service import ShadowPredictionService


def _prediction():
    frozen = FrozenArtifactInference()
    competition_id, comp = next(iter(frozen.m1.artifact.weights_payload["competitions"].items()))
    now = datetime(2026, 11, 5, 12, tzinfo=UTC)
    inp = ShadowPredictionInput(
        fixture_id=uuid4(),
        competition_code="E0",
        competition_id=UUID(competition_id),
        season_id=uuid4(),
        season_year="2026/27",
        home_team_id=UUID(comp["team_list"][0]),
        away_team_id=UUID(comp["team_list"][1]),
        kickoff_utc=now + timedelta(hours=2),
        prediction_time=now,
        feature_snapshot_id=uuid4(),
        feature_snapshot_fingerprint="c" * 64,
        history_fingerprint="d" * 64,
        feature_count=251,
        feature_values=[0.25] * 251,
        feature_keys=get_mvp_a_feature_set_v1_manifest().included_feature_keys,
    )
    return ShadowPredictionService(frozen_inference=frozen).predict(inp)


def test_actual_score_probability_uses_full_distribution_outside_top_five():
    pred = _prediction()
    assert pred.score_matrix is not None
    assert not any(
        s["home_goals"] == 4 and s["away_goals"] == 3
        for s in pred.top_scorelines
    )
    ev = PostMatchScoringService().score_prediction(
        pred, actual_home_goals=4, actual_away_goals=3
    )
    assert ev.actual_score_probability == pytest.approx(pred.score_matrix[4][3])
    assert ev.actual_score_probability > 0
    assert not ev.top1_score_hit
    assert not ev.top3_score_hit


def test_tampered_full_distribution_fails_integrity():
    pred = _prediction()
    pred.score_matrix[0][0] = 0.5
    with pytest.raises(ValueError, match="SCORE_MATRIX_FINGERPRINT_MISMATCH"):
        PostMatchScoringService().score_prediction(
            pred, actual_home_goals=0, actual_away_goals=0
        )
