# Clinical Redesign — Staged Rollout Plan

**Redesign scope:** Patient Profile > Clinical (Phases 1 + 2 Waves A/B + Phase 3 Slices 1–6).
**Freeze:** 2026-02-15.
**Status:** `READY FOR AUTHORIZED STAGED ROLLOUT`.
**Change control:** During rollout, only verified defects are accepted. No new features, telemetry categories, contract vocabularies, or role behavior changes.

## Rollout stages

### Stage 0 — Engineering validation (completed by release-gate pass)

| Item | Owner | Status |
|---|---|:-:|
| Frontend Jest suite green (117/117) | Clinical platform | ✅ |
| Backend Pytest clinical suite green (152/152) | Clinical platform | ✅ |
| Feature-flag registry frozen | Clinical platform lead | ✅ |
| Contract-freeze evidence | Clinical platform lead | ✅ |
| Rollback rehearsal (preview environment) | Platform reliability | ✅ |
| Security review — telemetry `extra=forbid` probe | Compliance | ✅ (test file) |

### Stage 1 — Internal users

**Cohort:** Product, QA, clinical informatics, support, operations (~10–15 accounts on the staging tenant).
**Entry criteria:** Stage 0 complete + G1 UAT signatures + G2 threshold approval + G3 production walk-through executed.
**Exit criteria:** No Blocker/Critical defects for 5 consecutive business days; internal survey ≥ 8/10.
**Start date:** TBD (post approvals).
**Duration:** 5 business days minimum.
**Owner:** Release manager.
**Backup owner:** Clinical platform lead.
**Enabled flags:** All eight clinical flags `on`.
**Monitoring:** See `CLINICAL_MONITORING_PLAN.md`.
**Support coverage:** Business hours + on-call Slack.
**Rollback threshold:** Any single production incident classified Blocker.
**Communication plan:** Internal email + dedicated Slack channel `#clinical-redesign-internal`.
**Approval authority:** Release manager + clinical platform lead.

### Stage 2 — Pilot clinic

**Cohort:** One pilot clinic (~5–20 users). Selected based on clinical mix + pilot willingness.
**Entry criteria:** Stage 1 exit criteria met + 200+ event chart measured under thresholds + support brief distributed + pilot feedback form ready.
**Exit criteria:** No Blocker/Critical defects for 10 consecutive business days; pilot survey ≥ 7.5/10; billing workflow unchanged (spot-check via existing billing metrics).
**Duration:** 10 business days minimum.
**Owner:** Release manager.
**Enabled flags:** All eight.
**Monitoring:** All Stage-1 signals + weekly pilot check-in.
**Support coverage:** Extended hours + dedicated support contact.
**Rollback threshold:** Any Blocker OR two Criticals within 24 h OR sustained error rate > threshold (see monitoring plan).
**Communication plan:** Weekly stakeholder email + Slack.
**Approval authority:** Product owner + clinical platform lead.

### Stage 3 — Expanded cohort

**Cohort:** 20–30% of tenants OR 3–5 additional clinics.
**Entry criteria:** Stage 2 exit criteria met.
**Exit criteria:** No Blocker/Critical for 10 business days across the cohort.
**Duration:** 10 business days minimum.
**Owner:** Release manager.
**Enabled flags:** All eight; ability to disable per-tenant if requested.
**Rollback threshold:** Same as Stage 2.
**Approval authority:** Product owner.

### Stage 4 — General availability

**Cohort:** All tenants.
**Entry criteria:** Stage 3 exit criteria met + monitoring signals stable + no open Blockers/Criticals.
**Enabled flags:** All eight.
**Owner:** Release manager.
**Approval authority:** Product owner + clinical platform lead.

## Monitoring signals (approved, PHI-safe)

See `CLINICAL_MONITORING_PLAN.md`. No PHI in any signal.

## Rollout stop conditions

**Approved thresholds:** `/app/memory/CLINICAL_PERFORMANCE_THRESHOLDS.md` (per combination row). **Promotion process:** `/app/memory/CLINICAL_PERFORMANCE_THRESHOLD_PROMOTION.md`. The same approved numbers govern both G2 release qualification and rollout stop conditions — do not maintain a second copy in this document.

Trigger immediate rollback (see `CLINICAL_ROLLBACK_RUNBOOK.md`) on any of:
- Blank Clinical page (verified reproducible for ≥ 3 users OR ≥ 2 tenants).
- Permission leakage (any user sees data they should not).
- Masking failure (unmasked PHI on a masked-role session).
- Cross-tenant data exposure (any).
- Signed-record mutation (any).
- Audit-log emission failure sustained > 5 min.
- Critical navigation failure (Clinical tab unreachable from Patient Detail).
- Sustained elevated backend error rate above approved threshold (see approved combination row in `CLINICAL_PERFORMANCE_THRESHOLDS.md`).
- Severe performance regression: P95 exceeds the approved rollback trigger for the sustain window in the matching combination row of `CLINICAL_PERFORMANCE_THRESHOLDS.md`. Never apply a threshold across profiles / network / datasets it wasn't measured for.
- Preference corruption (durable `ClinicalUIDefaults` rejected on read for > 1% of users).
- Unrecoverable partial-failure loop (SectionErrorBoundary retries fail indefinitely).
- Repeated section-boundary crashes for the same user > 5 within one session.
- Billing workflow regression (e.g., encounters no longer route to Ready).

**Numerical thresholds pending approval.** Once approved, they live in `CLINICAL_PERFORMANCE_THRESHOLDS.md` (single source of truth) and are referenced — never duplicated — from `CLINICAL_MONITORING_PLAN.md`, this document, `CLINICAL_ROLLOUT_CHECKLIST.md`, and `CLINICAL_GA_READINESS.md`.

## Change freeze during rollout

- Accept only verified defects.
- No features. No new modules. No new roles. No new telemetry categories.
- No default changes (workspace-mode defaults, encounter filter defaults, etc.) without rollout-owner approval.
- Legacy fallback must remain mounted throughout the rollout.

## Communication plan

| Stage | Channel | Cadence |
|---|---|---|
| Stage 1 | Slack `#clinical-redesign-internal` | Daily standup |
| Stage 2 | Slack + weekly stakeholder email | Weekly |
| Stage 3 | Weekly stakeholder email | Weekly |
| Stage 4 | Public release notes (see `CLINICAL_RELEASE_NOTES.md`) | Once at GA |

## Rollback ownership

Same as `CLINICAL_ROLLBACK_RUNBOOK.md`. Approved rollback authority: clinical platform lead OR platform reliability lead.

## Success metrics (post-GA review)

- Zero Blockers open in the 30 days after GA.
- Pilot satisfaction ≥ 8/10.
- Preference-save failure rate < 0.1%.
- Section-error-boundary activation rate < 0.05% of Clinical page loads.
- No PHI-related incident referencing the Clinical redesign.
