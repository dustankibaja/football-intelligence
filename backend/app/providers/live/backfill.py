"""Explicit CLI for provider-backed, current-season history synchronization.

Usage:
    cd backend
    python -m app.providers.live.backfill --days 30 --dry-run
    python -m app.providers.live.backfill --days 30

Requires LIVE_DATA_API_KEY and a migrated canonical PostgreSQL database.
Never runs automatically during training or changes frozen dataset releases.
"""
from __future__ import annotations

import argparse
import json

from app.db.session import SessionLocal
from app.providers.live.client import LiveFootballDataClient
from app.providers.live.history import LiveHistoryIngestor


def main() -> None:
    parser = argparse.ArgumentParser(description="Import verified final match history")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--window-days", type=int, default=7)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    provider = LiveFootballDataClient()
    records = provider.get_recent_finished_matches(
        days_back=args.days, window_days=args.window_days
    )
    if args.dry_run:
        print(json.dumps({
            "mode": "DRY_RUN",
            "provider": provider.provider_name,
            "finished_matches_received": len(records),
            "mapped_matches": sum(bool(r.get("is_mapped")) for r in records),
            "unresolved_matches": sum(not r.get("is_mapped") for r in records),
            "database_changes": 0,
        }, indent=2))
        return

    result = LiveHistoryIngestor(SessionLocal).ingest(records)
    print(json.dumps({
        "mode": "INGEST",
        "provider": provider.provider_name,
        "finished_matches_received": len(records),
        **result,
    }, indent=2))


if __name__ == "__main__":
    main()
