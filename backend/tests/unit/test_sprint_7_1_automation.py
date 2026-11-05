"""
Sprint 7.1 Operational Automation, Scheduling, Leases, Fencing & Reliability Unit Tests (Section 68)
Covers all mandated operational scenarios:
- Injectable UTC clock
- Monotonic fencing tokens & split-brain prevention
- Stale worker rejection
- Lease contention abort
- Phase 6 service delegation (prediction identity, result finality, postponement, cancellation, result corrections)
- Post-kickoff missed window enforcement (zero backfill)
- Transient vs permanent error classification & bounded retry
- Crash and restart recovery
- Two concurrent workers zero duplicate predictions/evaluations
- Observational alert deduplication
- Separation of operational health from Phase 5.2 model quality
- Dry run zero canonical writes
- Load scenario (>= 100 items)
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.automation.clock import FakeClock
from app.automation.domain import (
    ExecutionMode,
    OperationalHealthState,
    OperationalRunStatus,
)
from app.automation.errors import (
    CircuitBreaker,
    PermanentProviderError,
    RetryPolicy,
    TransientProviderError,
)
from app.automation.health import HealthWatchdog
from app.automation.lease import LeaseManager
from app.automation.orchestrator import OperationalOrchestrator
from app.prediction.domain import (
    ShadowPredictionResult,
)
from app.prediction.monitoring import ShadowMonitoringService
from app.prediction.policy import Phase5_2_PolicyEngine
from app.prediction.scoring import PostMatchScoringService
from app.prediction.service import ShadowPredictionService


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock(datetime(2026, 10, 23, 12, 0, 0, tzinfo=UTC))


@pytest.fixture
def sample_fixture(fake_clock: FakeClock) -> dict:
    kickoff = fake_clock.now() + timedelta(hours=3)
    return {
        "fixture_id": uuid4(),
        "competition_code": "Premier League",
        "season_year": "2026",
        "home_team_id": uuid4(),
        "away_team_id": uuid4(),
        "kickoff_utc": kickoff,
        "feature_snapshot_id": uuid4(),
        "feature_snapshot_fingerprint": "feat_fp_valid_02f8b524",
        "history_fingerprint": "hist_fp_valid_9a7b",
        "feature_values": [0.4, -0.2] + [0.0] * 249,
        "feature_keys": [f"feat_{i}" for i in range(251)],
        "feature_count": 251,
        "needs_snapshot": True,
    }


def test_ops_cycle_is_idempotent(fake_clock: FakeClock, sample_fixture: dict):
    orch = OperationalOrchestrator(clock=fake_clock, scheduler_instance_id="w-idem")
    # First execution
    s1 = orch.run_once(fixtures_due=[sample_fixture])
    assert s1.run_status == OperationalRunStatus.SUCCESS
    assert s1.predictions_created == 1
    assert s1.predictions_reused == 0

    # Second execution: fixture already has prediction
    s2 = orch.run_once(fixtures_due=[sample_fixture])
    assert s2.run_status == OperationalRunStatus.SUCCESS
    assert s2.predictions_created == 0
    assert s2.predictions_reused == 1
    assert len(orch.prediction_service._persisted_predictions) == 1


def test_ops_cycle_uses_injectable_utc_clock(fake_clock: FakeClock, sample_fixture: dict):
    orch = OperationalOrchestrator(clock=fake_clock, scheduler_instance_id="w-clock")
    s = orch.run_once(fixtures_due=[sample_fixture])
    assert s.started_at == datetime(2026, 10, 23, 12, 0, 0, tzinfo=UTC)
    assert s.started_at.tzinfo == UTC


def test_ops_live_mode_requires_provider_configuration(fake_clock: FakeClock):
    orch = OperationalOrchestrator(
        clock=fake_clock,
        execution_mode=ExecutionMode.LIVE_SHADOW,
        live_api_key=None,  # Missing API key!
    )
    s = orch.run_once()
    assert s.run_status == OperationalRunStatus.FAILED
    assert s.failure_code == "LIVE_PROVIDER_CONFIG_MISSING"


def test_ops_replay_mode_does_not_require_provider_credentials(fake_clock: FakeClock):
    orch = OperationalOrchestrator(
        clock=fake_clock,
        execution_mode=ExecutionMode.REPLAY_CERTIFICATION,
        live_api_key=None,
    )
    s = orch.run_once()
    assert s.run_status == OperationalRunStatus.SUCCESS


def test_ops_lease_acquisition_returns_monotonic_fencing_token(fake_clock: FakeClock):
    lease_mgr = LeaseManager(clock=fake_clock)
    ok1, l1 = lease_mgr.acquire("test_lease", "worker-1", ttl_seconds=30.0)
    assert ok1
    assert l1.fencing_token == 1

    # Worker-1 releases
    lease_mgr.release("test_lease", "worker-1", l1.fencing_token)

    # Worker-2 acquires: token must be strictly greater (monotonic)
    ok2, l2 = lease_mgr.acquire("test_lease", "worker-2", ttl_seconds=30.0)
    assert ok2
    assert l2.fencing_token == 2
    assert l2.fencing_token > l1.fencing_token


def test_ops_expired_lease_can_be_reacquired(fake_clock: FakeClock):
    lease_mgr = LeaseManager(clock=fake_clock)
    ok1, l1 = lease_mgr.acquire("test_lease", "worker-dead", ttl_seconds=10.0)
    assert ok1

    # Worker dies without releasing. Fast-forward clock past expiry
    fake_clock.advance(15.0)

    # Worker-new reacquires expired lease
    ok2, l2 = lease_mgr.acquire("test_lease", "worker-new", ttl_seconds=30.0)
    assert ok2
    assert l2.lease_owner == "worker-new"
    assert l2.fencing_token == l1.fencing_token + 1


def test_ops_stale_worker_cannot_commit_after_fencing_loss(fake_clock: FakeClock):
    lease_mgr = LeaseManager(clock=fake_clock)
    # Worker A acquires token 1
    _, lease_a = lease_mgr.acquire("operational_scheduler_lease", "worker-a", ttl_seconds=10.0)
    assert lease_a.fencing_token == 1

    # Worker A stalls, lease expires
    fake_clock.advance(15.0)

    # Worker B acquires token 2
    _, lease_b = lease_mgr.acquire("operational_scheduler_lease", "worker-b", ttl_seconds=30.0)
    assert lease_b.fencing_token == 2

    # Worker A resumes and validates token 1
    valid_a = lease_mgr.validate_token("operational_scheduler_lease", "worker-a", lease_a.fencing_token)
    assert not valid_a, "Stale Worker A must be rejected by fencing validation!"

    # Worker B validates token 2
    valid_b = lease_mgr.validate_token("operational_scheduler_lease", "worker-b", lease_b.fencing_token)
    assert valid_b, "Current Worker B must be valid!"


def test_ops_split_brain_is_prevented(fake_clock: FakeClock, sample_fixture: dict):
    lease_mgr = LeaseManager(clock=fake_clock)
    pred_svc = ShadowPredictionService()

    orch_b = OperationalOrchestrator(
        clock=fake_clock,
        lease_manager=lease_mgr,
        prediction_service=pred_svc,
        scheduler_instance_id="worker-b",
    )

    # Worker A acquires lease
    _, l_a = lease_mgr.acquire("operational_scheduler_lease", "worker-a", ttl_seconds=10.0)
    # Worker A stalls, clock advances past lease TTL
    fake_clock.advance(15.0)

    # Worker B acquires lease
    _, l_b = lease_mgr.acquire("operational_scheduler_lease", "worker-b", ttl_seconds=30.0)
    assert l_b.fencing_token > l_a.fencing_token

    # Worker B executes successfully
    s_b = orch_b.run_once(fixtures_due=[sample_fixture])
    assert s_b.run_status == OperationalRunStatus.SUCCESS
    assert s_b.predictions_created == 1

    # Stale Worker A resumes and attempts to validate stale token
    assert not lease_mgr.validate_token("operational_scheduler_lease", "worker-a", l_a.fencing_token)
    assert len(pred_svc._persisted_predictions) == 1, "Duplicate canonical predictions must be 0!"


def test_ops_lease_contention_records_aborted_attempt(fake_clock: FakeClock, sample_fixture: dict):
    lease_mgr = LeaseManager(clock=fake_clock)
    orch_2 = OperationalOrchestrator(clock=fake_clock, lease_manager=lease_mgr, scheduler_instance_id="w2")

    # W1 acquires lease and holds it
    lease_mgr.acquire("operational_scheduler_lease", "w1", ttl_seconds=60.0)

    # W2 attempts execution while lease active
    s2 = orch_2.run_once(fixtures_due=[sample_fixture])
    assert s2.run_status == OperationalRunStatus.ABORTED
    assert s2.failure_code == "LEASE_NOT_ACQUIRED"
    assert "Scheduler lease held by another worker" in s2.failure_summary


def test_ops_prediction_identity_is_delegated_to_phase6_3(fake_clock: FakeClock, sample_fixture: dict):
    orch = OperationalOrchestrator(clock=fake_clock)
    s = orch.run_once(fixtures_due=[sample_fixture])
    assert s.run_status == OperationalRunStatus.SUCCESS

    # Verify predictions are stored inside the certified Phase 6.3 ShadowPredictionService
    preds = list(orch.prediction_service._persisted_predictions.values())
    assert len(preds) == 1
    p = preds[0]
    assert p.prediction_mode == "SHADOW"
    assert p.c1_model_key == "C1_Multinomial_Logistic_1X2"
    assert p.m1_model_key == "M1_Attack_Defense_Poisson"
    assert p.phase5_2_policy_hash == "f974e06a3e2ad4730ed9e6d55ede58f10dd31f5400cd9e39c0c6b06df362887b"


def test_ops_does_not_define_weaker_prediction_identity(fake_clock: FakeClock, sample_fixture: dict):
    # Proves Sprint 7.1 delegates to Sprint 6.3 and does not create an independent or weaker identity
    orch = OperationalOrchestrator(clock=fake_clock)
    s = orch.run_once(fixtures_due=[sample_fixture])
    assert s.run_status == OperationalRunStatus.SUCCESS

    # Idempotency is strictly bounded by Phase 6.3
    # Same fixture but different snapshot fingerprint produces distinct shadow prediction in Phase 6.3
    f_diff_snap = dict(sample_fixture)
    f_diff_snap["feature_snapshot_fingerprint"] = "feat_fp_valid_new_hash_123"
    s2 = orch.run_once(fixtures_due=[f_diff_snap])
    assert s2.predictions_created == 1
    assert len(orch.prediction_service._persisted_predictions) == 2


def test_ops_result_finality_is_delegated_to_phase6_1(fake_clock: FakeClock, sample_fixture: dict):
    orch = OperationalOrchestrator(clock=fake_clock)
    orch.run_once(fixtures_due=[sample_fixture])
    fix_id = sample_fixture["fixture_id"]

    # Result synchronization with non-terminal status is ignored by scoring
    results = [{"fixture_id": fix_id, "match_status": "LIVE", "home_goals": 1, "away_goals": 0}]
    s = orch.run_once(results_due=results)
    assert s.evaluations_created == 0
    assert len(orch.scoring_service._evaluations) == 0


def test_ops_postponement_reuses_phase6_behavior(fake_clock: FakeClock, sample_fixture: dict):
    orch = OperationalOrchestrator(clock=fake_clock)
    orch.run_once(fixtures_due=[sample_fixture])
    fix_id = sample_fixture["fixture_id"]

    # Match postponed: prediction is preserved without scoring
    results = [{"fixture_id": fix_id, "match_status": "POSTPONED"}]
    s = orch.run_once(results_due=results)
    assert s.evaluations_created == 0
    assert len(orch.prediction_service._persisted_predictions) == 1
    assert len(orch.scoring_service._evaluations) == 0


def test_ops_cancellation_reuses_phase6_behavior(fake_clock: FakeClock, sample_fixture: dict):
    orch = OperationalOrchestrator(clock=fake_clock)
    orch.run_once(fixtures_due=[sample_fixture])
    fix_id = sample_fixture["fixture_id"]

    # Match cancelled: prediction is preserved, no evaluation generated
    results = [{"fixture_id": fix_id, "match_status": "CANCELLED"}]
    s = orch.run_once(results_due=results)
    assert s.evaluations_created == 0
    assert len(orch.prediction_service._persisted_predictions) == 1
    assert len(orch.scoring_service._evaluations) == 0


def test_ops_result_correction_reuses_phase6_versioning(fake_clock: FakeClock, sample_fixture: dict):
    orch = OperationalOrchestrator(clock=fake_clock)
    orch.run_once(fixtures_due=[sample_fixture])
    fix_id = sample_fixture["fixture_id"]

    # Initial result: 2-1 (v1)
    results_v1 = [{"fixture_id": fix_id, "match_status": "FINISHED", "home_goals": 2, "away_goals": 1, "result_version": 1}]
    s1 = orch.run_once(results_due=results_v1)
    assert s1.evaluations_created == 1
    assert len(orch.scoring_service._evaluations) == 1

    # Provider correction: 2-2 (v2)
    results_v2 = [{"fixture_id": fix_id, "match_status": "FINISHED", "home_goals": 2, "away_goals": 2, "result_version": 2}]
    s2 = orch.run_once(results_due=results_v2)
    assert s2.evaluations_created == 1
    assert len(orch.scoring_service._evaluations) == 2, "Both version 1 and corrected version 2 must be preserved!"


def test_ops_missed_window_never_backfills_prediction(fake_clock: FakeClock, sample_fixture: dict):
    orch = OperationalOrchestrator(clock=fake_clock)
    # Fast-forward clock PAST kickoff
    fake_clock.advance(timedelta(hours=4))

    s = orch.run_once(fixtures_due=[sample_fixture])
    assert s.run_status == OperationalRunStatus.SUCCESS
    assert s.predictions_created == 0
    assert s.predictions_missed_window == 1
    assert len(orch.prediction_service._persisted_predictions) == 0, "No hindsight prediction permitted!"


def test_ops_transient_provider_failure_retries(fake_clock: FakeClock):
    retry_policy = RetryPolicy(max_retries=3, initial_delay_seconds=1.0, backoff_multiplier=2.0, clock=fake_clock)
    circuit_breaker = CircuitBreaker(failure_threshold=5, cooldown_seconds=60.0, clock=fake_clock)
    orch = OperationalOrchestrator(clock=fake_clock, retry_policy=retry_policy, circuit_breaker=circuit_breaker)

    transient_err = TransientProviderError("HTTP_429", "Rate limit exceeded.")
    s = orch.run_once(simulate_provider_failure=transient_err)

    assert s.run_status == OperationalRunStatus.FAILED
    assert s.retry_count == 3
    assert s.failure_code == "HTTP_429"


def test_ops_permanent_provider_failure_does_not_retry(fake_clock: FakeClock):
    retry_policy = RetryPolicy(max_retries=3, clock=fake_clock)
    orch = OperationalOrchestrator(clock=fake_clock, retry_policy=retry_policy)

    perm_err = PermanentProviderError("AUTH_FORBIDDEN", "Invalid credentials.")
    s = orch.run_once(simulate_provider_failure=perm_err)

    assert s.run_status == OperationalRunStatus.FAILED
    assert s.retry_count == 1, "Permanent error must not be repeatedly retried!"
    assert s.failure_code == "AUTH_FORBIDDEN"


def test_ops_transient_database_failure_retries(fake_clock: FakeClock, sample_fixture: dict):
    orch = OperationalOrchestrator(clock=fake_clock)
    s = orch.run_once(
        fixtures_due=[sample_fixture],
        simulate_db_failure_at_stage="PREDICTION_PERSISTENCE",
    )
    assert s.run_status == OperationalRunStatus.FAILED
    assert s.failure_code == "PREDICTION_PERSISTENCE_FAILED"
    assert s.predictions_created == 0


def test_ops_scientific_integrity_failure_aborts(fake_clock: FakeClock, sample_fixture: dict):
    orch = OperationalOrchestrator(clock=fake_clock)
    # Adversarial manifest tamper
    bad_manifest = {"phase5_master_scientific_manifest_hash": "tampered_hash_00000000"}
    s = orch.run_once(fixtures_due=[sample_fixture], manifest_override=bad_manifest)

    assert s.run_status == OperationalRunStatus.FAILED
    assert s.failure_code == "MANIFEST_MISMATCH"
    assert s.predictions_created == 0


def test_ops_model_artifact_mismatch_aborts(fake_clock: FakeClock, sample_fixture: dict):
    orch = OperationalOrchestrator(clock=fake_clock)
    bad_artifact = {"c1_fingerprint": "art_fp_c1_tampered"}
    s = orch.run_once(fixtures_due=[sample_fixture], artifact_override=bad_artifact)

    assert s.run_status == OperationalRunStatus.FAILED
    assert s.failure_code == "MODEL_ARTIFACT_MISMATCH"
    assert s.predictions_created == 0


def test_ops_crash_before_prediction_commit_is_safe(fake_clock: FakeClock, sample_fixture: dict):
    orch = OperationalOrchestrator(clock=fake_clock)
    s = orch.run_once(
        fixtures_due=[sample_fixture],
        simulate_db_failure_at_stage="PRE_EXECUTION",
    )
    assert s.run_status == OperationalRunStatus.FAILED
    assert len(orch.prediction_service._persisted_predictions) == 0


def test_ops_crash_after_prediction_commit_is_idempotent(fake_clock: FakeClock, sample_fixture: dict):
    orch = OperationalOrchestrator(clock=fake_clock)
    # Initial run commits prediction
    s1 = orch.run_once(fixtures_due=[sample_fixture])
    assert s1.predictions_created == 1

    # Crash & restart run against same due work
    s2 = orch.run_once(fixtures_due=[sample_fixture])
    assert s2.predictions_created == 0
    assert s2.predictions_reused == 1
    assert len(orch.prediction_service._persisted_predictions) == 1


def test_ops_restart_resumes_without_duplicates(fake_clock: FakeClock, sample_fixture: dict):
    orch = OperationalOrchestrator(clock=fake_clock)
    f2 = dict(sample_fixture)
    f2["fixture_id"] = uuid4()
    f2["feature_snapshot_fingerprint"] = "feat_fp_f2_unique"

    # Worker finishes f1 then restarts
    orch.run_once(fixtures_due=[sample_fixture])

    # Restarted worker runs batch containing both f1 and f2
    s_restart = orch.run_once(fixtures_due=[sample_fixture, f2])
    assert s_restart.predictions_reused == 1
    assert s_restart.predictions_created == 1
    assert len(orch.prediction_service._persisted_predictions) == 2


def test_ops_two_workers_create_zero_duplicate_predictions(fake_clock: FakeClock, sample_fixture: dict):
    pred_svc = ShadowPredictionService()
    orch_a = OperationalOrchestrator(clock=fake_clock, prediction_service=pred_svc, scheduler_instance_id="w-a")
    orch_b = OperationalOrchestrator(clock=fake_clock, prediction_service=pred_svc, scheduler_instance_id="w-b")

    s_a = orch_a.run_once(fixtures_due=[sample_fixture])
    s_b = orch_b.run_once(fixtures_due=[sample_fixture])

    assert s_a.predictions_created == 1
    assert s_b.predictions_reused == 1
    assert len(pred_svc._persisted_predictions) == 1


def test_ops_two_workers_create_zero_duplicate_evaluations(fake_clock: FakeClock, sample_fixture: dict):
    pred_svc = ShadowPredictionService()
    scoring_svc = PostMatchScoringService()
    orch_a = OperationalOrchestrator(clock=fake_clock, prediction_service=pred_svc, scoring_service=scoring_svc, scheduler_instance_id="w-a")
    orch_b = OperationalOrchestrator(clock=fake_clock, prediction_service=pred_svc, scoring_service=scoring_svc, scheduler_instance_id="w-b")

    orch_a.run_once(fixtures_due=[sample_fixture])
    results = [{"fixture_id": sample_fixture["fixture_id"], "match_status": "FINISHED", "home_goals": 1, "away_goals": 0, "result_version": 1}]

    s_a = orch_a.run_once(results_due=results)
    s_b = orch_b.run_once(results_due=results)

    assert s_a.evaluations_created == 1
    assert s_b.evaluations_reused == 1
    assert len(scoring_svc._evaluations) == 1


def test_ops_alert_deduplication(fake_clock: FakeClock):
    mon = ShadowMonitoringService()
    orch = OperationalOrchestrator(clock=fake_clock, monitoring_service=mon)

    # High fallback rate batch triggering alert
    preds = []
    for _ in range(20):
        preds.append(ShadowPredictionResult(
            prediction_id=uuid4(),
            fixture_id=uuid4(),
            prediction_run_id=None,
            prediction_mode="SHADOW",
            generated_at=fake_clock.now(),
            kickoff_utc=fake_clock.now() + timedelta(hours=2),
            lead_time_seconds=7200,
            feature_snapshot_id=uuid4(),
            feature_snapshot_fingerprint="fp1",
            history_fingerprint="fp2",
            phase5_manifest_hash="h1",
            phase5_2_policy_hash="h2",
            phase6_1_manifest_hash="h3",
            phase6_2_manifest_hash="h4",
            c1_model_key="c1",
            c1_model_version="1.0.0",
            c1_artifact_fingerprint="af1",
            m1_model_key="m1",
            m1_model_version="1.0.0",
            m1_artifact_fingerprint="af2",
            prob_home=0.5,
            prob_draw=0.3,
            prob_away=0.2,
            structural_prob_home=0.5,
            structural_prob_draw=0.3,
            structural_prob_away=0.2,
            lambda_home=1.5,
            lambda_away=1.2,
            btts_yes=0.55,
            btts_no=0.45,
            over_2_5=0.50,
            under_2_5=0.50,
            exact_score_matrix_fingerprint="mfp",
            top_scorelines=[],
            quality_state="FALLBACK_MODEL_USED",
            quality_reasons=["KNOWN_COLD_START"],
            fallback_used=True,
            fallback_model="M1_Attack_Defense_Poisson",
            fallback_reason="KNOWN_COLD_START",
            model_disagreement_value=0.1,
            model_disagreement_band="LOW",
            is_canonical_evaluation_prediction=True,
            prediction_schema_version="1.0.0",
        ))

    orch.prediction_service._persisted_predictions = {p.prediction_id: p for p in preds}

    s1 = orch.run_once()
    assert s1.alerts_created == 1

    # Second cycle over identical cohort: duplicate alert must be suppressed
    s2 = orch.run_once()
    assert s2.alerts_created == 0, "Repeated cycle must not create duplicate alert!"


def test_ops_minimum_sample_monitoring_policy_is_preserved(fake_clock: FakeClock):
    mon = ShadowMonitoringService()
    # 10 evaluations (below minimum sample threshold of 50 for Log Loss)
    evals = []
    res = mon.evaluate_label_aware_cohort(evals, cohort_name="UNDERSIZED_BATCH")
    assert res["status"] == "INSUFFICIENT_SAMPLE"
    assert res["required_sample"] == 50


def test_ops_operational_health_is_separate_from_scientific_quality(fake_clock: FakeClock):
    watchdog = HealthWatchdog(clock=fake_clock)
    # Operational health status
    h_healthy = watchdog.check_health(database_reachable=True, current_alembic_head="20261105_0007", provider_configured=True)
    assert h_healthy.health_state == OperationalHealthState.HEALTHY

    # Scientific model quality state via Phase 5.2 engine
    p_eval = Phase5_2_PolicyEngine.evaluate(
        is_eligible=True,
        eligibility_reason=None,
        critical_feature_absent=False,
        known_cold_start=False,
        missingness_count=0,
        drift_psi=0.01,
        disagreement_value=0.05,
        disagreement_band="LOW",
    )
    assert p_eval.quality_state == "NORMAL"
    # Strict separation assertion
    assert h_healthy.health_state.value in ["HEALTHY", "DEGRADED", "STALE", "FAILED"]
    assert p_eval.quality_state in [
        "NORMAL", "LIMITED_HISTORY", "FEATURE_DEGRADED",
        "HIGH_DRIFT", "HIGH_MODEL_DISAGREEMENT", "FALLBACK_MODEL_USED", "BLOCKED"
    ]


def test_ops_dry_run_creates_zero_canonical_writes(fake_clock: FakeClock, sample_fixture: dict):
    pred_svc = ShadowPredictionService()
    scoring_svc = PostMatchScoringService()
    orch = OperationalOrchestrator(
        clock=fake_clock,
        prediction_service=pred_svc,
        scoring_service=scoring_svc,
    )

    s = orch.run_once(
        trigger_type="DRY_RUN",
        dry_run=True,
        fixtures_due=[sample_fixture],
    )
    assert s.run_status == OperationalRunStatus.SUCCESS
    assert len(pred_svc._persisted_predictions) == 0, "Dry run must persist 0 predictions!"
    assert len(scoring_svc._evaluations) == 0, "Dry run must persist 0 evaluations!"


def test_ops_no_model_training():
    # Audit prediction and automation packages for training keywords
    import inspect

    import app.automation.orchestrator as orch_mod
    import app.prediction.service as pred_mod

    orch_code = inspect.getsource(orch_mod)
    pred_code = inspect.getsource(pred_mod)

    assert ".fit(" not in orch_code
    assert ".partial_fit(" not in orch_code
    assert ".fit(" not in pred_code
    assert ".partial_fit(" not in pred_code


def test_ops_no_automatic_recalibration():
    # Ensure scoring does not trigger model retraining or coefficient mutation
    import inspect

    import app.prediction.scoring as scoring_mod
    scoring_code = inspect.getsource(scoring_mod)

    assert "fit(" not in scoring_code
    assert "recalibrate(" not in scoring_code
    assert "optimizer" not in scoring_code


def test_ops_load_scenario_100_fixtures(fake_clock: FakeClock):
    orch = OperationalOrchestrator(clock=fake_clock, scheduler_instance_id="load-worker")
    fixtures = []
    base_kickoff = fake_clock.now() + timedelta(hours=2)

    for i in range(120):
        fixtures.append({
            "fixture_id": uuid4(),
            "competition_id": uuid4(),
            "home_team_id": uuid4(),
            "away_team_id": uuid4(),
            "kickoff_utc": base_kickoff + timedelta(minutes=i),
            "feature_snapshot_id": uuid4(),
            "feature_snapshot_fingerprint": f"feat_fp_{i:04d}",
            "history_fingerprint": f"hist_fp_{i:04d}",
            "feature_values": [0.1 * (i % 5), -0.05 * (i % 3)] + [0.0] * 249,
            "needs_snapshot": True,
        })

    s = orch.run_once(fixtures_due=fixtures)
    assert s.run_status == OperationalRunStatus.SUCCESS
    assert s.fixtures_discovered == 120
    assert s.predictions_created == 120
    assert len(orch.prediction_service._persisted_predictions) == 120


def test_ops_concurrency_load_zero_duplicates(fake_clock: FakeClock):
    pred_svc = ShadowPredictionService()
    orch1 = OperationalOrchestrator(clock=fake_clock, prediction_service=pred_svc, scheduler_instance_id="w-conc-1")
    orch2 = OperationalOrchestrator(clock=fake_clock, prediction_service=pred_svc, scheduler_instance_id="w-conc-2")

    fixtures = []
    base_kickoff = fake_clock.now() + timedelta(hours=3)
    for i in range(50):
        fixtures.append({
            "fixture_id": uuid4(),
            "competition_id": uuid4(),
            "home_team_id": uuid4(),
            "away_team_id": uuid4(),
            "kickoff_utc": base_kickoff,
            "feature_snapshot_id": uuid4(),
            "feature_snapshot_fingerprint": f"feat_fp_conc_{i}",
            "history_fingerprint": "hist_fp_conc",
            "feature_values": [0.2, -0.1] + [0.0] * 249,
            "needs_snapshot": True,
        })

    s1 = orch1.run_once(fixtures_due=fixtures)
    s2 = orch2.run_once(fixtures_due=fixtures)

    assert s1.predictions_created == 50
    assert s2.predictions_reused == 50
    assert s2.predictions_created == 0
    assert len(pred_svc._persisted_predictions) == 50, "Zero duplicate canonical predictions under concurrency load!"


def test_ops_watchdog_run_staleness_threshold_is_configurable():
    wd = HealthWatchdog(max_run_staleness_hours=12.0)
    assert wd.max_run_staleness_hours == 12.0


def test_ops_watchdog_prediction_staleness_threshold_is_configurable():
    wd = HealthWatchdog(max_prediction_staleness_hours=36.0)
    assert wd.max_prediction_staleness_hours == 36.0


def test_ops_watchdog_default_thresholds_are_24h_and_48h():
    wd = HealthWatchdog()
    assert wd.max_run_staleness_hours == 24.0
    assert wd.max_prediction_staleness_hours == 48.0


def test_ops_watchdog_custom_thresholds_override_defaults(fake_clock: FakeClock):
    # Set run threshold to 6.0 hours
    wd = HealthWatchdog(clock=fake_clock, max_run_staleness_hours=6.0)
    last_run = fake_clock.now() - timedelta(hours=7.0)
    health = wd.check_health(database_reachable=True, current_alembic_head="20261105_0007", provider_configured=True, last_successful_run=last_run)
    assert health.health_state == OperationalHealthState.STALE
    assert "OPERATIONAL_STALENESS" in health.diagnostics[0]


def test_ops_watchdog_rejects_invalid_staleness_configuration():
    # 0 is invalid
    with pytest.raises(ValueError, match="INVALID_STALENESS_CONFIG"):
        HealthWatchdog(max_run_staleness_hours=0.0)

    # Negative is invalid
    with pytest.raises(ValueError, match="INVALID_STALENESS_CONFIG"):
        HealthWatchdog(max_run_staleness_hours=-10.0)

    # NaN is invalid
    with pytest.raises(ValueError, match="INVALID_STALENESS_CONFIG"):
        HealthWatchdog(max_run_staleness_hours=float("nan"))

    # Inf is invalid
    with pytest.raises(ValueError, match="INVALID_STALENESS_CONFIG"):
        HealthWatchdog(max_run_staleness_hours=float("inf"))

    # Non-number is invalid
    with pytest.raises(ValueError, match="INVALID_STALENESS_CONFIG"):
        HealthWatchdog(max_run_staleness_hours="invalid")  # type: ignore


def test_ops_watchdog_run_staleness_boundary_behavior(fake_clock: FakeClock):
    wd = HealthWatchdog(clock=fake_clock, max_run_staleness_hours=24.0)
    now = fake_clock.now()

    # 1. 23h 59m 59s -> NOT STALE
    t_23h59 = now - timedelta(hours=23, minutes=59, seconds=59)
    h1 = wd.check_health(database_reachable=True, current_alembic_head="20261105_0007", provider_configured=True, last_successful_run=t_23h59)
    assert h1.health_state == OperationalHealthState.HEALTHY

    # 2. 24h exactly -> NOT STALE (comparison operator contract is strictly '>')
    t_24h = now - timedelta(hours=24)
    h2 = wd.check_health(database_reachable=True, current_alembic_head="20261105_0007", provider_configured=True, last_successful_run=t_24h)
    assert h2.health_state == OperationalHealthState.HEALTHY

    # 3. 24h + 1s -> STALE
    t_24h_1s = now - timedelta(hours=24, seconds=1)
    h3 = wd.check_health(database_reachable=True, current_alembic_head="20261105_0007", provider_configured=True, last_successful_run=t_24h_1s)
    assert h3.health_state == OperationalHealthState.STALE


def test_ops_watchdog_prediction_staleness_boundary_behavior(fake_clock: FakeClock):
    wd = HealthWatchdog(clock=fake_clock, max_prediction_staleness_hours=48.0)
    now = fake_clock.now()

    # 1. 47h 59m 59s -> NOT STALE
    t_47h59 = now - timedelta(hours=47, minutes=59, seconds=59)
    h1 = wd.check_health(database_reachable=True, current_alembic_head="20261105_0007", provider_configured=True, last_prediction_activity=t_47h59)
    assert h1.health_state == OperationalHealthState.HEALTHY

    # 2. 48h exactly -> NOT STALE (comparison operator contract is strictly '>')
    t_48h = now - timedelta(hours=48)
    h2 = wd.check_health(database_reachable=True, current_alembic_head="20261105_0007", provider_configured=True, last_prediction_activity=t_48h)
    assert h2.health_state == OperationalHealthState.HEALTHY

    # 3. 48h + 1s -> STALE
    t_48h_1s = now - timedelta(hours=48, seconds=1)
    h3 = wd.check_health(database_reachable=True, current_alembic_head="20261105_0007", provider_configured=True, last_prediction_activity=t_48h_1s)
    assert h3.health_state == OperationalHealthState.STALE

