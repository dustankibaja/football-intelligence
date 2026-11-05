# Footmo — Gate 1 Implementation Progress (2026-11-05)

**Gate status: IN PROGRESS — NOT CERTIFIED.**
**Development branch:** `gate1/live-science-fail-closed`
**Review:** PR #5, GitHub issue #1.
**Original audited baseline:** `52b46e7fa7264940b925fbbe1f5dc834ef2c6bcc`.

This report distinguishes **implemented source code** from **observed test evidence** and from **unverified live operational claims**.

## Implemented source changes

1. Frozen C1 and M1 artifact loaders validate model key/version, artifact fingerprints, training dataset fingerprint, pinned competition registry, and loaded file SHA-256. Predictions delegate to trained model classes rather than live hard-coded coefficients. Unknown frozen-training teams trigger the frozen M1 structural fallback.
2. Provider adapter no longer generates synthetic 251-element features or invented feature/history fingerprints. The live feature materializer requires provider ID, canonical competition/season/teams, real recent final history, a pre-kickoff cutoff and durable immutable 251-feature snapshot.
3. Provider recent finished-history ingestion uses bounded date windows, explicit team mapping, verified final scores, deterministic provider fixture IDs and append-safe SQL. Frozen training release is not mutated.
4. Historical feature queries now exclude match results whose completion timestamps are later than the prediction cutoff.
5. Full 11×11 M1 exact-score distribution is persisted via Alembic `20261105_0007`; actual-score evaluation uses the full distribution, not only the top-five display list.
6. Live SQL prediction persistence requires real canonical fixture, feature snapshot and run identities. The active live cache excludes old synthetic records. Prediction runs are not customer-visible until finalized.
7. Live result polling, versioned evaluation persistence, canonical final-score updates, and audit records are implemented; provider corrections beyond result version 1 still require a complete correction/reconciliation design.
8. Private customer API and responsive Footmo frontend are implemented, default-deny and real-data-only. The frontend has a verified empty state, not fabricated match predictions.
9. A private staging Docker Compose topology, non-root API Dockerfile, environment controls, and runbook are committed. These files do not constitute a deployed or observed persistent host.
10. Live dry-run avoids writing history, snapshots, leases and operational summaries. Scheduler reports partial scientific blocks with a nonzero exit code.

## Verified automated evidence

GitHub Actions at commit `82798de5d460acf0ce715084bb7d4b7d05e1cad1`: **540 passed, 11 skipped, 7 warnings**. Later CI at `96fb561227c31f431195d6213595ec7db189124d`: **542 passed, 11 skipped, 7 warnings**. These were real CI runs, but not evidence of a live production soak. A later green run at commit `8a54a9b533d3a2fd2b04c42bb68452af463fc949` passed **545 tests, 11 skipped, 7 warnings**, including strict live SQL persistence and frontend checks. Newer commits require their own CI verification before sign-off.

The existing broad Ruff lint report is advisory; fatal new-code checks and frontend syntax/Compose checks are enforced separately. Do not treat an advisory lint job's green badge as zero lint debt.

## Untrusted prior evidence

`phase7_3_runtime_migration_evidence.json` reports 46 exported matches and 46 predictions, but the audited source used placeholder feature snapshots and heuristic live inference. These records remain in append-only history and are excluded from verified customer/API paths; **not certified**. A full per-fixture reconciliation with original provider payloads is still required.

## Blocking items before Gate 1 can be signed

- **Live provider evidence:** No verified 2026 provider API request, canonical match, completed history, 251-feature PIT snapshot, trained model inference and SQL prediction have been observed together on a persistent host in this work.
- **Drift:** Frozen Phase 5.2 PSI reference/cohort evidence is not connected. The live orchestrator intentionally blocks unknown drift inputs rather than passing a fabricated value of zero.
- **Parity:** Historical/live independent parity across all four leagues on the approved 4,338-fixture historical release has not been rerun against the new runtime path. Model-unit tests are not a substitute.
- **Operational evidence:** Real unattended multi-day scheduler runs, restart/backup-restore drill, correction handling, alerting and external monitoring are not verified.
- **Commercial prerequisites:** Data provider commercial rights, individual customer authentication/entitlements, payments, license choice, legal review, support and a public TLS deployment are unresolved.

## Release decision

**NO-GO for public or paid launch.** Keep PR #5 in draft, preserve the original scientific manifests and do not expose unverified predictions.
