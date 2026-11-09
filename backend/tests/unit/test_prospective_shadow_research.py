"""Prospective shadow constraints: immutable source receipts and no fake future results."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
import json

import numpy as np
import pytest

from app.research.historical_oos import Fixture, build_pre_match_features
from app.research import prospective_shadow as shadow


def test_scheduled_matches_never_change_causal_history():
    past = [
        Fixture("E0", "2526", date(2026, 4, 10), "Man City", "Fulham", 2, 0, 0),
        Fixture("E0", "2526", date(2026, 4, 15), "Fulham", "Man City", 0, 1, 2),
    ]
    future = Fixture("E0", "2627", date(2026, 11, 21), "Man City", "Fulham", -1, -1, -1)
    next_game = Fixture("E0", "2627", date(2026, 11, 22), "Fulham", "Man City", -1, -1, -1)
    x_both, labels = build_pre_match_features(past + [future, next_game])
    x_past, _ = build_pre_match_features(past)
    assert np.array_equal(x_both[:2], x_past)
    assert labels.tolist()[-2:] == [-1, -1]
    # No scheduled result/form may update league/team history for the next day.
    x_future_alone, _ = build_pre_match_features(past + [next_game])
    assert np.array_equal(x_both[-1], x_future_alone[-1])


def test_provider_aliases_are_exact_and_fail_closed():
    historical_teams = {
        "E0": {"Man City", "Fulham", "Tottenham", "Man United"},
        "SP1": set(), "I1": set(), "D1": set(),
    }
    assert shadow._exact_alias("E0", "Man City", historical_teams) == "Man City"
    assert shadow._exact_alias("E0", "Spurs", historical_teams) == "Tottenham"
    assert shadow._exact_alias("E0", "Unknown Promoted Club", historical_teams) is None
    assert shadow._exact_alias("E0", "MAN CITY", historical_teams) is None
    # A curated alias still fails if its target is absent from real source history.
    historical_teams["E0"].remove("Tottenham")
    assert shadow._exact_alias("E0", "Spurs", historical_teams) is None


def test_pinned_upstream_commit_and_fixture_checksums(monkeypatch):
    current = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
    commit = "a" * 40
    summary = {
        "season": "2627", "generated_utc": "2026-11-09T05:00:00Z",
        "leagues": [
            {"folder": f"data/{folder}", "errors": [], "fixtures": 1}
            for folder in shadow.FOLDERS.values()
        ],
    }
    files = {
        shadow.UPSTREAM_COMMIT_API: json.dumps({"sha": commit}).encode(),
        f"{shadow.UPSTREAM_BASE}/{commit}/build_summary.json": json.dumps(summary).encode(),
    }
    for i, folder in enumerate(shadow.FOLDERS.values()):
        files[f"{shadow.UPSTREAM_BASE}/{commit}/data/{folder}/fixtures_2627.csv"] = (
            "gw,date,time,home,away\n"
            f"13,2026-11-21,16:00,Home_{i},Away_{i}\n"
        ).encode()

    requested = []
    def load(url, *, max_bytes=2_000_000):
        requested.append(url)
        return files[url]

    monkeypatch.setattr(shadow, "_get_bytes", load)
    returned_commit, future, receipts = shadow._source_snapshot(current)
    assert returned_commit == commit
    assert len(future) == 4
    assert len(receipts) == 5
    assert all(f["date"] == date(2026, 11, 21) for f in future)
    assert all(f["calendar_time_verified_utc"] is False for f in future)
    assert all(len(item["sha256"]) == 64 for item in receipts)
    assert all(f"/{commit}/" in url for url in requested if "raw.githubusercontent" in url)
    assert receipts[0]["sha256"] == sha256(files[requested[1]]).hexdigest()


def test_stale_upstream_source_must_be_rejected(monkeypatch):
    now = datetime(2026, 11, 9, 12, tzinfo=UTC)
    commit = "c" * 40
    stale = json.dumps({
        "season": "2627", "generated_utc": "2026-10-01T00:00:00Z",
        "leagues": [],
    }).encode()
    monkeypatch.setattr(
        shadow,
        "_get_bytes",
        lambda url, **kwargs: (
            json.dumps({"sha": commit}).encode()
            if url == shadow.UPSTREAM_COMMIT_API else stale
        ),
    )
    with pytest.raises(ValueError, match="UPSTREAM_SCHEDULE_NOT_RECENT_ENOUGH"):
        shadow._source_snapshot(now)


def test_future_only_window_rejects_absent_research_fixtures():
    now = datetime(2026, 11, 9, 14, tzinfo=UTC)
    past = [
        Fixture("E0", "2526", date(2026, 5, 15), "Man City", "Fulham", 1, 0, 0)
    ]
    past_schedule = [{
        "league": "E0", "date": date(2026, 11, 9),
        "home": "Man City", "away": "Fulham",
    }]
    with pytest.raises(ValueError, match="NO_VERIFIABLE_FUTURE_FIXTURE_PAIRS"):
        shadow.prospective_shadow(
            past, [], "a" * 40, past_schedule, [], as_of=now,
        )
