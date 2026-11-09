"""Prospective scoring invariants; fake fixtures are test-only, never evidence."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json

import pytest

from app.research.prospective_reconciliation import reconcile_prospective_results

ISSUE = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
LATER = ISSUE + timedelta(days=8)


def _ledger():
    forecast = {
        "fixture_sha256": "a" * 64,
        "league": "E0",
        "provider_home": "Arsenal",
        "provider_away": "Chelsea",
        "expected_calendar_date": "2026-11-14",
        "prob_home": 0.52,
        "prob_draw": 0.29,
        "prob_away": 0.19,
        "prob_btts_yes": 0.54,
        "prob_over_2_5": 0.60,
        "model_artifact_sha256": "b" * 64,
        "feature_snapshot_sha256": "c" * 64,
        "status": "SHADOW_ONLY_UNCALIBRATED_WAITING_FOR_MATCH",
    }
    ledger = {
        "schema_version": "noncommercial_prospective_shadow_1.0.0",
        "status": "RESEARCH_SHADOW_NOT_CERTIFIED_OR_COMMERCIAL",
        "generated_at_utc": ISSUE.isoformat(),
        "counts": {"forecasts_issued": 1},
        "predictions": [forecast],
    }
    ledger["ledger_sha256"] = sha256(
        json.dumps(ledger, sort_keys=True).encode()
    ).hexdigest()
    return ledger


def _result():
    return {
        "fixture_sha256": "a" * 64,
        "league": "E0",
        "home": "Arsenal",
        "away": "Chelsea",
        "home_goals": 2,
        "away_goals": 1,
        "status": "FINISHED",
        "completed_at_utc": "2026-11-14T17:00:00+00:00",
        "retrieved_at_utc": "2026-11-15T08:00:00+00:00",
        "source_url": "https://example.org/verified-result",
        "source_payload_sha256": "d" * 64,
    }


def test_reconcile_scores_only_original_locked_probabilities():
    ledger = _ledger()
    original = deepcopy(ledger)
    report = reconcile_prospective_results(
        ledger, [_result()], evaluated_at_utc=LATER
    )
    assert ledger == original
    assert report["original_forecast_ledger_sha256"] == ledger["ledger_sha256"]
    assert report["finished_evaluated"] == 1
    assert report["pending_or_unverified"] == 0
    assert report["overall_1x2"]["sample_size"] == 1
    assert report["overall_1x2"]["log_loss"] > 0
    assert report["binary_brier"]["btts"] > 0
    assert report["result_receipts"][0]["original_probabilities_1x2"] == [
        0.52, 0.29, 0.19
    ]
    assert report["status"] == "RESEARCH_ONLY_RESULTS_NEED_INDEPENDENT_SOURCE_VERIFICATION"
    assert len(report["report_sha256"]) == 64


def test_pending_predictions_remain_pending_without_fabricating_results():
    ledger = _ledger()
    report = reconcile_prospective_results(
        ledger, [], evaluated_at_utc=LATER
    )
    assert report["finished_evaluated"] == 0
    assert report["pending_or_unverified"] == 1
    assert "overall_1x2" not in report


def test_original_probabilities_cannot_be_rewritten_after_issuance():
    ledger = _ledger()
    ledger["predictions"][0]["prob_home"] = 0.99
    with pytest.raises(ValueError, match="LEDGER_INTEGRITY_FAILED"):
        reconcile_prospective_results(ledger, [_result()], evaluated_at_utc=LATER)


def test_duplicate_and_unknown_results_rejected():
    ledger = _ledger()
    with pytest.raises(ValueError, match="DUPLICATE_RESULT"):
        reconcile_prospective_results(
            ledger, [_result(), _result()], evaluated_at_utc=LATER
        )
    other = _result()
    other["fixture_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="UNMATCHED_RESULT"):
        reconcile_prospective_results(ledger, [other], evaluated_at_utc=LATER)


def test_rescheduled_identity_mismatch_rejected():
    result = _result()
    result["away"] = "Everton"
    with pytest.raises(ValueError, match="RESULT_TEAM_OR_COMPETITION_IDENTITY"):
        reconcile_prospective_results(
            _ledger(), [result], evaluated_at_utc=LATER
        )


def test_pre_issue_result_and_missing_provenance_are_blocked():
    result = _result()
    result["completed_at_utc"] = "2026-11-07T18:00:00+00:00"
    with pytest.raises(ValueError, match="RESULT_TIMELINE"):
        reconcile_prospective_results(
            _ledger(), [result], evaluated_at_utc=LATER
        )
    result = _result()
    result["source_payload_sha256"] = "unverified"
    with pytest.raises(ValueError, match="PROVENANCE_MISSING"):
        reconcile_prospective_results(
            _ledger(), [result], evaluated_at_utc=LATER
        )


def test_unfinished_score_or_postdated_verification_blocked():
    result = _result()
    result["status"] = "POSTPONED"
    with pytest.raises(ValueError, match="NONFINAL_RESULT"):
        reconcile_prospective_results(
            _ledger(), [result], evaluated_at_utc=LATER
        )
    result = _result()
    result["retrieved_at_utc"] = "2026-11-20T09:00:00+00:00"
    with pytest.raises(ValueError, match="RESULT_TIMELINE"):
        reconcile_prospective_results(
            _ledger(), [result], evaluated_at_utc=LATER
        )
