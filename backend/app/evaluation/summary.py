"""Transparent, label-aware scientific performance from verified evaluations.

One latest evaluation per immutable pre-match prediction. Do not include
unverified historical shadow records or count superseded score corrections.
All metrics are empirical and never marketed as a guaranteed win rate.
"""
from __future__ import annotations

from typing import Any


def summarize_evaluations(evaluations: list[Any]) -> dict[str, Any]:
    n = len(evaluations)
    if n == 0:
        return {
            "sample_size": 0,
            "status": "INSUFFICIENT_SAMPLE",
            "mean_log_loss_1x2": None,
            "mean_brier_1x2": None,
            "mean_btts_log_loss": None,
            "mean_over_under_2_5_log_loss": None,
            "top1_score_hit_rate": None,
            "top3_score_hit_rate": None,
            "mean_actual_score_probability": None,
        }

    def mean(name: str) -> float:
        return round(sum(float(getattr(ev, name)) for ev in evaluations) / n, 5)

    return {
        "sample_size": n,
        "status": "EVALUATED" if n >= 50 else "SMALL_SAMPLE",
        "mean_log_loss_1x2": mean("log_loss_1x2"),
        "mean_brier_1x2": mean("brier_1x2"),
        "mean_btts_log_loss": mean("btts_log_loss"),
        "mean_over_under_2_5_log_loss": mean("ou25_log_loss"),
        "top1_score_hit_rate": round(sum(bool(ev.top1_score_hit) for ev in evaluations) / n, 5),
        "top3_score_hit_rate": round(sum(bool(ev.top3_score_hit) for ev in evaluations) / n, 5),
        "mean_actual_score_probability": mean("actual_score_probability"),
    }
