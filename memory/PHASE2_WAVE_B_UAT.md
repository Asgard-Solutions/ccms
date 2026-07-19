# Phase 2 Wave B — UAT matrix

Feature flags in scope: `clinicalRedesign` (parent) · `clinicalRedesignPhase2WaveA` · `clinicalRedesignPhase2WaveB`.

## Flag matrix (all combinations verified 2026-07-10 via automated screenshot pass)

| Parent | Wave A | Wave B | Expected Clinical tab |
|--------|--------|--------|----------------------|
| off    | *      | *      | Legacy `ClinicalTab` (Phase 0). No v2 markers rendered. |
| on     | off    | off    | v2 shell only — sticky header, section nav, care status, summary tiles, Episodes list. Legacy encounter + timeline + history cards under. |
| on     | on     | off    | Adds Active Episode card, Grouped Encounters + filters, Grouped Timeline + filters. History section keeps legacy `IntakeHistoryCard`. |
| on     | off    | on     | Adds Safety Summary + Progressive-disclosure Intake + Re-exam banner. Encounters + Timeline keep the legacy cards. |
| on     | on     | on     | Full Wave A + Wave B experience. |

## Scenario matrix (13 required)

| # | Scenario | Patient / setup | Verify |
|---|---|---|---|
| 1 | Linked appointment, encounter, signed note | Any demo patient with a completed visit | Grouped encounter card shows one row with `Completed` + `Note signed` status badges + billing dimension. All source IDs present in the expanded detail. |
| 2 | Appointment without encounter | Demo appointment without a linked encounter row | Grouped encounter row shows `Scheduled` (or matching appt status) + `Note missing`. `Unlinked` badge NOT set (has appointment_id). |
| 3 | Encounter without note | Orphan encounter | Grouped row `Completed` + `Note missing`, `Unlinked` badge if no appointment_id. |
| 4 | Draft note | Encounter with `sign_status=draft` note | Documentation dimension shows `Note draft`. |
| 5 | Amended note | Encounter with signed + amended addendum | Documentation still `Note signed` (amended surfaces on the note badge itself). |
| 6 | Billing warnings | Encounter with warn-severity readiness rows | Billing dimension = `Billing warning`. Care Status → billing row lit (aggregate). |
| 7 | Multiple diagnoses | ≥ 2 diagnoses on the patient | Each row shows ICD-10 + label + Primary badge (if flagged) + Active/Resolved StatusBadge + Clinical / Billing / Problem list classifications + View history / Edit / Mark resolved actions. |
| 8 | Resolved diagnosis | 1+ diagnosis with `status=resolved` | Row shows `Resolved` state badge + Reactivate action. View history shows created + resolved timeline. |
| 9 | Active + closed episodes | Patient with a closed episode | Active Episode card shown for active episode. Closed episodes in the Episodes list are not primary. |
| 10 | Active + completed plans | Multiple plans | TreatmentPlansCard shows each plan with segmented `TreatmentPlanProgress` bar + numeric legend. |
| 11 | Upcoming re-exam | Plan with `next_reexam_due_date` in future | `reexam-approaching` state shown when ≤ 14 days out with warning tone. |
| 12 | Overdue re-exam | Plan with `next_reexam_due_date` in past | `reexam-overdue` state with destructive tone + N days overdue. |
| 13 | Restricted role | Login as `staff@ccms.app` | Billing dimension hidden (403 on aggregate → row omitted). Chart quality still readable. |

## Sign-off

- Automated matrix: PASSED (Playwright).
- Manual UAT: pending clinician walkthrough.

## Known Wave B follow-ups (out of scope)
- Full §4 completion: `Set inactive` action — omitted per user direction; requires new backend status. Documented in `PRD.md § Phase 2 close-out`.
- Full §8 progress swap — **shipped** in this Phase 2 close-out.
