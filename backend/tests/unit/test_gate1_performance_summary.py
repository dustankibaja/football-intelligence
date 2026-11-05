"""No fictitious evaluation evidence or inflated correction counts."""
from types import SimpleNamespace

from app.evaluation.summary import summarize_evaluations


def test_performance_summary_never_invents_empty_metrics():
    result = summarize_evaluations([])
    assert result["sample_size"] == 0
    assert result["status"] == "INSUFFICIENT_SAMPLE"
    assert result["mean_log_loss_1x2"] is None
    assert result["top1_score_hit_rate"] is None


def test_performance_summary_uses_real_scored_outcomes():
    rows = [
        SimpleNamespace(
            log_loss_1x2=0.5, brier_1x2=0.3, btts_log_loss=0.7,
            ou25_log_loss=0.6, top1_score_hit=True, top3_score_hit=True,
            actual_score_probability=0.2,
        ),
        SimpleNamespace(
            log_loss_1x2=1.1, brier_1x2=0.8, btts_log_loss=1.3,
            ou25_log_loss=1.0, top1_score_hit=False, top3_score_hit=False,
            actual_score_probability=0.1,
        ),
    ]
    result = summarize_evaluations(rows)
    assert result["sample_size"] == 2
    assert result["status"] == "SMALL_SAMPLE"
    assert result["mean_log_loss_1x2"] == 0.8
    assert result["mean_brier_1x2"] == 0.55
    assert result["mean_btts_log_loss"] == 1.0
    assert result["mean_over_under_2_5_log_loss"] == 0.8
    assert result["top1_score_hit_rate"] == 0.5
    assert result["mean_actual_score_probability"] == 0.15
