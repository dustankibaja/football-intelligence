# Footmo accuracy mission — multiseason strength v1

Date: 2026-11-12. Research only; public prediction engine remains `footmo-empirical-poisson-trial-v1`.

## Why this candidate

The restored live trial has 21 correct predictions out of 39 (53.85%), and 10 of its 18 incorrect outcomes were draws. That is a small real-world sample, not a certified accuracy rate. The existing current-season empirical goal model has little information early in a season. The challenger adds multi-season team strength, ten-match form, scoring and conceding, rest, league goal rates, and the original engine's pre-match 1X2 estimates. A fixed L2 multinomial classifier is trained only on 2020/21–2023/24. No hyperparameters are selected using 2025/26 outcomes.

## Reproducible historical evidence

- OpenFootball CC0 source, pinned commit `e6744429ee395bc86f247348c6184bb08d4eb361`; 24 source-file SHA-256 receipts.
- 5,784 training matches; 1,426 development matches in 2024/25; 1,446 test matches in 2025/26. Twenty source-missing final scores in the 2024/25 Spanish and Italian final rounds are excluded, never filled. 2019/20 is not used because the source contains an impossible season date.
- Same-calendar-day score updates are excluded until the next day. Team identity uses exact source names within league; no fuzzy joins. Earlier source dates are treated conservatively at day granularity.
- 2024/25 common set: original 622/1,258 (49.4436%), challenger 653/1,258 (51.9078%). Full challenger 743/1,426 (52.1038%).
- 2025/26 common set: original 628/1,280 (49.0625%), challenger 659/1,280 (51.4844%), +31 correct / +2.4219 percentage points. Candidate-only correct 104; original-only correct 73. Mean 1X2 log loss 1.027654 → 0.997815. Positive top-class difference in each of the four leagues. Full challenger 742/1,446 (51.3140%); original coverage 1,280/1,446 (88.5201%).
- Draw limitation remains: on the 2025/26 common set the original picked 10 draws and got 4 right; the challenger picked 8 and got 2 right. Neither solves draw classification.
- These are retrospective splits already used in earlier project research, NOT an untouched prospective holdout and NOT evidence of 60% live accuracy.
- Frozen model artifact SHA-256: `d19f979a341c3027fcdc1189b9429fac74fd437d64a8545a7c49d2c8d25f0407`. Classifier parameters trained through 2023/24; team state advanced causally through 2025/26. CI reproduces the exact artifact from pinned source bytes.

## Prospective test rules

1. Use the existing football-data.org live provider and existing private Vercel Blob snapshots. Do not create public forecast or result files containing provider data.
2. Issue both the original empirical model and the frozen challenger on the exact same eligible fixture and issue time, with only current-season scores from earlier calendar days. Preserve the authoritative provider fixture ID, team IDs, kickoff and artifact hash. For current team names that do not exactly match the historical seed, use an explicit neutral initial state and report coverage; no guessing aliases.
3. Store the challenger and its same-cutoff original baseline together in separate versioned research records. Never alter archived primary forecasts, existing all-venue pairs, or prior shadow predictions. Only a real matching final score observed after kickoff plus four hours settles a pair; rescheduling requires a new pair. Report one latest valid pre-kickoff revision per fixture, preserving earlier versions.
4. Compare correct outcomes, full paired coverage, probability quality, missed draws, and league-specific differences. Keep original published-trial accuracy distinct from the research pair baseline, because the latter is issued at the same cutoff as the challenger. No hindsight substitution of a better prediction.
5. Report the first 100 genuine paired results as an initial checkpoint, not a certification threshold. Before any promotion, require a meaningful positive paired accuracy difference supported by uncertainty analysis, improved probability quality, and enough per-league evidence to rule out obvious regressions. Continue collecting if results are inconclusive. The 60% full-coverage goal is not met by a selected subset. No automatic promotion.

## Deployment and research boundary

The original primary model and all previously saved forecasts remain unchanged. The candidate produces 1X2 research probabilities only; existing goal and scoreline markets remain owned by the original engine. Runtime research failures are isolated from the primary application. The public state API exposes aggregate comparisons, not the candidate's match picks. All historical source records are public-domain; the production provider remains the existing private-trial data connection. The first prospective results must be awaited; the next league matches begin November 20, 2026.
