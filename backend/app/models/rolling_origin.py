"""Leakage-safe rolling-origin evaluation for RESEARCH challengers only.

Whole kickoff groups remain together. Each fold retrains on past data, uses a
separate pre-test validation window, and scores a strictly later, disjoint test
window. Results are out-of-sample research evidence, never publication approval.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Callable, TypeVar

from app.models.base import IPredictionModel
from app.models.dataset_dto import ModelingRowDTO
from app.models.evaluation import MetricSummaryDTO, ModelEvaluator

T = TypeVar("T", bound=IPredictionModel)


@dataclass(frozen=True)
class RollingOriginFold:
    fold_number: int
    training: tuple[ModelingRowDTO, ...]
    validation: tuple[ModelingRowDTO, ...]
    testing: tuple[ModelingRowDTO, ...]


@dataclass(frozen=True)
class RollingOriginEvaluation:
    model_key: str
    per_fold: tuple[MetricSummaryDTO, ...]
    pooled_test: MetricSummaryDTO
    tested_match_ids: tuple[str, ...]
    train_counts: tuple[int, ...]
    validation_counts: tuple[int, ...]
    test_counts: tuple[int, ...]
    mode: str = "RESEARCH_OOS_ONLY"


def make_rolling_origin_folds(
    rows: list[ModelingRowDTO],
    n_folds: int = 3,
    initial_train_groups: int = 20,
    validation_groups: int = 5,
    embargo: timedelta = timedelta(hours=3),
) -> tuple[RollingOriginFold, ...]:
    """Group by UTC kickoff; enforce positive embargo and disjoint test windows."""
    if n_folds < 1 or initial_train_groups < 2 or validation_groups < 1:
        raise ValueError("INVALID_ROLLING_ORIGIN_CONFIGURATION")
    if embargo < timedelta(0):
        raise ValueError("NEGATIVE_EMBARGO_PROHIBITED")
    if len({str(row.match_id) for row in rows}) != len(rows):
        raise ValueError("DUPLICATE_FIXTURE_IN_BACKTEST")
    if not rows:
        raise ValueError("EMPTY_BACKTEST_DATASET")

    identities = {
        (row.dataset_release_key, row.dataset_release_fingerprint, row.feature_set_key)
        for row in rows
    }
    if len(identities) != 1:
        raise ValueError("MIXED_DATASET_OR_FEATURE_RELEASES")
    for row in rows:
        if row.kickoff_utc.tzinfo is None:
            raise ValueError("BACKTEST_REQUIRES_UTC_AWARE_KICKOFF")
        if row.targets.get("match_result_1x2_v1") not in (0, 1, 2):
            raise ValueError("BACKTEST_REQUIRES_FINISHED_1X2_LABELS")

    by_kickoff: dict[object, list[ModelingRowDTO]] = {}
    for row in rows:
        kickoff = row.kickoff_utc
        by_kickoff.setdefault(kickoff, []).append(row)
    times = sorted(by_kickoff)
    required = initial_train_groups + validation_groups + n_folds
    if len(times) < required:
        raise ValueError("INSUFFICIENT_UNIQUE_KICKOFF_GROUPS")

    first_test = initial_train_groups + validation_groups
    available_test_groups = len(times) - first_test
    base, remainder = divmod(available_test_groups, n_folds)
    sizes = [base + (i < remainder) for i in range(n_folds)]

    folds: list[RollingOriginFold] = []
    tested_ids: set[str] = set()
    cursor = first_test

    for fold_idx, test_group_count in enumerate(sizes, start=1):
        test_start_time = times[cursor]
        val_start_idx = cursor - validation_groups
        validation_cutoff = times[val_start_idx]

        # Fully observed before the validation window starts; no same-kickoff
        # training/validation contamination.
        train_times = [
            t for t in times[:val_start_idx] if t + embargo < validation_cutoff
        ]
        # A validation game must have had enough time to finish before the
        # first test game's prediction point. Boundary games are purged.
        val_times = [
            t for t in times[val_start_idx:cursor] if t + embargo < test_start_time
        ]
        test_times = times[cursor : cursor + test_group_count]
        if not train_times or not val_times or not test_times:
            raise ValueError("INSUFFICIENT_OBSERVABLE_HISTORY_AFTER_EMBARGO")

        train_rows = tuple(r for t in train_times for r in by_kickoff[t])
        val_rows = tuple(r for t in val_times for r in by_kickoff[t])
        test_rows = tuple(r for t in test_times for r in by_kickoff[t])

        if not (
            max(r.kickoff_utc for r in train_rows) + embargo
            < min(r.kickoff_utc for r in val_rows)
            and max(r.kickoff_utc for r in val_rows) + embargo
            < min(r.kickoff_utc for r in test_rows)
        ):
            raise ValueError("BACKTEST_CHRONOLOGY_VIOLATION")

        this_ids = {str(r.match_id) for r in test_rows}
        if tested_ids & this_ids:
            raise ValueError("BACKTEST_REPEATED_TEST_FIXTURE")
        tested_ids.update(this_ids)

        folds.append(
            RollingOriginFold(
                fold_number=fold_idx,
                training=train_rows,
                validation=val_rows,
                testing=test_rows,
            )
        )
        cursor += test_group_count

    return tuple(folds)


def evaluate_rolling_origin(
    folds: tuple[RollingOriginFold, ...],
    model_factory: Callable[[], T],
) -> RollingOriginEvaluation:
    """Refit each fold on training ONLY and pool strictly nonoverlapping OOS scores.

    No calibration, hyperparameter selection or champion replacement is done
    here. Validation sets are intentionally untouched until a separate
    pre-registered calibration procedure is approved.
    """
    if not folds:
        raise ValueError("NO_ROLLING_ORIGIN_FOLDS")

    pooled_labels: list[int] = []
    pooled_probs: list[list[float]] = []
    fold_metrics: list[MetricSummaryDTO] = []
    train_counts: list[int] = []
    val_counts: list[int] = []
    test_counts: list[int] = []
    all_test_ids: set[str] = set()
    tested_order: list[str] = []
    model_key: str | None = None

    for fold in folds:
        test_ids = [str(row.match_id) for row in fold.testing]
        if all_test_ids.intersection(test_ids):
            raise ValueError("BACKTEST_REPEATED_TEST_FIXTURE")
        all_test_ids.update(test_ids)
        tested_order.extend(test_ids)

        model = model_factory()
        if model_key is None:
            model_key = model.model_key
        elif model.model_key != model_key:
            raise ValueError("MIXED_MODEL_KEYS_BETWEEN_FOLDS")
        model.fit(list(fold.training))

        labels: list[int] = []
        probs: list[list[float]] = []
        for row in fold.testing:
            label = row.targets.get("match_result_1x2_v1")
            if label not in (0, 1, 2):
                raise ValueError("MISSING_FINAL_OUTCOME_IN_TEST")
            prediction = model.predict_raw(row)
            forecast = [
                float(prediction.prob_home_win),
                float(prediction.prob_draw),
                float(prediction.prob_away_win),
            ]
            if any(not (0 <= v <= 1) for v in forecast):
                raise ValueError("INVALID_CHALLENGER_PROBABILITY")
            if abs(sum(forecast) - 1.0) > 1e-4:
                raise ValueError("UNNORMALIZED_CHALLENGER_PROBABILITY")
            labels.append(label)
            probs.append(forecast)

        fold_metrics.append(ModelEvaluator.compute_metric_summary(labels, probs))
        pooled_labels.extend(labels)
        pooled_probs.extend(probs)
        train_counts.append(len(fold.training))
        val_counts.append(len(fold.validation))
        test_counts.append(len(fold.testing))

    return RollingOriginEvaluation(
        model_key=model_key or "UNKNOWN",
        per_fold=tuple(fold_metrics),
        pooled_test=ModelEvaluator.compute_metric_summary(pooled_labels, pooled_probs),
        tested_match_ids=tuple(tested_order),
        train_counts=tuple(train_counts),
        validation_counts=tuple(val_counts),
        test_counts=tuple(test_counts),
    )
