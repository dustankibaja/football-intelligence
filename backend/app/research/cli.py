"""Run Sprint 7 offline research; output checksummed candidate evidence only."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from app.research.benchmark import benchmark
from app.research.data import acquire
from app.research.models import build_point_in_time_rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Footmo isolated research benchmark")
    parser.add_argument("--cache-dir", type=Path,
                        default=Path("data/research/sprint7/raw"))
    parser.add_argument("--output", type=Path,
                        default=Path("data/research/sprint7/report.json"))
    parser.add_argument("--offline", action="store_true",
                        help="Require previously downloaded, verified local CSV files")
    args = parser.parse_args()
    matches, manifest = acquire(args.cache_dir, offline=args.offline)
    rows = build_point_in_time_rows(matches)
    results = benchmark(rows)
    report = {
        "researchVersion": "sprint7-research-v1",
        "sourceManifest": manifest,
        "benchmark": results,
        "limitations": [
            "Football-Data.co.uk dates lack verified kickoff times; same-day results are embargoed",
            "Provider team names are not yet reconciled to live football-data.org identities",
            "Research proxy models are not the frozen C1/M1 certified inference artifacts",
            "Calibrated historical results do not certify live profitability or future accuracy",
            "No historical data or trained research coefficients are deployed to production"
        ]
    }
    payload = json.dumps(report, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False)
    report["reportFingerprintSha256"] = hashlib.sha256(payload.encode()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8")
    print(json.dumps({
        "status": results["status"],
        "fixtures": manifest["totalFixtures"],
        "sources": manifest["sourceCount"],
        "heldout": results["holdoutFixtureCount"],
        "fingerprint": report["reportFingerprintSha256"],
        "reportPath": str(args.output)
    }))


if __name__ == "__main__":
    main()
