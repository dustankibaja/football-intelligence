"""Gate 1 canonical provider identity and stale-history fail-closed tests."""
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base
from app.db.models.core import Competition, Match, Season, Team
from app.db.models.features import FeatureSnapshot
from app.features.live import (
    LiveFeatureMaterializationError,
    LiveFeatureMaterializer,
)


@pytest.fixture
def isolated_sqlite_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def _record(factory):
    with factory() as session:
        competition = Competition(code="E0", name="Premier League", country="England")
        home = Team(canonical_name="Gate One Home")
        away = Team(canonical_name="Gate One Away")
        session.add_all([competition, home, away])
        session.flush()
        season = Season(
            competition_id=competition.id, season_year="2026/27", start_year=2026
        )
        session.add(season)
        session.commit()
        home_id, away_id = home.id, away.id

    now = datetime(2026, 11, 5, 12, tzinfo=UTC)
    provider, provider_id = "football_data_org", "12345"
    return now, {
        "fixture_id": uuid5(NAMESPACE_URL, f"{provider}:{provider_id}"),
        "provider_name": provider,
        "provider_fixture_id": provider_id,
        "payload_fingerprint": "a" * 64,
        "competition_code": "E0",
        "season_year": "2026",
        "home_team_id": home_id,
        "away_team_id": away_id,
        "kickoff_utc": now + timedelta(hours=8),
        "is_mapped": True,
    }


def test_no_recent_history_cannot_be_imputed_as_real(isolated_sqlite_factory):
    now, record = _record(isolated_sqlite_factory)
    materializer = LiveFeatureMaterializer(isolated_sqlite_factory)
    with pytest.raises(
        LiveFeatureMaterializationError, match="CURRENT_TEAM_HISTORY_MISSING_OR_STALE"
    ):
        materializer.materialize(record, now)
    with isolated_sqlite_factory() as session:
        assert session.query(FeatureSnapshot).count() == 0


def test_forged_provider_fixture_id_is_rejected(isolated_sqlite_factory):
    now, record = _record(isolated_sqlite_factory)
    record["fixture_id"] = uuid4()
    with pytest.raises(
        LiveFeatureMaterializationError, match="PROVIDER_FIXTURE_IDENTITY_MISMATCH"
    ):
        LiveFeatureMaterializer(isolated_sqlite_factory).materialize(record, now)


def test_post_kickoff_materialization_is_rejected(isolated_sqlite_factory):
    now, record = _record(isolated_sqlite_factory)
    with pytest.raises(
        LiveFeatureMaterializationError, match="POST_KICKOFF_FEATURE_MATERIALIZATION"
    ):
        LiveFeatureMaterializer(isolated_sqlite_factory).materialize(
            record, record["kickoff_utc"] + timedelta(seconds=1)
        )


def test_real_recent_history_produces_persisted_251_feature_snapshot(isolated_sqlite_factory):
    now, record = _record(isolated_sqlite_factory)
    with isolated_sqlite_factory() as session:
        season = session.query(Season).filter_by(season_year="2026/27").one()
        past_kickoff = now - timedelta(days=10)
        session.add(Match(
            season_id=season.id,
            home_team_id=record["home_team_id"],
            away_team_id=record["away_team_id"],
            kickoff_utc=past_kickoff,
            completed_at=past_kickoff + timedelta(hours=2),
            status="FINISHED",
            home_score=2,
            away_score=1,
        ))
        session.commit()

    output = LiveFeatureMaterializer(isolated_sqlite_factory).materialize(
        record, now
    )
    assert output["feature_snapshot_id"] is not None
    assert len(output["feature_keys"]) == 251
    assert len(output["feature_values"]) == 251
    assert len(output["feature_snapshot_fingerprint"]) == 64 or len(output["feature_snapshot_fingerprint"]) == 16
    assert len(output["history_fingerprint"]) == 64
    assert output["snapshot_quality_state"] == "VALID"
    assert output["drift_status"] == "NOT_MEASURED"
    with isolated_sqlite_factory() as session:
        snap = session.query(FeatureSnapshot).one()
        assert snap.id == output["feature_snapshot_id"]
        assert snap.data_quality_metadata["source_fixture_id"] == "12345"
