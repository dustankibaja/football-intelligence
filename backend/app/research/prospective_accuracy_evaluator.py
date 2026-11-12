"""Score paired pre-match 1X2 forecasts against independently retrieved final scores.

The original challenger ledger is immutable. This module performs no model
fitting and never modifies any issued forecasts. All public claims remain
blocked until independent review of the source receipts and sufficient sample.
"""
from __future__ import annotations

import argparse
from datetime import UTC, date, datetime
from hashlib import sha256
import json
from pathlib import Path

import numpy as np

from app.research.football_data_result_verifier import (
    SOURCE_BASE,
    collect_result_receipts,
    local_result_reader,
)
from app.research.prospective_accuracy_challenger import _digest

# Fixed research policy identified on prior historical folds, not re-tuned on
# these 71 pre-match fixtures or any of their eventual results.
# Conditional accuracy NEVER replaces the full-coverage accuracy denominator.
RESEARCH_CONFIDENCE_CUTOFF = 0.50


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("PAIRED_RECEIPT_REQUIRES_AWARE_TIMESTAMP")
    return parsed.astimezone(UTC)


def _validate_ledger(paired: dict) -> tuple[datetime, dict]:
    unsigned = dict(paired)
    recorded_hash = unsigned.pop("ledger_sha256", None)
    if not isinstance(recorded_hash, str) or _digest(unsigned) != recorded_hash:
        raise ValueError("PAIRED_FORECAST_LEDGER_TAMPERED")
    if paired.get("schema_version") != "independent_prospective_elo_challenger_1.0.0":
        raise ValueError("PAIRED_FORECAST_SCHEMA_UNSUPPORTED")
    if paired.get("status") != "RESEARCH_ONLY_AWAITING_AUTHENTIC_POSTMATCH_RESULTS":
        raise ValueError("PAIRED_FORECAST_NOT_PROSPECTIVELY_ISSUED")
    if paired.get("model", {}).get("publication_eligible") is not False:
        raise ValueError("PAIRED_FORECAST_PUBLICATION_NOT_AUTHORIZED")
    issued_at = _timestamp(paired["generated_at_utc"])
    predictions = paired.get("predictions")
    if not isinstance(predictions, list) or not predictions:
        raise ValueError("PAIRED_FORECASTS_EMPTY")
    counts = paired["counts"]
    if counts["challenger_issued"] != len(predictions) or counts["baseline_future_qualified"] != len(predictions):
        raise ValueError("PAIRED_FORECAST_FULL_COVERAGE_MISMATCH")
    identities = {}
    for entry in predictions:
        fixture_id = entry["fixture_sha256"]
        if (not isinstance(fixture_id, str) or len(fixture_id) != 64
                or fixture_id in identities):
            raise ValueError("PAIRED_FIXTURE_ID_DUPLICATED")
        if date.fromisoformat(entry["expected_calendar_date"]) <= issued_at.date():
            raise ValueError("PAIRED_ISSUED_AFTER_FIXTURE_DATE")
        league, home, away = entry["league"], entry["provider_home"], entry["provider_away"]
        if league not in ("E0", "SP1", "I1", "D1") or not home or not away or home == away:
            raise ValueError("PAIRED_INVALID_FIXTURE_IDENTITY")
        recomputed = sha256(
            f"{league}|2627|{entry['expected_calendar_date']}|{home}|{away}".encode()
        ).hexdigest()
        if recomputed != fixture_id:
            raise ValueError("PAIRED_FIXTURE_HASH_MISMATCH")
        for model in ("baseline", "candidate"):
            vals = np.asarray([entry[f"{model}_prob_{term}"] for term in ("home", "draw", "away")], dtype=float)
            if (not np.isfinite(vals).all() or (vals < 0).any()
                    or (vals > 1).any() or abs(vals.sum() - 1) > 1e-4):
                raise ValueError("PAIRED_INVALID_1X2_PROBABILITY")
        if entry["status"] != "PAIRED_PREMATCH_SHADOW_UNSCORED":
            raise ValueError("PAIRED_FORECAST_ALREADY_SCORED")
        identities[fixture_id] = entry
    return issued_at, identities


def collect_paired_result_receipts(paired: dict, *, retrieved_at_utc: datetime, fetch_bytes=None) -> list[dict]:
    """Read authentic current 2026/27 CSV results; never infer scores or timestamps."""
    _validate_ledger(paired)
    minimal = {"predictions": [{
        "fixture_sha256": entry["fixture_sha256"],
        "league": entry["league"],
        "expected_calendar_date": entry["expected_calendar_date"],
        "provider_home": entry["provider_home"],
        "provider_away": entry["provider_away"],
    } for entry in paired["predictions"]]}
    return collect_result_receipts(
        minimal, retrieved_at_utc=retrieved_at_utc, fetch_bytes=fetch_bytes,
    )


def evaluate_paired_results(paired: dict, receipts: list[dict], *, evaluated_at_utc: datetime) -> dict:
    if evaluated_at_utc.tzinfo is None:
        raise ValueError("PAIRED_EVALUATION_REQUIRES_UTC_OFFSET")
    now = evaluated_at_utc.astimezone(UTC)
    issued_at, by_id = _validate_ledger(paired)
    if now <= issued_at:
        raise ValueError("PAIRED_EVALUATION_NOT_AFTER_ISSUANCE")
    if not isinstance(receipts, list):
        raise ValueError("PAIRED_RESULTS_RECEIPTS_LIST_REQUIRED")

    scored, seen = [], set()
    for receipt in receipts:
        ident = receipt["fixture_sha256"]
        if ident not in by_id or ident in seen:
            raise ValueError("PAIRED_RESULT_ID_UNMATCHED_OR_DUPLICATED")
        seen.add(ident)
        prediction = by_id[ident]
        if (receipt.get("league") != prediction["league"]
                or receipt.get("home") != prediction["provider_home"]
                or receipt.get("away") != prediction["provider_away"]
                or receipt.get("status") != "FINISHED"
                or receipt.get("result_time_precision") != "DATE_ONLY"):
            raise ValueError("PAIRED_RESULT_NOT_INDEPENDENT_FINISHED_FIXTURE")
        h, a = receipt.get("home_goals"), receipt.get("away_goals")
        if (isinstance(h, bool) or isinstance(a, bool)
                or not isinstance(h, int) or not isinstance(a, int)
                or not 0 <= h <= 20 or not 0 <= a <= 20):
            raise ValueError("PAIRED_RESULT_INVALID_SCORE")
        result_day = date.fromisoformat(receipt["final_result_date"])
        received = _timestamp(receipt["retrieved_at_utc"])
        if (result_day < date.fromisoformat(prediction["expected_calendar_date"])
                or result_day <= issued_at.date()
                or result_day >= received.date()
                or received > now):
            raise ValueError("PAIRED_RESULT_TIME_NOT_PROSPECTIVE")
        source = f"{SOURCE_BASE}/{prediction['league']}.csv"
        raw_hash = receipt.get("source_payload_sha256")
        if (receipt.get("source_url") != source
                or not isinstance(raw_hash, str) or len(raw_hash) != 64
                or any(c not in "0123456789abcdef" for c in raw_hash)):
            raise ValueError("PAIRED_RESULT_NOT_AUTHENTIC_SOURCE_RECEIPT")
        outcome = 0 if h > a else 1 if h == a else 2
        entries = {}
        for model in ("baseline", "candidate"):
            p = np.asarray([prediction[f"{model}_prob_{term}"] for term in ("home", "draw", "away")], dtype=float)
            entries[model] = {
                "probabilities": p.tolist(),
                "predicted_1x2": int(np.argmax(p)),
                "correct": bool(np.argmax(p) == outcome),
                "log_loss": -float(np.log(max(p[outcome], 1e-15))),
                "multiclass_brier": float(np.sum((p - np.eye(3)[outcome]) ** 2)),
            }
        scored.append({
            "fixture_sha256": ident,
            "league": prediction["league"],
            "expected_calendar_date": prediction["expected_calendar_date"],
            "result_date": result_day.isoformat(),
            "score": [h, a],
            "actual_1x2": outcome,
            "receipt": {
                "source_url": source,
                "source_payload_sha256": raw_hash,
                "source_collection_mode": receipt.get("source_collection_mode", "ORIGIN_UNVERIFIED"),
                "retrieved_at_utc": received.isoformat(),
                "result_time_precision": "DATE_ONLY",
            },
            **entries,
        })
    scored.sort(key=lambda item: item["fixture_sha256"])
    pending = sorted(set(by_id) - seen)
    # A reproducible selection is determined using only immutable pre-match
    # probabilities. Keep abstentions and still-pending results in the audit.
    selected_ids = {
        ident for ident, prediction in by_id.items()
        if max(float(prediction[f"candidate_prob_{label}"])
               for label in ("home", "draw", "away")) >= RESEARCH_CONFIDENCE_CUTOFF
    }
    selected_scored = [row for row in scored if row["fixture_sha256"] in selected_ids]
    selected_correct = len(selected_scored)
    confidence_gate = {
        "status": "RESEARCH_ONLY_NOT_LIVE_THRESHOLD_CERTIFIED",
        "threshold_min_top_class_probability": RESEARCH_CONFIDENCE_CUTOFF,
        "policy_pre_match_only": True,
        "retrospectively_motivated_not_prospectively_validated": True,
        "total_original_fixtures": len(by_id),
        "pre_match_selected": len(selected_ids),
        "pre_match_abstained": len(by_id) - len(selected_ids),
        "issued_cohort_coverage": len(selected_ids) / len(by_id),
        "selected_fixture_ids_sha256": sha256(
            json.dumps(sorted(selected_ids), separators=(",", ":")).encode()
        ).hexdigest(),
        "selected_scored_so_far": selected_correct,
        "selected_pending_or_unverified": len(selected_ids - seen),
        "abstained_scored_so_far": len(scored) - selected_correct,
        "selected_accuracy": None,
        "same_selected_fixtures_baseline_accuracy": None,
        "selected_accuracy_difference": None,
        "selected_mean_log_loss": None,
        "same_selected_fixtures_baseline_mean_log_loss": None,
        "scored_result_cohort_coverage": (selected_correct / len(scored)) if scored else None,
    }
    if selected_scored:
        confidence_gate["selected_accuracy"] = sum(
            int(row["candidate"]["correct"]) for row in selected_scored
        ) / selected_correct
        confidence_gate["same_selected_fixtures_baseline_accuracy"] = sum(
            int(row["baseline"]["correct"]) for row in selected_scored
        ) / selected_correct
        confidence_gate["selected_accuracy_difference"] = (
            confidence_gate["selected_accuracy"]
            - confidence_gate["same_selected_fixtures_baseline_accuracy"]
        )
        confidence_gate["selected_mean_log_loss"] = sum(
            row["candidate"]["log_loss"] for row in selected_scored
        ) / selected_correct
        confidence_gate["same_selected_fixtures_baseline_mean_log_loss"] = sum(
            row["baseline"]["log_loss"] for row in selected_scored
        ) / selected_correct

    summary = {
        "schema_version": "independent_paired_prospective_1x2_assessment_1.0.0",
        "status": "RESEARCH_ONLY_PENDING_SOURCE_INDEPENDENCE_AND_SAMPLE_VALIDATION",
        "evaluated_at_utc": now.isoformat(),
        "issued_at_utc": issued_at.isoformat(),
        "original_paired_ledger_sha256": paired["ledger_sha256"],
        "total_original_paired": len(by_id),
        "finished_evaluated": len(scored),
        "pending_or_unverified": len(pending),
        "pending_fixture_sha256s": pending,
        "matched_receipts": scored,
        "confidence_selection_research": confidence_gate,
        "limitations": [
            "Not the certified canonical 251-feature C1 champion.",
            "Provider source URL/hash receipts must be authenticated independently of evaluation code.",
            "Date-only scores are included no earlier than the following UTC calendar day.",
            "Pending, rescheduled, ambiguous, and unavailable fixtures are never silently scored.",
            "No production/publication or betting claim; no model is refitted here.",
            "Prospective accuracy needs sufficiently many independent completed matches.",
            "Selective confidence accuracy is CONDITIONAL on abstentions; never relabel as all-fixture accuracy.",
            "The 0.50 cutoff came from earlier historical inspection and requires prospective certification.",
        ],
    }
    if scored:
        n = len(scored)
        for model in ("baseline", "candidate"):
            correct = sum(int(row[model]["correct"]) for row in scored)
            summary[model] = {
                "n": n,
                "correct": correct,
                "full_scored_cohort_accuracy": correct / n,
                "mean_log_loss": sum(row[model]["log_loss"] for row in scored) / n,
                "mean_multiclass_brier": sum(row[model]["multiclass_brier"] for row in scored) / n,
            }
        summary["paired_difference_candidate_minus_baseline"] = {
            "accuracy_points": summary["candidate"]["full_scored_cohort_accuracy"]
                - summary["baseline"]["full_scored_cohort_accuracy"],
            "mean_log_loss_skill": summary["baseline"]["mean_log_loss"]
                - summary["candidate"]["mean_log_loss"],
            "paired_fixture_count": n,
        }
    summary["report_sha256"] = _digest(summary)
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--paired-ledger", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--results-dir", type=Path, help="Use approved local source CSV snapshots instead of network downloads.")
    args = parser.parse_args()
    original_path = Path(args.paired_ledger)
    out_path = Path(args.output)
    if original_path.resolve() == out_path.resolve():
        raise ValueError("PAIRED_IMMUTABLE_LEDGER_CANNOT_BE_OVERWRITTEN")
    paired = json.loads(original_path.read_text(encoding="utf-8"))
    retrieved = datetime.now(UTC)
    receipts = collect_paired_result_receipts(
        paired, retrieved_at_utc=retrieved,
        fetch_bytes=local_result_reader(args.results_dir) if args.results_dir else None,
    )
    report = evaluate_paired_results(paired, receipts, evaluated_at_utc=datetime.now(UTC))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"], "scored": report["finished_evaluated"],
        "pending": report["pending_or_unverified"],
        "paired_delta": report.get("paired_difference_candidate_minus_baseline"),
        "research_confidence_selection": report["confidence_selection_research"],
        "original_ledger_sha256": report["original_paired_ledger_sha256"],
        "report_sha256": report["report_sha256"],
        "output": str(out_path),
    }, indent=2))


if __name__ == "__main__":
    main()
