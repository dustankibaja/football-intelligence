"""
Cutoff-Safe Point-in-Time Historical Query Service
Retrieves historical match data strictly bounded by cutoff_timestamp and approved dataset release to prevent future data leakage.
"""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.dataset.base import DatasetReleaseStatusEnum, DatasetValidationError
from app.db.models import HistoricalDatasetMatch, HistoricalDatasetRelease, Match


class PointInTimeQueryService:
    """Historical Point-in-Time Query Helper enforcing strict anti-leakage time bounds & dataset release scoping."""

    def __init__(
        self,
        db_session: Session,
        dataset_key: str = "mvp_a_historical",
        version: str = "v1",
        approved_only: bool = True,
        enforce_date_only_precision: bool = True,
    ) -> None:
        self.db = db_session
        self.dataset_key = dataset_key
        self.version = version
        self.approved_only = approved_only
        self.enforce_date_only_precision = enforce_date_only_precision
        self._release_id: UUID | None = None

    def _get_dataset_release_id(self) -> UUID | None:
        """Retrieves and caches the dataset release ID, enforcing approved status when required."""
        if self._release_id is not None:
            return self._release_id

        query = self.db.query(HistoricalDatasetRelease).filter_by(key=self.dataset_key, version=self.version)
        release = query.first()

        if not release:
            release = self.db.query(HistoricalDatasetRelease).filter(
                HistoricalDatasetRelease.key.like(f"{self.dataset_key}%"),
                HistoricalDatasetRelease.version == self.version,
            ).first()

        if not release:
            total_releases = self.db.query(HistoricalDatasetRelease).count()
            if total_releases > 0 and self.approved_only:
                raise DatasetValidationError(
                    f"Historical dataset release '{self.dataset_key}:{self.version}' does not exist."
                )
            return None

        if self.approved_only and release.status != DatasetReleaseStatusEnum.APPROVED.value:
            raise DatasetValidationError(
                f"Dataset release '{self.dataset_key}:{self.version}' is in status '{release.status}'. "
                "Official point-in-time feature calculations require APPROVED datasets."
            )

        self._release_id = release.id
        return self._release_id

    def get_team_matches_before(
        self,
        team_id: UUID,
        cutoff_timestamp: datetime,
        limit: int | None = None,
        home_only: bool = False,
        away_only: bool = False,
        target_match_id: UUID | None = None,
        dataset_release_id: UUID | None = None,
        season_id: UUID | None = None,
    ) -> list[Match]:
        """Retrieves finished matches for a team strictly before cutoff_timestamp within the dataset release."""
        if cutoff_timestamp.tzinfo is None:
            cutoff_timestamp = cutoff_timestamp.replace(tzinfo=UTC)

        rel_id = dataset_release_id or self._get_dataset_release_id()

        query = self.db.query(Match).filter(
            Match.status == "FINISHED",
            Match.home_score.isnot(None),
            Match.away_score.isnot(None),
            # A finished result observed after the prediction cutoff is
            # forbidden even when the match kicked off earlier.
            (Match.completed_at.is_(None)) | (Match.completed_at < cutoff_timestamp),
        )

        if self.enforce_date_only_precision:
            # Conservative rule: exclude same-day matches when kickoff sub-day precision is unknown
            cutoff_start_of_day = datetime(
                cutoff_timestamp.year, cutoff_timestamp.month, cutoff_timestamp.day, 0, 0, 0, tzinfo=UTC
            )
            query = query.filter(Match.kickoff_utc < cutoff_start_of_day)
        else:
            query = query.filter(Match.kickoff_utc < cutoff_timestamp)

        if rel_id:
            query = query.join(HistoricalDatasetMatch, Match.id == HistoricalDatasetMatch.match_id).filter(
                HistoricalDatasetMatch.dataset_release_id == rel_id
            )

        if season_id:
            query = query.filter(Match.season_id == season_id)

        if target_match_id:
            query = query.filter(Match.id != target_match_id)

        if home_only:
            query = query.filter(Match.home_team_id == team_id)
        elif away_only:
            query = query.filter(Match.away_team_id == team_id)
        else:
            query = query.filter((Match.home_team_id == team_id) | (Match.away_team_id == team_id))

        query = query.order_by(Match.kickoff_utc.desc())

        if limit:
            query = query.limit(limit)

        return query.all()

    def get_league_matches_before(
        self,
        competition_id: UUID,
        cutoff_timestamp: datetime,
        season_id: UUID | None = None,
        dataset_release_id: UUID | None = None,
    ) -> list[Match]:
        """Retrieves all finished league matches strictly before cutoff_timestamp within the dataset release."""
        if cutoff_timestamp.tzinfo is None:
            cutoff_timestamp = cutoff_timestamp.replace(tzinfo=UTC)

        rel_id = dataset_release_id or self._get_dataset_release_id()

        query = self.db.query(Match).join(Match.season).filter(
            Match.status == "FINISHED",
            Match.home_score.isnot(None),
            Match.away_score.isnot(None),
            (Match.completed_at.is_(None)) | (Match.completed_at < cutoff_timestamp),
        )

        if self.enforce_date_only_precision:
            cutoff_start_of_day = datetime(
                cutoff_timestamp.year, cutoff_timestamp.month, cutoff_timestamp.day, 0, 0, 0, tzinfo=UTC
            )
            query = query.filter(Match.kickoff_utc < cutoff_start_of_day)
        else:
            query = query.filter(Match.kickoff_utc < cutoff_timestamp)

        if rel_id:
            query = query.join(HistoricalDatasetMatch, Match.id == HistoricalDatasetMatch.match_id).filter(
                HistoricalDatasetMatch.dataset_release_id == rel_id
            )

        if season_id:
            query = query.filter(Match.season_id == season_id)
        else:
            query = query.filter(Match.season.has(competition_id=competition_id))

        return query.order_by(Match.kickoff_utc.desc()).all()

    def get_previous_match(
        self,
        team_id: UUID,
        cutoff_timestamp: datetime,
        target_match_id: UUID | None = None,
        dataset_release_id: UUID | None = None,
        season_id: UUID | None = None,
    ) -> Match | None:
        """Retrieves the single most recent match for a team prior to cutoff_timestamp."""
        matches = self.get_team_matches_before(
            team_id, cutoff_timestamp, limit=1, target_match_id=target_match_id, dataset_release_id=dataset_release_id, season_id=season_id
        )
        return matches[0] if matches else None


import functools

class HistoricalQueryLayer:
    """Backward-compatible static wrapper for HistoricalQueryLayer delegating to PointInTimeQueryService."""

    @staticmethod
    @functools.lru_cache(maxsize=4096)
    def _cached_get_team_matches_before(
        db: Session,
        team_id: UUID,
        cutoff_timestamp: datetime,
        home_only: bool = False,
        away_only: bool = False,
        approved_only: bool = False,
        enforce_date_only_precision: bool = False,
        season_id: UUID | None = None,
    ) -> tuple:
        service = PointInTimeQueryService(
            db, approved_only=approved_only, enforce_date_only_precision=enforce_date_only_precision
        )
        return tuple(service.get_team_matches_before(
            team_id,
            cutoff_timestamp,
            limit=None,
            home_only=home_only,
            away_only=away_only,
            target_match_id=None,
            season_id=season_id,
        ))

    @staticmethod
    def get_team_matches_before(
        db: Session,
        team_id: UUID,
        cutoff_timestamp: datetime,
        limit: int | None = None,
        home_only: bool = False,
        away_only: bool = False,
        target_match_id: UUID | None = None,
        approved_only: bool = False,
        enforce_date_only_precision: bool = False,
        season_id: UUID | None = None,
    ) -> tuple:
        if enforce_date_only_precision:
            cutoff_timestamp = datetime(cutoff_timestamp.year, cutoff_timestamp.month, cutoff_timestamp.day, 0, 0, 0, tzinfo=UTC)
        matches = HistoricalQueryLayer._cached_get_team_matches_before(
            db, team_id, cutoff_timestamp, home_only, away_only,
            approved_only, enforce_date_only_precision, season_id
        )
        if target_match_id:
            matches = tuple(m for m in matches if m.id != target_match_id)
        if limit is not None:
            matches = matches[:limit]
        return matches

    @staticmethod
    def get_league_matches_before(
        db: Session,
        competition_id: UUID,
        cutoff_timestamp: datetime,
        season_id: UUID | None = None,
        approved_only: bool = False,
        enforce_date_only_precision: bool = False,
    ) -> tuple:
        if enforce_date_only_precision:
            cutoff_timestamp = datetime(cutoff_timestamp.year, cutoff_timestamp.month, cutoff_timestamp.day, 0, 0, 0, tzinfo=UTC)
        return HistoricalQueryLayer._cached_get_league_matches_before(
            db, competition_id, cutoff_timestamp, season_id, approved_only, enforce_date_only_precision
        )

    @staticmethod
    @functools.lru_cache(maxsize=1024)
    def _cached_get_league_matches_before(
        db: Session,
        competition_id: UUID,
        cutoff_timestamp: datetime,
        season_id: UUID | None = None,
        approved_only: bool = False,
        enforce_date_only_precision: bool = False,
    ) -> tuple:
        service = PointInTimeQueryService(
            db, approved_only=approved_only, enforce_date_only_precision=enforce_date_only_precision
        )
        return tuple(service.get_league_matches_before(
            competition_id, cutoff_timestamp, season_id=season_id
        ))

    @staticmethod
    def get_previous_match(
        db: Session,
        team_id: UUID,
        cutoff_timestamp: datetime,
        target_match_id: UUID | None = None,
        approved_only: bool = False,
        enforce_date_only_precision: bool = False,
        season_id: UUID | None = None,
    ) -> Match | None:
        matches = HistoricalQueryLayer.get_team_matches_before(
            db, team_id, cutoff_timestamp, limit=1, target_match_id=target_match_id,
            approved_only=approved_only, enforce_date_only_precision=enforce_date_only_precision, season_id=season_id
        )
        return matches[0] if matches else None
