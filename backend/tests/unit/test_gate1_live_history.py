"""Real-provider history ingestion is canonical, final-only and append-safe."""
import json
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.automation.clock import FakeClock
from app.db.models import Base
from app.db.models.core import Competition, Match, Team
from app.providers.live.client import LiveFootballDataClient
from app.providers.live.history import LiveHistoryIngestor


def test_provider_finished_history_parsing_is_real_and_odds_free():
    now = datetime(2026, 11, 5, 12, tzinfo=UTC)
    payload = {
        "matches": [{
            "id": 12345,
            "status": "FINISHED",
            "utcDate": (now - timedelta(hours=6)).isoformat(),
            "competition": {"id": 2021},
            "homeTeam": {"id": 61, "name": "Chelsea FC"},
            "awayTeam": {"id": 66, "name": "Manchester United FC"},
            "season": {"startDate": "2026-08-01", "endDate": "2027-05-31"},
            "score": {"fullTime": {"home": 2, "away": 1}},
            "odds": {"homeWin": 1.5},
        }]
    }
    client = LiveFootballDataClient(
        api_key="unit-test",
        clock=FakeClock(now),
        transport=lambda endpoint: (200, {}, json.dumps(payload)),
    )
    rows = client.get_recent_finished_matches(days_back=1)
    assert len(rows) == 1
    row = rows[0]
    assert row["is_mapped"] is True
    assert row["home_score"] == 2
    assert row["away_score"] == 1
    assert row["competition_code"] == "E0"
    assert row["fixture_id"] == uuid5(NAMESPACE_URL, "football_data_org:12345")


def test_history_ingestion_is_idempotent_and_rejects_corrections():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        comp = Competition(code="E0", name="Premier League", country="England")
        home = Team(canonical_name="Verified Chelsea")
        away = Team(canonical_name="Verified Manchester United")
        db.add_all([comp, home, away])
        db.commit()
        home_id, away_id = home.id, away.id

    now = datetime(2026, 11, 5, 12, tzinfo=UTC)
    record = {
        "provider_name": "football_data_org",
        "provider_fixture_id": "12345",
        "fixture_id": uuid5(NAMESPACE_URL, "football_data_org:12345"),
        "competition_code": "E0",
        "season_year": "2026",
        "season_start_date": "2026-08-01",
        "season_end_date": "2027-05-31",
        "home_team_id": home_id,
        "away_team_id": away_id,
        "kickoff_utc": now - timedelta(days=5),
        "home_score": 2,
        "away_score": 1,
        "status": "FINISHED",
        "payload_fingerprint": "a" * 64,
        "retrieved_at_utc": now,
        "is_mapped": True,
    }
    ingestor = LiveHistoryIngestor(factory)
    assert ingestor.ingest([record]) == {"created": 1, "reused": 0, "rejected": 0}
    assert ingestor.ingest([record]) == {"created": 0, "reused": 1, "rejected": 0}
    changed = dict(record, home_score=5)
    assert ingestor.ingest([changed]) == {"created": 0, "reused": 0, "rejected": 1}
    with factory() as db:
        match = db.query(Match).one()
        assert (match.home_score, match.away_score) == (2, 1)
        assert match.completed_at is None
    engine.dispose()
