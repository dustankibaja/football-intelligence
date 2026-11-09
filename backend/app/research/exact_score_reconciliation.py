"""Proper exact-score log scoring of independently issued 63-match shadow extension.

Evaluate only when independently verified final result receipts are available.
Keep original 1X2 forecast, exact-score extension and final-result audit separate.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from hashlib import sha256
import json
import math


ORIGINAL_63_LEDGER_SHA256 = (
    "a04e7d71389c0e18bae2aa2bbb21285f5de3d4d98874a51ff9559fd1e9de3412"
)


def _digest(payload: dict) -> str:
    return sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _verify_ledger(value: dict, hash_field: str) -> None:
    copy = dict(value)
    recorded = copy.pop(hash_field, None)
    if not recorded or recorded != _digest(copy):
        raise ValueError(f"IMMUTABLE_LEDGER_INTEGRITY_FAILURE:{hash_field}")


def _parse_timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("UTC_AWARE_EVIDENCE_TIMESTAMP_REQUIRED")
    return result.astimezone(UTC)


def evaluate_exact_scores(
    preissued_extension: dict, result_assessment: dict,
) -> dict:
    """Evaluate proper infinite-support Poisson likelihood, not top-five zeros."""
    _verify_ledger(preissued_extension, "extension_ledger_sha256")
    _verify_ledger(result_assessment, "report_sha256")
    if (
        preissued_extension.get("status")
        != "RESEARCH_ONLY_UNCALIBRATED_PREMATCH_DERIVATION"
        or preissued_extension.get("count") != 63
        or preissued_extension.get("original_forecast_ledger_sha256")
        != ORIGINAL_63_LEDGER_SHA256
        or result_assessment.get("original_forecast_ledger_sha256")
        != ORIGINAL_63_LEDGER_SHA256
        or result_assessment.get("original_forecasts") != 63
    ):
        raise ValueError("PROSPECTIVE_EXACT_SCORE_PROVENANCE_MISMATCH")
    issued = _parse_timestamp(preissued_extension["generated_at_utc"])
    evaluated = _parse_timestamp(result_assessment["evaluated_at_utc"])
    if evaluated <= issued:
        raise ValueError("RESULT_ASSESSMENT_PREDATES_PREISSUED_EXACT_SCORE")
    predictions = preissued_extension.get("predictions", [])
    if len(predictions) != 63:
        raise ValueError("INCOMPLETE_EXACT_SCORE_EXTENSION")

    by_id = {}
    for pred in predictions:
        fid = pred["fixture_sha256"]
        if fid in by_id or not isinstance(fid, str) or len(fid) != 64:
            raise ValueError("DUPLICATE_OR_INVALID_SCORE_EXTENSION_FIXTURE")
        by_id[fid] = pred
        if date.fromisoformat(pred["expected_calendar_date"]) <= issued.date():
            raise ValueError("EXACT_SCORE_DERIVED_TOO_LATE")
        grid = pred["score_probability_matrix_conditional_0_to_10"]
        if (
            pred["matrix_shape"] != [11, 11]
            or len(grid) != 11
            or any(len(row) != 11 for row in grid)
        ):
            raise ValueError("EXACT_SCORE_MATRIX_SCHEMA_INVALID")
        values = [float(x) for row in grid for x in row]
        if (
            any(not math.isfinite(x) or x < 0 or x > 1 for x in values)
            or abs(sum(values) - 1.0) > 1e-7
        ):
            raise ValueError("EXACT_SCORE_MATRIX_INVALID")
        mass = float(pred["retained_mass_before_normalization"])
        if not 0 < mass <= 1:
            raise ValueError("EXACT_SCORE_GRID_MASS_INVALID")
        lh = float(pred["home_lambda_original"])
        la = float(pred["away_lambda_original"])
        if not all(math.isfinite(x) and 0 < x <= 15 for x in (lh, la)):
            raise ValueError("INVALID_ORIGINAL_GOAL_RATES")

    receipt_ids = set()
    scored = []
    for receipt in result_assessment.get("result_receipts", []):
        fid = receipt["fixture_sha256"]
        if fid in receipt_ids or fid not in by_id:
            raise ValueError("RESULT_NOT_MATCHED_TO_ONE_ISSUED_FIXTURE")
        receipt_ids.add(fid)
        pred = by_id[fid]
        home, away = receipt["result_score"]
        if (
            isinstance(home, bool) or isinstance(away, bool)
            or not isinstance(home, int) or not isinstance(away, int)
            or home < 0 or away < 0 or home > 20 or away > 20
        ):
            raise ValueError("INVALID_ACTUAL_EXACT_SCORE")
        if (
            receipt["league"] != pred["league"]
            or receipt["original_expected_calendar_date"]
            != pred["expected_calendar_date"]
            or receipt["original_model_sha256"] != pred["model_sha256_original"]
        ):
            raise ValueError("SCORE_EXTENSION_FORECAST_IDENTITY_DRIFT")
        lh = float(pred["home_lambda_original"])
        la = float(pred["away_lambda_original"])
        # Untruncated Poisson PMF has proper support over ALL nonnegative scores.
        log_probability = (
            -lh - la + home * math.log(lh) + away * math.log(la)
            - math.lgamma(home + 1) - math.lgamma(away + 1)
        )
        exact_score_probability = math.exp(log_probability)
        if home <= 10 and away <= 10:
            matrix_probability_unconditional = (
                float(pred["score_probability_matrix_conditional_0_to_10"][home][away])
                * float(pred["retained_mass_before_normalization"])
            )
            if abs(exact_score_probability - matrix_probability_unconditional) > 1e-8:
                raise ValueError("SCORE_EXTENSION_CELL_PROBABILITY_INCONSISTENT")
        top_five = pred["top5_scores"]
        actual_in_top_1 = any(
            x["home_goals"] == home and x["away_goals"] == away
            for x in top_five[:1]
        )
        actual_in_top_3 = any(
            x["home_goals"] == home and x["away_goals"] == away
            for x in top_five[:3]
        )
        actual_in_top_5 = any(
            x["home_goals"] == home and x["away_goals"] == away
            for x in top_five[:5]
        )
        scored.append({
            "fixture_sha256": fid,
            "actual_score": [home, away],
            "exact_score_probability_full_support": exact_score_probability,
            "exact_score_negative_log_probability": -log_probability,
            "actual_in_top_1": actual_in_top_1,
            "actual_in_top_3": actual_in_top_3,
            "actual_in_top_5": actual_in_top_5,
            "outside_published_0_to_10_grid": home > 10 or away > 10,
        })

    if len(scored) != result_assessment.get("finished_evaluated"):
        raise ValueError("MISMATCHED_SCORED_RESULT_COUNT")
    if len(scored) + result_assessment.get("pending_or_unverified", -1) != 63:
        raise ValueError("UNACCOUNTED_FOR_PROSPECTIVE_FIXTURES")
    scored.sort(key=lambda x: x["fixture_sha256"])
    report = {
        "schema_version": "research_prospective_exact_score_evaluation_1.0.0",
        "status": "RESEARCH_ONLY_NOT_PUBLICATION_CERTIFIED",
        "original_forecast_ledger_sha256": ORIGINAL_63_LEDGER_SHA256,
        "exact_score_extension_sha256": preissued_extension["extension_ledger_sha256"],
        "independent_results_sha256": result_assessment["report_sha256"],
        "exact_score_issued_at_utc": issued.isoformat(),
        "evaluated_at_utc": evaluated.isoformat(),
        "n_completed": len(scored),
        "n_pending": 63 - len(scored),
        "scores": scored,
        "limitations": [
            "Proper Poisson exact-score probability, not top-five-only score probability.",
            "Calibration and model skill cannot be claimed until a substantial independent future cohort finishes.",
            "The pre-match extension is separately time-stamped; its issuance time needs independent artifact history.",
            "Only independently sourced result receipts are eligible; date-only result times remain uncertain.",
            "No odds, financial performance claims or automatic champion replacement.",
        ],
    }
    if scored:
        report["mean_exact_score_negative_log_probability"] = sum(
            x["exact_score_negative_log_probability"] for x in scored
        ) / len(scored)
        report["top1_hit_rate"] = sum(x["actual_in_top_1"] for x in scored) / len(scored)
        report["top3_hit_rate"] = sum(x["actual_in_top_3"] for x in scored) / len(scored)
        report["top5_hit_rate"] = sum(x["actual_in_top_5"] for x in scored) / len(scored)
    report["report_sha256"] = _digest(report)
    return report
