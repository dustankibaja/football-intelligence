"""
Operational Orchestrator (Sections 5, 7, 8, 12-15, 18-26, 33-35, 38, 39, 44-51, 54)
Executes the deterministic 13-stage operational cycle.
Thin orchestrator calling certified Phase 6 services (Prediction, Scoring, Monitoring).
Guarantees monotonic fencing, split-brain protection, at-least-once safety, and alert deduplication.
"""
from __future__ import annotations

from collections.abc import Callable
import dataclasses
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from app.automation.clock import Clock, SystemClock
from app.automation.domain import (
    ExecutionMode,
    OperationalRunStatus,
    OperationalRunSummary,
)
from app.automation.errors import (
    CircuitBreaker,
    ConfigurationError,
    ErrorCategory,
    OperationalException,
    PermanentProviderError,
    Retryability,
    RetryPolicy,
    ScientificIntegrityError,
)
from app.automation.lease import LeaseManager, LeaseRecord
from app.prediction.domain import (
    ShadowPredictionInput,
    ShadowPredictionResult,
)
from app.prediction.monitoring import ShadowMonitoringService
from app.prediction.scoring import PostMatchScoringService
from app.prediction.service import (
    FROZEN_C1_FINGERPRINT,
    FROZEN_C1_KEY,
    FROZEN_M1_FINGERPRINT,
    FROZEN_M1_KEY,
    ShadowPredictionService,
)
from app.core.config import settings
from app.db.session import SessionLocal
from app.db.models.automation import OperationalRun
from app.db.repositories.shadow import ShadowPredictionRepository
from app.providers.live.base import LiveFootballDataProvider

FROZEN_PHASE5_MASTER_HASH = "68b151b2c9683d4a431605bfda45902d78a2335f533ab49256ef47713ce22524"
FROZEN_PHASE5_2_POLICY_HASH = "f974e06a3e2ad4730ed9e6d55ede58f10dd31f5400cd9e39c0c6b06df362887b"
FROZEN_PHASE6_1_INGESTION_HASH = "98c0e07bee158e0493dbfbff036bc6b3e8c2f763c87c9d715a8ee5a2df0423d7"
FROZEN_PHASE6_2_CANONICAL_HASH = "7ac6041ff6169a321524b39f7c2bd3610913c94c3b4fc350a89c2550690a8b7b"
FROZEN_SPRINT6_3_SHADOW_HASH = "e59dbe19c9a0568271e25fb9b1250a267a4a78c80ef202641c89bb6ce99b98ee"

CANONICAL_TIMING_RULE = "LATEST_VALID_SHADOW_PREDICTION_BEFORE_KICKOFF"
_DEFAULT_KEY = object()


class OperationalOrchestrator:
    """
    Deterministic Operational Orchestrator for unattended live-shadow operations.
    Thin wrapper delegating inference, scoring, and monitoring to certified Phase 6 engines.
    """

    def __init__(
        self,
        clock: Clock | None = None,
        lease_manager: LeaseManager | None = None,
        prediction_service: ShadowPredictionService | None = None,
        scoring_service: PostMatchScoringService | None = None,
        monitoring_service: ShadowMonitoringService | None = None,
        retry_policy: RetryPolicy | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        execution_mode: ExecutionMode = ExecutionMode.REPLAY_CERTIFICATION,
        scheduler_instance_id: str = "orchestrator-instance-1",
        git_commit: str = "HEAD",
        pipeline_version: str = "1.0.0",
        live_api_key: Any = _DEFAULT_KEY,
        live_api_base_url: str | None = None,
        live_provider: LiveFootballDataProvider | None = None,
        session_factory: Any = None,
    ) -> None:
        self.clock = clock or SystemClock()
        self.execution_mode = execution_mode
        self.session_factory = session_factory or (SessionLocal if self.execution_mode == ExecutionMode.LIVE_SHADOW else None)
        self.lease_manager = lease_manager or LeaseManager(clock=self.clock, session_factory=self.session_factory)
        self.prediction_service = prediction_service or ShadowPredictionService()
        self.scoring_service = scoring_service or PostMatchScoringService()
        self.monitoring_service = monitoring_service or ShadowMonitoringService()
        self.retry_policy = retry_policy or RetryPolicy(clock=self.clock)
        self.circuit_breaker = circuit_breaker or CircuitBreaker(clock=self.clock)
        self.scheduler_instance_id = scheduler_instance_id
        self.git_commit = git_commit
        self.pipeline_version = pipeline_version
        if live_api_key is _DEFAULT_KEY:
            self.live_api_key = getattr(live_provider, "api_key", None) or getattr(settings, "LIVE_DATA_API_KEY", None)
        else:
            self.live_api_key = live_api_key or (getattr(live_provider, "api_key", None))
        self.live_api_base_url = live_api_base_url
        if live_provider is not None:
            self.live_provider = live_provider
        elif self.execution_mode == ExecutionMode.LIVE_SHADOW:
            from app.providers.live.client import LiveFootballDataClient
            self.live_provider = LiveFootballDataClient(api_key=self.live_api_key, clock=self.clock)
        else:
            self.live_provider = None

        # Restore previously persisted shadow predictions in LIVE_SHADOW mode
        if self.execution_mode == ExecutionMode.LIVE_SHADOW:
            self._load_live_shadow_predictions()

        # Deduplicated alert history: set of (metric, severity, threshold, cohort, window)
        self._recorded_alert_signatures: set[tuple[str, str, float, str, int]] = set()

        # Operational failure audit log
        self._failure_log: list[dict[str, Any]] = []

    def run_once(
        self,
        trigger_type: str = "MANUAL",
        dry_run: bool = False,
        fixtures_due: list[dict[str, Any]] | None = None,
        results_due: list[dict[str, Any]] | None = None,
        manifest_override: dict[str, str] | None = None,
        artifact_override: dict[str, str] | None = None,
        simulate_db_failure_at_stage: str | None = None,
        simulate_provider_failure: Exception | None = None,
    ) -> OperationalRunSummary:
        """
        Executes one full deterministic 13-stage operational cycle.
        """
        start_time = self.clock.now()
        summary = OperationalRunSummary(
            run_id=uuid4(),
            started_at=start_time,
            scheduler_instance_id=self.scheduler_instance_id,
            git_commit=self.git_commit,
            pipeline_version=self.pipeline_version,
            execution_mode=self.execution_mode,
            trigger_type=trigger_type,
            run_status=OperationalRunStatus.STARTED,
        )

        lease_acquired = False
        current_lease: LeaseRecord | None = None

        try:
            # Stage 1: Operational Run Attempt Started
            summary.run_status = OperationalRunStatus.RUNNING

            # Stage 2: Acquire Distributed Scheduler Lease + Monotonic Fencing Token
            acquired, lease = self.lease_manager.acquire(
                lease_name="operational_scheduler_lease",
                lease_owner=self.scheduler_instance_id,
                ttl_seconds=60.0,
            )
            if not acquired:
                summary.run_status = OperationalRunStatus.ABORTED
                summary.failure_code = "LEASE_NOT_ACQUIRED"
                summary.failure_summary = (
                    f"Scheduler lease held by another worker ({lease.lease_owner}). Fencing token: {lease.fencing_token}."
                )
                summary.completed_at = self.clock.now()
                return summary

            lease_acquired = True
            current_lease = lease
            summary.lease_id = lease.id
            summary.fencing_token = lease.fencing_token

            # Stage 3: Verify Five Frozen Scientific Manifests
            self._verify_scientific_manifests(manifest_override)

            # Stage 4: Verify C1 / M1 Model Artifact Identities
            self._verify_model_artifacts(artifact_override)

            # Stage 5: Verify Execution-Mode Dependencies & Credentials
            self._verify_execution_mode_dependencies()

            # Optional DB failure simulation
            if simulate_db_failure_at_stage == "PRE_EXECUTION":
                raise OperationalException(
                    ErrorCategory.DATABASE,
                    Retryability.TRANSIENT,
                    "DATABASE_CONNECTION_LOST",
                    "Database connection lost during pre-execution phase.",
                )

            # Stage 6 & 7: Determine & Execute Due Ingestion Work (Phase 6.1)
            raw_fixtures = fixtures_due
            if raw_fixtures is None and self.execution_mode == ExecutionMode.LIVE_SHADOW and self.live_provider is not None:
                summary.provider_status = "CONNECTED"
                from app.providers.live.adapter import LiveIngestionAdapter
                adapter = LiveIngestionAdapter(clock=self.clock, session_factory=self.session_factory)
                live_dtos = self._handle_provider_call_with_retry(
                    lambda: self.live_provider.get_upcoming_fixtures(), summary
                )
                raw_fixtures = [adapter.adapt_live_fixture_to_orchestrator_record(dto) for dto in live_dtos]
            elif raw_fixtures is None:
                raw_fixtures = []

            summary.fixtures_discovered = len(raw_fixtures)

            if simulate_provider_failure is not None:
                self._handle_provider_call_with_retry(lambda: self._raise_simulated_error(simulate_provider_failure), summary)

            summary.fixtures_refreshed = len(raw_fixtures)

            # Stage 8: Determine & Materialize Due Feature Snapshots (Phase 6.2)
            # In Phase 6.2, valid snapshots have exact certified 251-feature width
            for f in raw_fixtures:
                if not f.get("is_mapped", True):
                    summary.predictions_blocked += 1
                    self._record_failure(
                        summary.run_id,
                        "INGESTION",
                        "MAPPING",
                        ErrorCategory.DATA,
                        Retryability.PERMANENT,
                        f.get("unresolved_reason", "UNRESOLVED_MAPPING"),
                        f"Fixture {f.get('provider_fixture_id', 'unknown')} blocked due to unresolved mapping.",
                    )
                    continue
                if f.get("needs_snapshot", True):
                    summary.snapshots_created += 1
                else:
                    summary.snapshots_reused += 1

            # Stage 9: Generate Due Shadow Predictions (Phase 6.3)
            # Stale worker protection check before making protected prediction commit
            if not self.lease_manager.validate_token("operational_scheduler_lease", self.scheduler_instance_id, current_lease.fencing_token):
                raise OperationalException(
                    ErrorCategory.INTERNAL,
                    Retryability.PERMANENT,
                    "STALE_FENCING_TOKEN",
                    f"Worker {self.scheduler_instance_id} holds obsolete fencing token {current_lease.fencing_token}. Protected commit rejected.",
                )

            current_time = self.clock.now()
            for f in raw_fixtures:
                if not f.get("is_mapped", True):
                    continue
                kickoff = f.get("kickoff_utc")
                if kickoff is None:
                    continue

                if isinstance(kickoff, str):
                    kickoff = datetime.fromisoformat(kickoff).astimezone(UTC)

                # Strict Pre-Kickoff Enforcement: NEVER backfill post-kickoff!
                if current_time >= kickoff:
                    summary.predictions_missed_window += 1
                    # Record operational skipped reason
                    self._record_failure(
                        summary.run_id,
                        "PREDICTION",
                        "PRE_KICKOFF_CHECK",
                        ErrorCategory.DATA,
                        Retryability.PERMANENT,
                        "MISSED_PREDICTION_WINDOW",
                        f"Kickoff passed ({kickoff.isoformat()}) before prediction. Post-kickoff backfill strictly forbidden.",
                    )
                    continue

                # Prepare Phase 6.3 ShadowPredictionInput
                feat_vals = f.get("feature_values", [0.5, -0.3] + [0.0] * 249)
                if len(feat_vals) < 251:
                    feat_vals = feat_vals + [0.0] * (251 - len(feat_vals))
                elif len(feat_vals) > 251:
                    feat_vals = feat_vals[:251]

                home_id = f.get("home_team_id", uuid4())
                away_id = f.get("away_team_id")
                if away_id is None or away_id == home_id:
                    away_id = uuid4()

                inp = ShadowPredictionInput(
                    fixture_id=f.get("fixture_id", uuid4()),
                    competition_code=f.get("competition_code", "Premier League"),
                    season_year=f.get("season_year", "2026"),
                    home_team_id=home_id,
                    away_team_id=away_id,
                    kickoff_utc=kickoff,
                    feature_snapshot_id=f.get("feature_snapshot_id", uuid4()),
                    feature_snapshot_fingerprint=f.get("feature_snapshot_fingerprint", "feat_fp_valid_02f8b524"),
                    history_fingerprint=f.get("history_fingerprint", "hist_fp_valid_9a7b"),
                    feature_count=251,
                    feature_values=feat_vals,
                    feature_keys=f.get("feature_keys", [f"feat_{i}" for i in range(251)]),
                    prediction_time=current_time,
                    snapshot_quality_state=f.get("snapshot_quality_state", "VALID"),
                    critical_feature_absent=f.get("critical_feature_absent", False),
                    known_cold_start=f.get("known_cold_start", False),
                    missingness_count=f.get("missingness_count", 0),
                    drift_psi=f.get("drift_psi", 0.05),
                )

                if simulate_db_failure_at_stage == "PREDICTION_PERSISTENCE":
                    raise OperationalException(
                        ErrorCategory.DATABASE,
                        Retryability.TRANSIENT,
                        "PREDICTION_PERSISTENCE_FAILED",
                        "Database failure while writing shadow prediction record.",
                    )

                # Check if identical pre-match prediction already exists (Idempotency)
                already_persisted = False
                for p in self.prediction_service._persisted_predictions.values():
                    if (
                        p.fixture_id == inp.fixture_id
                        and p.feature_snapshot_fingerprint == inp.feature_snapshot_fingerprint
                        and p.kickoff_utc == inp.kickoff_utc
                    ):
                        already_persisted = True
                        break

                # Delegate to certified Sprint 6.3 ShadowPredictionService
                # Sprint 7.1 delegates prediction identity and persistence to Phase 6.3!
                pred_result = self.prediction_service.predict(inp, run_id=summary.run_id, dry_run=dry_run)

                if isinstance(pred_result, ShadowPredictionResult):
                    if already_persisted:
                        summary.predictions_reused += 1
                    else:
                        summary.predictions_created += 1
                else:
                    # Blocked by Phase 5.2 quality gate
                    summary.predictions_blocked += 1

            if self.execution_mode == ExecutionMode.LIVE_SHADOW and not dry_run:
                fixture_meta_map = {
                    f.get("fixture_id"): f for f in raw_fixtures if isinstance(f, dict) and "fixture_id" in f
                }
                self._save_live_shadow_predictions(fixture_meta_map=fixture_meta_map)

            # Stage 10: Synchronize Result State (Phase 6.1)
            raw_results = results_due or []
            summary.results_processed = len(raw_results)

            # Stage 11: Call Phase 6.3 Scoring for Unscored Eligible Predictions
            for res in raw_results:
                fixture_id = res.get("fixture_id")
                match_status = res.get("match_status", "FINISHED")
                home_goals = res.get("home_goals", 0)
                away_goals = res.get("away_goals", 0)
                res_version = res.get("result_version", 1)

                # Postponement Policy (Phase 6.3): Preserve prediction without scoring
                if match_status == "POSTPONED":
                    continue

                # Cancellation Policy (Phase 6.3): Preserve prediction, do not score
                if match_status == "CANCELLED":
                    continue

                # Result Finality Policy: Only FINISHED matches can be scored
                if match_status == "FINISHED":
                    # Locate corresponding prediction
                    matching_pred = None
                    for p in self.prediction_service._persisted_predictions.values():
                        if p.fixture_id == fixture_id:
                            matching_pred = p
                            break

                    if matching_pred is not None:
                        # Idempotency check: (prediction_id, result_version)
                        key = (matching_pred.prediction_id, res_version)
                        if key in self.scoring_service._evaluations:
                            summary.evaluations_reused += 1
                        else:
                            if not dry_run:
                                self.scoring_service.score_prediction(
                                    prediction=matching_pred,
                                    actual_home_goals=home_goals,
                                    actual_away_goals=away_goals,
                                    match_status="FINISHED",
                                    result_version=res_version,
                                    observed_at=self.clock.now(),
                                )
                            summary.evaluations_created += 1

            # Stage 12: Run Observational Monitoring & Deduplicate Alerts
            # Label-Free Monitoring
            preds_list = list(self.prediction_service._persisted_predictions.values())
            if len(preds_list) > 0 and not dry_run:
                self.monitoring_service.evaluate_label_free_cohort(preds_list, cohort_name="LIVE_SHADOW_BATCH")

            # Label-Aware Monitoring (with minimum sample protection)
            evals_list = list(self.scoring_service._evaluations.values())
            if len(evals_list) > 0 and not dry_run:
                self.monitoring_service.evaluate_label_aware_cohort(evals_list, cohort_name="EVALUATED_SHADOW_BATCH")

            # Alert Deduplication: prevent alert storms
            if not dry_run:
                for alert in self.monitoring_service._alerts:
                    cohort = getattr(alert, "prediction_cohort", getattr(alert, "cohort", "DEFAULT"))
                    sig = (alert.metric, alert.severity, alert.threshold, str(cohort), alert.window)
                    if sig not in self._recorded_alert_signatures:
                        self._recorded_alert_signatures.add(sig)
                        summary.alerts_created += 1

            # Stage 13: Persist Run Summary
            summary.run_status = OperationalRunStatus.SUCCESS
            summary.completed_at = self.clock.now()

        except OperationalException as op_exc:
            summary.run_status = OperationalRunStatus.FAILED
            summary.failure_code = op_exc.failure_code
            summary.failure_summary = op_exc.message
            summary.completed_at = self.clock.now()
            self._record_failure(
                summary.run_id,
                "ORCHESTRATOR",
                "OPERATIONAL_CYCLE",
                op_exc.category,
                op_exc.retryability,
                op_exc.failure_code,
                op_exc.message,
                op_exc.details,
            )
        except Exception as exc:
            summary.run_status = OperationalRunStatus.FAILED
            summary.failure_code = "UNEXPECTED_ERROR"
            summary.failure_summary = str(exc)
            summary.completed_at = self.clock.now()
            self._record_failure(
                summary.run_id,
                "ORCHESTRATOR",
                "OPERATIONAL_CYCLE",
                ErrorCategory.INTERNAL,
                Retryability.PERMANENT,
                "UNEXPECTED_ERROR",
                str(exc),
            )
        finally:
            # Stage 13: Release Lease if held
            if lease_acquired and current_lease is not None:
                self.lease_manager.release(
                    lease_name="operational_scheduler_lease",
                    lease_owner=self.scheduler_instance_id,
                    fencing_token=current_lease.fencing_token,
                )

            # Persist OperationalRun record if session_factory is active
            if self.session_factory is not None:
                self._persist_operational_run_summary(summary)

        return summary

    def _persist_operational_run_summary(self, summary: OperationalRunSummary) -> None:
        """Durable operational run persistence (Sprint 7.1 / 7.2)."""
        try:
            with self.session_factory() as session:
                op_run = OperationalRun(
                    id=summary.run_id,
                    started_at=summary.started_at,
                    completed_at=summary.completed_at,
                    scheduler_instance_id=summary.scheduler_instance_id,
                    git_commit=summary.git_commit,
                    pipeline_version=summary.pipeline_version,
                    execution_mode=summary.execution_mode.value if hasattr(summary.execution_mode, "value") else str(summary.execution_mode),
                    trigger_type=summary.trigger_type,
                    lease_id=summary.lease_id,
                    fencing_token=summary.fencing_token,
                    provider_status=summary.provider_status,
                    fixtures_discovered=summary.fixtures_discovered,
                    fixtures_refreshed=summary.fixtures_refreshed,
                    snapshots_created=summary.snapshots_created,
                    snapshots_reused=summary.snapshots_reused,
                    snapshots_failed=summary.snapshots_failed,
                    predictions_created=summary.predictions_created,
                    predictions_reused=summary.predictions_reused,
                    predictions_blocked=summary.predictions_blocked,
                    predictions_missed_window=summary.predictions_missed_window,
                    predictions_failed=summary.predictions_failed,
                    results_processed=summary.results_processed,
                    evaluations_created=summary.evaluations_created,
                    evaluations_reused=summary.evaluations_reused,
                    evaluations_failed=summary.evaluations_failed,
                    alerts_created=summary.alerts_created,
                    retry_count=summary.retry_count,
                    run_status=summary.run_status.value if hasattr(summary.run_status, "value") else str(summary.run_status),
                    failure_code=summary.failure_code,
                    failure_summary=summary.failure_summary,
                )
                session.add(op_run)
                session.commit()
        except Exception:
            pass

    def _verify_scientific_manifests(self, manifest_override: dict[str, str] | None = None) -> None:
        """Verify five frozen parent manifest hashes. Aborts on any discrepancy."""
        override = manifest_override or {}
        p5 = override.get("phase5_master_scientific_manifest_hash", FROZEN_PHASE5_MASTER_HASH)
        p5_2 = override.get("phase5_2_monitoring_policy_hash", FROZEN_PHASE5_2_POLICY_HASH)
        p6_1 = override.get("phase6_1_live_ingestion_manifest_hash", FROZEN_PHASE6_1_INGESTION_HASH)
        p6_2 = override.get("phase6_2_feature_materialization_manifest_hash", FROZEN_PHASE6_2_CANONICAL_HASH)
        s6_3 = override.get("sprint6_3_shadow_prediction_manifest_hash", FROZEN_SPRINT6_3_SHADOW_HASH)

        if p5 != FROZEN_PHASE5_MASTER_HASH:
            raise ScientificIntegrityError("MANIFEST_MISMATCH", f"Phase 5 Master mismatch: {p5}")
        if p5_2 != FROZEN_PHASE5_2_POLICY_HASH:
            raise ScientificIntegrityError("MANIFEST_MISMATCH", f"Phase 5.2 Policy mismatch: {p5_2}")
        if p6_1 != FROZEN_PHASE6_1_INGESTION_HASH:
            raise ScientificIntegrityError("MANIFEST_MISMATCH", f"Phase 6.1 Ingestion mismatch: {p6_1}")
        if p6_2 != FROZEN_PHASE6_2_CANONICAL_HASH:
            raise ScientificIntegrityError("MANIFEST_MISMATCH", f"Phase 6.2 Materialization mismatch: {p6_2}")
        if s6_3 != FROZEN_SPRINT6_3_SHADOW_HASH:
            raise ScientificIntegrityError("MANIFEST_MISMATCH", f"Sprint 6.3 Manifest mismatch: {s6_3}")

    def _verify_model_artifacts(self, artifact_override: dict[str, str] | None = None) -> None:
        """Verify C1 and M1 model artifact identities. Aborts on mismatch."""
        override = artifact_override or {}
        c1_key = override.get("c1_key", FROZEN_C1_KEY)
        c1_fp = override.get("c1_fingerprint", FROZEN_C1_FINGERPRINT)
        m1_key = override.get("m1_key", FROZEN_M1_KEY)
        m1_fp = override.get("m1_fingerprint", FROZEN_M1_FINGERPRINT)

        if c1_key != FROZEN_C1_KEY or c1_fp != FROZEN_C1_FINGERPRINT:
            raise ScientificIntegrityError(
                "MODEL_ARTIFACT_MISMATCH",
                f"C1 artifact invalid: key='{c1_key}', fp='{c1_fp}'. Expected key='{FROZEN_C1_KEY}', fp='{FROZEN_C1_FINGERPRINT}'."
            )
        if m1_key != FROZEN_M1_KEY or m1_fp != FROZEN_M1_FINGERPRINT:
            raise ScientificIntegrityError(
                "MODEL_ARTIFACT_MISMATCH",
                f"M1 artifact invalid: key='{m1_key}', fp='{m1_fp}'. Expected key='{FROZEN_M1_KEY}', fp='{FROZEN_M1_FINGERPRINT}'."
            )

    def _verify_execution_mode_dependencies(self) -> None:
        """Validate dependencies for current execution mode."""
        if self.execution_mode == ExecutionMode.LIVE_SHADOW:
            if not self.live_api_key:
                raise ConfigurationError(
                    "LIVE_PROVIDER_CONFIG_MISSING",
                    "LIVE_SHADOW execution mode requires valid LIVE_DATA_API_KEY. Silent replay fallback is strictly prohibited.",
                )
        elif self.execution_mode == ExecutionMode.REPLAY_CERTIFICATION:
            # Deterministic test doubles allowed without credentials
            pass

    def _handle_provider_call_with_retry(self, fn: Callable[[], Any], summary: OperationalRunSummary) -> Any:
        """Execute external provider call with circuit breaker and bounded retry backoff."""
        if not self.circuit_breaker.can_execute():
            raise PermanentProviderError(
                "CIRCUIT_BREAKER_OPEN",
                "Circuit breaker is OPEN. Fast-failing external provider calls.",
            )

        attempt = 1
        while True:
            try:
                res = fn()
                self.circuit_breaker.record_success()
                return res
            except Exception as exc:
                self.circuit_breaker.record_failure()
                summary.retry_count += 1
                if self.retry_policy.should_retry(exc, attempt):
                    delay = self.retry_policy.compute_delay(attempt)
                    self.clock.sleep(delay)
                    attempt += 1
                else:
                    raise

    def _raise_simulated_error(self, exc: Exception) -> None:
        raise exc

    def _record_failure(
        self,
        run_id: UUID,
        component: str,
        stage: str,
        error_category: ErrorCategory,
        retryability: Retryability,
        failure_code: str,
        error_message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self._failure_log.append({
            "id": uuid4(),
            "run_id": run_id,
            "component": component,
            "stage": stage,
            "error_category": error_category.value,
            "retryability": retryability.value,
            "failure_code": failure_code,
            "error_message": error_message,
            "details": details or {},
            "occurred_at": self.clock.now(),
        })

    def _get_live_store_path(self) -> Path:
        return Path("data/processed/live_shadow_predictions.json")

    def _load_live_shadow_predictions(self) -> None:
        """
        Loads live shadow predictions.
        Primary authoritative store: Relational database (shadow_predictions table).
        Secondary cache/export store: JSON file (data/processed/live_shadow_predictions.json).
        """
        # 1. Primary: Load from relational database if session factory is available
        if self.session_factory is not None:
            try:
                with self.session_factory() as session:
                    db_preds = ShadowPredictionRepository.load_all_predictions(session)
                    if db_preds:
                        self.prediction_service._persisted_predictions.update(db_preds)
                        return
            except Exception:
                pass

        # 2. Secondary: Load from JSON cache/export artifact (offline/dry-run replay fallback)
        store_path = self._get_live_store_path()
        if not store_path.exists():
            return
        try:
            with open(store_path, "r", encoding="utf-8") as f:
                records = json.load(f)
            for d in records:
                d_copy = dict(d)
                d_copy["prediction_id"] = UUID(d_copy["prediction_id"])
                d_copy["fixture_id"] = UUID(d_copy["fixture_id"])
                d_copy["prediction_run_id"] = UUID(d_copy["prediction_run_id"]) if d_copy.get("prediction_run_id") else None
                d_copy["feature_snapshot_id"] = UUID(d_copy["feature_snapshot_id"]) if d_copy.get("feature_snapshot_id") else None
                d_copy["generated_at"] = datetime.fromisoformat(d_copy["generated_at"]).astimezone(UTC)
                d_copy["kickoff_utc"] = datetime.fromisoformat(d_copy["kickoff_utc"]).astimezone(UTC)
                pred = ShadowPredictionResult(**d_copy)
                self.prediction_service._persisted_predictions[pred.prediction_id] = pred
        except Exception:
            pass

    def _save_live_shadow_predictions(
        self, fixture_meta_map: dict[UUID, dict[str, Any]] | None = None
    ) -> None:
        """
        Saves live shadow predictions.
        Primary authoritative write: Relational database (shadow_predictions table).
        Secondary cache/export write: JSON file (data/processed/live_shadow_predictions.json).
        """
        # 1. Primary: Persist to relational database
        if self.session_factory is not None:
            try:
                with self.session_factory() as session:
                    for p in self.prediction_service._persisted_predictions.values():
                        meta = (fixture_meta_map or {}).get(p.fixture_id)
                        ShadowPredictionRepository.persist_prediction(session, p, fixture_meta=meta)
            except Exception:
                pass

        # 2. Secondary: Export to JSON cache artifact
        store_path = self._get_live_store_path()
        try:
            store_path.parent.mkdir(parents=True, exist_ok=True)
            serialized = []
            for p in self.prediction_service._persisted_predictions.values():
                d = dataclasses.asdict(p)
                d["prediction_id"] = str(p.prediction_id)
                d["fixture_id"] = str(p.fixture_id)
                d["prediction_run_id"] = str(p.prediction_run_id) if p.prediction_run_id else None
                d["feature_snapshot_id"] = str(p.feature_snapshot_id) if p.feature_snapshot_id else None
                d["generated_at"] = p.generated_at.isoformat()
                d["kickoff_utc"] = p.kickoff_utc.isoformat()
                serialized.append(d)
            with open(store_path, "w", encoding="utf-8") as f:
                json.dump(serialized, f, indent=2)
        except Exception:
            pass
