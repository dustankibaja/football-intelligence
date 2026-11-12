"""Private immutable canonical shadow prediction reads; never a live feed."""
from uuid import UUID
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.research_access import internal_research_read, page
from app.db.models import ShadowPrediction
from app.db.session import get_db

router = APIRouter(dependencies=[Depends(internal_research_read)])


@router.get("/")
def list_predictions(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
    fixture_id: UUID | None = None,
    db: Session = Depends(get_db),
):
    query = select(ShadowPrediction).where(
        ShadowPrediction.is_canonical_evaluation_prediction.is_(True),
        ShadowPrediction.prediction_mode == "SHADOW",
        ShadowPrediction.generated_at < ShadowPrediction.kickoff_utc,
    )
    if fixture_id is not None:
        query = query.where(ShadowPrediction.fixture_id == fixture_id)
    rows = db.scalars(query.order_by(ShadowPrediction.generated_at.desc(), ShadowPrediction.id).offset(offset).limit(limit)).all()
    return page([{
        "id": str(row.id), "fixture_id": str(row.fixture_id),
        "generated_at": row.generated_at, "kickoff_utc": row.kickoff_utc,
        "mode": row.prediction_mode, "canonical_evaluation": True,
        "probabilities_1x2": [float(row.prob_home), float(row.prob_draw), float(row.prob_away)],
        "structural_probabilities_1x2": [float(row.structural_prob_home), float(row.structural_prob_draw), float(row.structural_prob_away)],
        "lambda_home": float(row.lambda_home), "lambda_away": float(row.lambda_away),
        "over_2_5": float(row.over_2_5), "under_2_5": float(row.under_2_5),
        "btts_yes": float(row.btts_yes), "btts_no": float(row.btts_no),
        "quality_state": row.quality_state, "fallback_used": row.fallback_used,
        "model_disagreement_band": row.model_disagreement_band,
        "c1_model_key": row.c1_model_key, "c1_model_version": row.c1_model_version,
        "m1_model_key": row.m1_model_key, "m1_model_version": row.m1_model_version,
        "feature_fingerprint": row.feature_snapshot_fingerprint,
        "publication_eligible": False,
    } for row in rows], limit, offset)
