# Handoff — Complete the Clinical Redesign (Items 6–10)

**Purpose:** Items 1–5 from the "Complete the Patient Profile > Clinical UX/UI redesign end-to-end" plan shipped on 2026-02-15. This file hands off items 6–10 to the next session with everything a fresh agent needs.

**Current baseline (do not regress):**
- Feature-flag hierarchy: `clinicalRedesign > (Phase2WaveA | Phase2WaveB | Phase3 > (Slice3 | Slice4 | Slice5))` — every child rolls back independently; parent-off disables descendants; legacy `ClinicalTab` remains available.
- 109/109 frontend clinical unit tests pass.
- 45/45 backend `test_preferences_slice5.py` tests pass.
- 54/54 backend Slice 5 + grouped-timeline-filters tests pass.
- ESLint clean on every file touched.
- `PRD.md`, `CHANGELOG.md`, `ROADMAP.md` all reflect Slice 4/5 as Done.

---

## Item 6 — Slice 6 hardening (three sub-slices)

**Flag:** add `clinicalRedesignPhase3Slice6` behind env var `REACT_APP_CLINICAL_REDESIGN_PHASE3_SLICE6`, default **on**, parent `clinicalRedesignPhase3`. Register in `frontend/src/utils/featureFlags.js` `FLAG_DEFAULTS`, `ENV_VAR_MAP`, `FLAG_PARENTS`.

### 6A — Telemetry hardening
- Audit every existing clinical telemetry call site (`grep -rn "trackUiEvent\|trackCareStatusAction" /app/frontend/src/pages/clinical/`).
- Enforce an **allow-list** at both the frontend helper and the backend `services/telemetry/router.py`. Reject unknown event names / extra fields at 422.
- Distinguish `_attempted` from `_completed` events (e.g. `clinical.next_action.attempted` vs `clinical.next_action.completed`).
- Add rate-limit / dedupe for noisy events (filter-change, scroll).
- **PHI probe tests** (`backend/tests/test_telemetry_phi_probe.py`): fuzz every event with fields like `patient_id`, `encounter_id`, `note_id`, `diagnosis_code`, `date_of_service`, `name`, `email`, `search_text`, `free_text`, `provider_name`, `episode_name`, `record_id`, `outcome_score`, URLs — assert each is rejected. Also assert tenant-isolation guard.
- Document the final contract in `/app/memory/CLINICAL_TELEMETRY_SCHEMA.md`.

### 6B — Section-level error boundaries + partial-failure UI
- Wrap each major section in `ClinicalTabV2` (Care Status, Next Actions, Active Episode, History, Diagnoses, Encounters, Care Plan, Re-exams, Timeline, Imaging, Outcomes, Data Quality) with a `<SectionErrorBoundary>` that:
  - Isolates crashes (does not blank the page).
  - Shows "Section unavailable — Retry" copy with a `data-testid="section-error-<slug>"`.
  - Distinguishes 403 (permission-denied — muted copy, no retry) from 5xx (error — retry).
  - Never converts failed loads into `0` counts (see existing pattern in `CurrentCareStatusPanel` for billing).
  - Cancels stale requests via `AbortController` on unmount.
- Add tests: mock each API to 403 / 500 / timeout and assert other sections still render.

### 6C — Accessibility hardening (WCAG 2.2 AA)
- Fix any remaining sticky-header hash offset (already `scroll-mt-40` — verify with browser zoom @200%).
- Ensure focus restoration after `Dialog`/`DialogContent` close (use shadcn `Dialog` — already restores). Add tests.
- Add `aria-live="polite"` regions for filter counts and Next Actions refresh.
- Verify heading hierarchy: exactly one h1 per patient page (page title), h2 for section headings, h3 for object titles. Currently `h2` `Clinical summary` + h3 in `CurrentCareStatusPanel`.
- Verify all icon-only buttons have `aria-label` (Move up/down in `SummaryConfigDrawer` — done).
- Contrast audit: run axe-core against a rendered ClinicalTabV2 with the `screenshot-tool` and confirm no critical violations.

### 6D — Performance hardening (measure first)
- Add `performance.mark`/`performance.measure` for initial Clinical render, grouped encounter fetch, timeline fetch, filter apply, outcome chart render, return-state restore.
- Report P50/P95 in dev console (behind flag).
- Only add virtualization to timeline/encounters if measurements show >800ms on 200+ event history (existing `INITIAL_RENDER_CAP = 100` may already be sufficient).

---

## Item 7 — Visual-system cleanup (5-level surface tokens)

Extend `frontend/src/index.css`:
```css
--surface-nav:       <darkest — app chrome>
--surface-page:      <base background — already --background>
--surface-section:   <one step up — already --card>
--surface-interactive: <hover / click layer>
--surface-selected:  <active / elevated>
```
Apply consistently across `PatientContextHeader`, `SectionNav`, `CurrentCareStatusPanel`, `NextActionsPanel`, `ActiveEpisodeCard`, `IntakeHistoryProgressive`, `DiagnosesCard`, `GroupedEncountersCard`, `TreatmentPlansCard`, `ReExamSection`, `GroupedTimelineCard`, `ImagingCard`, `MediaCard`, `OutcomesSection`, `DataQualityPanel`, dialogs & drawers. Keep the existing dark theme. Do **not** perform an app-wide theme rewrite.

---

## Item 8 — Final feature-flag matrix verification

Write `frontend/src/pages/clinical/ClinicalTabV2.flagMatrix.test.jsx`:
- Iterate through the Cartesian product of on/off for each of the 8 flags.
- Assert `ClinicalTabV2` mounts without throwing.
- Assert legacy `ClinicalTab` mounts when `clinicalRedesign=off`.
- Assert no `undefined` component references leak into the tree.

---

## Item 9 — UAT matrix

Create `/app/memory/PHASE3_UAT.md` covering the 50 scenarios listed in the spec (masked patient, no active episode, provider role, etc.). For each scenario: expected sections, expected CTAs, permitted actions, expected empty/error states. Include screenshots (one per role: general, provider, front_desk, billing, administrator).

---

## Item 10 — Final deliverables package

Provide:
1. Executive summary.
2. File-change summary (`git diff --stat`).
3. New / modified components + endpoints.
4. Preference schema changes (already captured in `test_preferences_slice5.py`).
5. Telemetry contract changes (from item 6A).
6. Feature-flag hierarchy diagram.
7. Test results (frontend + backend).
8. UAT results.
9. Accessibility findings.
10. Performance measurements.
11. Rollback verification matrix.
12. Screenshots for the five workspace-mode variants.
13. Known limitations.
14. Deferred backlog (Diagnosis "Set inactive", case-type outcome mapping, chart print sheet, worklist widgets, clinic-wide dashboards, AI cost estimator).
15. Final statement confirming no weakening of permissions/masking/audit/signed-record/tenant-isolation behaviour.

---

## Constraints (do not violate)

- **Do NOT** alias diagnosis inactive → resolved. Backend status model still only supports active + resolved.
- **Do NOT** extend outcome suggestions with case-type mappings.
- **Do NOT** build clinic-wide dashboards, print sheets, or the admin-facing feature-flag panel.
- **Do NOT** store any patient-scoped field in `/me/preferences`. The `extra=forbid` test matrix in `test_preferences_slice5.py` already covers the primary attack surface.
- **Do NOT** load any full note body in the summary rail.
- Preserve all Phase 1/2/3 (Slices 1–5) behaviour.
