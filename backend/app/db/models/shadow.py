"""
Live Shadow Prediction & Evaluation ORM Models
Append-only immutable prediction records, separate post-match evaluations, and observational monitoring alerts.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, BaseMixin

if TYPE_CHECKING:
    from app.db.models.core import Match
    from app.db.models.features import FeatureSnapshot


class ShadowPredictionRun(Base, BaseMixin):
    """Batch / Execution run for shadow predictions."""
    __tablename__ = "shadow_prediction_runs"

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="SUCCESS", nullable=False, index=True)
    candidate_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    eligible_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    predicted_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    fallback_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    blocked_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    run_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    predictions: Mapped[list[ShadowPrediction]] = relationship("ShadowPrediction", back_populates="prediction_run")


class ShadowPrediction(Base, BaseMixin):
    """Append-only immutable pre-match shadow prediction snapshot."""
    __tablename__ = "shadow_predictions"
    __table_args__ = (
        CheckConstraint("prob_home >= 0 AND prob_home <= 1", name="chk_shadow_prob_home"),
        CheckConstraint("prob_draw >= 0 AND prob_draw <= 1", name="chk_shadow_prob_draw"),
        CheckConstraint("prob_away >= 0 AND prob_away <= 1", name="chk_shadow_prob_away"),
        CheckConstraint("abs(prob_home + prob_draw + prob_away - 1.0) <= 0.01", name="chk_shadow_prob_sum"),
        CheckConstraint("abs(btts_yes + btts_no - 1.0) <= 0.01", name="chk_shadow_btts_sum"),
        CheckConstraint("abs(over_2_5 + under_2_5 - 1.0) <= 0.01", name="chk_shadow_ou25_sum"),
    )

    fixture_id: Mapped[UUID] = mapped_column(
        ForeignKey("matches.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    prediction_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("shadow_prediction_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    prediction_mode: Mapped[str] = mapped_column(String(20), default="SHADOW", nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False, index=True)
    kickoff_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    lead_time_seconds: Mapped[int] = mapped_column(Integer, nullable=False)

    feature_snapshot_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("feature_snapshots.id", ondelete="SET NULL"), nullable=True
    )
    feature_snapshot_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    history_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    phase5_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    phase5_2_policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    phase6_1_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    phase6_2_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    c1_model_key: Mapped[str] = mapped_column(String(50), nullable=False)
    c1_model_version: Mapped[str] = mapped_column(String(20), nullable=False)
    c1_artifact_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    m1_model_key: Mapped[str] = mapped_column(String(50), nullable=False)
    m1_model_version: Mapped[str] = mapped_column(String(20), nullable=False)
    m1_artifact_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    # Core 1X2 Probabilities
    prob_home: Mapped[float] = mapped_column(Numeric(6, 5), nullable=False)
    prob_draw: Mapped[float] = mapped_column(Numeric(6, 5), nullable=False)
    prob_away: Mapped[float] = mapped_column(Numeric(6, 5), nullable=False)

    # M1 Structural Diagnostics
    structural_prob_home: Mapped[float] = mapped_column(Numeric(6, 5), nullable=False)
    structural_prob_draw: Mapped[float] = mapped_column(Numeric(6, 5), nullable=False)
    structural_prob_away: Mapped[float] = mapped_column(Numeric(6, 5), nullable=False)

    # Poisson Expected Goals
    lambda_home: Mapped[float] = mapped_column(Numeric(6, 3), nullable=False)
    lambda_away: Mapped[float] = mapped_column(Numeric(6, 3), nullable=False)

    # Goal Market Probabilities
    btts_yes: Mapped[float] = mapped_column(Numeric(6, 5), nullable=False)
    btts_no: Mapped[float] = mapped_column(Numeric(6, 5), nullable=False)
    over_2_5: Mapped[float] = mapped_column(Numeric(6, 5), nullable=False)
    under_2_5: Mapped[float] = mapped_column(Numeric(6, 5), nullable=False)

    exact_score_matrix_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    top_scorelines: Mapped[list] = mapped_column(JSON, nullable=False)
    score_matrix: Mapped[list | None] = mapped_column(JSON, nullable=True)

    quality_state: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    quality_reasons: Mapped[list | None] = mapped_column(JSON, nullable=True)

    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    fallback_model: Mapped[str | None] = mapped_column(String(50), nullable=True)
    fallback_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    model_disagreement_value: Mapped[float] = mapped_column(Numeric(6, 5), nullable=False)
    model_disagreement_band: Mapped[str] = mapped_column(String(20), nullable=False)

    is_canonical_evaluation_prediction: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    prediction_schema_version: Mapped[str] = mapped_column(String(20), default="1.0.0", nullable=False)

    match: Mapped[Match] = relationship("Match")
    prediction_run: Mapped[ShadowPredictionRun | None] = relationship("ShadowPredictionRun", back_populates="predictions")
    feature_snapshot: Mapped[FeatureSnapshot | None] = relationship("FeatureSnapshot")
    evaluations: Mapped[list[ShadowEvaluation]] = relationship("ShadowEvaluation", back_populates="prediction")


class ShadowEvaluation(Base, BaseMixin):
    """Separate post-match evaluation record scored against certified terminal result."""
    __tablename__ = "shadow_evaluations"
    __table_args__ = (
        UniqueConstraint("prediction_id", "result_version", name="uq_shadow_evaluation_prediction_version"),
    )

    prediction_id: Mapped[UUID] = mapped_column(
        ForeignKey("shadow_predictions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    fixture_id: Mapped[UUID] = mapped_column(
        ForeignKey("matches.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    result_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    result_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    actual_home_goals: Mapped[int] = mapped_column(Integer, nullable=False)
    actual_away_goals: Mapped[int] = mapped_column(Integer, nullable=False)
    actual_1x2: Mapped[str] = mapped_column(String(10), nullable=False)
    actual_btts: Mapped[bool] = mapped_column(Boolean, nullable=False)
    actual_over_2_5: Mapped[bool] = mapped_column(Boolean, nullable=False)

    log_loss_1x2: Mapped[float] = mapped_column(Numeric(8, 5), nullable=False)
    brier_1x2: Mapped[float] = mapped_column(Numeric(8, 5), nullable=False)
    btts_log_loss: Mapped[float] = mapped_column(Numeric(8, 5), nullable=False)
    btts_brier: Mapped[float] = mapped_column(Numeric(8, 5), nullable=False)
    ou25_log_loss: Mapped[float] = mapped_column(Numeric(8, 5), nullable=False)
    ou25_brier: Mapped[float] = mapped_column(Numeric(8, 5), nullable=False)

    actual_score_probability: Mapped[float] = mapped_column(Numeric(6, 5), nullable=False)
    top1_score_hit: Mapped[bool] = mapped_column(Boolean, nullable=False)
    top3_score_hit: Mapped[bool] = mapped_column(Boolean, nullable=False)

    evaluation_schema_version: Mapped[str] = mapped_column(String(20), default="1.0.0", nullable=False)

    prediction: Mapped[ShadowPrediction] = relationship("ShadowPrediction", back_populates="evaluations")
    match: Mapped[Match] = relationship("Match")


class MonitoringAlert(Base, BaseMixin):
    """Observational monitoring alert record."""
    __tablename__ = "monitoring_alerts"

    severity: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    metric: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    value: Mapped[float] = mapped_column(Numeric(10, 5), nullable=False)
    threshold: Mapped[float] = mapped_column(Numeric(10, 5), nullable=False)
    window: Mapped[int] = mapped_column(Integer, nullable=False)
    model_identity: Mapped[str] = mapped_column(String(100), nullable=False)
    prediction_cohort: Mapped[str] = mapped_column(String(100), nullable=False)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    metadata_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
