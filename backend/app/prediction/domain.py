"""
Shadow Prediction Domain Models & DTOs
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class ScorelineProbability:
    home_goals: int
    away_goals: int
    probability: float
    rank: int


@dataclass
class ShadowPredictionInput:
    fixture_id: UUID
    competition_code: str
    season_year: str
    home_team_id: UUID
    away_team_id: UUID
    kickoff_utc: datetime
    prediction_time: datetime
    feature_snapshot_id: UUID | None
    feature_snapshot_fingerprint: str
    history_fingerprint: str
    feature_count: int
    feature_values: list[float | None]
    feature_keys: list[str]
    snapshot_quality_state: str = "VALID"
    missingness_count: int = 0
    drift_psi: float = 0.0
    known_cold_start: bool = False
    critical_feature_absent: bool = False
    competition_id: UUID | None = None
    season_id: UUID | None = None


@dataclass
class ShadowPredictionResult:
    prediction_id: UUID
    fixture_id: UUID
    prediction_run_id: UUID | None
    prediction_mode: str
    generated_at: datetime
    kickoff_utc: datetime
    lead_time_seconds: int

    feature_snapshot_id: UUID | None
    feature_snapshot_fingerprint: str
    history_fingerprint: str

    phase5_manifest_hash: str
    phase5_2_policy_hash: str
    phase6_1_manifest_hash: str
    phase6_2_manifest_hash: str

    c1_model_key: str
    c1_model_version: str
    c1_artifact_fingerprint: str

    m1_model_key: str
    m1_model_version: str
    m1_artifact_fingerprint: str

    prob_home: float
    prob_draw: float
    prob_away: float

    structural_prob_home: float
    structural_prob_draw: float
    structural_prob_away: float

    lambda_home: float
    lambda_away: float

    btts_yes: float
    btts_no: float

    over_2_5: float
    under_2_5: float

    exact_score_matrix_fingerprint: str
    top_scorelines: list[dict[str, Any]]

    quality_state: str
    quality_reasons: list[str]

    fallback_used: bool
    fallback_model: str | None
    fallback_reason: str | None

    model_disagreement_value: float
    model_disagreement_band: str

    is_canonical_evaluation_prediction: bool
    prediction_schema_version: str = "1.0.0"
    # Complete frozen goal distribution; retained for exact-score evaluation.
    score_matrix: list[list[float]] | None = None

    @property
    def effective_model_1x2(self) -> str:
        """Returns the effective 1X2 model: fallback model if triggered, else C1."""
        return self.fallback_model if self.fallback_used and self.fallback_model else self.c1_model_key


@dataclass
class BlockedDecisionRecord:
    fixture_id: UUID
    decision_time: datetime
    reason: str
    policy_hash: str
    snapshot_fingerprint: str
    quality_state: str = "BLOCKED"


@dataclass
class ShadowEvaluationResult:
    evaluation_id: UUID
    prediction_id: UUID
    fixture_id: UUID
    result_version: int
    result_observed_at: datetime

    actual_home_goals: int
    actual_away_goals: int
    actual_1x2: str
    actual_btts: bool
    actual_over_2_5: bool

    log_loss_1x2: float
    brier_1x2: float
    btts_log_loss: float
    btts_brier: float
    ou25_log_loss: float
    ou25_brier: float

    actual_score_probability: float
    top1_score_hit: bool
    top3_score_hit: bool
    evaluation_schema_version: str = "1.0.0"


@dataclass
class MonitoringAlertDTO:
    alert_id: UUID
    created_at: datetime
    severity: str
    metric: str
    value: float
    threshold: float
    window: int
    model_identity: str
    prediction_cohort: str
    reason: str
    metadata_payload: dict[str, Any] | None = None
