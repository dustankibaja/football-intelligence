"""Unattended worker keeps retrying failures and respects explicit stop."""
import threading

import pytest

from app.automation.worker import run_scheduler


def test_worker_retries_failed_cycle_then_succeeds():
    results = iter([3, 1, 0])
    seen = []
    runs = run_scheduler(
        interval_seconds=300, stop_event=threading.Event(),
        run_cycle=lambda: seen.append(next(results)) or seen[-1],
        max_cycles=3,
    )
    assert runs == 3
    assert seen == [3, 1, 0]


def test_worker_stops_without_running_after_stop_signal():
    stop = threading.Event()
    stop.set()
    assert run_scheduler(interval_seconds=300, stop_event=stop, run_cycle=lambda: 0) == 0


def test_worker_rejects_excessive_polling():
    with pytest.raises(ValueError, match="WORKER_INTERVAL_OUT_OF_RANGE"):
        run_scheduler(interval_seconds=1, stop_event=threading.Event())
