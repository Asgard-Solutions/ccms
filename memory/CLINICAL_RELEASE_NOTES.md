# Clinical Redesign — Release Notes

**Version:** 2026-02-15 freeze build.
**Applies to:** Clinical redesign covering Phase 1 + Phase 2 (Waves A/B) + Phase 3 (Slices 1–6).

---

## For clinical users

**What changed on the Clinical tab**

- New sticky patient orientation strip at the top of every chart shows name/initials, active episode, primary diagnosis, provider, next appointment, re-exam due, and red-flag alerts. Mask-aware.
- New sticky section nav with keyboard support and deep-link hashes (Summary, History, Diagnoses, Encounters, Care plan, Timeline, Imaging, Outcomes).
- **Current Care Status** panel lists everything needing attention in one place: episode gaps, unsigned notes, missing intake, billing warnings, safety red flags, upcoming/overdue re-exam.
- **Next Actions** panel surfaces up to nine deterministic recommendations. Mandatory rules cannot be dismissed; optional rules can.
- **Grouped encounters** replace the flat list — each row bundles the appointment, the encounter, its note, and its billing readiness with a single status pill per dimension.
- **Care timeline** now groups visit-linked artefacts into one event, offers filters (event kind, source, provider, date window), and can save named presets.
- **Outcome snapshot + trend** — neutral, non-interpretive summary per instrument (NDI, Oswestry, VAS, functional index, Bournemouth neck). Trend chart uses shape encoding so it's legible without color; a data-table equivalent is always available.
- **Imaging metadata card** highlights modality + date; missing classification flagged as a data-quality issue you can resolve inline.
- **Data Quality panel** lists patient-scoped chart issues (missing primary diagnosis, unsigned notes older than 7 days, missing note on an encounter, etc.) with a Resolve button that jumps to the section that owns the fix.
- **Workspace modes** (general / provider / front desk / billing / administrator) reorder the page for how you actually work. Your default is remembered across sessions.
- **Section-level error boundaries** — if one section can't load, the rest of the chart still works.

**What is unchanged**

- Permissions, masking, audit trail, signed-record immutability, and tenant isolation behave exactly as they did before the redesign.
- No new access. No new PHI captured. No new preferences that hold patient data.

## For operations

**What changed**

- Preferences (`/api/auth/me/preferences`) accept a new `clinical_ui_defaults` block for durable UI preferences (workspace mode, summary module order, encounter filter default, outcome view default, collapsed modules, timeline presets). `extra=forbid` prevents any patient identifier from being stored.
- New telemetry endpoint `POST /api/telemetry/ui-action` records care-status CTA, next-action, and outcome-suggestion **attempts** only. No PHI. `extra=forbid`.

**What operations needs to know**

- Feature-flag hierarchy allows granular rollback (see `CLINICAL_ROLLBACK_RUNBOOK.md`).
- Legacy `ClinicalTab` remains mounted; rolling back the parent flag reverts a user to the pre-redesign layout instantly.
- Preferences service outage does not break the Clinical page — an optimistic rollback + toast is shown, and the user's session-scoped work continues.

## For billing users

- Billing status is now surfaced on the Clinical tab **and** in the Grouped Encounters card as a distinct "Billing" dimension per encounter.
- A chart-wide **billing-readiness aggregate** row on Current Care Status summarises the warning + blocked counts (rows are only shown for roles that can see billing).
- The Care Status "Review billing issues" CTA jumps directly to Billing.

## For administrators

- New `administrator` workspace mode prioritises Data Quality and audit-sensitive actions.
- All feature flags default `on`; env-var overrides + per-user localStorage overrides supported. See runbook.

## For support

**What changed for triage**

- New telemetry `POST /telemetry/ui-event` records `clinical.section.load_failed` when any Clinical section fetch errors. Use this to reconstruct partial-failure incidents without touching PHI.
- Section-error-boundary text ("Section unavailable — Retry") is a distinct signature; if a user reports it, ask which section slug is affected.
- If a user reports the Clinical page looks "different" than expected, ask them to check the workspace mode indicator in the summary rail — they may have switched modes.

**Known-issue decision tree** — see `CLINICAL_SUPPORT_BRIEF.md`.

## For engineering

- Feature-flag registry frozen (see `CLINICAL_CONTRACT_FREEZE.md`).
- Every telemetry and preference contract is `extra=forbid`.
- Frontend clinical Jest: 117/117 pass. Backend clinical contract Pytest: 152/152 pass.
- No changes to permissions, masking, audit-log emission, or signed-record semantics.
- Section boundaries wrap Imaging, Outcomes, Timeline. Other sections retain per-card fallbacks — do not extend the boundary layer without a separately scoped resilience review.

## For security and compliance

- No PHI in telemetry (probe-tested).
- No PHI in durable preferences (probe-tested).
- Route-instance tokens for return state are opaque; never derived from patient/record IDs.
- Session store TTL 30 minutes; cleared on logout + permission change.
- Cross-tenant isolation unchanged.
- `extra=forbid` on every new contract; unknown keys → 422.
- Preference-service failures never mutate durable state on the server.

## Feature-flag rollout

All flags default `on`. Every flag has an env-var default and a per-user localStorage override. Rollback:
- Any single slice → set its env var to `off` + rebuild + redeploy.
- Entire redesign → set `REACT_APP_CLINICAL_REDESIGN=off`.
- Per-user override → `localStorage.setItem('ccms.flags.<key>','off')` in the affected browser.

## Rollback availability

Confirmed. See `CLINICAL_ROLLBACK_RUNBOOK.md`.

## Known limitations

- No first-open workspace-mode onboarding toast — users discover the switcher on their own.
- Large-chart performance (200+ timeline events) has not yet been measured on production-shape data; re-measure during pilot.
- Preview environment watermark can occlude the back-to-top button in Playwright; unaffected in production.

## Deferred features (not shipping in this release)

- Diagnosis "Set inactive" state.
- Case-type-based outcome suggestion mappings.
- Chart-at-a-glance print sheet.
- My Worklist dashboard widget.
- Today's Chart Preview dashboard widget.
- Billing digest.
- Clinic-wide data-quality aggregate endpoint / ops dashboard.
- Change Healthcare / Optum production transport.
- AI cost estimator on the AI models page.
- Admin-facing feature-flag management panel.
- Application-wide theme overhaul.
- Extension of `SectionErrorBoundary` around AI Scribe / Billing Ledger.

## Support and escalation

For any issue mentioning **blank Clinical page**, **section unavailable — retry**, **workspace mode reset**, or **preferences not saving**, follow `CLINICAL_SUPPORT_BRIEF.md`.

For any issue involving suspected data mutation or PHI leakage, escalate immediately to platform reliability + compliance and reference this release version.

## No-change statement

This release does **not** modify any of the following:

- Role-based access permissions.
- PHI masking behavior on the Patient list, Patient detail, or Portal surfaces.
- Audit-log emission on any PHI-touching action.
- Signed-record immutability rules.
- Tenant isolation guarantees.

No feature described in these notes claims to prevent errors, guarantee compliance, or eliminate risk.

## Release version + hash

Frozen commit: see `git log --grep 'freeze' /app/memory/CLINICAL_REDESIGN_FREEZE.md`.
Test evidence: `/app/memory/release_evidence/AUTOMATED_TEST_RESULTS.md`.
