"""Versioned pre-match exact-score extension of the original 63-forecast ledger.

This deterministic research-only extension derives independent-Poisson score
probabilities from lambdas *already issued before the game*. It neither
rewrites the original shadow ledger nor claims externally validated results.
"""
from __future__ import annotations

import argparse
from datetime import UTC, date, datetime
from hashlib import sha256
import json
import math
from pathlib import Path


ORIGINAL_63_LEDGER_SHA256 = (
    "a04e7d71389c0e18bae2aa2bbb21285f5de3d4d98874a51ff9559fd1e9de3412"
)


def _fingerprint(obj: dict) -> str:
    return sha256(json.dumps(obj, sort_keys=True).encode("utf-8")).hexdigest()


def derive_exact_scores(original_ledger: dict, *, issued_at_utc: datetime) -> dict:
    """Issue deterministic 0..10 score grid while ALL matches are still future."""
    if issued_at_utc.tzinfo is None:
        raise ValueError("UTC_AWARE_EXTENSION_ISSUANCE_REQUIRED")
    now = issued_at_utc.astimezone(UTC)
    unsigned = dict(original_ledger)
    declared = unsigned.pop("ledger_sha256", None)
    if declared != ORIGINAL_63_LEDGER_SHA256 or _fingerprint(unsigned) != declared:
        raise ValueError("ORIGINAL_63_FORECAST_LEDGER_INTEGRITY_FAILURE")
    if original_ledger.get("status") != "RESEARCH_SHADOW_NOT_CERTIFIED_OR_COMMERCIAL":
        raise ValueError("RESEARCH_FORECAST_LEDGER_ONLY")
    issued = datetime.fromisoformat(original_ledger["generated_at_utc"])
    if issued.tzinfo is None or now <= issued.astimezone(UTC):
        raise ValueError("EXTENSION_MUST_POSTDATE_ORIGINAL_FORECAST")
    forecasts = original_ledger.get("predictions", [])
    if len(forecasts) != 63 or original_ledger["counts"]["forecasts_issued"] != 63:
        raise ValueError("ORIGINAL_63_FORECAST_COVERAGE_MISMATCH")
    rows = []
    fixture_ids: set[str] = set()
    for forecast in forecasts:
        fid = forecast["fixture_sha256"]
        if fid in fixture_ids or len(fid) != 64:
            raise ValueError("DUPLICATE_OR_INVALID_ORIGINAL_FIXTURE")
        fixture_ids.add(fid)
        if date.fromisoformat(forecast["expected_calendar_date"]) <= now.date():
            raise ValueError("EXACT_SCORE_EXTENSION_AFTER_FIXTURE_DAY_PROHIBITED")
        if forecast["status"] != "SHADOW_ONLY_UNCALIBRATED_WAITING_FOR_MATCH":
            raise ValueError("ORIGINAL_FORECAST_NOT_PENDING")

        lh = float(forecast["lambda_home"])
        la = float(forecast["lambda_away"])
        if not all(math.isfinite(v) and 0 < v <= 15 for v in (lh, la)):
            raise ValueError("INVALID_PREISSUED_EXPECTED_GOALS")

        home_p = [math.exp(-lh) * (lh ** i) / math.factorial(i) for i in range(11)]
        away_p = [math.exp(-la) * (la ** j) / math.factorial(j) for j in range(11)]
        raw = [[home_p[i] * away_p[j] for j in range(11)] for i in range(11)]
        retained = sum(map(sum, raw))
        if not 0 < retained <= 1.0 + 1e-9:
            raise ValueError("INVALID_SCORE_MATRIX_MASS")
        grid = [[v / retained for v in row] for row in raw]

        reconstructed = (
            sum(grid[i][j] for i in range(11) for j in range(11) if i > j),
            sum(grid[i][i] for i in range(11)),
            sum(grid[i][j] for i in range(11) for j in range(11) if i < j),
        )
        mismatch = max(
            abs(p - float(forecast[key]))
            for p, key in zip(reconstructed, ("prob_home", "prob_draw", "prob_away"))
        )
        if mismatch > 0.00001:
            raise ValueError("RECONSTRUCTED_1X2_DOES_NOT_MATCH_ORIGINAL")

        all_scores = [
            {"home_goals": i, "away_goals": j, "probability": round(grid[i][j], 8)}
            for i in range(11) for j in range(11)
        ]
        top_five = sorted(
            all_scores,
            key=lambda score: (-score["probability"], score["home_goals"], score["away_goals"]),
        )[:5]
        rows.append({
            "fixture_sha256": fid,
            "league": forecast["league"],
            "home": forecast["provider_home"],
            "away": forecast["provider_away"],
            "expected_calendar_date": forecast["expected_calendar_date"],
            "home_lambda_original": lh,
            "away_lambda_original": la,
            "model_sha256_original": forecast["model_artifact_sha256"],
            "matrix_shape": [11, 11],
            "retained_mass_before_normalization": round(retained, 12),
            "out_of_grid_probability_mass": round(max(0.0, 1.0 - retained), 12),
            "score_probability_matrix_conditional_0_to_10": [
                [round(v, 10) for v in row] for row in grid
            ],
            "top5_scores": top_five,
            "max_1x2_discrepancy": mismatch,
        })

    rows.sort(key=lambda item: item["fixture_sha256"])
    report = {
        "schema_version": "prospective_exact_score_extension_1.0.0",
        "status": "RESEARCH_ONLY_UNCALIBRATED_PREMATCH_DERIVATION",
        "generated_at_utc": now.isoformat(),
        "original_forecast_ledger_sha256": declared,
        "count": len(rows),
        "method": "INDEPENDENT_POISSON_RHO_ZERO_GRID_0_TO_10_CONDITIONAL_NORMALIZATION",
        "probability_status": "RAW_UNCALIBRATED",
        "max_1x2_reconstruction_discrepancy": max(
            row["max_1x2_discrepancy"] for row in rows
        ),
        "limitations": [
            "Separately dated pre-match extension, not part of the original November 9 issuance.",
            "Score probabilities derive from prior lambdas, not separately calibrated scores.",
            "Both goals are conditionally normalized to 0..10; tail mass remains explicit.",
            "No actual post-match results used in construction, and no production publication.",
        ],
        "predictions": rows,
    }
    report["extension_ledger_sha256"] = _fingerprint(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--original-ledger", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    original_path = Path(args.original_ledger)
    output_path = Path(args.output)
    if original_path.resolve() == output_path.resolve():
        raise ValueError("NO_OVERWRITE_OF_ORIGINAL_FORECAST_LEDGER")
    original = json.loads(original_path.read_text(encoding="utf-8"))
    result = derive_exact_scores(original, issued_at_utc=datetime.now(UTC))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": result["status"],
        "forecast_count": result["count"],
        "extension_hash": result["extension_ledger_sha256"],
        "max_1x2_discrepancy": result["max_1x2_reconstruction_discrepancy"],
    }, indent=2))


if __name__ == "__main__":
    main()
