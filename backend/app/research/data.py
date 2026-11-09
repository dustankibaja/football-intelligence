"""Auditable, offline-first historical research acquisition (not an approved release)."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import time
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

PROVIDER = "football-data.co.uk"
BASE_URL = "https://www.football-data.co.uk/mmz4281"
COMPETITIONS = {"EPL": "E0", "LA_LIGA": "SP1", "SERIE_A": "I1", "BUNDESLIGA": "D1"}
SEASONS = ("2021-22", "2022-23", "2023-24", "2024-25", "2025-26")
REQUIRED = {"Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR"}


@dataclass(frozen=True, slots=True)
class Match:
    competition: str
    season: str
    day: date
    home: str
    away: str
    home_goals: int
    away_goals: int

    @property
    def outcome(self) -> int:
        return 0 if self.home_goals > self.away_goals else 1 if self.home_goals == self.away_goals else 2


def source_url(competition: str, season: str) -> str:
    if competition not in COMPETITIONS or season not in SEASONS:
        raise ValueError("Unapproved competition or season")
    code = season[2:4] + season[5:7]
    return f"{BASE_URL}/{code}/{COMPETITIONS[competition]}.csv"


def _decode(raw: bytes) -> str:
    if not raw or len(raw) > 3_000_000:
        raise ValueError("Source is empty or unexpectedly large")
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("Unsupported CSV encoding")


def _date(value: str) -> date:
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Invalid date: {value!r}")


def parse_source(raw: bytes, competition: str, season: str) -> list[Match]:
    if competition not in COMPETITIONS or season not in SEASONS:
        raise ValueError("Unapproved source identity")
    text = _decode(raw)
    if "<html" in text[:500].lower() or "<!doctype" in text[:500].lower():
        raise ValueError("HTML response instead of verified CSV")
    reader = csv.DictReader(io.StringIO(text))
    headers = set(reader.fieldnames or [])
    if not REQUIRED.issubset(headers):
        raise ValueError(f"Missing required columns: {sorted(REQUIRED - headers)}")
    matches: list[Match] = []
    seen: set[tuple[date, str, str]] = set()
    first_year = int(season[:4])
    for line, row in enumerate(reader, 2):
        if not row.get("Date", "").strip():
            # Provider CSVs can include empty trailing lines; never fabricate games.
            if any(str(row.get(field) or "").strip() for field in REQUIRED - {"Date"}):
                raise ValueError(f"Partial fixture row {line}")
            continue
        if row.get("Div", COMPETITIONS[competition]).strip() != COMPETITIONS[competition]:
            raise ValueError(f"Competition mismatch on row {line}")
        day = _date(row["Date"].strip())
        if day < date(first_year, 7, 1) or day > date(first_year + 1, 6, 30):
            raise ValueError(f"Date outside season on row {line}")
        home, away = row["HomeTeam"].strip(), row["AwayTeam"].strip()
        if not home or not away or home == away:
            raise ValueError(f"Invalid team identity on row {line}")
        try:
            hg, ag = int(row["FTHG"]), int(row["FTAG"])
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Invalid full-time score on row {line}") from exc
        if min(hg, ag) < 0 or max(hg, ag) > 25:
            raise ValueError(f"Out-of-range score on row {line}")
        outcome = "H" if hg > ag else "D" if hg == ag else "A"
        if row["FTR"].strip() != outcome:
            raise ValueError(f"Full-time result mismatch on row {line}")
        key = (day, home, away)
        if key in seen:
            raise ValueError(f"Duplicate fixture on row {line}")
        seen.add(key)
        matches.append(Match(competition, season, day, home, away, hg, ag))
    if not 250 <= len(matches) <= 420:
        raise ValueError(f"Unacceptable fixture coverage: {len(matches)} for {competition} {season}")
    return sorted(matches, key=lambda m: (m.day, m.home, m.away))


def _download(url: str) -> bytes:
    # URL is generated exclusively from a fixed provider and allowlisted codes.
    request = urllib.request.Request(url, headers={
        "User-Agent": "FootmoResearch/1.0 (+historical-source-validation)",
        "Accept": "text/csv,text/plain"
    })
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=35) as response:
                return response.read(3_000_001)
        except (OSError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("Download failed")


def verify_source_lock(manifest: dict, lock: dict) -> None:
    """Fail closed if the provider silently edits any frozen research source."""
    observed = {f"{s['competition']}:{s['season']}": s["sha256"]
                for s in manifest["sources"]}
    if lock.get("schema") != 1 or lock.get("sourceCount") != len(observed):
        raise ValueError("Research source lock schema/coverage mismatch")
    if observed != lock.get("sources"):
        raise ValueError("Research source checksum drift: new version requires review")
    if manifest["datasetFingerprintSha256"] != lock.get("datasetFingerprintSha256"):
        raise ValueError("Research dataset fingerprint drift")


def acquire(cache_dir: Path, *, offline: bool = False) -> tuple[list[Match], dict]:
    """Download 20 completed competition-seasons, validate and fingerprint.

    No raw CSV is committed or uploaded to public artifacts. Existing approved
    historical releases remain untouched. Fails closed on missing sources.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    all_matches: list[Match] = []
    sources: list[dict] = []
    for competition in COMPETITIONS:
        for season in SEASONS:
            url = source_url(competition, season)
            path = cache_dir / f"{competition}_{season}.csv"
            if not path.exists():
                if offline:
                    raise FileNotFoundError(f"Missing offline source {path}")
                raw = _download(url)
                # Validate before writing the cache.
                parsed = parse_source(raw, competition, season)
                path.write_bytes(raw)
            else:
                raw = path.read_bytes()
                parsed = parse_source(raw, competition, season)
            digest = hashlib.sha256(raw).hexdigest()
            all_matches.extend(parsed)
            sources.append({
                "competition": competition, "season": season,
                "url": url, "sha256": digest, "bytes": len(raw),
                "fixtures": len(parsed), "datePrecision": "DATE_ONLY"
            })
    # Exclude bookmaker columns and unverified external team identity mappings.
    canonical = [(m.competition, m.season, m.day.isoformat(), m.home,
                  m.away, m.home_goals, m.away_goals)
                 for m in sorted(all_matches, key=lambda x: (
                     x.competition, x.season, x.day, x.home, x.away))]
    fingerprint = hashlib.sha256(json.dumps(canonical,
        separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
    manifest = {
        "provider": PROVIDER, "sourceCount": len(sources),
        "totalFixtures": len(all_matches), "seasons": list(SEASONS),
        "competitions": list(COMPETITIONS), "sources": sources,
        "datasetFingerprintSha256": fingerprint,
        "datasetStatus": "RESEARCH_CANDIDATE_NOT_APPROVED",
        "temporalPrecision": "DATE_ONLY_PREVIOUS_DAY_ONLY",
        "bookmakerFieldsUsed": False
    }
    lock_path = Path(__file__).with_name("source-lock.json")
    if not lock_path.exists():
        raise FileNotFoundError("Frozen research source lock is required")
    verify_source_lock(manifest, json.loads(lock_path.read_text(encoding="utf-8")))
    return all_matches, manifest
