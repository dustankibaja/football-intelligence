"""Private, paginated read-only canonical match API."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.api.v1.research_access import internal_research_read, page
from app.db.models import Match, Season
from app.db.session import get_db

router = APIRouter(dependencies=[Depends(internal_research_read)])


@router.get("/")
def list_matches(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
    status: str | None = Query(None, max_length=20),
    db: Session = Depends(get_db),
):
    query = select(Match).options(
        joinedload(Match.home_team), joinedload(Match.away_team),
        joinedload(Match.season).joinedload(Season.competition),
    )
    if status is not None:
        query = query.where(Match.status == status)
    rows = db.scalars(query.order_by(Match.kickoff_utc.desc(), Match.id).offset(offset).limit(limit)).all()
    return page([{
        "id": str(row.id),
        "competition": row.season.competition.code,
        "season": row.season.season_year,
        "home_team": row.home_team.canonical_name,
        "away_team": row.away_team.canonical_name,
        "kickoff_utc": row.kickoff_utc,
        "status": row.status,
        "home_score": row.home_score,
        "away_score": row.away_score,
    } for row in rows], limit, offset)
