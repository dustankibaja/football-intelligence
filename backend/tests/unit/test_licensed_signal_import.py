from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.research.licensed_signal_import import SignalBundle, materialize_signals


def sample():
    fixture, comp, h, a = (str(uuid4()) for _ in range(4))
    cutoff = datetime(2026, 11, 21, 12, tzinfo=UTC)
    receipt = {"provider_key": "licensed_test_provider", "source_record_id": "fixture-state",
               "source_payload_sha256": "a" * 64,
               "published_at": (cutoff - timedelta(minutes=10)).isoformat(),
               "captured_at": cutoff.isoformat()}
    prior = []
    for team in (h, a):
        for i in range(5):
            prior.append({"fixture_id": str(uuid4()), "team_id": team,
                          "completed_at": (cutoff - timedelta(days=3 + 7*i)).isoformat(),
                          "xg_for": 1.5, "xg_against": 1.0, "receipt": {
                              **receipt, "source_record_id": f"{team}-{i}"
                          }})
    availability = [{"fixture_id": fixture, "team_id": team,
                     "confirmed_starters": [str(uuid4()) for _ in range(11)],
                     "unavailable_players": [], "receipt": {**receipt, "source_record_id": team}}
                    for team in (h, a)]
    return {"schema_version": "licensed_football_signals_1.0.0",
            "target": {"fixture_id": fixture, "competition_id": comp, "home_team_id": h,
                       "away_team_id": a, "kickoff_utc": (cutoff + timedelta(hours=3)).isoformat(),
                       "cutoff_utc": cutoff.isoformat()},
            "grants": [{"provider_key": "licensed_test_provider",
                        "authorization_reference": "SYNTHETIC TEST ONLY",
                        "permits_internal_research": True, "permits_model_training": True}],
            "prior_matches": prior, "availability": availability}


def materialize(data):
    return materialize_signals(SignalBundle.model_validate(data))


def test_full_known_pre_match_signals_and_explicit_zero_availability():
    data = sample()
    original = deepcopy(data)
    result = materialize(data)
    assert data == original
    assert result["complete_feature_coverage"] is True
    assert result["publication_eligible"] is False
    assert result["features"]["home_xg_for_last5"] == 1.5
    assert result["features"]["away_xg_against_last5"] == 1.0
    assert result["features"]["home_unavailable_player_count"] == 0
    assert result["coverage"]["home"]["prior_matches_used"] == 5
    assert len(result["feature_report_sha256"]) == 64
    assert materialize(data)["feature_report_sha256"] == result["feature_report_sha256"]


def test_missing_data_not_treated_as_zero_or_full_history():
    data = sample()
    data["prior_matches"].pop(0)
    data["availability"][0]["unavailable_players"] = None
    data["availability"][0]["confirmed_starters"] = None
    result = materialize(data)
    assert result["features"]["home_xg_for_last5"] is None
    assert result["features"]["home_prior_match_count"] == 4
    assert result["features"]["home_unavailable_player_count"] is None
    assert result["features"]["home_confirmed_lineup"] is None
    assert result["complete_feature_coverage"] is False


def test_no_future_results_or_later_capture_can_enter_feature_set():
    data = sample()
    data["prior_matches"][0]["receipt"]["captured_at"] = "2026-11-21T12:00:01Z"
    with pytest.raises(ValueError, match="NOT_KNOWN_BEFORE_CUTOFF"):
        materialize(data)
    data = sample()
    data["prior_matches"][0]["completed_at"] = "2026-11-21T14:00:00Z"
    with pytest.raises(ValueError, match="NOT_COMPLETED_BEFORE_CUTOFF"):
        materialize(data)
    data = sample()
    data["prior_matches"][0]["fixture_id"] = data["target"]["fixture_id"]
    with pytest.raises(ValueError, match="PRIOR_MATCH_IDENTITY_MISMATCH"):
        materialize(data)


def test_no_unlicensed_or_ambiguous_observations():
    data = sample()
    data["grants"][0]["permits_model_training"] = False
    with pytest.raises(ValueError, match="SOURCE_RIGHTS_DO_NOT_COVER"):
        materialize(data)
    data = sample()
    data["prior_matches"].append(deepcopy(data["prior_matches"][0]))
    with pytest.raises(ValueError, match="DUPLICATE_PRIOR_MATCH"):
        materialize(data)
    data = sample()
    data["availability"][0]["unavailable_players"] = [data["availability"][0]["confirmed_starters"][0]]
    with pytest.raises(ValueError, match="BOTH_STARTING_AND_UNAVAILABLE"):
        materialize(data)
