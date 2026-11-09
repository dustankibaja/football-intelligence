"""Proper full-support prospective score likelihood, including non-top-five results."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import math

import pytest

from app.research import exact_score_extension as ext
from app.research import exact_score_reconciliation as scoring
from app.research.prospective_reconciliation import reconcile_prospective_results

ISSUE = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
EXTENSION = ISSUE + timedelta(minutes=10)
EVALUATED = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)


def _fixture_data(monkeypatch):
    lh, la = 1.3, 1.1
    hp = [math.exp(-lh) * lh ** i / math.factorial(i) for i in range(11)]
    ap = [math.exp(-la) * la ** i / math.factorial(i) for i in range(11)]
    raw = [[hp[i]*ap[j] for j in range(11)] for i in range(11)]
    mass = sum(map(sum, raw))
    probs = [
        sum(raw[i][j] for i in range(11) for j in range(11) if i > j) / mass,
        sum(raw[i][i] for i in range(11)) / mass,
        sum(raw[i][j] for i in range(11) for j in range(11) if i < j) / mass,
    ]
    forecasts = [
        {
            "fixture_sha256": f"{i+1:064x}",
            "league": "E0",
            "provider_home": f"Home{i}",
            "provider_away": f"Away{i}",
            "expected_calendar_date": "2026-11-21",
            "prob_home": probs[0],
            "prob_draw": probs[1],
            "prob_away": probs[2],
            "lambda_home": lh,
            "lambda_away": la,
            "prob_btts_yes": 0.45,
            "prob_over_2_5": 0.35,
            "model_artifact_sha256": "b" * 64,
            "feature_snapshot_sha256": "c" * 64,
            "status": "SHADOW_ONLY_UNCALIBRATED_WAITING_FOR_MATCH",
        }
        for i in range(63)
    ]
    original = {
        "schema_version": "noncommercial_prospective_shadow_1.0.0",
        "status": "RESEARCH_SHADOW_NOT_CERTIFIED_OR_COMMERCIAL",
        "generated_at_utc": ISSUE.isoformat(),
        "counts": {"forecasts_issued": 63},
        "predictions": forecasts,
    }
    digest = sha256(json.dumps(original, sort_keys=True).encode()).hexdigest()
    original["ledger_sha256"] = digest
    monkeypatch.setattr(ext, "ORIGINAL_63_LEDGER_SHA256", digest)
    monkeypatch.setattr(scoring, "ORIGINAL_63_LEDGER_SHA256", digest)
    extension = ext.derive_exact_scores(original, issued_at_utc=EXTENSION)
    return original, extension


def _verified_receipt(original, goals_h, goals_a):
    fixture = original["predictions"][0]
    receipt = {
        "fixture_sha256": fixture["fixture_sha256"],
        "league": fixture["league"],
        "home": fixture["provider_home"],
        "away": fixture["provider_away"],
        "home_goals": goals_h,
        "away_goals": goals_a,
        "status": "FINISHED",
        "result_time_precision": "DATE_ONLY",
        "final_result_date": "2026-11-21",
        "retrieved_at_utc": "2026-11-22T09:00:00+00:00",
        "source_url": "https://example.org/test-source",
        "source_payload_sha256": "f" * 64,
    }
    return reconcile_prospective_results(
        original, [receipt], evaluated_at_utc=EVALUATED
    )


def test_non_top_five_score_has_strictly_positive_probability(monkeypatch):
    original, extension = _fixture_data(monkeypatch)
    pre = deepcopy(extension)
    evaluated = scoring.evaluate_exact_scores(
        extension, _verified_receipt(original, 4, 3)
    )
    assert extension == pre
    assert evaluated["n_completed"] == 1 and evaluated["n_pending"] == 62
    score = evaluated["scores"][0]
    assert score["exact_score_probability_full_support"] > 0
    assert score["exact_score_negative_log_probability"] > 0
    assert score["actual_in_top_5"] is False
    assert evaluated["mean_exact_score_negative_log_probability"] > 0
    assert len(evaluated["report_sha256"]) == 64


def test_score_outside_published_grid_uses_untruncated_poisson(monkeypatch):
    original, extension = _fixture_data(monkeypatch)
    evaluated = scoring.evaluate_exact_scores(
        extension, _verified_receipt(original, 11, 0)
    )
    score = evaluated["scores"][0]
    assert score["outside_published_0_to_10_grid"] is True
    assert score["exact_score_probability_full_support"] > 0
    assert math.isfinite(score["exact_score_negative_log_probability"])


def test_unfinished_cohort_has_no_fabricated_accuracy(monkeypatch):
    original, extension = _fixture_data(monkeypatch)
    audit = reconcile_prospective_results(
        original, [], evaluated_at_utc=EVALUATED
    )
    evaluated = scoring.evaluate_exact_scores(extension, audit)
    assert evaluated["n_pending"] == 63
    assert evaluated["n_completed"] == 0
    assert "mean_exact_score_negative_log_probability" not in evaluated
    assert "top1_hit_rate" not in evaluated


def test_tampered_score_matrix_and_result_counts_rejected(monkeypatch):
    original, extension = _fixture_data(monkeypatch)
    audit = _verified_receipt(original, 2, 1)
    altered = deepcopy(extension)
    altered["predictions"][0]["score_probability_matrix_conditional_0_to_10"][2][1] = 0.999
    with pytest.raises(ValueError, match="LEDGER_INTEGRITY_FAILURE"):
        scoring.evaluate_exact_scores(altered, audit)
    tampered = deepcopy(audit)
    tampered["pending_or_unverified"] = 0
    with pytest.raises(ValueError, match="LEDGER_INTEGRITY_FAILURE"):
        scoring.evaluate_exact_scores(extension, tampered)
