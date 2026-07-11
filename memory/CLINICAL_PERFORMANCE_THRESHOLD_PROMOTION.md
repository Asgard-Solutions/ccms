# Clinical Performance — Threshold Promotion Runbook

**Purpose:** The exact process the release manager and platform reliability lead follow to promote a harness measurement into approved thresholds that gate **both** release qualification (G2) AND runtime monitoring / rollout stop conditions.

**Why:** Keeping release qualification and runtime monitoring on the **same** approved numbers prevents the "measured differently at Stage 2" trap where a release passed a lax budget but rolls back on a tighter runtime alert (or vice versa).

**Preconditions:**

- Fixture available (`scripts/seed_large_chart.py`) — verified 2026-02-15.
- Harness available (`scripts/run_clinical_perf.py`) — verified 2026-02-15.
- Frontend production build (`cd /app/frontend && yarn build`).
- Non-production environment.

## Step 1 — Run the harness

Execute the canonical G2 command:

```
cd /app/backend && APP_ENV=development python -m scripts.run_clinical_perf \
  --patient fixture-large-chart-patient-0001 \
  --seed-fixture --fixture-events 500 \
  --runs 20 --warmup 3 --profile desktop --network normal \
  --confirm-non-production
```

Expected artefacts:

- `/app/memory/performance/PHASE3_PERFORMANCE_RAW_RESULTS.json`
- `/app/memory/performance/PHASE3_PERFORMANCE_REPORT.md`

Both are labeled **"Measured — threshold approval required"**. The harness does **not** decide pass/fail.

## Step 2 — Platform reliability review

Platform reliability lead reviews the report and **explicitly** proposes:

- **Release budget** per metric — the value the report must clear.
- **Warning alert** per metric — higher than the release budget, with headroom to absorb normal variance.
- **Rollback trigger** per metric — strictly higher than the warning, requires a sustain window.
- **Sustain windows** for warning + rollback.

**Do NOT auto-derive warning / rollback from the first observed values.** Multiply for headroom; discuss with the on-call rotation; anchor to real user tolerance thresholds. Noisy runtime alerts are the failure mode this rule exists to prevent.

## Step 3 — Approval recording

Populate the matching combination row in `/app/memory/CLINICAL_PERFORMANCE_THRESHOLDS.md`:

- One row per (`profile`, `network`, `dataset size`, `browser/device`) combination.
- **Enforce ordering:** for every metric, `Release budget < Warning < Rollback`. Reject any row that violates.
- Fill approval owner, approval date, approval channel (ticket / email / meeting record).

## Step 4 — Cross-document promotion

Once the row is signed:

1. Update `CLINICAL_MONITORING_PLAN.md` — replace the pending thresholds in the "Proposed stop thresholds (require approval)" table with the approved values from Combination row(s). Cite the row.
2. Update `PHASE3_PERFORMANCE_TEST_PLAN.md` §Proposed thresholds — change the header from *Proposed* to *Approved* and cite the row.
3. Update `PHASE3_PERFORMANCE_REPORT.md` (or a newer run's report) — flip the final gate status from `COMPLETE — MEASURED, BUDGET APPROVAL REQUIRED` to `COMPLETE — MEETS APPROVED BUDGET` **only if** the measured values clear the newly approved release budgets.
4. Update `CLINICAL_STAGED_ROLLOUT_PLAN.md` §"Rollout stop conditions" — reference the approved rollback triggers from `CLINICAL_PERFORMANCE_THRESHOLDS.md` for the exact profile + dataset combinations that apply to each stage.
5. Update `CLINICAL_ROLLOUT_CHECKLIST.md` — check off the "Performance thresholds approved" item and add the approval date + owner.
6. Update `CLINICAL_GA_READINESS.md` — populate the "GA target" column in the readiness metrics table with the approved values.
7. Update `CLINICAL_RELEASE_GATE_STATUS.md` G2 row when the release-budget comparison lands.

## Step 5 — Context enforcement

Every promoted threshold carries the **full** approval context (profile, network, dataset, browser/device). Downstream references must include the context. Example:

> Timeline load P95 stop threshold: **X ms** on **desktop / normal network / 500-event fixture** (approved combination 1). Mobile and throttled conditions require separate approval before being governed by this value.

If a rollout stage exposes an unapproved combination (e.g., pilot clinic uses tablet, or a 1000-event chart appears), the release manager must either:

- Delay the stage until the missing combination is approved, OR
- Explicitly waive the missing combination with a documented residual-risk statement, OR
- Fall back to conservative Combination 1 thresholds and re-measure during the stage.

## Step 6 — Renewal

Every approval expires at the earlier of:

- 180 days from approval date, OR
- Any change to the harness measurement method, OR
- Any change to the Clinical redesign scope (post-freeze lift).

Renewal repeats Steps 1–4. The previous row moves to the History section of `CLINICAL_PERFORMANCE_THRESHOLDS.md`.

## Never do these

- Never copy the observed value into the release budget, warning, and rollback columns.
- Never derive rollback triggers from a single run.
- Never apply thresholds across profiles / network / dataset combinations they weren't measured against.
- Never mark G2 `COMPLETE — MEETS APPROVED BUDGET` without the approval row signed AND the measured values clearing the release budget in that row.
- Never let runtime warning/rollback numbers diverge from `CLINICAL_PERFORMANCE_THRESHOLDS.md` — one source of truth.
- Never populate the History section without moving the row (not copying).
