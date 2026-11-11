"""Paired result assessment: no source invention, no forecast editing."""
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256

import pytest

from app.research import prospective_accuracy_challenger as challenger
from app.research import prospective_accuracy_evaluator as evaluator


def _ledger():
    issued = datetime(2026, 11, 11, 8, tzinfo=UTC)
    identity = "E0|2627|2026-11-21|Man City|Fulham"
    ident = sha256(identity.encode()).hexdigest()
    row = {
        "fixture_sha256": ident, "league": "E0",
        "provider_home": "Man City", "provider_away": "Fulham",
        "expected_calendar_date": "2026-11-21",
        "baseline_prob_home": 0.5, "baseline_prob_draw": 0.25, "baseline_prob_away": 0.25,
        "candidate_prob_home": 0.2, "candidate_prob_draw": 0.3, "candidate_prob_away": 0.5,
        "status": "PAIRED_PREMATCH_SHADOW_UNSCORED",
    }
    ledger = {
        "schema_version": "independent_prospective_elo_challenger_1.0.0",
        "status": "RESEARCH_ONLY_AWAITING_AUTHENTIC_POSTMATCH_RESULTS",
        "generated_at_utc": issued.isoformat(),
        "model": {"publication_eligible": False},
        "counts": {"baseline_future_qualified": 1, "challenger_issued": 1},
        "predictions": [row],
    }
    ledger["ledger_sha256"] = challenger._digest(ledger)
    return ledger


def _receipt(ledger):
    return {
        "fixture_sha256": ledger["predictions"][0]["fixture_sha256"],
        "league": "E0", "home": "Man City", "away": "Fulham",
        "home_goals": 1, "away_goals": 2,
        "status": "FINISHED", "result_time_precision": "DATE_ONLY",
        "final_result_date": "2026-11-21",
        "retrieved_at_utc": "2026-11-23T08:00:00+00:00",
        "source_url": "https://www.football-data.co.uk/mmz4281/2627/E0.csv",
        "source_payload_sha256": "f" * 64,
    }


def test_prospective_paired_accuracy_report_preserves_original_and_counts():
    ledger = _ledger()
    original = deepcopy(ledger)
    report = evaluator.evaluate_paired_results(
        ledger, [_receipt(ledger)],
        evaluated_at_utc=datetime(2026, 11, 23, 9, tzinfo=UTC),
    )
    assert ledger == original
    assert report["finished_evaluated"] == 1
    assert report["pending_or_unverified"] == 0
    assert report["baseline"]["correct"] == 0
    assert report["candidate"]["correct"] == 1
    assert report["paired_difference_candidate_minus_baseline"]["accuracy_points"] == 1.0
    gate = report["confidence_selection_research"]
    assert gate["threshold_min_top_class_probability"] == 0.5
    assert gate["total_original_fixtures"] == 1
    assert gate["pre_match_selected"] == 1
    assert gate["pre_match_abstained"] == 0
    assert gate["selected_scored_so_far"] == 1
    assert gate["selected_pending_or_unverified"] == 0
    assert gate["selected_accuracy"] == 1.0
    assert gate["same_selected_fixtures_baseline_accuracy"] == 0.0
    assert gate["selected_accuracy_difference"] == 1.0
    assert report["report_sha256"] == challenger._digest({
        k: v for k, v in report.items() if k != "report_sha256"
    })


def test_no_results_reports_pending_not_fake_success():
    ledger = _ledger()
    report = evaluator.evaluate_paired_results(
        ledger, [],
        evaluated_at_utc=datetime(2026, 11, 12, tzinfo=UTC),
    )
    assert report["finished_evaluated"] == 0
    assert report["pending_or_unverified"] == 1
    assert "candidate" not in report
    assert "paired_difference_candidate_minus_baseline" not in report
    gate = report["confidence_selection_research"]
    assert gate["pre_match_selected"] == 1
    assert gate["pre_match_abstained"] == 0
    assert gate["selected_pending_or_unverified"] == 1
    assert gate["selected_scored_so_far"] == 0
    assert gate["selected_accuracy"] is None
    assert gate["same_selected_fixtures_baseline_accuracy"] is None
    assert gate["scored_result_cohort_coverage"] is None


def test_rejects_edited_future_prediction_and_ambiguous_scores():
    ledger = _ledger()
    tampered = deepcopy(ledger)
    tampered["predictions"][0]["candidate_prob_home"] = 0.9
    with pytest.raises(ValueError, match="PAIRED_FORECAST_LEDGER_TAMPERED"):
        evaluator.evaluate_paired_results(
            tampered, [], evaluated_at_utc=datetime(2026, 11, 23, tzinfo=UTC)
        )
    for receipts, pattern in [
        ([_receipt(ledger)] * 2, "DUPLICATED"),
        ([dict(_receipt(ledger), final_result_date="2026-11-11")], "TIME_NOT_PROSPECTIVE"),
        ([dict(_receipt(ledger), source_url="https://fake.example/x.csv")], "NOT_AUTHENTIC_SOURCE_RECEIPT"),
        ([dict(_receipt(ledger), home_goals=-1)], "INVALID_SCORE"),
    ]:
        with pytest.raises(ValueError, match=pattern):
            evaluator.evaluate_paired_results(
                ledger, receipts, evaluated_at_utc=datetime(2026, 11, 23, 9, tzinfo=UTC)
            )


def test_collect_receipts_from_independent_current_season_csv_not_predictions():
    ledger = _ledger()
    empty = b"Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\n"
    finished = empty + b"21/11/2026,Man City,Fulham,1,2,A\n"
    def fetch(url):
        return finished if url.endswith("/E0.csv") else empty
    receipts = evaluator.collect_paired_result_receipts(
        ledger,
        retrieved_at_utc=datetime(2026, 11, 23, 8, tzinfo=UTC),
        fetch_bytes=fetch,
    )
    assert len(receipts) == 1
    assert receipts[0]["source_payload_sha256"] == sha256(finished).hexdigest()
    assert receipts[0]["fixture_sha256"] == ledger["predictions"][0]["fixture_sha256"]
    assert receipts[0]["result_time_precision"] == "DATE_ONLY"


def test_confidence_selection_does_not_erase_low_confidence_finished_results():
    ledger = _ledger()
    low = ledger["predictions"][0]
    low.update(candidate_prob_home=0.34, candidate_prob_draw=0.33,
               candidate_prob_away=0.33)
    ledger.pop("ledger_sha256")
    ledger["ledger_sha256"] = challenger._digest(ledger)
    assessed = evaluator.evaluate_paired_results(
        ledger, [_receipt(ledger)],
        evaluated_at_utc=datetime(2026, 11, 23, 9, tzinfo=UTC),
    )
    assert assessed["finished_evaluated"] == 1
    assert assessed["candidate"]["n"] == 1
    gate = assessed["confidence_selection_research"]
    assert gate["pre_match_selected"] == 0
    assert gate["pre_match_abstained"] == 1
    assert gate["issued_cohort_coverage"] == 0.0
    assert gate["abstained_scored_so_far"] == 1
    assert gate["selected_scored_so_far"] == 0
    assert gate["selected_accuracy"] is None
    assert gate["same_selected_fixtures_baseline_accuracy"] is None
    assert gate["selected_pending_or_unverified"] == 0


def test_confidence_selection_fingerprint_is_stable_and_does_not_edit_frozen_ledger():
    ledger = _ledger()
    frozen = deepcopy(ledger)
    first = evaluator.evaluate_paired_results(
        ledger, [], evaluated_at_utc=datetime(2026, 11, 12, tzinfo=UTC),
    )
    second = evaluator.evaluate_paired_results(
        ledger, [], evaluated_at_utc=datetime(2026, 11, 12, tzinfo=UTC),
    )
    assert ledger == frozen
    assert first["confidence_selection_research"] == second["confidence_selection_research"]
    assert first["confidence_selection_research"]["selected_fixture_ids_sha256"]
    assert first["report_sha256"] == second["report_sha256"]
