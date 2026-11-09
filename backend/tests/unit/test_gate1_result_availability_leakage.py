"""A final score learned after cutoff cannot enter a historical PIT feature."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base
from app.db.models.core import Competition, Match, Season, Team
from app.features.query import PointInTimeQueryService


def test_result_finalized_after_cutoff_is_not_visible_to_features():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        comp = Competition(code="PIT_TEST", name="PIT Test", country="Test")
        home = Team(canonical_name="PIT Home")
        away = Team(canonical_name="PIT Away")
        session.add_all([comp, home, away])
        session.flush()
        season = Season(competition_id=comp.id, season_year="2026/27")
        session.add(season)
        session.flush()
        now = datetime(2026, 11, 5, 12, tzinfo=UTC)
        match = Match(
            season_id=season.id, home_team_id=home.id, away_team_id=away.id,
            kickoff_utc=now - timedelta(days=2), status="FINISHED",
            home_score=1, away_score=0,
            completed_at=now + timedelta(hours=1),
        )
        session.add(match)
        session.flush()
        pit = PointInTimeQueryService(
            session, approved_only=False, enforce_date_only_precision=False
        )
        assert pit.get_team_matches_before(home.id, now) == []
        assert pit.get_league_matches_before(comp.id, now) == []
        match.completed_at = now - timedelta(days=1)
        session.flush()
        assert [m.id for m in pit.get_team_matches_before(home.id, now)] == [match.id]
        assert [m.id for m in pit.get_league_matches_before(comp.id, now)] == [match.id]
    engine.dispose()
