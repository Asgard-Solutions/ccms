# Phase 1 Clinical Redesign — User Acceptance Testing matrix

Feature flag: `clinicalRedesign` (env `REACT_APP_CLINICAL_REDESIGN`, default `on`)
Fallback: legacy `ClinicalTab` renders when flag is `off`.

Testers should sign each row with initials + timestamp after verifying.

## Roles / test accounts

Source of truth: `/app/memory/test_credentials.md`

| Role tested | Account | Notes |
|---|---|---|
| Admin | `admin@ccms.app` | Full unmask, export, archive |
| Doctor | see credentials file | Read/write clinical, no archive |
| Front-desk (staff) | see credentials file | Restricted PHI, no unmask, no archive |
| Provider (limited) | doctor without unmask right | Verifies "More actions" collapses when no privileges |

## Scenario matrix (10 required + 1 accepted-limitation)

| # | Scenario | Patient / setup | What to verify | Evidence | Pass / Notes |
|---|---|---|---|---|---|
| 1 | Patient with active episode and active plan | `M. R.` (`0601bbe4-…`) — active episode "New patient — cervicogenic headache & neck stiffness", 1 active plan | Context header shows episode + primary Dx + provider + next appt. Care-status panel lists visits completed / scheduled / remaining. Summary tiles reflect real counts. | Screenshot of `clinical-patient-context-header` + `clinical-care-status-panel` | |
| 2 | Patient without an active episode | Any patient after closing all episodes, OR a freshly created patient with no episode | Context "Episode" chip renders "No active episode"; Care-status "Active episode" row reads "No current episode" with muted tone (no invented data); "New episode" CTA visible for writers | Screenshot of care-status panel showing muted state | |
| 3 | Missing intake data | Patient whose `clinical/history` returns `{}` or lacks `chief_complaint` / `history_of_present_illness` | Context alerts include no missing-intake row. Care-status panel exposes "Missing required information (N fields)" with **warning** tone and an "Open history" CTA. IntakeHistoryCard shows required fields with warning styling `Missing required information`. | Screenshot of care-status + history section | |
| 4 | Positive red-flag responses | Patient with `history.red_flag_screening.fever = true` (staging seed or force-edit via re-import) | Context header shows red badge "Red-flag: fever". Care-status includes "Safety: Positive red-flag findings: fever". IntakeHistoryCard renders positive findings in destructive box, NOT the "No fever, recent trauma…" sentence. | Screenshot header + history sentence | |
| 5 | Negative red-flag responses (baseline) | `M. R.` or any demo patient with all red-flags false | IntakeHistoryCard shows the clinical sentence `No fever, recent trauma, or night pain reported.` with foreground colour (no warning styling). Raw booleans (`fever: false`) NOT visible. | Screenshot of history field `history-field-red_flag_screening` | |
| 6 | Billing warnings on an encounter | Encounter with unresolved billing readiness checks (any patient with existing claims — e.g. `M. R.`) | Encounter card's BillingReadinessPanel collapsed header shows sentence-case "Billing readiness" label + status badge + count summary (e.g. "2 warnings · Missing modifier") + hint text. Click expands full panel. `aria-expanded` toggles. | Screenshot of collapsed + expanded panel | |
| 7 | No imaging | `M. R.` (no media rows) | Imaging section shows compact horizontal empty state `media-empty` with `Upload` CTA (writers only); tall dashed centered card NOT rendered. | Screenshot of imaging section | |
| 8 | No outcomes recorded | Fresh patient with zero outcomes | Outcomes section shows compact horizontal `outcomes-empty` with `Record outcome` CTA (writers only). Snapshot / Trend tabs still switchable but empty. | Screenshot of outcomes section | |
| 9 | No scheduled re-exam | Any patient without an upcoming re-exam | Context "Re-exam due" chip reads "Not scheduled"; if re-exam IS due (plan `next_reexam_due_date` present) → warning tone + "Schedule re-exam" CTA in care-status panel. `reexams-empty` in Care plan section is compact row. | Screenshot(s) of context chip + care-status row | |
| 10 | Restricted user permissions | Login as staff / front-desk (no unmask, no archive) | "More actions" menu shows only the permitted items (e.g. Export only). If NO permitted actions, the "More actions" trigger is hidden. Interactive summary tiles and section nav still fully usable. | Screenshot of More actions dropdown as staff | |
| 11 | Masked vs unmasked patient states | `M. R.` — start masked, then Reveal, then Hide again | Masked state: initials `MR`, name `M. R.`, DOB `19**-**-**`. After Reveal: full name + DOB visible; age computed and shown in context (`Age NN`) — only when unmasked. Hide restores mask. Audit event fired on Reveal (check `/api/audit-logs` or DB `audit_logs`). | Screenshots of both states + audit row | |

## Additional acceptance-criteria checks

- [ ] Sticky patient header remains visible while scrolling through every section (already automated).
- [ ] Section nav highlights currently-visible section (verified 8/8 sections including Outcomes after 2026-07-10 fix).
- [ ] Deep-link `/patients/<id>?tab=clinical#imaging` opens with Imaging section active on load.
- [ ] Keyboard: Tab traverses nav pills + tiles; Enter/Space activates; visible focus ring present.
- [ ] Reduced-motion: `prefers-reduced-motion: reduce` disables tile hover translate + back-to-top translate (already applied via `motion-reduce:` utilities).

## Known limitations documented for testers

- **Preview watermark** ("Made with Emergent") can occlude bottom-right `clinical-back-to-top` click coordinates in Playwright automation. Manual click and production tenants unaffected. See `PHASE1_TEST_DISPOSITION.md`.
- **Chart-wide billing aggregate** — `Current care status` "Billing warnings" row deliberately hidden in Phase 1 (no invented data). Populates in Phase 2 aggregation step.

## Sign-off

| Tester | Role | Signed at | Notes |
|---|---|---|---|
|  |  |  |  |

