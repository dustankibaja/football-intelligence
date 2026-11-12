"""Read-only versioned post-match evaluations of canonical shadow predictions."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.research_access import internal_research_read, page
from app.db.models import ShadowEvaluation, ShadowPrediction
from app.db.session import get_db

router = APIRouter(dependencies=[Depends(internal_research_read)])


@router.get("/")
def list_evaluations(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
    db: Session = Depends(get_db),
):
    query = select(ShadowEvaluation).join(
        ShadowPrediction, ShadowEvaluation.prediction_id == ShadowPrediction.id
    ).where(
        ShadowPrediction.is_canonical_evaluation_prediction.is_(True),
        ShadowPrediction.prediction_mode == "SHADOW",
        ShadowPrediction.generated_at < ShadowPrediction.kickoff_utc,
        ShadowEvaluation.result_observed_at > ShadowPrediction.kickoff_utc,
    )
    rows = db.scalars(query.order_by(ShadowEvaluation.result_observed_at.desc(), ShadowEvaluation.id).offset(offset).limit(limit)).all()
    return page([{
        "id": str(row.id), "prediction_id": str(row.prediction_id), "fixture_id": str(row.fixture_id),
        "result_version": row.result_version, "result_observed_at": row.result_observed_at,
        "actual_score": [row.actual_home_goals, row.actual_away_goals],
        "actual_1x2": row.actual_1x2,
        "log_loss_1x2": float(row.log_loss_1x2), "brier_1x2": float(row.brier_1x2),
        "btts_brier": float(row.btts_brier), "ou25_brier": float(row.ou25_brier),
        "publication_eligible": False,
    } for row in rows], limit, offset)
