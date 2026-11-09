# Football Intelligence — Sprint 7.3 forensic review (2026-11-09)

## Scope and provenance
Read-only inspection of committed `phase7_3_live_soak_evidence.json`, `phase7_3_live_soak_certification_manifest.json`, and live adapter, orchestrator, prediction and scoring implementations. No claim is made that this review observed the developer's Windows scheduler, a VPS, an up-to-date provider account, or any database outside disposable GitHub CI.

## Verified evidence
- The committed manifest says `SOAK_IN_PROGRESS_NOT_YET_CERTIFIED`; the 2026-10-30 evidence records 46 predictions, including 11 M1 fallback predictions, 7 completed/evaluated matches, and 39 pending evaluations.
- The evidence's fixture horizon ends on 2026-11-02, while the last audit timestamp is 2026-10-30. As of this review the snapshot is stale. Re-polling or database inspection is required before any revised completion claim.
- The 46 recorded predictions have exactly two distinct 1X2 triples. All 46 have the same score-matrix fingerprint. This is a strong warning against interpreting the dataset as fixture-specific scientific inference.
- `app/providers/live/adapter.py` supplies placeholder `feature_values = [0.45, -0.2] + [0.0] * 249` for mapped live fixtures, rather than invoking the historical/live feature parity engine.
- `app/automation/orchestrator.py` also defaults absent feature vectors to static values, pads/truncates lengths to 251, and can supply synthetic IDs and feature fingerprints.
- `app/prediction/service.py` computes Poisson lambdas and C1 logits with explicit hard-coded coefficients; its model fingerprint identifiers are short literals. Runtime loading and verification against frozen model coefficient artifacts was not demonstrated by this path.
- In historical v1.0.0 scoring, `actual_score_probability` was set from `top_scorelines` (top five) only. Six of the seven stored evaluations have zero in this field even though an in-grid Poisson score probability need not be zero.
- The manifest reports `persistent_host_status = BLOCKED_NO_HOST_ACCESS` and `windows_live_scheduler = DISABLED`.

## Scoring correction (new evaluations only)
The new scoring schema v1.1.0 reconstructs the 10x10 Poisson grid from *stored*, three-decimal `lambda_home`/`lambda_away` and scores every observed result within that grid. The top-five shortlist continues to determine top-1/top-3 hit metrics only. It does not change any saved prediction, frozen model manifest, or historical evaluation. Historical v1.0.0 rows must remain immutable. Because rates were rounded before storage, v1.1.0 exact-score values are reconstructed to saved-rate precision, not guaranteed bitwise-identical to the original full-precision matrix. Results outside the truncated 0..9 by 0..9 score grid remain out of support and require a separately versioned tail policy.

## Seven execution gates for the next scientific/operational release
1. **Authoritative CI:** green fresh PostgreSQL/Alembic/pytest GitHub execution tied to a commit, with no SQLite fallback in CI.
2. **Score probability semantics:** v1.1.0 regression tests for non-top-five outcomes; no rewriting historical evaluations.
3. **Real 251-feature live materialization:** remove placeholder arrays and synthetic snapshots; generate point-in-time values through an approved dataset/feature pipeline and prove per-fixture parity and leakage isolation.
4. **Genuine frozen C1/M1 serving:** load actual certified coefficients/model artifacts, verify hashes and team-specific output, and prevent fallback to hand-coded surrogate weights under the certified identity.
5. **Result-completion closure:** re-poll original 46 fixtures using the authorized provider, ingest remaining official terminal results and corrections idempotently, report completion/pending/void counts, and preserve pre-match prediction immutability. Never backfill predictions after kickoff.
6. **Persistent runtime:** provision/verify an actual supported Linux host with locked-down secrets, six-hour scheduler, lease fencing, logs, restart recovery, and backup/restore. Local Windows-only and YAML-only evidence are insufficient.
7. **Final operational/scientific certification:** require non-synthetic predictions, multi-day unattended success, provider/database incident accounting, calibration and proper-score metrics with adequate sample sizes, stable scientific hashes, and a signed/hashed evidence manifest. A passing CI build alone does not certify live scientific performance.

## Gate decision
**PostgreSQL CI:** verified separately by GitHub Actions run `42601715801` (PR #6) — PostgreSQL 16.15, revision `20261023_0006`, targeted integration 57 passed / 3 skipped, full regression 522 passed / 11 skipped; the Ruff job remains advisory (468 reported violations). That run predates the scoring correction.

**Sprint 7.3 scientific/operational certification:** **BLOCKED** until gates 3–7 above have direct evidence. The current 46-prediction sample cannot establish genuine fixture-specific model quality, and the prior seven evaluated scores do not certify the remaining fixtures.
