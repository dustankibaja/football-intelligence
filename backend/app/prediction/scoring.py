"""
Post-Match Scoring & Outcome Evaluation Engine (Sections 16, 52-57)
Calculates canonical 1X2 Log Loss and unnormalized Brier score, BTTS, O/U 2.5,
exact score probabilities, top-1/top-3 hits, and enforces result finality and outcome separation.
"""
from __future__ import annotations

import math
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.prediction.domain import ShadowEvaluationResult, ShadowPredictionResult


def calculate_1x2_metrics(prob_home: float, prob_draw: float, prob_away: float, actual_1x2: str) -> tuple[float, float]:
    eps = 1e-15
    y_h = 1.0 if actual_1x2 == "HOME_WIN" else 0.0
    y_d = 1.0 if actual_1x2 == "DRAW" else 0.0
    y_a = 1.0 if actual_1x2 == "AWAY_WIN" else 0.0

    p_actual = prob_home if actual_1x2 == "HOME_WIN" else (prob_draw if actual_1x2 == "DRAW" else prob_away)
    log_loss = -math.log(max(eps, p_actual))

    # Unnormalized multiclass Brier score: sum of squared differences
    brier = (prob_home - y_h) ** 2 + (prob_draw - y_d) ** 2 + (prob_away - y_a) ** 2
    return round(log_loss, 5), round(brier, 5)


def calculate_binary_metrics(prob_yes: float, actual_outcome: bool) -> tuple[float, float]:
    eps = 1e-15
    y = 1.0 if actual_outcome else 0.0
    p_clamped = min(max(prob_yes, eps), 1.0 - eps)

    log_loss = -(y * math.log(p_clamped) + (1.0 - y) * math.log(1.0 - p_clamped))
    brier = (prob_yes - y) ** 2
    return round(log_loss, 5), round(brier, 5)


class PostMatchScoringService:
    """Scores pre-match shadow predictions against certified terminal outcomes."""

    def __init__(self) -> None:
        self._evaluations: dict[tuple[UUID, int], ShadowEvaluationResult] = {}

    def score_prediction(
        self,
        prediction: ShadowPredictionResult,
        actual_home_goals: int,
        actual_away_goals: int,
        match_status: str = "FINISHED",
        result_version: int = 1,
        observed_at: datetime | None = None,
    ) -> ShadowEvaluationResult:
        # Result Finality Assertion
        if match_status != "FINISHED":
            raise ValueError(
                f"CANNOT_SCORE_NON_TERMINAL_RESULT: Match status is '{match_status}'. Only 'FINISHED' is permitted."
            )

        # Idempotency check: (prediction_id, result_version)
        key = (prediction.prediction_id, result_version)
        if key in self._evaluations:
            return self._evaluations[key]

        obs_time = observed_at or datetime.now(UTC)

        # Actual labels
        if actual_home_goals > actual_away_goals:
            actual_1x2 = "HOME_WIN"
        elif actual_home_goals == actual_away_goals:
            actual_1x2 = "DRAW"
        else:
            actual_1x2 = "AWAY_WIN"

        actual_btts = (actual_home_goals > 0 and actual_away_goals > 0)
        actual_ou25 = (actual_home_goals + actual_away_goals >= 3)

        # Metrics calculation
        ll_1x2, brier_1x2 = calculate_1x2_metrics(
            prediction.prob_home, prediction.prob_draw, prediction.prob_away, actual_1x2
        )
        btts_ll, btts_brier = calculate_binary_metrics(prediction.btts_yes, actual_btts)
        ou_ll, ou_brier = calculate_binary_metrics(prediction.over_2_5, actual_ou25)

        # The top-five list is NOT the full exact-score distribution.
        # New certified predictions persist the original full score matrix.
        # Legacy replay predictions lack this field; reconstruct their
        # independent-Poisson grid from stored lambdas without rewriting them.
        if prediction.score_matrix is not None:
            import hashlib
            import json

            matrix = prediction.score_matrix
            matrix_fp = hashlib.sha256(
                json.dumps(matrix, sort_keys=True).encode("utf-8")
            ).hexdigest()
            if matrix_fp != prediction.exact_score_matrix_fingerprint:
                raise ValueError("SCORE_MATRIX_FINGERPRINT_MISMATCH")
            if (
                not matrix or any(len(row) != len(matrix) for row in matrix)
                or any(not math.isfinite(p) or p < 0 for row in matrix for p in row)
                or abs(sum(sum(row) for row in matrix) - 1.0) > 1e-6
            ):
                raise ValueError("INVALID_FULL_SCORE_DISTRIBUTION")
        else:
            # Compatibility-only reconstruction for historical replay records.
            # Such records must not be promoted to certified live evidence.
            from app.prediction.service import compute_score_matrix
            matrix = compute_score_matrix(
                prediction.lambda_home, prediction.lambda_away, max_goals=10
            )

        actual_score_prob = (
            matrix[actual_home_goals][actual_away_goals]
            if 0 <= actual_home_goals < len(matrix)
            and 0 <= actual_away_goals < len(matrix)
            else 0.0
        )
        top1_hit = any(
            item["home_goals"] == actual_home_goals
            and item["away_goals"] == actual_away_goals
            and item["rank"] == 1
            for item in prediction.top_scorelines
        )
        top3_hit = any(
            item["home_goals"] == actual_home_goals
            and item["away_goals"] == actual_away_goals
            and item["rank"] <= 3
            for item in prediction.top_scorelines
        )

        evaluation = ShadowEvaluationResult(
            evaluation_id=uuid4(),
            prediction_id=prediction.prediction_id,
            fixture_id=prediction.fixture_id,
            result_version=result_version,
            result_observed_at=obs_time,
            actual_home_goals=actual_home_goals,
            actual_away_goals=actual_away_goals,
            actual_1x2=actual_1x2,
            actual_btts=actual_btts,
            actual_over_2_5=actual_ou25,
            log_loss_1x2=ll_1x2,
            brier_1x2=brier_1x2,
            btts_log_loss=btts_ll,
            btts_brier=btts_brier,
            ou25_log_loss=ou_ll,
            ou25_brier=ou_brier,
            actual_score_probability=actual_score_prob,
            top1_score_hit=top1_hit,
            top3_score_hit=top3_hit,
            evaluation_schema_version="1.0.0",
        )

        self._evaluations[key] = evaluation
        return evaluation
