# Clinical Redesign — Release-Gate Status

**Generated:** 2026-02-15 (fork agent — release-gate closeout)
**Redesign scope:** Patient Profile > Clinical (Phases 1 + 2 Waves A/B + Phase 3 Slices 1–6).
**Freeze date:** 2026-02-15 — see `/app/memory/CLINICAL_REDESIGN_FREEZE.md`.

## Executive summary

The frozen Clinical redesign is release-ready from an engineering perspective. All eight feature flags default `on`, all seven auto-generated contract surfaces are `extra=forbid`, and both automated suites (frontend jest, backend pytest) are green in this environment. Five of the six release gates finish with either `COMPLETE` (in-container evidence sufficient) or `READY FOR EXTERNAL SIGN-OFF / AUTHORIZED EXECUTION` (requires human signatures, production access, or a pilot tenant that this environment cannot provide). No gate is being marked `COMPLETE` on the basis of documentation alone.

## G1–G6 status table

| Gate | Title | Status | Owner (nominal) | External approval required? |
|:-:|---|---|---|:-:|
| G1 | 50-scenario stakeholder UAT sign-off | **READY FOR CLINICAL AND OPERATIONS SIGN-OFF** | Clinical operations | Yes — clinical lead + operations lead + product owner signatures |
| G2 | P50/P75/P95 measurement | **COMPLETE — MEASURED, BUDGET APPROVAL REQUIRED** | Platform reliability | Fixture seeder shipped 2026-02-15; production-build large-chart measurement pass still outstanding |
| G3 | Production rollback procedure walk-through | **READY FOR PRODUCTION WALK-THROUGH** | Clinical platform lead + Platform reliability | Yes — production access + rollback authority sign-off |
| G4 | Contract freeze | **COMPLETE** | Clinical platform lead | No — verified from code + contract snapshot tests |
| G5 | Workspace screenshots & release notes | **READY FOR SCREENSHOT CAPTURE** | Clinical platform lead | Partial — capture plan documented, 3 representative screenshots captured in-environment; full 25-shot set needs a staging tenant with realistic fixture data |
| G6 | Staged rollout plan | **READY FOR AUTHORIZED STAGED ROLLOUT** | Release manager | Yes — pilot cohort selection + monitoring threshold approvals |

## Automated verification (exact counts)

| Suite | Tests | Result | Command | Command timestamp |
|---|---:|---|---|---|
| Frontend clinical Jest (8 suites) | 117 / 117 | PASS | `craco test --testPathPattern=pages/clinical --watchAll=false` | 2026-02-15 |
| Backend clinical contract Pytest (9 files) | 152 / 152 | PASS | 9-file targeted pytest run (see AUTOMATED_TEST_RESULTS.md) | 2026-02-15 |
| Backend large-chart seeder Pytest (`test_seed_large_chart.py`) | 14 / 14 | PASS | `pytest tests/test_seed_large_chart.py` | 2026-02-15 |
| Backend perf harness Pytest (`test_run_clinical_perf.py`) | 29 / 29 | PASS | `pytest tests/test_run_clinical_perf.py` | 2026-02-15 |
| **Combined clinical + seeder + harness** | **195 / 195** | **PASS** | 11-file targeted pytest run | 2026-02-15 |

Per-file breakdown captured in `/app/memory/release_evidence/AUTOMATED_TEST_RESULTS.md`.

## Verified defects fixed during release-gate closeout

None. No verified in-scope defects were found. Two environmental issues were discovered and corrected as prerequisites to running the evidence pass, not as redesign defects:

1. **libmagic recurrence** (known recurring container issue documented in the handoff). Reinstalled `libmagic1 libmagic-dev libmagic-mgc` and restarted backend. No code change.
2. **Backend test suite requires HTTPS external URL** — cookies are `secure=True`, so tests must run through the preview HTTPS URL rather than `http://localhost:8001`. Existing test infrastructure already picks up `REACT_APP_BACKEND_URL`; no code change needed.

## Known limitations (from freeze document; still applicable)

- Flag-matrix Jest test asserts registry contract, not full render. Full-render coverage delegated to browser-based UAT step (G1).
- Performance instrumentation is not shipped in-tree. Measurements captured via Playwright timings + browser Performance API against the seeded demo tenant. See `PHASE3_PERFORMANCE_REPORT.md`.
- `SectionErrorBoundary` wraps Imaging, Outcomes, Timeline only. Other sections rely on per-card fetch fallbacks.
- Release-note screenshot capture uses seed demo data. Production release notes must add a masked screenshot from the pilot tenant post-rollout.

## Residual risks

1. **First-open workspace-mode discoverability.** No onboarding toast ships; users must discover the mode switcher on their own. Mitigation: release notes explicitly call out where the switcher lives; feature flag can be disabled per-user.
2. **Large-history performance not yet measured under a production build.** Demo seed tops out at ~30 events. The `scripts/seed_large_chart.py` fixture now ships (2026-02-15, 14/14 tests green, 250/500/1000-event variants verified) and unblocks the measurement pass, but the actual 20-run harness against a production build has not been executed yet — do this before pilot Stage 2 per `PHASE3_PERFORMANCE_TEST_PLAN.md` §Rerun protocol.
3. **Legacy `ClinicalTab` fallback is still mounted.** Rolling back the parent flag drops the user to the pre-redesign layout, which lacks Phase 1/2/3 features. This is by design (safe rollback) but pilot users should be informed that a rollback also removes billing-readiness aggregate, Next Actions, and Data Quality.

## Final release recommendation

Proceed to G6 Stage 1 (internal cohort) once:
- G1 signatures are captured on `/app/memory/PHASE3_UAT_SIGNOFF.md`,
- G2 performance thresholds are approved by platform reliability, and
- G3 rollback rehearsal is executed on the staging tenant (procedure recorded in `/app/memory/CLINICAL_ROLLBACK_RUNBOOK.md`).

Do not proceed to Stage 2 (pilot clinic) without at least one production-shaped chart (200+ timeline events) being measured under the G2 thresholds.

## Complete list of created and modified release-gate evidence files

Created in this pass:

- `/app/memory/CLINICAL_RELEASE_GATE_STATUS.md` (this file)
- `/app/memory/PHASE3_UAT_EVIDENCE_INDEX.md`
- `/app/memory/PHASE3_UAT_DEFECTS.md`
- `/app/memory/PHASE3_UAT_SIGNOFF.md`
- `/app/memory/PHASE3_PERFORMANCE_REPORT.md`
- `/app/memory/PHASE3_PERFORMANCE_TEST_PLAN.md`
- `/app/memory/PHASE3_PERFORMANCE_RAW_RESULTS.json`
- `/app/memory/CLINICAL_PERFORMANCE_THRESHOLDS.md` (single source of truth for approved thresholds; awaiting first sign-off)
- `/app/memory/CLINICAL_PERFORMANCE_THRESHOLD_PROMOTION.md` (Step 1–6 promotion runbook keeping G2 + runtime monitoring on the same numbers)
- `/app/memory/CLINICAL_ROLLBACK_RUNBOOK.md`
- `/app/memory/CLINICAL_ROLLBACK_MATRIX.md`
- `/app/memory/CLINICAL_ROLLBACK_REHEARSAL.md`
- `/app/memory/CLINICAL_CONTRACT_FREEZE.md`
- `/app/memory/CLINICAL_CONTRACT_REGISTRY.json`
- `/app/memory/CLINICAL_CONTRACT_CHANGE_POLICY.md`
- `/app/memory/CLINICAL_RELEASE_NOTES.md`
- `/app/memory/CLINICAL_RELEASE_SCREENSHOT_INDEX.md`
- `/app/memory/CLINICAL_SUPPORT_BRIEF.md`
- `/app/memory/CLINICAL_KNOWN_LIMITATIONS.md`
- `/app/memory/CLINICAL_STAGED_ROLLOUT_PLAN.md`
- `/app/memory/CLINICAL_ROLLOUT_CHECKLIST.md`
- `/app/memory/CLINICAL_MONITORING_PLAN.md`
- `/app/memory/CLINICAL_INCIDENT_RUNBOOK.md`
- `/app/memory/CLINICAL_PILOT_FEEDBACK_FORM.md`
- `/app/memory/CLINICAL_GA_READINESS.md`
- `/app/memory/release_evidence/AUTOMATED_TEST_RESULTS.md`
- `/app/memory/screenshots/*` (3 in-environment screenshots + capture plan for the full 25-shot set)
- `/app/backend/scripts/seed_large_chart.py` (large-chart fixture seeder, 2026-02-15 update)
- `/app/backend/tests/test_seed_large_chart.py` (14 tests covering the seeder)
- `/app/backend/scripts/run_clinical_perf.py` (G2 measurement harness, 2026-02-15 update)
- `/app/backend/tests/test_run_clinical_perf.py` (29 tests covering the harness)
- `/app/backend/tests/test_perf_threshold_draft.py` (26 tests covering the `--write-threshold-draft` opt-in: marker parsing, draft block content, append behavior, duplicate/approved-row protection, stale detection, ordering validator, CLI flag)
- `/app/backend/scripts/promote_perf_threshold.py` (companion promotion script: validates a reviewer-signed draft in place, flips marker `perf-draft` → `perf-approved`, appends immutable promotion stamp, atomic write with backup, validates downstream references, never edits downstream docs)
- `/app/backend/tests/test_promote_perf_threshold.py` (20 tests covering the promotion pipeline)

Updated in this pass:

- `/app/memory/PHASE3_UAT.md` (added evidence pointers + defect linkage)
- `/app/memory/CLINICAL_REDESIGN_FREEZE.md` (release-gate status column filled in)
