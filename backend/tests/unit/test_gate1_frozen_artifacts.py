"""Independent Gate 1 verification of the actual committed frozen model artifacts."""
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.features.manifest import get_mvp_a_feature_set_v1_manifest
from app.prediction.domain import ShadowPredictionInput
from app.prediction.frozen_inference import (
    EXPECTED_C1_FINGERPRINT,
    EXPECTED_M1_FINGERPRINT,
    FROZEN_COMPETITION_CODES,
    FrozenArtifactInference,
)
from app.prediction.service import ShadowPredictionService


def _sample_input(engine: FrozenArtifactInference):
    comp_id_str, comp = next(iter(engine.m1.artifact.weights_payload["competitions"].items()))
    teams = comp["team_list"]
    assert len(teams) >= 3
    manifest = get_mvp_a_feature_set_v1_manifest()
    assert manifest.feature_count == 251
    now = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    return ShadowPredictionInput(
        fixture_id=uuid4(),
        competition_code=FROZEN_COMPETITION_CODES[comp_id_str],
        competition_id=UUID(comp_id_str),
        season_id=uuid4(),
        season_year="2026/27",
        home_team_id=UUID(teams[0]),
        away_team_id=UUID(teams[1]),
        kickoff_utc=now + timedelta(hours=5),
        prediction_time=now,
        feature_snapshot_id=uuid4(),
        feature_snapshot_fingerprint="a" * 64,
        history_fingerprint="b" * 64,
        feature_count=251,
        feature_values=[0.2] * 251,
        feature_keys=manifest.included_feature_keys,
    ), teams


def test_actual_frozen_artifacts_are_loaded_and_verified():
    engine = FrozenArtifactInference()
    engine.verify()
    assert engine.c1.artifact.artifact_fingerprint == EXPECTED_C1_FINGERPRINT
    assert engine.m1.artifact.artifact_fingerprint == EXPECTED_M1_FINGERPRINT
    assert len(engine.c1_file_sha256) == 64
    assert len(engine.m1_file_sha256) == 64


def test_real_model_forward_pass_and_team_specific_goals():
    engine = FrozenArtifactInference()
    inp, teams = _sample_input(engine)
    out = engine.predict(inp, inp.competition_id, inp.season_id)
    assert abs(sum(out.c1_probabilities) - 1) < 1e-5
    assert abs(sum(out.m1_probabilities) - 1) < 1e-5
    assert out.lambda_home > 0 and out.lambda_away > 0
    assert len(out.score_matrix) == 11
    assert len(out.score_matrix[0]) == 11
    assert len(out.top_scorelines) == 5
    assert len(out.score_matrix_fingerprint) == 64

    # The frozen M1 team attack/defence weights must affect lambdas.
    inp.away_team_id = UUID(teams[2])
    second = engine.predict(inp, inp.competition_id, inp.season_id)
    assert (out.lambda_home, out.lambda_away) != (second.lambda_home, second.lambda_away)


def test_frozen_forward_pass_used_by_shadow_prediction_service():
    engine = FrozenArtifactInference()
    inp, _ = _sample_input(engine)
    svc = ShadowPredictionService(frozen_inference=engine)
    svc.assert_live_inference_ready()
    result = svc.predict(inp)
    assert result.c1_artifact_fingerprint == EXPECTED_C1_FINGERPRINT
    assert result.m1_artifact_fingerprint == EXPECTED_M1_FINGERPRINT
    assert result.feature_snapshot_id == inp.feature_snapshot_id
    assert result.prediction_run_id is None
    assert result.lambda_home > 0
    assert result.lambda_away > 0


def test_frozen_forward_pass_rejects_fake_feature_order():
    engine = FrozenArtifactInference()
    inp, _ = _sample_input(engine)
    inp.feature_keys = list(reversed(inp.feature_keys))
    with pytest.raises(ValueError, match="FROZEN_FEATURE_SCHEMA_MISMATCH"):
        engine.predict(inp, inp.competition_id, inp.season_id)


def test_frozen_forward_pass_rejects_unseen_competition():
    engine = FrozenArtifactInference()
    inp, _ = _sample_input(engine)
    with pytest.raises(ValueError, match="M1_COMPETITION_NOT_IN_FROZEN_ARTIFACT"):
        engine.predict(inp, uuid4(), inp.season_id)


def test_unseen_team_triggers_certified_m1_fallback():
    engine = FrozenArtifactInference()
    inp, _ = _sample_input(engine)
    inp.away_team_id = uuid4()
    pred = ShadowPredictionService(frozen_inference=engine).predict(inp)
    assert pred.fallback_used is True
    assert pred.fallback_model == "M1_Attack_Defense_Poisson"
    assert pred.quality_state == "FALLBACK_MODEL_USED"
    assert pred.prob_home == pred.structural_prob_home


def test_frozen_model_forward_pass_covers_all_four_competitions():
    engine = FrozenArtifactInference()
    inp, _ = _sample_input(engine)
    pairs = []
    for comp_id, comp in engine.m1.artifact.weights_payload["competitions"].items():
        inp.competition_id = UUID(comp_id)
        inp.competition_code = FROZEN_COMPETITION_CODES[comp_id]
        inp.home_team_id = UUID(comp["team_list"][0])
        inp.away_team_id = UUID(comp["team_list"][1])
        out = engine.predict(inp, inp.competition_id, inp.season_id)
        assert out.lambda_home > 0 and out.lambda_away > 0
        assert abs(sum(out.c1_probabilities) - 1.0) < 1e-5
        assert abs(sum(out.m1_probabilities) - 1.0) < 1e-5
        pairs.append((out.lambda_home, out.lambda_away))
    assert len(pairs) == 4
    assert len(set(pairs)) >= 2
