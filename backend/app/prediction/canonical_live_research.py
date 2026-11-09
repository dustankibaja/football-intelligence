"""Read-only live materialization from approved canonical PostgreSQL history.

RESEARCH_GATED ONLY: no production/shadow-ledger writes and no synthetic
fallback. The certified C1/M1 models are loaded from pinned immutable files.
A 4,338-match approved historical release must be present, with matching
fingerprint, before even attempting live inference.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import math
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import (
    HistoricalDatasetMatch,
    HistoricalDatasetRelease,
    Match,
    Provider,
    ProviderMatchMapping,
)
from app.features.assembler import FeatureSetAssembler
from app.features.manifest import get_mvp_a_feature_set_v1_manifest
from app.models.dataset_dto import ModelingRowDTO, assert_feature_label_isolation
from app.prediction.research_shadow import (
    ArtifactBackedResearchShadow,
    EXPECTED_TRAINING_DATASET_FINGERPRINT,
    ResearchShadowForecast,
)


EXPECTED_APPROVED_HISTORICAL_MATCHES = 4338


@dataclass(frozen=True)
class CanonicalMaterializationProof:
    canonical_match_id: UUID
    provider_fixture_id: str
    feature_keys: tuple[str, ...]
    feature_values: tuple[float | None, ...]
    snapshot_sha256: str
    dataset_sha256: str
    missing_feature_count: int
    materialized_at_utc: datetime
    historical_match_count: int
    research_only: bool = True


@dataclass(frozen=True)
class CanonicalShadowInference:
    proof: CanonicalMaterializationProof
    forecast: ResearchShadowForecast
    status: str = "RESEARCH_SHADOW_NOT_PRODUCTION_CERTIFIED"


class CanonicalPostgresShadowReader:
    """Resolve *actual* provider-to-canonical fixture mapping and Phase 3 features."""

    def __init__(self, predictor: ArtifactBackedResearchShadow | None = None):
        self.predictor = predictor or ArtifactBackedResearchShadow()
        self.manifest = get_mvp_a_feature_set_v1_manifest()
        frozen_order = self.predictor.c1.artifact.preprocessor_artifact.ordered_input_features
        if (
            self.manifest.feature_count != 251
            or self.manifest.included_feature_keys != frozen_order
        ):
            raise ValueError("CANONICAL_PHASE3_VS_FROZEN_C1_FEATURE_SCHEMA_MISMATCH")
        self.assembler = FeatureSetAssembler(self.manifest)

    @staticmethod
    def _validate_corpus(db: Session) -> HistoricalDatasetRelease:
        if db.bind is None or db.bind.dialect.name != "postgresql":
            raise ValueError("CANONICAL_LIVE_SNAPSHOT_REQUIRES_POSTGRESQL")
        releases = (
            db.query(HistoricalDatasetRelease)
            .filter(
                HistoricalDatasetRelease.key == "mvp_a_historical",
                HistoricalDatasetRelease.version == "v1",
            )
            .all()
        )
        if len(releases) != 1:
            raise ValueError("CANONICAL_APPROVED_HISTORICAL_RELEASE_MISSING")
        release = releases[0]
        if (
            release.status != "APPROVED"
            or release.dataset_fingerprint != EXPECTED_TRAINING_DATASET_FINGERPRINT
            or release.total_matches != EXPECTED_APPROVED_HISTORICAL_MATCHES
        ):
            raise ValueError("CANONICAL_HISTORICAL_RELEASE_UNVERIFIED")
        actual = (
            db.query(func.count(HistoricalDatasetMatch.id))
            .filter(HistoricalDatasetMatch.dataset_release_id == release.id)
            .scalar()
        )
        if actual != EXPECTED_APPROVED_HISTORICAL_MATCHES:
            raise ValueError("CANONICAL_HISTORY_MEMBERSHIP_COUNT_MISMATCH")
        return release

    @staticmethod
    def _resolve_mapped_fixture(
        db: Session, provider_name: str, provider_fixture_id: str
    ) -> Match:
        """No UUIDv5 placeholders or loose team/time fuzzy matching."""
        provider = (
            db.query(Provider)
            .filter_by(provider_key=provider_name, is_active=True)
            .one_or_none()
        )
        if provider is None:
            raise ValueError("CANONICAL_PROVIDER_NOT_REGISTERED")
        mapping = (
            db.query(ProviderMatchMapping)
            .filter_by(
                provider_id=provider.id,
                external_match_id=provider_fixture_id,
            )
            .one_or_none()
        )
        if mapping is None:
            raise ValueError("CANONICAL_PROVIDER_FIXTURE_MAPPING_MISSING")
        match = db.query(Match).filter_by(id=mapping.match_id).one_or_none()
        if match is None:
            raise ValueError("CANONICAL_FIXTURE_ROW_MISSING")
        return match

    def forecast(
        self,
        db: Session,
        *,
        provider_name: str,
        provider_fixture_id: str,
        as_of: datetime,
    ) -> CanonicalShadowInference:
        if as_of.tzinfo is None:
            raise ValueError("CANONICAL_CUTOFF_REQUIRES_UTC_AWARE_TIMESTAMP")
        as_of = as_of.astimezone(UTC)
        release = self._validate_corpus(db)
        match = self._resolve_mapped_fixture(db, provider_name, provider_fixture_id)
        kickoff = match.kickoff_utc
        if kickoff.tzinfo is None:
            raise ValueError("CANONICAL_MATCH_KICKOFF_MISSING_TIMEZONE")
        kickoff = kickoff.astimezone(UTC)
        if as_of >= kickoff:
            raise ValueError("CANONICAL_POST_KICKOFF_FORECAST_FORBIDDEN")
        if match.status != "SCHEDULED" or (
            match.home_score is not None or match.away_score is not None
        ):
            raise ValueError("CANONICAL_FIXTURE_ALREADY_LIVE_OR_FINISHED")
        if not match.season or not match.season.competition_id:
            raise ValueError("CANONICAL_COMPETITION_OR_SEASON_MISSING")
        if not match.home_team_id or not match.away_team_id:
            raise ValueError("CANONICAL_TEAM_IDENTITIES_UNVERIFIED")
        if match.home_team_id == match.away_team_id:
            raise ValueError("CANONICAL_DUPLICATE_HOME_AND_AWAY_TEAM")

        vector = self.assembler.assemble_feature_vector_for_match(
            db,
            match.id,
            cutoff_timestamp=as_of,
            dataset_key=release.key,
            dataset_version=release.version,
        )
        keys = [item.feature_key for item in vector.ordered_feature_values]
        values = [item.value for item in vector.ordered_feature_values]
        if (
            vector.feature_set_key != self.manifest.feature_set_key
            or vector.total_feature_count != 251
            or keys != self.manifest.included_feature_keys
            or len(vector.snapshot_fingerprint) != 64
            or len(values) != 251
        ):
            raise ValueError("CANONICAL_FEATURE_VECTOR_INTEGRITY_FAILED")
        assert_feature_label_isolation(keys)
        if any(
            value is not None and (
                not isinstance(value, (int, float)) or not math.isfinite(value)
            )
            for value in values
        ):
            raise ValueError("CANONICAL_NONFINITE_FEATURE_DETECTED")
        if (
            vector.missing_feature_count < 0
            or vector.missing_feature_count > 251
        ):
            raise ValueError("CANONICAL_MISSINGNESS_NOT_AUDITED")

        row = ModelingRowDTO(
            match_id=match.id,
            competition_id=match.season.competition_id,
            season_id=match.season_id,
            kickoff_utc=kickoff,
            temporal_precision="EXACT_TIMESTAMP",
            home_team_id=match.home_team_id,
            away_team_id=match.away_team_id,
            home_team_name=match.home_team.canonical_name if match.home_team else None,
            away_team_name=match.away_team.canonical_name if match.away_team else None,
            dataset_release_key=f"{release.key}:{release.version}",
            dataset_release_fingerprint=release.dataset_fingerprint,
            feature_set_key=self.manifest.feature_set_key,
            feature_set_fingerprint=vector.snapshot_fingerprint,
            ordered_feature_names=keys,
            feature_values=values,
            missingness_metadata={
                item.feature_key: str(item.missing_reason)
                for item in vector.ordered_feature_values if item.is_missing
            },
            targets={},
        )
        forecast = self.predictor.predict(row, prediction_time=as_of)
        proof = CanonicalMaterializationProof(
            canonical_match_id=match.id,
            provider_fixture_id=provider_fixture_id,
            feature_keys=tuple(keys),
            feature_values=tuple(values),
            snapshot_sha256=vector.snapshot_fingerprint,
            dataset_sha256=release.dataset_fingerprint,
            missing_feature_count=vector.missing_feature_count,
            materialized_at_utc=as_of,
            historical_match_count=EXPECTED_APPROVED_HISTORICAL_MATCHES,
        )
        return CanonicalShadowInference(proof=proof, forecast=forecast)
