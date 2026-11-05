"""Restartable, unattended private shadow prediction worker.

This process is intentionally separate from FastAPI: web request traffic must
never control prediction scheduling. The SQL lease protects overlapping workers.
"""
from __future__ import annotations

import argparse
import logging
import signal
import threading
from collections.abc import Callable

from app.automation.cli import ops_run_once

logger = logging.getLogger("footmo.worker")


def run_scheduler(
    *,
    interval_seconds: int,
    stop_event: threading.Event,
    run_cycle: Callable[[], int] | None = None,
    max_cycles: int | None = None,
    wait: Callable[[float], bool] | None = None,
) -> int:
    if not 300 <= interval_seconds <= 86400:
        raise ValueError("WORKER_INTERVAL_OUT_OF_RANGE")
    if max_cycles is not None and max_cycles < 1:
        raise ValueError("WORKER_MAX_CYCLES_INVALID")

    cycle = run_cycle or (lambda: ops_run_once(["--mode", "LIVE_SHADOW", "--trigger", "UNATTENDED_WORKER"]))
    wait_fn = wait or stop_event.wait
    runs = 0
    while not stop_event.is_set():
        try:
            result = cycle()
            if result:
                logger.error("Unattended cycle ended with status=%d; retrying next interval", result)
            else:
                logger.info("Unattended prediction cycle completed successfully")
        except Exception:
            # The worker must survive transient provider outages; each failed
            # cycle is still reported in logs, never falsely called successful.
            logger.exception("Unattended prediction cycle crashed")
        runs += 1
        if max_cycles is not None and runs >= max_cycles:
            break
        if wait_fn(interval_seconds):
            break
    return runs


def main() -> None:
    parser = argparse.ArgumentParser(description="Continuous private Footmo prediction worker")
    parser.add_argument("--interval-seconds", type=int, default=21600)
    parser.add_argument("--max-cycles", type=int)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    stop_event = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop_event.set())
    signal.signal(signal.SIGINT, lambda *_: stop_event.set())
    logger.info("Footmo private worker started; interval=%ds", args.interval_seconds)
    runs = run_scheduler(
        interval_seconds=args.interval_seconds, stop_event=stop_event,
        max_cycles=args.max_cycles,
    )
    logger.info("Footmo worker stopped after %d cycles", runs)


if __name__ == "__main__":
    main()
