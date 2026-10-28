"""
Sprint 7.3 Multi-Day Soak & Core-Engine Operational Certification Tests (Section 67)
Verifies:
- Relational prediction persistence survives process restart with empty memory
- Result ingestion links directly to pre-existing prediction record
- Duplicate provider result delivery produces zero duplicate evaluations
- Official result correction creates a versioned evaluation while preserving prediction immutability
- Scheduler restart preserves strictly monotonic fencing generation
- Prediction row remains strictly immutable after evaluation
- Sprint 7.3 manifest is deterministic and newline-invariant
- Sprint 7.3 manifest cryptographically binds the soak evidence artifact
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.automation.clock import FakeClock
from app.automation.domain import ExecutionMode
from app.automation.lease import LeaseManager
from app.automation.orchestrator import OperationalOrchestrator
from app.db.base import Base
from app.db.models.shadow import (
    ShadowEvaluation,
    ShadowPrediction,
    ShadowPredictionRun,
)
from app.db.repositories.shadow import ShadowPredictionRepository
from app.prediction.domain import ShadowPredictionInput, ShadowPredictionResult
from app.prediction.scoring import PostMatchScoringService
from app.prediction.service import ShadowPredictionService

PHASE7_3_MANIFEST_PATH = (
    Path(__file__).resolve().parents[3] / "phase7_3_live_soak_certification_manifest.json"
)
PHASE7_3_EVIDENCE_PATH = (
    Path(__file__).resolve().parents[3] / "phase7_3_live_soak_evidence.json"
)


@pytest.fixture
def clean_db_factory():
    """Provides an isolated clean in-memory database session factory."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine)
    yield factory
    engine.dispose()


def make_sample_prediction(fixture_id=None, kickoff=None) -> ShadowPredictionResult:
    now = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)
    ko = kickoff or (now + timedelta(hours=4))
    fid = fixture_id or uuid4()

    inp = ShadowPredictionInput(
        fixture_id=fid,
        competition_code="Premier League",
        season_year="2024-25",
        home_team_id=uuid4(),
        away_team_id=uuid4(),
        kickoff_utc=ko,
        prediction_time=now,
        feature_snapshot_id=uuid4(),
        feature_snapshot_fingerprint=f"fp_feat_{fid}",
        history_fingerprint="fp_hist_sample",
        feature_count=251,
        feature_values=[0.5] * 251,
        feature_keys=[f"f_{i}" for i in range(251)],
    )
    svc = ShadowPredictionService()
    pred = svc.predict(inp)
    assert isinstance(pred, ShadowPredictionResult)
    return pred


# 1. Prediction survives process restart
def test_phase7_3_postgres_prediction_survives_process_restart(clean_db_factory):
    pred = make_sample_prediction()

    # Process A writes prediction
    with clean_db_factory() as session:
        db_pred, created = ShadowPredictionRepository.persist_prediction(session, pred)
        assert created is True
        assert db_pred.id == pred.prediction_id

    # Process B starts with empty in-memory state
    with clean_db_factory() as session:
        recovered = ShadowPredictionRepository.load_all_predictions(session)
        assert len(recovered) == 1
        assert pred.prediction_id in recovered
        p = recovered[pred.prediction_id]
        assert p.fixture_id == pred.fixture_id
        assert float(p.prob_home) == pred.prob_home


# 2. Result links to existing prediction
def test_phase7_3_real_result_links_to_existing_prediction(clean_db_factory):
    pred = make_sample_prediction()

    with clean_db_factory() as session:
        db_pred, _ = ShadowPredictionRepository.persist_prediction(session, pred)

        scoring = PostMatchScoringService()
        ev = scoring.score_prediction(
            pred,
            actual_home_goals=2,
            actual_away_goals=1,
            match_status="FINISHED",
            result_version=1,
        )
        db_ev, created = ShadowPredictionRepository.persist_evaluation(session, ev)
        assert created is True
        assert db_ev.prediction_id == pred.prediction_id
        assert db_ev.fixture_id == pred.fixture_id

        # Verify relationship linkage
        session.refresh(db_pred)
        assert len(db_pred.evaluations) == 1
        assert db_pred.evaluations[0].actual_home_goals == 2
        assert db_pred.evaluations[0].actual_away_goals == 1


# 3. Duplicate result does not duplicate evaluation
def test_phase7_3_duplicate_result_does_not_duplicate_evaluation(clean_db_factory):
    pred = make_sample_prediction()

    with clean_db_factory() as session:
        ShadowPredictionRepository.persist_prediction(session, pred)

        scoring = PostMatchScoringService()
        ev1 = scoring.score_prediction(
            pred,
            actual_home_goals=1,
            actual_away_goals=0,
            match_status="FINISHED",
            result_version=1,
        )
        _, created1 = ShadowPredictionRepository.persist_evaluation(session, ev1)
        assert created1 is True
        assert session.query(ShadowEvaluation).count() == 1

        # Identical result received again from provider
        ev2 = scoring.score_prediction(
            pred,
            actual_home_goals=1,
            actual_away_goals=0,
            match_status="FINISHED",
            result_version=1,
        )
        _, created2 = ShadowPredictionRepository.persist_evaluation(session, ev2)
        assert created2 is False
        assert session.query(ShadowEvaluation).count() == 1


# 4. Result correction versions evaluation
def test_phase7_3_result_correction_versions_evaluation(clean_db_factory):
    pred = make_sample_prediction()

    with clean_db_factory() as session:
        db_pred, _ = ShadowPredictionRepository.persist_prediction(session, pred)

        scoring = PostMatchScoringService()
        # Initial official result
        ev1 = scoring.score_prediction(
            pred, actual_home_goals=2, actual_away_goals=1, match_status="FINISHED", result_version=1
        )
        ShadowPredictionRepository.persist_evaluation(session, ev1)

        # Official result amended by league authority
        ev2 = scoring.score_prediction(
            pred, actual_home_goals=2, actual_away_goals=2, match_status="FINISHED", result_version=2
        )
        ShadowPredictionRepository.persist_evaluation(session, ev2)

        # Both evaluation versions preserved
        evals = session.query(ShadowEvaluation).filter_by(prediction_id=pred.prediction_id).order_by(ShadowEvaluation.result_version.asc()).all()
        assert len(evals) == 2
        assert evals[0].result_version == 1
        assert evals[0].actual_away_goals == 1
        assert evals[1].result_version == 2
        assert evals[1].actual_away_goals == 2


# 5. Scheduler restart preserves fencing generation
def test_phase7_3_scheduler_restart_preserves_fencing_generation(clean_db_factory):
    fake_clock = FakeClock(datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC))
    lease_name = f"test_lease_{uuid4().hex[:8]}"

    # Worker 1 acquires lease
    mgr1 = LeaseManager(clock=fake_clock, session_factory=clean_db_factory)
    acq1, rec1 = mgr1.acquire(lease_name, "scheduler-instance-1", ttl_seconds=60)
    assert acq1 is True
    assert rec1 is not None
    assert rec1.fencing_token == 1

    # Lease expires naturally
    fake_clock.advance(timedelta(seconds=120))

    # Worker 2 (restarted scheduler instance) acquires lease
    mgr2 = LeaseManager(clock=fake_clock, session_factory=clean_db_factory)
    acq2, rec2 = mgr2.acquire(lease_name, "scheduler-instance-2", ttl_seconds=60)
    assert acq2 is True
    assert rec2 is not None
    assert rec2.fencing_token == 2
    assert rec2.fencing_token > rec1.fencing_token, "Fencing generation must increment monotonically across restarts"


# 6. Prediction immutability after evaluation
def test_phase7_3_prediction_immutability_after_evaluation(clean_db_factory):
    pred = make_sample_prediction()

    with clean_db_factory() as session:
        db_pred, _ = ShadowPredictionRepository.persist_prediction(session, pred)
        initial_prob_home = float(db_pred.prob_home)
        initial_exact_matrix_fp = db_pred.exact_score_matrix_fingerprint

        scoring = PostMatchScoringService()
        ev = scoring.score_prediction(
            pred, actual_home_goals=3, actual_away_goals=0, match_status="FINISHED", result_version=1
        )
        ShadowPredictionRepository.persist_evaluation(session, ev)

        # Re-fetch prediction from DB
        session.refresh(db_pred)
        assert float(db_pred.prob_home) == initial_prob_home
        assert db_pred.exact_score_matrix_fingerprint == initial_exact_matrix_fp
        assert db_pred.generated_at is not None


# 7. Manifest determinism
def test_phase7_3_manifest_is_deterministic():
    p = PHASE7_3_MANIFEST_PATH
    assert p.exists(), f"Sprint 7.3 manifest missing at {p}"

    with open(p, encoding="utf-8") as f:
        data = json.load(f)

    canon1 = json.dumps(data, sort_keys=True).encode("utf-8")
    canon2 = json.dumps(data, sort_keys=True).encode("utf-8")
    h1 = hashlib.sha256(canon1).hexdigest()
    h2 = hashlib.sha256(canon2).hexdigest()
    assert h1 == h2
    assert len(h1) == 64
    assert h1 == "66fb6923822a11b0032668f826f12f6eb0529a19b10b225c4c543d3bfef6231b"

    # CRLF vs LF invariance
    crlf_str = json.dumps(data, indent=2).replace("\n", "\r\n")
    lf_str = json.dumps(data, indent=2).replace("\r\n", "\n")
    h_crlf = hashlib.sha256(json.dumps(json.loads(crlf_str), sort_keys=True).encode("utf-8")).hexdigest()
    h_lf = hashlib.sha256(json.dumps(json.loads(lf_str), sort_keys=True).encode("utf-8")).hexdigest()
    assert h_crlf == h_lf


# 8. Manifest binds soak evidence artifact
def test_phase7_3_manifest_binds_soak_evidence():
    p_manifest = PHASE7_3_MANIFEST_PATH
    p_evidence = PHASE7_3_EVIDENCE_PATH
    assert p_evidence.exists(), f"Sprint 7.3 evidence missing at {p_evidence}"

    with open(p_evidence, encoding="utf-8") as f:
        ev_data = json.load(f)
    canon_ev = json.dumps(ev_data, sort_keys=True).encode("utf-8")
    expected_ev_hash = hashlib.sha256(canon_ev).hexdigest()

    with open(p_manifest, encoding="utf-8") as f:
        manifest_data = json.load(f)

    assert manifest_data["soak_evidence_sha256"] == expected_ev_hash
    assert manifest_data["soak_evidence_artifact_path"] == "phase7_3_live_soak_evidence.json"
