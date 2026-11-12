# Research workspace release and remaining production gates — November 12, 2026

## Scope

This is a tested private/research product milestone, not the launch of a commercially licensed live football prediction service. The web workspace uses user-supplied expected goals for a mathematically independent Poisson scenario and browser-local research ledger inspection. The private backend reads canonical records but does not publish forecasts. No raw provider records or private API credentials are packaged in the static build.

## Running the software

- Web: from `live-web`, run `npm test`, `npm run build`, then serve `dist` over HTTP. Vercel project `footmo-live` uses `live-web` as its root.
- Private backend: set strong server-side environment variables `FI_POSTGRES_PASSWORD` and `FI_RESEARCH_READ_TOKEN` (minimum 32 characters for the API token). Use `docker compose -f infrastructure/docker-compose.release.yml up --build -d`. PostgreSQL remains unexposed; the API binds to loopback by default. The migration service applies the checked-in Alembic chain before API startup.
- Verify `/api/v1/health` reports connected PostgreSQL and production environment. GET `/api/v1/matches/`, `predictions/`, `models/`, and `evaluations/` require a private bearer credential and provide paginated, read-only records. Empty database means empty results, not fake forecasts. Never place bearer credentials in the web frontend. Production does not silently fall back to SQLite.
- The container smoke test uses disposable credentials and a new PostgreSQL database. It is not evidence that a production database has been provisioned or that canonical history has been loaded.

## Provider rights and licensed data import

Football-Data.co.uk publishes a limited personal/academic access policy: [provider usage policy](https://football-data.co.uk/contact.php). Repeated automated model-training downloads or a commercial data product are not authorized by public availability. Existing direct-network research workflows run their offline tests on PR/push, but real-source steps require an explicitly authorized manual dispatch and `FOOTBALL_DATA_NETWORK_APPROVAL_REFERENCE`. The repository setting is an operator assertion only, not permission; configure it only after documented authorization covering exact retrieval frequency, training, retention, and intended distribution. Do not use a public mirror as a workaround. The original research source [open-matchday](https://github.com/oladeeayo/open-matchday) is attributed, explicitly noncommercial, and its compilation does not override underlying provider rights.

The original 63 and 71 pre-match ledgers, their immutable SHA-256 receipts, and frozen model artifacts are retained. The 63-match exact-score extension is not regenerated on later code pushes. A local results evaluation can consume legitimately acquired source CSV snapshots named E0.csv, SP1.csv, I1.csv and D1.csv:

```bash
cd backend
python -m app.research.prospective_accuracy_evaluator \
  --paired-ledger /private/verified/paired_prospective_elo_2627.json \
  --results-dir /private/authorized/results \
  --output /private/assessments/paired_evaluation.json
```

A local file's source URL/hash is not independent proof of its origin; the receipt marks local/injected bytes as origin-unverified. Independently authenticate provider provenance and timestamps before scientific or commercial claims. Do not upload raw restricted data to the public repository or a broadly accessible artifact.

### Richer pre-match signal pipeline

The new `app.research.licensed_signal_import` module accepts a local normalized JSON bundle, with `schema_version: "licensed_football_signals_1.0.0"`:

- `target`: exact canonical fixture/competition/team UUIDs and timezone-aware kickoff and forecast cutoff.
- `grants`: provider identity, written authorization reference, internal-research and model-training permissions, optional expiry, separate publication permission.
- `prior_matches`: completed previous fixture/team IDs, xG for/against, completion timestamp, source record identifier, original published/captured timestamps and source payload SHA-256.
- `availability`: same target fixture and team IDs, optional 11 distinct confirmed starter IDs, optional unavailable player IDs, original receipt timestamps. Null means unknown; an explicitly empty unavailable-player list means known zero.

The importer rejects future or same-target match statistics, duplicate prior-match/team observations, unknown or expired rights declarations, post-cutoff capture times, invalid team IDs and a player both starting and unavailable. It produces a separate research feature report with five-match xG/xGA means (null if fewer than five eligible matches), explicit missingness, coverage, as-of lineage and SHA-256 fingerprints. It never downloads a provider feed, trains a model, or changes the existing 251-feature C1 artifact.

```bash
cd backend
python -m app.research.licensed_signal_import \
  --input /private/authorized/fixture_signal_bundle.json \
  --output /private/research/fixture_signal_features.json
```

For a real provider integration, require documented rights and coverage for the four target leagues, historical pre-match-as-of snapshots and reliable identity mappings. Provider availability and commercial rights must be checked separately. The input contract does not independently authenticate provider grants or source IDs. Confirm lineups and injury availability can only inform predictions at cutoffs where they were actually known. xG from the predicted match is never a pre-match input.

## Remaining gates before a live tool

1. **Canonical database and source:** provision secured PostgreSQL and restore the original rights-approved 4,338-fixture historical release with matching dataset fingerprint, exact provider/team mappings and canonical 251-feature order. An empty migrated database is insufficient. `CanonicalPostgresShadowReader` already checks these conditions and runs pinned frozen C1/M1 artifacts; it rejects unknown teams and post-kickoff fixtures. Do not synthesize identifiers or use a 24-feature Elo model as if it were C1.
2. **Prospective model validation:** issue a new same-fixture, pre-kickoff canonical C1/M1 versus any challenger ledger with pre-registered evaluation and source availability. The 71 existing paired M1/Elo forecasts remain a separate exploratory cohort. Independent final scores and sufficient completed fixtures are necessary; no 60% full-coverage accuracy is certified.
3. **Commercial authorization:** acquire appropriate football data rights (training, API automation, storage and intended publication). No provider plan or paid service has been purchased by this work. A source grant in JSON or an environment flag is not a license.
4. **Production backend:** deploy the private container on a selected secured host with real database credentials, backups, TLS, audit logs, monitoring and approved ingestion configuration. Keep research-only endpoints gated; live publication remains an independent release decision.

These are explicit release blockers, not invitations to generate predictions from missing data. The web research workspace may be deployed and used without a paid data feed, but it is not the production prediction service.
