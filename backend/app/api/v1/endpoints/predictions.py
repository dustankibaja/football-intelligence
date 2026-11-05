"""Read-only customer predictions from verified, immutable SQL snapshots."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.v1.security import require_customer_api_token
from app.db.models.shadow import ShadowPrediction
from app.db.session import get_db
from app.prediction.frozen_inference import (
    EXPECTED_C1_FINGERPRINT, EXPECTED_M1_FINGERPRINT,
)

router = APIRouter(dependencies=[Depends(require_customer_api_token)])


def _verified(p: ShadowPrediction) -> bool:
    if (
        p.feature_snapshot_id is None
        or p.prediction_run_id is None
        or p.score_matrix is None
        or p.c1_artifact_fingerprint != EXPECTED_C1_FINGERPRINT
        or p.m1_artifact_fingerprint != EXPECTED_M1_FINGERPRINT
        or p.generated_at >= p.kickoff_utc
        or p.feature_snapshot is None
        or p.prediction_run is None
        or p.prediction_run.status not in ("SUCCESS", "PARTIAL_SUCCESS")
        or p.prediction_run.completed_at is None
    ):
        return False
    metadata = p.feature_snapshot.data_quality_metadata or {}
    if (
        metadata.get("fingerprint_sha256") != p.feature_snapshot_fingerprint
        or metadata.get("history_fingerprint") != p.history_fingerprint
        or not metadata.get("source_provider")
        or not metadata.get("source_fixture_id")
    ):
        return False
    fingerprint = hashlib.sha256(
        json.dumps(p.score_matrix, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return fingerprint == p.exact_score_matrix_fingerprint


def _serialize(p: ShadowPrediction) -> dict:
    m = p.match
    return {
        "prediction_id": str(p.id),
        "fixture_id": str(p.fixture_id),
        "kickoff_utc": p.kickoff_utc,
        "generated_at": p.generated_at,
        "home_team": m.home_team.canonical_name,
        "away_team": m.away_team.canonical_name,
        "competition": m.season.competition.name,
        "competition_code": m.season.competition.code,
        "season": m.season.season_year,
        "one_x_two": {
            "home": float(p.prob_home),
            "draw": float(p.prob_draw),
            "away": float(p.prob_away),
        },
        "expected_goals": {
            "home": float(p.lambda_home),
            "away": float(p.lambda_away),
        },
        "btts": {"yes": float(p.btts_yes), "no": float(p.btts_no)},
        "over_under_2_5": {
            "over": float(p.over_2_5), "under": float(p.under_2_5),
        },
        "top_scorelines": p.top_scorelines[:5],
        "quality_state": p.quality_state,
        "quality_reasons": p.quality_reasons,
        "fallback_used": p.fallback_used,
        "model_disagreement": float(p.model_disagreement_value),
        "model_provenance": {
            "c1_fingerprint": p.c1_artifact_fingerprint,
            "m1_fingerprint": p.m1_artifact_fingerprint,
            "feature_snapshot_fingerprint": p.feature_snapshot_fingerprint,
        },
    }


@router.get("/")
def list_predictions(
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    # Over-fetch to avoid returning unverified legacy records in the response.
    rows = db.query(ShadowPrediction).filter(
        ShadowPrediction.score_matrix.isnot(None),
        ShadowPrediction.feature_snapshot_id.isnot(None),
        ShadowPrediction.prediction_run_id.isnot(None),
        ShadowPrediction.c1_artifact_fingerprint == EXPECTED_C1_FINGERPRINT,
        ShadowPrediction.m1_artifact_fingerprint == EXPECTED_M1_FINGERPRINT,
        ShadowPrediction.generated_at < ShadowPrediction.kickoff_utc,
        ShadowPrediction.kickoff_utc > datetime.now(UTC),
    ).order_by(ShadowPrediction.kickoff_utc.asc()).limit(1000).all()
    # A single earliest verified forecast per upcoming fixture.
    # Historical forecasts belong in evaluations, not upcoming matches.
    verified, seen = [], set()
    for p in rows:
        if p.fixture_id in seen or not _verified(p):
            continue
        seen.add(p.fixture_id)
        verified.append(_serialize(p))
        if len(verified) >= limit:
            break
    return {"items": verified, "count": len(verified)}


@router.get("/{fixture_id}")
def get_fixture_prediction(
    fixture_id: UUID, db: Session = Depends(get_db),
):
    rows = db.query(ShadowPrediction).filter_by(
        fixture_id=fixture_id
    ).order_by(ShadowPrediction.generated_at.desc()).limit(100).all()
    for p in rows:
        if _verified(p):
            return _serialize(p)
    raise HTTPException(status_code=404, detail="No verified prediction for fixture.")
