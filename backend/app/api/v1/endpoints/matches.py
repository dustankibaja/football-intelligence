"""Verified upcoming fixtures only; never list synthetic provider placeholders."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.v1.endpoints.predictions import _verified
from app.api.v1.security import require_customer_api_token
from app.db.models.shadow import ShadowPrediction
from app.db.session import get_db

router = APIRouter(dependencies=[Depends(require_customer_api_token)])


@router.get("/")
def list_matches(
    limit: int = Query(default=30, ge=1, le=100),
    db: Session = Depends(get_db),
):
    now = datetime.now(UTC)
    predictions = db.query(ShadowPrediction).filter(
        ShadowPrediction.kickoff_utc > now,
        ShadowPrediction.score_matrix.isnot(None),
    ).order_by(ShadowPrediction.kickoff_utc.asc()).limit(500).all()
    items, seen = [], set()
    for p in predictions:
        if p.fixture_id in seen or not _verified(p):
            continue
        seen.add(p.fixture_id)
        m = p.match
        items.append({
            "fixture_id": str(m.id),
            "home_team": m.home_team.canonical_name,
            "away_team": m.away_team.canonical_name,
            "competition": m.season.competition.name,
            "kickoff_utc": m.kickoff_utc,
            "prediction_available": True,
            "quality_state": p.quality_state,
        })
        if len(items) >= limit:
            break
    return {"items": items, "count": len(items)}
