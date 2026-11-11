# Confidence-based 1X2 selection — prospective research design (11 Nov 2026)

**This document does not claim 60% full-coverage or live accuracy.**

## Baseline and the meaning of accuracy

On the same four-season retrospective 5,784-match corpus, the current 24-feature causal-Elo research model achieved **3,049/5,784 = 52.714%** 1X2 top-class accuracy, with 100% fixture coverage. The earlier live 21/39 = 53.85% figure is a different, much smaller cohort; neither is a 59% baseline.

Observed pre-match confidence filter on the *same* retrospective model:

| Fixed minimum max-class probability | Selected matches | Correct | Conditional accuracy | Coverage of all 5,784 |
|---|---:|---:|---:|---:|
| None | 5,784 | 3,049 | 52.71% | 100.00% |
| 0.50 | 3,236 | 1,980 | **61.19%** | 55.95% |
| 0.55 | 2,543 | 1,621 | 63.74% | 43.97% |
| 0.60 | 1,924 | 1,279 | 66.48% | 33.26% |

These were inspected retrospectively. **The excluded predictions do not disappear from the denominator of full-fixture accuracy.** A historical conditional hit rate is not a proven real-time guarantee.

## New independent prospective test

- The original, read-only November 11 M1/Elo paired pre-match forecast set contains **71** future fixtures (artifact GitHub Actions run `42990766670`, ledger SHA-256 `a7648cbb5af7f647bf8b677fec1e67266356bc9868e051166d96b7d81e1144ff`).
- Applying the **fixed research 0.50 candidate max-class probability cutoff before any of those matches finish** selects **32** and abstains on **39**, a planned **45.07%** prediction coverage. This is not a retroactively chosen successful subset.
- The daily independent results workflow `.github/workflows/prospective-accuracy-results.yml` permanently links the original 71-fixture SHA and stores a SHA-256 of the 32 selected fixture IDs. It separately tracks selected finished, selected pending and abstained finished.
- Each actual final score, once obtained from an independently authenticated source and past the safe date-only completion boundary, is counted in **full-fixture prospective accuracy** and in the **conditional subset** when originally selected. We compare the Elo challenger to original M1 on the **same candidate-selected fixtures**, not a different set chosen by M1.
- Until a selected match is independently finalized, its conditional accuracy is **null**, never zero, one or an invented outcome.
- This is a preliminary, small cohort. Unresolved/rescheduled/blocked fixtures remain accounted for; no existing 63-forecast or 71-forecast ledger, production model, or live publication gate is changed.

## Proposed high-confidence product mode — NOT enabled

The interface could eventually display an option labelled **“High-confidence predictions”** which shows the actual probability and explicit **“No confident pick”** on fixtures below a validated threshold. If implemented, the displayed conditional historical hit rate must always include fixture coverage and sample size, and the service must not imply a 60% guaranteed full-fixture accuracy.

Do not enable until the fixed threshold is validated prospectively with independently retrieved scores, the source's licenses support the desired use, the full-coverage and selective calibration metrics pass predefined criteria, and deployment authorization is separately reviewed.

## How to improve beyond the current signal

The 50-feature opponent-adjusted form expansion failed a verified historical ablation (see `docs/research/advanced_form_ablation_2026_11_11.md`): extra transformations of old score data were worse than the existing 24-feature Elo. Priority should shift to genuinely informative, licensed, pre-kickoff signals: event-derived xG/shot quality, confirmed lineups and expected absences with timestamps, injuries/suspensions, game load, player availability, team-level transfer/strength transitions, and a verifiable team/competition identity map. Evaluate each source using strict chronological snapshot timing, missing-data masks and independent future fixtures. The Football-Data.co.uk site explicitly restricts commercial and automated AI-data-training use of its free CSVs; do not assume the research CSVs authorize a commercial app.
