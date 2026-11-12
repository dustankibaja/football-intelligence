# Football Intelligence & Probability Analysis System

A scientific football probability research and evaluation system for European football.

> Research workspace, not a certified live commercial forecast service. Probabilities are not guarantees; no betting or financial advice.

## Current status

- Versioned prediction engine: 1X2, Over/Under 2.5, BTTS and score distributions.
- Frozen C1 and M1 artifacts, canonical temporal feature contracts and immutable shadow forecasts.
- Retrospective and prospective comparisons, including independent outcome reconciliation, confidence/coverage tracking and research-only challenger models.
- Static research workspace in `live-web/`: interactive user-supplied Poisson scenarios, local paired-ledger/assessment inspection, no live fixture feed or publication.
- Private read-only REST API and reproducible PostgreSQL container deployment.
- Live commercial data, canonical production database provisioning, independent prospective challenger validation, and model promotion remain gated.

## Repository layout

```text
backend/           FastAPI, SQLAlchemy, Alembic, scientific models and tests
live-web/          Dependency-free research web workspace (Vercel root)
frontend/          Legacy frontend planning placeholder; not the deployment root
data/              Local data storage; do not commit licensed raw source files
docs/              Architecture, research evidence and operating procedures
infrastructure/    Local PostgreSQL and private release container configuration
.github/           Test, research and deployment workflows
```

## Run the research workspace locally

```bash
cd live-web
npm test
npm run build
python -m http.server 8080 --directory dist
```

Open `http://localhost:8080`. The scenario lab uses manually supplied expected-goal assumptions, not the trained models. Import your own approved research artifacts locally; nothing is transmitted by the browser.

## Run the private API

Use Python 3.11+ and PostgreSQL 16. For a disposable local/private release:

```bash
# Supply FI_POSTGRES_PASSWORD and FI_RESEARCH_READ_TOKEN securely in the environment.
# Generate unique strong credentials; never commit them.
docker compose -f infrastructure/docker-compose.release.yml up --build -d
curl http://127.0.0.1:8000/api/v1/health
```

The composition creates an initially empty PostgreSQL database, applies the canonical migration chain and binds the API to localhost. It does not import the approved historical corpus, ingest live fixtures or publish model forecasts. Private matches, predictions, models and evaluations require a server-side bearer token of at least 32 characters. For a real deployment, provision a secured server, approved data and backups, TLS, secret management and operational monitoring.

Alternative development setup: `pip install -e "./backend[dev]"`, configure a local PostgreSQL database using `.env.example`, run `alembic upgrade head` from `backend/`, then `uvicorn app.main:app --reload --port 8000`. Production refuses SQLite fallback.

## Data rights and scientific gates

Football-Data.co.uk's free data is not a commercial or automated heavy-training license. Do not enable source network workflows without documented permission for the exact use. Existing authorized local source snapshots can be evaluated offline. See [provider policy](https://football-data.co.uk/contact.php).

Our archived noncommercial fixture research uses [open-matchday](https://github.com/oladeeayo/open-matchday) with source attribution; that compilation does not grant commercial rights or override upstream terms. No raw provider data is shipped with the research web application.

The licensed-signal import contract validates source permission assertions, canonical identities, prior-match xG/xGA, as-of availability and confirmed lineup data, and explicit missingness. It does not independently grant rights, fetch a licensed feed, or change the frozen C1 feature schema.

See [the research workspace release and remaining production gates](docs/operations/research_workspace_release_2026_11_12.md).

## License

Repository software: MIT, as stated in [LICENSE](LICENSE). This license does **not** grant rights to third-party football data, provider feeds or separately licensed artifacts.
