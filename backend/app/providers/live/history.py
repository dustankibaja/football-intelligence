"""Conservative current-season history ingestion for live PIT features.

This does not modify the approved 2021–2024 training release. All match IDs
come from authenticated provider fixture IDs, all scores from final results,
and unresolved identities are rejected rather than fabricated.
"""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy.orm import Session

from app.db.models.core import Competition, Match, Season, SystemAuditLog, Team
from app.features.query import HistoricalQueryLayer


class LiveHistoryIngestionError(ValueError):
    """Canonical identity, temporal, or source-provenance violation."""


class LiveHistoryIngestor:
    def __init__(self, session_factory):
        if session_factory is None:
            raise LiveHistoryIngestionError("LIVE_DATABASE_REQUIRED")
        self.session_factory = session_factory

    def ingest(self, records: list[dict]) -> dict[str, int]:
        result = {"created": 0, "reused": 0, "rejected": 0}
        for record in records:
            try:
                with self.session_factory() as session:
                    created = self._ingest_one(session, record)
                    session.commit()
                result["created" if created else "reused"] += 1
            except (LiveHistoryIngestionError, ValueError):
                result["rejected"] += 1
        if result["created"]:
            HistoricalQueryLayer._cached_get_team_matches_before.cache_clear()
            HistoricalQueryLayer._cached_get_league_matches_before.cache_clear()
        return result

    @staticmethod
    def _ingest_one(db: Session, record: dict) -> bool:
        provider, provider_id = record.get("provider_name"), record.get("provider_fixture_id")
        source_fp = record.get("payload_fingerprint")
        if not provider or not provider_id or not source_fp:
            raise LiveHistoryIngestionError("PROVIDER_HISTORY_PROVENANCE_MISSING")
        expected_id = uuid5(NAMESPACE_URL, f"{provider}:{provider_id}")
        if record.get("fixture_id") != expected_id:
            raise LiveHistoryIngestionError("PROVIDER_HISTORY_IDENTITY_MISMATCH")
        if record.get("status") != "FINISHED" or not record.get("is_mapped"):
            raise LiveHistoryIngestionError("HISTORY_RESULT_NOT_FINAL_OR_UNMAPPED")
        home_id, away_id = record.get("home_team_id"), record.get("away_team_id")
        if (
            not isinstance(home_id, UUID) or not isinstance(away_id, UUID)
            or home_id == away_id
            or db.query(Team).filter(Team.id.in_([home_id, away_id])).count() != 2
        ):
            raise LiveHistoryIngestionError("HISTORY_TEAMS_NOT_CANONICAL")
        hg, ag = record.get("home_score"), record.get("away_score")
        if (
            not isinstance(hg, int) or not isinstance(ag, int)
            or hg < 0 or ag < 0
        ):
            raise LiveHistoryIngestionError("HISTORY_FINAL_SCORE_INVALID")
        kickoff = record.get("kickoff_utc")
        retrieved = record.get("retrieved_at_utc")
        if (
            not isinstance(kickoff, datetime) or kickoff.tzinfo is None
            or not isinstance(retrieved, datetime) or retrieved.tzinfo is None
            or kickoff >= retrieved
        ):
            raise LiveHistoryIngestionError("HISTORY_TEMPORAL_PROVENANCE_INVALID")

        comp = db.query(Competition).filter_by(code=record.get("competition_code")).first()
        if comp is None:
            raise LiveHistoryIngestionError("HISTORY_COMPETITION_NOT_CANONICAL")
        start_date, end_date = record.get("season_start_date"), record.get("season_end_date")
        start_year = str(record.get("season_year", ""))
        if not start_year.isdigit() or not start_date or not end_date:
            raise LiveHistoryIngestionError("HISTORY_SEASON_PROVENANCE_MISSING")
        start_dt = datetime.fromisoformat(start_date)
        end_dt = datetime.fromisoformat(end_date)
        if start_dt.year != int(start_year) or end_dt <= start_dt:
            raise LiveHistoryIngestionError("HISTORY_SEASON_DATES_INVALID")
        season = db.query(Season).filter_by(
            competition_id=comp.id, start_year=int(start_year)
        ).first()
        if season is None:
            season_year = f"{int(start_year)}/{str(end_dt.year)[-2:]}"
            season = db.query(Season).filter_by(
                competition_id=comp.id, season_year=season_year
            ).first()
            if season is None:
                season = Season(
                    competition_id=comp.id,
                    season_year=season_year,
                    start_year=int(start_year),
                    end_year=end_dt.year,
                    start_date=start_dt.replace(tzinfo=UTC),
                    end_date=end_dt.replace(tzinfo=UTC),
                )
                db.add(season)
                db.flush()

        existing = db.query(Match).filter_by(id=expected_id).first()
        if existing is not None:
            if (
                existing.season_id != season.id
                or existing.home_team_id != home_id
                or existing.away_team_id != away_id
                or existing.kickoff_utc.replace(tzinfo=UTC) != kickoff.astimezone(UTC)
            ):
                raise LiveHistoryIngestionError("HISTORY_EXISTING_FIXTURE_CONFLICT")
            if existing.status == "FINISHED":
                if (existing.home_score, existing.away_score) != (hg, ag):
                    raise LiveHistoryIngestionError("HISTORY_SCORE_CORRECTION_REQUIRES_VERSION")
                return False
            if existing.status not in ("SCHEDULED", "TIMED"):
                raise LiveHistoryIngestionError("HISTORY_EXISTING_STATUS_INVALID")
            existing.status = "FINISHED"
            existing.home_score = hg
            existing.away_score = ag
        else:
            db.add(Match(
                id=expected_id, season_id=season.id,
                home_team_id=home_id, away_team_id=away_id,
                kickoff_utc=kickoff, status="FINISHED",
                home_score=hg, away_score=ag,
                # The provider supplied no completion timestamp. Do not invent one.
                completed_at=None,
            ))
        db.add(SystemAuditLog(
            event_type="LIVE_HISTORY_INGESTED",
            description=f"{provider}:{provider_id}:{source_fp}"[:255],
        ))
        return True
