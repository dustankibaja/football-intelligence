# Footmo — Private Staging Runbook

**Status: NOT PUBLIC-LAUNCH APPROVED.** This runbook documents a deployment path; it does not prove a host is running.

## Preconditions

- A persistent Linux host with Docker Compose, firewall and SSH hardening.
- Confirmed provider permissions for storing/using football data commercially.
- Real canonical teams, competitions, seasons and recent completed-match history in PostgreSQL.
- Scientific Gate 1 sign-off including valid PIT snapshots, measured drift policy inputs, model artifact hashes, real provider fixtures and no synthetic evidence.
- An off-host database backup destination and monitored restore drill.
- TLS reverse proxy and a real hostname before external exposure.

## Private staging

1. Place secrets in an untracked, owner-readable environment file outside the Git checkout. Required: `POSTGRES_USER`, `POSTGRES_PASSWORD`, `CUSTOMER_API_TOKEN`; optionally `LIVE_DATA_API_KEY` and `CUSTOMER_ALLOWED_ORIGINS`.
2. From repository root, run:
   `docker compose --env-file /secure/path/footmo.env -f infrastructure/compose.staging.yml build`
3. Start the database:
   `docker compose --env-file /secure/path/footmo.env -f infrastructure/compose.staging.yml up -d postgres`
4. Run migrations:
   `docker compose --env-file /secure/path/footmo.env -f infrastructure/compose.staging.yml run --rm api alembic upgrade head`
5. Start private API and web:
   `docker compose --env-file /secure/path/footmo.env -f infrastructure/compose.staging.yml up -d api web`
6. Test localhost endpoints, read-only customer API, and verified empty states. Do not open ports 8000/5173 publicly.
7. Confirm database snapshots, backup and restore, monitoring, and provider quotas before scheduling unattended jobs.

## Live history synchronization

With a migrated database and authorized provider key, from the backend environment:
- Preview: `python -m app.providers.live.backfill --days 30 --dry-run`
- Apply: `python -m app.providers.live.backfill --days 30`

Review rejected/unmapped fixtures and verify each source before attempting live predictions. Never synthesize teams or history to bypass the gate.

## Current blockers

- No verified persistent host or real multi-day soak in this branch.
- Frozen Phase 5.2 drift PSI reference is not connected; live predictions remain blocked rather than claiming a false zero drift.
- No verified commercial redistribution license from the football data provider.
- Customer API uses a single private staging key; per-user authentication, subscriptions, rate limits and entitlements are required before public access.
- Historical shadow predictions with fabricated feature provenance remain quarantined from customer APIs.
- CI's historical Ruff backlog still needs enforcement/cleanup.
- No observed payment sandbox, security review or real customer beta.
