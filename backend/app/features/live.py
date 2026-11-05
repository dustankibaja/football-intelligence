"""Canonical, point-in-time live fixture feature materialization.

Never fabricate a match, team, season, history, feature or fingerprint. The
historical feature calculators are reused unchanged; this adapter only supplies
a verified live Match, source history lineage and durable FeatureSnapshot.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db.models.core import Competition, Match, Season, Team
from app.db.models.features import FeatureSnapshot
from app.features.assembler import FeatureSetAssembler
from app.features.manifest import get_mvp_a_feature_set_v1_manifest
from app.features.query import HistoricalQueryLayer
from app.features.sets import FeatureSetManager


class LiveFeatureMaterializationError(ValueError):
    """Provider/canonical/observation evidence insufficient for live inference."""


def _utc(dt: datetime) -> datetime:
    if not isinstance(dt, datetime) or dt.tzinfo is None:
        raise LiveFeatureMaterializationError("LIVE_TIMESTAMP_MUST_BE_UTC_AWARE")
    return dt.astimezone(UTC)


class LiveFeatureMaterializer:
    """Materializes one immutable 251-feature snapshot in a database session."""

    def __init__(self, session_factory, max_history_age_days: int = 180):
        if session_factory is None:
            raise LiveFeatureMaterializationError("LIVE_DATABASE_REQUIRED")
        self.session_factory = session_factory
        self.max_history_age_days = max_history_age_days

    def materialize(self, record: dict, cutoff: datetime) -> dict:
        cutoff = _utc(cutoff)
        kickoff = _utc(record.get("kickoff_utc"))
        if cutoff >= kickoff:
            raise LiveFeatureMaterializationError("POST_KICKOFF_FEATURE_MATERIALIZATION")
        if not record.get("is_mapped"):
            raise LiveFeatureMaterializationError("UNRESOLVED_PROVIDER_MAPPING")
        provider = record.get("provider_name")
        provider_id = record.get("provider_fixture_id")
        source_fp = record.get("payload_fingerprint")
        if not all((provider, provider_id, source_fp)):
            raise LiveFeatureMaterializationError("LIVE_PROVIDER_PROVENANCE_MISSING")
        fixture_id = uuid5(NAMESPACE_URL, f"{provider}:{provider_id}")
        if record.get("fixture_id") != fixture_id:
            raise LiveFeatureMaterializationError("PROVIDER_FIXTURE_IDENTITY_MISMATCH")

        home_id, away_id = record.get("home_team_id"), record.get("away_team_id")
        if not isinstance(home_id, UUID) or not isinstance(away_id, UUID) or home_id == away_id:
            raise LiveFeatureMaterializationError("INVALID_CANONICAL_TEAM_IDS")

        with self.session_factory() as db:
            competition = db.query(Competition).filter_by(code=record.get("competition_code")).first()
            if competition is None:
                raise LiveFeatureMaterializationError("UNKNOWN_CANONICAL_COMPETITION")
            season = db.query(Season).filter_by(
                competition_id=competition.id, season_year=record.get("season_year")
            ).first()
            if season is None and str(record.get("season_year", "")).isdigit():
                seasons = db.query(Season).filter_by(
                    competition_id=competition.id, start_year=int(record["season_year"])
                ).all()
                if len(seasons) == 1:
                    season = seasons[0]
            if season is None:
                raise LiveFeatureMaterializationError("UNKNOWN_CANONICAL_SEASON")
            if db.query(Team).filter(Team.id.in_([home_id, away_id])).count() != 2:
                raise LiveFeatureMaterializationError("CANONICAL_TEAM_NOT_REGISTERED")

            match = db.query(Match).filter_by(id=fixture_id).first()
            if match is not None:
                if (
                    match.season_id != season.id
                    or match.home_team_id != home_id
                    or match.away_team_id != away_id
                    or _utc(match.kickoff_utc.replace(tzinfo=UTC) if match.kickoff_utc.tzinfo is None else match.kickoff_utc) != kickoff
                ):
                    raise LiveFeatureMaterializationError("CANONICAL_FIXTURE_CONFLICT")
            else:
                match = Match(
                    id=fixture_id, season_id=season.id,
                    home_team_id=home_id, away_team_id=away_id,
                    kickoff_utc=kickoff, status="SCHEDULED",
                )
                db.add(match)
                db.flush()

            # Restrict observations to completed pre-cutoff fixtures with
            # non-null scores. The conservative date-only cutoff mirrors
            # the frozen historical calculators' same-day exclusion.
            start_of_day = cutoff.replace(hour=0, minute=0, second=0, microsecond=0)
            histories = db.query(Match).filter(
                Match.id != fixture_id,
                Match.status == "FINISHED",
                Match.home_score.isnot(None),
                Match.away_score.isnot(None),
                Match.kickoff_utc < start_of_day,
                or_(Match.completed_at.is_(None), Match.completed_at < cutoff),
                or_(
                    Match.home_team_id.in_([home_id, away_id]),
                    Match.away_team_id.in_([home_id, away_id]),
                ),
            ).order_by(Match.kickoff_utc.desc(), Match.id).limit(120).all()
            recent_cutoff = cutoff - timedelta(days=self.max_history_age_days)
            home_recent = any(
                (m.home_team_id == home_id or m.away_team_id == home_id)
                and _utc(m.kickoff_utc.replace(tzinfo=UTC) if m.kickoff_utc.tzinfo is None else m.kickoff_utc) >= recent_cutoff
                for m in histories
            )
            away_recent = any(
                (m.home_team_id == away_id or m.away_team_id == away_id)
                and _utc(m.kickoff_utc.replace(tzinfo=UTC) if m.kickoff_utc.tzinfo is None else m.kickoff_utc) >= recent_cutoff
                for m in histories
            )
            if not home_recent or not away_recent:
                raise LiveFeatureMaterializationError("CURRENT_TEAM_HISTORY_MISSING_OR_STALE")

            history_records = [
                (str(m.id), m.kickoff_utc.isoformat(), str(m.home_team_id),
                 str(m.away_team_id), m.home_score, m.away_score)
                for m in sorted(histories, key=lambda x: (x.kickoff_utc, str(x.id)))
            ]
            history_fp = hashlib.sha256(
                json.dumps(history_records, separators=(",", ":")).encode("utf-8")
            ).hexdigest()

            manifest = get_mvp_a_feature_set_v1_manifest()
            fset = FeatureSetManager.get_or_create_mvp_a_feature_set(db)
            existing = db.query(FeatureSnapshot).filter_by(
                match_id=fixture_id, feature_set_id=fset.id, snapshot_type="FINAL"
            ).first()
            if existing is not None:
                meta = existing.data_quality_metadata or {}
                if (
                    meta.get("source_provider") != provider
                    or meta.get("source_fixture_id") != provider_id
                    or not meta.get("source_payload_fingerprint")
                    or not meta.get("history_fingerprint")
                    or not meta.get("fingerprint_sha256")
                    or meta.get("feature_keys") != manifest.included_feature_keys
                ):
                    raise LiveFeatureMaterializationError("EXISTING_SNAPSHOT_PROVENANCE_INVALID")
                snapshot = existing
                was_created = False
            else:
                # Prevent process-local cross-session cache reuse from
                # contaminating point-in-time live feature calculations.
                HistoricalQueryLayer._cached_get_team_matches_before.cache_clear()
                HistoricalQueryLayer._cached_get_league_matches_before.cache_clear()
                assembled = FeatureSetAssembler(manifest).assemble_feature_vector_for_match(
                    db, match_id=fixture_id, cutoff_timestamp=cutoff
                )
                if assembled.total_feature_count != 251:
                    raise LiveFeatureMaterializationError("LIVE_FEATURE_COUNT_MISMATCH")
                values = assembled.feature_vector_dict
                if list(values) != manifest.included_feature_keys:
                    raise LiveFeatureMaterializationError("LIVE_FEATURE_ORDER_MISMATCH")
                snapshot = FeatureSnapshot(
                    match_id=fixture_id,
                    feature_set_id=fset.id,
                    snapshot_type="FINAL",
                    feature_version=manifest.version,
                    cutoff_timestamp=cutoff,
                    feature_values=values,
                    missing_features={
                        r.feature_key: str(r.missing_reason)
                        for r in assembled.ordered_feature_values if r.is_missing
                    },
                    data_quality_metadata={
                        "fingerprint_sha256": assembled.snapshot_fingerprint,
                        "history_fingerprint": history_fp,
                        "source_provider": provider,
                        "source_fixture_id": provider_id,
                        "source_payload_fingerprint": source_fp,
                        "source_retrieved_at": str(record.get("retrieved_at_utc")),
                        "feature_keys": manifest.included_feature_keys,
                        "history_observation_count": len(histories),
                        "cutoff_timestamp": cutoff.isoformat(),
                        "drift_status": "NOT_MEASURED",
                    },
                )
                db.add(snapshot)
                db.flush()
                was_created = True

            metadata = snapshot.data_quality_metadata
            values = snapshot.feature_values
            if list(values) != manifest.included_feature_keys:
                raise LiveFeatureMaterializationError("SNAPSHOT_FEATURE_ORDER_INVALID")
            missing_count = sum(v is None for v in values.values())
            if missing_count == 251:
                raise LiveFeatureMaterializationError("ALL_LIVE_FEATURES_MISSING")
            db.commit()
            return {
                **record,
                "competition_id": competition.id,
                "season_id": season.id,
                "feature_snapshot_id": snapshot.id,
                "feature_snapshot_fingerprint": metadata["fingerprint_sha256"],
                "history_fingerprint": metadata["history_fingerprint"],
                "feature_values": list(values.values()),
                "feature_keys": manifest.included_feature_keys,
                "snapshot_quality_state": "VALID",
                "missingness_count": missing_count,
                "known_cold_start": False,
                "critical_feature_absent": False,
                "drift_psi": None,
                "drift_status": "NOT_MEASURED",
                "needs_snapshot": was_created,
            }
