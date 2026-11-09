"""SQL-backed result polling survives restarts and versions score corrections."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.automation.clock import FakeClock
from app.automation.domain import ExecutionMode
from app.automation.orchestrator import OperationalOrchestrator
from app.db.models import Base
from app.db.models.core import Competition, Match, Season, Team
from app.db.models.features import FeatureSet, FeatureSnapshot
from app.db.models.shadow import ShadowPredictionRun
from app.db.repositories.shadow import ShadowPredictionRepository
from app.features.manifest import get_mvp_a_feature_set_v1_manifest
from app.prediction.domain import ShadowPredictionInput
from app.prediction.frozen_inference import FROZEN_COMPETITION_CODES, FrozenArtifactInference
from app.prediction.scoring import PostMatchScoringService
from app.prediction.service import ShadowPredictionService
from app.providers.live.base import LiveFixtureStatus, LiveResultDTO


class FinalResultProvider:
    provider_name = "football_data_org"
    api_key = "test-only-not-a-live-credential"

    def __init__(self, observed):
        self.observed = observed
        self.scores = (2, 1)
        self.requested = []

    def get_fixture_results(self, provider_fixture_ids):
        self.requested.append(provider_fixture_ids)
        return [
            LiveResultDTO(
                provider_fixture_id=fid, provider_name=self.provider_name,
                status=LiveFixtureStatus.FINISHED,
                home_score=self.scores[0], away_score=self.scores[1],
                is_final=True, result_version=1,
                retrieved_at_utc=self.observed,
                payload_fingerprint="c" * 64, raw_payload={},
            )
            for fid in provider_fixture_ids
        ]


def test_result_polling_survives_restart_and_corrects_without_rewriting():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    frozen = FrozenArtifactInference()
    comp_id, comp_data = next(iter(frozen.m1.artifact.weights_payload["competitions"].items()))
    home_id, away_id = map(UUID, comp_data["team_list"][:2])
    observed = datetime(2026, 11, 5, 12, tzinfo=UTC)
    kickoff = observed - timedelta(days=1)
    predicted_at = kickoff - timedelta(hours=3)
    fixture_id, season_id, run_id, snapshot_id = uuid4(), uuid4(), uuid4(), uuid4()
    manifest = get_mvp_a_feature_set_v1_manifest()

    with factory() as db:
        db.add(Competition(
            id=UUID(comp_id), code=FROZEN_COMPETITION_CODES[comp_id],
            name="Result Polling League", country="Test",
        ))
        db.add_all([
            Team(id=home_id, canonical_name="Polling Home"),
            Team(id=away_id, canonical_name="Polling Away"),
        ])
        db.flush()
        db.add(Season(
            id=season_id, competition_id=UUID(comp_id),
            season_year="2026/27", start_year=2026,
        ))
        db.add(FeatureSet(name=manifest.feature_set_key, version=manifest.version))
        db.flush()
        feature_set_id = db.query(FeatureSet).one().id
        db.add(Match(
            id=fixture_id, season_id=season_id,
            home_team_id=home_id, away_team_id=away_id,
            kickoff_utc=kickoff, status="SCHEDULED",
        ))
        db.add(FeatureSnapshot(
            id=snapshot_id, match_id=fixture_id, feature_set_id=feature_set_id,
            snapshot_type="FINAL", feature_version=manifest.version,
            cutoff_timestamp=predicted_at,
            feature_values={key: 0.2 for key in manifest.included_feature_keys},
            missing_features={},
            data_quality_metadata={
                "fingerprint_sha256": "a" * 64,
                "history_fingerprint": "b" * 64,
                "source_provider": "football_data_org",
                "source_fixture_id": "verified-result-123",
            },
        ))
        db.add(ShadowPredictionRun(
            id=run_id, started_at=predicted_at,
            completed_at=predicted_at + timedelta(seconds=2),
            status="SUCCESS",
        ))
        db.commit()
        inp = ShadowPredictionInput(
            fixture_id=fixture_id,
            competition_code=FROZEN_COMPETITION_CODES[comp_id],
            competition_id=UUID(comp_id), season_id=season_id,
            season_year="2026/27", home_team_id=home_id, away_team_id=away_id,
            kickoff_utc=kickoff, prediction_time=predicted_at,
            feature_snapshot_id=snapshot_id,
            feature_snapshot_fingerprint="a" * 64,
            history_fingerprint="b" * 64,
            feature_count=251, feature_values=[0.2] * 251,
            feature_keys=manifest.included_feature_keys,
        )
        prediction = ShadowPredictionService(frozen_inference=frozen).predict(inp, run_id=run_id)
        _, created = ShadowPredictionRepository.persist_prediction(
            db, prediction, strict_live=True
        )
        assert created

    provider = FinalResultProvider(observed)
    scheduler = OperationalOrchestrator(
        clock=FakeClock(observed),
        execution_mode=ExecutionMode.LIVE_SHADOW,
        live_provider=provider,
        session_factory=factory,
    )
    summary = SimpleNamespace(retry_count=0)
    first = scheduler._fetch_live_due_results(summary)
    assert len(first) == 1
    assert first[0]["result_version"] == 1
    assert first[0]["home_goals"] == 2
    with factory() as db:
        evaluated = PostMatchScoringService().score_prediction(
            prediction, 2, 1, observed_at=observed
        )
        ShadowPredictionRepository.persist_evaluation(db, evaluated)
        match = db.query(Match).filter_by(id=fixture_id).one()
        match.status = "FINISHED"
        match.home_score = 2
        match.away_score = 1
        match.completed_at = observed
        db.commit()

    assert scheduler._fetch_live_due_results(summary) == []
    provider.scores = (3, 2)
    correction = scheduler._fetch_live_due_results(summary)
    assert len(correction) == 1
    assert correction[0]["result_version"] == 2
    assert (correction[0]["home_goals"], correction[0]["away_goals"]) == (3, 2)
    engine.dispose()
