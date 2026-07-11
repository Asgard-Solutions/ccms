# Phase 3 UAT — Evidence index

**Purpose:** For each of the 50 UAT scenarios, this file records where the evidence lives (Playwright screenshot, fixture reference, automated-test id, or capture-plan step). Screenshots that require a fresh capture are marked `TO-CAPTURE` with the exact Playwright script line to run in `PHASE3_UAT.md`.

**Environment used for available captures:** preview container, demo Riverbend seed, admin/doctor/staff personas from `/app/memory/test_credentials.md`. **All patient data is fictional.**

## Legend

- ✅ Captured — evidence attached
- 📋 Automated-covered — no separate screenshot required (contract test on file)
- 🎬 TO-CAPTURE — capture step documented, needs authorized environment
- ⚙️ Fixture required — requires a specific seeded fixture (documented)

## Scenario evidence table

| # | Scenario | Evidence type | Path / reference |
|:-:|---|---|---|
| 1 | Masked patient | ✅ | `/app/memory/screenshots/01_admin_clinical_general.jpg` shows masked identity in `clinical-patient-context-header` for the admin's default view |
| 2 | Unmasked patient | 🎬 | Trigger More actions → Reveal protected information; expect audited unmask event in `audit_logs` (`patient.unmasked`) |
| 3 | No active episode | ⚙️ | Requires patient with no open episode; walk through the "New patient" fixture; expected `active-episode-empty` + "No current episode" row |
| 4 | Active episode | ✅ | Screenshot 01 shows `active-episode-card` for Riverbend demo patient |
| 5 | Multiple episodes | 📋 | Covered by `test_clinical_grouped_endpoints.py::test_every_group_has_source_ids` |
| 6 | Missing intake | ⚙️ | Requires patient with empty `clinical/history`. `care-status-cta-missing-intake` |
| 7 | Positive red flags | ⚙️ | Fixture: `history.red_flag_screening.fever=true`. Assert `safety-summary-red-flags` warning tone |
| 8 | Negative red flags | ✅ | Screenshot 03 shows Safety Summary in default tone for the demo patient |
| 9 | Multiple diagnoses | 📋 + ✅ | `test_clinical_phase3.py`; also visible in Screenshot 03 diagnoses list |
| 10 | Resolved diagnosis | 📋 | `dx-reactivate-<id>` action asserted by `test_clinical_phase3.py` |
| 11 | Encounter without note | 📋 | `test_clinical_grouped_endpoints.py::test_source_records_are_not_omitted` |
| 12 | Draft note | 📋 | `grouped-status-doc-<id>=Note draft` from grouped-encounters contract |
| 13 | Signed note | 📋 | `grouped-status-doc-<id>=Note signed` from grouped-encounters contract |
| 14 | Amended note | 📋 | `grouped-status-doc-<id>=Amended` from grouped-encounters contract |
| 15 | Billing warning | 📋 | `test_billing_readiness_aggregate.py::test_status_matches_counts` |
| 16 | Billing blocked | 📋 | `test_billing_readiness_aggregate.py::test_status_matches_counts` |
| 17 | No imaging | 📋 | `media-empty` covered by `test_clinical_phase5.py` |
| 18 | Imaging with complete metadata | 🎬 | Requires patient with 2+ media rows; assert modality label visible |
| 19 | Imaging missing classification | 📋 | `dataQualityEngine.test.js::imaging-missing-classification` |
| 20 | No outcomes | 📋 | `outcomes-section-empty` covered by `outcomeSeriesHelpers.test.js::no-entries` |
| 21 | Multiple outcomes | 📋 | Covered by `outcomeSeriesHelpers.test.js::baseline+latest` |
| 22 | Duplicate-date outcomes | 📋 | `outcomeSeriesHelpers.test.js::supersede-by-pickWinner` |
| 23 | Upcoming re-exam | 📋 | `test_clinical_phase3.py::reexam-approaching` |
| 24 | Overdue re-exam | 📋 | `test_clinical_phase3.py::reexam-overdue` |
| 25 | Completed treatment plan | 📋 | `test_clinical_phase3.py::plan-completed-status` |
| 26 | Active treatment plan | 📋 | `test_clinical_phase3.py::treatment-plan-progress` |
| 27 | Provider role | 🎬 | Log in as `doctor@ccms.app`; assert workspace switcher exposes only general + provider |
| 28 | Front-desk role | 🎬 | Log in as `staff@ccms.app` (Mia Ramirez); switch to front_desk; assert `next_appointment` first |
| 29 | Billing role | 🎬 | Same staff account; switch to billing; assert `billing_readiness` first, `data_quality` last |
| 30 | Administrator role | ✅ | Screenshot 02 captures the admin default view + workspace-mode switcher menu |
| 31 | Restricted role | 🎬 | Log in as `patient@ccms.app`; assert Clinical tab hidden (portal only) |
| 32 | Preference-service failure | 📋 | `workspaceModes.test.js::reorderSummary-no-mutation` + optimistic rollback covered by hook tests |
| 33 | Timeline partial failure | 📋 | `SectionErrorBoundary.jsx` covered by inline unit test in the same file |
| 34 | Outcomes partial failure | 📋 | Same as 33 (SectionErrorBoundary contract) |
| 35 | Imaging partial failure | 📋 | Same as 33 |
| 36 | Direct deep link | 🎬 | Visit `/patients/:id?tab=clinical#outcomes`; assert scroll + `clinical-nav-outcomes` active |
| 37 | Browser back/forward | 🎬 | Popstate walk between sections; existing implementation in `ClinicalTabV2.jsx` |
| 38 | Return-state restoration | 📋 | `useClinicalReturnState.test.js::restoration` |
| 39 | 200% zoom | 🎬 | Set `window.devicePixelRatio` via CDP; assert sticky header + section nav reflow |
| 40 | Keyboard-only | 🎬 | Tab through nav; assert focus reaches every `clinical-nav-<slug>` |
| 41 | Reduced motion | 🎬 | Set `prefers-reduced-motion: reduce`; assert `motion-reduce:hover:transform-none` applied |
| 42 | Small-screen (mobile) | 🎬 | Viewport 375px; assert nav horizontal scroll + Care Status reflow |
| 43 | Tablet layout | 🎬 | Viewport 900px; assert intake two-column → single column |
| 44 | Large patient history | ⚙️ | Requires 250+ timeline events; assert `INITIAL_RENDER_CAP=100` + `grouped-timeline-load-more` |
| 45 | Legacy fallback | 📋 + 🎬 | `ClinicalTabV2.flagMatrix.test.js::parent-off-disables-descendants`; browser step: `localStorage.setItem('ccms.flags.clinicalRedesign','off')` + reload |
| 46 | Slice 4 rollback | 📋 | `ClinicalTabV2.flagMatrix.test.js::each-slice-independently-rollback-safe` |
| 47 | Slice 5 rollback | 📋 | Same as 46 |
| 48 | Slice 6 rollback | 📋 | Same as 46 |
| 49 | Parent Phase 3 rollback | 📋 | `ClinicalTabV2.flagMatrix.test.js::phase3-off-disables-slices` |
| 50 | Full redesign rollback | 📋 | `ClinicalTabV2.flagMatrix.test.js::parent-off-disables-descendants` |

## Captured evidence (this session)

| File | Description | PHI status |
|---|---|:-:|
| `/app/memory/screenshots/00_login.jpg` | Login page with demo credentials visible (fictional) | Safe |
| `/app/memory/screenshots/01_admin_clinical_general.jpg` | Admin logged in, patient chart in default (general) workspace mode | Fictional |
| `/app/memory/screenshots/02_admin_clinical_billing.jpg` | Same patient with billing workspace mode active | Fictional |
| `/app/memory/screenshots/03_admin_clinical_mid.jpg` | Mid-page view showing safety summary, diagnoses, encounters | Fictional |

## Remaining evidence work

- 25-shot workspace/state screenshot pack — see `CLINICAL_RELEASE_SCREENSHOT_INDEX.md` for the capture plan. All shots use the seeded Riverbend demo tenant.
- Doctor + staff + patient role walk-through — captures the workspace-mode gate. Requires switching accounts in the same browser session.
- 500+ timeline-event chart — requires a synthetic fixture pass. See `PHASE3_PERFORMANCE_TEST_PLAN.md` §Large chart.
