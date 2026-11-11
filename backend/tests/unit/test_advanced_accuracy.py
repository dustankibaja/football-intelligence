"""Causal form/strength features and fixed rolling 1X2 research regression."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import numpy as np
import pytest

from app.research.historical_oos import Fixture
from app.research import advanced_accuracy as research


def _corpus(monkeypatch, per_league=12):
    for league in research.LEAGUE_MATCH_COUNTS:
        monkeypatch.setitem(research.LEAGUE_MATCH_COUNTS, league, per_league)
    fixtures, receipts = [], []
    for s_idx, season in enumerate(research.SEASONS):
        for league in research.LEAGUE_MATCH_COUNTS:
            receipts.append({
                "league": league, "season": season, "sha256": "a" * 64,
                "url": f"https://test.example/{season}/{league}.csv",
            })
            for i in range(per_league):
                day = date(2019 + s_idx, 8, 1) + timedelta(days=i)
                a, b = (f"{league}_A", f"{league}_B") if i % 2 else (f"{league}_B", f"{league}_A")
                hg, ag = (i + s_idx) % 4, (i + 2 * s_idx) % 3
                y = 0 if hg > ag else 1 if hg == ag else 2
                fixtures.append(Fixture(league, season, day, a, b, hg, ag, y))
    return fixtures, receipts


def test_causal_strength_features_schema_are_finite_and_have_no_missing_dates(monkeypatch):
    fixtures, receipts = _corpus(monkeypatch)
    x = research.build_causal_strength_form_features(fixtures)
    assert x.shape == (len(fixtures), 26)
    assert np.isfinite(x).all()
    assert np.all(x[:, 20:23] >= 0)
    np.testing.assert_allclose(x[:, 20:23].sum(axis=1), 1.0, atol=1e-10)
    assert research.TOTAL_RESEARCH_FEATURE_COUNT == 50
    assert len(receipts) == 28


def test_future_and_same_day_result_mutations_cannot_influence_prematch_features(monkeypatch):
    fixtures, _ = _corpus(monkeypatch)
    before = research.build_causal_strength_form_features(fixtures)
    mutated = list(fixtures)
    original = mutated[0]
    other_label = 2 if original.outcome != 2 else 0
    mutated[0] = replace(original, home_goals=0 if other_label == 2 else 2,
                         away_goals=2 if other_label == 2 else 0, outcome=other_label)
    after = research.build_causal_strength_form_features(mutated)
    same_day = fixtures[0].match_date
    for i, match in enumerate(fixtures):
        if match.match_date <= same_day:
            np.testing.assert_array_equal(before[i], after[i])
        if match.league != original.league:
            np.testing.assert_array_equal(before[i], after[i])
    assert any(
        not np.array_equal(before[i], after[i])
        for i, match in enumerate(fixtures)
        if match.league == original.league and match.match_date > same_day
    )


def test_future_unplayed_fixtures_are_never_added_to_team_state(monkeypatch):
    fixtures, _ = _corpus(monkeypatch)
    latest = max(f.match_date for f in fixtures)
    planned = replace(fixtures[-1], match_date=latest + timedelta(days=4),
                      home_goals=-1, away_goals=-1, outcome=-1)
    future = replace(fixtures[-1], match_date=latest + timedelta(days=5),
                     home_goals=-1, away_goals=-1, outcome=-1)
    with_extra = research.build_causal_strength_form_features(fixtures + [planned, future])
    without_extra = research.build_causal_strength_form_features(fixtures + [future])
    np.testing.assert_array_equal(with_extra[-1], without_extra[-1])
    np.testing.assert_array_equal(with_extra[:len(fixtures)], without_extra[:len(fixtures)])
    assert np.isfinite(with_extra).all()


def test_fail_closed_on_corrupted_scoring_or_nonfinite_fixture_history(monkeypatch):
    fixtures, _ = _corpus(monkeypatch)
    invalid = list(fixtures)
    invalid[0] = replace(fixtures[0], home_goals=0, away_goals=0, outcome=2)
    with pytest.raises(ValueError, match="INCONSISTENT_ADVANCED_RESEARCH_HISTORY_OUTCOME"):
        research.build_causal_strength_form_features(invalid)
    invalid[0] = replace(fixtures[0], home_goals=-1, away_goals=-1, outcome=0)
    with pytest.raises(ValueError, match="INVALID_ADVANCED_RESEARCH_HISTORY_RESULT"):
        research.build_causal_strength_form_features(invalid)


def test_same_fixture_full_coverage_rolling_experiment_does_not_train_on_test_seasons(monkeypatch):
    fixtures, receipts = _corpus(monkeypatch)
    reports = research.evaluate_advanced_accuracy(fixtures, receipts)
    assert reports["status"] == "EXPLORATORY_RETROSPECTIVE_NO_MODEL_PROMOTION"
    assert reports["reference_corpus"]["test_fixtures"] == 192
    assert reports["feature_contract"]["old_input_count"] == 24
    assert reports["feature_contract"]["new_input_count"] == 50
    assert reports["feature_contract"]["strict_previous_calendar_date"] is True
    assert [f["training_fixtures"] for f in reports["folds"]] == [144, 192, 240, 288]
    assert [f["test_fixtures"] for f in reports["folds"]] == [48] * 4
    assert all(f["training_latest_date"] < f["test_earliest_date"] for f in reports["folds"])
    for key, stats in reports["pooled"].items():
        assert stats["accuracy"]["sample_size"] == 192
        assert stats["metrics"]["sample_size"] == 192
        assert stats["accuracy"]["accuracy"] == stats["accuracy"]["correct"] / 192
    for key, details in reports["paired_vs_old_elo"].items():
        assert details["accuracy_difference"]["sample_size"] == 192
        assert len(details["accuracy_difference"]["confidence_interval_95"]) == 2
        assert len(details["log_loss_skill"]["confidence_interval_95"]) == 2
    assert len(reports["research_report_sha256"]) == 64


def test_duplicate_source_receipts_and_missing_matches_fail_before_training(monkeypatch):
    fixtures, receipts = _corpus(monkeypatch)
    with pytest.raises(ValueError, match="SOURCE_COVERAGE_INCOMPLETE"):
        research.evaluate_advanced_accuracy(fixtures[:-1], receipts)
    duplicates = [dict(x) for x in receipts]
    duplicates[-1] = dict(duplicates[0])
    with pytest.raises(ValueError, match="DUPLICATE_SOURCE_RECEIPT"):
        research.evaluate_advanced_accuracy(fixtures, duplicates)
