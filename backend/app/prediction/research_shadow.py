"""Read-only, artifact-backed research shadow inference for canonical pre-match rows.

This is an independent challenger lane. It does not replace/publish champion
predictions, does not fit models, and does not claim calibration or live readiness.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import math
from pathlib import Path
from uuid import UUID

from app.models.baselines.attack_defense_poisson import AttackDefensePoissonModel
from app.models.baselines.multinomial_logistic import MultinomialLogistic1X2Model
from app.models.dataset_dto import ModelingRowDTO, assert_feature_label_isolation

# SHA-256 of the exact previously fitted, committed Phase 4/5 artifacts.
# These identifiers are pinned deliberately; an edited/retrained model must
# enter as a separate challenger with a new reviewed identity.
C1_ARTIFACT_SHA256 = "0fb3c6341eb4f027b2264b3bb9b5a89168f42e387c7e457e01e7fa6e4f417a35"
M1_ARTIFACT_SHA256 = "0279434d8b4d03b2b3e6ccb50e8d74d0d157ca4d58487413b1e90b0c59f56cdb"
C1_PAYLOAD_FINGERPRINT = "6328ef1d0b175964"
M1_PAYLOAD_FINGERPRINT = "e116413051f52d5f"
EXPECTED_TRAINING_DATASET_FINGERPRINT = (
    "9b9ceb90da803eaa498c26a070d4ac69483fa4d06c391444b2c326f3fa03021c"
)
EXPECTED_FEATURE_WIDTH = 251


@dataclass(frozen=True)
class ResearchShadowForecast:
    fixture_id: UUID
    predicted_at_utc: datetime
    competition_id: UUID
    c1_1x2: tuple[float, float, float]
    m1_1x2: tuple[float, float, float]
    lambda_home: float
    lambda_away: float
    btts_yes: float
    over_2_5: float
    c1_artifact_sha256: str
    m1_artifact_sha256: str
    feature_set_fingerprint: str
    lane: str = "RESEARCH_SHADOW"
    probability_status: str = "RAW_UNCALIBRATED"


class ArtifactBackedResearchShadow:
    """Run *actual trained* C1 and M1 side by side; never serve to production."""

    def __init__(self, artifact_directory: Path | None = None) -> None:
        base = Path(artifact_directory) if artifact_directory else Path(__file__).resolve().parents[2]
        c1_path = base / "c1_artifact.json"
        m1_path = base / "m1_artifact.json"
        self._verify_file(c1_path, C1_ARTIFACT_SHA256)
        self._verify_file(m1_path, M1_ARTIFACT_SHA256)

        self.c1 = MultinomialLogistic1X2Model()
        self.m1 = AttackDefensePoissonModel()
        self.c1.load_artifact(str(c1_path))
        self.m1.load_artifact(str(m1_path))
        for model, expected_key, expected_fingerprint in (
            (self.c1, "multinomial_logistic_1x2_v1", C1_PAYLOAD_FINGERPRINT),
            (self.m1, "attack_defense_poisson_v1", M1_PAYLOAD_FINGERPRINT),
        ):
            artifact = model.artifact
            if (
                artifact is None
                or artifact.model_key != expected_key
                or artifact.artifact_fingerprint != expected_fingerprint
                or not artifact.verify_integrity()
                or artifact.training_window.modeling_dataset_fingerprint
                != EXPECTED_TRAINING_DATASET_FINGERPRINT
                or artifact.training_window.row_count != 2892
                or artifact.feature_set_key != "mvp_a_feature_set_v1"
            ):
                raise ValueError("UNVERIFIED_FROZEN_MODEL_ARTIFACT")
        if (
            self.c1.artifact.preprocessor_artifact.ordered_input_features
            != self.m1.artifact.preprocessor_artifact.ordered_input_features
        ):
            raise ValueError("MODEL_FEATURE_ORDER_MISMATCH")

    @staticmethod
    def _verify_file(path: Path, expected_sha256: str) -> None:
        if not path.is_file():
            raise ValueError("FROZEN_MODEL_ARTIFACT_MISSING")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha256:
            raise ValueError("FROZEN_MODEL_ARTIFACT_SHA256_MISMATCH")

    def predict(
        self, row: ModelingRowDTO, prediction_time: datetime
    ) -> ResearchShadowForecast:
        """Score an independently materialized feature vector; do not create one."""
        if prediction_time.tzinfo is None or row.kickoff_utc.tzinfo is None:
            raise ValueError("UTC_AWARE_TIMESTAMPS_REQUIRED")
        prediction_time = prediction_time.astimezone(UTC)
        if prediction_time >= row.kickoff_utc.astimezone(UTC):
            raise ValueError("POST_KICKOFF_PREDICTION_PROHIBITED")
        if row.targets:
            raise ValueError("LABELS_NOT_PERMITTED_IN_PREMATCH_SHADOW_INFERENCE")
        expected_features = self.c1.artifact.preprocessor_artifact.ordered_input_features
        if (
            row.feature_set_key != "mvp_a_feature_set_v1"
            or len(row.ordered_feature_names) != EXPECTED_FEATURE_WIDTH
            or row.ordered_feature_names != expected_features
            or len(row.feature_values) != EXPECTED_FEATURE_WIDTH
        ):
            raise ValueError("CERTIFIED_251_FEATURE_SCHEMA_REQUIRED")
        assert_feature_label_isolation(row.ordered_feature_names)
        if (
            len(row.feature_set_fingerprint) != 64
            or len(row.dataset_release_fingerprint) != 64
        ):
            raise ValueError("FEATURE_AND_DATASET_PROVENANCE_REQUIRED")
        for value in row.feature_values:
            if value is not None and (
                not isinstance(value, (int, float)) or not math.isfinite(value)
            ):
                raise ValueError("NONFINITE_FEATURE_VALUE")

        # M1 must use a learned competition and both learned team identities,
        # never its permissive fallback / fabricated zero-strength parameters.
        comp = self.m1.artifact.weights_payload["competitions"].get(
            str(row.competition_id)
        )
        if not comp or not row.home_team_id or not row.away_team_id:
            raise ValueError("M1_CERTIFIED_COMPETITION_OR_TEAM_UNAVAILABLE")
        if row.home_team_id == row.away_team_id:
            raise ValueError("INVALID_FIXTURE_SAME_TEAM")
        known_teams = comp.get("team_attack_strengths", {})
        if str(row.home_team_id) not in known_teams or str(row.away_team_id) not in known_teams:
            raise ValueError("M1_UNSEEN_TEAM_REQUIRES_SEPARATE_COLD_START_POLICY")

        c1 = self.c1.predict_raw(row)
        m1 = self.m1.predict_raw(row)
        if m1.predicted_home_goals is None or m1.predicted_away_goals is None:
            raise ValueError("M1_DID_NOT_RETURN_EXPECTED_GOALS")

        c1_probs = (c1.prob_home_win, c1.prob_draw, c1.prob_away_win)
        m1_probs = (m1.prob_home_win, m1.prob_draw, m1.prob_away_win)
        for probs in (c1_probs, m1_probs):
            if not all(math.isfinite(p) and 0 <= p <= 1 for p in probs):
                raise ValueError("INVALID_MODEL_PROBABILITIES")
            if not math.isclose(sum(probs), 1.0, abs_tol=1e-4):
                raise ValueError("UNNORMALIZED_MODEL_PROBABILITIES")

        return ResearchShadowForecast(
            fixture_id=row.match_id,
            predicted_at_utc=prediction_time,
            competition_id=row.competition_id,
            c1_1x2=c1_probs,
            m1_1x2=m1_probs,
            lambda_home=m1.predicted_home_goals,
            lambda_away=m1.predicted_away_goals,
            btts_yes=m1.prob_btts_yes,
            over_2_5=m1.prob_over_25,
            c1_artifact_sha256=C1_ARTIFACT_SHA256,
            m1_artifact_sha256=M1_ARTIFACT_SHA256,
            feature_set_fingerprint=row.feature_set_fingerprint,
        )
