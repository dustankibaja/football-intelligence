"""Integration-style tests against the REAL committed frozen C1/M1 artifacts.

Synthetic values below test serving mechanics only; they are NOT model-quality
evidence and are never published as live fixture predictions.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.models.dataset_dto import ModelingRowDTO
from app.prediction.research_shadow import ArtifactBackedResearchShadow

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def runner():
    return ArtifactBackedResearchShadow()


def sample_row(runner, *, reverse=False):
    comps = runner.m1.artifact.weights_payload["competitions"]
    comp_id, comp = next(
        (cid, data) for cid, data in comps.items()
        if len(data.get("team_attack_strengths", {})) >= 2
    )
    attacks = comp["team_attack_strengths"]
    ordered_teams = sorted(attacks, key=lambda team: attacks[team])
    home, away = ordered_teams[0], ordered_teams[-1]
    if reverse:
        home, away = away, home
    keys = runner.c1.artifact.preprocessor_artifact.ordered_input_features
    return ModelingRowDTO(
        match_id=uuid4(),
        competition_id=UUID(comp_id),
        season_id=uuid4(),
        home_team_id=UUID(home),
        away_team_id=UUID(away),
        kickoff_utc=NOW + timedelta(hours=24),
        dataset_release_key="mvp_a_historical:v1",
        dataset_release_fingerprint=(
            runner.c1.artifact.training_window.modeling_dataset_fingerprint
        ),
        feature_set_key="mvp_a_feature_set_v1",
        feature_set_fingerprint="e" * 64,
        ordered_feature_names=list(keys),
        feature_values=[0.0] * len(keys),
        targets={},
    )


def test_real_frozen_artifacts_load_and_score_independently(runner):
    row = sample_row(runner)
    result = runner.predict(row, NOW)

    assert result.lane == "RESEARCH_SHADOW"
    assert result.probability_status == "RAW_UNCALIBRATED"
    assert result.lambda_home > 0
    assert result.lambda_away > 0
    assert len(result.c1_artifact_sha256) == 64
    assert len(result.m1_artifact_sha256) == 64
    assert abs(sum(result.c1_1x2) - 1.0) < 1e-4
    assert abs(sum(result.m1_1x2) - 1.0) < 1e-4
    assert row.targets == {}


def test_learned_m1_teams_produce_distinct_expected_goals(runner):
    # Same synthetic feature numbers but a reversed team pairing; M1 must
    # use certified team parameters, not a constant-lambda surrogate.
    first = runner.predict(sample_row(runner), NOW)
    reversed_fixture = runner.predict(sample_row(runner, reverse=True), NOW)
    assert (first.lambda_home, first.lambda_away) != (
        reversed_fixture.lambda_home,
        reversed_fixture.lambda_away,
    )


def test_no_labels_or_post_kickoff_shadow_inference(runner):
    row = sample_row(runner)
    row.targets["home_goals_v1"] = 3
    with pytest.raises(ValueError, match="LABELS_NOT_PERMITTED"):
        runner.predict(row, NOW)
    row.targets.clear()
    with pytest.raises(ValueError, match="POST_KICKOFF"):
        runner.predict(row, row.kickoff_utc)


def test_wrong_feature_order_fails_closed(runner):
    row = sample_row(runner)
    row.ordered_feature_names[0], row.ordered_feature_names[1] = (
        row.ordered_feature_names[1], row.ordered_feature_names[0]
    )
    with pytest.raises(ValueError, match="CERTIFIED_251_FEATURE_SCHEMA_REQUIRED"):
        runner.predict(row, NOW)


def test_unseen_m1_team_cannot_fall_back_to_zero_strength(runner):
    row = sample_row(runner)
    row.home_team_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    with pytest.raises(ValueError, match="M1_UNSEEN_TEAM"):
        runner.predict(row, NOW)


def test_changed_frozen_artifact_is_rejected(runner, tmp_path):
    # Copy genuine files verbatim, mutate bytes, and confirm SHA guard rejects.
    backend_dir = Path(__file__).resolve().parents[2]
    for name in ("c1_artifact.json", "m1_artifact.json"):
        (tmp_path / name).write_bytes((backend_dir / name).read_bytes())
    with (tmp_path / "c1_artifact.json").open("ab") as f:
        f.write(b"\n")
    with pytest.raises(ValueError, match="FROZEN_MODEL_ARTIFACT_SHA256_MISMATCH"):
        ArtifactBackedResearchShadow(artifact_directory=tmp_path)
