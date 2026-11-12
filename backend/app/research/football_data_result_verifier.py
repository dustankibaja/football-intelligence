"""Read-only independent 2026/27 result receipts from Football-Data.co.uk.

For noncommercial scientific evaluation only. Never import bookmaker odds;
never invent a completed timestamp (the source is calendar-date precision).
"""
from __future__ import annotations

import csv
from datetime import UTC, date, datetime
from hashlib import sha256
import io
from pathlib import Path
from urllib.request import Request, urlopen

from app.research.prospective_shadow import ALIASES
from app.research.source_policy import require_football_data_network_permission

SOURCE_BASE = "https://www.football-data.co.uk/mmz4281/2627"
LEAGUES = ("E0", "SP1", "I1", "D1")
MAX_SOURCE_BYTES = 3_000_000


def _read_url(url: str) -> bytes:
    require_football_data_network_permission()
    request = Request(
        url, headers={"User-Agent": "FootballIntelligenceResearchResults/1.0"}
    )
    with urlopen(request, timeout=30) as response:
        if "html" in response.headers.get("Content-Type", "").lower():
            raise ValueError("RESULT_PROVIDER_RETURNED_HTML")
        data = response.read(MAX_SOURCE_BYTES + 1)
    if not data or len(data) > MAX_SOURCE_BYTES:
        raise ValueError("INVALID_RESULT_SOURCE_SIZE")
    return data


def local_result_reader(directory: Path):
    """Read manually acquired/authorized source CSVs without network access.

    Source filenames must be E0.csv, SP1.csv, I1.csv, D1.csv; no arbitrary
    paths or URLs are accepted. The operator must verify local provenance
    and rights separately; a local file cannot prove its external origin.
    """
    directory = Path(directory).resolve()
    allowed = {f"{SOURCE_BASE}/{league}.csv": directory / f"{league}.csv" for league in LEAGUES}

    def read(url: str) -> bytes:
        if url not in allowed:
            raise ValueError("UNAPPROVED_LOCAL_RESULT_SOURCE")
        path = allowed[url]
        if path.resolve().parent != directory or not path.is_file():
            raise ValueError("MISSING_OR_UNSAFE_LOCAL_RESULT_SNAPSHOT")
        if path.stat().st_size > MAX_SOURCE_BYTES:
            raise ValueError("INVALID_RESULT_SOURCE_SIZE")
        return path.read_bytes()

    return read


def _canonical(league: str, name: str) -> str:
    """Only approved exact upstream aliases; no fuzzy matching."""
    return ALIASES.get(league, {}).get(name, name)


def _parse_source(raw: bytes, league: str) -> list[dict]:
    if not raw or len(raw) > MAX_SOURCE_BYTES:
        raise ValueError("INVALID_RESULT_SOURCE_SIZE")
    if raw[:256].lstrip().lower().startswith((b"<html", b"<!doctype")):
        raise ValueError("RESULT_SOURCE_NOT_CSV")
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig", errors="replace")))
    required = {"Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR"}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise ValueError("RESULT_SOURCE_COLUMNS_MISSING")
    results = []
    seen = set()
    for index, row in enumerate(reader, start=2):
        if not row or not str(row.get("HomeTeam") or "").strip():
            continue
        home = str(row.get("HomeTeam") or "").strip()
        away = str(row.get("AwayTeam") or "").strip()
        if not home or not away or home == away:
            raise ValueError(f"INVALID_RESULT_PAIRING_{league}_{index}")
        raw_date = str(row.get("Date") or "").strip()
        when = None
        for fmt in ("%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%m-%y"):
            try:
                when = datetime.strptime(raw_date, fmt).date()
                break
            except ValueError:
                pass
        if when is None or not date(2026, 7, 1) <= when <= date(2027, 7, 31):
            raise ValueError("INVALID_RESULT_SEASON_OR_DATE")
        token = (when, home, away)
        if token in seen:
            raise ValueError("DUPLICATE_RESULT_PROVIDER_FIXTURE")
        seen.add(token)
        outcome = str(row.get("FTR") or "").strip().upper()
        score_home = str(row.get("FTHG") or "").strip()
        score_away = str(row.get("FTAG") or "").strip()
        # Current-season provider files legitimately contain future fixtures.
        # A partially supplied score/outcome must be rejected, not inferred.
        if not outcome and not score_home and not score_away:
            continue
        if outcome not in {"H", "D", "A"} or not score_home or not score_away:
            raise ValueError("INCOMPLETE_CURRENT_SEASON_RESULT")
        try:
            hg, ag = int(score_home), int(score_away)
        except ValueError as exc:
            raise ValueError("MALFORMED_RESULT_GOALS") from exc
        if not (0 <= hg <= 20 and 0 <= ag <= 20):
            raise ValueError("UNREASONABLE_CURRENT_SEASON_SCORE")
        expected_outcome = "H" if hg > ag else "D" if hg == ag else "A"
        if expected_outcome != outcome:
            raise ValueError("RESULT_AND_FINAL_SCORE_CONTRADICT")
        results.append({
            "league": league,
            "home": home,
            "away": away,
            "result_date": when,
            "home_goals": hg,
            "away_goals": ag,
        })
    return results


def collect_result_receipts(
    original_ledger: dict,
    *,
    retrieved_at_utc: datetime,
    fetch_bytes=None,
) -> list[dict]:
    """Read independent completed match CSVs and match ONLY issued fixture IDs.

    Matching requires original scheduled date AND exact canonical team names.
    Rescheduled fixtures with changed dates remain pending until independently
    reconciled under an explicit correction audit; never guess identity.
    """
    if retrieved_at_utc.tzinfo is None:
        raise ValueError("UTC_AWARE_RESULT_RETRIEVAL_REQUIRED")
    retrieved_at_utc = retrieved_at_utc.astimezone(UTC)
    fetch = fetch_bytes or _read_url
    predictions = original_ledger.get("predictions", [])
    candidates = {}
    for p in predictions:
        key = (
            p["league"],
            date.fromisoformat(p["expected_calendar_date"]),
            _canonical(p["league"], p["provider_home"]),
            _canonical(p["league"], p["provider_away"]),
        )
        if key in candidates:
            raise ValueError("AMBIGUOUS_ORIGINAL_FIXTURE_IDENTITY")
        candidates[key] = p["fixture_sha256"]

    receipts = []
    matched = set()
    for league in LEAGUES:
        url = f"{SOURCE_BASE}/{league}.csv"
        raw = fetch(url)
        raw_hash = sha256(raw).hexdigest()
        for game in _parse_source(raw, league):
            key = (
                league, game["result_date"],
                _canonical(league, game["home"]),
                _canonical(league, game["away"]),
            )
            ident = candidates.get(key)
            if ident is None:
                continue
            # Provider dates lack a verified UTC final whistle time. Allow the
            # result into the ledger only starting the following UTC day.
            if game["result_date"] >= retrieved_at_utc.date():
                continue
            if ident in matched:
                raise ValueError("DUPLICATE_VERIFIED_FIXTURE_RESULT")
            matched.add(ident)
            forecast = next(p for p in predictions if p["fixture_sha256"] == ident)
            receipts.append({
                "fixture_sha256": ident,
                "league": league,
                "home": forecast["provider_home"],
                "away": forecast["provider_away"],
                "home_goals": game["home_goals"],
                "away_goals": game["away_goals"],
                "status": "FINISHED",
                "result_time_precision": "DATE_ONLY",
                "final_result_date": game["result_date"].isoformat(),
                "retrieved_at_utc": retrieved_at_utc.isoformat(),
                "source_url": url,
                "source_payload_sha256": raw_hash,
            })
    return sorted(receipts, key=lambda r: r["fixture_sha256"])


def main() -> None:
    """Reconcile archived real issued forecasts against current public scores.

    The original ledger is read-only and never overwritten. Caller must
    archive both the original ledger and this separate output for provenance.
    """
    import argparse
    import json
    from pathlib import Path

    from app.research.prospective_reconciliation import reconcile_prospective_results

    parser = argparse.ArgumentParser(description="Read-only prospective football result verification")
    parser.add_argument("--original-ledger", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--results-dir", type=Path, help="Rights-reviewed local E0/SP1/I1/D1 CSV snapshots; no network.")
    args = parser.parse_args()
    original_path = Path(args.original_ledger)
    output_path = Path(args.output)
    if original_path.resolve() == output_path.resolve():
        raise ValueError("ORIGINAL_IMMUTABLE_LEDGER_CANNOT_BE_OVERWRITTEN")
    original = json.loads(original_path.read_text(encoding="utf-8"))
    retrieved_at = datetime.now(UTC)
    fetched = collect_result_receipts(original, retrieved_at_utc=retrieved_at,
                                      fetch_bytes=local_result_reader(args.results_dir) if args.results_dir else None)
    assessment = reconcile_prospective_results(
        original,
        fetched,
        evaluated_at_utc=datetime.now(UTC),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(assessment, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "status": assessment["status"],
        "original_forecasts": assessment["original_forecasts"],
        "finished_evaluated": assessment["finished_evaluated"],
        "pending_or_unverified": assessment["pending_or_unverified"],
        "original_ledger_sha256": assessment["original_forecast_ledger_sha256"],
        "evaluation_sha256": assessment["report_sha256"],
        "output": str(output_path),
    }, indent=2))


if __name__ == "__main__":
    main()
