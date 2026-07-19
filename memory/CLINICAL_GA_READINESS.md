# Clinical Redesign — GA Readiness Checklist

**Purpose:** Final gate to promote the Clinical redesign from Stage 3 to Stage 4 (general availability).

**Status:** `READY FOR AUTHORIZED STAGED ROLLOUT` — GA cannot be declared until every checkbox below is marked and approvals recorded.

## GA entry criteria

- [ ] Stage 3 exit criteria satisfied (see `CLINICAL_STAGED_ROLLOUT_PLAN.md`)
- [ ] G1 UAT signed by clinical + operations leads
- [ ] G2 performance thresholds approved AND measured on a 200+ event chart
- [ ] G3 rollback runbook rehearsed in production
- [ ] G4 contract freeze re-confirmed (registry vs. code)
- [ ] G5 screenshot set complete (25 shots) + release notes distributed

**Performance thresholds:** governed by `/app/memory/CLINICAL_PERFORMANCE_THRESHOLDS.md`. GA cannot proceed until at least one approved combination row (typically desktop / normal / 500-event) is signed, and the readiness metrics table below references those exact values. Additional combinations (throttled, mobile, larger datasets) require their own approval rows before governing a stage that exposes them.

- [ ] G6 monitoring dashboards stable ≥ 10 business days across Stage 3 cohort
- [ ] No open Blocker/Critical defects tagged to the Clinical redesign

## GA readiness metrics (record at GA cut)

| Metric | Baseline | Stage 3 observed | GA target |
|---|---:|---:|---:|
| Clinical page load failures per hour per tenant | ___ | ___ | ≤ baseline |
| Section-error-boundary activation rate | ___ | ___ | ≤ baseline |
| API error rate on Clinical endpoints | ___ | ___ | ≤ baseline |
| Preference-save failure rate | ___ | ___ | ≤ 0.5% |
| Timeline P95 | ___ | ___ | ≤ approved release budget (combination row in `CLINICAL_PERFORMANCE_THRESHOLDS.md`); alerts fire at approved warning threshold; rollback at approved rollback trigger |
| Wall-clock initial render P95 | ___ | ___ | Same — reference approved combination row |
| Encounters P95 | ___ | ___ | Same |
| Billing-readiness aggregate P95 | ___ | ___ | Same |
| Support ticket volume tagged `clinical-redesign` | ___ | ___ | ≤ 2× baseline |

## Communications

- [ ] Public release notes published — link: __________________
- [ ] Support brief distributed to all support tiers
- [ ] Training deck ready (if any)
- [ ] Rollback runbook accessible to all on-call rotations
- [ ] Compliance sign-off recorded

## Sign-off

| Role | Name | Signature | Date |
|---|---|---|---|
| Release manager | ____________________ | ____________________ | __________ |
| Clinical platform lead | ____________________ | ____________________ | __________ |
| Platform reliability lead | ____________________ | ____________________ | __________ |
| Product owner | ____________________ | ____________________ | __________ |
| Compliance officer | ____________________ | ____________________ | __________ |

## Post-GA guardrails (first 30 days)

- Monitoring dashboards remain live.
- On-call rotation stays engaged.
- Rollback path remains armed — do not archive the runbook.
- Any new defect referencing this release version → treat as a rollout defect, not a normal maintenance ticket.
- Retrospective scheduled at 30 days post-GA to consider lifting the freeze (separate decision).

## What GA does NOT authorize

- New features.
- New workflow-mode roles.
- New telemetry categories.
- New preference fields.
- Removal of the legacy fallback.
- Cross-patient aggregation surfaces.

These continue to require a separately scoped follow-up.
