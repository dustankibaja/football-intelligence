"""Append-only prospective outcome verification against an earlier shadow ledger.

This module never updates a forecast. Only a later, separately hashed report is
created after the original forecast's match calendar day has fully elapsed.
Noncommercial research only; provider results are independently source-pinned.
"""
from __future__ import annotations

import argparse
import csv
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
import io
import json
import math
from pathlib import Path

from app.models.evaluation import ModelEvaluator
from app.research.prospective_shadow import (
    FOLDERS,
    UPSTREAM_BASE,
    UPSTREAM_COMMIT_API,
    UPSTREAM_REPOSITORY,
    _get_bytes,
)


def verify_original_forecast_ledger(document: dict) -> str:
    """Check structure, original SHA256, pre-date rule, and forecast IDs."""
    expected = document.get("ledger_sha256")
    if not isinstance(expected, str) or len(expected) != 64:
        raise ValueError("FORECAST_LEDGER_MISSING_SHA256")
    without_checksum = dict(document)
    without_checksum.pop("ledger_sha256")
    actual = sha256(json.dumps(without_checksum, sort_keys=True).encode()).hexdigest()
    if expected != actual:
        raise ValueError("PREMATCH_FORECAST_LEDGER_TAMPERED")
    if document.get("status") != "RESEARCH_SHADOW_NOT_CERTIFIED_OR_COMMERCIAL":
        raise ValueError("NOT_PROSPECTIVE_RESEARCH_SHADOW")
    if document.get("fixture_source_repo") != UPSTREAM_REPOSITORY:
        raise ValueError("UNEXPECTED_FIXTURE_SOURCE")
    if not isinstance(document.get("predictions"), list) or not document["predictions"]:
        raise ValueError("EMPTY_FORECAST_LEDGER")

    generated = datetime.fromisoformat(document["generated_at_utc"])
    if generated.tzinfo is None:
        raise ValueError("PREMATCH_ISSUANCE_TIME_NOT_UTC_AWARE")
    generated = generated.astimezone(UTC)
    seen = set()
    for forecast in document["predictions"]:
        match_date = date.fromisoformat(forecast["expected_calendar_date"])
        if generated.date() >= match_date:
            raise ValueError("FORECAST_NOT_ISSUED_BEFORE_MATCH_DATE")
        identity = (
            f"{forecast['league']}|2627|{match_date}|"
            f"{forecast['provider_home']}|{forecast['provider_away']}"
        )
        if sha256(identity.encode()).hexdigest() != forecast["fixture_sha256"]:
            raise ValueError("FORECAST_FIXTURE_IDENTITY_CHANGED")
        if forecast["fixture_sha256"] in seen:
            raise ValueError("DUPLICATED_FORECAST_FIXTURE")
        seen.add(forecast["fixture_sha256"])
        p = [float(forecast[key]) for key in ("prob_home", "prob_draw", "prob_away")]
        if not all(math.isfinite(v) and 0 <= v <= 1 for v in p) or abs(sum(p) - 1) > 1e-4:
            raise ValueError("UNTRUSTWORTHY_SAVED_PROBABILITIES")
    return actual


def parse_completed_result_csv(raw: bytes, league: str) -> dict[tuple, dict]:
    """Validate reported FULL-TIME results; do not infer from partial score."""
    if not raw or len(raw) > 2_000_000 or raw[:200].lstrip().lower().startswith(b"<html"):
        raise ValueError("INVALID_OFFICIAL_RESULT_PAYLOAD")
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig", errors="replace")))
    required = {"date", "home", "away", "ft_home", "ft_away", "ft_result"}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise ValueError("RESULT_FILE_HEADER_INCOMPLETE")
    results = {}
    for row_num, item in enumerate(reader, start=2):
        match_date = date.fromisoformat(str(item["date"]).strip())
        home = str(item["home"]).strip()
        away = str(item["away"]).strip()
        if not home or not away or home == away:
            raise ValueError(f"INVALID_RESULT_PAIR_AT_ROW_{row_num}")
        home_goals = int(str(item["ft_home"]).strip())
        away_goals = int(str(item["ft_away"]).strip())
        if not (0 <= home_goals <= 30 and 0 <= away_goals <= 30):
            raise ValueError(f"UNREASONABLE_RESULT_SCORE_AT_ROW_{row_num}")
        outcome = 0 if home_goals > away_goals else 1 if home_goals == away_goals else 2
        if str(item["ft_result"]).strip().upper() != ("H", "D", "A")[outcome]:
            raise ValueError(f"RESULT_SCORE_MISMATCH_AT_ROW_{row_num}")
        key = (league, match_date, home, away)
        if key in results:
            raise ValueError("DUPLICATE_COMPLETED_RESULT_IDENTITY")
        results[key] = {
            "home_goals": home_goals,
            "away_goals": away_goals,
            "outcome": outcome,
            "result_source": str(item.get("source") or ""),
        }
    return results


def download_pinned_results(as_of: datetime) -> tuple[str, dict[tuple, dict], list[dict]]:
    """Load completed scorelines from one pinned, recently generated source."""
    data = json.loads(_get_bytes(UPSTREAM_COMMIT_API).decode("utf-8"))
    commit = str(data.get("sha", ""))
    if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
        raise ValueError("UNTRUSTED_RESULT_SOURCE_COMMIT")
    summary_url = f"{UPSTREAM_BASE}/{commit}/build_summary.json"
    summary_raw = _get_bytes(summary_url)
    summary = json.loads(summary_raw.decode("utf-8"))
    generated = datetime.fromisoformat(summary["generated_utc"].replace("Z", "+00:00"))
    if not timedelta(0) <= as_of - generated <= timedelta(hours=72):
        raise ValueError("STALE_RESULT_PROVIDER_SNAPSHOT")
    if summary.get("season") != "2627":
        raise ValueError("INCORRECT_CURRENT_RESULT_SEASON")
    league_entries = {
        item["folder"]: item for item in summary["leagues"]
    }
    receipts = [{
        "source": summary_url, "sha256": sha256(summary_raw).hexdigest(),
        "type": "result_summary",
    }]
    combined: dict[tuple, dict] = {}
    for league, folder in FOLDERS.items():
        entry = league_entries.get(f"data/{folder}")
        if entry is None or entry.get("errors"):
            raise ValueError("INCOMPLETE_RESULT_PROVIDER_COVERAGE")
        url = f"{UPSTREAM_BASE}/{commit}/data/{folder}/results_2627.csv"
        raw = _get_bytes(url)
        parsed = parse_completed_result_csv(raw, league)
        if len(parsed) != int(entry.get("results", -1)):
            raise ValueError(f"RESULT_SOURCE_SUMMARY_COUNT_MISMATCH_{league}")
        for identity, result in parsed.items():
            if identity in combined:
                raise ValueError("DUPLICATE_CROSS_LEAGUE_RESULT")
            combined[identity] = result
        receipts.append({
            "source": url,
            "league": league,
            "sha256": sha256(raw).hexdigest(),
            "completed_results": len(parsed),
        })
    return commit, combined, receipts


def _poisson_score_probability(home_goals: int, away_goals: int, lh: float, la: float) -> float:
    if lh <= 0 or la <= 0 or not all(math.isfinite(x) for x in (lh, la)):
        raise ValueError("INVALID_EXPECTED_GOALS")
    return float(math.exp(
        -(lh + la)
        + home_goals * math.log(lh) - math.lgamma(home_goals + 1)
        + away_goals * math.log(la) - math.lgamma(away_goals + 1)
    ))


def reconcile_prospective_results(
    forecast_ledger: dict,
    provider_commit: str,
    official_completed_results: dict[tuple, dict],
    result_source_receipts: list[dict],
    *,
    as_of: datetime,
) -> dict:
    """Score only final results AFTER the forecast match date passed.

    The original ledger is not mutated. Unmatched fixtures are pending/review,
    never invented or silently treated as draws.
    """
    original_checksum = verify_original_forecast_ledger(forecast_ledger)
    if as_of.tzinfo is None:
        raise ValueError("RESULT_AS_OF_MUST_BE_TZ_AWARE")
    as_of = as_of.astimezone(UTC)
    issued = datetime.fromisoformat(forecast_ledger["generated_at_utc"]).astimezone(UTC)
    if as_of <= issued:
        raise ValueError("RESULT_REPORT_BEFORE_FORECAST_ISSUANCE")
    if len(provider_commit) != 40:
        raise ValueError("RESULT_SOURCE_COMMIT_REQUIRED")
    rows = []
    y = []
    probability_triples = []
    market_sums = {
        "over_2_5_binary_brier": 0.0,
        "btts_binary_brier": 0.0,
        "exact_score_log_loss_sum": 0.0,
    }
    previous_date = issued.date()
    pair_index: dict[tuple, list[date]] = {}
    for league, match_date, home, away in official_completed_results:
        pair_index.setdefault((league, home, away), []).append(match_date)

    for forecast in forecast_ledger["predictions"]:
        expected_date = date.fromisoformat(forecast["expected_calendar_date"])
        identity = (
            forecast["league"], expected_date,
            forecast["provider_home"], forecast["provider_away"],
        )
        item = {
            "fixture_sha256": forecast["fixture_sha256"],
            "forecast_issued_at_utc": forecast_ledger["generated_at_utc"],
            "scheduled_date": forecast["expected_calendar_date"],
            "league": forecast["league"],
            "source_forecast_immutable_sha256": original_checksum,
        }
        if expected_date > as_of.date() or expected_date == as_of.date():
            item["evaluation_status"] = "PENDING_MATCH_DATE_NOT_FULLY_ELAPSED"
        elif expected_date <= previous_date:
            raise ValueError("IMPROPER_POST_HOC_FORECAST")
        elif identity in official_completed_results:
            result = official_completed_results[identity]
            outcome = result["outcome"]
            probs = [
                float(forecast["prob_home"]),
                float(forecast["prob_draw"]),
                float(forecast["prob_away"]),
            ]
            lh = float(forecast["lambda_home"])
            la = float(forecast["lambda_away"])
            hg = int(result["home_goals"])
            ag = int(result["away_goals"])
            score_probability = _poisson_score_probability(hg, ag, lh, la)
            over_outcome = float(hg + ag > 2)
            btts_outcome = float(hg > 0 and ag > 0)
            over_p = float(forecast["prob_over_2_5"])
            btts_p = float(forecast["prob_btts_yes"])
            if any(not 0 <= p <= 1 for p in (over_p, btts_p)):
                raise ValueError("INVALID_SAVED_MARKET_PROBABILITY")
            market_sums["over_2_5_binary_brier"] += (over_p - over_outcome) ** 2
            market_sums["btts_binary_brier"] += (btts_p - btts_outcome) ** 2
            market_sums["exact_score_log_loss_sum"] -= math.log(max(score_probability, 1e-15))
            y.append(outcome)
            probability_triples.append(probs)
            item.update({
                "evaluation_status": "EVALUATED_AFTER_FINAL_RESULT",
                "actual_home_goals": hg,
                "actual_away_goals": ag,
                "actual_1x2": outcome,
                "actual_score_probability": score_probability,
                "observed_match_result_source": result.get("result_source", ""),
            })
        else:
            changed_dates = [
                match_date for match_date in pair_index.get(
                    (forecast["league"], forecast["provider_home"], forecast["provider_away"]), []
                ) if match_date != expected_date
            ]
            if changed_dates:
                item["evaluation_status"] = "REVIEW_POSSIBLE_RESCHEDULE_OR_SOURCE_CORRECTION"
                item["new_result_date_candidates"] = [
                    date_value.isoformat() for date_value in sorted(changed_dates)
                ]
            else:
                item["evaluation_status"] = "PENDING_OFFICIAL_FINAL_RESULT_OR_POSTPONED"
        rows.append(item)

    n = len(y)
    if n:
        metrics = ModelEvaluator.compute_metric_summary(y, probability_triples).model_dump()
        markets = {key: value / n for key, value in market_sums.items()}
    else:
        metrics = None
        markets = None
    counts = {}
    for row in rows:
        status = row["evaluation_status"]
        counts[status] = counts.get(status, 0) + 1
    if sum(counts.values()) != len(forecast_ledger["predictions"]):
        raise ValueError("RECONCILIATION_COUNT_LOSS")

    report = {
        "schema_version": "prospective_result_reconciliation_1.0.0",
        "status": "PROSPECTIVE_RESEARCH_AUDIT_NOT_PRODUCTION_CERTIFICATION",
        "reconciled_at_utc": as_of.isoformat(),
        "source_forecast_ledger_sha256": original_checksum,
        "source_forecast_issued_at_utc": issued.isoformat(),
        "forecast_source_upstream_commit": forecast_ledger["fixture_source_commit"],
        "result_source_upstream_commit": provider_commit,
        "result_source_receipts": result_source_receipts,
        "evaluation_counts": counts,
        "evaluated_count": n,
        "forecast_count": len(forecast_ledger["predictions"]),
        "observed_score_metrics": metrics,
        "observed_goal_market_metrics": markets,
        "minimum_recommended_1x2_calibration_sample": 100,
        "calibration_sample_sufficient": n >= 100,
        "evaluation_rows": rows,
        "limitations": [
            "Open-matchday is independent noncommercial provider, not official certification.",
            "Result date, names and teams matched exactly to original provider fixture identity.",
            "A possible schedule change is quarantined, never silently rekeyed.",
            "Same-day final results are excluded because kickoff timezone is not certified.",
            "No original forecast probabilities, scorelines or model parameters are changed.",
            "At least 100 evaluated games are required before drawing even preliminary calibration inferences.",
        ],
    }
    report["report_sha256"] = sha256(
        json.dumps(report, sort_keys=True).encode()
    ).hexdigest()
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--forecast-ledger", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    source = json.loads(Path(args.forecast_ledger).read_text(encoding="utf-8"))
    as_of = datetime.now(UTC)
    commit, completed, receipts = download_pinned_results(as_of)
    result = reconcile_prospective_results(
        source, commit, completed, receipts, as_of=as_of,
    )
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": result["status"],
        "original_forecast_ledger_sha256": result["source_forecast_ledger_sha256"],
        "evaluated": result["evaluated_count"],
        "forecast_count": result["forecast_count"],
        "status_counts": result["evaluation_counts"],
        "sample_calibration_sufficient": result["calibration_sample_sufficient"],
        "report_sha256": result["report_sha256"],
    }, indent=2))


if __name__ == "__main__":
    main()
