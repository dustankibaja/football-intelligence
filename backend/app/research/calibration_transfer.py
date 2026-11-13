"""Historical three-fold calibration transfer, each temperature fitted on PRIOR year.

This is a retrospective robustness check of a fixed M1-decay challenger and
a post-hoc illustrative confidence threshold, NOT a new blind holdout.
Every target season remains strictly later than both training and calibration.
"""
from __future__ import annotations

import argparse
from datetime import date
from hashlib import sha256
import json
from pathlib import Path

import numpy as np
from scipy.optimize import minimize_scalar

from app.research.historical_oos import (
    LEAGUE_MATCH_COUNTS,
    SEASONS,
    _log_loss,
    _metric_summary,
    _research_modeling_rows,
    _structural_model,
    _structural_probabilities,
    _temperature,
    build_pre_match_features,
    download_completed_seasons,
)
from app.research.uncertainty import (
    date_clustered_log_loss_skill,
    draw_probability_audit,
    selective_skill_audit,
)

CALIBRATION_TEST_PAIRS = (("2223", "2324"), ("2324", "2425"), ("2425", "2526"))
MODEL = "M1_DECAY_365"
RETROSPECTIVE_FIXED_CONFIDENCE_CUTOFF = 0.60


def evaluate_temporal_calibration_transfer(fixtures, source_manifest):
    n_per_year = sum(LEAGUE_MATCH_COUNTS.values())
    if len(fixtures) != n_per_year * len(SEASONS):
        raise ValueError("CALIBRATION_RESEARCH_HISTORICAL_COVERAGE_MISMATCH")
    if (
        len(source_manifest) != len(LEAGUE_MATCH_COUNTS) * len(SEASONS)
        or len({(s["league"], s["season"]) for s in source_manifest}) != len(source_manifest)
        or any(
            s["finished_matches"] != LEAGUE_MATCH_COUNTS[s["league"]]
            for s in source_manifest
        )
    ):
        raise ValueError("CALIBRATION_RESEARCH_SOURCE_PROVENANCE_INVALID")

    x, y = build_pre_match_features(fixtures)
    rows = _research_modeling_rows(fixtures, x, source_manifest)
    fold_summaries = []
    aggregate_labels = []
    aggregate_prior = []
    aggregate_raw = []
    aggregate_cal = []
    aggregate_dates: list[date] = []
    seen_test_ids = set()

    for tune_year, test_year in CALIBRATION_TEST_PAIRS:
        index = SEASONS.index(tune_year)
        training_years = set(SEASONS[:index])
        train_ids = [i for i, match in enumerate(fixtures) if match.season in training_years]
        tune_ids = [i for i, match in enumerate(fixtures) if match.season == tune_year]
        test_ids = [i for i, match in enumerate(fixtures) if match.season == test_year]
        if (
            len(train_ids) < 3 * n_per_year
            or len(tune_ids) != n_per_year or len(test_ids) != n_per_year
        ):
            raise ValueError("CALIBRATION_SEASON_GROUP_SIZE_INVALID")
        latest_train = max(fixtures[i].match_date for i in train_ids)
        first_tune = min(fixtures[i].match_date for i in tune_ids)
        latest_tune = max(fixtures[i].match_date for i in tune_ids)
        first_test = min(fixtures[i].match_date for i in test_ids)
        if not latest_train < first_tune <= latest_tune < first_test:
            raise ValueError("CALIBRATION_TRAIN_TUNE_TEST_LEAKAGE")

        model = _structural_model(MODEL)
        model.fit([rows[i] for i in train_ids])
        if not model.artifact or not model.artifact.verify_integrity():
            raise ValueError("CALIBRATION_MODEL_ARTIFACT_UNVERIFIED")
        tuning_raw = _structural_probabilities(model, rows, tune_ids)
        opt = minimize_scalar(
            lambda temp: _log_loss(y[tune_ids], _temperature(tuning_raw, temp)),
            method="bounded", bounds=(0.5, 3.0),
            options={"xatol": 1e-5},
        )
        if not opt.success or not np.isfinite(opt.x) or not 0.5 <= opt.x <= 3.0:
            raise ValueError("CALIBRATION_TEMPERATURE_FIT_FAILED")
        temperature = float(opt.x)

        test_raw = _structural_probabilities(model, rows, test_ids)
        test_cal = _temperature(test_raw, temperature)
        counts = np.bincount(y[train_ids], minlength=3).astype(float)
        if np.any(counts == 0):
            raise ValueError("CALIBRATION_TRAINING_PRIOR_CLASS_MISSING")
        baseline = np.tile(counts / counts.sum(), (len(test_ids), 1))

        test_keys = [
            (fixtures[i].league, fixtures[i].season, fixtures[i].match_date,
             fixtures[i].home, fixtures[i].away)
            for i in test_ids
        ]
        if seen_test_ids.intersection(test_keys) or len(set(test_keys)) != len(test_keys):
            raise ValueError("CALIBRATION_OOS_REUSED_FIXTURES")
        seen_test_ids.update(test_keys)

        per_league = {}
        for league in LEAGUE_MATCH_COUNTS:
            mask = np.asarray([fixtures[i].league == league for i in test_ids])
            if int(mask.sum()) != LEAGUE_MATCH_COUNTS[league]:
                raise ValueError("CALIBRATION_LEAGUE_COVERAGE_MISMATCH")
            per_league[league] = {
                "n": int(mask.sum()),
                "raw": _metric_summary(y[test_ids][mask], test_raw[mask]),
                "temperature_calibrated": _metric_summary(
                    y[test_ids][mask], test_cal[mask],
                ),
            }

        test_dates = [fixtures[i].match_date for i in test_ids]
        clustered = date_clustered_log_loss_skill(
            y[test_ids], test_cal, baseline, test_dates,
            n_bootstrap=2000, seed=20261109,
        )
        fold_summaries.append({
            "training_seasons": sorted(training_years),
            "tuning_season": tune_year,
            "test_season": test_year,
            "training_count": len(train_ids),
            "tune_count": len(tune_ids),
            "test_count": len(test_ids),
            "latest_training_date": latest_train.isoformat(),
            "earliest_tuning_date": first_tune.isoformat(),
            "latest_tuning_date": latest_tune.isoformat(),
            "earliest_test_date": first_test.isoformat(),
            "model_fingerprint": model.artifact.artifact_fingerprint,
            "temperature_fitted_on_tuning_only": temperature,
            "tuning_raw_log_loss": _log_loss(y[tune_ids], tuning_raw),
            "tuning_calibrated_log_loss": _log_loss(
                y[tune_ids], _temperature(tuning_raw, temperature)
            ),
            "baseline": _metric_summary(y[test_ids], baseline),
            "raw": _metric_summary(y[test_ids], test_raw),
            "calibrated": _metric_summary(y[test_ids], test_cal),
            "calibrated_minus_raw_log_loss": (
                _log_loss(y[test_ids], test_cal)
                - _log_loss(y[test_ids], test_raw)
            ),
            "calibrated_skill_vs_training_prior": clustered.improvement_log_loss,
            "clustered_skill_interval_95": list(clustered.confidence_interval_95),
            "draw_diagnostics": draw_probability_audit(y[test_ids], test_cal),
            "selective_fixed_threshold_posthoc": selective_skill_audit(
                y[test_ids], test_cal, baseline,
                RETROSPECTIVE_FIXED_CONFIDENCE_CUTOFF,
            ),
            "per_league": per_league,
        })
        aggregate_labels.extend(y[test_ids].tolist())
        aggregate_prior.extend(baseline.tolist())
        aggregate_raw.extend(test_raw.tolist())
        aggregate_cal.extend(test_cal.tolist())
        aggregate_dates.extend(test_dates)

    labels = np.asarray(aggregate_labels, dtype=int)
    prior = np.asarray(aggregate_prior)
    raw = np.asarray(aggregate_raw)
    cal = np.asarray(aggregate_cal)
    if len(labels) != 3 * n_per_year or len(seen_test_ids) != len(labels):
        raise ValueError("CALIBRATION_POOLED_TEST_COUNT_INVALID")
    pooled_ci = date_clustered_log_loss_skill(
        labels, cal, prior, aggregate_dates,
        n_bootstrap=2000, seed=20261109,
    )
    report = {
        "schema_version": "rolling_season_calibration_transfer_v1",
        "status": "RETROSPECTIVE_RESEARCH_CALIBRATION_NOT_PROSPECTIVE_CERTIFICATION",
        "challenger": MODEL,
        "source_manifest": source_manifest,
        "source_manifest_sha256": sha256(
            json.dumps(source_manifest, sort_keys=True).encode()
        ).hexdigest(),
        "test_seasons": [x[1] for x in CALIBRATION_TEST_PAIRS],
        "total_distinct_test_fixtures": len(labels),
        "folds": fold_summaries,
        "pooled_training_prior": _metric_summary(labels, prior),
        "pooled_raw": _metric_summary(labels, raw),
        "pooled_calibrated": _metric_summary(labels, cal),
        "pooled_calibrated_minus_raw_log_loss": _log_loss(labels, cal) - _log_loss(labels, raw),
        "pooled_calibrated_skill_vs_prior": pooled_ci.improvement_log_loss,
        "pooled_clustered_skill_interval_95": list(pooled_ci.confidence_interval_95),
        "pooled_draw_audit": draw_probability_audit(labels, cal),
        "pooled_selective_fixed_threshold_posthoc": selective_skill_audit(
            labels, cal, prior, RETROSPECTIVE_FIXED_CONFIDENCE_CUTOFF,
        ),
        "limitations": [
            "Retrospective model and calibration sensitivity, not pre-registered independent validation.",
            "All calibration temperatures fit exclusively on the season immediately preceding test.",
            "Model parameters fit only on earlier seasons than the tuning season, not retrained on tuning outcomes.",
            "No frozen C1 scientific artifact comparison or canonical 251-feature live parity is claimed.",
            "Confidence threshold 0.60 is illustrative and retrospective, not prospective investment advice.",
            "No production, bookmaker odds, betting profit or model promotion claim.",
        ],
    }
    report["research_report_sha256"] = sha256(
        json.dumps(report, sort_keys=True).encode()
    ).hexdigest()
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", default="../data/research_football_data_raw")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    fixtures, receipts = download_completed_seasons(Path(args.raw_dir))
    report = evaluate_temporal_calibration_transfer(fixtures, receipts)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "n": report["total_distinct_test_fixtures"],
        "prior_log_loss": report["pooled_training_prior"]["log_loss"],
        "raw_log_loss": report["pooled_raw"]["log_loss"],
        "calibrated_log_loss": report["pooled_calibrated"]["log_loss"],
        "calibration_log_loss_change": report["pooled_calibrated_minus_raw_log_loss"],
        "folds": [{
            "test_season": fold["test_season"],
            "temperature": fold["temperature_fitted_on_tuning_only"],
            "calibration_minus_raw_ll": fold["calibrated_minus_raw_log_loss"],
        } for fold in report["folds"]],
        "sha256": report["research_report_sha256"],
    }, indent=2))


if __name__ == "__main__":
    main()
