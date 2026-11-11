"""Independent prospective accuracy challenger: genuine pre-match pairing invariants."""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256

import numpy as np
import pytest

from app.research.historical_oos import Fixture
from app.research import prospective_accuracy_challenger as candidate


def _prepared():
    as_of = datetime(2026, 11, 11, 8, 0, tzinfo=UTC)
    history = []
    for i, outcome in enumerate((0, 1, 2, 0, 1, 2)):
        if outcome == 0:
            gh, ga = 2, 0
        elif outcome == 1:
            gh, ga = 1, 1
        else:
            gh, ga = 0, 2
        history.append(Fixture(
            "E0", "2526", date(2026, 3, 1) + timedelta(days=3 * i),
            "Man City", "Fulham", gh, ga, outcome,
        ))
    receipt = [{"league": "E0", "season": "2526", "sha256": "a" * 64}]
    day = date(2026, 11, 21)
    token = f"E0|2627|{day}|Man City|Fulham"
    fixture_id = sha256(token.encode()).hexdigest()
    baseline = {
        "schema_version": "noncommercial_prospective_shadow_1.0.0",
        "status": "RESEARCH_SHADOW_NOT_CERTIFIED_OR_COMMERCIAL",
        "generated_at_utc": as_of.isoformat(),
        "historical_source_receipts": receipt,
        "fixture_source_commit": "b" * 40,
        "upstream_source_receipts": [{"sha256": "c" * 64}],
        "model": {"training_rows": len(history), "publication_eligible": False},
        "counts": {"blocked": 0},
        "predictions": [{
            "fixture_sha256": fixture_id, "league": "E0",
            "provider_home": "Man City", "provider_away": "Fulham",
            "expected_calendar_date": day.isoformat(),
            "prob_home": 0.5, "prob_draw": 0.25, "prob_away": 0.25,
        }],
    }
    baseline["ledger_sha256"] = candidate._digest(baseline)
    return history, receipt, baseline, as_of


def test_new_elo_shadow_is_strictly_prematch_and_paired_without_rewriting_baseline():
    history, receipts, baseline, as_of = _prepared()
    original = deepcopy(baseline)
    out = candidate.issue_accuracy_challenger(history, receipts, baseline, as_of=as_of)
    assert baseline == original
    assert out["status"] == "RESEARCH_ONLY_AWAITING_AUTHENTIC_POSTMATCH_RESULTS"
    assert out["model"]["training_rows"] == len(history)
    assert out["model"]["publication_eligible"] is False
    assert out["model"]["input_feature_count"] == 24
    assert out["counts"]["challenger_issued"] == out["counts"]["baseline_future_qualified"] == 1
    row = out["predictions"][0]
    assert row["fixture_sha256"] == baseline["predictions"][0]["fixture_sha256"]
    assert [row["baseline_prob_home"], row["baseline_prob_draw"], row["baseline_prob_away"]] == [0.5, 0.25, 0.25]
    candidate_probs = [row["candidate_prob_home"], row["candidate_prob_draw"], row["candidate_prob_away"]]
    assert all(np.isfinite(candidate_probs))
    assert all(0 <= p <= 1 for p in candidate_probs)
    assert abs(sum(candidate_probs) - 1) < 1e-6
    assert len(row["candidate_model_sha256"]) == 64
    assert len(row["candidate_feature_snapshot_sha256"]) == 64
    assert out["ledger_sha256"] == candidate._digest({k: v for k, v in out.items() if k != "ledger_sha256"})


def test_rejects_tampered_old_ledger_or_changed_training_sources():
    history, receipts, baseline, as_of = _prepared()
    corrupt = deepcopy(baseline)
    corrupt["predictions"][0]["prob_home"] = 0.9
    with pytest.raises(ValueError, match="TAMPERED_BASELINE_LEDGER_SHA256"):
        candidate.issue_accuracy_challenger(history, receipts, corrupt, as_of=as_of)
    with pytest.raises(ValueError, match="CHALLENGER_HISTORICAL_SOURCE_MISMATCH"):
        candidate.issue_accuracy_challenger(history, [{"sha256": "x" * 64}], baseline, as_of=as_of)


def test_post_date_forecasts_and_duplicate_pairs_fail_closed():
    history, receipts, baseline, as_of = _prepared()
    invalid = deepcopy(baseline)
    invalid["predictions"][0]["expected_calendar_date"] = as_of.date().isoformat()
    old = invalid.pop("ledger_sha256")
    invalid["ledger_sha256"] = candidate._digest(invalid)
    with pytest.raises(ValueError, match="CHALLENGER_POST_FIXTURE_DATE_ISSUE_FORBIDDEN"):
        candidate.issue_accuracy_challenger(history, receipts, invalid, as_of=as_of)
    duplicate = deepcopy(baseline)
    duplicate["predictions"].append(deepcopy(duplicate["predictions"][0]))
    duplicate.pop("ledger_sha256")
    duplicate["ledger_sha256"] = candidate._digest(duplicate)
    with pytest.raises(ValueError, match="CHALLENGER_FIXTURE_IDENTITY_OR_DUPLICATE"):
        candidate.issue_accuracy_challenger(history, receipts, duplicate, as_of=as_of)


def test_timestamps_and_future_probability_contract_must_match():
    history, receipts, baseline, as_of = _prepared()
    with pytest.raises(ValueError, match="BASELINE_AND_CHALLENGER_ISSUE_TIMES_DIFFER"):
        candidate.issue_accuracy_challenger(
            history, receipts, baseline, as_of=as_of + timedelta(minutes=1)
        )
    invalid = deepcopy(baseline)
    invalid["predictions"][0]["prob_draw"] = 0.9
    invalid.pop("ledger_sha256")
    invalid["ledger_sha256"] = candidate._digest(invalid)
    with pytest.raises(ValueError, match="CHALLENGER_M1_BASELINE_PROBABILITIES_INVALID"):
        candidate.issue_accuracy_challenger(history, receipts, invalid, as_of=as_of)
