# Footmo — Football Intelligence & Probability Analysis

Footmo is a scientific football probability platform covering the Premier League, La Liga, Serie A and Bundesliga. It estimates 1X2 outcomes, expected goals, BTTS, Over/Under 2.5 and exact scorelines using pre-match evidence.

**Status (2026-11-05): ACTIVE DEVELOPMENT — NOT COMMERCIALLY LIVE.** The historical scientific program and model artifacts exist, but the newly integrated live pipeline has not passed independent 2026 provider-data parity, multi-day host soak or commercial launch checks.

> **Probability, not certainty.** Footmo does not guarantee match outcomes, financial returns or betting results. It does not use bookmaker odds as predictive inputs or execute bets.

## Current implementation

- **Historical science:** 4,338 historical fixtures and 251 point-in-time features in the approved MVP-A research program. C1 trained multinomial logistic regression owns primary 1X2 probabilities. M1 attack–defence Poisson owns goals, exact scores and structural fallback.
- **Frozen inference:** Real committed C1/M1 artifact loading, fingerprint checks, competition registry validation and model-class forward inference.
- **Live pipeline:** Provider-backed canonical fixture and final-result ingestion, recent-history reconciliation, PIT snapshot materialization, pre-kickoff enforcement, immutable prediction and evaluation persistence.
- **Customer preview:** Responsive web UI in `frontend/`; authenticated read-only API for verified predictions, matches, evaluations and model metadata. The API is disabled by default.
- **Staging infrastructure:** Private Docker Compose topology, non-root API image and Linux scheduler configuration. No persistent host deployment has been certified.
- **Testing:** PostgreSQL migrations, integration and unit tests, fatal Python checks and frontend syntax checks in GitHub Actions.

See [the production release gates](docs/production/FOOTMO_RELEASE_PLAN.md) and [the Gate 1 implementation evidence](docs/production/GATE1_PROGRESS_2026-11-05.md). The current development work is tracked in [PR #5](https://github.com/dustankibaja/football-intelligence/pull/5).

## Repository

```text
backend/          FastAPI, trained artifact inference, SQLAlchemy, Alembic, Pytest
frontend/         Responsive customer web preview (HTML/CSS/JavaScript)
data/             Research data directories (not bundled with the production image)
docs/             Scientific specifications and production release evidence
infrastructure/   PostgreSQL, private staging Compose and runbook
scripts/          Operational scheduler configurations
.github/          CI regression and frontend checks
```

## Local backend development

Requires Python 3.11+, PostgreSQL 16 and Git.

```bash
git clone https://github.com/dustankibaja/football-intelligence.git
cd football-intelligence
python -m venv .venv
# Activate .venv using your shell's instructions.
python -m pip install -e "./backend[dev]"
# Configure your untracked .env from .env.example; never commit secrets.
docker compose -f infrastructure/docker-compose.yml up -d
cd backend
alembic upgrade head
pytest
uvicorn app.main:app --reload --port 8000
```

For a private frontend preview, see [frontend/README.md](frontend/README.md). For a host-based staging deployment, see [infrastructure/STAGING_RUNBOOK.md](infrastructure/STAGING_RUNBOOK.md).

## Scientific and operational safeguards

1. No post-kickoff prediction backfill; observations must precede the feature cutoff.
2. Real provider IDs, canonical team/competition/season mappings and source fingerprints are required for live data.
3. Frozen trained model artifacts are verified; no automatic training or recalibration during live operation.
4. Predictions and versioned evaluations are separate, append-only evidence.
5. Invalid legacy shadow records are excluded from verified customer endpoints.
6. Missing drift-reference evidence currently blocks live predictions rather than producing an invented PSI value.
7. No commercial claims are permitted until provider licensing, customer entitlements, security, persistent hosting and a real unattended soak are verified.

## Licensing

The repository currently contains an **MIT LICENSE file**. That license governs the repository code as published. Football data and other third-party content have separate licensing requirements. Any decision to change the software license for future commercial distribution requires an explicit rights review; do not assume the current MIT license makes third-party football data commercially redistributable.
