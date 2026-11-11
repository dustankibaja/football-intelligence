"""Issue a NEW pre-match 1X2 research challenger beside an unmodified M1 ledger.

This is an independently timestamped, noncommercial shadow experiment. It
never edits the original 63 forecasts, canonical C1/M1 weights, or live tables.
Prospective evidence is valid only if both candidates were issued before the
fixture date and final scores are verified separately afterwards.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path

import numpy as np

from app.research.historical_oos import (
    Fixture,
    _model,
    _probabilities,
    build_pre_match_features,
    download_completed_seasons,
)
from app.research.multiseason_stability import _causal_elo_features
from app.research.prospective_shadow import (
    _exact_alias,
    _source_snapshot,
    prospective_shadow,
)


def _digest(payload: dict) -> str:
    return sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode("utf-8")).hexdigest()


def issue_accuracy_challenger(
    history: list[Fixture],
    historical_receipts: list[dict],
    baseline: dict,
    *,
    as_of: datetime,
) -> dict:
    """Pair all qualified M1 future fixtures with fresh 24-feature Elo-L2 forecasts.

    No outcome from the future fixture or an unplayed same-day fixture enters
    training, causal form, or the fixed Elo state. No selective omission of any
    qualified baseline fixture is permitted.
    """
    if as_of.tzinfo is None:
        raise ValueError("CHALLENGER_REQUIRES_UTC_AWARE_ISSUE_TIMESTAMP")
    as_of = as_of.astimezone(UTC)
    if baseline.get("schema_version") != "noncommercial_prospective_shadow_1.0.0":
        raise ValueError("UNSUPPORTED_PREMATCH_BASELINE_SCHEMA")
    if baseline.get("status") != "RESEARCH_SHADOW_NOT_CERTIFIED_OR_COMMERCIAL":
        raise ValueError("BASELINE_NOT_QUALIFIED_RESEARCH_LEDGER")
    original = dict(baseline)
    original_digest = original.pop("ledger_sha256", None)
    if original_digest != _digest(original):
        raise ValueError("TAMPERED_BASELINE_LEDGER_SHA256")
    if datetime.fromisoformat(baseline["generated_at_utc"]).astimezone(UTC) != as_of:
        raise ValueError("BASELINE_AND_CHALLENGER_ISSUE_TIMES_DIFFER")
    if baseline.get("historical_source_receipts") != historical_receipts:
        raise ValueError("CHALLENGER_HISTORICAL_SOURCE_MISMATCH")
    if len(history) != baseline["model"]["training_rows"]:
        raise ValueError("CHALLENGER_TRAINING_COVERAGE_MISMATCH")
    if not history or not historical_receipts or max(f.match_date for f in history) >= as_of.date():
        raise ValueError("CHALLENGER_HISTORY_NOT_STRICTLY_HISTORICAL")
    if baseline["model"]["publication_eligible"] is not False:
        raise ValueError("BASELINE_SHOULD_NOT_BE_PUBLICATION_ELIGIBLE")
    qualified = baseline.get("predictions")
    if not isinstance(qualified, list) or not qualified:
        raise ValueError("CHALLENGER_REQUIRES_PREISSUED_BASELINE_FIXTURES")

    teams = {league: set() for league in ("E0", "SP1", "I1", "D1")}
    for match in history:
        if match.league not in teams:
            raise ValueError("UNREGISTERED_RESEARCH_LEAGUE")
        teams[match.league].update((match.home, match.away))
    future = []
    seen = set()
    for entry in qualified:
        league = entry["league"]
        if league not in teams:
            raise ValueError("UNREGISTERED_FUTURE_RESEARCH_LEAGUE")
        d = datetime.fromisoformat(entry["expected_calendar_date"]).date()
        if d <= as_of.date():
            raise ValueError("CHALLENGER_POST_FIXTURE_DATE_ISSUE_FORBIDDEN")
        home = entry["provider_home"]
        away = entry["provider_away"]
        fixture_token = f"{league}|2627|{d.isoformat()}|{home}|{away}"
        fixture_id = sha256(fixture_token.encode("utf-8")).hexdigest()
        if fixture_id != entry["fixture_sha256"] or fixture_id in seen:
            raise ValueError("CHALLENGER_FIXTURE_IDENTITY_OR_DUPLICATE")
        seen.add(fixture_id)
        a = _exact_alias(league, home, teams)
        b = _exact_alias(league, away, teams)
        if a is None or b is None or a == b:
            raise ValueError("CHALLENGER_TEAM_MAPPING_NOT_EXACT")
        p = [entry["prob_home"], entry["prob_draw"], entry["prob_away"]]
        if not np.isfinite(p).all() or min(p) < 0 or abs(sum(p) - 1) > 1e-4:
            raise ValueError("CHALLENGER_M1_BASELINE_PROBABILITIES_INVALID")
        future.append(Fixture(league, "2627", d, a, b, -1, -1, -1))
    if max(f.match_date for f in history) >= min(f.match_date for f in future):
        raise ValueError("CHALLENGER_FUTURE_TRAINING_TIME_OVERLAP")

    all_fixtures = history + future
    causal_x, labels = build_pre_match_features(all_fixtures)
    elo_x = _causal_elo_features(all_fixtures)
    features = np.column_stack((causal_x, elo_x))
    if features.shape != (len(all_fixtures), 24):
        raise ValueError("CHALLENGER_FEATURE_SCHEMA_DRIFT")
    train_size = len(history)
    if not np.isin(labels[:train_size], [0, 1, 2]).all() or not np.all(labels[train_size:] == -1):
        raise ValueError("CHALLENGER_TEST_RESULTS_LEAKED_INTO_FEATURE_SET")

    model = _model("L2_1.0")
    model.fit(features[:train_size], labels[:train_size])
    result = _probabilities(model, features[train_size:])
    if result.shape != (len(future), 3):
        raise ValueError("CHALLENGER_OUTPUT_NOT_FULL_COVERAGE")

    scaler = model[0]
    estimator = model[-1]
    weights = {
        "name": "CAUSAL_ELO_L2_1.0",
        "classes": estimator.classes_.astype(int).tolist(),
        "scaler_means": scaler.mean_.tolist(),
        "scaler_scales": scaler.scale_.tolist(),
        "coefficients": estimator.coef_.tolist(),
        "intercepts": estimator.intercept_.tolist(),
        "training_feature_sha256": sha256(
            np.asarray(features[:train_size], dtype="<f8").tobytes()
            + np.asarray(labels[:train_size], dtype="<i8").tobytes()
        ).hexdigest(),
        "source_receipts_sha256": _digest({"receipts": historical_receipts}),
    }
    model_sha = _digest(weights)
    paired = []
    for item, row, probabilities in zip(qualified, features[train_size:], result):
        paired.append({
            "fixture_sha256": item["fixture_sha256"],
            "league": item["league"],
            "provider_home": item["provider_home"],
            "provider_away": item["provider_away"],
            "expected_calendar_date": item["expected_calendar_date"],
            "baseline_prob_home": item["prob_home"],
            "baseline_prob_draw": item["prob_draw"],
            "baseline_prob_away": item["prob_away"],
            "candidate_prob_home": float(probabilities[0]),
            "candidate_prob_draw": float(probabilities[1]),
            "candidate_prob_away": float(probabilities[2]),
            "candidate_model_sha256": model_sha,
            "candidate_feature_snapshot_sha256": sha256(
                json.dumps(row.tolist(), separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            "status": "PAIRED_PREMATCH_SHADOW_UNSCORED",
        })
    paired.sort(key=lambda x: (x["expected_calendar_date"], x["league"], x["fixture_sha256"]))
    result_report = {
        "schema_version": "independent_prospective_elo_challenger_1.0.0",
        "status": "RESEARCH_ONLY_AWAITING_AUTHENTIC_POSTMATCH_RESULTS",
        "generated_at_utc": as_of.isoformat(),
        "baseline_ledger_sha256": original_digest,
        "upstream_fixture_commit": baseline["fixture_source_commit"],
        "upstream_source_receipts": baseline["upstream_source_receipts"],
        "historical_source_receipts": historical_receipts,
        "model": {
            "name": "CAUSAL_ELO_L2_1.0",
            "weights_sha256": model_sha,
            "training_rows": train_size,
            "training_latest_calendar_date": max(f.match_date for f in history).isoformat(),
            "input_feature_count": 24,
            "training_label_classes": weights["classes"],
            "probability_status": "RAW_UNCALIBRATED",
            "publication_eligible": False,
            "source_research_only": True,
        },
        "counts": {
            "baseline_future_qualified": len(qualified),
            "challenger_issued": len(paired),
            "baseline_blocked": baseline["counts"]["blocked"],
        },
        "predictions": paired,
        "limitations": [
            "This is a NEW shadow ledger; no previously issued 63-forecast ledger is modified.",
            "The M1 baseline and challenger are issued for the exact same future fixtures and date-only cutoff.",
            "Source schedule times are not verified UTC kickoffs; strict future calendar day gate.",
            "Historical training ends at 2025/26; no 2026/27 scored matches enter model fitting or Elo features.",
            "Research team identities use exact source aliases, not verified canonical Phase 6 team IDs.",
            "Raw research model probabilities are not the frozen 251-feature C1 champion.",
            "Future results, accuracy, calibration, live utility and profitability are NOT established.",
            "The selected research candidate was investigated post hoc on completed seasons; prospective scoring required.",
            "Upstream schedule is noncommercial and this data must not be republished as a commercial product.",
        ],
    }
    result_report["ledger_sha256"] = _digest(result_report)
    return result_report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", default="../data/research_football_data_raw")
    parser.add_argument("--output", required=True)
    parser.add_argument("--lookahead-days", type=int, default=21)
    args = parser.parse_args()
    as_of = datetime.now(UTC)
    history, receipts = download_completed_seasons(Path(args.raw_dir))
    source_commit, upcoming, source_receipts = _source_snapshot(as_of)
    baseline = prospective_shadow(
        history, receipts, source_commit, upcoming, source_receipts,
        as_of=as_of, lookahead_days=args.lookahead_days,
    )
    report = issue_accuracy_challenger(history, receipts, baseline, as_of=as_of)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "issued": report["counts"]["challenger_issued"],
        "baseline_ledger_sha256": report["baseline_ledger_sha256"],
        "candidate_weights_sha256": report["model"]["weights_sha256"],
        "new_paired_ledger_sha256": report["ledger_sha256"],
        "report": str(output),
    }, indent=2))


if __name__ == "__main__":
    main()
