# Clinical Rollback Rehearsal

**Purpose:** Executable record of the rollback matrix rehearsal against a running instance. Any scenario that requires production access is marked `READY FOR PRODUCTION WALK-THROUGH` — this rehearsal did **not** touch production settings.

## Rehearsal identity

| Field | Value |
|---|---|
| Date | 2026-02-15 |
| Environment | Preview container (`phi-safe-clinical-ui.preview.emergentagent.com`) |
| Operator | Release-gate closeout agent (fork) |
| Observer | (pending human observer) |
| Data set | Riverbend Chiropractic & Wellness demo seed |

## Scenarios executed in this rehearsal

| # | Scenario | Method | Result | Time to rollback | Time to restore | Evidence |
|:-:|---|---|---|---:|---:|---|
| 1 | All flags on (baseline) | Fresh incognito, no overrides | ✅ Redesign renders | — | — | `screenshots/01_admin_clinical_general.jpg` |
| 2 | Parent `clinicalRedesign` off | `localStorage.setItem('ccms.flags.clinicalRedesign','off'); location.reload();` | ✅ Legacy `ClinicalTab` renders | < 1 s (localStorage), immediate reload | < 1 s + reload | `screenshots/04_legacy_fallback.jpg` |
| 3 | Slice 5 off | `localStorage.setItem('ccms.flags.clinicalRedesignPhase3Slice5','off'); location.reload();` | ✅ Workspace switcher hidden; default NAV_ITEMS order | < 1 s + reload | < 1 s + reload | `screenshots/05_slice5_off.jpg` |
| 4 | Parent off with children on (stored) | Set every child to `on`, then parent to `off` | ✅ Every child effectively off (verified via registry test — parent gate walks first) | — | — | `ClinicalTabV2.flagMatrix.test.js` (jest run 2026-02-15) |
| 5 | Env default overridable | Backend build had `REACT_APP_CLINICAL_REDESIGN_PHASE3_SLICE5=on`; user override off in localStorage | ✅ User override wins → slice effectively off | — | — | Registry test `environment-default-on-is-overridable-by-user-off` |

## Scenarios that require a production walk-through (not executed here)

- Env-var flip in production deploy env vars.
- CDN cache invalidation post-rebuild.
- Multi-region propagation timing.
- Rollback communication to affected tenants.

Marked **READY FOR PRODUCTION WALK-THROUGH.** The runbook (`CLINICAL_ROLLBACK_RUNBOOK.md`) contains the exact commands.

## Findings

- Registry contract tests + per-user localStorage overrides behave as documented. No regression detected.
- Parent-off cascade is instantaneous within a single page load; no residual mounted surface leaked to the DOM tree in the manual walk.
- Legacy fallback (`ClinicalTab`) rendered without errors on the Riverbend demo patient chart.
- No patient data was modified. `updated_at` on the demo patient's encounters is unchanged before/after the rehearsal (spot-checked via `/api/patients/<id>/clinical/encounters/grouped`).
- Legal-hold, masking, and audit surfaces continue to enforce their pre-rollback contracts.

## Defects found

None.

## Approval

| Role | Name | Signature | Date |
|---|---|---|---|
| Release manager (pending) | ______________________ | ______________________ | ______________________ |
| Clinical platform lead (pending) | ______________________ | ______________________ | ______________________ |
| Platform reliability lead (pending) | ______________________ | ______________________ | ______________________ |

## Status

**READY FOR PRODUCTION WALK-THROUGH.**
