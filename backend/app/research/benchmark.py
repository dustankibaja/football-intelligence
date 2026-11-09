"""Two nested chronological walk-forward folds with out-of-sample calibration."""
from __future__ import annotations

import math
import random
from collections import defaultdict
from dataclasses import dataclass

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from app.research.models import FeatureRow, selective_decision

CANDIDATES = ("league_prior", "empirical_poisson", "decay_poisson",
              "dixon_coles", "elo_form_logistic")
FOLDS = (
    {"train": ("2021-22", "2022-23"), "calibration": "2023-24", "test": "2024-25"},
    {"train": ("2021-22", "2022-23", "2023-24"), "calibration": "2024-25", "test": "2025-26"}
)


def _logloss(p: tuple[float, ...], y: int) -> float:
    return -math.log(max(1e-12, min(1.0, p[y])))


def _brier(p: tuple[float, ...], y: int) -> float:
    return sum((prob - (1.0 if i == y else 0.0)) ** 2 for i, prob in enumerate(p))


def _normalize(values) -> tuple[float, float, float]:
    p = [max(1e-6, min(1.0, float(x))) for x in values]
    total = sum(p)
    return tuple(x / total for x in p)


def _features(rows: list[FeatureRow]) -> np.ndarray:
    return np.asarray([row.x for row in rows], dtype=np.float64)


def _labels(rows: list[FeatureRow]) -> np.ndarray:
    return np.asarray([row.label for row in rows], dtype=np.int64)


def _logistic_predictions(train: list[FeatureRow], targets: list[FeatureRow]):
    if not targets:
        return []
    model = make_pipeline(StandardScaler(), LogisticRegression(
        C=.5, max_iter=800, random_state=42))
    model.fit(_features(train), _labels(train))
    classes = model[-1].classes_.tolist()
    return [_normalize({int(c): float(v) for c, v in zip(classes, p)}.get(i, 1e-6)
                       for i in range(3)) for p in model.predict_proba(_features(targets))]


def _log_features(probabilities) -> np.ndarray:
    return np.asarray([[math.log(max(1e-6, p)) for p in row]
                       for row in probabilities], dtype=np.float64)


@dataclass
class Calibrator:
    model: LogisticRegression

    @classmethod
    def fit(cls, probs, labels):
        if len(probs) < 100 or set(labels) != {0, 1, 2}:
            return None
        model = LogisticRegression(C=.5, max_iter=800, random_state=42)
        model.fit(_log_features(probs), np.asarray(labels))
        return cls(model)

    def predict(self, probs):
        if not probs:
            return []
        classes = self.model.classes_.tolist()
        return [_normalize({int(c): float(v) for c, v in zip(classes, p)}.get(i, 1e-6)
                           for i in range(3))
                for p in self.model.predict_proba(_log_features(probs))]


def _calibrate_without_leakage(calibration_rows, calibration_probs, test_probs):
    """Use first half of previous season to fit, second half to decide.

    Never choose a calibrator using the test season. Refit on the entire
    calibration season only after that earlier validation decision.
    """
    dates = sorted({row.match.day for row in calibration_rows})
    midpoint = dates[len(dates) // 2]
    first = [i for i, row in enumerate(calibration_rows) if row.match.day < midpoint]
    second = [i for i, row in enumerate(calibration_rows) if row.match.day >= midpoint]
    split_model = Calibrator.fit([calibration_probs[i] for i in first],
                                 [calibration_rows[i].label for i in first])
    if split_model is None or len(second) < 50:
        return test_probs, {"applied": False, "reason": "INSUFFICIENT_CALIBRATION"}
    raw_loss = sum(_logloss(calibration_probs[i], calibration_rows[i].label)
                   for i in second) / len(second)
    validated = split_model.predict([calibration_probs[i] for i in second])
    calibrated_loss = sum(_logloss(p, calibration_rows[i].label)
                          for p, i in zip(validated, second)) / len(second)
    apply = calibrated_loss + .005 < raw_loss
    if not apply:
        return test_probs, {
            "applied": False, "reason": "NO_VALIDATED_GAIN",
            "validationRawLogLoss": round(raw_loss, 6),
            "validationCalibratedLogLoss": round(calibrated_loss, 6)
        }
    full_model = Calibrator.fit(calibration_probs, _labels(calibration_rows).tolist())
    if full_model is None:
        return test_probs, {"applied": False, "reason": "REFIT_FAILED"}
    return full_model.predict(test_probs), {
        "applied": True, "method": "MULTINOMIAL_LOG_PROBABILITIES",
        "selection": "EARLIER_SEASON_SECOND_HALF_ONLY",
        "validationRawLogLoss": round(raw_loss, 6),
        "validationCalibratedLogLoss": round(calibrated_loss, 6)
    }


def metrics(rows: list[FeatureRow], probabilities: list[tuple[float, ...]]) -> dict:
    if len(rows) != len(probabilities):
        raise ValueError("Rows and predictions are misaligned")
    if not rows:
        return {"n": 0}
    n = len(rows)
    correct = sum(max(range(3), key=lambda i: p[i]) == row.label
                  for row, p in zip(rows, probabilities))
    qualified = [(row, p) for row, p in zip(rows, probabilities)
                 if selective_decision(row, p).startswith("WATCH_ONLY")]
    return {
        "n": n,
        "accuracy": round(correct / n, 6),
        "logLoss": round(sum(_logloss(p, row.label) for row, p in
                             zip(rows, probabilities)) / n, 6),
        "brier": round(sum(_brier(p, row.label) for row, p in
                           zip(rows, probabilities)) / n, 6),
        "drawObservedRate": round(sum(row.label == 1 for row in rows) / n, 6),
        "drawPredictedMean": round(sum(p[1] for p in probabilities) / n, 6),
        "watchOnlyN": len(qualified),
        "watchOnlyCoverage": round(len(qualified) / n, 6),
        "watchOnlyAccuracy": round(sum(max(range(3), key=lambda i: p[i]) == row.label
                                       for row, p in qualified) / len(qualified), 6)
                             if qualified else None,
        "watchOnlyStatus": "RESEARCH_NOT_BETTING_ADVICE"
    }


def _paired_bootstrap(rows, candidate, baseline, iterations=500, seed=42):
    if not rows:
        return {"meanDelta": None, "ci95": None}
    differences = [_logloss(p, row.label) - _logloss(b, row.label)
                   for row, p, b in zip(rows, candidate, baseline)]
    rng = random.Random(seed)
    samples = [sum(differences[rng.randrange(len(rows))]
                   for _ in range(len(rows))) / len(rows)
               for _ in range(iterations)]
    samples.sort()
    return {
        "meanDelta": round(sum(differences) / len(differences), 6),
        "ci95": [round(samples[int(.025 * iterations)], 6),
                 round(samples[int(.975 * iterations) - 1], 6)],
        "negativeIsBetter": True
    }


def benchmark(rows: list[FeatureRow]) -> dict:
    if not rows:
        raise ValueError("No historical research data")
    fold_reports = []
    pooled: dict[str, list] = {name: [] for name in CANDIDATES}
    baseline_pooled: list = []
    heldout_rows: list[FeatureRow] = []
    for fold in FOLDS:
        train = [r for r in rows if r.match.season in fold["train"] and
                 r.history_count >= 30]
        calibration = [r for r in rows if r.match.season == fold["calibration"]]
        test = [r for r in rows if r.match.season == fold["test"]]
        if len(train) < 500 or len(calibration) < 500 or len(test) < 500:
            raise ValueError(f"Insufficient chronological fold coverage: {fold}")
        if max(r.match.day for r in train) >= min(r.match.day for r in calibration) or (
            max(r.match.day for r in calibration) >= min(r.match.day for r in test)
        ):
            raise ValueError("Temporal leakage: training/calibration/test overlap")
        fold_result = {
            **fold, "trainN": len(train), "calibrationN": len(calibration),
            "testN": len(test), "candidates": {}
        }
        for candidate in CANDIDATES:
            if candidate == "elo_form_logistic":
                cal_probs = _logistic_predictions(train, calibration)
                test_probs = _logistic_predictions(train, test)
            else:
                cal_probs = [r.probabilities[candidate] for r in calibration]
                test_probs = [r.probabilities[candidate] for r in test]
            selected, calibration_report = _calibrate_without_leakage(
                calibration, cal_probs, test_probs)
            fold_result["candidates"][candidate] = {
                "raw": metrics(test, test_probs),
                "selected": metrics(test, selected),
                "calibration": calibration_report
            }
            pooled[candidate].extend(zip(test, selected))
            if candidate == "empirical_poisson":
                baseline_pooled.extend(test_probs)
        heldout_rows.extend(test)
        fold_reports.append(fold_result)
    if any(len(pooled[name]) != len(heldout_rows) for name in CANDIDATES):
        raise ValueError("Paired evaluation sample mismatch")
    aggregate = {}
    for name in CANDIDATES:
        samples = pooled[name]
        selected = [p for _, p in samples]
        aggregate[name] = {
            "metrics": metrics(heldout_rows, selected),
            "pairedVsEmpiricalPoisson": _paired_bootstrap(
                heldout_rows, selected, baseline_pooled),
            "perLeague": {
                league: metrics([r for r, _ in samples if r.match.competition == league],
                                [p for r, p in samples if r.match.competition == league])
                for league in sorted({r.match.competition for r in heldout_rows})
            }
        }
    return {
        "status": "RETROSPECTIVE_RESEARCH_NOT_CERTIFIED",
        "folds": fold_reports,
        "aggregate": aggregate,
        "holdoutFixtureCount": len(heldout_rows),
        "dateOnlyPolicy": "NO_SAME_DAY_RESULT_FEATURES",
        "calibrationPolicy": "TRAIN_ON_PRIOR_SEASONS_VALIDATE_ON_PRIOR_SEASON_ONLY",
        "liveChampionEvaluated": False,
        "frozenC1M1Evaluated": False,
        "reasonFrozenArtifactsNotRun": "Requires canonical 251-feature release and identity-verified live parity",
        "productionModelChanged": False,
        "automaticPromotionAllowed": False
    }
