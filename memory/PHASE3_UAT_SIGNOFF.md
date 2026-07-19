# Phase 3 UAT — Sign-off form

**Purpose:** Capture the final human sign-off required to close G1. This form is signable but not signed — no signatures are fabricated. The evidence package that supports each sign-off row lives in the linked evidence files.

**Redesign scope:** Patient Profile > Clinical, Phases 1 + 2 (Waves A/B) + Phase 3 (Slices 1–6).
**Freeze date:** 2026-02-15.
**Scenario matrix:** `/app/memory/PHASE3_UAT.md` (50 scenarios).

## Result summary (to be filled by testers)

| Category | Count |
|---|---:|
| Pass | ___ / 50 |
| Fail | ___ / 50 |
| Blocked | ___ / 50 |
| Retested and passed after defect fix | ___ |
| Accepted known limitations | ___ |

## Accepted known limitations (pre-declared, do not count against pass rate)

1. **Preview environment watermark** — "Made with Emergent" overlay in the preview environment can occlude clinical-back-to-top clicks in Playwright automation. Documented as environmental in `PHASE1_TEST_DISPOSITION.md`. Manual click succeeds; the watermark does not ship to production tenants.
2. **Full 500+ event chart** — No production-shape patient with 500+ timeline events is present in the demo seed. Large-history behavior is measured on the 250+ synthetic-history run described in `PHASE3_PERFORMANCE_REPORT.md`. Re-verify during pilot with a real chart.
3. **Screenshots use masked demo data** — Every screenshot in the release package uses fictional Riverbend Chiropractic personas. No production PHI is captured.

## Residual-risk statement (must be reviewed by clinical lead + operations lead)

The Clinical redesign is additive UI on top of the pre-redesign backend contracts. Rolling back any single flag returns the affected surface to the immediately prior state:
- Parent flag off → legacy `ClinicalTab` renders in full.
- Any slice flag off → the shell + preserved-fallback card continues to render; the redesign layer is hidden.
No permission, masking, tenant-isolation, audit, signed-record, or file-immutability guarantee is weakened. `extra=forbid` on every telemetry and preference contract prevents accidental PHI leakage through the new surfaces.

## Sign-off table

| Role | Name | Signature | Date |
|---|---|---|---|
| Clinical lead | ______________________ | ______________________ | ______________________ |
| Operations lead | ______________________ | ______________________ | ______________________ |
| Product owner | ______________________ | ______________________ | ______________________ |
| Platform reliability | ______________________ | ______________________ | ______________________ |

## Release recommendation (to be selected by product owner after signatures)

- [ ] APPROVED for internal (Stage 1) rollout.
- [ ] APPROVED for internal + pilot clinic (Stage 1 + Stage 2) rollout.
- [ ] APPROVED for full staged rollout (Stages 1–4).
- [ ] REJECTED — see defect list in `PHASE3_UAT_DEFECTS.md`.
- [ ] DEFERRED — resolve residual risk _____________ first.

## Status

**READY FOR CLINICAL AND OPERATIONS SIGN-OFF.**

No signatures were fabricated by the release-gate closeout pass. This form is prepared, versioned, and stored alongside the automated-test evidence for the human sign-off step.
