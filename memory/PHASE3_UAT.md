# Phase 3 UAT — Patient Profile > Clinical Redesign

**Status:** signable UAT covering the full Clinical redesign (Phases 1 + 2 + 3 Slices 1–5 shipped; Slice 6 hardening in-flight in the parallel branch).

Each scenario lists the fixture, the expected behaviour, and the `data-testid` anchor a QA engineer can drive through Playwright.

## Fixtures

| Fixture | Role | Auth |
|---|---|---|
| `admin@ccms.app / Admin@ComplianceClinic1` | admin | may switch every workspace mode |
| `doctor@ccms.app / Doctor@ComplianceClinic1` | doctor | may switch general + provider |
| `staff@ccms.app / Staff@ComplianceClinic1` | staff | may switch general + front_desk + billing |

All fictional identities live inside **Riverbend Chiropractic & Wellness** (see `DEMO_SEED.md`).

## Scenarios (50)

1. **Masked patient**  — sign in as staff → open patient list → expect masked initials in `clinical-patient-context-header`.
2. **Unmasked patient** — sign in as admin → open More actions → Reveal protected information (audited) → expect unmasked name.
3. **No active episode** — expect `active-episode-empty` fallback and "No current episode" row in Care Status.
4. **Active episode** — expect `active-episode-card` with episode title + status + provider.
5. **Multiple episodes** — expect Episodes section to list all with `pickActiveEpisode` selecting the current one.
6. **Missing intake** — expect Care Status row "Missing required information (n fields)" with `care-status-cta-missing-intake`.
7. **Positive red flags** — expect `safety-summary-red-flags` row in warning tone + `care-status-row-red-flag`.
8. **Negative red flags** — expect Safety Summary in default (non-warning) tone; row absent from Care Status.
9. **Multiple diagnoses** — expect Diagnoses list to render each with `dx-row-<id>` and the primary badge on exactly one.
10. **Resolved diagnosis** — expect state pill "Resolved" and `dx-reactivate-<id>` action.
11. **Encounter without note** — expect Encounters filter default "Needs action" and `grouped-status-doc-<id>` = "Note missing".
12. **Draft note** — expect `grouped-status-doc-<id>` = "Note draft".
13. **Signed note** — expect `grouped-status-doc-<id>` = "Note signed".
14. **Amended note** — expect `grouped-status-doc-<id>` = "Amended".
15. **Billing warning** — expect `grouped-billing-message-<id>` inline copy + `grouped-status-billing-<id>` = "Billing warning".
16. **Billing blocked** — expect blocked tone (destructive) + Care Status "Review billing issues".
17. **No imaging** — expect `media-empty` compact row + Slice 4 `ImagingCard` render above the legacy card.
18. **Imaging with complete metadata** — expect each media tile to show modality label + date.
19. **Imaging missing classification** — expect `data-quality-row-imaging-missing-classification` in Data Quality panel.
20. **No outcomes** — expect `outcomes-section-empty`.
21. **Multiple outcomes** — expect per-instrument `OutcomeSnapshotCard` + chart/table toggle.
22. **Duplicate-date outcomes** — expect `outcome-<key>-superseded-note` on chart view.
23. **Upcoming re-exam** — expect `reexam-approaching` state and `reexam-schedule-btn`.
24. **Overdue re-exam** — expect `reexam-overdue` state with days-overdue copy.
25. **Completed treatment plan** — expect plan row `plan-row-<id>-status` = "Completed" and `TreatmentPlanProgress` at 100%.
26. **Active treatment plan** — expect segmented progress bar with completed/scheduled/remaining legend.
27. **Provider role** — sign in as doctor → expect workspace switcher to expose only general + provider → mode description reads "Provider mode · Next Actions and encounters prioritized".
28. **Front-desk role** — sign in as staff → expect general + front_desk + billing → summary rail leads with `next_appointment` when front_desk mode picked.
29. **Billing role** — same staff account switches to billing → expect `billing_readiness` module first, `data_quality` last.
30. **Administrator role** — sign in as admin → expect all 5 modes and `data_quality` module first when administrator selected.
31. **Restricted role** — sign in as patient → expect Clinical tab hidden entirely (patient portal has its own view).
32. **Preference-service failure** — mock `PATCH /auth/me/preferences` to 500 → expect toast + optimistic rollback of switcher state, Clinical page remains usable.
33. **Timeline partial failure** — mock `/clinical/timeline/grouped` to 500 → expect `grouped-timeline-error` inside the Timeline section boundary; other sections continue to render.
34. **Outcomes partial failure** — mock `/clinical/outcomes` to 500 → expect `outcomes-section-error` + Care Status still visible.
35. **Imaging partial failure** — mock `/clinical/media` to 500 → expect `ImagingCard` error state + legacy `MediaCard` still attempts its own fetch.
36. **Direct deep link** — visit `/patients/:id?tab=clinical#outcomes` → expect scroll to outcomes with `clinical-nav-outcomes` active.
37. **Browser back/forward** — jump between sections → browser back restores previous section via `popstate` listener.
38. **Return-state restoration** — set filters on timeline, jump away, return → filters restored via `useClinicalReturnState`.
39. **200% zoom** — set browser zoom to 200% → expect sticky patient header + section nav to reflow without covering hash target.
40. **Keyboard-only** — Tab through nav → each `clinical-nav-<slug>` reachable and Space/Enter fires `jumpTo`; skip link (`clinical-skip-link`) visible on first Tab.
41. **Reduced motion** — set `prefers-reduced-motion: reduce` → expect Summary tile hover translate disabled (`motion-reduce:hover:transform-none`).
42. **Small-screen (mobile)** — viewport 375px → expect nav to scroll horizontally, Care Status to reflow, dialogs to fit viewport.
43. **Tablet layout** — viewport 900px → expect two-column intake to collapse to single column but sticky header remain.
44. **Large patient history** — 250+ timeline events → expect INITIAL_RENDER_CAP=100 + `grouped-timeline-load-more` button.
45. **Legacy fallback** — set `ccms.flags.clinicalRedesign=off` in localStorage → expect legacy `ClinicalTab` component to render.
46. **Slice 4 rollback** — set `ccms.flags.clinicalRedesignPhase3Slice4=off` → expect `ImagingCard` + `DataQualityPanel` removed; `MediaCard` remains.
47. **Slice 5 rollback** — set `ccms.flags.clinicalRedesignPhase3Slice5=off` → expect workspace switcher hidden + section order returns to default NAV_ITEMS.
48. **Slice 6 rollback** — set `ccms.flags.clinicalRedesignPhase3Slice6=off` → expect section boundaries still catch errors (defence-in-depth) but visible error UI reverts to per-card fallback.
49. **Parent Phase 3 rollback** — set `ccms.flags.clinicalRedesignPhase3=off` → expect every Phase 3 child inert regardless of stored preferences; layout collapses to Phase 2 behaviour.
50. **Full Clinical redesign rollback** — set `ccms.flags.clinicalRedesign=off` → expect legacy tab, no sticky header, no section nav, no Care Status.

## Rollback verification matrix

Verified via `ClinicalTabV2.flagMatrix.test.jsx` (12 hand-picked slices of the 256 flag combos — full sweep is redundant given the nested-flag maths). Every combination mounts without throwing and produces non-empty output.

Acceptance signed by: _____________________  Date: _____________
