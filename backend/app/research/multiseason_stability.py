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
    _model,
    _probabilities,
    _research_modeling_rows,
    _structural_model,
    _structural_probabilities,
    build_pre_match_features,
    download_completed_seasons,
)
from app.research.uncertainty import date_clustered_log_loss_skill


def _accuracy_diagnostics(labels, probabilities):
    """Descriptive 1X2 accuracy and fixed confidence/coverage slices.

    Thresholds are NOT tuned here and must not be promoted from retrospective
    research into a production abstention policy without fresh validation.
    """
    labels = np.asarray(labels, dtype=int)
    probabilities = np.asarray(probabilities, dtype=float)
    if (labels.ndim != 1 or probabilities.shape != (len(labels), 3)
            or not np.isin(labels, [0, 1, 2]).all()
            or not np.isfinite(probabilities).all()
            or (probabilities < 0).any()
            or not np.allclose(probabilities.sum(axis=1), 1.0, atol=1e-6)):
        raise ValueError("INVALID_ACCURACY_DIAGNOSTIC_INPUT")
    predictions = np.argmax(probabilities, axis=1)
    correct = predictions == labels
    n = len(labels)
    slices = []
    for threshold in (0.45, 0.50, 0.55, 0.60):
        selected = probabilities.max(axis=1) >= threshold
        count = int(selected.sum())
        slices.append({
            "minimum_top_class_probability": threshold,
            "selected": count,
            "abstained": n - count,
            "coverage": count / n if n else 0.0,
            "accuracy": float(correct[selected].mean()) if count else None,
            "correct": int(correct[selected].sum()),
        })
    return {
        "sample_size": n,
        "correct": int(correct.sum()),
        "accuracy": float(correct.mean()) if n else None,
        "confidence_slices": slices,
    }


def _causal_elo_features(fixtures):
    """Fixed-form team-rating features from results on STRICTLY earlier dates.

    Research-only identities are (league, source team name). A 65-point home
    offset and K=24 are fixed hypotheses, not test-season tuned parameters.
    A scheduled, unplayed fixture does not modify any rating.
    """
    ratings = {}
    played = {}
    matrix = np.zeros((len(fixtures), 4), dtype=float)
    ordered = sorted(
        enumerate(fixtures),
        key=lambda pair: (pair[1].match_date, pair[1].league, pair[1].home, pair[1].away),
    )
    cursor = 0
    while cursor < len(ordered):
        match_day = ordered[cursor][1].match_date
        end = cursor
        while end < len(ordered) and ordered[end][1].match_date == match_day:
            end += 1
        daily = ordered[cursor:end]
        for i, match in daily:
            hk = (match.league, match.home)
            ak = (match.league, match.away)
            matrix[i] = [
                ratings.get(hk, 0.0) / 400.0,
                ratings.get(ak, 0.0) / 400.0,
                np.log1p(played.get(hk, 0)),
                np.log1p(played.get(ak, 0)),
            ]
        used = set()
        for _, match in daily:
            if (match.outcome, match.home_goals, match.away_goals) == (-1, -1, -1):
                continue
            if match.outcome not in (0, 1, 2):
                raise ValueError("ELO_INVALID_MATCH_OUTCOME")
            hk = (match.league, match.home)
            ak = (match.league, match.away)
            if hk in used or ak in used or hk == ak:
                raise ValueError("ELO_DUPLICATE_TEAM_SAME_DATE")
            used.update((hk, ak))
            hr, ar = ratings.get(hk, 0.0), ratings.get(ak, 0.0)
            expectation = 1.0 / (1.0 + 10.0 ** (-(hr + 65.0 - ar) / 400.0))
            observed = (1.0, 0.5, 0.0)[match.outcome]
            delta = 24.0 * (observed - expectation)
            ratings[hk] = hr + delta
            ratings[ak] = ar - delta
            played[hk] = played.get(hk, 0) + 1
            played[ak] = played.get(ak, 0) + 1
        cursor = end
    if not np.isfinite(matrix).all():
        raise ValueError("NONFINITE_ELO_RESEARCH_FEATURE")
    return matrix


def _date_clustered_accuracy_difference(labels, challenger, baseline, dates, *, seed=20261111, n_bootstrap=2000):
    """Paired full-coverage top-class accuracy delta, resampling whole match dates.

    A descriptive retrospective interval, not a sealed or prospective claim.
    """
    _accuracy_diagnostics(labels, challenger)
    _accuracy_diagnostics(labels, baseline)
    labels = np.asarray(labels, dtype=int)
    challenger = np.asarray(challenger, dtype=float)
    baseline = np.asarray(baseline, dtype=float)
    if len(labels) == 0 or len(dates) != len(labels) or n_bootstrap < 200:
        raise ValueError("INVALID_PAIRED_ACCURACY_DATE_CLUSTERS")
    unique_dates = sorted(set(dates))
    if len(unique_dates) < 20:
        raise ValueError("INSUFFICIENT_PAIRED_ACCURACY_DATE_CLUSTERS")
    diff = (np.argmax(challenger, axis=1) == labels).astype(float) - (np.argmax(baseline, axis=1) == labels).astype(float)
    indices_by_day = {day: [] for day in unique_dates}
    for i, day in enumerate(dates):
        indices_by_day[day].append(i)
    sums = np.asarray([diff[indices_by_day[day]].sum() for day in unique_dates])
    counts = np.asarray([len(indices_by_day[day]) for day in unique_dates])
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(unique_dates), size=(n_bootstrap, len(unique_dates)))
    sampled = sums[draws].sum(axis=1) / counts[draws].sum(axis=1)
    ci = np.quantile(sampled, [0.025, 0.975])
    return {
        "accuracy_difference": float(diff.mean()),
        "confidence_interval_95": [float(ci[0]), float(ci[1])],
        "bootstrap_probability_positive": float(np.mean(sampled > 0)),
        "unique_match_dates": len(unique_dates),
        "bootstrap_iterations": n_bootstrap,
        "sample_size": len(labels),
    }


TEST_SEASONS = ("2223", "2324", "2425", "2526")
STRUCTURAL_CHALLENGER = "M1_DECAY_365"
FIXED_LOGISTIC_CHALLENGER = "L2_1.0"
FIXED_ENSEMBLE_M1_WEIGHT = 0.5


def evaluate_multiseason_stability(fixtures, receipts):
    if len(fixtures) != len(SEASONS) * sum(LEAGUE_MATCH_COUNTS.values()):
        raise ValueError("RESEARCH_HISTORICAL_COVERAGE_INCOMPLETE")
    if len(receipts) != len(SEASONS) * len(LEAGUE_MATCH_COUNTS):
        raise ValueError("RESEARCH_SOURCE_RECEIPTS_INCOMPLETE")
    if len({(p["season"], p["league"]) for p in receipts}) != len(receipts):
        raise ValueError("RESEARCH_SOURCE_PROVENANCE_DUPLICATED")

    x, y = build_pre_match_features(fixtures)
    elo_x = _causal_elo_features(fixtures)
    elo_augmented = np.column_stack((x, elo_x))
    rows = _research_modeling_rows(fixtures, x, receipts)
    if len(rows) != len(fixtures) or x.shape != (len(rows), 20):
        raise ValueError("RESEARCH_FEATURE_ALIGNMENT_MISMATCH")
    observed = []
    baseline_pred = []
    challenger_pred = []
    logistic_pred = []
    ensemble_pred = []
    elo_logistic_pred = []
    elo_blend_pred = []
    elo_hgb_pred = []
    hgb_blend_pred = []
    league_elo_logistic_pred = []
    league_elo_blend_pred = []
    stacked_meta_pred = []
    prior_oos_meta_features = []
    prior_oos_meta_labels = []
    prior_oos_meta_seasons = []
    scored_dates: list[date] = []
    outcomes_seen = set()
    folds = []
    for year in TEST_SEASONS:
        before = set(SEASONS[:SEASONS.index(year)])
        train_indices = [i for i, row in enumerate(fixtures) if row.season in before]
        test_indices = [i for i, row in enumerate(fixtures) if row.season == year]
        if (
            len(test_indices) != sum(LEAGUE_MATCH_COUNTS.values())
            or len(train_indices) < 3 * sum(LEAGUE_MATCH_COUNTS.values())
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
        # Independently fixed retrospective challengers. No test-season selection.
        logistic_model = _model(FIXED_LOGISTIC_CHALLENGER)
        logistic_model.fit(x[train_indices], train_label)
        logistic = _probabilities(logistic_model, x[test_indices])
        ensemble = FIXED_ENSEMBLE_M1_WEIGHT * challenger + (1 - FIXED_ENSEMBLE_M1_WEIGHT) * logistic
        _accuracy_diagnostics(test_label, logistic)
        _accuracy_diagnostics(test_label, ensemble)
        elo_model = _model(FIXED_LOGISTIC_CHALLENGER)
        elo_model.fit(elo_augmented[train_indices], train_label)
        elo_logistic = _probabilities(elo_model, elo_augmented[test_indices])
        elo_blend = 0.5 * challenger + 0.5 * elo_logistic
        _accuracy_diagnostics(test_label, elo_logistic)
        _accuracy_diagnostics(test_label, elo_blend)
        hgb_model = _model("HGB_120")
        hgb_model.fit(elo_augmented[train_indices], train_label)
        elo_hgb = _probabilities(hgb_model, elo_augmented[test_indices])
        hgb_blend = 0.5 * challenger + 0.5 * elo_hgb
        _accuracy_diagnostics(test_label, elo_hgb)
        _accuracy_diagnostics(test_label, hgb_blend)
        # Each league is fitted only on its own strictly earlier seasons.
        league_elo_logistic = np.full_like(challenger, np.nan)
        for code in LEAGUE_MATCH_COUNTS:
            league_train = [i for i in train_indices if fixtures[i].league == code]
            positions = [j for j, i in enumerate(test_indices) if fixtures[i].league == code]
            league_test = [test_indices[j] for j in positions]
            if len(league_train) < 3 * LEAGUE_MATCH_COUNTS[code] or len(league_test) != LEAGUE_MATCH_COUNTS[code]:
                raise ValueError("LEAGUE_MODEL_ROLLING_COVERAGE_INVALID")
            league_model = _model(FIXED_LOGISTIC_CHALLENGER)
            league_model.fit(elo_augmented[league_train], y[league_train])
            league_elo_logistic[positions] = _probabilities(league_model, elo_augmented[league_test])
        _accuracy_diagnostics(test_label, league_elo_logistic)
        league_elo_blend = 0.5 * challenger + 0.5 * league_elo_logistic
        _accuracy_diagnostics(test_label, league_elo_blend)
        # Meta-learner only sees OUT-OF-SAMPLE predictions and final results
        # from previous completed seasons. Current season stays sealed.
        current_meta_features = np.column_stack((challenger, elo_logistic, elo_hgb))
        meta_training_fixtures = sum(len(a) for a in prior_oos_meta_labels)
        if prior_oos_meta_features:
            stacked_model = _model("L2_0.1")
            stacked_model.fit(np.vstack(prior_oos_meta_features), np.concatenate(prior_oos_meta_labels))
            stacked_meta = _probabilities(stacked_model, current_meta_features)
        else:
            # No earlier independent OOS meta-labels are available for first fold.
            stacked_meta = elo_blend.copy()
        _accuracy_diagnostics(test_label, stacked_meta)
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
            "stacked_meta_training_oos_fixtures": meta_training_fixtures,
            "stacked_meta_training_seasons": list(prior_oos_meta_seasons),
            "training_latest_date": max_train_date.isoformat(),
            "test_earliest_date": min_test_date.isoformat(),
            "fitted_artifact_fingerprint": model.artifact.artifact_fingerprint,
            "baseline_metrics": _metric_summary(test_label, baseline),
            "raw_challenger_metrics": _metric_summary(test_label, challenger),
            "baseline_accuracy_diagnostics": _accuracy_diagnostics(test_label, baseline),
            "challenger_accuracy_diagnostics": _accuracy_diagnostics(test_label, challenger),
            "fixed_logistic_metrics": _metric_summary(test_label, logistic),
            "fixed_logistic_accuracy_diagnostics": _accuracy_diagnostics(test_label, logistic),
            "fixed_ensemble_metrics": _metric_summary(test_label, ensemble),
            "fixed_ensemble_accuracy_diagnostics": _accuracy_diagnostics(test_label, ensemble),
            "fixed_elo_logistic_metrics": _metric_summary(test_label, elo_logistic),
            "fixed_elo_logistic_accuracy_diagnostics": _accuracy_diagnostics(test_label, elo_logistic),
            "fixed_elo_blend_metrics": _metric_summary(test_label, elo_blend),
            "fixed_elo_blend_accuracy_diagnostics": _accuracy_diagnostics(test_label, elo_blend),
            "fixed_elo_hgb_metrics": _metric_summary(test_label, elo_hgb),
            "fixed_elo_hgb_accuracy_diagnostics": _accuracy_diagnostics(test_label, elo_hgb),
            "fixed_hgb_blend_metrics": _metric_summary(test_label, hgb_blend),
            "fixed_hgb_blend_accuracy_diagnostics": _accuracy_diagnostics(test_label, hgb_blend),
            "fixed_league_elo_logistic_metrics": _metric_summary(test_label, league_elo_logistic),
            "fixed_league_elo_logistic_accuracy_diagnostics": _accuracy_diagnostics(test_label, league_elo_logistic),
            "fixed_league_elo_blend_metrics": _metric_summary(test_label, league_elo_blend),
            "fixed_league_elo_blend_accuracy_diagnostics": _accuracy_diagnostics(test_label, league_elo_blend),
            "stacked_meta_metrics": _metric_summary(test_label, stacked_meta),
            "stacked_meta_accuracy_diagnostics": _accuracy_diagnostics(test_label, stacked_meta),
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
                "fixed_logistic": _metric_summary(test_label[mask], logistic[mask]),
                "fixed_ensemble": _metric_summary(test_label[mask], ensemble[mask]),
                "fixed_elo_logistic": _metric_summary(test_label[mask], elo_logistic[mask]),
                "fixed_elo_blend": _metric_summary(test_label[mask], elo_blend[mask]),
                "fixed_elo_hgb": _metric_summary(test_label[mask], elo_hgb[mask]),
                "fixed_hgb_blend": _metric_summary(test_label[mask], hgb_blend[mask]),
                "fixed_league_elo_logistic": _metric_summary(test_label[mask], league_elo_logistic[mask]),
                "fixed_league_elo_blend": _metric_summary(test_label[mask], league_elo_blend[mask]),
                "stacked_meta": _metric_summary(test_label[mask], stacked_meta[mask]),
            }
        folds.append(fold)
        observed.extend(test_label.tolist())
        baseline_pred.extend(baseline.tolist())
        challenger_pred.extend(challenger.tolist())
        logistic_pred.extend(logistic.tolist())
        ensemble_pred.extend(ensemble.tolist())
        elo_logistic_pred.extend(elo_logistic.tolist())
        elo_blend_pred.extend(elo_blend.tolist())
        elo_hgb_pred.extend(elo_hgb.tolist())
        hgb_blend_pred.extend(hgb_blend.tolist())
        league_elo_logistic_pred.extend(league_elo_logistic.tolist())
        league_elo_blend_pred.extend(league_elo_blend.tolist())
        stacked_meta_pred.extend(stacked_meta.tolist())
        prior_oos_meta_features.append(current_meta_features)
        prior_oos_meta_labels.append(test_label.copy())
        prior_oos_meta_seasons.append(year)
        scored_dates.extend(fixtures[i].match_date for i in test_indices)

    y_all = np.asarray(observed, dtype=int)
    p_baseline = np.asarray(baseline_pred, dtype=float)
    p_challenger = np.asarray(challenger_pred, dtype=float)
    p_logistic = np.asarray(logistic_pred, dtype=float)
    p_ensemble = np.asarray(ensemble_pred, dtype=float)
    p_elo_logistic = np.asarray(elo_logistic_pred, dtype=float)
    p_elo_blend = np.asarray(elo_blend_pred, dtype=float)
    p_elo_hgb = np.asarray(elo_hgb_pred, dtype=float)
    p_hgb_blend = np.asarray(hgb_blend_pred, dtype=float)
    p_league_elo_logistic = np.asarray(league_elo_logistic_pred, dtype=float)
    p_league_elo_blend = np.asarray(league_elo_blend_pred, dtype=float)
    p_stacked_meta = np.asarray(stacked_meta_pred, dtype=float)
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
        "fixed_research_comparators": [FIXED_LOGISTIC_CHALLENGER, "M1_LOGISTIC_50_50", "CAUSAL_ELO_L2_1.0", "M1_ELO_L2_50_50", "CAUSAL_ELO_HGB_120", "M1_ELO_HGB_50_50", "LEAGUE_ELO_L2_1.0", "M1_LEAGUE_ELO_L2_50_50", "PRIOR_OOS_STACKED_META_L2_0.1"],
        "stacked_meta_policy": {"training": "EARLIER_SEASON_OOS_PREDICTIONS_AND_LABELS_ONLY", "base_inputs": ["M1_DECAY_365", "CAUSAL_ELO_L2_1.0", "CAUSAL_ELO_HGB_120"], "learner": "L2_0.1", "warmup_policy": "M1_ELO_L2_50_50"},
        "fixed_elo_policy": {"rating_offset": 65, "k_factor": 24, "initial_rating_centered": 0.0, "extra_features": 4},
        "fixed_ensemble_m1_weight": FIXED_ENSEMBLE_M1_WEIGHT,
        "comparison_selection_policy": "NO_TEST_RESULT_SELECTION_NO_PRODUCTION_PROMOTION",
        "years": list(TEST_SEASONS),
        "folds": folds,
        "total_oos_fixtures": len(y_all),
        "pooled_baseline_metrics": _metric_summary(y_all, p_baseline),
        "pooled_baseline_accuracy_diagnostics": _accuracy_diagnostics(y_all, p_baseline),
        "pooled_challenger_accuracy_diagnostics": _accuracy_diagnostics(y_all, p_challenger),
        "pooled_raw_challenger_metrics": _metric_summary(y_all, p_challenger),
        "pooled_fixed_logistic_metrics": _metric_summary(y_all, p_logistic),
        "pooled_fixed_logistic_accuracy_diagnostics": _accuracy_diagnostics(y_all, p_logistic),
        "pooled_fixed_elo_logistic_metrics": _metric_summary(y_all, p_elo_logistic),
        "pooled_fixed_elo_logistic_accuracy_diagnostics": _accuracy_diagnostics(y_all, p_elo_logistic),
        "pooled_fixed_elo_blend_metrics": _metric_summary(y_all, p_elo_blend),
        "pooled_fixed_elo_blend_accuracy_diagnostics": _accuracy_diagnostics(y_all, p_elo_blend),
        "pooled_fixed_elo_hgb_metrics": _metric_summary(y_all, p_elo_hgb),
        "pooled_fixed_elo_hgb_accuracy_diagnostics": _accuracy_diagnostics(y_all, p_elo_hgb),
        "pooled_fixed_hgb_blend_metrics": _metric_summary(y_all, p_hgb_blend),
        "pooled_fixed_hgb_blend_accuracy_diagnostics": _accuracy_diagnostics(y_all, p_hgb_blend),
        "pooled_fixed_league_elo_logistic_metrics": _metric_summary(y_all, p_league_elo_logistic),
        "pooled_fixed_league_elo_logistic_accuracy_diagnostics": _accuracy_diagnostics(y_all, p_league_elo_logistic),
        "pooled_fixed_league_elo_blend_metrics": _metric_summary(y_all, p_league_elo_blend),
        "pooled_fixed_league_elo_blend_accuracy_diagnostics": _accuracy_diagnostics(y_all, p_league_elo_blend),
        "pooled_stacked_meta_metrics": _metric_summary(y_all, p_stacked_meta),
        "pooled_stacked_meta_accuracy_diagnostics": _accuracy_diagnostics(y_all, p_stacked_meta),
        "stacked_meta_vs_m1_clustered_accuracy_difference": _date_clustered_accuracy_difference(y_all, p_stacked_meta, p_challenger, scored_dates),
        "fixed_league_elo_blend_vs_m1_clustered_accuracy_difference": _date_clustered_accuracy_difference(y_all, p_league_elo_blend, p_challenger, scored_dates),
        "fixed_hgb_blend_vs_m1_clustered_accuracy_difference": _date_clustered_accuracy_difference(y_all, p_hgb_blend, p_challenger, scored_dates),
        "fixed_elo_blend_vs_m1_clustered_accuracy_difference": _date_clustered_accuracy_difference(y_all, p_elo_blend, p_challenger, scored_dates),
        "fixed_elo_blend_vs_m1_clustered_log_loss_skill": {
            "improvement_log_loss": (elo_skill := date_clustered_log_loss_skill(
                y_all, p_elo_blend, p_challenger, scored_dates,
                n_bootstrap=2000, seed=20261109,
            )).improvement_log_loss,
            "confidence_interval_95": list(elo_skill.confidence_interval_95),
            "unique_match_dates": elo_skill.unique_match_dates,
            "bootstrap_iterations": elo_skill.bootstrap_iterations,
        },
        "pooled_fixed_ensemble_metrics": _metric_summary(y_all, p_ensemble),
        "pooled_fixed_ensemble_accuracy_diagnostics": _accuracy_diagnostics(y_all, p_ensemble),
        "fixed_logistic_vs_m1_clustered_accuracy_difference": _date_clustered_accuracy_difference(y_all, p_logistic, p_challenger, scored_dates),
        "fixed_ensemble_vs_m1_clustered_accuracy_difference": _date_clustered_accuracy_difference(y_all, p_ensemble, p_challenger, scored_dates),
        "fixed_ensemble_vs_m1_clustered_log_loss_skill": {
            "improvement_log_loss": (ensemble_skill := date_clustered_log_loss_skill(
                y_all, p_ensemble, p_challenger, scored_dates,
                n_bootstrap=2000, seed=20261109,
            )).improvement_log_loss,
            "confidence_interval_95": list(ensemble_skill.confidence_interval_95),
            "unique_match_dates": ensemble_skill.unique_match_dates,
            "bootstrap_iterations": ensemble_skill.bootstrap_iterations,
        },
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
            "The 20-feature logistic comparator is NOT the certified 251-feature C1 champion.",
            "Research Elo features are computed only from earlier calendar dates and source aliases, not verified canonical team IDs.",
            "Fixed logistic and ensemble results are post hoc comparisons, not a fresh sealed holdout or model selection basis.",
            "Actual scores are taken from authenticated source URLs with hashes, not reconstructed.",
            "Confidence/coverage slices are post hoc descriptive only; thresholds were not validated prospectively.",
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
        "m1_accuracy": report["pooled_challenger_accuracy_diagnostics"]["accuracy"],
        "logistic_accuracy": report["pooled_fixed_logistic_accuracy_diagnostics"]["accuracy"],
        "ensemble_accuracy": report["pooled_fixed_ensemble_accuracy_diagnostics"]["accuracy"],
        "elo_logistic_accuracy": report["pooled_fixed_elo_logistic_accuracy_diagnostics"]["accuracy"],
        "elo_blend_accuracy": report["pooled_fixed_elo_blend_accuracy_diagnostics"]["accuracy"],
        "elo_hgb_accuracy": report["pooled_fixed_elo_hgb_accuracy_diagnostics"]["accuracy"],
        "hgb_blend_accuracy": report["pooled_fixed_hgb_blend_accuracy_diagnostics"]["accuracy"],
        "league_elo_logistic_accuracy": report["pooled_fixed_league_elo_logistic_accuracy_diagnostics"]["accuracy"],
        "league_elo_blend_accuracy": report["pooled_fixed_league_elo_blend_accuracy_diagnostics"]["accuracy"],
        "stacked_meta_accuracy": report["pooled_stacked_meta_accuracy_diagnostics"]["accuracy"],
        "ensemble_vs_m1_log_loss_skill": report["fixed_ensemble_vs_m1_clustered_log_loss_skill"],
        "report_sha256": report["research_report_sha256"],
    }, indent=2))


if __name__ == "__main__":
    main()
