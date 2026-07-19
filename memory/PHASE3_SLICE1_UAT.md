# Phase 3 Slice 1 — UAT playbook

Manual acceptance checklist for the Cross-record linking / Deterministic Next Actions surface.

## Pre-flight
- Sign in as `doctor@ccms.app / Doctor@ComplianceClinic1`.
- Confirm `localStorage.getItem('ccms.flags.clinicalRedesignPhase3')` is either unset or `"on"`.

## Panel visibility
- Open a demo patient chart (e.g. Ethan Parker).
- **Expect** a `Next actions` panel above `Active episode`, with either a rule list or the empty-state.

## Rule fidelity
- **Sign unsigned note**: create a draft SOAP note on the chart → panel row appears within 30s.
- **Complete missing documentation**: check the encounters card and count visits without a note; the panel's row shows the same count in its `why`.
- **Attach or link diagnosis to encounter**: resolve the primary diagnosis (Mark resolved) while another draft note is open → row appears; add a new primary → row disappears on next chart refresh.
- **Review billing warning**: manually promote a billing readiness row to warning → row surfaces.
- **Open blocked billing-readiness issue**: block a readiness row → row surfaces with destructive tone. The warning-level row must disappear at the same time.
- **Schedule due or overdue re-exam**: set the plan's `next_reexam_due_date` to yesterday → row surfaces (destructive tone).
- **Schedule remaining planned visits**: with a plan of 10 visits, 3 completed, 2 scheduled → row shows "5 planned visits ... not on the calendar yet."
- **Review missing required intake information**: clear `chief_complaint` on the intake form → row surfaces.
- **Record configured outcome measure**: attach a configured instrument, ensure last outcome is > 14 days old → row surfaces; record a new outcome → row disappears on refresh.

## Dismiss semantics
- Only `schedule-remaining-planned-visits` and `record-configured-outcome-measure` render a dismiss (`×`) button.
- Click Dismiss → row disappears immediately.
- Refresh the page in the same tab → the dismissal is remembered (session scope).
- Logout, log back in → dismissal is cleared (session reset).

## Cross-chart isolation
- On chart A, dismiss the outcome-measure row.
- Navigate directly (in the URL bar) to chart B → chart B does NOT reflect the dismissal.
- Use the browser back button to return to chart A → dismissal persists.

## Feature flag rollback
- Toggle `localStorage.setItem('ccms.flags.clinicalRedesignPhase3', 'off')` in devtools → the panel disappears after reload.
- Reset with `localStorage.removeItem('ccms.flags.clinicalRedesignPhase3')` → panel returns.
- Toggle the parent `clinicalRedesign` off → panel disappears **even if the child flag is on**.

## Telemetry contract
- With devtools network open, click Open on any next-action row → verify a POST to `/api/telemetry/ui-action` with `event_name=clinical_next_action_interaction`, `interaction=opened`, and `action_id=<slug>`; server responds 204.
- Click Dismiss → same endpoint, `interaction=dismissed`; server responds 204.

## Sign-off
- [ ] All rule fires match the underlying data.
- [ ] Dismissals are session-scoped.
- [ ] Cross-chart isolation confirmed.
- [ ] Feature flag rollback confirmed.
- [ ] Telemetry payloads observed (both interactions).
- [ ] No PHI in `history.state.ccms_route_token`, `sessionStorage`, or telemetry payloads.
