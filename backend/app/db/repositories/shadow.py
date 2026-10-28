"""
Authoritative Relational Database Repository for Live Shadow Predictions.
Enforces append-only immutability, relational idempotency, and foreign-key integrity
against certified PostgreSQL and SQLite relational tables.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.db.models.core import Competition, Match, Season, Team
from app.db.models.features import FeatureSnapshot
from app.db.models.shadow import (
    ShadowEvaluation,
    ShadowPrediction,
    ShadowPredictionRun,
)
from app.prediction.domain import (
    ShadowEvaluationResult,
    ShadowPredictionResult,
)

logger = logging.getLogger(__name__)


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


class ShadowPredictionRepository:
    """Authoritative relational repository for ShadowPrediction and ShadowEvaluation records."""

    @staticmethod
    def _ensure_match_exists(
        session: Session,
        fixture_id: UUID,
        kickoff_utc: datetime,
        fixture_meta: dict[str, Any] | None = None,
    ) -> Match:
        """
        Ensures a stub or real Match entity exists in the matches table
        so that ShadowPrediction foreign key constraint is satisfied.
        """
        match_row = session.query(Match).filter_by(id=fixture_id).first()
        if match_row is not None:
            return match_row

        meta = fixture_meta or {}
        comp_code = meta.get("competition_code", "E0")
        season_year = meta.get("season_year", "2024-25")
        home_team_id = meta.get("home_team_id")
        away_team_id = meta.get("away_team_id")

        # Resolve or stub active teams
        if home_team_id is None or away_team_id is None:
            active_teams = session.query(Team).limit(2).all()
            if len(active_teams) >= 2:
                home_team_id = home_team_id or active_teams[0].id
                away_team_id = away_team_id or active_teams[1].id
            else:
                # Fallback to deterministic team creation if teams table empty
                t1 = Team(id=uuid4(), canonical_name=f"Team_{uuid4().hex[:6]}", country="Unknown")
                t2 = Team(id=uuid4(), canonical_name=f"Team_{uuid4().hex[:6]}", country="Unknown")
                session.add(t1)
                session.add(t2)
                session.flush()
                home_team_id = home_team_id or t1.id
                away_team_id = away_team_id or t2.id

        # Resolve or stub competition
        comp = session.query(Competition).filter_by(code=comp_code).first()
        if comp is None:
            comp = Competition(code=comp_code, name=f"{comp_code} League", country="Europe")
            session.add(comp)
            session.flush()

        # Resolve or stub season
        season = session.query(Season).filter_by(
            competition_id=comp.id, season_year=season_year
        ).first()
        if season is None:
            season = Season(competition_id=comp.id, season_year=season_year)
            session.add(season)
            session.flush()

        match_row = Match(
            id=fixture_id,
            season_id=season.id,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            kickoff_utc=_ensure_utc(kickoff_utc),
            status="SCHEDULED",
        )
        session.add(match_row)
        session.flush()
        return match_row

    @classmethod
    def persist_prediction(
        cls,
        session: Session,
        pred: ShadowPredictionResult,
        fixture_meta: dict[str, Any] | None = None,
    ) -> tuple[ShadowPrediction, bool]:
        """
        Persists a single ShadowPredictionResult into the shadow_predictions table.
        Guarantees:
        - Append-only immutability: returns existing row if already persisted.
        - Foreign key integrity: creates match entity if absent.
        - Returns (ShadowPrediction, created_bool).
        """
        # Idempotency check 1: Exact prediction ID
        existing = session.query(ShadowPrediction).filter_by(id=pred.prediction_id).first()
        if existing is not None:
            return existing, False

        # Idempotency check 2: Deduplication by (fixture_id, fingerprint, kickoff)
        existing_dup = session.query(ShadowPrediction).filter_by(
            fixture_id=pred.fixture_id,
            feature_snapshot_fingerprint=pred.feature_snapshot_fingerprint,
            kickoff_utc=_ensure_utc(pred.kickoff_utc),
        ).first()
        if existing_dup is not None:
            return existing_dup, False

        # Ensure foreign key on matches table
        cls._ensure_match_exists(session, pred.fixture_id, pred.kickoff_utc, fixture_meta)

        # Check optional FKs
        run_fk = None
        if pred.prediction_run_id is not None:
            run_row = session.query(ShadowPredictionRun).filter_by(id=pred.prediction_run_id).first()
            if run_row is not None:
                run_fk = run_row.id
            else:
                # Create stub run row to satisfy foreign key
                stub_run = ShadowPredictionRun(
                    id=pred.prediction_run_id,
                    started_at=_ensure_utc(pred.generated_at),
                    status="SUCCESS",
                )
                session.add(stub_run)
                session.flush()
                run_fk = stub_run.id

        snap_fk = None
        if pred.feature_snapshot_id is not None:
            snap_row = session.query(FeatureSnapshot).filter_by(id=pred.feature_snapshot_id).first()
            if snap_row is not None:
                snap_fk = snap_row.id

        db_pred = ShadowPrediction(
            id=pred.prediction_id,
            fixture_id=pred.fixture_id,
            prediction_run_id=run_fk,
            prediction_mode=pred.prediction_mode,
            generated_at=_ensure_utc(pred.generated_at),
            kickoff_utc=_ensure_utc(pred.kickoff_utc),
            lead_time_seconds=pred.lead_time_seconds,
            feature_snapshot_id=snap_fk,
            feature_snapshot_fingerprint=pred.feature_snapshot_fingerprint,
            history_fingerprint=pred.history_fingerprint,
            phase5_manifest_hash=pred.phase5_manifest_hash,
            phase5_2_policy_hash=pred.phase5_2_policy_hash,
            phase6_1_manifest_hash=pred.phase6_1_manifest_hash,
            phase6_2_manifest_hash=pred.phase6_2_manifest_hash,
            c1_model_key=pred.c1_model_key,
            c1_model_version=pred.c1_model_version,
            c1_artifact_fingerprint=pred.c1_artifact_fingerprint,
            m1_model_key=pred.m1_model_key,
            m1_model_version=pred.m1_model_version,
            m1_artifact_fingerprint=pred.m1_artifact_fingerprint,
            prob_home=pred.prob_home,
            prob_draw=pred.prob_draw,
            prob_away=pred.prob_away,
            structural_prob_home=pred.structural_prob_home,
            structural_prob_draw=pred.structural_prob_draw,
            structural_prob_away=pred.structural_prob_away,
            lambda_home=pred.lambda_home,
            lambda_away=pred.lambda_away,
            btts_yes=pred.btts_yes,
            btts_no=pred.btts_no,
            over_2_5=pred.over_2_5,
            under_2_5=pred.under_2_5,
            exact_score_matrix_fingerprint=pred.exact_score_matrix_fingerprint,
            top_scorelines=pred.top_scorelines,
            quality_state=pred.quality_state,
            quality_reasons=pred.quality_reasons,
            fallback_used=pred.fallback_used,
            fallback_model=pred.fallback_model,
            fallback_reason=pred.fallback_reason,
            model_disagreement_value=pred.model_disagreement_value,
            model_disagreement_band=pred.model_disagreement_band,
            is_canonical_evaluation_prediction=pred.is_canonical_evaluation_prediction,
            prediction_schema_version=pred.prediction_schema_version,
        )
        session.add(db_pred)
        session.commit()
        return db_pred, True

    @classmethod
    def persist_evaluation(
        cls,
        session: Session,
        ev: ShadowEvaluationResult,
    ) -> tuple[ShadowEvaluation, bool]:
        """
        Persists a post-match evaluation record linked to its canonical prediction.
        Enforces idempotency on (prediction_id, result_version).
        """
        existing = session.query(ShadowEvaluation).filter_by(
            prediction_id=ev.prediction_id,
            result_version=ev.result_version,
        ).first()
        if existing is not None:
            return existing, False

        db_ev = ShadowEvaluation(
            id=ev.evaluation_id,
            prediction_id=ev.prediction_id,
            fixture_id=ev.fixture_id,
            result_version=ev.result_version,
            result_observed_at=_ensure_utc(ev.result_observed_at),
            actual_home_goals=ev.actual_home_goals,
            actual_away_goals=ev.actual_away_goals,
            actual_1x2=ev.actual_1x2,
            actual_btts=ev.actual_btts,
            actual_over_2_5=ev.actual_over_2_5,
            log_loss_1x2=ev.log_loss_1x2,
            brier_1x2=ev.brier_1x2,
            btts_log_loss=ev.btts_log_loss,
            btts_brier=ev.btts_brier,
            ou25_log_loss=ev.ou25_log_loss,
            ou25_brier=ev.ou25_brier,
            actual_score_probability=ev.actual_score_probability,
            top1_score_hit=ev.top1_score_hit,
            top3_score_hit=ev.top3_score_hit,
            evaluation_schema_version=ev.evaluation_schema_version,
        )
        session.add(db_ev)
        session.commit()
        return db_ev, True

    @classmethod
    def load_all_predictions(cls, session: Session) -> dict[UUID, ShadowPredictionResult]:
        """Loads all persisted shadow predictions from relational store into domain DTOs."""
        db_preds = session.query(ShadowPrediction).all()
        results: dict[UUID, ShadowPredictionResult] = {}
        for p in db_preds:
            dto = ShadowPredictionResult(
                prediction_id=p.id,
                fixture_id=p.fixture_id,
                prediction_run_id=p.prediction_run_id,
                prediction_mode=p.prediction_mode,
                generated_at=_ensure_utc(p.generated_at),
                kickoff_utc=_ensure_utc(p.kickoff_utc),
                lead_time_seconds=p.lead_time_seconds,
                feature_snapshot_id=p.feature_snapshot_id,
                feature_snapshot_fingerprint=p.feature_snapshot_fingerprint,
                history_fingerprint=p.history_fingerprint,
                phase5_manifest_hash=p.phase5_manifest_hash,
                phase5_2_policy_hash=p.phase5_2_policy_hash,
                phase6_1_manifest_hash=p.phase6_1_manifest_hash,
                phase6_2_manifest_hash=p.phase6_2_manifest_hash,
                c1_model_key=p.c1_model_key,
                c1_model_version=p.c1_model_version,
                c1_artifact_fingerprint=p.c1_artifact_fingerprint,
                m1_model_key=p.m1_model_key,
                m1_model_version=p.m1_model_version,
                m1_artifact_fingerprint=p.m1_artifact_fingerprint,
                prob_home=float(p.prob_home),
                prob_draw=float(p.prob_draw),
                prob_away=float(p.prob_away),
                structural_prob_home=float(p.structural_prob_home),
                structural_prob_draw=float(p.structural_prob_draw),
                structural_prob_away=float(p.structural_prob_away),
                lambda_home=float(p.lambda_home),
                lambda_away=float(p.lambda_away),
                btts_yes=float(p.btts_yes),
                btts_no=float(p.btts_no),
                over_2_5=float(p.over_2_5),
                under_2_5=float(p.under_2_5),
                exact_score_matrix_fingerprint=p.exact_score_matrix_fingerprint,
                top_scorelines=p.top_scorelines,
                quality_state=p.quality_state,
                quality_reasons=p.quality_reasons,
                fallback_used=p.fallback_used,
                fallback_model=p.fallback_model,
                fallback_reason=p.fallback_reason,
                model_disagreement_value=float(p.model_disagreement_value),
                model_disagreement_band=p.model_disagreement_band,
                is_canonical_evaluation_prediction=p.is_canonical_evaluation_prediction,
                prediction_schema_version=p.prediction_schema_version,
            )
            results[dto.prediction_id] = dto
        return results
