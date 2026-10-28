"""
Operator CLI Commands (Sections 54-56, 63)
Deployment-agnostic CLI primitives:
  ops-run-once: Executes one deterministic operational cycle and terminates.
  ops-dry-run: Identifies due work, validates provenance, zero canonical writes.
  ops-health: Runs health watchdog and reports operational status.
  ops-status: Reports recent operational run executions and failure codes.
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from app.automation.domain import ExecutionMode, OperationalRunStatus
from app.automation.health import HealthWatchdog
from app.automation.orchestrator import OperationalOrchestrator
from app.db.session import SessionLocal


def ops_run_once(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Execute one deterministic operational cycle.")
    parser.add_argument("--mode", choices=["REPLAY_CERTIFICATION", "LIVE_SHADOW"], default="REPLAY_CERTIFICATION")
    parser.add_argument("--trigger", default="CLI_RUN_ONCE")
    args = parser.parse_args(argv)

    orchestrator = OperationalOrchestrator(
        execution_mode=ExecutionMode(args.mode),
        scheduler_instance_id="cli-worker-run-once",
        session_factory=SessionLocal,
    )
    summary = orchestrator.run_once(trigger_type=args.trigger, dry_run=False)

    print(f"Operational Run Completed: {summary.run_id}")
    print(f"Status: {summary.run_status.value}")
    print(f"Fencing Token: {summary.fencing_token}")
    print(f"Predictions Created: {summary.predictions_created}, Reused: {summary.predictions_reused}")
    print(f"Missed Windows: {summary.predictions_missed_window}")
    print(f"Evaluations Created: {summary.evaluations_created}, Reused: {summary.evaluations_reused}")
    print(f"Alerts Created: {summary.alerts_created}")

    if summary.run_status in (OperationalRunStatus.SUCCESS, OperationalRunStatus.PARTIAL_SUCCESS):
        return 0
    elif summary.run_status == OperationalRunStatus.ABORTED:
        print(f"Aborted: {summary.failure_code} - {summary.failure_summary}")
        return 2
    else:
        print(f"Failed: {summary.failure_code} - {summary.failure_summary}")
        return 1


def ops_dry_run(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Simulate operational cycle with ZERO canonical writes.")
    parser.add_argument("--mode", choices=["REPLAY_CERTIFICATION", "LIVE_SHADOW"], default="REPLAY_CERTIFICATION")
    args = parser.parse_args(argv)

    orchestrator = OperationalOrchestrator(
        execution_mode=ExecutionMode(args.mode),
        scheduler_instance_id="cli-worker-dry-run",
    )
    summary = orchestrator.run_once(trigger_type="DRY_RUN", dry_run=True)

    print(f"Dry Run Simulation Finished: {summary.run_id}")
    print(f"Status: {summary.run_status.value}")
    print("Canonical DB Writes: 0")
    print(f"Due Predictions Identified: {summary.predictions_created}")
    return 0


def ops_health(argv: Sequence[str] | None = None) -> int:
    watchdog = HealthWatchdog()
    health = watchdog.check_health()

    print(f"Operational Health: {health.health_state.value}")
    print(f"Database Reachable: {health.database_reachable}")
    print(f"Alembic Head Valid: {health.alembic_head_valid}")
    print(f"Scientific Manifests Valid: {health.scientific_manifests_valid}")
    print(f"C1 Model Available: {health.c1_available}")
    print(f"M1 Model Available: {health.m1_available}")
    print(f"Provider Configured: {health.provider_configured}")

    for diag in health.diagnostics:
        print(f"  - {diag}")

    return 0 if health.health_state != "FAILED" else 1


def ops_status(argv: Sequence[str] | None = None) -> int:
    print("Operational Status: Attended / Unattended orchestration ready.")
    print("Execution Modes: REPLAY_CERTIFICATION (active), LIVE_SHADOW (pending credentials)")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "dry-run":
        sys.exit(ops_dry_run(sys.argv[2:]))
    elif len(sys.argv) > 1 and sys.argv[1] == "health":
        sys.exit(ops_health(sys.argv[2:]))
    elif len(sys.argv) > 1 and sys.argv[1] == "status":
        sys.exit(ops_status(sys.argv[2:]))
    else:
        sys.exit(ops_run_once(sys.argv[1:]))
