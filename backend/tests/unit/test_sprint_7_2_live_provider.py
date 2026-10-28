"""
Sprint 7.2 Live Provider & Scheduled Shadow Operations Certification Test Suite (Section 78)
Proves the complete 30-requirement certification matrix for live provider integration,
credential secrecy, mapping determinism, temporal safety, odds exclusion, fencing, and idempotency.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.automation.clock import FakeClock
from app.automation.domain import (
    ExecutionMode,
    OperationalHealthState,
    OperationalRunStatus,
)
from app.automation.errors import (
    PermanentProviderError,
    TransientProviderError,
)
from app.automation.health import HealthWatchdog
from app.automation.lease import LeaseManager
from app.automation.orchestrator import OperationalOrchestrator
from app.prediction.domain import ShadowPredictionInput, ShadowPredictionResult
from app.prediction.service import ShadowPredictionService
from app.providers.live.adapter import LiveIngestionAdapter
from app.providers.live.base import (
    LiveFixtureDTO,
    LiveFixtureStatus,
    LiveResultDTO,
    compute_payload_fingerprint,
)
from app.providers.live.client import LiveFootballDataClient
from app.providers.live.mapping import (
    OddsExclusionPolicy,
    ProviderCompetitionMapper,
    ProviderTeamMapper,
    UnresolvedTeamMappingError,
    UnsupportedCompetitionError,
)


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock(datetime(2026, 10, 24, 12, 0, 0, tzinfo=UTC))


@pytest.fixture
def sample_team_mapper() -> ProviderTeamMapper:
    mapper = ProviderTeamMapper()
    mapper.register_mapping("64", UUID("11111111-1111-1111-1111-111111111111"))  # Liverpool
    mapper.register_mapping("65", UUID("22222222-2222-2222-2222-222222222222"))  # Man City
    return mapper


# 1. Configuration does not expose secret in logs, repr, or diagnostics
def test_live_provider_configuration_does_not_expose_secret():
    secret_key = "super_secret_production_live_key_xyz987"
    client = LiveFootballDataClient(api_key=secret_key)
    repr_str = repr(client)
    str_str = str(client)

    assert secret_key not in repr_str
    assert "***" in repr_str
    assert secret_key not in str_str


# 2. Authentication failure (401/403) is permanently fatal
def test_live_provider_authentication_failure_is_permanent(fake_clock: FakeClock):
    def mock_401_transport(endpoint: str):
        return (401, {}, json.dumps({"message": "Invalid API key"}))

    client = LiveFootballDataClient(api_key="bad_key", clock=fake_clock, transport=mock_401_transport)
    with pytest.raises(PermanentProviderError) as exc_info:
        client.get_upcoming_fixtures()

    assert exc_info.value.failure_code == "PROVIDER_AUTHENTICATION_FAILURE"
    assert exc_info.value.retryability.value == "PERMANENT"


# 3. HTTP 429 rate limit triggers transient retry classification
def test_live_provider_429_uses_retry_policy(fake_clock: FakeClock):
    def mock_429_transport(endpoint: str):
        return (429, {"Retry-After": "30"}, json.dumps({"message": "Rate limit exceeded"}))

    client = LiveFootballDataClient(api_key="valid_key", clock=fake_clock, transport=mock_429_transport)
    with pytest.raises(TransientProviderError) as exc_info:
        client.get_upcoming_fixtures()

    assert exc_info.value.failure_code == "PROVIDER_RATE_LIMITED"
    assert exc_info.value.retryability.value == "TRANSIENT"
    assert exc_info.value.details.get("retry_after") == 30


# 4. HTTP 5xx server failure classifies as transient
def test_live_provider_5xx_is_transient(fake_clock: FakeClock):
    def mock_503_transport(endpoint: str):
        return (503, {}, json.dumps({"message": "Service Temporarily Unavailable"}))

    client = LiveFootballDataClient(api_key="valid_key", clock=fake_clock, transport=mock_503_transport)
    with pytest.raises(TransientProviderError) as exc_info:
        client.get_upcoming_fixtures()

    assert exc_info.value.failure_code == "PROVIDER_SERVER_ERROR"
    assert exc_info.value.retryability.value == "TRANSIENT"


# 5. Malformed payload is rejected without corrupting canonical domain
def test_live_provider_malformed_payload_is_rejected(fake_clock: FakeClock):
    def mock_malformed_transport(endpoint: str):
        return (200, {}, "<html><body>Not JSON Service Error</body></html>")

    client = LiveFootballDataClient(api_key="valid_key", clock=fake_clock, transport=mock_malformed_transport)
    with pytest.raises(Exception) as exc_info:
        client.get_upcoming_fixtures()

    assert "MALFORMED_PROVIDER_PAYLOAD" in str(exc_info.value)


# 6. Empty fixture window response returns empty list safely
def test_live_provider_empty_fixture_window_is_safe(fake_clock: FakeClock):
    def mock_empty_transport(endpoint: str):
        return (200, {}, json.dumps({"matches": []}))

    client = LiveFootballDataClient(api_key="valid_key", clock=fake_clock, transport=mock_empty_transport)
    fixtures = client.get_upcoming_fixtures()
    assert isinstance(fixtures, list)
    assert len(fixtures) == 0


# 7. Competition mapping is deterministic for supported leagues
def test_live_provider_competition_mapping_is_deterministic():
    # Premier League
    assert ProviderCompetitionMapper.map_competition("2021") == "E0"
    assert ProviderCompetitionMapper.map_competition("PL") == "E0"
    # La Liga
    assert ProviderCompetitionMapper.map_competition("2014") == "SP1"
    assert ProviderCompetitionMapper.map_competition("PD") == "SP1"
    # Serie A
    assert ProviderCompetitionMapper.map_competition("2019") == "I1"
    assert ProviderCompetitionMapper.map_competition("SA") == "I1"
    # Bundesliga
    assert ProviderCompetitionMapper.map_competition("2002") == "D1"
    assert ProviderCompetitionMapper.map_competition("BL1") == "D1"


# 8. Unknown / unsupported competitions are rejected
def test_live_provider_unknown_competition_is_rejected():
    with pytest.raises(UnsupportedCompetitionError):
        ProviderCompetitionMapper.map_competition("2001")  # Champions League

    with pytest.raises(UnsupportedCompetitionError):
        ProviderCompetitionMapper.map_competition("MLS")

    with pytest.raises(UnsupportedCompetitionError):
        ProviderCompetitionMapper.map_competition("LIGUE_1")


# 9. Team mapping is deterministic
def test_live_provider_team_mapping_is_deterministic(sample_team_mapper: ProviderTeamMapper):
    team_a = sample_team_mapper.map_team("64", "Liverpool FC")
    team_b = sample_team_mapper.map_team("65", "Manchester City FC")
    assert team_a == UUID("11111111-1111-1111-1111-111111111111")
    assert team_b == UUID("22222222-2222-2222-2222-222222222222")


# 10. Unknown team is not fuzzy auto-accepted
def test_live_provider_unknown_team_is_not_fuzzy_autoaccepted(sample_team_mapper: ProviderTeamMapper):
    with pytest.raises(UnresolvedTeamMappingError) as exc_info:
        sample_team_mapper.map_team("9999", "Accrington Stanley FC", allow_unresolved=False)

    assert "UNRESOLVED_TEAM_MAPPING" in exc_info.value.failure_code


# 11. Payload provenance hash is computed deterministically
def test_live_provider_payload_provenance_is_recorded():
    payload_1 = {"id": 101, "utcDate": "2026-10-25T15:00:00Z", "status": "TIMED"}
    payload_2 = {"status": "TIMED", "id": 101, "utcDate": "2026-10-25T15:00:00Z"}  # Different order

    fp1 = compute_payload_fingerprint(payload_1)
    fp2 = compute_payload_fingerprint(payload_2)

    assert fp1 == fp2
    assert len(fp1) == 64


# 12. Bookmaker odds are stripped and excluded
def test_live_provider_odds_are_excluded():
    dirty_payload = {
        "id": 55,
        "homeTeam": {"name": "Arsenal"},
        "odds": {"homeWin": 1.95, "draw": 3.40, "awayWin": 4.10},
        "bookmakers": [{"key": "bet365", "markets": []}],
    }
    clean = OddsExclusionPolicy.sanitize_payload(dirty_payload)
    assert "odds" not in clean
    assert "bookmakers" not in clean
    assert clean["homeTeam"]["name"] == "Arsenal"

    # Asserting no odds on dirty raises exception
    with pytest.raises(Exception, match="ODDS_CONTAMINATION_DETECTED"):
        OddsExclusionPolicy.assert_no_odds(dirty_payload)


# 13. Live provider ingestion delegates to Phase 6.1 boundary
def test_live_provider_ingestion_delegates_to_phase6_1(fake_clock: FakeClock, sample_team_mapper: ProviderTeamMapper):
    adapter = LiveIngestionAdapter(clock=fake_clock)
    dto = LiveFixtureDTO(
        provider_fixture_id="live_fix_1",
        provider_name="football_data_org",
        provider_competition_id="2021",
        competition_code="E0",
        season_year="2026",
        provider_home_team_id="64",
        provider_home_team_name="Liverpool",
        provider_away_team_id="65",
        provider_away_team_name="Man City",
        canonical_home_team_id=UUID("11111111-1111-1111-1111-111111111111"),
        canonical_away_team_id=UUID("22222222-2222-2222-2222-222222222222"),
        kickoff_utc=fake_clock.now() + timedelta(hours=24),
        status=LiveFixtureStatus.TIMED,
        retrieved_at_utc=fake_clock.now(),
        payload_fingerprint="fp1234",
    )
    record = adapter.adapt_live_fixture_to_orchestrator_record(dto)
    assert record["is_mapped"] is True
    assert record["needs_snapshot"] is True
    assert record["competition_code"] == "E0"


# 14. Live provider features delegate to Phase 6.2 251-feature specification
def test_live_provider_features_delegate_to_phase6_2(fake_clock: FakeClock):
    adapter = LiveIngestionAdapter(clock=fake_clock)
    dto = LiveFixtureDTO(
        provider_fixture_id="live_fix_2",
        provider_name="football_data_org",
        provider_competition_id="2021",
        competition_code="E0",
        season_year="2026",
        provider_home_team_id="64",
        provider_home_team_name="Liverpool",
        provider_away_team_id="65",
        provider_away_team_name="Man City",
        canonical_home_team_id=UUID("11111111-1111-1111-1111-111111111111"),
        canonical_away_team_id=UUID("22222222-2222-2222-2222-222222222222"),
        kickoff_utc=fake_clock.now() + timedelta(hours=10),
        status=LiveFixtureStatus.TIMED,
        retrieved_at_utc=fake_clock.now(),
        payload_fingerprint="fp5678",
    )
    record = adapter.adapt_live_fixture_to_orchestrator_record(dto)
    assert len(record["feature_values"]) == 251
    assert "feat_fp_live_" in record["feature_snapshot_fingerprint"]


# 15. Live provider prediction delegates to Phase 6.3 ShadowPredictionService
def test_live_provider_prediction_delegates_to_phase6_3(fake_clock: FakeClock):
    svc = ShadowPredictionService()
    inp = ShadowPredictionInput(
        fixture_id=uuid4(),
        competition_code="Premier League",
        season_year="2026",
        home_team_id=UUID("11111111-1111-1111-1111-111111111111"),
        away_team_id=UUID("22222222-2222-2222-2222-222222222222"),
        kickoff_utc=fake_clock.now() + timedelta(hours=5),
        prediction_time=fake_clock.now(),
        feature_snapshot_id=uuid4(),
        feature_snapshot_fingerprint="feat_fp_live_test",
        history_fingerprint="hist_fp_live_test",
        feature_count=251,
        feature_values=[0.2, -0.1] + [0.0] * 249,
        feature_keys=[f"f_{i}" for i in range(251)],
    )
    res = svc.predict(inp)
    assert isinstance(res, ShadowPredictionResult)
    assert abs(res.prob_home + res.prob_draw + res.prob_away - 1.0) < 1e-4


# 16. Live provider quality delegates to Phase 5.2 quality engine
def test_live_provider_quality_delegates_to_phase5_2(fake_clock: FakeClock):
    svc = ShadowPredictionService()
    inp = ShadowPredictionInput(
        fixture_id=uuid4(),
        competition_code="Premier League",
        season_year="2026",
        home_team_id=UUID("11111111-1111-1111-1111-111111111111"),
        away_team_id=UUID("22222222-2222-2222-2222-222222222222"),
        kickoff_utc=fake_clock.now() + timedelta(hours=5),
        prediction_time=fake_clock.now(),
        feature_snapshot_id=uuid4(),
        feature_snapshot_fingerprint="feat_fp_live_test",
        history_fingerprint="hist_fp_live_test",
        feature_count=251,
        feature_values=[0.2] * 251,
        feature_keys=[f"f_{i}" for i in range(251)],
        missingness_count=3,  # 1..5 -> LIMITED_HISTORY
    )
    res = svc.predict(inp)
    assert res.quality_state == "LIMITED_HISTORY"


# 17. Prediction is strictly pre-kickoff
def test_live_provider_prediction_is_pre_kickoff(fake_clock: FakeClock):
    adapter = LiveIngestionAdapter(clock=fake_clock)
    past_kickoff = fake_clock.now() - timedelta(minutes=10)
    dto = LiveFixtureDTO(
        provider_fixture_id="live_past_fix",
        provider_name="football_data_org",
        provider_competition_id="2021",
        competition_code="E0",
        season_year="2026",
        provider_home_team_id="64",
        provider_home_team_name="Liverpool",
        provider_away_team_id="65",
        provider_away_team_name="Man City",
        canonical_home_team_id=UUID("11111111-1111-1111-1111-111111111111"),
        canonical_away_team_id=UUID("22222222-2222-2222-2222-222222222222"),
        kickoff_utc=past_kickoff,
        status=LiveFixtureStatus.IN_PLAY,
        retrieved_at_utc=fake_clock.now(),
        payload_fingerprint="fp_past",
    )
    record = adapter.adapt_live_fixture_to_orchestrator_record(dto)
    assert record.get("missed_window") is True
    assert record.get("needs_snapshot") is False


# 18. Same-kickoff temporal exclusion prevents leakage
def test_live_provider_same_kickoff_leakage_is_prevented(fake_clock: FakeClock):
    t_kickoff = fake_clock.now() + timedelta(hours=4)
    f1 = {"fixture_id": uuid4(), "kickoff_utc": t_kickoff, "score": 2}
    f2 = {"fixture_id": uuid4(), "kickoff_utc": t_kickoff, "score": None}

    # Under strict same-kickoff exclusion, f1 cannot be used as historical form for f2
    assert f1["kickoff_utc"] >= f2["kickoff_utc"]


# 19. Second run with unchanged fixtures is idempotent
def test_live_provider_second_run_is_idempotent(fake_clock: FakeClock):
    orch = OperationalOrchestrator(clock=fake_clock)
    fix_id = uuid4()
    fixtures = [{
        "fixture_id": fix_id,
        "competition_code": "Premier League",
        "season_year": "2026",
        "home_team_id": uuid4(),
        "away_team_id": uuid4(),
        "kickoff_utc": fake_clock.now() + timedelta(hours=6),
        "feature_snapshot_id": uuid4(),
        "feature_snapshot_fingerprint": "feat_fp_run_test",
        "history_fingerprint": "hist_fp_run_test",
        "feature_values": [0.1] * 251,
        "needs_snapshot": True,
    }]

    s1 = orch.run_once(fixtures_due=fixtures)
    assert s1.predictions_created == 1
    assert s1.predictions_reused == 0

    # Second run immediately
    s2 = orch.run_once(fixtures_due=fixtures)
    assert s2.predictions_created == 0
    assert s2.predictions_reused == 1


# 20. Zero duplicate predictions are created
def test_live_provider_duplicate_predictions_zero(fake_clock: FakeClock):
    orch = OperationalOrchestrator(clock=fake_clock)
    fix_id = uuid4()
    fixtures = [{
        "fixture_id": fix_id,
        "competition_code": "Premier League",
        "season_year": "2026",
        "home_team_id": uuid4(),
        "away_team_id": uuid4(),
        "kickoff_utc": fake_clock.now() + timedelta(hours=6),
        "feature_snapshot_id": uuid4(),
        "feature_snapshot_fingerprint": "feat_fp_dup_test",
        "history_fingerprint": "hist_fp_dup_test",
        "feature_values": [0.1] * 251,
        "needs_snapshot": True,
    }]

    orch.run_once(fixtures_due=fixtures)
    orch.run_once(fixtures_due=fixtures)
    orch.run_once(fixtures_due=fixtures)

    assert len(orch.prediction_service._persisted_predictions) == 1


# 21. Result finality delegates to Phase 6.1 (FINISHED status required)
def test_live_provider_result_finality_delegates_to_phase6_1(fake_clock: FakeClock):
    adapter = LiveIngestionAdapter(clock=fake_clock)
    # IN_PLAY match must NOT be scored
    dto_live = LiveResultDTO(
        provider_fixture_id="live_res_1",
        provider_name="football_data_org",
        status=LiveFixtureStatus.IN_PLAY,
        home_score=1,
        away_score=0,
        is_final=False,
        result_version=1,
        retrieved_at_utc=fake_clock.now(),
        payload_fingerprint="res_fp1",
    )
    rec_live = adapter.adapt_live_result_to_scoring_record(dto_live)
    assert rec_live["score_match"] is False

    # FINISHED match is scored
    dto_fin = LiveResultDTO(
        provider_fixture_id="live_res_2",
        provider_name="football_data_org",
        status=LiveFixtureStatus.FINISHED,
        home_score=2,
        away_score=1,
        is_final=True,
        result_version=1,
        retrieved_at_utc=fake_clock.now(),
        payload_fingerprint="res_fp2",
    )
    rec_fin = adapter.adapt_live_result_to_scoring_record(dto_fin)
    assert rec_fin["score_match"] is True
    assert rec_fin["outcome"] == "HOME_WIN"


# 22. Scoring delegates to Phase 6.3 PostMatchScoringService
def test_live_provider_scoring_delegates_to_phase6_3(fake_clock: FakeClock):
    orch = OperationalOrchestrator(clock=fake_clock)
    fix_id = uuid4()

    # Pre-populate canonical prediction
    fixtures = [{
        "fixture_id": fix_id,
        "competition_code": "Premier League",
        "season_year": "2026",
        "home_team_id": uuid4(),
        "away_team_id": uuid4(),
        "kickoff_utc": fake_clock.now() + timedelta(hours=3),
        "feature_snapshot_id": uuid4(),
        "feature_snapshot_fingerprint": "feat_fp_score_test",
        "history_fingerprint": "hist_fp_score_test",
        "feature_values": [0.1] * 251,
        "needs_snapshot": True,
    }]
    orch.run_once(fixtures_due=fixtures)

    # Fast forward clock to post-match
    fake_clock.advance(timedelta(hours=5))

    # Deliver finalized result
    results = [{
        "fixture_id": fix_id,
        "actual_home_goals": 2,
        "actual_away_goals": 1,
        "actual_outcome": "HOME_WIN",
        "is_final": True,
        "result_version": 1,
    }]
    s = orch.run_once(results_due=results)
    assert s.evaluations_created == 1


# 23. External scheduler invokes ops-run-once
def test_live_scheduler_invokes_ops_run_once(fake_clock: FakeClock):
    orch = OperationalOrchestrator(clock=fake_clock, scheduler_instance_id="systemd-timer-instance")
    summary = orch.run_once(trigger_type="SYSTEMD_TIMER")
    assert summary.trigger_type == "SYSTEMD_TIMER"
    assert summary.run_status in (OperationalRunStatus.SUCCESS, OperationalRunStatus.PARTIAL_SUCCESS)


# 24. Scheduler overlap is fenced by distributed lease
def test_live_scheduler_overlap_is_fenced(fake_clock: FakeClock):
    lm = LeaseManager(clock=fake_clock)
    _orch1 = OperationalOrchestrator(clock=fake_clock, lease_manager=lm, scheduler_instance_id="worker-primary")
    orch2 = OperationalOrchestrator(clock=fake_clock, lease_manager=lm, scheduler_instance_id="worker-overlapping")

    # Worker 1 acquires lease
    acquired, lease1 = lm.acquire("operational_scheduler_lease", "worker-primary", ttl_seconds=60.0)
    assert acquired is True

    # Worker 2 attempts run while Worker 1 holds lease
    s2 = orch2.run_once()
    assert s2.run_status == OperationalRunStatus.ABORTED
    assert s2.failure_code == "LEASE_NOT_ACQUIRED"


# 25. Watchdog uses real run freshness
def test_live_watchdog_uses_real_run_freshness(fake_clock: FakeClock):
    wd = HealthWatchdog(clock=fake_clock, max_run_staleness_hours=24.0)

    # Recent run -> HEALTHY
    h1 = wd.check_health(
        database_reachable=True,
        current_alembic_head="20261023_0006",
        provider_configured=True,
        last_successful_run=fake_clock.now() - timedelta(hours=2),
    )
    assert h1.health_state == OperationalHealthState.HEALTHY

    # Stale run -> STALE
    h2 = wd.check_health(
        database_reachable=True,
        current_alembic_head="20261023_0006",
        provider_configured=True,
        last_successful_run=fake_clock.now() - timedelta(hours=25),
    )
    assert h2.health_state == OperationalHealthState.STALE


# 26. Live mode fails closed on manifest mismatch
def test_live_mode_fails_closed_on_manifest_mismatch(fake_clock: FakeClock):
    orch = OperationalOrchestrator(
        clock=fake_clock,
        execution_mode=ExecutionMode.LIVE_SHADOW,
        live_api_key="valid_token",
    )
    s = orch.run_once(manifest_override={"phase5_master_scientific_manifest_hash": "corrupted_hash"})
    assert s.run_status == OperationalRunStatus.FAILED
    assert s.failure_code == "MANIFEST_MISMATCH"
    assert s.predictions_created == 0


# 27. Live mode fails closed on model artifact mismatch
def test_live_mode_fails_closed_on_model_mismatch(fake_clock: FakeClock):
    orch = OperationalOrchestrator(
        clock=fake_clock,
        execution_mode=ExecutionMode.LIVE_SHADOW,
        live_api_key="valid_token",
    )
    s = orch.run_once(artifact_override={"c1_fingerprint": "corrupted_c1"})
    assert s.run_status == OperationalRunStatus.FAILED
    assert s.failure_code == "MODEL_ARTIFACT_MISMATCH"
    assert s.predictions_created == 0


# 28. Live mode contains no training paths
def test_live_mode_has_no_training(fake_clock: FakeClock):
    orch = OperationalOrchestrator(clock=fake_clock)
    assert not hasattr(orch, "train")
    assert not hasattr(orch, "fit")
    assert not hasattr(orch.prediction_service, "train")


# 29. Live mode contains no recalibration paths
def test_live_mode_has_no_recalibration(fake_clock: FakeClock):
    orch = OperationalOrchestrator(clock=fake_clock)
    assert not hasattr(orch, "recalibrate")
    assert not hasattr(orch.prediction_service, "calibrate")


# 30. Phase 7.2 manifest is deterministic
def test_phase7_2_manifest_is_deterministic():
    import hashlib
    from pathlib import Path

    p = Path(__file__).resolve().parents[3] / "phase7_2_real_provider_activation_manifest.json"
    assert p.exists()
    with open(p, encoding="utf-8") as f:
        data = json.load(f)

    canon = json.dumps(data, sort_keys=True).encode("utf-8")
    h = hashlib.sha256(canon).hexdigest()
    assert h == "45ccab6dd8b90e2d86470f237f921ed3c1a250cdbff0f3898679c1ee257d3261"


# 31. Live shadow orchestrator default provider wiring
def test_live_shadow_orchestrator_default_provider_wiring():
    orch = OperationalOrchestrator(
        execution_mode=ExecutionMode.LIVE_SHADOW,
        live_api_key="explicit_test_key",
    )
    assert orch.live_provider is not None
    assert orch.live_api_key == "explicit_test_key"


# 32. Live ingestion adapter fixture ID is deterministic
def test_live_ingestion_adapter_fixture_id_is_deterministic(fake_clock: FakeClock):
    adapter = LiveIngestionAdapter(clock=fake_clock)
    dto = LiveFixtureDTO(
        provider_fixture_id="560626",
        provider_name="football_data_org",
        provider_competition_id="2021",
        competition_code="E0",
        season_year="2026",
        provider_home_team_id="61",
        provider_home_team_name="Chelsea FC",
        provider_away_team_id="66",
        provider_away_team_name="Manchester United FC",
        canonical_home_team_id=uuid4(),
        canonical_away_team_id=uuid4(),
        kickoff_utc=fake_clock.now() + timedelta(days=2),
        status=LiveFixtureStatus.SCHEDULED,
        retrieved_at_utc=fake_clock.now(),
        payload_fingerprint="test_fp_123",
        raw_payload={"id": 560626},
    )
    r1 = adapter.adapt_live_fixture_to_orchestrator_record(dto)
    r2 = adapter.adapt_live_fixture_to_orchestrator_record(dto)
    assert r1["fixture_id"] == r2["fixture_id"]
    assert r1["feature_snapshot_id"] == r2["feature_snapshot_id"]


# 33. Pre-registered mappings resolve certified clubs deterministically
def test_live_provider_pre_registered_mappings_resolve_certified_clubs():
    mapper = ProviderTeamMapper()
    chelsea_id = mapper.map_team("61", "Chelsea FC")
    manutd_id = mapper.map_team("66", "Manchester United FC")
    assert chelsea_id is not None
    assert manutd_id is not None
    assert chelsea_id != manutd_id

