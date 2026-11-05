"""End-to-end strict SQL persistence of a genuine frozen-artifact prediction."""
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints.predictions import _verified
from app.db.models import Base
from app.db.models.core import Competition, Match, Season, Team
from app.db.models.features import FeatureSet, FeatureSnapshot
from app.db.models.shadow import ShadowPrediction, ShadowPredictionRun
from app.db.repositories.shadow import ShadowPredictionRepository
from app.features.manifest import get_mvp_a_feature_set_v1_manifest
from app.prediction.domain import ShadowPredictionInput
from app.prediction.frozen_inference import (
    FROZEN_COMPETITION_CODES, FrozenArtifactInference,
)
from app.prediction.service import ShadowPredictionService


def test_genuine_frozen_prediction_persists_without_any_stub_entities():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    frozen = FrozenArtifactInference()
    comp_id, comp_data = next(iter(frozen.m1.artifact.weights_payload["competitions"].items()))
    home_id, away_id = map(UUID, comp_data["team_list"][:2])
    now = datetime(2026, 11, 5, 12, tzinfo=UTC)
    kickoff = now + timedelta(hours=3)
    fixture_id, season_id, run_id, snap_id = uuid4(), uuid4(), uuid4(), uuid4()
    manifest = get_mvp_a_feature_set_v1_manifest()
    feature_fp, history_fp = "a" * 64, "b" * 64

    with factory() as db:
        db.add(Competition(
            id=UUID(comp_id), code=FROZEN_COMPETITION_CODES[comp_id],
            name="Verified League", country="Verified",
        ))
        db.add_all([
            Team(id=home_id, canonical_name="Verified Home"),
            Team(id=away_id, canonical_name="Verified Away"),
        ])
        db.flush()
        db.add(Season(
            id=season_id, competition_id=UUID(comp_id),
            season_year="2026/27", start_year=2026,
        ))
        db.add(FeatureSet(name=manifest.feature_set_key, version=manifest.version))
        db.flush()
        fset = db.query(FeatureSet).one()
        db.add(Match(
            id=fixture_id, season_id=season_id,
            home_team_id=home_id, away_team_id=away_id,
            kickoff_utc=kickoff, status="SCHEDULED",
        ))
        db.add(FeatureSnapshot(
            id=snap_id, match_id=fixture_id, feature_set_id=fset.id,
            snapshot_type="FINAL", feature_version=manifest.version,
            cutoff_timestamp=now,
            feature_values={key: 0.2 for key in manifest.included_feature_keys},
            missing_features={},
            data_quality_metadata={
                "fingerprint_sha256": feature_fp,
                "history_fingerprint": history_fp,
                "source_provider": "football_data_org",
                "source_fixture_id": "verified-123",
            },
        ))
        db.add(ShadowPredictionRun(
            id=run_id, started_at=now, status="RUNNING",
        ))
        db.commit()

        inp = ShadowPredictionInput(
            fixture_id=fixture_id,
            competition_code=FROZEN_COMPETITION_CODES[comp_id],
            competition_id=UUID(comp_id),
            season_id=season_id,
            season_year="2026/27",
            home_team_id=home_id,
            away_team_id=away_id,
            kickoff_utc=kickoff,
            prediction_time=now,
            feature_snapshot_id=snap_id,
            feature_snapshot_fingerprint=feature_fp,
            history_fingerprint=history_fp,
            feature_count=251,
            feature_values=[0.2] * 251,
            feature_keys=manifest.included_feature_keys,
        )
        prediction = ShadowPredictionService(frozen_inference=frozen).predict(
            inp, run_id=run_id
        )
        ShadowPredictionRepository.verify_certified_live_prediction(
            db, prediction, allow_running=True
        )
        saved, created = ShadowPredictionRepository.persist_prediction(
            db, prediction, strict_live=True
        )
        assert created is True
        assert saved.feature_snapshot_id == snap_id
        assert saved.prediction_run_id == run_id
        assert db.query(ShadowPrediction).count() == 1
        assert not _verified(saved), "In-flight run must never reach customer API"
        run = db.query(ShadowPredictionRun).filter_by(id=run_id).one()
        run.status = "SUCCESS"
        run.completed_at = now + timedelta(seconds=10)
        db.commit()
        db.refresh(saved)
        assert _verified(saved), "Only completed, provenanced predictions are exposed"
    engine.dispose()
