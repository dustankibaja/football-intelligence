# Advanced form and opponent-strength feature ablation (11 Nov 2026)

**Result: NO-GO for replacing the existing 24-feature causal-Elo research model.**

The research-only GitHub Actions run [42993889855](https://github.com/dustankibaja/football-intelligence/actions/runs/42993889855) and full system CI run [42993889684](https://github.com/dustankibaja/football-intelligence/actions/runs/42993889684) both passed. Historical 2019–26 sources were downloaded with 28 source SHA receipts. Four chronological 2022/23–2025/26 test folds held out the entire season from fitting. All compared models scored **exactly the same 5,784 matches**.

| Model | Correct / 5,784 | 1X2 accuracy | Log loss | Change vs 24-feature baseline |
|---|---:|---:|---:|---:|
| Existing 24-feature causal-Elo logistic | 3,049 | 52.714% | 0.987876 | Reference |
| Extended 50-feature logistic | 3,011 | 52.057% | 0.994096 | -0.657 pp |
| Extended 50-feature HGB | 2,979 | 51.504% | 1.003612 | -1.210 pp |
| Fixed 50/50 extended logistic/HGB | 3,030 | 52.386% | 0.993412 | -0.328 pp |

Additional features included 5/12-match exponentially weighted points and goals, opponent-Elo-adjusted form, league win/draw/away prior rates, seasonal timing and recent draw behavior. Same-day results and unplayed future fixtures are explicitly isolated in regression tests. These features did **not** improve full-coverage accuracy, log loss, or both; greater feature dimensionality added noise or redundant information. Do **not** replace the research baseline, frozen 251-feature C1, live goal-model owner, or published 63/71 shadow forecasts.

The observed difference for extended 50-feature logistic was -0.657 percentage points, with descriptive match-date-clustered 95% interval **[-1.192, -0.102] points**. Extended HGB: -1.210 points, descriptive 95% interval **[-1.978, -0.394] points**. The fixed blend was -0.328 points, interval **[-0.908, +0.227] points**. None establishes prospective performance. Because the earlier research cohort had already been inspected, **none of these intervals corrects for repeated retrospective exploration**.

## Product decision

The scientific gate stays **NO-GO** for any live model promotion. Preserve the existing 24-feature causal-Elo challenger for the separately issued and immutable 71 pre-match 2026/27 forecasts, which will be independently evaluated after results exist. Preserve the canonical frozen 251-feature C1 champion and M1 goal-market owner.

A different research question is selective prediction confidence: the **existing 24-feature Elo** retrospectively achieved 1,980 correct of 3,236 predictions (61.19%) at a fixed maximum predicted 1X2 probability threshold of 0.50. It **abstained on 2,548/5,784 fixtures** (44.05%). That threshold was inspected on historical data and must be validated prospectively; it cannot be described as 61% full-coverage accuracy, cannot be retrofitted to already issued forecasts, and cannot prove 60% live accuracy.

## Next technically justified experiments

1. Build strict timestamp/source-licensed pre-kickoff snapshots for richer *independent signal*: injury/availability, confirmed lineups, recovery and fixture congestion, high-quality expected goals (xG), shots/shot quality, and travel (where sourced). Never insert final-match statistics or post-kickoff news into a pre-match feature row.
2. Establish team mapping across promotion/renaming, season-boundary rating carryover, and missing-data masks before model changes.
3. Create a **future** sealed, same-fixture comparison against frozen C1 and current M1, with full-fixture 1X2 accuracy, log loss, Brier/calibration, league/time drift and transparent abstention coverage.
4. Source data licensing must be reviewed before commercial use. The existing public Football-Data.co.uk site states its free data is intended for private individuals and restricts automated data-training/commercial products; this historical research is **not** a commercial data authorization.

Never select a candidate using only this repeatedly examined retrospective cohort.
