"""Independent current-season provider receipts, date-only finality, zero fabrication."""

from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
import json

import pytest

from app.research.football_data_result_verifier import (
    _parse_source,
    collect_result_receipts,
)
from app.research.prospective_reconciliation import reconcile_prospective_results

ISSUED = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
OBSERVED = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


def _original():
    item = {
        "fixture_sha256": "a" * 64,
        "league": "E0",
        "provider_home": "Spurs",
        "provider_away": "Man Utd",
        "expected_calendar_date": "2026-11-14",
        "prob_home": 0.55,
        "prob_draw": 0.25,
        "prob_away": 0.20,
        "prob_btts_yes": 0.6,
        "prob_over_2_5": 0.45,
        "model_artifact_sha256": "b" * 64,
        "feature_snapshot_sha256": "c" * 64,
        "status": "SHADOW_ONLY_UNCALIBRATED_WAITING_FOR_MATCH",
    }
    ledger = {
        "schema_version": "noncommercial_prospective_shadow_1.0.0",
        "status": "RESEARCH_SHADOW_NOT_CERTIFIED_OR_COMMERCIAL",
        "generated_at_utc": ISSUED.isoformat(),
        "counts": {"forecasts_issued": 1},
        "predictions": [item],
    }
    ledger["ledger_sha256"] = sha256(json.dumps(ledger, sort_keys=True).encode()).hexdigest()
    return ledger


def _source(rows):
    return (
        "Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\n" + rows
    ).encode()


def _get_urls(csv_content):
    def fetch(url):
        if "/E0.csv" in url:
            return csv_content
        return _source("")
    return fetch


def test_provider_scores_reconcile_without_fake_final_whistle():
    ledger = _original()
    original = deepcopy(ledger)
    csv_content = _source("14/11/2026,Tottenham,Man United,2,1,H\n")
    receipts = collect_result_receipts(
        ledger, retrieved_at_utc=OBSERVED, fetch_bytes=_get_urls(csv_content)
    )
    assert len(receipts) == 1
    assert receipts[0]["result_time_precision"] == "DATE_ONLY"
    assert "completed_at_utc" not in receipts[0]
    assert receipts[0]["source_payload_sha256"] == sha256(csv_content).hexdigest()
    assert receipts[0]["source_url"].endswith("/2627/E0.csv")

    report = reconcile_prospective_results(ledger, receipts, evaluated_at_utc=OBSERVED)
    assert report["finished_evaluated"] == 1
    assert report["pending_or_unverified"] == 0
    assert report["overall_1x2"]["sample_size"] == 1
    assert report["result_receipts"][0]["final_result_date"] == "2026-11-14"
    assert ledger == original


def test_unfinished_future_provider_fixture_remains_pending():
    ledger = _original()
    data = _source("14/11/2026,Tottenham,Man United,,,\n")
    receipts = collect_result_receipts(
        ledger, retrieved_at_utc=OBSERVED, fetch_bytes=_get_urls(data)
    )
    assert receipts == []
    assessment = reconcile_prospective_results(ledger, receipts, evaluated_at_utc=OBSERVED)
    assert assessment["finished_evaluated"] == 0
    assert assessment["pending_or_unverified"] == 1


def test_same_day_provider_result_is_not_assumed_final():
    ledger = _original()
    data = _source("14/11/2026,Tottenham,Man United,2,1,H\n")
    receipts = collect_result_receipts(
        ledger,
        retrieved_at_utc=datetime(2026, 11, 14, 20, tzinfo=UTC),
        fetch_bytes=_get_urls(data),
    )
    assert receipts == []


def test_date_only_result_requires_next_utc_calendar_day():
    ledger = _original()
    receipt = collect_result_receipts(
        ledger, retrieved_at_utc=OBSERVED,
        fetch_bytes=_get_urls(_source("14/11/2026,Tottenham,Man United,2,1,H\n")),
    )[0]
    receipt["retrieved_at_utc"] = "2026-11-14T23:59:59+00:00"
    with pytest.raises(ValueError, match="RESULT_TIMELINE"):
        reconcile_prospective_results(
            ledger, [receipt], evaluated_at_utc=OBSERVED
        )


def test_provider_full_time_label_must_match_goals():
    with pytest.raises(ValueError, match="RESULT_AND_FINAL_SCORE_CONTRADICT"):
        _parse_source(_source("14/11/2026,Tottenham,Man United,2,1,A\n"), "E0")


def test_provider_partial_result_is_never_inferred():
    with pytest.raises(ValueError, match="INCOMPLETE_CURRENT_SEASON_RESULT"):
        _parse_source(_source("14/11/2026,Tottenham,Man United,2,,H\n"), "E0")


def test_rescheduled_fixture_cannot_be_automatically_matched_by_club_names():
    ledger = _original()
    receipts = collect_result_receipts(
        ledger, retrieved_at_utc=OBSERVED,
        fetch_bytes=_get_urls(_source("15/11/2026,Tottenham,Man United,2,1,H\n")),
    )
    assert receipts == []


def test_result_provider_duplicate_and_html_fail_closed():
    duplicate = _source(
        "14/11/2026,Tottenham,Man United,2,1,H\n"
        "14/11/2026,Tottenham,Man United,2,1,H\n"
    )
    with pytest.raises(ValueError, match="DUPLICATE_RESULT"):
        _parse_source(duplicate, "E0")
    with pytest.raises(ValueError, match="RESULT_SOURCE_NOT_CSV"):
        _parse_source(b"<html>not csv</html>", "E0")
