"""Mechanical tests: temperature fitting never reads the scored season."""

from datetime import date, timedelta
import numpy as np
import pytest
from app.research import historical_oos as history
from app.research import calibration_transfer as trial


def _toy_corpus(monkeypatch, each=24):
    for league in trial.LEAGUE_MATCH_COUNTS:
        monkeypatch.setitem(trial.LEAGUE_MATCH_COUNTS, league, each)
    fixtures = []
    manifests = []
    for season_idx, season in enumerate(trial.SEASONS):
        for league in trial.LEAGUE_MATCH_COUNTS:
            manifests.append({
                "league": league, "season": season,
                "sha256": "a" * 64, "finished_matches": each,
                "url": f"https://example.org/{season}/{league}.csv",
            })
            for k in range(each):
                home_goals = (k + season_idx) % 3
                away_goals = (k * 2 + season_idx) % 4
                fixtures.append(history.Fixture(
                    league=league,
                    season=season,
                    match_date=date(2019 + season_idx, 8, 1) + timedelta(days=k),
                    home=f"{league}_Home{k}",
                    away=f"{league}_Away{k}",
                    home_goals=home_goals,
                    away_goals=away_goals,
                    outcome=(0 if home_goals > away_goals else
                             1 if home_goals == away_goals else 2),
                ))
    return fixtures, manifests


class _Artifact:
    artifact_fingerprint = "test-only"
    def verify_integrity(self):
        return True


class _Fitted:
    artifact = None
    def __init__(self, training_last_years):
        self.trained_years = training_last_years
    def fit(self, rows):
        self.trained_years.append(max(row.kickoff_utc.year for row in rows))
        self.artifact = _Artifact()


def test_temperature_fit_is_separate_from_every_later_test_season(monkeypatch):
    rows, manifests = _toy_corpus(monkeypatch)
    years = []
    monkeypatch.setattr(trial, "_structural_model", lambda name: _Fitted(years))
    monkeypatch.setattr(
        trial, "_structural_probabilities",
        lambda model, all_rows, indices: np.tile([0.46, 0.30, 0.24], (len(indices), 1)),
    )
    report = trial.evaluate_temporal_calibration_transfer(rows, manifests)
    assert years == [2021, 2022, 2023]
    assert report["test_seasons"] == ["2324", "2425", "2526"]
    assert report["total_distinct_test_fixtures"] == 3 * 4 * 24
    assert report["pooled_calibrated"]["sample_size"] == 288
    assert [f["training_count"] for f in report["folds"]] == [288, 384, 480]
    for fold in report["folds"]:
        assert (
            fold["latest_training_date"] < fold["earliest_tuning_date"]
            <= fold["latest_tuning_date"] < fold["earliest_test_date"]
        )
        assert 0.5 <= fold["temperature_fitted_on_tuning_only"] <= 3.0
        assert fold["test_count"] == fold["tune_count"] == 96
        assert fold["selective_fixed_threshold_posthoc"]["threshold"] == 0.60
    assert len(report["research_report_sha256"]) == 64


def test_fails_on_incomplete_or_duplicate_provider_provenance(monkeypatch):
    rows, manifests = _toy_corpus(monkeypatch)
    with pytest.raises(ValueError, match="COVERAGE_MISMATCH"):
        trial.evaluate_temporal_calibration_transfer(rows[:-1], manifests)
    invalid = [dict(item) for item in manifests]
    invalid[-1] = dict(invalid[0])
    with pytest.raises(ValueError, match="PROVENANCE_INVALID"):
        trial.evaluate_temporal_calibration_transfer(rows, invalid)
