"""Exploratory, causal team-form feature research for football 1X2.

Every feature for a fixture is calculated from results on STRICTLY EARLIER
calendar dates. This is a research-only sensitivity analysis of historical
results, NOT a calibrated production C1 model or an independent fresh holdout.
No bookmaker odds or post-match/upcoming fixture outcomes are used in features.
"""
from __future__ import annotations

import argparse
from collections import defaultdict, deque
from datetime import date
from hashlib import sha256
import json
import math
from pathlib import Path

import numpy as np

from app.research.historical_oos import (
    LEAGUE_MATCH_COUNTS,
    SEASONS,
    _log_loss,
    _metric_summary,
    _model,
    _probabilities,
    build_pre_match_features,
    download_completed_seasons,
)
from app.research.multiseason_stability import (
    TEST_SEASONS,
    _accuracy_diagnostics,
    _causal_elo_features,
    _date_clustered_accuracy_difference,
)
from app.research.uncertainty import date_clustered_log_loss_skill

ADDITIONAL_FEATURE_COUNT = 26
TOTAL_RESEARCH_FEATURE_COUNT = 20 + 4 + ADDITIONAL_FEATURE_COUNT
TRAINING_MIN_YEARS = 3
# Fixed feature hypotheses. No hyperparameter/feature selection on test labels.
SHORT_WINDOW = 5
LONG_WINDOW = 12
SHORT_DECAY = 0.66
LONG_DECAY = 0.85
LEAGUE_OUTCOME_PRIOR = (0.43, 0.27, 0.30)
LEAGUE_PRIOR_WEIGHT = 100.0


def _team_rolling_features(history: deque) -> list[float]:
    """Points, goals, draws, opponent quality and strength-adjusted form."""
    if not history:
        return [0.43, 0.43, 1.30, 1.20, 1.30, 1.20, 0.10, 0.27, 0.0, 0.0]

    def weighted(field: int, count: int, decay: float) -> float:
        rows = list(history)[-count:]
        weights = np.power(decay, np.arange(len(rows) - 1, -1, -1, dtype=float))
        return float(np.average([row[field] for row in rows], weights=weights))

    return [
        weighted(0, SHORT_WINDOW, SHORT_DECAY),
        weighted(0, LONG_WINDOW, LONG_DECAY),
        weighted(1, SHORT_WINDOW, SHORT_DECAY),
        weighted(2, SHORT_WINDOW, SHORT_DECAY),
        weighted(1, LONG_WINDOW, LONG_DECAY),
        weighted(2, LONG_WINDOW, LONG_DECAY),
        weighted(1, SHORT_WINDOW, SHORT_DECAY)
            - weighted(2, SHORT_WINDOW, SHORT_DECAY),
        weighted(3, LONG_WINDOW, LONG_DECAY),
        weighted(4, LONG_WINDOW, LONG_DECAY),
        weighted(5, LONG_WINDOW, LONG_DECAY),
    ]


def build_causal_strength_form_features(fixtures: list) -> np.ndarray:
    """Return 26 new features. Identical pre-match date cutoff for every fixture.

    Past team state incorporates only finished historical matches; all fixtures
    on the same calendar date are emitted before ANY same-date result updates.
    The Elo quality-adjusted form term uses the *pre-match* Elo snapshot of
    each historical opponent, not a retrospective/end-of-season team rating.
    The schedule marker (-1,-1,-1) never updates team or league statistics.
    """
    if not fixtures:
        raise ValueError("EMPTY_ADVANCED_RESEARCH_FIXTURE_SET")
    elo = _causal_elo_features(fixtures)
    histories = defaultdict(lambda: deque(maxlen=LONG_WINDOW))
    league_counts = defaultdict(lambda: np.zeros(3, dtype=np.float64))
    features = np.zeros((len(fixtures), ADDITIONAL_FEATURE_COUNT), dtype=float)
    ordered = sorted(
        enumerate(fixtures),
        key=lambda x: (x[1].match_date, x[1].league, x[1].home, x[1].away),
    )
    pos = 0
    while pos < len(ordered):
        day = ordered[pos][1].match_date
        end = pos
        while end < len(ordered) and ordered[end][1].match_date == day:
            end += 1
        current = ordered[pos:end]

        for i, match in current:
            h = _team_rolling_features(histories[(match.league, match.home)])
            a = _team_rolling_features(histories[(match.league, match.away)])
            observed = league_counts[match.league]
            rates = (observed + LEAGUE_PRIOR_WEIGHT * np.asarray(LEAGUE_OUTCOME_PRIOR))
            rates = rates / rates.sum()
            month_phase = 2 * math.pi * (day.month - 1) / 12
            features[i, :] = [
                *h, *a,
                float(rates[0]), float(rates[1]), float(rates[2]),
                float(elo[i, 0] - elo[i, 1]),
                math.sin(month_phase), math.cos(month_phase),
            ]

        for i, match in current:
            if match.outcome == match.home_goals == match.away_goals == -1:
                continue
            if (
                match.outcome not in (0, 1, 2)
                or not 0 <= match.home_goals <= 20
                or not 0 <= match.away_goals <= 20
                or match.home == match.away
            ):
                raise ValueError("INVALID_ADVANCED_RESEARCH_HISTORY_RESULT")
            expected_outcome = (
                0 if match.home_goals > match.away_goals
                else 1 if match.home_goals == match.away_goals
                else 2
            )
            if expected_outcome != match.outcome:
                raise ValueError("INCONSISTENT_ADVANCED_RESEARCH_HISTORY_OUTCOME")

            home_rating = float(elo[i, 0]) * 400
            away_rating = float(elo[i, 1]) * 400
            expected_home = 1.0 / (1.0 + 10.0 ** (-(home_rating + 65.0 - away_rating) / 400.0))
            home_points = (1.0, 1.0 / 3.0, 0.0)[match.outcome]
            away_points = (0.0, 1.0 / 3.0, 1.0)[match.outcome]
            h = (home_points, float(match.home_goals), float(match.away_goals),
                 float(match.outcome == 1), home_points - expected_home, away_rating / 400.0)
            a = (away_points, float(match.away_goals), float(match.home_goals),
                 float(match.outcome == 1), away_points - (1.0 - expected_home), home_rating / 400.0)
            histories[(match.league, match.home)].append(h)
            histories[(match.league, match.away)].append(a)
            league_counts[match.league][match.outcome] += 1
        pos = end

    if (features.shape != (len(fixtures), ADDITIONAL_FEATURE_COUNT)
            or not np.isfinite(features).all()):
        raise ValueError("ADVANCED_RESEARCH_FEATURE_SCHEMA_OR_FINITE_FAILURE")
    return features


def _full_feature_matrix(fixtures):
    base, y = build_pre_match_features(fixtures)
    elo = _causal_elo_features(fixtures)
    extra = build_causal_strength_form_features(fixtures)
    x_old = np.column_stack((base, elo))
    x_advanced = np.column_stack((x_old, extra))
    if x_old.shape != (len(fixtures), 24) or x_advanced.shape != (
        len(fixtures), TOTAL_RESEARCH_FEATURE_COUNT
    ):
        raise ValueError("ADVANCED_MODEL_FEATURE_SET_ALIGNMENT_FAILED")
    return x_old, x_advanced, y


def evaluate_advanced_accuracy(fixtures, receipts):
    """Same 5,784 genuine OOS matches as prior research; no test-label tuning."""
    if len(fixtures) != len(SEASONS) * sum(LEAGUE_MATCH_COUNTS.values()):
        raise ValueError("ADVANCED_RESEARCH_SOURCE_COVERAGE_INCOMPLETE")
    if len(receipts) != len(SEASONS) * len(LEAGUE_MATCH_COUNTS):
        raise ValueError("ADVANCED_RESEARCH_SOURCE_MANIFEST_INCOMPLETE")
    if len({(p["league"], p["season"]) for p in receipts}) != len(receipts):
        raise ValueError("ADVANCED_RESEARCH_DUPLICATE_SOURCE_RECEIPT")

    old_x, advanced_x, labels = _full_feature_matrix(fixtures)
    pred_old, pred_advanced, pred_hgb, pred_blend, actual, scored_dates = [], [], [], [], [], []
    identities = set()
    folds = []
    for year in TEST_SEASONS:
        prior_years = set(SEASONS[:SEASONS.index(year)])
        train_ids = [i for i, f in enumerate(fixtures) if f.season in prior_years]
        test_ids = [i for i, f in enumerate(fixtures) if f.season == year]
        expected = sum(LEAGUE_MATCH_COUNTS.values())
        if len(test_ids) != expected or len(train_ids) < TRAINING_MIN_YEARS * expected:
            raise ValueError("ADVANCED_ROLLING_TRAIN_TEST_COVERAGE_ERROR")
        latest_train = max(fixtures[i].match_date for i in train_ids)
        first_test = min(fixtures[i].match_date for i in test_ids)
        if latest_train >= first_test or set(train_ids).intersection(test_ids):
            raise ValueError("ADVANCED_ROLLING_TIME_LEAKAGE_DETECTED")
        y_train, y_test = labels[train_ids], labels[test_ids]
        if not np.isin(y_train, [0, 1, 2]).all() or not np.isin(y_test, [0, 1, 2]).all():
            raise ValueError("ADVANCED_OUTCOME_LABELS_NOT_FINISHED")

        # Frozen, previously established 24-feature causal-Elo baseline.
        old = _model("L2_1.0")
        old.fit(old_x[train_ids], y_train)
        p_old = _probabilities(old, old_x[test_ids])

        # Two strictly fixed new research hypotheses: regularized logistic
        # and constrained shallow boosted trees on exactly the same 50 inputs.
        advanced = _model("L2_1.0")
        advanced.fit(advanced_x[train_ids], y_train)
        p_adv = _probabilities(advanced, advanced_x[test_ids])
        boosted = _model("HGB_120")
        boosted.fit(advanced_x[train_ids], y_train)
        p_hgb = _probabilities(boosted, advanced_x[test_ids])
        p_blend = 0.5 * p_adv + 0.5 * p_hgb

        for predictions in (p_old, p_adv, p_hgb, p_blend):
            _accuracy_diagnostics(y_test, predictions)
        for i in test_ids:
            f = fixtures[i]
            ident = (f.league, f.season, f.match_date, f.home, f.away)
            if ident in identities:
                raise ValueError("DUPLICATE_ADVANCED_HELDOUT_FIXTURE")
            identities.add(ident)

        fold = {
            "test_season": year,
            "training_seasons": sorted(prior_years),
            "training_fixtures": len(train_ids),
            "test_fixtures": len(test_ids),
            "training_latest_date": latest_train.isoformat(),
            "test_earliest_date": first_test.isoformat(),
            "old_elo": _accuracy_diagnostics(y_test, p_old),
            "extended_elo_logistic": _accuracy_diagnostics(y_test, p_adv),
            "extended_form_hgb": _accuracy_diagnostics(y_test, p_hgb),
            "extended_fixed_blend": _accuracy_diagnostics(y_test, p_blend),
            "per_league": {},
        }
        for league in LEAGUE_MATCH_COUNTS:
            which = np.array([fixtures[i].league == league for i in test_ids])
            if which.sum() != LEAGUE_MATCH_COUNTS[league]:
                raise ValueError("ADVANCED_TEST_LEAGUE_COVERAGE_MISMATCH")
            fold["per_league"][league] = {
                "n": int(which.sum()),
                "old_elo": _accuracy_diagnostics(y_test[which], p_old[which]),
                "extended_elo_logistic": _accuracy_diagnostics(y_test[which], p_adv[which]),
                "extended_form_hgb": _accuracy_diagnostics(y_test[which], p_hgb[which]),
                "extended_fixed_blend": _accuracy_diagnostics(y_test[which], p_blend[which]),
            }
        folds.append(fold)
        actual.extend(y_test.tolist())
        pred_old.extend(p_old.tolist())
        pred_advanced.extend(p_adv.tolist())
        pred_hgb.extend(p_hgb.tolist())
        pred_blend.extend(p_blend.tolist())
        scored_dates.extend(fixtures[i].match_date for i in test_ids)

    y = np.asarray(actual, dtype=int)
    pp = {
        "old_elo": np.asarray(pred_old, dtype=float),
        "extended_elo_logistic": np.asarray(pred_advanced, dtype=float),
        "extended_form_hgb": np.asarray(pred_hgb, dtype=float),
        "extended_fixed_blend": np.asarray(pred_blend, dtype=float),
    }
    output = {
        "schema_version": "advanced_causal_form_rolling_research_v1",
        "status": "EXPLORATORY_RETROSPECTIVE_NO_MODEL_PROMOTION",
        "reference_corpus": {
            "test_seasons": list(TEST_SEASONS),
            "test_fixtures": len(y),
            "leagues": list(LEAGUE_MATCH_COUNTS),
            "source_manifest": receipts,
            "source_manifest_sha256": sha256(
                json.dumps(receipts, sort_keys=True).encode("utf-8")
            ).hexdigest(),
        },
        "feature_contract": {
            "old_input_count": 24,
            "new_input_count": TOTAL_RESEARCH_FEATURE_COUNT,
            "additional_input_count": ADDITIONAL_FEATURE_COUNT,
            "max_team_history": LONG_WINDOW,
            "strict_previous_calendar_date": True,
            "no_2026_27_fixtures_or_scores": True,
            "no_odds": True,
            "no_test_season_fit": True,
            "candidate_names": ["extended_elo_logistic", "extended_form_hgb", "extended_fixed_blend"],
        },
        "folds": folds,
        "pooled": {
            key: {
                "accuracy": _accuracy_diagnostics(y, probabilities),
                "metrics": _metric_summary(y, probabilities),
                "draw_predicted_as_top_class": int(np.sum(np.argmax(probabilities, axis=1) == 1)),
            }
            for key, probabilities in pp.items()
        },
        "paired_vs_old_elo": {
            key: {
                "accuracy_difference": _date_clustered_accuracy_difference(
                    y, pp[key], pp["old_elo"], scored_dates
                ),
                "log_loss_skill": {
                    "improvement_log_loss": (interval := date_clustered_log_loss_skill(
                        y, pp[key], pp["old_elo"], scored_dates, n_bootstrap=2000,
                        seed=20261111,
                    )).improvement_log_loss,
                    "confidence_interval_95": list(interval.confidence_interval_95),
                    "unique_match_dates": interval.unique_match_dates,
                    "bootstrap_iterations": interval.bootstrap_iterations,
                },
            }
            for key in ("extended_elo_logistic", "extended_form_hgb", "extended_fixed_blend")
        },
        "limitations": [
            "All model choices were investigated retrospectively after inspecting prior seasons; selection bias is present.",
            "The 5,784 historical fixtures are previously analyzed, not an untouched newly sealed prospective sample.",
            "The baseline is a 24-feature research model, not the certified 251-feature canonical C1 champion.",
            "Goal-only public CSV inputs cannot reconstruct historical xG, injuries, formations, confirmed lineups or accurate kickoff time.",
            "Opponents and longitudinal team ratings use source-specific exact team names, not canonical mapped identifiers.",
            "No fresh 2026/27 completed match is in training or evaluation.",
            "Additional training features do not guarantee better future performance.",
            "Do not change the 71 sealed prospective pairs, production champion, or publication gates from this study.",
            "NO gambling, ROI, profitability or commercial claim is authorized.",
        ],
    }
    serialized = json.dumps(output, sort_keys=True, allow_nan=False)
    output["research_report_sha256"] = sha256(serialized.encode("utf-8")).hexdigest()
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", default="data/research_football_data_raw")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    fixtures, receipts = download_completed_seasons(Path(args.raw_dir))
    report = evaluate_advanced_accuracy(fixtures, receipts)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "historical_fixtures": report["reference_corpus"]["test_fixtures"],
        "source_sha256": report["reference_corpus"]["source_manifest_sha256"],
        "accuracies": {k: v["accuracy"]["accuracy"] for k, v in report["pooled"].items()},
        "log_losses": {k: v["metrics"]["log_loss"] for k, v in report["pooled"].items()},
        "accuracy_differences": {k: v["accuracy_difference"]
                                 for k, v in report["paired_vs_old_elo"].items()},
        "report_sha256": report["research_report_sha256"],
    }, indent=2))


if __name__ == "__main__":
    main()
