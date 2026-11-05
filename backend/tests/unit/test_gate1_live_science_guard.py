"""Gate 1 regression: no live fixture may use unverified heuristic inference."""
from datetime import UTC, datetime, timedelta

import pytest

from app.automation.clock import FakeClock
from app.automation.domain import ExecutionMode, OperationalRunStatus
from app.automation.lease import LeaseManager
from app.automation.orchestrator import OperationalOrchestrator
from app.prediction.service import ShadowPredictionService


def test_unverified_frozen_model_artifacts_fail_closed():
    with pytest.raises(RuntimeError, match="LIVE_ARTIFACT_INFERENCE_NOT_CONNECTED"):
        ShadowPredictionService().assert_live_inference_ready()


def test_live_scheduler_does_not_publish_heuristic_predictions():
    clock = FakeClock(datetime(2026, 11, 5, 12, 0, tzinfo=UTC))
    orchestrator = OperationalOrchestrator(
        clock=clock,
        lease_manager=LeaseManager(clock=clock),
        execution_mode=ExecutionMode.LIVE_SHADOW,
        live_api_key="unit-test-key",
    )
    fixture = {
        "is_mapped": True,
        "kickoff_utc": clock.now() + timedelta(hours=3),
    }
    summary = orchestrator.run_once(fixtures_due=[fixture])

    assert summary.run_status == OperationalRunStatus.FAILED
    assert summary.failure_code == "LIVE_ARTIFACT_INFERENCE_NOT_CONNECTED"
    assert summary.predictions_created == 0
    assert not orchestrator.prediction_service._persisted_predictions
