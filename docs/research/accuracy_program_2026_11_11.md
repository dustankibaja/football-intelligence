# Football Intelligence: 1X2 accuracy research checkpoint (11 November 2026)

**Scope:** scientific retrospective evaluation only. **Not** a certified live model upgrade, independent prospective test, betting profitability claim, or permission to change the frozen C1/M1 publication policy.

## Objective and comparable evidence

A previous limited live/shadow summary showed **21/39 = 53.85%** 1X2 hit rate. That sample is *not* directly comparable to the four-season outcomes dataset described below. Improving the application means beating an appropriate **same-fixture** baseline on new, genuinely pre-match forecasts, not just displaying a higher number on a selected subset.

The research workflow executes **5,784 distinct real fixtures**, four complete seasons (2022/23 through 2025/26), four competitions (EPL, La Liga, Serie A, Bundesliga), and 28 source CSV SHA-256 receipts. Each season's model is trained on previous completed seasons only; features use results from *strictly earlier calendar dates*, and a finished same-day match cannot inform another fixture that day.

## Full-coverage results from successful real-source workflow

Validation: GitHub Actions run **42988729431** (Real Historical Four-Season Rolling OOS Audit); system CI run **42988729396**, both passed on commit `337e33d3`. The report is archived in the `football-multiseason-genuine-oos-research` artifact, not by rewriting any prediction ledger. Reference source-manifest fingerprint: `e1ca1d96f708836f68dd7af66f14e0b7d74a57a9f2e4948cfab56dcaac25b522`.

| Retrospective candidate | Correct / 5,784 | 1X2 accuracy | 1X2 log loss |
|---|---:|---:|---:|
| Prior-only class frequency | 2,530 | 43.741% | 1.073676 |
| M1_DECAY_365 structural baseline | 2,955 | 51.089% | 0.999259 |
| 20-feature L2 logistic | 2,871 | 49.637% | 1.020866 |
| M1 + 20-feature logistic (50/50) | 3,036 | 52.490% | 0.996571 |
| **24-feature causal-Elo L2 logistic** | **3,049** | **52.714%** | 0.987876 |
| M1 + causal-Elo logistic (50/50) | 3,045 | 52.645% | **0.986396** |
| 24-feature causal-Elo HGB | 3,016 | 52.144% | 0.998621 |
| M1 + causal-Elo HGB (50/50) | 3,040 | 52.559% | 0.989627 |
| League-specific causal-Elo L2 logistic | 3,035 | 52.472% | 0.996187 |
| M1 + league-specific causal-Elo logistic (50/50) | 3,036 | 52.490% | 0.987376 |
| Prior-OOS-trained logistic meta-learner | 3,029 | 52.369% | 0.987606 |

**Descriptive finding:** causal-Elo logistic predicts **94 more correct outcomes** than M1 on exactly the same 5,784 fixtures (+1.625 percentage points) and has lower log loss. The 50/50 M1/Elo blend has the lowest *reported pooled log loss*, at 0.986396, though its accuracy is slightly lower. These are comparisons of retrospective research models, **not the frozen certified C1 champion**.

### Yearwise variability

| Held-out season | M1 accuracy | Causal-Elo logistic accuracy |
|---|---:|---:|
| 2022/23 | 51.452% | 53.112% |
| 2023/24 | 51.729% | 53.942% |
| 2024/25 | 49.862% | 52.628% |
| 2025/26 | 51.314% | 51.176% |

The causal-Elo candidate **does not outperform M1 in every year**. Historical results across several explored variants cannot establish an expected future accuracy level. A previously explored M1/Elo 50/50 blend produced a paired match-date-clustered accuracy difference of +1.556 percentage points versus M1 (exploratory 95% interval +0.950 to +2.201 points), but the interval is not adjusted for trying multiple models. The best observed Elo logistic comparator itself showed +1.625 percentage points against M1 (exploratory match-date-clustered 95% interval **+0.861 to +2.435 points**), verified by successful real-source workflow run **42989254008**. These descriptive bootstrap intervals are **not adjusted for multiple comparisons** and therefore are not proof of future superiority.

### Diagnostic finding: draws

M1 selected **DRAW** as top 1X2 class in about **0.07%** of historical fixtures, while observed draws were **25.57%** of fixtures; causal-Elo logistic selected DRAW in about **1.8%**. This is a decision-profile issue to investigate, not by itself proof of poor draw-probability calibration. Do not force an artificial draw-frequency quota or claim an accuracy increase without out-of-sample evidence.

## Changes made in the research pipeline

1. Correct-count, full-coverage accuracy, per-season/competition metrics and fixed confidence-vs-coverage slices.
2. Strict-date causal Elo rating features: 65-point home advantage, K=24, research source team IDs, and no same-day or future-result influence.
3. Fixed challengers and blends (20/24-feature logistic, HGB, league-specific models), plus a second-stage learner trained **only on preceding seasons' genuine out-of-sample predictions and outcomes**.
4. Paired calendar-date bootstrap comparisons, raw log loss, manifest receipts and regression tests for timing, source coverage, probability validation and immutable evidence.
5. **No changes** to the canonical C1 champion, M1 goal market owner, published probabilities, live shadow ledger, deployment gate, or 63 pre-match forecasts.

## Scientific release gate

- **NO-GO** on promoting any exploratory challenger or changing production probabilities now.
- Prospective validation requires freshly issued immutable, pre-kickoff forecasts for the *same future fixtures* from the frozen champion and any challenger, with independent final-score receipts and transparent no-coverage/failure accounting.
- Track **full-fixture 1X2 accuracy**, paired difference, log loss, Brier/calibration, per-league/time drift, and draw behavior. Conditional high-confidence hit rate is a different metric and must report coverage and abstentions.
- The objective of exceeding 53.85% must be judged on an appropriately powered **new** evaluation; current 52.714% retrospective research is not that goal.
- Multiple retrospective candidates were examined, so selecting the observed best one here creates selection bias; the next period must be pre-registered before results arrive.

## Separate deployment blocker

The Vercel project `footmo-live` currently fails previews with `NOW_SANDBOX_WORKER_ROOTDIR_NOT_EXIST`: project Root Directory is configured as `live-web`, which does not exist in the current repository. No frontend deploy target can be responsibly inferred from the placeholder `frontend/` directory. Do **not** silently change Vercel settings to another unverified path or imply live website availability.

All reported metrics refer to independent historical research, not gambling returns or guaranteed predictions.
