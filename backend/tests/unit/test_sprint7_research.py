"""Fast offline tests for the isolated research acquisition and model gates."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.research.benchmark import benchmark, metrics
from app.research.data import Match, parse_source, source_url
from app.research.models import (
    FeatureRow, build_point_in_time_rows, goal_probabilities, selective_decision,
)


def _csv(n=250):
    rows = ["Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,B365H"]
    for i in range(n):
        day = date(2021, 8, 1) + timedelta(days=i)
        hg, ag = (i % 4), (i % 3)
        outcome = "H" if hg > ag else "D" if hg == ag else "A"
        rows.append(f"E0,{day:%d/%m/%Y},Home{i % 20},Away{i % 20},{hg},{ag},{outcome},1.90")
    return ("\n".join(rows) + "\n").encode()


def _match(day, home, away, hg, ag):
    return Match("EPL", "2021-22", day, home, away, hg, ag)


def test_source_url_is_allowlisted_and_deterministic():
    assert source_url("EPL", "2024-25").endswith("/2425/E0.csv")
    assert source_url("BUNDESLIGA", "2025-26").endswith("/2526/D1.csv")
    with pytest.raises(ValueError):
        source_url("EPL", "2026-27")
    with pytest.raises(ValueError):
        source_url("../private", "2024-25")


def test_csv_requires_complete_score_consistency_and_does_not_use_odds():
    matches = parse_source(_csv(), "EPL", "2021-22")
    assert len(matches) == 250
    assert matches[0].home == "Home0"
    assert not hasattr(matches[0], "B365H")
    with pytest.raises(ValueError, match="result mismatch"):
        parse_source(_csv().replace(b",1.90", b",1.90").replace(
            b",0,0,D,1.90", b",0,0,H,1.90", 1), "EPL", "2021-22")
    with pytest.raises(ValueError, match="coverage"):
        parse_source(_csv(10), "EPL", "2021-22")
    with pytest.raises(ValueError, match="HTML"):
        parse_source(b"<html>not a CSV</html>", "EPL", "2021-22")


def test_same_date_outcomes_cannot_leak_into_features():
    day = date(2021, 8, 1)
    first = _match(day, "A", "B", 8, 0)
    second = _match(day, "C", "D", 1, 1)
    following = _match(day + timedelta(days=1), "C", "A", 1, 0)
    rows = build_point_in_time_rows([first, second, following])
    only_second = build_point_in_time_rows([second])
    second_row = next(r for r in rows if r.match == second)
    assert second_row.probabilities == only_second[0].probabilities
    assert second_row.history_count == 0
    following_row = next(r for r in rows if r.match == following)
    assert following_row.history_count == 2
    assert following_row.probabilities["empirical_poisson"] != second_row.probabilities["empirical_poisson"]


def test_goal_distribution_is_normalized_and_dixon_coles_changes_draw():
    standard = goal_probabilities(1.4, 1.2)
    adjusted = goal_probabilities(1.4, 1.2, -.08)
    assert sum(standard) == pytest.approx(1.0)
    assert sum(adjusted) == pytest.approx(1.0)
    assert adjusted[1] > standard[1]


def test_research_selective_policy_abstains_without_sufficient_evidence():
    m = _match(date(2021, 8, 1), "A", "B", 1, 0)
    row = FeatureRow(m, (0.0,), {}, 90, 8, 8)
    assert selective_decision(row, (.65, .20, .15)).startswith("WATCH_ONLY")
    assert selective_decision(row, (.60, .20, .20)).startswith("ABSTAIN")
    assert selective_decision(row, (.61, .30, .09)).startswith("ABSTAIN")
    assert selective_decision(FeatureRow(m, (0.0,), {}, 90, 1, 8),
                              (.65, .20, .15)).startswith("ABSTAIN")


def test_metrics_are_scored_only_against_known_heldout_labels():
    m = _match(date(2021, 8, 1), "A", "B", 1, 0)
    row = FeatureRow(m, (0.0,), {}, 90, 8, 8)
    result = metrics([row], [(.7, .2, .1)])
    assert result["accuracy"] == 1.0
    assert result["watchOnlyN"] == 1
    assert result["watchOnlyAccuracy"] == 1.0
    with pytest.raises(ValueError, match="misaligned"):
        metrics([row], [])


def test_benchmark_fails_closed_on_insufficient_coverage():
    row = FeatureRow(_match(date(2021, 8, 1), "A", "B", 1, 0),
                     (0.0,), {}, 0, 0, 0)
    with pytest.raises(ValueError, match="Insufficient chronological fold"):
        benchmark([row])
