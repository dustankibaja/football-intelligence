# Sprint 7 — Historical expansion, challenger benchmarking and shadow mode

**Status:** RESEARCH IN PROGRESS. This is not a scientific certification or a
production model promotion. The production homepage remains driven by
`footmo-empirical-poisson-trial-v1` and its strict >60% green watchlist.

## 1. Historical dataset expansion

The existing approved `mvp_a_historical_v1` is immutable. Do not alter its
membership, checksum, release key, or frozen C1/M1 model artifacts.

The new **research candidate** acquires 20 completed competition-seasons
(2021–22 through 2025–26, EPL, La Liga, Serie A, Bundesliga) from
`football-data.co.uk` using allowlisted URLs. It validates every source's
schema, score/result consistency, season dates, fixture identity and coverage.
Each source receives a SHA-256 and the normalized dataset gets a deterministic
fingerprint. Raw bookmaker columns are not ingested into features or models.

CSV source dates may not represent verified kickoff times. For leakage
protection, **all same-day results are withheld until the following calendar
date**. Do not claim exact kickoff-level parity from this research dataset.

Run from repository root after `pip install -e ./backend`:

```bash
python -m app.research.cli \
  --cache-dir data/research/sprint7/raw \
  --output data/research/sprint7/report.json
```

Use `--offline` to reproduce from cached source files without network.
The GitHub Actions workflow `sprint7-research.yml` runs acquisition and
benchmarking on an isolated runner and uploads only the JSON evidence report,
not copyrighted raw provider CSV. Failure to fetch/verify any source fails
the entire research run. The research data are **not yet approved or imported
into the production canonical PostgreSQL dataset**.

## 2. Competing models

The isolated research harness evaluates:

- `league_prior`: Laplace-smoothed historical 1X2 league frequencies.
- `empirical_poisson`: current-style venue attack/defense proxy.
- `decay_poisson`: 180-day half-life venue attack/defense.
- `dixon_coles`: time-decayed Poisson with fixed experimental rho=-0.08.
- `elo_form_logistic`: regularized multinomial logistic regression trained on
  point-in-time Elo, form, rest, league and venue features.

**Important:** these are research models, not the certified C1 or M1 frozen
artifact implementations. The 251-feature historical/live parity and canonical
team identity reconciliation required for direct C1/M1 live inference remain
separate scientific gates. No research proxy may be presented as C1/M1.

## 3. Nested season-forward backtesting and calibration

Two chronological folds, across all four leagues:

| Fold | Model training seasons | Calibration season | Untouched test season |
|---|---|---|---|
| A | 2021–22, 2022–23 | 2023–24 | 2024–25 |
| B | 2021–22 through 2023–24 | 2024–25 | 2025–26 |

Within each calibration season, the first half of match dates fits a
multinomial log-probability calibrator; the second half decides whether it
improves log loss. If validated, the calibrator is refitted on the full
**previous** season before the untouched test season. Model selection never
uses a held-out test result. Dynamic match features can use **earlier**
matches within the test season, simulating rolling walk-forward updates.

Report log loss, Brier, 1X2 accuracy, draw calibration, selective coverage,
selective accuracy, per-league results and paired bootstrap confidence
intervals. Compare against the empirical Poisson **research proxy**, not
against the deployed live model unless matching forward predictions exist.

The >60% selective policy also requires at least 60 earlier league matches,
four venue matches for each side, a >=12-point lead over the next 1X2 class,
and draw probability <=28%. This is a **watch-only/abstain** research rule,
not a profitable-bet guarantee. Odds, bookmaker margin and actual net returns
are not modeled.

## 4. Independent live shadow mode

The deployed shadow candidate `footmo-shadow-decay-dc-research-v1` uses only
verified current-season `football-data.org/v4` results available at least
four hours before publication. It is separate from the offline benchmark.
It cannot read the historical CSV research cache and **does not claim the
offline model has been trained or selected for live use**.

The Vercel Blob private snapshot stores append-only
`shadowPredictions` and `shadowEvaluations`, including immutable
pre-kickoff forecast revisions. Existing production predictions and
evaluations are unchanged. `/api/state` exposes only aggregate shadow
counts and **paired** log-loss/Brier differences, not challenger picks.
The analysis page labels these as research. Set `SHADOW_MODE_ENABLED=false`
to stop creating new shadow forecasts; existing audit records remain intact.

No backfill of past matches is permitted. A shadow evaluation requires a
persisted pre-kickoff forecast and a verified full-time score. Revised
provider scores create versioned evaluations. The homepage does not consume
shadow predictions.

## 5. Promotion gate (manual, not automatic)

Before proposing a production model change, require all of:

1. Source coverage and SHA-256 evidence for all 20 datasets; canonical team
   identity and feature mapping independently verified.
2. Reproduced historical walk-forward benchmark with held-out test scores,
   uncertainty intervals, calibration and per-league diagnostics.
3. Direct frozen C1/M1 champion comparison using its **real 251-feature**
   pipeline; no fabricated feature values, artifacts or fingerprints.
4. >=100 paired genuinely published pre-kickoff shadow evaluations (more
   desirable), with improved proper scoring rules and no serious regression.
5. Data provider licensing/redistribution review, leakage and model-parity
   tests, documented human approval and rollback plan.

**No automatic promotion. No claim of 80–90% football prediction accuracy,
guaranteed wins, positive betting expected value, or certified live science.**
