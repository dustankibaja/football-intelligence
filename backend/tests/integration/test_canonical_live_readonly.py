"""Canonical 251-feature live research reader refuses absent or synthetic history.

This is a *mechanical* PostgreSQL safety-gate test. It is NOT a substitute for
the unavailable approved 4,338-match corpus, nor a genuine live accuracy test.
"""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.db.models import (
    Competition,
    HistoricalDatasetRelease,
    Match,
    Provider,
    ProviderMatchMapping,
    Season,
    Team,
)
from app.prediction.canonical_live_research import (
    CanonicalPostgresShadowReader,
    EXPECTED_APPROVED_HISTORICAL_MATCHES,
)
from app.prediction.research_shadow import (
    EXPECTED_TRAINING_DATASET_FINGERPRINT,
)


@pytest.mark.integration
def test_readonly_canonical_scoring_refuses_absent_approved_corpus(db_session):
    if db_session.bind.dialect.name != "postgresql":
        pytest.skip("Genuine PostgreSQL CI-only database boundary")
    reader = CanonicalPostgresShadowReader()
    assert reader.manifest.feature_count == 251
    with pytest.raises(ValueError, match="CANONICAL_APPROVED_HISTORICAL_RELEASE_MISSING"):
        reader.forecast(
            db_session,
            provider_name="football_data_org",
            provider_fixture_id="never_imported",
            as_of=datetime.now(UTC),
        )


@pytest.mark.integration
def test_readonly_canonical_scoring_refuses_fake_approved_metadata(db_session):
    if db_session.bind.dialect.name != "postgresql":
        pytest.skip("Genuine PostgreSQL CI-only database boundary")
    release = HistoricalDatasetRelease(
        key="mvp_a_historical", version="v1", status="APPROVED",
        total_matches=1, dataset_fingerprint=EXPECTED_TRAINING_DATASET_FINGERPRINT,
    )
    db_session.add(release)
    db_session.flush()
    reader = CanonicalPostgresShadowReader()
    with pytest.raises(ValueError, match="CANONICAL_HISTORICAL_RELEASE_UNVERIFIED"):
        reader.forecast(
            db_session,
            provider_name="football_data_org",
            provider_fixture_id="never_imported",
            as_of=datetime.now(UTC),
        )
    assert EXPECTED_APPROVED_HISTORICAL_MATCHES == 4338


@pytest.mark.integration
def test_provider_fixture_resolution_requires_actual_database_mapping(db_session):
    if db_session.bind.dialect.name != "postgresql":
        pytest.skip("Genuine PostgreSQL CI-only database boundary")
    reader = CanonicalPostgresShadowReader()
    provider = Provider(
        provider_key="source_verified_" + uuid4().hex[:8],
        display_name="Verified Research Provider",
        is_active=True,
    )
    competition = Competition(
        code="CLV_" + uuid4().hex[:8], name="Canonical Gate Test",
        country="Test",
    )
    season = Season(competition=competition, season_year="2026/27")
    a = Team(canonical_name="Canonical A " + uuid4().hex[:8])
    b = Team(canonical_name="Canonical B " + uuid4().hex[:8])
    target = Match(
        season=season, home_team=a, away_team=b,
        kickoff_utc=datetime.now(UTC) + timedelta(days=10),
        status="SCHEDULED",
    )
    mapping = ProviderMatchMapping(
        provider=provider, match=target, external_match_id="provider_001"
    )
    db_session.add_all([provider, competition, season, a, b, target, mapping])
    db_session.flush()

    assert reader._resolve_mapped_fixture(
        db_session, provider.provider_key, "provider_001"
    ).id == target.id
    with pytest.raises(ValueError, match="CANONICAL_PROVIDER_FIXTURE_MAPPING_MISSING"):
        reader._resolve_mapped_fixture(
            db_session, provider.provider_key, "arbitrary_provider_uuid5"
        )
    with pytest.raises(ValueError, match="CANONICAL_PROVIDER_NOT_REGISTERED"):
        reader._resolve_mapped_fixture(
            db_session, "source_not_registered", "provider_001"
        )


def test_approved_canonical_reader_never_invents_other_model():
    reader = CanonicalPostgresShadowReader()
    assert reader.predictor.c1.artifact.model_key == "multinomial_logistic_1x2_v1"
    assert reader.predictor.m1.artifact.model_key == "attack_defense_poisson_v1"
    assert reader.predictor.c1.artifact.verify_integrity()
    assert reader.predictor.m1.artifact.verify_integrity()
    assert reader.manifest.included_feature_keys == (
        reader.predictor.c1.artifact.preprocessor_artifact.ordered_input_features
    )
