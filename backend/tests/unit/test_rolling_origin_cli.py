"""Smoke test for the real modeling CLI research rolling-origin entry point."""

import argparse
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

from app.models.baselines.class_prior import ClassPrior1X2Model
from app.models.dataset_dto import ModelingRowDTO
from app.models import cli


def test_modeling_cli_rolling_origin_produces_independent_report(
    monkeypatch, tmp_path, capsys
):
    start = datetime(2024, 1, 1, 14, 0, tzinfo=UTC)
    rows = [
        ModelingRowDTO(
            match_id=uuid5(NAMESPACE_URL, f"cli-research-{i}"),
            competition_id=uuid5(NAMESPACE_URL, "cli-league"),
            season_id=uuid5(NAMESPACE_URL, "cli-season"),
            kickoff_utc=start + timedelta(days=i),
            dataset_release_key="approved-research:v1",
            dataset_release_fingerprint="b" * 64,
            feature_set_key="mvp_a_feature_set_v1",
            feature_set_fingerprint="c" * 64,
            ordered_feature_names=["form"],
            feature_values=[float(i)],
            targets={"match_result_1x2_v1": i % 3},
        )
        for i in range(60)
    ]

    class FakeSession:
        def scalars(self, _query):
            return SimpleNamespace(all=lambda: [])

        def close(self):
            pass

    class FakeBuilder:
        def __init__(self, **_kwargs):
            pass

        def build_modeling_dataset(self, _db, limit=None):
            return None, rows if limit is None else rows[:limit]

    monkeypatch.setattr(cli, "SessionLocal", FakeSession)
    monkeypatch.setattr(cli, "ModelingDatasetBuilder", FakeBuilder)
    monkeypatch.setattr(
        cli,
        "global_model_registry",
        SimpleNamespace(get_model_class=lambda _name: ClassPrior1X2Model),
    )

    out = tmp_path / "out_of_sample_only.json"
    args = argparse.Namespace(
        model="class_prior_1x2_v1",
        dataset="mvp_a_historical",
        version="v1",
        split_strategy="rolling_origin",
        limit=None,
        folds=3,
        embargo_hours=3.0,
        output=str(out),
    )
    cli.evaluate_command(args)

    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["evaluation_mode"] == "RESEARCH_OOS_ONLY"
    assert data["fold_count"] == 3
    assert data["pooled_test"]["sample_size"] == 24
    assert len(set(data["tested_match_ids"])) == 24
    assert "NON-PRODUCTION" in capsys.readouterr().out
