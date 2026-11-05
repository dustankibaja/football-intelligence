"""Post-match evaluations and aggregate performance for verified forecasts."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.v1.endpoints.predictions import _verified
from app.api.v1.security import require_customer_api_token
from app.db.models.shadow import ShadowEvaluation
from app.db.session import get_db
from app.evaluation.summary import summarize_evaluations

router = APIRouter(dependencies=[Depends(require_customer_api_token)])

# A bounded scan is sufficient for the 60-day private trial. If reached, the
# summary explicitly declares truncation rather than presenting partial data
# as an all-time performance report.
MAX_EVALUATIONS_SCANNED = 5000


def _latest_verified_evaluations(db: Session) -> tuple[list[ShadowEvaluation], bool]:
    rows = db.query(ShadowEvaluation).order_by(
        ShadowEvaluation.result_observed_at.desc(),
        ShadowEvaluation.result_version.desc(),
    ).limit(MAX_EVALUATIONS_SCANNED + 1).all()
    truncated = len(rows) > MAX_EVALUATIONS_SCANNED
    seen, verified = set(), []
    for ev in rows[:MAX_EVALUATIONS_SCANNED]:
        if ev.prediction_id in seen or not _verified(ev.prediction):
            continue
        seen.add(ev.prediction_id)
        verified.append(ev)
    return verified, truncated


@router.get("/summary")
def performance_summary(db: Session = Depends(get_db)):
    verified, truncated = _latest_verified_evaluations(db)
    return {**summarize_evaluations(verified), "truncated": truncated}


@router.get("/")
def list_evaluations(
    limit: int = Query(default=30, ge=1, le=100),
    db: Session = Depends(get_db),
):
    evaluations, truncated = _latest_verified_evaluations(db)
    items = []
    for ev in evaluations[:limit]:
        match = ev.prediction.match
        items.append({
            "evaluation_id": str(ev.id),
            "fixture_id": str(ev.fixture_id),
            "prediction_id": str(ev.prediction_id),
            "home_team": match.home_team.canonical_name,
            "away_team": match.away_team.canonical_name,
            "competition": match.season.competition.name,
            "kickoff_utc": match.kickoff_utc,
            "result_version": ev.result_version,
            "result_observed_at": ev.result_observed_at,
            "actual_score": {
                "home": ev.actual_home_goals, "away": ev.actual_away_goals,
            },
            "actual_1x2": ev.actual_1x2,
            "log_loss_1x2": float(ev.log_loss_1x2),
            "brier_1x2": float(ev.brier_1x2),
            "btts_log_loss": float(ev.btts_log_loss),
            "over_under_2_5_log_loss": float(ev.ou25_log_loss),
            "actual_score_probability": float(ev.actual_score_probability),
        })
    return {"items": items, "count": len(items), "truncated": truncated}
