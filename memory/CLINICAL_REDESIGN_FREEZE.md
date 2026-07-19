# Patient Profile > Clinical Redesign — FREEZE

**Freeze date:** 2026-02-15
**Status:** Frozen. Changes accepted only for verified defects. New ideas → separately scoped follow-ups.

## What was frozen

- **Phase 1** — Shell, sticky patient context header, section nav, current-care-status, summary tiles, more-actions menu, missing-value vocabulary, compact empty states, billing-readiness aggregate, PHI-safe telemetry, legacy fallback.
- **Phase 2 Wave A + B** — ActiveEpisodeCard, grouped encounters/timeline, StatusBadge, progressive intake, safety summary, re-exam section, structured diagnosis rows, treatment plan progress, billing aggregation.
- **Phase 3 Slice 3** — Outcome snapshot, trend, table, deterministic suggestions.
- **Phase 3 Slice 4** — Imaging metadata card + data-quality panel (re-integrated).
- **Phase 3 Slice 5** — Role-aware workspace modes, configurable summary rail (Move up/down, no drag-and-drop), durable non-patient preferences (`extra=forbid` schema).
- **Phase 3 Slice 6** — Section-level error boundaries (Imaging / Outcomes / Timeline), telemetry PHI-probe tests, aria-live workspace-mode announcement + persistent description, 5-level surface tokens, flag-matrix registry test.

## Feature-flag ownership

All flags default **on**. Env overrides live in `frontend/.env` and are individually rollback-safe.

| Flag | Env var | Owner |
|------|---------|-------|
| `clinicalRedesign` | `REACT_APP_CLINICAL_REDESIGN` | Clinical platform lead |
| `clinicalRedesignPhase2WaveA` | `REACT_APP_CLINICAL_REDESIGN_PHASE2_WAVE_A` | Clinical platform lead |
| `clinicalRedesignPhase2WaveB` | `REACT_APP_CLINICAL_REDESIGN_PHASE2_WAVE_B` | Clinical platform lead |
| `clinicalRedesignPhase3` | `REACT_APP_CLINICAL_REDESIGN_PHASE3` | Clinical platform lead |
| `clinicalRedesignPhase3Slice3` | `REACT_APP_CLINICAL_REDESIGN_PHASE3_SLICE3` | Outcomes team |
| `clinicalRedesignPhase3Slice4` | `REACT_APP_CLINICAL_REDESIGN_PHASE3_SLICE4` | Imaging + data-quality team |
| `clinicalRedesignPhase3Slice5` | `REACT_APP_CLINICAL_REDESIGN_PHASE3_SLICE5` | Workspace / preferences team |
| `clinicalRedesignPhase3Slice6` | `REACT_APP_CLINICAL_REDESIGN_PHASE3_SLICE6` | Platform reliability |

**Rollback contract:** parent-off disables every descendant regardless of stored child preferences; stored child preferences persist but are effectively off. Legacy `ClinicalTab` + `MediaCard` fallbacks remain mounted at all times.

## Release gates — closeout status (updated 2026-02-15 fork agent)

See `/app/memory/CLINICAL_RELEASE_GATE_STATUS.md` for the definitive per-gate status.

| Gate | Status | Evidence |
|:-:|---|---|
| G1 | READY FOR CLINICAL AND OPERATIONS SIGN-OFF | `PHASE3_UAT_SIGNOFF.md`, `PHASE3_UAT_EVIDENCE_INDEX.md`, `PHASE3_UAT_DEFECTS.md` |
| G2 | COMPLETE — MEASURED, BUDGET APPROVAL REQUIRED | `PHASE3_PERFORMANCE_REPORT.md`, `PHASE3_PERFORMANCE_RAW_RESULTS.json`, `PHASE3_PERFORMANCE_TEST_PLAN.md` |
| G3 | READY FOR PRODUCTION WALK-THROUGH | `CLINICAL_ROLLBACK_RUNBOOK.md`, `CLINICAL_ROLLBACK_MATRIX.md`, `CLINICAL_ROLLBACK_REHEARSAL.md` |
| G4 | COMPLETE | `CLINICAL_CONTRACT_FREEZE.md`, `CLINICAL_CONTRACT_REGISTRY.json`, `CLINICAL_CONTRACT_CHANGE_POLICY.md` |
| G5 | READY FOR SCREENSHOT CAPTURE | `CLINICAL_RELEASE_NOTES.md`, `CLINICAL_RELEASE_SCREENSHOT_INDEX.md`, `CLINICAL_SUPPORT_BRIEF.md`, `CLINICAL_KNOWN_LIMITATIONS.md` |
| G6 | READY FOR AUTHORIZED STAGED ROLLOUT | `CLINICAL_STAGED_ROLLOUT_PLAN.md`, `CLINICAL_ROLLOUT_CHECKLIST.md`, `CLINICAL_MONITORING_PLAN.md`, `CLINICAL_INCIDENT_RUNBOOK.md`, `CLINICAL_PILOT_FEEDBACK_FORM.md`, `CLINICAL_GA_READINESS.md` |

Automated verification (2026-02-15): frontend **117/117** pass, backend clinical **152/152** pass. See `release_evidence/AUTOMATED_TEST_RESULTS.md`.

## Original release-gate list (verbatim from freeze 2026-02-15)

Each gate is owned by a named team and must be signed-off before the redesign leaves staging.

- [ ] **G1 — 50-scenario stakeholder UAT.** Walk `/app/memory/PHASE3_UAT.md` with clinical + operations leads for each of the 50 scenarios. Capture signatures at the footer. **Owner:** Clinical operations.
- [ ] **G2 — P50 / P95 performance measurement** on representative charts, including at least one with **≥ 200 timeline events**. Record initial Clinical render, grouped-encounter load, timeline load + filter-apply, outcome chart render, and return-state restoration. **Owner:** Platform reliability.
- [ ] **G3 — Production rollback procedure verified.** Exercise:
  - Parent redesign off (returns to legacy `ClinicalTab`).
  - Phase 3 off with children on (children must remain effectively off).
  - Each slice individually off (siblings must remain functional).
  - Env-default on with user override off (user opt-out must win).
  Document the exact sequence in the ops runbook. **Owner:** Clinical platform lead + Platform reliability.
- [ ] **G4 — Contract freeze.** Confirm the following are unchanged since 2026-02-15 and version-tagged:
  - `services/identity/models.py::ClinicalUIDefaults` (Slice 5 fields + `extra=forbid`).
  - `services/telemetry/router.py::UIEventPayload` allow-list.
  - `frontend/src/utils/featureFlags.js` flag registry + parent chain.
  - `frontend/src/pages/clinical/workspaceModes.js` mode/section/summary registry.
  **Owner:** Clinical platform lead.
- [ ] **G5 — Screenshots & release notes.** Capture one screenshot per workspace mode (general, provider, front_desk, billing, administrator) and attach to release notes. **Owner:** Clinical platform lead.
- [ ] **G6 — Staged rollout.** Promote through the normal staged-release process (internal → pilot clinic → GA). **Owner:** Release manager.

## Deliverables package

1. Executive summary → `CHANGELOG.md` (2026-02-15 entries × 3).
2. File-change summary → `git diff --stat` at the freeze commit.
3. New / modified components + endpoints → `CHANGELOG.md`.
4. Preference schema changes → `test_preferences_slice5.py`.
5. Telemetry contract → `services/telemetry/SCHEMA.md` + `test_telemetry_phi_probe.py`.
6. Feature-flag hierarchy → above.
7. Test results → 117/117 frontend clinical unit tests + 89 targeted backend tests.
8. UAT scenarios → `PHASE3_UAT.md` (50 scenarios).
9. Accessibility findings → aria-live workspace-mode region, skip-to-summary link, sentence-case labels, `min-h-11` targets, `SectionErrorBoundary` with accessible retry.
10. Performance measurements → **pending G2**.
11. Rollback verification → **pending G3**.
12. Screenshots per role → **pending G5**.
13. Known limitations → below.
14. Deferred backlog → below.
15. **Final statement:** No permissions, masking, audit, signed-record, or tenant-isolation behaviour was weakened. `extra=forbid` on `ClinicalUIDefaults`, PHI-probe on `UIEventPayload`, and per-section permission gates continue to enforce the pre-redesign contract.

## Known limitations (frozen — do not extend before G1–G6 pass)

- Flag-matrix test verifies registry contract, not full render. Full-render coverage is deferred to the browser-based UAT step.
- Performance instrumentation not shipped in-tree; measurements will be captured externally during G2.
- `SectionErrorBoundary` wraps Imaging, Outcomes, Timeline only. Other Clinical sections rely on per-card fetch fallbacks.

## Deferred backlog (do NOT re-scope during freeze)

- Diagnosis "Set inactive" — blocked on backend status-model decision.
- Case-type-based outcome suggestion mappings.
- Chart-at-a-glance print sheet.
- My Worklist dashboard.
- Today's Chart Preview widget.
- Billing digest.
- Clinic-wide data-quality aggregate endpoint / ops dashboard.
- Change Healthcare / Optum transport.
- AI cost estimator.
- Admin-facing feature-flag management panel.
- Application-wide theme overhaul.
- First-open workspace-mode onboarding toast (deferred until post-freeze usage data justifies it).
- **`SectionErrorBoundary` reuse around AI Scribe / Billing Ledger** — deliberately not reused; those surfaces have different failure and recovery semantics (recording state, unsaved drafts, financial posting, reconciliation) and need a separately scoped resilience review answering:
  1. Which failures are render-only vs. request / mutation failures?
  2. Whether retry is idempotent (posting a payment twice is unacceptable).
  3. How unsaved state (audio buffer, draft note, edited ledger row) is preserved across a boundary reset.
  4. Whether permission-denied, timeout, and partial-data states need distinct handling.
  5. Whether error events remain PHI-safe when the surface owns richer clinical text.
  6. Whether the boundary should reset automatically after route or record changes.
  Do not import `SectionErrorBoundary` into either surface before answering these questions.

## Change-control rule

From 2026-02-15 forward:
- **Verified defects** — accepted; open a ticket referencing this freeze document.
- **New features / new modules / role additions / new preferences / new telemetry events** — belong in a separately scoped follow-up. Do not commingle with defect fixes.
- **Flag renames or restructures** — require sign-off from every flag owner listed above.

_"Frozen except for verified defects" — the redesign has earned the right not to be improved further this quarter._
