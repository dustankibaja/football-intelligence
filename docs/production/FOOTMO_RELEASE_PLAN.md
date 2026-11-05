# Footmo — Production Release Gates

**Status:** NOT PRODUCTION READY. Work starts from GitHub issue #1 and draft PR #5.
**Source baseline:** `master` at `52b46e7fa7264940b925fbbe1f5dc834ef2c6bcc` (2026-10-30).
**Development branch:** `gate1/live-science-fail-closed`.

This document is an execution plan, not a certification report. Never mark a gate complete based only on a green CI badge, static configuration, a replay, or a manifest claiming PASS.

## Gate 1 — Scientific integrity (#1) — IN PROGRESS

1. Stop synthetic live features and heuristic model inference; fail closed.
2. Reconcile provider fixture identity, team/competition/season/kickoff against canonical data.
3. Materialize and persist the exact 251 point-in-time features with ordered keys, source lineage, snapshot ID and cryptographic fingerprint. Ingest approved up-to-date match history as needed; a 2021–2024-only historical release is not current 2026 form.
4. Load the actual frozen `backend/c1_artifact.json` and `backend/m1_artifact.json`, verify their contents, model-specific preprocessing, class ordering, competition/team mappings and forward outputs. C1 1X2, M1 goal markets and structural fallback.
5. Prove historical/live parity and adversarial leakage resistance; no retraining or silent recalibration.
6. Quarantine invalid prior shadow evidence append-only. The 46 historical exported shadow records are not automatically valid scientific predictions: no feature snapshot IDs, a single score-matrix fingerprint and inconsistent exported fixture associations.
7. Repair exact-score evaluation from a full distribution rather than only top-five scorelines.
8. Add tests that fail on any synthetic feature vector, fake fingerprint, invented team ID, heuristic coefficient or missing provenance.

**Exit evidence:** genuine real-provider pre-kickoff fixtures, canonical identity, 251 persisted features each, loaded model artifact SHA-256, independent prediction parity and reproducible test/CI reports. The temporary safety stop in PR #5 must only be replaced by a real verifier and frozen forward pass.

## Gate 2 — Unattended operations (#2) — NOT STARTED

Real provider result polling, finalized-score/versioned evaluations persisted to PostgreSQL, no swallowed DB errors, idempotency and fencing, actual persistent scheduler host, backup/restore and restart drills, multiple elapsed days of observed runs, alerting, provider outages and rate limits, and strict CI quality gates. Do not count a Docker/CI runner as an always-on production host.

**Exit evidence:** independently observed real unattended runs, genuine result/evaluation records linked to unchanged pre-kickoff predictions, restart/restore logs and no duplicate writes.

## Gate 3 — Customer application (#3) — NOT STARTED

Build a responsive web-first Footmo MVP with secure read-only predictions API, upcoming fixtures, 1X2, BTTS, O/U 2.5, scoreline probabilities, uncertainty/quality states, evaluation history, user accounts, accessible UI and admin operations. No fabricated fixture or probability demo presented as real.

**Exit evidence:** deployed staging URL, API contract, integration/E2E/accessibility/security tests, customer journey sign-off.

## Gate 4 — Commercial launch (#4) — NOT STARTED

Verify data provider commercial redistribution permissions and plan limits; resolve MIT license versus All Rights Reserved documentation; set up subscription billing and customer support; review applicable local legal/privacy requirements; production domain/TLS, secrets, backups, monitoring, release rollback and staged beta. No guarantee-of-profit claims or automatic betting.

**Exit evidence:** verified licensing and compliance decisions, successful billing sandbox tests, security and incident runbooks, documented go/no-go approval.

## Engineering rules

- Preserve certified Phase 4–6 parent science artifacts; any correction needs a new version and explicit independent validation.
- No bookmaker odds as model inputs, no automated betting, no post-kickoff prediction backfill.
- No guessed fixture IDs, teams, seasons, feature values, fingerprints, evaluation labels or model artifacts.
- Maintain append-only provenance. Quarantine invalid records without deleting/replacing evidence.
- Do not commit secrets or copy provider API keys into issues, PRs or logs.
- Never merge a failing or provisional gate as production-ready.
- Require real evidence at each boundary, then proceed to the next gate.
