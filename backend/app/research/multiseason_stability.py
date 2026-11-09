"""Multi-season retrospective OOS stability audit of fixed structural M1 decay.

This is a *post hoc sensitivity analysis*, not a new sealed prospective test.
Four completed seasons are scored in independently refitted rolling-origin folds,
each using only PREVIOUS completed seasons for parameter estimation.
"""
from __future__ import annotations

import argparse
from datetime import date
from hashlib import sha256
import json
from pathlib import Path

import numpy as np

from app.research.historical_oos import (
    LEAGUE_MATCH_COUNTS,
    SEASONS,
    _log_loss,
    _metric_summary,
    _research_modeling_rows,
    _structural_model,
    _structural_probabilities,
    build_pre_match_features,
    download_completed_seasons,
)
from app.research.uncertainty import date_clustered_log_loss_skill


TEST_SEASONS = ("2223", "2324", "2425", "2526")
STRUCTURAL_CHALLENGER = "M1_DECAY_365"


def evaluate_multiseason_stability(fixtures, receipts):
    if len(fixtures) != len(SEASONS) * sum(LEAGUE_MATCH_COUNTS.values()):
        raise ValueError("RESEARCH_HISTORICAL_COVERAGE_INCOMPLETE")
    if len(receipts) != len(SEASONS) * len(LEAGUE_MATCH_COUNTS):
        raise ValueError("RESEARCH_SOURCE_RECEIPTS_INCOMPLETE")
    if len({(p["season"], p["league"]) for p in receipts}) != len(receipts):
        raise ValueError("RESEARCH_SOURCE_PROVENANCE_DUPLICATED")

    x, y = build_pre_match_features(fixtures)
    rows = _research_modeling_rows(fixtures, x, receipts)
    if len(rows) != len(fixtures) or x.shape != (len(rows), 20):
        raise ValueError("RESEARCH_FEATURE_ALIGNMENT_MISMATCH")
    observed = []
    baseline_pred = []
    challenger_pred = []
    scored_dates: list[date] = []
    outcomes_seen = set()
    folds = []
    for year in TEST_SEASONS:
        before = set(SEASONS[:SEASONS.index(year)])
        train_indices = [i for i, row in enumerate(fixtures) if row.season in before]
        test_indices = [i for i, row in enumerate(fixtures) if row.season == year]
        if (
            len(test_indices) != sum(LEAGUE_MATCH_COUNTS.values())
            or len(train_indices) < 3000
        ):
            raise ValueError("ROLLING_SEASON_SOURCE_COVERAGE_MISMATCH")
        max_train_date = max(fixtures[i].match_date for i in train_indices)
        min_test_date = min(fixtures[i].match_date for i in test_indices)
        if max_train_date >= min_test_date:
            raise ValueError("ROLLING_SEASON_OUTCOME_LEAKAGE")
        if set(train_indices).intersection(test_indices):
            raise ValueError("ROLLING_SEASON_INDEX_OVERLAP")
        train_label = y[train_indices]
        test_label = y[test_indices]
        prior = np.bincount(train_label, minlength=3).astype(float)
        prior /= prior.sum()
        if np.any(prior <= 0):
            raise ValueError("PRIOR_REQUIRES_THREE_OBSERVED_CLASSES")
        baseline = np.tile(prior, (len(test_indices), 1))
        model = _structural_model(STRUCTURAL_CHALLENGER)
        model.fit([rows[i] for i in train_indices])
        if not model.artifact or not model.artifact.verify_integrity():
            raise ValueError("ROLLED_MODEL_ARTIFACT_NOT_VERIFIED")
        challenger = _structural_probabilities(model, rows, test_indices)
        if not np.isfinite(challenger).all():
            raise ValueError("NONFINITE_OOS_PROBABILITIES")

        for i in test_indices:
            key = (fixtures[i].league, fixtures[i].season,
                   fixtures[i].match_date, fixtures[i].home, fixtures[i].away)
            if key in outcomes_seen:
                raise ValueError("REUSED_HISTORICAL_TEST_FIXTURE")
            outcomes_seen.add(key)
        fold = {
            "test_season": year,
            "training_seasons": sorted(before),
            "training_fixtures": len(train_indices),
            "test_fixtures": len(test_indices),
            "training_latest_date": max_train_date.isoformat(),
            "test_earliest_date": min_test_date.isoformat(),
            "fitted_artifact_fingerprint": model.artifact.artifact_fingerprint,
            "baseline_metrics": _metric_summary(test_label, baseline),
            "raw_challenger_metrics": _metric_summary(test_label, challenger),
            "log_loss_skill": _log_loss(test_label, baseline)
              - _log_loss(test_label, challenger),
            "per_league": {},
        }
        for code in LEAGUE_MATCH_COUNTS:
            mask = np.array([fixtures[i].league == code for i in test_indices])
            if mask.sum() != LEAGUE_MATCH_COUNTS[code]:
                raise ValueError("ROLLING_SEASON_LEAGUE_COVERAGE_MISMATCH")
            fold["per_league"][code] = {
                "n": int(mask.sum()),
                "raw_challenger": _metric_summary(test_label[mask], challenger[mask]),
                "baseline": _metric_summary(test_label[mask], baseline[mask]),
            }
        folds.append(fold)
        observed.extend(test_label.tolist())
        baseline_pred.extend(baseline.tolist())
        challenger_pred.extend(challenger.tolist())
        scored_dates.extend(fixtures[i].match_date for i in test_indices)

    y_all = np.asarray(observed, dtype=int)
    p_baseline = np.asarray(baseline_pred, dtype=float)
    p_challenger = np.asarray(challenger_pred, dtype=float)
    if len(y_all) != 4 * sum(LEAGUE_MATCH_COUNTS.values()):
        raise ValueError("ROLLING_TOTAL_HELDOUT_COUNT_MISMATCH")
    intervals = date_clustered_log_loss_skill(
        y_all, p_challenger, p_baseline, scored_dates,
        n_bootstrap=2000, seed=20261109,
    )
    output = {
        "schema_version": "research_multiseason_retrospective_oos_v1",
        "status": "RETROSPECTIVE_RESEARCH_SENSITIVITY_NOT_FRESH_HOLDOUT",
        "source_manifest": receipts,
        "source_manifest_sha256": sha256(
            json.dumps(receipts, sort_keys=True).encode()
        ).hexdigest(),
        "strategy": "YEARWISE_EXPANDING_TRAINING_ROLLING_ORIGIN",
        "challenger": STRUCTURAL_CHALLENGER,
        "years": list(TEST_SEASONS),
        "folds": folds,
        "total_oos_fixtures": len(y_all),
        "pooled_baseline_metrics": _metric_summary(y_all, p_baseline),
        "pooled_raw_challenger_metrics": _metric_summary(y_all, p_challenger),
        "match_date_clustered_log_loss_skill": {
            "improvement_log_loss": intervals.improvement_log_loss,
            "confidence_interval_95": list(intervals.confidence_interval_95),
            "unique_match_dates": intervals.unique_match_dates,
            "bootstrap_iterations": intervals.bootstrap_iterations,
        },
        "limitations": [
            "Retrospective seasonal sensitivity after 2025-26 research; not independent pre-registration.",
            "Model refitted at each season boundary only; previously completed in-season results may enter date-only features.",
            "Original Phase 4 frozen C1 champion is untouched and not compared directly across this dataset.",
            "Actual scores are taken from authenticated source URLs with hashes, not reconstructed.",
            "These research results cannot authorize production/publication or gambling claims.",
        ],
    }
    output["research_report_sha256"] = sha256(
        json.dumps(output, sort_keys=True).encode()
    ).hexdigest()
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", default="../data/research_football_data_raw")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    fixtures, receipts = download_completed_seasons(Path(args.raw_dir))
    report = evaluate_multiseason_stability(fixtures, receipts)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "count": report["total_oos_fixtures"],
        "baseline_log_loss": report["pooled_baseline_metrics"]["log_loss"],
        "challenger_log_loss": report["pooled_raw_challenger_metrics"]["log_loss"],
        "per_year_skill": {f["test_season"]: f["log_loss_skill"] for f in report["folds"]},
        "report_sha256": report["research_report_sha256"],
    }, indent=2))


if __name__ == "__main__":
    main()
