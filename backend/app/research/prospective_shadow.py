"""Prospective, read-only *research* shadow forecasts from validated future fixtures.

Non-commercial provider: open-matchday, pinned to an upstream commit per run.
No post-kickoff forecasts, no production model writes, no paid API secrets.
This is NOT the Phase 6.2 certified 251-feature serving pipeline.
"""
from __future__ import annotations

import argparse
import csv
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
import io
import json
import math
import os
from pathlib import Path
from urllib.request import Request, urlopen

from app.research.historical_oos import (
    Fixture,
    _research_modeling_rows,
    _structural_model,
    build_pre_match_features,
    download_completed_seasons,
)


UPSTREAM_REPOSITORY = "oladeeayo/open-matchday"
UPSTREAM_BASE = "https://raw.githubusercontent.com/oladeeayo/open-matchday"
UPSTREAM_COMMIT_API = "https://api.github.com/repos/oladeeayo/open-matchday/commits/master"
FOLDERS = {
    "E0": "ENG_1_premier-league",
    "SP1": "ESP_1_la-liga",
    "I1": "ITA_1_serie-a",
    "D1": "GER_1_bundesliga",
}
# These are reviewed candidate aliases, NOT fuzzy matches. An alias is used
# ONLY when it resolves to an exact name in the genuine historical source.
ALIASES = {
    "E0": {
        "Spurs": "Tottenham",
        "Man Utd": "Man United",
    },
    "SP1": {
        "FC Barcelona": "Barcelona",
        "Athletic Club": "Ath Bilbao",
        "RCD Espanyol de Barcelona": "Espanol",
        "Deportivo Alavés": "Alaves",
        "Valencia CF": "Valencia",
        "Sevilla FC": "Sevilla",
        "Real Betis": "Betis",
        "Villarreal CF": "Villarreal",
        "CA Osasuna": "Osasuna",
        "Getafe CF": "Getafe",
        "Atlético de Madrid": "Ath Madrid",
        "Elche CF": "Elche",
        "Levante UD": "Levante",
    },
    "I1": {
        "Internazionale": "Inter",
    },
    "D1": {
        "FC Bayern München": "Bayern Munich",
        "1. FC Köln": "FC Koln",
        "1. FC Union Berlin": "Union Berlin",
        "Bayer 04 Leverkusen": "Leverkusen",
        "Borussia Mönchengladbach": "M'gladbach",
        "Borussia Dortmund": "Dortmund",
        "Eintracht Frankfurt": "Ein Frankfurt",
        "FC Augsburg": "Augsburg",
        "1. FSV Mainz 05": "Mainz",
        "FC Schalke 04": "Schalke 04",
        "Hamburger SV": "Hamburg",
        "SC Freiburg": "Freiburg",
        "SC Paderborn 07": "Paderborn",
        "SV Werder Bremen": "Werder Bremen",
        "TSG Hoffenheim": "Hoffenheim",
        "VfB Stuttgart": "Stuttgart",
    },
}


def _get_bytes(url: str, *, max_bytes: int = 2_000_000) -> bytes:
    request = Request(
        url,
        headers={"User-Agent": "FootballIntelligenceNoncommercialResearch/1.0"},
    )
    with urlopen(request, timeout=25) as response:
        data = response.read(max_bytes + 1)
    if not data or len(data) > max_bytes:
        raise ValueError("INVALID_UPSTREAM_PAYLOAD_SIZE")
    return data


def _source_snapshot(as_of: datetime) -> tuple[str, list[dict], list[dict]]:
    """Load one *pinned* upstream commit, reject stale or malformed schedules."""
    response = json.loads(_get_bytes(UPSTREAM_COMMIT_API).decode("utf-8"))
    commit = str(response.get("sha", ""))
    if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
        raise ValueError("UNVERIFIABLE_PROVIDER_COMMIT")

    summary_url = f"{UPSTREAM_BASE}/{commit}/build_summary.json"
    summary_raw = _get_bytes(summary_url)
    summary = json.loads(summary_raw.decode("utf-8"))
    generated_at = datetime.fromisoformat(str(summary["generated_utc"]).replace("Z", "+00:00"))
    if not timedelta(0) <= as_of - generated_at <= timedelta(hours=72):
        raise ValueError("UPSTREAM_SCHEDULE_NOT_RECENT_ENOUGH")
    if summary.get("season") != "2627":
        raise ValueError("UNEXPECTED_UPSTREAM_SEASON")

    rows = []
    receipts = [{
        "source": summary_url,
        "sha256": sha256(summary_raw).hexdigest(),
        "type": "upstream_build_summary",
    }]
    known_folders = {str(item.get("folder")): item for item in summary.get("leagues", [])}
    for league, folder in FOLDERS.items():
        entry = known_folders.get(f"data/{folder}")
        if not entry or entry.get("errors"):
            raise ValueError(f"UPSTREAM_COMPETITION_COVERAGE_OR_ERROR_{league}")
        url = f"{UPSTREAM_BASE}/{commit}/data/{folder}/fixtures_2627.csv"
        raw = _get_bytes(url)
        if raw[:256].lstrip().lower().startswith((b"<html", b"<!doctype")):
            raise ValueError("UPSTREAM_SCHEDULE_HTML_NOT_CSV")
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
        if not reader.fieldnames or not {"date", "home", "away"}.issubset(reader.fieldnames):
            raise ValueError("UPSTREAM_FIXTURE_SCHEMA_MISMATCH")
        seen = set()
        count = 0
        for record in reader:
            d = date.fromisoformat(str(record.get("date", "")).strip())
            home = str(record.get("home", "")).strip()
            away = str(record.get("away", "")).strip()
            if not home or not away or home == away:
                raise ValueError("INVALID_UPSTREAM_HOME_AWAY_NAMES")
            key = (d, home, away)
            if key in seen:
                raise ValueError("DUPLICATE_UPSTREAM_SCHEDULE")
            seen.add(key)
            count += 1
            rows.append({
                "league": league,
                "date": d,
                "home": home,
                "away": away,
                "reported_time": str(record.get("time") or ""),
                "calendar_time_verified_utc": False,
            })
        if count < 1 or count != int(entry.get("fixtures", -1)):
            raise ValueError(f"UPSTREAM_SCHEDULE_COUNT_MISMATCH_{league}")
        receipts.append({
            "source": url,
            "league": league,
            "sha256": sha256(raw).hexdigest(),
            "fixture_rows": count,
        })
    return commit, rows, receipts


def _exact_alias(
    league: str, input_name: str, historical_teams: dict[str, set[str]]
) -> str | None:
    if input_name in historical_teams[league]:
        return input_name
    mapped = ALIASES.get(league, {}).get(input_name)
    return mapped if mapped in historical_teams[league] else None


def prospective_shadow(
    history: list[Fixture],
    historical_receipts: list[dict],
    upstream_commit: str,
    schedules: list[dict],
    upstream_receipts: list[dict],
    *,
    as_of: datetime,
    lookahead_days: int = 21,
) -> dict:
    """Issue forecasts strictly before the source's future calendar date.

    Times from the secondary provider are approximate/local. Do not treat
    them as certified UTC kickoffs; require a FULL FUTURE DAY for safety.
    """
    if as_of.tzinfo is None:
        raise ValueError("UTC_AWARE_ISSUE_TIME_REQUIRED")
    as_of = as_of.astimezone(UTC)
    if not 1 <= lookahead_days <= 45:
        raise ValueError("INVALID_LOOKAHEAD")
    historical_teams: dict[str, set[str]] = {
        league: set() for league in FOLDERS
    }
    for row in history:
        historical_teams[row.league].update((row.home, row.away))

    due = []
    blocked = []
    seen = set()
    for item in schedules:
        future_date = item["date"]
        if not as_of.date() < future_date <= as_of.date() + timedelta(days=lookahead_days):
            continue
        league = item["league"]
        a = _exact_alias(league, item["home"], historical_teams)
        b = _exact_alias(league, item["away"], historical_teams)
        token = f"{league}|2627|{future_date}|{item['home']}|{item['away']}"
        fixture_sha = sha256(token.encode()).hexdigest()
        if fixture_sha in seen:
            raise ValueError("DUPLICATE_PROVIDER_FIXTURE_HASH")
        seen.add(fixture_sha)
        if a is None or b is None or a == b:
            blocked.append({
                "fixture_sha256": fixture_sha,
                "league": league,
                "reason": "UNRESOLVED_OR_UNSEEN_PROVIDER_TEAM",
            })
            continue
        # -1 is a deliberate UNPLAYED marker; historical feature engineering
        # must never update scores/form from these future records.
        due.append((
            item,
            fixture_sha,
            Fixture(league, "2627", future_date, a, b, -1, -1, -1),
        ))

    if not due:
        raise ValueError("NO_VERIFIABLE_FUTURE_FIXTURE_PAIRS")

    future = [entry[2] for entry in due]
    if max(h.match_date for h in history) >= min(f.match_date for f in future):
        raise ValueError("HISTORICAL_RESULTS_AFTER_SHADOW_KICKOFF")

    full_features, _ = build_pre_match_features(history + future)
    training_features = full_features[:len(history)]
    future_features = full_features[len(history):]
    # Actual observed features only; no synthetic future score updates.
    combined_sources = list(historical_receipts)
    train_rows = _research_modeling_rows(
        history, training_features, combined_sources
    )
    forecast_rows = _research_modeling_rows(
        future, future_features, combined_sources
    )
    model = _structural_model("M1_DECAY_365")
    model.fit(train_rows)
    competitions = model.artifact.weights_payload["competitions"]
    if len(competitions) != len(FOLDERS) or not all(
        comp.get("diagnostics", {}).get("success") for comp in competitions.values()
    ):
        raise ValueError("SHADOW_MODEL_FIT_NONCONVERGENCE")
    model_sha = sha256(model.artifact.model_dump_json().encode()).hexdigest()

    issued = []
    for ((item, fixture_sha, _), row) in zip(due, forecast_rows):
        team_parameters = competitions.get(str(row.competition_id), {})
        known = team_parameters.get("team_attack_strengths", {})
        counts = team_parameters.get("team_match_counts", {})
        if (
            str(row.home_team_id) not in known
            or str(row.away_team_id) not in known
            or counts.get(str(row.home_team_id), 0) < 5
            or counts.get(str(row.away_team_id), 0) < 5
        ):
            blocked.append({
                "fixture_sha256": fixture_sha,
                "league": row.competition_id.hex,
                "reason": "INSUFFICIENT_RESEARCH_TEAM_HISTORY",
            })
            continue
        assert row.kickoff_utc.date() > as_of.date()
        prediction = model.predict_raw(row.model_copy(update={"targets": {}}))
        probs = (
            float(prediction.prob_home_win),
            float(prediction.prob_draw),
            float(prediction.prob_away_win),
        )
        if (
            not all(math.isfinite(p) and 0 <= p <= 1 for p in probs)
            or abs(sum(probs) - 1) > 1e-4
            or prediction.predicted_home_goals is None
            or prediction.predicted_away_goals is None
            or prediction.prob_over_25 is None
            or prediction.prob_btts_yes is None
        ):
            raise ValueError("INVALID_RESEARCH_SHADOW_PROBABILITY")
        issued.append({
            "fixture_sha256": fixture_sha,
            "league": item["league"],
            "provider_home": item["home"],
            "provider_away": item["away"],
            "expected_calendar_date": item["date"].isoformat(),
            "prob_home": probs[0],
            "prob_draw": probs[1],
            "prob_away": probs[2],
            "lambda_home": float(prediction.predicted_home_goals),
            "lambda_away": float(prediction.predicted_away_goals),
            "prob_over_2_5": float(prediction.prob_over_25),
            "prob_btts_yes": float(prediction.prob_btts_yes),
            "model_artifact_sha256": model_sha,
            "feature_snapshot_sha256": row.feature_set_fingerprint,
            "status": "SHADOW_ONLY_UNCALIBRATED_WAITING_FOR_MATCH",
        })
    if not issued:
        raise ValueError("NO_QUALIFIED_PROSPECTIVE_SHADOW_PREDICTIONS")

    issued.sort(key=lambda p: (
        p["expected_calendar_date"], p["league"], p["fixture_sha256"]
    ))
    result = {
        "schema_version": "noncommercial_prospective_shadow_1.0.0",
        "status": "RESEARCH_SHADOW_NOT_CERTIFIED_OR_COMMERCIAL",
        "generated_at_utc": as_of.isoformat(),
        "source_research_license": (
            "open-matchday: attribution, informational/non-commercial research only. "
            "Original football-data.co.uk historical files were not redistributed."
        ),
        "fixture_source_repo": UPSTREAM_REPOSITORY,
        "fixture_source_commit": upstream_commit,
        "upstream_source_receipts": upstream_receipts,
        "historical_source_receipts": historical_receipts,
        "model": {
            "name": "M1_DECAY_365_RETRAINED_ON_COMPLETED_2019_2026",
            "weights_sha256": model_sha,
            "artifact_fingerprint": model.artifact.artifact_fingerprint,
            "training_rows": len(train_rows),
            "training_latest_calendar_date": max(h.match_date for h in history).isoformat(),
            "probability_status": "RAW_UNCALIBRATED",
            "requires_new_calibration": True,
            "publication_eligible": False,
        },
        "counts": {
            "future_schedule_rows_in_window": len(issued) + len(blocked),
            "candidate_mapped": len(due),
            "forecasts_issued": len(issued),
            "blocked": len(blocked),
        },
        "blocked": blocked,
        "predictions": issued,
        "limitations": [
            "Provider open-matchday is non-commercial and not an official production match API.",
            "Provider kickoff time zone is not confirmed; only matches on future calendar days were eligible.",
            "No actual 2026/27 results contribute to the fitted model; team parameters end at 2025/26.",
            "Manual, exact verified team alias mappings are allowed; unrecognized clubs are blocked.",
            "This is distinct from Phase 6.2 251-feature certified live prediction serving.",
            "No results have occurred for these predictions at their issue time; prospective calibration unknown.",
            "A GitHub Actions artifact is a time-stamped research snapshot, not a permanent immutable database.",
            "Retraining model on entire history invalidates prior temperature calibration and selection thresholds.",
        ],
    }
    payload = json.dumps(result, sort_keys=True, default=str)
    result["ledger_sha256"] = sha256(payload.encode()).hexdigest()
    return result


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--raw-dir", default="data/research_football_data_raw")
    p.add_argument("--output", required=True)
    p.add_argument("--lookahead-days", type=int, default=21)
    args = p.parse_args()
    as_of = datetime.now(UTC)
    history, receipts = download_completed_seasons(Path(args.raw_dir))
    commit, fixtures, upstream = _source_snapshot(as_of)
    report = prospective_shadow(
        history, receipts, commit, fixtures, upstream, as_of=as_of,
        lookahead_days=args.lookahead_days,
    )
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "source_commit": report["fixture_source_commit"],
        "source_freshness_required_hours": 72,
        "historical_training_matches": report["model"]["training_rows"],
        "model_sha256": report["model"]["weights_sha256"],
        "forecasts_issued": report["counts"]["forecasts_issued"],
        "blocked": report["counts"]["blocked"],
        "earliest_forecast": report["predictions"][0]["expected_calendar_date"],
        "sample_forecast": report["predictions"][0],
        "ledger_sha256": report["ledger_sha256"],
        "report": str(out),
    }, indent=2))


if __name__ == "__main__":
    main()
