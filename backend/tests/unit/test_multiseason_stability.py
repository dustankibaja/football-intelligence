"""Retrospective yearwise OOS boundaries: synthetic fixtures test mechanics only."""

from datetime import date, timedelta
from types import SimpleNamespace

import numpy as np
import pytest

from app.research import historical_oos as source
from app.research import multiseason_stability as research


def _small_historical_corpus(monkeypatch, per_league=12):
    # Retain original four-competition schema; lower only expected row counts.
    for league in research.LEAGUE_MATCH_COUNTS:
        monkeypatch.setitem(research.LEAGUE_MATCH_COUNTS, league, per_league)
    rows, receipts = [], []
    for s_idx, season in enumerate(research.SEASONS):
        for league in research.LEAGUE_MATCH_COUNTS:
            receipts.append({
                "league": league, "season": season, "sha256": "a" * 64,
                "url": f"https://example.org/{season}/{league}.csv",
                "finished_matches": per_league,
            })
            for day_idx in range(per_league):
                match_day = date(2019 + s_idx, 8, 1) + timedelta(days=day_idx)
                home = f"{league}_A" if day_idx % 2 else f"{league}_B"
                away = f"{league}_B" if day_idx % 2 else f"{league}_A"
                hg = (day_idx + s_idx) % 4
                ag = (day_idx + 2) % 3
                label = 0 if hg > ag else (1 if hg == ag else 2)
                rows.append(source.Fixture(
                    league=league, season=season, match_date=match_day,
                    home=home, away=away, home_goals=hg, away_goals=ag,
                    outcome=label,
                ))
    return rows, receipts


class _MockArtifact:
    artifact_fingerprint = "synthetic-test-only"

    def verify_integrity(self):
        return True


class _FakeFittedModel:
    artifact = None

    def fit(self, train_rows):
        assert train_rows
        self.artifact = _MockArtifact()


def test_four_season_rolling_origin_scoring_never_trains_on_test_year(monkeypatch):
    rows, receipts = _small_historical_corpus(monkeypatch)

    def mock_model(name):
        assert name == "M1_DECAY_365"
        return _FakeFittedModel()

    def mock_probs(model, all_rows, indices):
        assert model.artifact
        return np.tile(np.array([0.46, 0.30, 0.24]), (len(indices), 1))

    monkeypatch.setattr(research, "_structural_model", mock_model)
    monkeypatch.setattr(research, "_structural_probabilities", mock_probs)
    report = research.evaluate_multiseason_stability(rows, receipts)

    assert report["schema_version"] == "research_multiseason_retrospective_oos_v1"
    assert report["status"] == "RETROSPECTIVE_RESEARCH_SENSITIVITY_NOT_FRESH_HOLDOUT"
    assert report["total_oos_fixtures"] == 4 * 4 * 12
    assert report["pooled_raw_challenger_metrics"]["sample_size"] == 192
    assert report["pooled_baseline_metrics"]["sample_size"] == 192
    assert [f["test_season"] for f in report["folds"]] == list(research.TEST_SEASONS)
    assert [f["training_fixtures"] for f in report["folds"]] == [144, 192, 240, 288]
    assert all(f["test_fixtures"] == 48 for f in report["folds"])
    assert all(f["training_latest_date"] < f["test_earliest_date"] for f in report["folds"])
    assert len(report["match_date_clustered_log_loss_skill"]["confidence_interval_95"]) == 2
    assert len(report["research_report_sha256"]) == 64
    assert report["pooled_fixed_logistic_accuracy_diagnostics"]["sample_size"] == 192
    assert report["pooled_fixed_ensemble_accuracy_diagnostics"]["sample_size"] == 192
    assert report["pooled_fixed_ensemble_metrics"]["sample_size"] == 192
    assert len(report["fixed_ensemble_vs_m1_clustered_log_loss_skill"]["confidence_interval_95"]) == 2
    assert report["comparison_selection_policy"] == "NO_TEST_RESULT_SELECTION_NO_PRODUCTION_PROMOTION"
    assert all(f["fixed_logistic_metrics"]["sample_size"] == 48 for f in report["folds"])
    assert all(f["fixed_ensemble_metrics"]["sample_size"] == 48 for f in report["folds"])
    assert report["pooled_challenger_accuracy_diagnostics"]["sample_size"] == 192
    assert report["pooled_baseline_accuracy_diagnostics"]["sample_size"] == 192
    assert len(report["pooled_challenger_accuracy_diagnostics"]["confidence_slices"]) == 4
    assert all("challenger_accuracy_diagnostics" in fold for fold in report["folds"])


def test_incomplete_and_duplicated_historical_provenance_rejected(monkeypatch):
    rows, receipts = _small_historical_corpus(monkeypatch)
    with pytest.raises(ValueError, match="COVERAGE_INCOMPLETE"):
        research.evaluate_multiseason_stability(rows[:-1], receipts)
    repeated = [dict(x) for x in receipts]
    repeated[-1] = dict(repeated[0])
    with pytest.raises(ValueError, match="PROVENANCE_DUPLICATED"):
        research.evaluate_multiseason_stability(rows, repeated)


def test_current_season_results_cannot_leak_into_prior_season_model(monkeypatch):
    rows, receipts = _small_historical_corpus(monkeypatch)
    trained_max_years = []

    class ChronologyModel(_FakeFittedModel):
        def fit(self, train_rows):
            trained_max_years.append(max(x.kickoff_utc.year for x in train_rows))
            super().fit(train_rows)

    monkeypatch.setattr(research, "_structural_model", lambda name: ChronologyModel())
    monkeypatch.setattr(
        research, "_structural_probabilities",
        lambda model, all_rows, idx: np.tile([0.46, 0.30, 0.24], (len(idx), 1)),
    )
    research.evaluate_multiseason_stability(rows, receipts)
    assert trained_max_years == [2021, 2022, 2023, 2024]


def test_accuracy_diagnostics_coverage_and_abstention_are_consistent():
    labels = np.array([0, 1, 2, 1])
    probs = np.array([
        [0.80, 0.10, 0.10],
        [0.20, 0.60, 0.20],
        [0.40, 0.35, 0.25],
        [0.45, 0.40, 0.15],
    ])
    result = research._accuracy_diagnostics(labels, probs)
    assert result["sample_size"] == 4
    assert result["correct"] == 2
    assert result["accuracy"] == 0.5
    assert [x["selected"] for x in result["confidence_slices"]] == [3, 2, 2, 2]
    assert result["confidence_slices"][0]["correct"] == 2
    assert result["confidence_slices"][1]["accuracy"] == 1.0
    assert all(x["selected"] + x["abstained"] == 4 for x in result["confidence_slices"])


def test_accuracy_diagnostics_rejects_invalid_probability_vectors():
    with pytest.raises(ValueError, match="INVALID_ACCURACY_DIAGNOSTIC_INPUT"):
        research._accuracy_diagnostics(np.array([0]), np.array([[0.9, 0.9, -0.8]]))
    with pytest.raises(ValueError, match="INVALID_ACCURACY_DIAGNOSTIC_INPUT"):
        research._accuracy_diagnostics(np.array([3]), np.array([[0.2, 0.3, 0.5]]))


def test_fixed_logistic_and_blend_fits_never_see_test_season(monkeypatch):
    rows, receipts = _small_historical_corpus(monkeypatch)
    fitted_epochs = []

    class SpyLogistic:
        def fit(self, features, labels):
            fitted_epochs.append((len(features), len(labels)))

    monkeypatch.setattr(research, "_model", lambda name: SpyLogistic())
    monkeypatch.setattr(research, "_probabilities", lambda model, features: np.tile([0.30, 0.30, 0.40], (len(features), 1)))
    monkeypatch.setattr(research, "_structural_model", lambda name: _FakeFittedModel())
    monkeypatch.setattr(research, "_structural_probabilities",
                        lambda model, all_rows, idx: np.tile([0.46, 0.30, 0.24], (len(idx), 1)))
    report = research.evaluate_multiseason_stability(rows, receipts)
    assert fitted_epochs == [(144, 144), (192, 192), (240, 240), (288, 288)]
    assert report["pooled_fixed_ensemble_accuracy_diagnostics"]["sample_size"] == 192
    assert report["fixed_ensemble_m1_weight"] == 0.5
