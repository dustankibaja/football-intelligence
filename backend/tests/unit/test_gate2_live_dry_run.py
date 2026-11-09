"""A live dry-run must not create any canonical or operational SQL rows."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.automation.domain import ExecutionMode
from app.automation.orchestrator import OperationalOrchestrator
from app.db.models import Base
from app.db.models.automation import OperationalRun, SchedulerLease
from app.db.models.core import Match
from app.db.models.features import FeatureSnapshot


class FakeLiveProvider:
    provider_name = "football_data_org"
    api_key = "unit-test"

    def __init__(self):
        self.history_calls = 0

    def get_upcoming_fixtures(self):
        return []

    def get_recent_finished_matches(self, **kwargs):
        self.history_calls += 1
        return []

    def get_fixture_results(self, fixture_ids):
        return []


def test_live_dry_run_does_not_write_database_or_backfill_history():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    provider = FakeLiveProvider()
    orchestrator = OperationalOrchestrator(
        execution_mode=ExecutionMode.LIVE_SHADOW,
        session_factory=factory,
        live_provider=provider,
        live_api_key="unit-test",
    )
    summary = orchestrator.run_once(dry_run=True)
    assert summary.snapshots_created == 0
    assert summary.predictions_created == 0
    assert provider.history_calls == 0
    with factory() as db:
        assert db.query(OperationalRun).count() == 0
        assert db.query(SchedulerLease).count() == 0
        assert db.query(Match).count() == 0
        assert db.query(FeatureSnapshot).count() == 0
    engine.dispose()
