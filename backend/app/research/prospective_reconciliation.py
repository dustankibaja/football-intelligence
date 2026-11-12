"""Append-only prospective shadow evaluation, separate from issued forecasts.

Inputs:
- original time-stamped research ledger from the artifact archive;
- separately verified completed results with traceable provider source receipts.

This module NEVER modifies an original forecast or treats user-provided results
as independently authenticated. Research-only; no production model promotion.
"""
from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime
from hashlib import sha256
import json
import math
from typing import Any

from app.models.evaluation import ModelEvaluator


def _digest(obj: dict[str, Any]) -> str:
    return sha256(json.dumps(obj, sort_keys=True).encode("utf-8")).hexdigest()


def _timestamp(value: str) -> datetime:
    ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        raise ValueError("TIMESTAMP_MUST_INCLUDE_UTC_OFFSET")
    return ts.astimezone(UTC)


def reconcile_prospective_results(
    original_ledger: dict[str, Any],
    verified_final_results: list[dict[str, Any]],
    *,
    evaluated_at_utc: datetime,
) -> dict[str, Any]:
    """Build a NEW assessment from original predictions and audited results.

    A result receipt is an assertion, not cryptographic proof that the named
    external provider supplied it. Independent upstream verification remains
    mandatory before results can be used for scientific publication.
    """
    if evaluated_at_utc.tzinfo is None:
        raise ValueError("UTC_AWARE_EVALUATION_TIME_REQUIRED")
    now = evaluated_at_utc.astimezone(UTC)
    original = dict(original_ledger)
    original_hash = original.pop("ledger_sha256", None)
    if not isinstance(original_hash, str) or _digest(original) != original_hash:
        raise ValueError("ORIGINAL_FORECAST_LEDGER_INTEGRITY_FAILED")
    if original.get("schema_version") != "noncommercial_prospective_shadow_1.0.0":
        raise ValueError("UNSUPPORTED_ORIGINAL_FORECAST_LEDGER")
    issued_at = _timestamp(str(original["generated_at_utc"]))
    if now <= issued_at:
        raise ValueError("EVALUATION_NOT_AFTER_FORECAST_ISSUANCE")
    if original.get("status") != "RESEARCH_SHADOW_NOT_CERTIFIED_OR_COMMERCIAL":
        raise ValueError("FORECAST_NOT_RESEARCH_ONLY")
    forecasts = original.get("predictions")
    if not isinstance(forecasts, list) or not forecasts:
        raise ValueError("NO_AUTHENTIC_FORECASTS_TO_SCORE")
    if original.get("counts", {}).get("forecasts_issued") != len(forecasts):
        raise ValueError("FORECAST_LEDGER_COUNT_MISMATCH")

    by_sha: dict[str, dict[str, Any]] = {}
    for forecast in forecasts:
        ident = forecast.get("fixture_sha256")
        if not isinstance(ident, str) or len(ident) != 64 or ident in by_sha:
            raise ValueError("DUPLICATE_OR_INVALID_ORIGINAL_FORECAST_ID")
        fixture_day = date.fromisoformat(forecast["expected_calendar_date"])
        if fixture_day <= issued_at.date():
            raise ValueError("PRE_OR_SAME_DAY_ISSUED_FORECAST")
        if forecast["status"] != "SHADOW_ONLY_UNCALIBRATED_WAITING_FOR_MATCH":
            raise ValueError("FORECAST_NOT_ORIGINALLY_PENDING")
        p = [float(forecast[k]) for k in ("prob_home", "prob_draw", "prob_away")]
        if any(not math.isfinite(x) or x < 0 or x > 1 for x in p):
            raise ValueError("INVALID_ORIGINAL_1X2_PROBABILITIES")
        if abs(sum(p) - 1) > 1e-4:
            raise ValueError("UNNORMALIZED_ORIGINAL_PROBABILITIES")
        by_sha[ident] = forecast

    if not isinstance(verified_final_results, list):
        raise ValueError("RESULTS_LIST_REQUIRED")

    scored = []
    seen_results: set[str] = set()
    for record in verified_final_results:
        ident = record.get("fixture_sha256")
        if ident not in by_sha:
            raise ValueError("UNMATCHED_RESULT_OR_FORBIDDEN_RETROACTIVE_FORECAST")
        if ident in seen_results:
            raise ValueError("DUPLICATE_RESULT_FOR_FORECAST")
        seen_results.add(ident)
        forecast = by_sha[ident]
        if record.get("league") != forecast["league"] or (
            record.get("home") != forecast["provider_home"]
            or record.get("away") != forecast["provider_away"]
        ):
            raise ValueError("RESULT_TEAM_OR_COMPETITION_IDENTITY_MISMATCH")
        if record.get("status") != "FINISHED":
            raise ValueError("NONFINAL_RESULT_CANNOT_BE_EVALUATED")
        home, away = record.get("home_goals"), record.get("away_goals")
        if (
            isinstance(home, bool) or isinstance(away, bool)
            or not isinstance(home, int) or not isinstance(away, int)
            or not (0 <= home <= 20 and 0 <= away <= 20)
        ):
            raise ValueError("INVALID_AUTHENTIC_SCORE")
        retrieved_at = _timestamp(str(record["retrieved_at_utc"]))
        precision = record.get("result_time_precision", "EXACT_TIMESTAMP")
        expected_date = date.fromisoformat(forecast["expected_calendar_date"])
        if precision == "DATE_ONLY":
            # Football-Data's result CSV does not certify completion time.
            # Conservatively score only on a later UTC calendar day; never
            # manufacture or imply an exact UTC final whistle.
            finished_date = date.fromisoformat(str(record["final_result_date"]))
            if (
                finished_date < expected_date
                or finished_date <= issued_at.date()
                or retrieved_at.date() <= finished_date
                or retrieved_at > now
            ):
                raise ValueError("RESULT_TIMELINE_IS_NOT_PROSPECTIVE")
            completion_receipt = {
                "result_time_precision": "DATE_ONLY",
                "final_result_date": finished_date.isoformat(),
            }
        elif precision == "EXACT_TIMESTAMP":
            completed_at = _timestamp(str(record["completed_at_utc"]))
            if (
                completed_at <= issued_at
                or completed_at.date() < expected_date
                or retrieved_at < completed_at
                or retrieved_at > now
            ):
                raise ValueError("RESULT_TIMELINE_IS_NOT_PROSPECTIVE")
            completion_receipt = {
                "result_time_precision": "EXACT_TIMESTAMP",
                "completed_at_utc": completed_at.isoformat(),
            }
        else:
            raise ValueError("UNSUPPORTED_RESULT_TIME_PRECISION")
        source = record.get("source_url")
        raw_sha = record.get("source_payload_sha256")
        if (
            not isinstance(source, str) or not source.startswith("https://")
            or not isinstance(raw_sha, str) or len(raw_sha) != 64
            or any(x not in "0123456789abcdef" for x in raw_sha)
        ):
            raise ValueError("RESULT_SOURCE_PROVENANCE_MISSING")
        outcome = 0 if home > away else 1 if home == away else 2
        selected = [
            float(forecast["prob_home"]),
            float(forecast["prob_draw"]),
            float(forecast["prob_away"]),
        ]
        scored.append({
            "fixture_sha256": ident,
            "league": record["league"],
            "result_score": [home, away],
            "result_source_url": source,
            "result_source_sha256": raw_sha,
            "result_source_collection_mode": record.get("source_collection_mode", "ORIGIN_UNVERIFIED"),
            "retrieved_at_utc": retrieved_at.isoformat(),
            **completion_receipt,
            "actual_1x2": outcome,
            "original_probabilities_1x2": selected,
            "original_expected_calendar_date": forecast["expected_calendar_date"],
            "original_model_sha256": forecast["model_artifact_sha256"],
            "original_feature_sha256": forecast["feature_snapshot_sha256"],
            "individual_log_loss": -math.log(max(selected[outcome], 1e-15)),
            "individual_brier": sum(
                (selected[k] - (1 if k == outcome else 0)) ** 2
                for k in range(3)
            ),
            "actual_btts": bool(home and away),
            "actual_over_2_5": home + away >= 3,
            "original_btts_probability": float(forecast["prob_btts_yes"]),
            "original_over_2_5_probability": float(forecast["prob_over_2_5"]),
        })

    scored.sort(key=lambda row: row["fixture_sha256"])
    missing = sorted(set(by_sha) - seen_results)
    report: dict[str, Any] = {
        "schema_version": "research_prospective_result_reconciliation_1.0.0",
        "status": "RESEARCH_ONLY_RESULTS_NEED_INDEPENDENT_SOURCE_VERIFICATION",
        "evaluated_at_utc": now.isoformat(),
        "original_issued_at_utc": issued_at.isoformat(),
        "original_forecast_ledger_sha256": original_hash,
        "original_forecasts": len(forecasts),
        "finished_evaluated": len(scored),
        "pending_or_unverified": len(missing),
        "pending_fixture_sha256s": missing,
        "result_receipts": scored,
        "scientific_limitations": [
            "Result source URLs and hashes have structural validation only; an external authentication step is required.",
            "Date-only upstream fixture times cannot independently establish kickoff timing.",
            "Results are never used to refit or update the original model within this evaluation.",
            "A small or incomplete prospective cohort cannot establish reliable calibration.",
            "No production, commercial or betting-performance claim is permitted.",
        ],
    }
    if scored:
        labels = [row["actual_1x2"] for row in scored]
        probabilities = [row["original_probabilities_1x2"] for row in scored]
        report["overall_1x2"] = ModelEvaluator.compute_metric_summary(
            labels, probabilities
        ).model_dump()
        report["binary_brier"] = {
            "btts": sum(
                (row["original_btts_probability"] - float(row["actual_btts"])) ** 2
                for row in scored
            ) / len(scored),
            "over_2_5": sum(
                (row["original_over_2_5_probability"] - float(row["actual_over_2_5"])) ** 2
                for row in scored
            ) / len(scored),
        }
        report["per_league_counts"] = dict(Counter(x["league"] for x in scored))
    report["report_sha256"] = _digest(report)
    return report
