# Prospective causal-Elo 1X2 accuracy experiment — issued 11 November 2026

**Research-only, noncommercial. No live-model upgrade or betting claim.**

## Goal and current evidence

- The existing narrow live 1X2 evaluation was **21/39 = 53.85%**, on different fixtures.
- In separate completed 2022/23–2025/26 rolling-origin retrospective research, the 24-feature causal-Elo logistic model achieved **3,049/5,784 = 52.714%** versus raw M1 decay **2,955/5,784 = 51.089%**. Those are not prospective proof of future superiority or a direct comparison against frozen 251-feature C1.
- This experiment creates **new** sealed pre-match forecasts for the **same 71 eligible upcoming 2026/27 fixtures** with both M1 and the fixed L2_1.0 causal-Elo logistic challenger. Unqualified fixtures are never silently included, and unplayed scores are never imputed.

## Issuance receipt

The initial prospective scientific job executed successfully on **GitHub Actions run 42990766670**. It used the genuine four-league 2026/27 schedule pinned at upstream commit `e1ec9773df8030a3e0c613f52eb3bd85437c21ab` and the approved 28 historical-source receipt set.

| Property | Immutable value |
|---|---|
| Original paired issuance UTC | 2026-11-11T08:55:04.755832+00:00 |
| Total qualified M1/Elo pairs | **71** |
| Blocked by original source eligibility rules | **18** |
| Expected match calendar dates | **2026-11-20 through 2026-12-02** |
| Challenger | CAUSAL_ELO_L2_1.0 (24 features, L2 regularization fixed at 1.0) |
| Training examples | 10,122 completed matches, seasons 2019–2026 |
| Latest training match day | 2026-05-24 |
| Challenger fitted-model hash | `8b9831b6c8e94488f8ad556af30d1cae1b8727823538891ddf747eff2e5a88ed` |
| Original M1 baseline ledger SHA-256 | `a5f2836dee513c788240900b6b5bd2c7fc28cf0f86c1a56ca978dfb971783989` |
| New paired ledger SHA-256 | `a7648cbb5af7f647bf8b677fec1e67266356bc9868e051166d96b7d81e1144ff` |
| Original artifact name | `football-prospective-elo-paired-2026-27` |
| Original artifact run ID | `42990766670` |

The original artifact ZIP digest is recorded by GitHub Actions; do not replace the originally issued evidence with a fresh run's predictions. The separate **63-match 9 November forecast ledger remains untouched**.

The schedule source's exact UTC kickoffs are unverified; eligibility requires the *full future calendar date*, not an assumed kickoff. Teams are bound by reviewed explicit aliases or rejected. No 2026/27 result is incorporated into training.

## Automated future results

`.github/workflows/prospective-accuracy-results.yml` runs nightly at **08:17 UTC (11:17 Tanzania time)** and on manual dispatch. It:
1. Downloads the original Nov 11 issued artifact from **run 42990766670**, and fails if its recorded SHA-256, issuance timestamp, or 71-forecast count differs.
2. Retrieves 2026/27 final scores from original, independent Football-Data.co.uk CSV URLs; no sportsbook odds are used. It matches **exact original match date, team names, league and fixture hash**, and accepts date-only final scores only on a *later UTC day*.
3. Scores M1 and the challenger on precisely the **same finished fixtures**, reports their 1X2 accuracy, log loss and multiclass Brier, records still-pending original fixtures, and archives a separate research result artifact `sealed-paired-2026-11-11-postmatch-assessment`.
4. Never edits the 71 original predictions, 63 previous shadow predictions, or the canonical C1/M1 model artifacts.

The first evaluation, **GitHub Actions run 42991501310** on 11 November, succeeded: **0 finished / 71 pending**, as required for forecasts issued before the events. This is not evidence of accuracy.

Run manually after new final scores arrive with `workflow_dispatch`, or evaluate a verified downloaded archive locally:

```bash
cd backend
python -m app.research.prospective_accuracy_evaluator \
  --paired-ledger ../research_inputs/frozen_2026_11_11/paired_prospective_elo_2627.json \
  --output ../research_outputs/verified_paired_prospective_1x2.json
```

## Promotion gate and safeguards

A production upgrade requires **sufficient newly completed independent prospective fixtures**, transparent number of unscored/rescheduled fixtures, comparison with the canonical C1 champion on equivalent 251-feature inputs, acceptable full-coverage accuracy **and** proper scoring-rule/calibration performance, and a documented independent release review.

**Do not** treat these 71 matches as proof of reaching 60% or beating the 53.85% earlier sample. They have not happened at issuance time. The prior retrospective candidate search was exploratory and creates multiple-comparison/selection bias. Do not use this provider's noncommercial terms for product commercialization. Artifact retention is **90 days**, after which the pinned result-reconciliation workflow will need an appropriately reviewed longer-term evidence archive or be disabled; it must not silently substitute a new ledger.

The unrelated Vercel `footmo-live` preview currently has a missing configured root `live-web`; this research pipeline is neither a deployed frontend nor a workaround for that blocker.
