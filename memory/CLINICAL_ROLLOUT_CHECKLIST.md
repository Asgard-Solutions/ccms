# Clinical Rollout Checklist

**Purpose:** Concrete, dated checklist that the release manager marks off during rollout. Nothing on this list is speculative — every item corresponds to a required artifact or approval that this release-gate pass has already prepared.

## Stage 0 — Engineering validation

- [x] Frontend Jest clinical suite green — 117/117
- [x] Backend Pytest clinical suite green — 152/152
- [x] Feature-flag registry frozen — see `CLINICAL_CONTRACT_FREEZE.md`
- [x] Contract-freeze evidence generated — `CLINICAL_CONTRACT_FREEZE.md`, `CLINICAL_CONTRACT_REGISTRY.json`
- [x] Rollback rehearsal in preview — `CLINICAL_ROLLBACK_REHEARSAL.md`
- [x] Support brief ready — `CLINICAL_SUPPORT_BRIEF.md`
- [x] Release notes drafted — `CLINICAL_RELEASE_NOTES.md`
- [x] Monitoring plan documented — `CLINICAL_MONITORING_PLAN.md`
- [ ] Performance thresholds approved by platform reliability
- [ ] Production rollback walk-through executed
- [ ] Stage-1 cohort selected

## Stage 1 — Internal users

- [ ] G1 UAT signatures captured on `PHASE3_UAT_SIGNOFF.md`
- [ ] G2 performance thresholds approved
- [ ] G3 production walk-through executed + signed
- [ ] Internal Slack channel created + support contacts posted
- [ ] Kickoff email sent
- [ ] Rollback tested from production settings (dry-run, no user impact)
- [ ] Daily standup scheduled for the stage
- [ ] Monitoring dashboards live + reviewed
- [ ] 5 business days of clean signal
- [ ] Internal survey ≥ 8/10
- [ ] Stage-2 pilot clinic selected

## Stage 2 — Pilot clinic

- [ ] Large-chart (200+ event) fixture created + measured under thresholds
- [ ] Pilot clinic contacted + agreed
- [ ] Support brief distributed to pilot clinic
- [ ] Pilot feedback form live — `CLINICAL_PILOT_FEEDBACK_FORM.md`
- [ ] Rollback path re-verified against pilot tenant
- [ ] Weekly stakeholder cadence set
- [ ] 10 business days of clean signal
- [ ] Pilot survey ≥ 7.5/10
- [ ] Billing spot-check clean
- [ ] Stage-3 cohort selected

## Stage 3 — Expanded cohort

- [ ] Stage-2 exit criteria signed off
- [ ] Cohort communication sent
- [ ] Per-tenant disable path re-verified
- [ ] 10 business days of clean signal
- [ ] Stage-4 approval

## Stage 4 — GA

- [ ] Stage-3 exit criteria signed off
- [ ] Product owner approval recorded
- [ ] Public release notes published
- [ ] Support brief distributed to all support teams
- [ ] Monitoring dashboards remain active for 30 days post-GA
- [ ] Retrospective scheduled 30 days post-GA
- [ ] Freeze exit criteria evaluated (may or may not lift the freeze — decision separate)

## Ongoing change control (all stages)

- [ ] Every defect ticket references `CLINICAL_REDESIGN_FREEZE.md`.
- [ ] Every code change references a release-gate + failing test + rollback impact.
- [ ] No feature request executed during rollout.
- [ ] Legacy fallback remains mounted at every stage.
