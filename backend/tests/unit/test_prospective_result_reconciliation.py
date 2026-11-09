"""Result verification without retroactive forecasts or invented scores."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
import json
import math

import pytest

from app.research.prospective_results import (
    parse_completed_result_csv,
    reconcile_prospective_results,
    verify_original_forecast_ledger,
)


def pre_match_ledger():
    forecast = {
        "fixture_sha256": "",
        "league": "E0",
        "provider_home": "Man City",
        "provider_away": "Fulham",
        "expected_calendar_date": "2026-11-21",
        "prob_home": 0.5,
        "prob_draw": 0.28,
        "prob_away": 0.22,
        "lambda_home": 1.6,
        "lambda_away": 0.9,
        "prob_over_2_5": 0.49,
        "prob_btts_yes": 0.54,
        "status": "SHADOW_ONLY_UNCALIBRATED_WAITING_FOR_MATCH",
    }
    token = "E0|2627|2026-11-21|Man City|Fulham"
    forecast["fixture_sha256"] = hashlib.sha256(token.encode()).hexdigest()
    body = {
        "status": "RESEARCH_SHADOW_NOT_CERTIFIED_OR_COMMERCIAL",
        "fixture_source_repo": "oladeeayo/open-matchday",
        "fixture_source_commit": "a" * 40,
        "generated_at_utc": "2026-11-09T12:15:00+00:00",
        "predictions": [forecast],
    }
    body["ledger_sha256"] = hashlib.sha256(
        json.dumps(body, sort_keys=True).encode()
    ).hexdigest()
    return body


def result(league="E0", date="2026-11-21", home="Man City", away="Fulham", goals=(2, 1)):
    return {
        (league, datetime.fromisoformat(date).date(), home, away): {
            "home_goals": goals[0],
            "away_goals": goals[1],
            "outcome": 0 if goals[0] > goals[1] else 1 if goals[0] == goals[1] else 2,
            "result_source": "research-upstream",
        }
    }


def test_reconcile_only_after_full_match_day_and_preserve_forecast():
    source = pre_match_ledger()
    original = deepcopy(source)
    before_match = reconcile_prospective_results(
        source, "b" * 40, {}, [], as_of=datetime(2026, 11, 20, tzinfo=UTC)
    )
    assert before_match["evaluated_count"] == 0
    assert before_match["evaluation_counts"]["PENDING_MATCH_DATE_NOT_FULLY_ELAPSED"] == 1
    assert before_match["observed_score_metrics"] is None

    same_day = reconcile_prospective_results(
        source, "b" * 40, result(), [], as_of=datetime(2026, 11, 21, 23, tzinfo=UTC)
    )
    assert same_day["evaluated_count"] == 0

    evaluated = reconcile_prospective_results(
        source, "b" * 40, result(), [], as_of=datetime(2026, 11, 22, 0, 1, tzinfo=UTC)
    )
    assert evaluated["evaluated_count"] == 1
    assert evaluated["observed_score_metrics"]["sample_size"] == 1
    assert evaluated["observed_goal_market_metrics"]["over_2_5_binary_brier"] > 0
    assert evaluated["calibration_sample_sufficient"] is False
    assert evaluated["evaluation_rows"][0]["evaluation_status"] == "EVALUATED_AFTER_FINAL_RESULT"
    assert evaluated["evaluation_rows"][0]["actual_score_probability"] > 0
    assert source == original


def test_quarantine_later_rescheduled_match_without_rekeying():
    forecast = pre_match_ledger()
    later_result = result(date="2026-11-23")
    audited = reconcile_prospective_results(
        forecast, "c" * 40, later_result, [],
        as_of=datetime(2026, 11, 24, tzinfo=UTC),
    )
    assert audited["evaluated_count"] == 0
    assert audited["evaluation_rows"][0]["evaluation_status"] == (
        "REVIEW_POSSIBLE_RESCHEDULE_OR_SOURCE_CORRECTION"
    )
    assert audited["evaluation_rows"][0]["new_result_date_candidates"] == ["2026-11-23"]


def test_ledger_tampering_and_after_kickoff_forgery_rejected():
    doc = pre_match_ledger()
    assert verify_original_forecast_ledger(doc) == doc["ledger_sha256"]
    doc["predictions"][0]["prob_draw"] = 0.50
    with pytest.raises(ValueError, match="PREMATCH_FORECAST_LEDGER_TAMPERED"):
        verify_original_forecast_ledger(doc)

    after = pre_match_ledger()
    after["generated_at_utc"] = "2026-11-21T12:00:00+00:00"
    after.pop("ledger_sha256")
    after["ledger_sha256"] = hashlib.sha256(
        json.dumps(after, sort_keys=True).encode()
    ).hexdigest()
    with pytest.raises(ValueError, match="FORECAST_NOT_ISSUED_BEFORE_MATCH_DATE"):
        verify_original_forecast_ledger(after)


def test_real_result_csv_requires_actual_fulltime_and_deduplication():
    header = "gw,date,time,home,away,ft_home,ft_away,ft_result,source\n"
    body = "13,2026-11-21,16:00,Man City,Fulham,2,1,H,fixturedl\n"
    parsed = parse_completed_result_csv((header + body).encode(), "E0")
    assert len(parsed) == 1
    assert next(iter(parsed.values()))["outcome"] == 0
    with pytest.raises(ValueError, match="RESULT_SCORE_MISMATCH"):
        parse_completed_result_csv(
            (header + body.replace("2,1,H", "2,1,A")).encode(), "E0",
        )
    with pytest.raises(ValueError, match="DUPLICATE_COMPLETED_RESULT"):
        parse_completed_result_csv((header + body + body).encode(), "E0")
    with pytest.raises(ValueError, match="INVALID_OFFICIAL_RESULT_PAYLOAD"):
        parse_completed_result_csv(b"<html>Not Found</html>", "E0")
