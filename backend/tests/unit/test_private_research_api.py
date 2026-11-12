"""Product API regression: authorization, pagination, canonical-only shadow reads."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.core.config import settings
from app.db import session as db_module
from app.db.models import Competition, FeatureSet, Match, Model, ModelVersion, Season, ShadowEvaluation, ShadowPrediction, Team

TOKEN = "test-private-token-" + "x" * 32
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def test_private_reads_fail_closed_when_unconfigured_or_unauthorized(client, monkeypatch):
    endpoints = ("matches", "predictions", "models", "evaluations")
    monkeypatch.setattr(settings, "INTERNAL_RESEARCH_READ_TOKEN", None)
    for path in endpoints:
        assert client.get(f"/api/v1/{path}/").status_code == 503
    monkeypatch.setattr(settings, "INTERNAL_RESEARCH_READ_TOKEN", SecretStr(TOKEN))
    for path in endpoints:
        assert client.get(f"/api/v1/{path}/").status_code == 401
        assert client.get(f"/api/v1/{path}/", headers={"Authorization": "Bearer wrong"}).status_code == 401
        r = client.get(f"/api/v1/{path}/", headers=HEADERS)
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-store"
        assert r.json()["publication_eligible"] is False
        assert r.json()["scope"] == "PRIVATE_RESEARCH_READ_ONLY"


def test_real_private_read_models_and_canonical_evidence(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "INTERNAL_RESEARCH_READ_TOKEN", SecretStr(TOKEN))
    key = uuid4().hex[:10]
    kickoff = datetime(2026, 11, 21, 15, tzinfo=UTC)
    c = Competition(code=f"Q{key}", name="QA League", country="QA")
    db_session.add(c); db_session.flush()
    season = Season(competition_id=c.id, season_year="2026/27")
    home = Team(canonical_name=f"Home {key}")
    away = Team(canonical_name=f"Away {key}")
    feature = FeatureSet(name=f"Features {key}", version="1")
    model = Model(model_key=f"C1_{key}", name="Canonical test")
    db_session.add_all([season, home, away, feature, model]); db_session.flush()
    match = Match(season_id=season.id, home_team_id=home.id, away_team_id=away.id,
                  kickoff_utc=kickoff, status="FINISHED", home_score=2, away_score=1)
    version = ModelVersion(model_id=model.id, feature_set_id=feature.id, version="1",
                           lifecycle_status="EXPERIMENTAL", artifact_path="/private/artifact")
    db_session.add_all([match, version]); db_session.flush()
    def prediction(canonical, generated):
        return ShadowPrediction(
            fixture_id=match.id, prediction_mode="SHADOW", generated_at=generated,
            kickoff_utc=kickoff, lead_time_seconds=int((kickoff-generated).total_seconds()),
            feature_snapshot_fingerprint="a"*64, history_fingerprint="b"*64,
            phase5_manifest_hash="c"*64, phase5_2_policy_hash="d"*64,
            phase6_1_manifest_hash="e"*64, phase6_2_manifest_hash="f"*64,
            c1_model_key="C1", c1_model_version="1", c1_artifact_fingerprint="1"*64,
            m1_model_key="M1", m1_model_version="1", m1_artifact_fingerprint="2"*64,
            prob_home=.5, prob_draw=.25, prob_away=.25,
            structural_prob_home=.45, structural_prob_draw=.3, structural_prob_away=.25,
            lambda_home=1.6, lambda_away=1.2, btts_yes=.55, btts_no=.45,
            over_2_5=.5, under_2_5=.5, exact_score_matrix_fingerprint="3"*64,
            top_scorelines=[], quality_state="READY", fallback_used=False,
            model_disagreement_value=.05, model_disagreement_band="LOW",
            is_canonical_evaluation_prediction=canonical,
        )
    good = prediction(True, kickoff-timedelta(hours=24))
    excluded = prediction(False, kickoff-timedelta(hours=6))
    db_session.add_all([good, excluded]); db_session.flush()
    ev = ShadowEvaluation(
        prediction_id=good.id, fixture_id=match.id, result_observed_at=kickoff+timedelta(hours=3),
        actual_home_goals=2, actual_away_goals=1, actual_1x2="HOME", actual_btts=True,
        actual_over_2_5=True, log_loss_1x2=.693, brier_1x2=.375, btts_log_loss=.6,
        btts_brier=.2, ou25_log_loss=.69, ou25_brier=.25,
        actual_score_probability=.09, top1_score_hit=False, top3_score_hit=True,
    )
    db_session.add(ev); db_session.flush()
    response = client.get("/api/v1/matches/?status=FINISHED&limit=100", headers=HEADERS)
    assert response.status_code == 200
    found = next(x for x in response.json()["items"] if x["id"]==str(match.id))
    assert found["competition"]==c.code and found["home_team"]==home.canonical_name
    predictions = client.get(f"/api/v1/predictions/?fixture_id={match.id}", headers=HEADERS).json()["items"]
    assert len(predictions)==1 and predictions[0]["id"]==str(good.id)
    assert predictions[0]["probabilities_1x2"]==[.5,.25,.25]
    assert predictions[0]["publication_eligible"] is False
    versions = client.get("/api/v1/models/?limit=100", headers=HEADERS).json()["items"]
    found = next(x for x in versions if x["id"]==str(version.id))
    assert found["model_key"]==model.model_key and "artifact_path" not in found
    evaluations = client.get("/api/v1/evaluations/?limit=100", headers=HEADERS).json()["items"]
    assert any(x["id"]==str(ev.id) and x["actual_score"]==[2,1] for x in evaluations)
    assert client.get("/api/v1/predictions/?limit=101", headers=HEADERS).status_code==422
    assert client.post("/api/v1/predictions/", headers=HEADERS, json={}).status_code==405
    assert len(db_session.scalars(select(ShadowPrediction).where(ShadowPrediction.fixture_id==match.id)).all())==2


def test_production_database_failure_refuses_local_sqlite(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "production")
    def fail(*args, **kwargs):
        raise OSError("connection failed")
    monkeypatch.setattr(db_module, "create_engine", fail)
    with pytest.raises(RuntimeError, match="refusing SQLite fallback"):
        db_module._init_engine()
