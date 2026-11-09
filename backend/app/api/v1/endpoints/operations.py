"""Private, evidence-based operational status for the unattended trial."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.v1.security import require_customer_api_token
from app.api.v1.endpoints.predictions import _verified
from app.db.models.automation import OperationalRun
from app.db.models.shadow import ShadowEvaluation, ShadowPrediction
from app.db.session import get_db

router = APIRouter(dependencies=[Depends(require_customer_api_token)])


def _aware(value):
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@router.get("/status")
def private_operational_status(db: Session = Depends(get_db)):
    now = datetime.now(UTC)
    last = db.query(OperationalRun).filter_by(
        execution_mode="LIVE_SHADOW"
    ).order_by(OperationalRun.started_at.desc()).first()
    predictions = db.query(ShadowPrediction).filter(
        ShadowPrediction.score_matrix.isnot(None),
        ShadowPrediction.feature_snapshot_id.isnot(None),
    ).order_by(ShadowPrediction.generated_at.desc()).limit(500).all()
    last_pred = next((p for p in predictions if _verified(p)), None)
    evaluations = db.query(ShadowEvaluation).order_by(
        ShadowEvaluation.result_observed_at.desc()
    ).limit(500).all()
    last_eval = next((ev for ev in evaluations if _verified(ev.prediction)), None)
    completed_at = _aware(last.completed_at) if last else None
    # A recent cycle is evidence of recent execution, not proof that a
    # scheduler will remain running indefinitely.
    return {
        "observed_at": now,
        "worker_activity": (
            "NO_RUNS" if completed_at is None
            else "RECENT" if completed_at >= now - timedelta(hours=8)
            else "STALE"
        ),
        "last_run_status": last.run_status if last else None,
        "last_run_at": completed_at,
        "last_failure_code": last.failure_code if last else None,
        "last_prediction_at": _aware(last_pred.generated_at) if last_pred else None,
        "last_evaluation_at": _aware(last_eval.result_observed_at) if last_eval else None,
    }
