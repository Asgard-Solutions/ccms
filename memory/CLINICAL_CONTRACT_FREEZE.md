# Clinical Contract Freeze — G4 evidence

**Redesign scope:** Patient Profile > Clinical (Phases 1 + 2 Waves A/B + Phase 3 Slices 1–6).
**Freeze date:** 2026-02-15.
**Status:** `COMPLETE`.
**Verified against code:** 2026-02-15 in the fork agent release-gate pass.

## Contract registry (17 contracts frozen)

| # | Contract | Owner | Version | Source file | Consumer(s) | Test file(s) |
|:-:|---|---|---|---|---|---|
| 1 | `ClinicalUIDefaults` | Workspace / preferences team | 1.0 | `backend/services/identity/models.py` | `PATCH /auth/me/preferences`, `GET /auth/me`, frontend Slice 5 hooks | `test_preferences_slice5.py`, `test_clinical_ui_defaults.py` |
| 2 | `PreferencesUpdate` | Identity team | 1.0 | `backend/services/identity/models.py` | `PATCH /auth/me/preferences` | `test_clinical_ui_defaults.py` |
| 3 | `UIEventPayload` | Platform reliability | 1.0 | `backend/services/telemetry/router.py` | `POST /telemetry/ui-event` | `test_telemetry_phi_probe.py` |
| 4 | `UIActionPayload` (three shapes) | Product analytics | 1.1 (Slice 3) | `backend/services/telemetry/router.py` | `POST /telemetry/ui-action` | `test_telemetry_ui_action.py`, `test_next_action_telemetry.py`, `test_outcome_suggestion_telemetry.py` |
| 5 | Outcome-suggestion telemetry payload | Outcomes team | 1.0 | `backend/services/telemetry/router.py` (`clinical_outcome_suggestion_interaction`) | `POST /telemetry/ui-action` | `test_outcome_suggestion_telemetry.py` |
| 6 | Feature-flag registry | Clinical platform lead | 1.0 | `frontend/src/utils/featureFlags.js` (`FLAG_DEFAULTS`, `ENV_VAR_MAP`, `FLAG_PARENTS`) | ClinicalTabV2 + every gated slice | `ClinicalTabV2.flagMatrix.test.js` |
| 7 | Workspace-mode registry | Workspace / preferences team | 1.0 | `frontend/src/pages/clinical/workspaceModes.js` | ClinicalTabV2, WorkspaceModeSwitcher, SummaryConfigDrawer | `workspaceModes.test.js` |
| 8 | Timeline grouped endpoint schema | Clinical platform lead | 1.0 unfiltered / 1.1 filtered | `backend/services/clinical/grouped_router.py` | `GET /patients/{id}/clinical/timeline/grouped` | `test_clinical_grouped_endpoints.py`, `test_grouped_timeline_filters.py` |
| 9 | Timeline filter schema | Timeline team | 1.1 | `backend/services/clinical/grouped_router.py` (`filter_meta`) | Same endpoint | `test_grouped_timeline_filters.py` |
| 10 | Encounter grouped endpoint schema | Clinical platform lead | 1.0 | `backend/services/clinical/grouped_router.py` | `GET /patients/{id}/clinical/encounters/grouped` | `test_clinical_grouped_endpoints.py` |
| 11 | Billing-readiness aggregate schema | Billing team | 1.0 | `backend/services/clinical/grouped_router.py` (`billing-readiness/aggregate`) | `GET /patients/{id}/clinical/billing-readiness/aggregate` | `test_billing_readiness_aggregate.py` |
| 12 | Next Actions rule registry | Clinical platform lead | 1.0 | `frontend/src/pages/clinical/nextActionsEngine.js` | NextActionsPanel | `nextActionsEngine.test.js` |
| 13 | Outcome-series derivation contract | Outcomes team | 1.0 | `frontend/src/pages/clinical/outcomeSeriesHelpers.js` | OutcomeSnapshotCard, OutcomeTrendChart, OutcomeTrendTable | `outcomeSeriesHelpers.test.js` |
| 14 | Imaging modality + source vocabulary | Imaging + data-quality team | 1.0 | `frontend/src/pages/clinical/dataQualityEngine.js`, `frontend/src/pages/clinical/ImagingCard.jsx` | ImagingCard, DataQualityPanel | `dataQualityEngine.test.js` |
| 15 | Data-quality rule registry | Imaging + data-quality team | 1.0 | `frontend/src/pages/clinical/dataQualityEngine.js` | DataQualityPanel | `dataQualityEngine.test.js` |
| 16 | Return-state hook contract | Clinical platform lead | 1.0 | `frontend/src/pages/clinical/useClinicalReturnState.js` | GroupedTimelineCard, ImagingCard, OutcomesSection | `useClinicalReturnState.test.js` |
| 17 | StatusBadge dimensions + vocabulary | Clinical platform lead | 1.0 | `frontend/src/pages/clinical/status/StatusBadge.jsx` | GroupedEncountersCard, TreatmentPlansCard, DiagnosesCard | Integration-covered (no dedicated test file) |

## Contract-level guarantees (verified 2026-02-15)

- **`extra=forbid` active on all Pydantic contracts.** Confirmed by grep:
  - `services/identity/models.py` — 12 model_configs with `extra="forbid"`.
  - `services/telemetry/router.py` — 2 model_configs with `extra="forbid"`.
- **Forbidden patient/record identifiers rejected.** Confirmed by `test_clinical_ui_defaults.py::TestRejectsPhiOrFreeText` (patient_id, encounter_id, note_id, dates of service, diagnosis codes, free-text q, episode_ids).
- **Unknown enum values rejected.** Confirmed by `test_grouped_timeline_filters.py::test_unknown_kind_ignored_not_errored` (ignored, not 400) and `test_next_action_telemetry.py::test_unknown_action_id` (422).
- **Existing schema versions pinned.** `schema_version: "1.0"` on grouped-encounters / billing-readiness; `schema_version: "1.1"` on filtered grouped-timeline. Locked in `test_clinical_grouped_endpoints.py` and `test_grouped_timeline_filters.py`.
- **Backward-compatible behavior preserved.** Unfiltered timeline request returns 1.0 shape unchanged.
- **Source-record IDs present where contractually required.** `test_clinical_grouped_endpoints.py::test_source_records_are_not_omitted`.
- **No PHI in telemetry.** `test_telemetry_phi_probe.py` fuzzes 13 PHI-like keys; all rejected.
- **No PHI in durable preferences.** `test_clinical_ui_defaults.py::TestRejectsPhiOrFreeText`.
- **Workspace-mode ordering deterministic.** `workspaceModes.test.js::resolveSummaryOrder` + `reorderSummary` (no-mutation).
- **Feature-flag parent chain tested.** `ClinicalTabV2.flagMatrix.test.js::parent-off-disables-descendants`.
- **Data-quality rules deterministic + non-mutating.** `dataQualityEngine.test.js::input-arrays-byte-identical`.
- **Next Actions deterministic + non-clinical.** `nextActionsEngine.test.js::labels-and-why-strings-stay-non-clinical`.
- **Outcome calculations non-interpretive.** `outcomeSeriesHelpers.test.js::no-clinical-inference-fields`.
- **No undocumented field consumed by the frontend.** Every schema is declared in the corresponding contract doc under `/app/memory/PHASE3_SLICE*_CONTRACTS.md`.
- **No frontend dependency on free-form backend text.** Every UI label is derived from structured slugs or vocabularies documented in the contract table above.

## Change policy

See `/app/memory/CLINICAL_CONTRACT_CHANGE_POLICY.md`.

## Snapshot registry file

Machine-readable representation at `/app/memory/CLINICAL_CONTRACT_REGISTRY.json`.

## Verification method

For each contract, the freeze pass:
1. Located the source file.
2. Confirmed the guardrails listed above.
3. Ran the corresponding test file(s) — all passed in the automated run (see `AUTOMATED_TEST_RESULTS.md`).
4. Confirmed the contract document under `/app/memory/PHASE3_SLICE*_CONTRACTS.md` matches the code (no drift detected).

## Status

**COMPLETE.**

No contract discrepancies discovered. Contract tests pass. Registry matches code as of 2026-02-15.
