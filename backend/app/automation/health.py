"""
Operational Health Diagnostics & Staleness Watchdog (Sections 40-43)
Exposes operational health summary (HEALTHY, DEGRADED, STALE, FAILED)
and strictly isolates operational health from Phase 5.2 scientific model quality states.
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

from app.automation.clock import Clock, SystemClock
from app.automation.domain import HealthStatusDTO, OperationalHealthState
from app.automation.lease import LeaseManager
from app.core.config import settings

FROZEN_PHASE5_MASTER_HASH = "68b151b2c9683d4a431605bfda45902d78a2335f533ab49256ef47713ce22524"
FROZEN_PHASE5_2_POLICY_HASH = "f974e06a3e2ad4730ed9e6d55ede58f10dd31f5400cd9e39c0c6b06df362887b"
FROZEN_PHASE6_1_INGESTION_HASH = "98c0e07bee158e0493dbfbff036bc6b3e8c2f763c87c9d715a8ee5a2df0423d7"
FROZEN_PHASE6_2_CANONICAL_HASH = "7ac6041ff6169a321524b39f7c2bd3610913c94c3b4fc350a89c2550690a8b7b"
FROZEN_SPRINT6_3_SHADOW_HASH = "e59dbe19c9a0568271e25fb9b1250a267a4a78c80ef202641c89bb6ce99b98ee"

EXPECTED_ALEMBIC_HEAD = "20261105_0007"

DEFAULT_RUN_STALE_AFTER_HOURS: float = 24.0
DEFAULT_PREDICTION_STALE_AFTER_HOURS: float = 48.0


class HealthWatchdog:
    """
    Evaluates operational health without modifying system state.
    Monitors database connectivity, migration head, scientific manifest integrity,
    model artifact availability, lease health, and operational staleness.
    Staleness thresholds are configuration-driven with certified defaults (24h run, 48h prediction).
    """

    def __init__(
        self,
        clock: Clock | None = None,
        lease_manager: LeaseManager | None = None,
        max_run_staleness_hours: float | None = None,
        max_prediction_staleness_hours: float | None = None,
    ) -> None:
        self.clock = clock or SystemClock()
        self.lease_manager = lease_manager

        run_val = (
            max_run_staleness_hours
            if max_run_staleness_hours is not None
            else getattr(settings, "OPS_RUN_STALE_AFTER_HOURS", DEFAULT_RUN_STALE_AFTER_HOURS)
        )
        pred_val = (
            max_prediction_staleness_hours
            if max_prediction_staleness_hours is not None
            else getattr(settings, "OPS_PREDICTION_STALE_AFTER_HOURS", DEFAULT_PREDICTION_STALE_AFTER_HOURS)
        )

        self._validate_threshold("max_run_staleness_hours", run_val)
        self._validate_threshold("max_prediction_staleness_hours", pred_val)

        self.max_run_staleness_hours = float(run_val)
        self.max_prediction_staleness_hours = float(pred_val)

    @staticmethod
    def _validate_threshold(name: str, val: Any) -> None:
        if val is None or not isinstance(val, (int, float)) or isinstance(val, bool):
            raise ValueError(f"INVALID_STALENESS_CONFIG: '{name}' must be a positive number, got {val}.")
        if math.isnan(val) or math.isinf(val) or val <= 0:
            raise ValueError(
                f"INVALID_STALENESS_CONFIG: '{name}' must be strictly positive and finite (> 0), got {val}."
            )

    def check_health(
        self,
        database_reachable: bool = True,
        current_alembic_head: str = EXPECTED_ALEMBIC_HEAD,
        c1_available: bool = True,
        m1_available: bool = True,
        last_successful_run: datetime | None = None,
        last_prediction_activity: datetime | None = None,
        provider_configured: bool = False,
    ) -> HealthStatusDTO:
        diagnostics: list[str] = []
        now = self.clock.now()

        # 1. Database & Schema Checks
        alembic_valid = (current_alembic_head == EXPECTED_ALEMBIC_HEAD)
        if not database_reachable:
            diagnostics.append("DATABASE_UNREACHABLE: Database connection failed.")
        if not alembic_valid:
            diagnostics.append(
                f"ALEMBIC_HEAD_MISMATCH: Current head is '{current_alembic_head}', expected '{EXPECTED_ALEMBIC_HEAD}'."
            )

        # 2. Scientific Manifest Integrity
        manifest_valid = self._verify_manifests()
        if not manifest_valid:
            diagnostics.append("MANIFEST_INTEGRITY_FAILURE: Frozen parent manifest hashes do not match certified baseline.")

        # 3. Model Artifact Availability
        if not c1_available:
            diagnostics.append("MODEL_ARTIFACT_MISSING: C1 Logistic Regression artifact unavailable.")
        if not m1_available:
            diagnostics.append("MODEL_ARTIFACT_MISSING: M1 Poisson artifact unavailable.")

        # 4. Lease State
        lease_active = False
        fencing_token = 0
        if self.lease_manager:
            lease = self.lease_manager.get_lease("operational_scheduler_lease")
            if lease:
                lease_active = lease.is_active and not lease.is_expired(now)
                fencing_token = lease.fencing_token

        # 5. Staleness Checks
        is_stale = False
        if last_successful_run is not None:
            time_since_last_run = (now - last_successful_run).total_seconds() / 3600.0
            if time_since_last_run > self.max_run_staleness_hours:
                is_stale = True
                diagnostics.append(
                    f"OPERATIONAL_STALENESS: Last successful run was {time_since_last_run:.1f}h ago (limit: {self.max_run_staleness_hours}h)."
                )

        if last_prediction_activity is not None:
            time_since_pred = (now - last_prediction_activity).total_seconds() / 3600.0
            if time_since_pred > self.max_prediction_staleness_hours:
                is_stale = True
                diagnostics.append(
                    f"PREDICTION_STALENESS: Last prediction activity was {time_since_pred:.1f}h ago (limit: {self.max_prediction_staleness_hours}h)."
                )

        # Determine Operational Health State
        if not database_reachable or not manifest_valid or not alembic_valid or not c1_available or not m1_available:
            state = OperationalHealthState.FAILED
        elif is_stale:
            state = OperationalHealthState.STALE
        elif not provider_configured:
            # Provider pending is DEGRADED operational state
            state = OperationalHealthState.DEGRADED
            diagnostics.append("PROVIDER_PENDING: Real provider credentials not configured (replay/shadow pending).")
        else:
            state = OperationalHealthState.HEALTHY

        return HealthStatusDTO(
            health_state=state,
            database_reachable=database_reachable,
            alembic_head_valid=alembic_valid,
            scientific_manifests_valid=manifest_valid,
            c1_available=c1_available,
            m1_available=m1_available,
            lease_active=lease_active,
            current_fencing_token=fencing_token,
            last_successful_run=last_successful_run,
            last_prediction_activity=last_prediction_activity,
            provider_configured=provider_configured,
            diagnostics=diagnostics,
        )

    def _verify_manifests(self) -> bool:
        # Check manifest file if available on disk
        manifest_path = Path(__file__).resolve().parents[3] / "phase6_3_shadow_prediction_manifest.json"
        if not manifest_path.exists():
            return False  # Missing scientific evidence is never a healthy state.

        try:
            with open(manifest_path, encoding="utf-8") as f:
                data = json.load(f)
            parents = data.get("immutable_scientific_manifest_hashes", {})
            return (
                parents.get("phase5_master_scientific_manifest_hash") == FROZEN_PHASE5_MASTER_HASH
                and parents.get("phase5_2_monitoring_policy_hash") == FROZEN_PHASE5_2_POLICY_HASH
                and parents.get("phase6_1_live_ingestion_manifest_hash") == FROZEN_PHASE6_1_INGESTION_HASH
                and parents.get("phase6_2_feature_materialization_manifest_hash") == FROZEN_PHASE6_2_CANONICAL_HASH
            )
        except Exception:
            return False
