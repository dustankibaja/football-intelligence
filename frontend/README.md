# Footmo — Customer web preview

A responsive, real-data-only customer-facing dashboard. It displays verified match probabilities and post-match evaluations from the backend. It does **not** create demo fixtures, generate forecasts in the browser, or use bookmaker odds.

## Run locally

1. Start PostgreSQL and apply backend migrations (`cd backend && alembic upgrade head`).
2. Set a private `CUSTOMER_API_TOKEN` in the backend environment. Do not commit it.
3. Set `CUSTOMER_ALLOWED_ORIGINS=http://localhost:5173` and run the FastAPI backend (`cd backend && uvicorn app.main:app --reload`).
4. From the repository's `frontend` folder run `python -m http.server 5173`.
5. Visit `http://localhost:5173`, enter `http://localhost:8000` and your private API token.

The access token is held in page memory only, not saved to localStorage or cookies. This is a **private staging access method**, not a customer identity/subscription system. Do not distribute a shared token to paying customers.

## Current boundaries

- By default, the customer API is **disabled** (HTTP 503).
- Predictions are only returned if linked to verified C1/M1 artifact fingerprints, immutable feature snapshots, full score matrices and canonical fixture records.
- If the scientific pipeline is blocked, the dashboard shows an honest empty state.
- Authentication/accounts, individual entitlements, payment subscriptions, provider commercial licensing, monitoring, and production deployment remain required before public launch.
