"""Tests for causal date-only football features and immutable research sourcing."""
from datetime import date
import numpy as np
import pytest

from app.research import historical_oos as research


def _fixture(day, home, away, home_goals, away_goals, league="E0"):
    outcome = 0 if home_goals > away_goals else 1 if home_goals == away_goals else 2
    return research.Fixture(
        league=league, season="1920", match_date=day, home=home, away=away,
        home_goals=home_goals, away_goals=away_goals, outcome=outcome,
    )


def test_all_same_day_fixtures_use_pre_date_history_only():
    day_1 = date(2020, 1, 1)
    day_2 = date(2020, 1, 2)
    original = [
        _fixture(day_1, "Arsenal", "Chelsea", 1, 0),
        _fixture(day_1, "Liverpool", "Man City", 2, 2),
        _fixture(day_2, "Arsenal", "Liverpool", 2, 0),
    ]
    changed = [
        _fixture(day_1, "Arsenal", "Chelsea", 10, 0),
        _fixture(day_1, "Liverpool", "Man City", 0, 5),
        _fixture(day_2, "Arsenal", "Liverpool", 2, 0),
    ]
    first_x, _ = research.build_pre_match_features(original)
    changed_x, _ = research.build_pre_match_features(changed)
    assert first_x.shape == (3, 20)
    assert np.array_equal(first_x[:2], changed_x[:2])
    assert not np.array_equal(first_x[2], changed_x[2])


def test_later_score_changes_cannot_change_earlier_features():
    earlier = _fixture(date(2020, 2, 1), "Arsenal", "Chelsea", 1, 0)
    next_game = _fixture(date(2020, 2, 5), "Chelsea", "Arsenal", 2, 0)
    modified = _fixture(date(2020, 2, 5), "Chelsea", "Arsenal", 0, 7)
    a, _ = research.build_pre_match_features([earlier, next_game])
    b, _ = research.build_pre_match_features([earlier, modified])
    assert np.array_equal(a, b)


def test_invalid_csv_does_not_silently_impute_results(monkeypatch):
    monkeypatch.setitem(research.LEAGUE_MATCH_COUNTS, "E0", 2)
    header = "Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\n"
    first = "01/08/2020,Home,Away,1,0,H\n"
    second = "02/08/2020,Other,Third,0,2,A\n"
    assert len(research.parse_finished_csv((header + first + second).encode(), "E0", "2021")) == 2
    with pytest.raises(ValueError, match="MISSING_FINAL_RESULT"):
        research.parse_finished_csv((header + first + "02/08/2020,Other,Third,,,\n").encode(), "E0", "2021")
    with pytest.raises(ValueError, match="RESULT_SCORE_MISMATCH"):
        research.parse_finished_csv((header + first + "02/08/2020,Other,Third,0,2,H\n").encode(), "E0", "2021")
    with pytest.raises(ValueError, match="DUPLICATE"):
        research.parse_finished_csv((header + first + "02/08/2020,Home,Away,1,0,H\n").encode(), "E0", "2021")
    with pytest.raises(ValueError, match="COVERAGE_FAILED"):
        research.parse_finished_csv((header + first).encode(), "E0", "2021")
    with pytest.raises(ValueError, match="HTML_NOT_MATCH_CSV"):
        research.parse_finished_csv(b"<html>error</html>", "E0", "2021")


def test_temperature_scaling_preserves_normalization_and_finiteness():
    raw = np.asarray([[0.6, 0.25, 0.15], [0.2, 0.4, 0.4], [0.1, 0.1, 0.8]])
    calibrated = research._temperature(raw, 1.4)
    assert calibrated.shape == raw.shape
    assert np.isfinite(calibrated).all()
    assert np.allclose(calibrated.sum(axis=1), 1)
    assert np.all(calibrated >= 0) and np.all(calibrated <= 1)


def test_sealed_validation_partition_is_predeclared():
    assert research.TUNE_SEASON == "2425"
    assert research.TEST_SEASON == "2526"
    assert "2526" not in research.TRAIN_SEASONS
    assert "2425" not in research.TRAIN_SEASONS
    assert len(research.SEASONS) == 7
