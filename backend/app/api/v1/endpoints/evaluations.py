"""Immutable post-match evaluations for verified scientific predictions."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.v1.endpoints.predictions import _verified
from app.api.v1.security import require_customer_api_token
from app.db.models.shadow import ShadowEvaluation
from app.db.session import get_db

router = APIRouter(dependencies=[Depends(require_customer_api_token)])


@router.get("/")
def list_evaluations(
    limit: int = Query(default=30, ge=1, le=100),
    db: Session = Depends(get_db),
):
    evaluations = db.query(ShadowEvaluation).order_by(
        ShadowEvaluation.result_observed_at.desc()
    ).limit(500).all()
    items = []
    for ev in evaluations:
        if not _verified(ev.prediction):
            continue
        items.append({
            "evaluation_id": str(ev.id),
            "fixture_id": str(ev.fixture_id),
            "prediction_id": str(ev.prediction_id),
            "result_version": ev.result_version,
            "result_observed_at": ev.result_observed_at,
            "actual_score": {
                "home": ev.actual_home_goals, "away": ev.actual_away_goals,
            },
            "actual_1x2": ev.actual_1x2,
            "log_loss_1x2": float(ev.log_loss_1x2),
            "brier_1x2": float(ev.brier_1x2),
            "actual_score_probability": float(ev.actual_score_probability),
        })
        if len(items) >= limit:
            break
    return {"items": items, "count": len(items)}
