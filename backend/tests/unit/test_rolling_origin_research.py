"""Rolling-origin research evaluation: complete kickoff groups, embargo, OOS pooling."""

from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

import pytest

from app.models.baselines.class_prior import ClassPrior1X2Model
from app.models.dataset_dto import ModelingRowDTO
from app.models.rolling_origin import (
    evaluate_rolling_origin,
    make_rolling_origin_folds,
)

START = datetime(2023, 7, 1, 15, 0, tzinfo=UTC)
COMPETITION_ID = uuid5(NAMESPACE_URL, "test-competition")
SEASON_ID = uuid5(NAMESPACE_URL, "test-season")


def labeled_history(groups=60, fixtures_per_group=2):
    rows = []
    for i in range(groups):
        for j in range(fixtures_per_group):
            rows.append(
                ModelingRowDTO(
                    match_id=uuid5(NAMESPACE_URL, f"history:{i}:{j}"),
                    competition_id=COMPETITION_ID,
                    season_id=SEASON_ID,
                    home_team_id=uuid5(NAMESPACE_URL, f"home:{i}:{j}"),
                    away_team_id=uuid5(NAMESPACE_URL, f"away:{i}:{j}"),
                    kickoff_utc=START + timedelta(days=i),
                    dataset_release_key="test_approved:v1",
                    dataset_release_fingerprint="d" * 64,
                    feature_set_key="mvp_a_feature_set_v1",
                    feature_set_fingerprint="e" * 64,
                    ordered_feature_names=["form"],
                    feature_values=[float(i)],
                    targets={"match_result_1x2_v1": (i + j) % 3},
                )
            )
    return rows


def test_same_kickoff_matches_never_split_across_partitions():
    folds = make_rolling_origin_folds(
        labeled_history(), n_folds=3, initial_train_groups=20,
        validation_groups=5, embargo=timedelta(hours=3)
    )
    assert len(folds) == 3
    tested = set()
    for fold in folds:
        train_times = {x.kickoff_utc for x in fold.training}
        val_times = {x.kickoff_utc for x in fold.validation}
        test_times = {x.kickoff_utc for x in fold.testing}
        assert train_times.isdisjoint(val_times)
        assert train_times.isdisjoint(test_times)
        assert val_times.isdisjoint(test_times)
        assert max(train_times) + timedelta(hours=3) < min(val_times)
        assert max(val_times) + timedelta(hours=3) < min(test_times)
        # All fixtures in a kickoff group must remain in one partition.
        for part in (fold.training, fold.validation, fold.testing):
            by_time = {}
            for row in part:
                by_time[row.kickoff_utc] = by_time.get(row.kickoff_utc, 0) + 1
            assert set(by_time.values()) == {2}
        test_ids = {row.match_id for row in fold.testing}
        assert not tested.intersection(test_ids)
        tested.update(test_ids)
    assert len(tested) == 70  # (60 - 25) kickoff groups * 2


def test_rolling_origin_refits_each_fold_and_pools_unique_oos_scores():
    folds = make_rolling_origin_folds(
        labeled_history(), n_folds=3, initial_train_groups=20, validation_groups=5
    )
    evaluation = evaluate_rolling_origin(folds, ClassPrior1X2Model)

    assert evaluation.mode == "RESEARCH_OOS_ONLY"
    assert evaluation.model_key == "class_prior_1x2_v1"
    assert len(evaluation.per_fold) == 3
    assert evaluation.pooled_test.sample_size == 70
    assert sum(evaluation.test_counts) == evaluation.pooled_test.sample_size
    assert list(evaluation.train_counts) == sorted(evaluation.train_counts)
    assert len(set(evaluation.tested_match_ids)) == 70
    assert evaluation.pooled_test.log_loss > 0
    assert evaluation.pooled_test.brier_score > 0
    assert all(x.sample_size > 0 for x in evaluation.per_fold)


def test_duplicate_fixture_is_rejected_before_training():
    rows = labeled_history()
    rows.append(rows[0].model_copy(deep=True))
    with pytest.raises(ValueError, match="DUPLICATE_FIXTURE_IN_BACKTEST"):
        make_rolling_origin_folds(rows)


def test_future_unfinished_match_is_not_a_valid_backtest_row():
    rows = labeled_history()
    rows[-1].targets.clear()
    with pytest.raises(ValueError, match="BACKTEST_REQUIRES_FINISHED_1X2_LABELS"):
        make_rolling_origin_folds(rows)


def test_insufficient_history_and_negative_embargo_fail_closed():
    with pytest.raises(ValueError, match="INSUFFICIENT_UNIQUE_KICKOFF_GROUPS"):
        make_rolling_origin_folds(labeled_history(groups=8))
    with pytest.raises(ValueError, match="NEGATIVE_EMBARGO_PROHIBITED"):
        make_rolling_origin_folds(labeled_history(), embargo=timedelta(hours=-1))
