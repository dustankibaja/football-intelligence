"""Live shadow must never predict from synthetic provider feature placeholders."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.automation.clock import FakeClock
from app.automation.domain import ExecutionMode
from app.automation.lease import LeaseManager
from app.automation.orchestrator import OperationalOrchestrator
from app.providers.live.adapter import LiveIngestionAdapter
from app.providers.live.base import LiveFixtureDTO, LiveFixtureStatus


def _mapped_fixture(now):
    return LiveFixtureDTO(
        provider_fixture_id="safety_gate_fixture_001",
        provider_name="football_data_org",
        provider_competition_id="2021",
        competition_code="E0",
        season_year="2026",
        provider_home_team_id="64",
        provider_home_team_name="Home",
        provider_away_team_id="65",
        provider_away_team_name="Away",
        canonical_home_team_id=uuid4(),
        canonical_away_team_id=uuid4(),
        kickoff_utc=now + timedelta(hours=8),
        status=LiveFixtureStatus.TIMED,
        retrieved_at_utc=now,
        payload_fingerprint="a" * 64,
    )


def test_live_adapter_never_emits_fake_scientific_features():
    now = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
    record = LiveIngestionAdapter(clock=FakeClock(now)).adapt_live_fixture_to_orchestrator_record(
        _mapped_fixture(now)
    )

    assert record["is_mapped"] is True
    assert record["snapshot_quality_state"] == "UNMATERIALIZED"
    assert record["snapshot_block_reason"] == "LIVE_FEATURE_SNAPSHOT_NOT_MATERIALIZED"
    for forbidden in (
        "feature_values", "feature_keys", "feature_snapshot_id",
        "feature_snapshot_fingerprint", "history_fingerprint",
    ):
        assert forbidden not in record


def test_live_orchestrator_blocks_unmaterialized_provider_fixture():
    now = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
    clock = FakeClock(now)
    record = LiveIngestionAdapter(clock=clock).adapt_live_fixture_to_orchestrator_record(
        _mapped_fixture(now)
    )
    orch = OperationalOrchestrator(
        clock=clock,
        execution_mode=ExecutionMode.LIVE_SHADOW,
        lease_manager=LeaseManager(clock=clock),
        live_api_key="safe-test-credential",
    )
    summary = orch.run_once(fixtures_due=[record], dry_run=True)

    assert summary.snapshots_failed == 1
    assert summary.predictions_blocked == 1
    assert summary.predictions_created == 0
    assert any(
        item["failure_code"] == "LIVE_FEATURE_SNAPSHOT_UNAVAILABLE"
        for item in orch._failure_log
    )


def test_live_orchestrator_rejects_short_feature_vector_even_if_marked_valid():
    now = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
    clock = FakeClock(now)
    record = {
        "fixture_id": uuid4(),
        "competition_code": "E0",
        "season_year": "2026",
        "home_team_id": uuid4(),
        "away_team_id": uuid4(),
        "kickoff_utc": now + timedelta(hours=8),
        "feature_snapshot_id": uuid4(),
        "feature_snapshot_fingerprint": "a" * 64,
        "history_fingerprint": "b" * 64,
        "feature_values": [0.3, 0.2],
        "feature_keys": ["f0", "f1"],
        "snapshot_quality_state": "VALID",
        "needs_snapshot": False,
    }
    orch = OperationalOrchestrator(
        clock=clock,
        execution_mode=ExecutionMode.LIVE_SHADOW,
        lease_manager=LeaseManager(clock=clock),
        live_api_key="safe-test-credential",
    )
    summary = orch.run_once(fixtures_due=[record], dry_run=True)

    assert summary.predictions_blocked == 1
    assert summary.predictions_created == 0
    assert any(
        item["failure_code"] == "LIVE_FEATURE_SNAPSHOT_UNAVAILABLE"
        for item in orch._failure_log
    )
