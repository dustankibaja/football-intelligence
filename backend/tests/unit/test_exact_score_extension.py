"""Pre-match exact-score extension: mathematics, provenance, no retroactive writes."""

from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
import json
import math

import pytest

from app.research import exact_score_extension as ext


def _issued_ledger(monkeypatch):
    now = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
    records = []
    for i in range(63):
        lh = 0.7 + 0.02 * i
        la = 1.25 - 0.006 * i
        hp = [math.exp(-lh) * lh ** k / math.factorial(k) for k in range(11)]
        ap = [math.exp(-la) * la ** k / math.factorial(k) for k in range(11)]
        grid = [[hp[j] * ap[k] for k in range(11)] for j in range(11)]
        mass = sum(map(sum, grid))
        h = sum(grid[j][k] for j in range(11) for k in range(11) if j > k) / mass
        d = sum(grid[j][j] for j in range(11)) / mass
        a = 1.0 - h - d
        records.append({
            "fixture_sha256": f"{i:064x}",
            "league": "E0",
            "provider_home": f"Home{i}",
            "provider_away": f"Away{i}",
            "expected_calendar_date": "2026-11-21",
            "lambda_home": lh,
            "lambda_away": la,
            "model_artifact_sha256": "b" * 64,
            "prob_home": h,
            "prob_draw": d,
            "prob_away": a,
            "status": "SHADOW_ONLY_UNCALIBRATED_WAITING_FOR_MATCH",
        })
    ledger = {
        "schema_version": "noncommercial_prospective_shadow_1.0.0",
        "status": "RESEARCH_SHADOW_NOT_CERTIFIED_OR_COMMERCIAL",
        "generated_at_utc": now.isoformat(),
        "counts": {"forecasts_issued": 63},
        "predictions": records,
    }
    digest = sha256(json.dumps(ledger, sort_keys=True).encode()).hexdigest()
    monkeypatch.setattr(ext, "ORIGINAL_63_LEDGER_SHA256", digest)
    ledger["ledger_sha256"] = digest
    return ledger


def test_score_grid_stays_consistent_with_actual_original_preissued_lambdas(monkeypatch):
    ledger = _issued_ledger(monkeypatch)
    original = deepcopy(ledger)
    report = ext.derive_exact_scores(
        ledger, issued_at_utc=datetime(2026, 11, 9, 14, 0, tzinfo=UTC)
    )
    assert ledger == original
    assert report["count"] == 63
    assert report["status"] == "RESEARCH_ONLY_UNCALIBRATED_PREMATCH_DERIVATION"
    assert len({row["fixture_sha256"] for row in report["predictions"]}) == 63
    assert report["max_1x2_reconstruction_discrepancy"] < 1e-5
    assert all(len(row["top5_scores"]) == 5 for row in report["predictions"])
    for row in report["predictions"]:
        assert row["matrix_shape"] == [11, 11]
        assert len(row["score_probability_matrix_conditional_0_to_10"]) == 11
        assert all(len(series) == 11 for series in row["score_probability_matrix_conditional_0_to_10"])
        assert abs(
            sum(sum(series) for series in row["score_probability_matrix_conditional_0_to_10"]) - 1
        ) < 1e-8


def test_tampered_original_forecast_is_rejected(monkeypatch):
    ledger = _issued_ledger(monkeypatch)
    ledger["predictions"][0]["prob_home"] = 0.99
    with pytest.raises(ValueError, match="INTEGRITY_FAILURE"):
        ext.derive_exact_scores(
            ledger, issued_at_utc=datetime(2026, 11, 9, 14, 0, tzinfo=UTC)
        )


def test_post_fixture_score_extension_is_prohibited(monkeypatch):
    ledger = _issued_ledger(monkeypatch)
    with pytest.raises(ValueError, match="AFTER_FIXTURE_DAY"):
        ext.derive_exact_scores(
            ledger, issued_at_utc=datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
        )


def test_original_and_extension_must_be_separately_dated(monkeypatch):
    ledger = _issued_ledger(monkeypatch)
    with pytest.raises(ValueError, match="MUST_POSTDATE"):
        ext.derive_exact_scores(
            ledger, issued_at_utc=datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
        )
