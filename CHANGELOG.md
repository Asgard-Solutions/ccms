# Changelog

All notable, user-visible, or security-relevant changes to CCMS are recorded
here. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project follows a rolling date-based release cadence (no SemVer
public release yet — we're pre-1.0).

> **Update rule** — every merged PR that changes behavior, adds a feature,
> fixes a bug, or changes a dependency MUST append an entry to this file.
> See [`docs/DOC_UPDATE_POLICY.md`](./docs/DOC_UPDATE_POLICY.md).

## [Unreleased]

### Added
- **Billing — Phase 9 Claims-from-Encounter (2026-04-21).** New
  `POST /api/billing/claims/from-encounter` synthesises a draft claim
  skeleton from a documented clinical encounter. Reuses the Phase 8
  readiness evaluator (`evaluate_billing_readiness` extracted into a
  reusable helper) and copies patient, rendering provider, DOS,
  diagnoses, and documented procedures into claim headers + lines + dx.
  CPT codes default to kind-based hints (e.g. `98940` for manipulation,
  `97140` for soft-tissue, `97110` for therapeutic exercise, `99203`
  for exam) and billed_cents start at 0 — the operator finalises codes
  and pricing in the Claim Editor. Blocked encounters return 409 with
  a structured `blocking` list unless an admin passes `force=true`.
  Non-admins cannot force. Every creation emits a
  `billing.claim.created_from_encounter` audit row with the source
  encounter id + readiness status.
  - **Backend**: `services/billing/router.py` —
    `ClaimFromEncounterInput`, `KIND_TO_HINT`, and the endpoint itself.
    9 pytest cases (6 in `test_billing_phase9.py`, 2 added by testing
    agent in `test_billing_phase9_nonadmin.py`).
  - **Frontend**: `BillingReadinessPanel` gained a "Create claim draft"
    button + `CreateClaimDialog` (payer + policy selects pulled from
    existing insurance hooks, POS/notes fields, force-override
    checkbox gated on `role==='admin'`). On success, toasts and
    navigates to `/billing/claims/{id}`.
- **Frontend UX — `window.confirm()` sweep (2026-04-21).** Replaced
  every remaining `window.confirm()` and `window.prompt()` call with
  Shadcn `AlertDialog` via a new reusable wrapper
  (`/app/frontend/src/components/ConfirmDialog.jsx`). Browser-native
  dialogs were being silently blocked in the preview iframe, which
  left destructive actions non-functional. Updated surfaces:
  - Clinical: `AddendumPanel` (delete draft), `MediaCard` (delete media).
  - Access control: `RoleManagement` (revoke role + revoke override),
    `Elevation` (cancel request).
  - Billing: `PatientInsuranceManager` (deactivate policy).
  - Compliance: `Privacy` (transition request via `Dialog`, fulfil
    delete via `ConfirmDialog`).
- **Frontend refactor — `ProvidersProvider` context (2026-04-21).**
  New `/app/frontend/src/contexts/ProvidersContext.jsx` caches the
  `/auth/providers` roster once per session (with in-flight dedupe)
  and exposes `useProviders()`. `PatientDetail`, `PatientWizardDialog`,
  `BookDialog`, and `ProviderFilter` all stopped issuing their own
  fetch and now read from the context. Removes ~4 redundant roundtrips
  per navigation.

### Added (previous — kept for continuity)
- **Clinical module — Phase 8 (2026-04-21).** Billing Readiness,
  lifecycle hardening, addenda, and audit coverage. The chart is now
  "defensibly billable": every appointment-linked encounter exposes a
  read-only Billing Readiness evaluation; signed follow-up notes,
  initial exams, and re-exams are fully immutable and extended only
  through append-only, individually-signed addenda; all
  create/edit/sign/delete/linkage events are captured in the global
  `audit_logs` stream plus the patient-scoped
  `clinical_audit_events` projection.
  - **Backend** under `services/clinical/`:
    - `addenda_models.py` + `addenda_router.py` — new collection
      `clinical_addenda`. Strict authorship: any writer may create;
      only the addendum's author or an admin may edit / sign /
      delete that addendum. Parent must be signed (409 otherwise).
      Post-sign PATCH/DELETE return 409.
    - `billing_readiness_router.py` — single GET endpoint, read-only.
      Response schema future-billing-friendly:
      `{encounter_id, appointment_id, provider_id, provider_name,
      date_of_service, episode_id, visit_type, visit_type_label,
      note {kind,status,signed_at,signed_by,addendum_count,has_addenda},
      diagnoses[], procedures[], treatment_plan, overall_status,
      checks[], generated_at}`. Checks cover patient/provider/DOS
      presence, appointment linkage, encounter completeness, note
      existence + signed + signature present, diagnosis linkage,
      treatment documented, objective findings, response / progress
      documented, treatment-plan linkage (fail for follow-up/treatment
      visits; info for NPE/re-eval), and re-exam-overdue.
      `overall_status` is `blocked` if any fail-severity check fails,
      `warnings` if any warn-severity fails, else `ready`. Never
      mutates billing data.
    - `notes_models.py` — `CareTimelineEntry.kind` adds `addendum`;
      `FollowUpNotePublic` adds `has_addenda`, `addendum_count`,
      `latest_addendum_at`.
    - `exams_models.py`, `reexams_models.py` — same three addendum
      fields on `InitialExamPublic`, `ReExamPublic`.
    - Hydrate functions on notes/exams/re-exams now count addenda
      per parent so editor headers can show
      `Signed · +N addendum(s)`.
    - `notes_router.py` care-timeline endpoint aggregates signed
      addenda (kind=`addendum`), anchored to the parent artifact's
      deep-link.
    - `notes_router.py` PATCH now emits dedicated
      `follow_up_note.treatment_plan_linkage_changed` and
      `follow_up_note.diagnosis_linkage_changed` clinical-audit
      events in addition to the generic `updated` audit.
  - **Frontend** under `pages/clinical/`:
    - `BillingReadinessPanel.jsx` — collapsible per-encounter;
      persistent header status chip; check rows + future-billing
      summary with diagnoses and procedures.
    - `AddendumPanel.jsx` — mounts under each signed
      note/exam/re-exam editor; create dialog (reason + narrative),
      sign, delete-draft. Post-sign actions disappear; non-author
      drafts hide author-only actions.
    - `LifecycleBadge.jsx` — shared lifecycle pill for chart-wide
      use.
    - `EncountersCard.jsx` — mounts `BillingReadinessPanel` per row.
    - `FollowUpNoteEditor.jsx`, `InitialExamEditor.jsx`,
      `ReExamEditor.jsx` — mount `AddendumPanel` (with `onChanged`
      callback so the editor's status badge refreshes the addendum
      suffix without a page reload). Badges show
      `Signed · +N addendum(s)` when applicable.
    - `CareTimelineCard.jsx` — `KIND_META` extended for `addendum`
      with `MessageSquarePlus` icon; timeline rows for addenda show
      the reason as subtitle and deep-link to the parent artifact.
  - **Testing**: `backend/tests/test_clinical_phase8.py` — nine
    tests, all green (sign-locks-PATCH, addendum requires-signed-parent,
    addendum create/edit/sign/lock lifecycle, non-author-forbidden +
    admin-can-sign, billing readiness blocked / ready / missing-plan,
    timeline addendum kind, linkage-change audit). Frontend
    validated via `testing_agent_v3_fork` iteration 38 (all addendum
    + billing-readiness + timeline flows pass; minor
    auto-refresh polish already resolved).
  - **Guardrails**: billing readiness stays read-only and evaluative;
    signed base artifacts stay locked; addenda are append-only +
    individually signed + immutable once signed; no billing
    automation, no CPT suggestion, no claim generation in this
    phase.

- **Clinical module — Phase 7 (2026-04-21).** Imaging & Clinical
  Media + Outcomes / Functional Measures + Care Timeline v2. Chart
  gets first-class file storage for x-rays / MRI / CT / ultrasound /
  clinical photos / outside records, plus longitudinal patient-
  reported outcomes (NDI, Oswestry, Pain VAS, pain scale,
  functional index, custom) with inline trend SVGs. The Care Timeline
  merges three new entry kinds (`clinical_media`, `outcome_entry`,
  `diagnosis_change`) on top of the existing encounter / exam /
  note / re-exam / plan stream, plus a fourth kind
  (`intake_submission`) derived from clinical audit events.
  - **Backend** under `services/clinical/`:
    - `media_models.py` + `media_router.py` — list / multipart
      upload / detail / streamed download / metadata patch /
      soft-delete. Categories: xray, mri_ct_report, ultrasound,
      clinical_photo, outside_record, other_pdf. MIME validation
      via `python-magic` (PNG / JPEG / WebP / HEIC + PDF, 25 MB
      cap). Objects persisted through the pre-existing
      `core.object_storage`; binary never inlined.
    - `outcomes_models.py` + `outcomes_router.py` — list / record
      outcome + `GET /outcomes/trends` grouping by
      `(measure_type, label)`, chronological series for inline
      charting.
    - `notes_router.py` care-timeline endpoint extended to aggregate
      `clinical_media` (excluding soft-deleted), `outcome_entry`
      (excluding `source=reexam` to avoid duplicating the re-exam
      row), `diagnosis_change` (from `clinical_audit_events` where
      `event_type` in `diagnosis.created/updated/resolved/activated`),
      and `intake_submission` (from
      `clinical_history.intake_submitted` events).
    - `reexams_router.py` on-sign hook now emits one
      `clinical_outcome_entries` row per OutcomeUpdate with
      `source=reexam` and `reexam_id` linkage — the trends
      endpoint picks them up automatically.
  - **Frontend** under `pages/clinical/`:
    - `MediaCard.jsx` — filter chips, 4-col thumbnail grid, upload
      dialog (category / source / body region / study date /
      findings), detail dialog with inline image / PDF preview,
      download link, soft-delete button. Re-auth-aware on 401.
    - `OutcomesCard.jsx` — snapshot grid (per-measure chip +
      delta-vs-prior badge) and trend mode (compact inline SVG line
      chart per measure, no charting library).
    - `CareTimelineCard.jsx` — extended `KIND_META` and
      `STATUS_TONE` tables; renders new kinds with proper icons,
      status tones, and optional deep-links.
    - `TreatmentPlanEditor.jsx` — new read-only "Latest outcomes"
      section right after baselines; pulls from `/outcomes/trends`;
      never mutates data.
    - `ClinicalTab.jsx` — mounts `MediaCard` and `OutcomesCard`;
      the Phase-2 Imaging / Outcomes placeholders are removed
      (only Billing Readiness placeholder remains).
  - **Testing**: backend `pytest`
    (`backend/tests/test_clinical_phase7.py`) covers the full
    media + outcomes + timeline merge flow including re-exam
    auto-emission. Frontend validated via `testing_agent_v3_fork`
    (iteration 37) for static wiring + main-agent self-test in the
    live preview (admin login → media upload → outcomes record
    (7 then 4) → trend SVG → care timeline merge) with all four
    new `data-testid` scopes verified.
  - **Guardrails**: reused existing `core.object_storage` (no new
    third-party dependency); auto-emitted standalone outcomes on
    re-exam sign; inline SVG charts (no chart library); treatment
    plan "Latest outcomes" is read-only and lightweight.
  - **Ops note**: `python-magic` requires `libmagic1` at the
    system level; confirmed installed in the container. Add to the
    base image / Dockerfile for new builds.

- **Clinical module — Phase 6 (2026-02-22).** Treatment Plans +
  Re-Exams workflow. Chart-level plan of care (goals, frequency,
  duration, baselines, discharge criteria) plus a structured
  comparison Re-Exam launched from `re_evaluation` encounters. Signing
  a `modify_plan` re-exam emits a `treatment_plan.revised_recommended`
  audit event but never mutates the plan — explicit provider action
  required.
  - **Backend** under `services/clinical/`:
    - `treatment_plans_models.py` — plan + goal + baseline Pydantic
      models with `TreatmentPlanProgress` (visits_completed /
      total_visits / percent).
    - `treatment_plans_router.py` — endpoints under `/api`:
      - `GET /patients/{pid}/clinical/treatment-plans`
      - `POST /patients/{pid}/clinical/treatment-plans` —
        one-active-plan-per-episode guard → 409 with `existing_plan_id`
        in the error detail.
      - `GET/PATCH /patients/{pid}/clinical/treatment-plans/{tpid}`
        (PATCH on discharged / completed / cancelled → 409).
      - `POST .../{tpid}/set-status` — all transitions with required
        reason; discharged records `discharge_reason` +
        `discharged_at`.
      - Visit progress computed live from signed follow-up notes on
        the same episode since `start_date`.
    - `reexams_models.py` — `GoalProgressEntry`, `OutcomeUpdate`
      (typed: ndi / oswestry / pain_vas / functional_index /
      custom), `RECOMMENDATION` Literal (continue / modify_plan /
      discharge / transition_maintenance), reuses
      `ExamExamination` + `NewDiagnosisDraft` from Phase 4.
    - `reexams_router.py` — endpoints under `/api`:
      - `GET /patients/{pid}/clinical/re-exams`
      - `POST /patients/{pid}/clinical/re-exams` — one per
        encounter; duplicate returns 200 + `X-ReExam-Existed: true`.
        Cancelled encounter → 409. At create, freezes
        `baseline_snapshot` containing active plan (goals +
        baselines + frequency) + most recent signed Initial Exam
        history/examination + prior re-exam (for trend context).
        Also captures `visit_number_at_reexam` = signed follow-up
        notes count on this episode.
      - `GET/PATCH /patients/{pid}/clinical/re-exams/{rid}` — PATCH
        validates `updated_diagnosis_ids` belong to patient (400) +
        `goal_progress.goal_id` against baseline plan goal ids (400).
      - `POST .../mark-sign-ready` / `.../unmark-sign-ready`
      - `POST .../sign` — terminal. Requires `recommendation_decision`
        (400 if missing). Materializes `new_diagnoses` with ICD-10
        uppercasing + de-dup (same semantics as Initial Exam).
        When decision=modify_plan, emits
        `treatment_plan.revised_recommended` audit event; plan
        unchanged.
      - `GET .../narrative` — rendering: `RE-EXAMINATION NOTE`
        header + BASELINE (frozen) + UPDATED OBJECTIVE FINDINGS +
        GOAL PROGRESS (baseline→current→target per goal) + OUTCOME
        MEASURES + RECOMMENDATION sections.
    - Summary endpoint extended: `treatment_plans.{total, open}`
      (open = active) and `re_exams.{total, open}` (open = draft +
      sign_ready).
    - Follow-up note `_hydrate` now injects `active_plan_summary`
      (id, title, frequency, top-3 goals, visit progress) when an
      active plan exists on the note's episode.
    - Care timeline merges `treatment_plan` + `re_exam` entries
      alongside encounters + exams + notes with deep-link paths.
  - **Access + audit**: reads `admin|doctor|staff`, writes
    `admin|doctor` + `require_reauth`. Tenant isolation — cross-
    tenant probes 404. Every mutation emits a global `audit_logs`
    row + patient-scoped `clinical_audit_events` (events:
    `treatment_plan.created/updated/status_changed`,
    `re_exam.created/updated/signed`,
    `treatment_plan.revised_recommended`).
  - **Indexes** in `core/db.py`: `clinical_treatment_plans` on
    `(tenant_id, patient_id, plan_status)` + `(tenant_id, episode_id)`;
    `clinical_reexams` on `(tenant_id, encounter_id)` UNIQUE +
    `(tenant_id, patient_id, date_of_service)` + `(tenant_id, status)`.
  - **Frontend**:
    - `pages/clinical/TreatmentPlansCard.jsx` + `TreatmentPlanEditor.jsx`
      (route `/patients/:pid/clinical/treatment-plans/:tpid`) —
      structured sections (overview, interventions, goals, baselines,
      home-care, activity/work, discharge, maintenance). Set-status
      dialog with required reason. Progress bar tied to
      `frequency_total_visits`.
    - `pages/clinical/ReExamsCard.jsx` + `ReExamEditor.jsx`
      (route `/patients/:pid/clinical/re-exams/:rid`) — frozen
      plan + initial exam snapshot rendered read-only; goal progress
      rows auto-seeded from plan goals; typed outcome measures
      editor; recommendation radio + reason; `revised_plan_summary`
      conditional on decision=modify_plan; sign disabled when no
      decision or while dirty; signed banner post-sign.
    - `pages/clinical/EncountersCard.jsx` — `re_evaluation` →
      `encounter-start-reexam-{id}` (replaces Start Initial Exam
      for that type); no regression for new_patient_exam
      (encounter-start-exam-{id}) or follow_up/treatment_visit
      (encounter-start-note-{id}).
    - `pages/clinical/ClinicalTab.jsx` — `stat-treatment-plans` +
      `stat-reexams` tiles added; two new cards mounted; Phase-2
      placeholders removed.
    - `pages/clinical/CareTimelineCard.jsx` — supports
      `treatment_plan` + `re_exam` kinds with distinct icons.
    - `pages/clinical/FollowUpNoteEditor.jsx` —
      `note-active-plan-strip` renders plan title + frequency +
      top-3 goals + visit progress when an active plan exists on
      the note's episode.
    - `App.js` routes for both editors.
  - **Tests**: `backend/tests/test_clinical_phase6.py` — 14
    cases; 14/14 passing. Phase 5 regression 12/12 green.
    Frontend E2E (`iteration_36.json`): TreatmentPlanEditor 19/19
    testids + ReExamEditor 21/21 testids verified.
  - **Test-ids**: `stat-treatment-plans`, `stat-reexams`,
    `treatment-plans-card`, `plans-empty`, `plans-list`,
    `plan-row-{id}`, `plan-row-{id}-status`, `plan-row-{id}-progress`,
    `plan-create-btn`, `treatment-plan-editor`, `plan-status-badge`,
    `plan-discharge-reason`, `plan-save-btn`, `plan-set-status-btn`,
    `plan-progress`, `plan-progress-text`, `plan-section-overview/
    interventions/goals/baselines/recommendations/discharge`,
    `plan-title`, `plan-reexam-date`, `plan-freq-week`,
    `plan-total-visits`, `plan-duration-weeks`, `plan-target-regions`,
    `plan-intervention-row-{i}`, `plan-intervention-{i}-kind/desc/
    freq/remove`, `plan-intervention-add`, `plan-goal-row-{i}`,
    `plan-goal-{i}-desc/measure/baseline/target/status/remove`,
    `plan-goal-add`, `plan-baseline-pain`, `plan-baseline-rom`,
    `plan-fm-row-{i}`, `plan-fm-{i}-label/value/unit/remove`,
    `plan-fm-add`, `plan-home-care`, `plan-activity-work`,
    `plan-discharge-criteria`, `plan-maintenance-notes`,
    `plan-set-status-dialog`, `plan-status-select`,
    `plan-status-reason`, `plan-status-submit-btn`,
    `reexams-card`, `reexams-empty`, `reexams-list`,
    `reexam-row-{id}`, `reexam-row-{id}-status`,
    `reexam-row-{id}-reco`, `encounter-start-reexam-{id}`,
    `reexam-editor`, `reexam-status-badge`, `reexam-visit-number`,
    `reexam-save-btn`, `reexam-mark-ready-btn`,
    `reexam-unmark-ready-btn`, `reexam-sign-btn`,
    `reexam-narrative-btn`, `reexam-narrative-dialog`,
    `reexam-narrative-text`, `reexam-section-baseline/goals/findings/
    outcomes/recommendation`, `reexam-plan-snapshot`,
    `reexam-goal-list`, `reexam-goal-row-{i}`,
    `reexam-goal-{i}-baseline/current/status/note`,
    `reexam-findings-{field}`, `reexam-outcome-list`,
    `reexam-outcome-row-{i}`, `reexam-outcome-{i}-type/label/score/
    max/note/remove`, `reexam-outcome-add`, `reexam-decision-group`,
    `reexam-decision-{value}`, `reexam-decision-reason`,
    `reexam-revised-summary`, `reexam-signed-banner`,
    `note-active-plan-strip`, `note-active-plan-progress`,
    `note-plan-goal-{id}`, `timeline-entry-treatment_plan-{id}`,
    `timeline-entry-re_exam-{id}`.

## [Unreleased — earlier in the window]

### Added
- **Clinical module — Phase 5 (2026-02-22).** Follow-up / Daily Visit
  Notes workflow + Care Timeline. Launched from in-progress
  encounters of type `follow_up` or `treatment_visit`; structured
  SOAP editor rendered at `/patients/:pid/clinical/follow-up/:nid`;
  surfaces as a chart card and in the chronological Care Timeline.
  - **Backend** under `services/clinical/`:
    - `notes_models.py` — Pydantic models: `NoteSubjective` (interval
      history, pain scale 0–10, `pain_change` better/worse/same/
      fluctuating, functional change, home-care adherence yes/partial/no
      + notes), `NoteObjective` (repeatable `RegionFinding[]` with
      palpation / ROM summary / notes, reassessment summary, optional
      Vitals), `NoteAssessment` (`response_to_care`
      improving/plateau/regressing/new_complaint + clinical impression),
      `NotePlan` (repeatable `TreatmentEntry[]` kinds adjustment /
      modality / soft_tissue / exercise / other with segments /
      technique / modality / region / duration_min; regions_treated
      chip list; home-care reinforcement; next-visit plan +
      recommended_interval_days). REQUIRED_FIELDS drives completeness
      scoring.
    - `notes_router.py` — endpoints under `/api`:
      - `GET /patients/{pid}/clinical/notes` (list; `status_in` +
        `episode_id` filters)
      - `POST /patients/{pid}/clinical/notes` — create from
        `encounter_id`. One note per encounter (non-cancelled);
        duplicate returns 200 + `X-Note-Existed: true` header.
        Optional `copy_forward_from_note_id` seeds the new note's
        structured sections from a prior signed note.
      - `GET/PATCH /patients/{pid}/clinical/notes/{nid}` — PATCH
        blocks on signed (409).
      - `POST .../copy-forward` — explicit; non-destructive by
        default, `force=true` overwrites. Rejects unsigned source
        (400) and cross-patient source (400).
      - `POST .../mark-sign-ready` / `.../unmark-sign-ready` —
        draft ↔ sign_ready transitions.
      - `POST .../sign` — terminal; assigns `visit_number` =
        prior-signed-count-within-episode + 1.
      - `GET .../narrative` — SOAP-formatted rendering with
        `FOLLOW-UP / DAILY VISIT NOTE` header and
        `SUBJECTIVE (S)` / `OBJECTIVE (O)` / `ASSESSMENT (A)` /
        `PLAN (P)` sections. Empty sections omitted.
      - `GET /patients/{pid}/clinical/care-timeline` — chronological
        merge of encounters + initial exams + follow-up notes with
        kind-specific deep-link paths, sorted date-desc.
      - `POST /appointments/{aid}/clinical/notes` — convenience
        launch that reuses the active non-cancelled encounter on
        that appointment.
    - Summary endpoint now exposes live `notes.{total, open}` where
      `open = draft + sign_ready`.
  - **Access + audit**: reads `admin|doctor|staff`, writes
    `admin|doctor` + `require_reauth`. Tenant isolation via
    `scoped_filter` — cross-tenant probes 404. Every mutation emits
    both a global `audit_logs` row and a patient-scoped
    `clinical_audit_events` row (`follow_up_note.created`,
    `follow_up_note.updated`, `follow_up_note.copy_forward`,
    `follow_up_note.signed`).
  - **Indexes** in `core/db.py`: `clinical_follow_up_notes` on
    `(tenant_id, encounter_id)` UNIQUE,
    `(tenant_id, patient_id, date_of_service)`,
    `(tenant_id, status)`, `(tenant_id, episode_id)`.
  - **Frontend**:
    - `pages/clinical/FollowUpNoteEditor.jsx` — full-page editor
      at `/patients/:pid/clinical/follow-up/:nid`. Structured
      widgets per SOAP section; completeness meter header with
      missing-field chips (click-to-focus); Save / Copy-forward /
      Mark sign-ready / Unmark / Sign / View narrative toolbar.
      Copied-forward fields render with a yellow "Copied forward"
      badge per-field. Read-only signed banner post-sign.
    - `pages/clinical/FollowUpNotesCard.jsx` — list card on
      Clinical tab with status / visit # / provider / completeness
      meter per row.
    - `pages/clinical/CareTimelineCard.jsx` — chronological
      timeline merging encounters + initial exams + follow-up
      notes; kind-specific icons + deep links.
    - `pages/clinical/ClinicalTab.jsx` — new `stat-notes` tile
      in summary row; mounts FollowUpNotesCard + CareTimelineCard;
      Phase-2 placeholders for follow-notes + timeline removed.
    - `pages/clinical/EncountersCard.jsx` — `follow_up` /
      `treatment_visit` encounters now expose
      `encounter-start-note-{id}`; `new_patient_exam` /
      `re_evaluation` continue to expose `encounter-start-exam-{id}`.
    - `App.js` route:
      `/patients/:pid/clinical/follow-up/:nid`.
  - **Tests**: `backend/tests/test_clinical_phase5.py` — 12
    cases covering full lifecycle + copy-forward semantics +
    care-timeline merging + tenant isolation + reauth. Phase
    1+2+4 regression 35/35 green.
  - **Test-ids**: `stat-notes`, `clinical-notes-card`,
    `notes-empty`, `notes-list`, `note-row-{id}`,
    `note-row-{id}-status`, `note-row-{id}-visit`,
    `note-row-{id}-completeness`, `encounter-start-note-{id}`,
    `follow-up-note-editor`, `note-status-badge`,
    `note-visit-number`, `note-completeness`,
    `note-completeness-score`, `note-missing-list`,
    `note-missing-{field}`, `note-section-subjective/objective/
    assessment/plan`, `note-interval-history`, `note-pain-scale`,
    `note-pain-change`, `note-adherence`, `note-functional-change`,
    `note-region-findings`, `note-region-{i}-body/palpation/rom/notes`,
    `note-region-add`, `note-reassessment`, `note-vitals-bp`,
    `note-vitals-pulse`, `note-response-to-care`,
    `note-clinical-impression`, `note-treatment-list`,
    `note-treatment-{i}-kind/segments/technique/modality/region/
    duration/remove`, `note-treatment-add`, `note-regions-treated`,
    `note-home-care`, `note-next-visit-plan`, `note-interval-days`,
    `note-save-btn`, `note-copy-forward-btn`, `note-mark-ready-btn`,
    `note-unmark-ready-btn`, `note-sign-btn`, `note-narrative-btn`,
    `note-narrative-dialog`, `note-narrative-text`,
    `note-signed-banner`, `note-copy-forward-dialog`,
    `copy-forward-source-{id}`, `copy-forward-force`,
    `copy-forward-submit-btn`, `note-copied-{field-id}`,
    `care-timeline-card`, `care-timeline-list`,
    `care-timeline-empty`, `timeline-entry-{kind}-{id}`,
    `timeline-open-{kind}-{id}`.

## [Unreleased — previously merged]

### Added
- **Clinical module — Phase 4 (2026-02-22).** Initial Exam workflow:
  structured, signable, one-per-encounter initial evaluation record
  launched from the calendar → encounter → exam pipeline, rendered
  under Patient Profile > Clinical.
  - **Backend** new module `services/clinical/`:
    - `exam_template.py` — system default `default-initial-exam-v1`
      with three sections (history / examination / assessment),
      snapshotted into every exam at create so template evolution
      never mutates signed exams.
    - `exams_models.py` — `ExamHistory` (11 free-text H&P fields),
      `ExamExamination` (vitals + observation / posture / gait /
      palpation / segmental findings + structured `RangeOfMotion`
      across cervical/thoracic/lumbar/shoulders/hips +
      `OrthopedicTest[]` with positive/negative/equivocal results +
      `MuscleStrengthEntry[]` graded 0–5 with side + neurologic /
      sensory-reflex narratives), `ExamAssessment` (functional
      limitations, summary, impression, treatment recommendations),
      `NewDiagnosisDraft` (ICD-10 drafts materialized at sign time).
    - `exams_router.py` — endpoints under `/api`:
      - `GET /clinical/exam-templates/default`
      - `GET /patients/{pid}/clinical/exams` (list; `status_in`
        filter)
      - `POST /patients/{pid}/clinical/exams` — create from
        encounter. `prefill_from_chart=true` (default) copies
        `clinical_history` into empty exam.history fields and
        auto-selects active diagnoses. One-exam-per-encounter:
        duplicate create returns 200 + `X-Exam-Existed: true`
        header + the existing exam.
      - `GET/PATCH /patients/{pid}/clinical/exams/{eid}` — PATCH
        blocks on signed status (409); cross-patient diagnosis_ids
        → 400.
      - `POST .../prefill` — explicit non-destructive re-pull from
        the chart; only fills empty fields; updates
        `prefilled_from_chart_at`.
      - `POST .../mark-sign-ready` + `.../unmark-sign-ready` — draft
        ↔ sign_ready transitions; wrong-status → 409.
      - `POST .../sign` — terminal. Materializes `new_diagnoses`
        into `clinical_diagnoses` rows with ICD-10 uppercasing,
        case-insensitive de-dup on (code, body_region, laterality)
        against active problem list, onset_date copied from the
        encounter date-of-service, one-primary-per-episode
        enforcement across both existing + newly-materialized rows.
        Double-sign / sign-after-close → 409.
      - `GET .../narrative` — Initial-Exam-oriented rendering
        (NOT SOAP): `INITIAL EXAMINATION` header, HISTORY /
        EXAMINATION / ASSESSMENT & PLAN sections, inline
        structured vitals / ROM / orthopedic tests / muscle
        strength, DIAGNOSES block with primary flagging. Empty
        sections are omitted.
    - Summary endpoint now exposes live `initial_exams.{total, open}`
      where `open = draft + sign_ready`.
  - **Access + audit**: reads gated by `admin|doctor|staff`, writes
    by `admin|doctor` + `require_reauth`. Tenant isolation via
    `scoped_filter` — cross-tenant GET/PATCH/sign all return 404.
    Every mutation emits both a global `audit_logs` row AND a
    patient-scoped `clinical_audit_events` row (events:
    `initial_exam.created`, `initial_exam.updated`,
    `initial_exam.prefilled`, `initial_exam.signed`).
  - **Indexes** in `core/db.py`: `clinical_initial_exams` on
    `(tenant_id, patient_id, date_of_service)`,
    `(tenant_id, encounter_id)`, `(tenant_id, status)`.
  - **Frontend**:
    - `pages/clinical/InitialExamEditor.jsx` — full structured
      editor driven by the frozen `template_snapshot`. Widgets for
      vitals, ROM, orthopedic tests, muscle strength, existing
      diagnoses, new diagnosis drafts; narrative dialog; save /
      mark-sign-ready / unmark / sign actions with UX-correct
      enable/disable (save disabled when clean; sign disabled
      while dirty). `exam-signed-banner` replaces the editable form
      after sign.
    - `pages/clinical/InitialExamsCard.jsx` — rendered on the
      Clinical tab: lists every exam with status / date / provider
      with direct navigation to the editor.
    - `pages/clinical/ClinicalTab.jsx` — stat row leads with
      live `stat-exams` tile (open count).
    - `pages/clinical/EncountersCard.jsx` — each in-progress
      encounter now carries a `encounter-start-exam-{id}` action
      that POSTs `/clinical/exams` and navigates to the editor.
    - `App.js` route:
      `/patients/:id/clinical/initial-exam/:examId`.
  - **Tests**: `backend/tests/test_clinical_phase4.py` — 11
    cases covering create-from-encounter with auto-fill + frozen
    template, prefill-from-chart, idempotent one-exam-per-encounter,
    cancelled-encounter reject, PATCH structured round-trip +
    cross-patient diagnosis_ids 400, explicit prefill preserves
    provider edits, mark-sign-ready/unmark/sign transitions, sign
    materializes new_diagnoses with ICD-10 uppercase + de-dup +
    primary uniqueness, narrative rendering, summary counts live,
    cross-tenant 404, reauth required on writes. Phase 1+2
    regression 24/24 green.
  - **Test-ids**: `stat-exams`, `clinical-exams-card`,
    `encounter-start-exam-{id}`, `initial-exam-editor`,
    `exam-status-badge`, `exam-section-{id}`, `exam-vitals-bp`,
    `exam-vitals-pulse`, `exam-rom-{region}-{movement}`,
    `exam-ortho-row-{i}`, `exam-ms-row-{i}`,
    `exam-existing-dx-{id}`, `exam-new-dx-row-{i}`,
    `exam-new-dx-add`, `exam-save-btn`, `exam-mark-ready-btn`,
    `exam-unmark-ready-btn`, `exam-sign-btn`, `exam-prefill-btn`,
    `exam-narrative-btn`, `exam-narrative-dialog`,
    `exam-narrative-text`, `exam-signed-banner`.

### Infra / build
- **`libmagic1` is a runtime requirement.** `services/patient/
  documents_router.py` imports `python-magic` for MIME sniffing on
  uploads. The container base image must ship the `libmagic1`
  system package so uvicorn can cold-start. If the preview returns
  502 on boot, run
  `sudo apt-get update && sudo apt-get install -y libmagic1 && sudo supervisorctl restart backend`
  and add `libmagic1` to the Dockerfile / bootstrap script.

## [Earlier — Phase 3 through iteration 25]

### Added
- **Clinical module — Phase 3 (2026-02-21).** Appointment-first encounter
  launch infrastructure. Providers launch documentation from the
  appointment; the clinical record stays patient-owned. No full SOAP /
  exam note forms yet — plumbing only.
  - New backend router
    (`services/clinical/encounters_router.py` + `encounters_models.py`)
    mounted on both `/api/appointments/{aid}/clinical/*` (launch +
    lookup) and `/api/patients/{pid}/clinical/encounters/*`
    (authoritative chart surface):
    - `POST /appointments/{aid}/clinical/encounters` — launch;
      idempotent (returns `{encounter, existed: bool}` with 201 or 200).
    - `GET /appointments/{aid}/clinical/encounter` — fetch existing
      non-cancelled encounter for an appointment.
    - `GET /patients/{pid}/clinical/encounters` + filters.
    - `GET/PATCH /patients/{pid}/clinical/encounters/{eid}`.
    - `POST .../encounters/{eid}/complete` and `.../cancel`.
  - Encounter types: `new_patient_exam`, `follow_up`, `re_evaluation`,
    `treatment_visit`.
  - Lifecycle statuses: `in_progress → completed | cancelled`.
  - Frozen `appointment_snapshot` captured at launch (patient/provider/
    location/start/end/status/reason) so post-launch appointment edits
    NEVER mutate the chart encounter record.
  - Exception workflow: cancelled appointments require
    `exception_reason` AND `admin|doctor` role. Resulting encounter
    carries `is_exception=true`, `exception_reason`,
    `exception_invoked_by`, `exception_invoked_at`, and the original
    `appointment_status_at_launch`. Staff are blocked (403).
  - `GET /api/appointments/{id}` now projects `clinical_encounter_id`
    and `clinical_encounter_status`.
  - Summary endpoint exposes live `encounters.{total, open}` counts.
  - Writes require reauth; every mutation audited to both
    `audit_logs` AND `clinical_audit_events` (scoped chart projection).
  - Tenant isolation verified — cross-tenant probes return 404.
  - Indexes added for `clinical_encounters` in `core/db.py`.
  - **Frontend**:
    - `pages/clinical/EncounterLaunchDialog.jsx` — opened from
      BookDialog's new `appt-launch-encounter-btn` with a Stethoscope
      icon. Picks encounter type (auto-inferred from reason),
      optional episode (any status), and — for cancelled
      appointments — a required exception reason. Routes to
      `/patients/{pid}?tab=clinical&encounter={eid}` on success; shows
      an "existing encounter" banner if the POST comes back with
      `existed=true`.
    - `pages/clinical/EncountersCard.jsx` — new live card on the
      Clinical tab. Lists encounters with type, status, duration,
      provider, episode, exception flag. Inline complete/cancel
      transitions. Highlights the encounter whose id matches the
      `?encounter=` query param. "Appointment" button deep-links to
      the scheduling page on the correct day.
    - `pages/clinical/ClinicalTab.jsx` — summary row leads with a
      live `stat-encounters` tile (in-progress count); `EncountersCard`
      renders below the Diagnoses card.
    - `pages/PatientDetail.jsx` — tabs now URL-synced via
      `?tab=...`; deep-links from Launch land on the Clinical tab.
    - `pages/scheduling/SchedulingPage.jsx` + `DayView.jsx` —
      Day/Week/Month views all route cancelled appointments through
      BookDialog so admins/doctors can invoke the exception-launch
      workflow. Day view's cancelled tile now has a clickable
      "Canceled · Open" pill (`scheduling-day-appt-open-{id}`) while
      the underlying slot remains freely re-bookable.
  - **Tests**: `backend/tests/test_clinical_phase3.py` — 9 cases
    covering context freeze, idempotent relaunch, chart visibility,
    cancelled-without-reason 409, cancelled-with-reason 201 +
    exception flags, staff blocked from exception path, cross-tenant/
    cross-patient episode 400, complete/cancel lifecycle, PATCH on
    non-in-progress blocked (409), tenant isolation, reauth required
    on writes, summary reflects live encounter counts.
  - **Test-ids**: `appt-launch-encounter-btn`,
    `encounter-launch-dialog`, `encounter-existing-banner`,
    `encounter-exception-banner`, `encounter-exception-reason`,
    `encounter-type-select`, `encounter-episode-select`,
    `encounter-open-existing-btn`, `encounter-launch-submit-btn`,
    `clinical-encounters-card`, `encounter-filter-status`,
    `encounters-empty`, `encounters-list`, `encounter-row-{id}`,
    `encounter-row-{id}-status`, `encounter-row-{id}-exception`,
    `encounter-open-appt-{id}`, `encounter-complete-{id}`,
    `encounter-cancel-{id}`, `encounter-complete-dialog`,
    `encounter-cancel-dialog`, `stat-encounters`,
    `scheduling-day-appt-open-{id}`.

- **Clinical module — Phase 2 (2026-02-21).** Intake & History integration
  + Diagnoses / Problem List under Patient Profile > Clinical. Chart-first;
  no exam or follow-up workflows yet.
  - Backend: new routers `services/clinical/history_router.py` and
    `services/clinical/diagnoses_router.py`.
  - **History endpoints** (`/api/patients/{pid}/clinical/history`):
    - `GET` — auto-seeds on first access from the most recent completed
      intake form. Field-level traceability via `field_meta[<key>] =
      {source, source_form_id, updated_at, updated_by}`.
    - `PATCH` — partial; any supplied field flips to
      `source="provider_edit"`. Unsupplied keys untouched.
    - `POST /import` — explicit, non-destructive re-import. Preserves
      provider-edited fields; returns `imported_fields` / `skipped_fields`
      / `source_form_id`. Rejects drafts (409) and missing form (409).
  - **Diagnoses endpoints**
    (`/api/patients/{pid}/clinical/diagnoses[/{id}[/resolve|/reactivate]]`):
    create / list with `status_in` + `episode_id` filters / get / patch /
    resolve (with optional resolution notes, defaults resolved_date to
    now) / reactivate (blocked with 409 if already in target state).
    Fields: ICD-10 code (upper-cased), label, status (active/resolved),
    is_primary, body_region, laterality (left/right/bilateral/midline),
    chronicity (acute/subacute/chronic), onset_date, resolved_date,
    resolution_notes, notes, optional `episode_id` (any episode — active,
    on-hold, or closed). `is_primary=True` auto-uniqued within
    `(patient, episode_id-or-null, status=active)`.
  - **Summary**: `GET /clinical/summary` now returns live `diagnoses`
    counts + `history_present` flag.
  - **Access**: reads `admin|doctor|staff`, writes `admin|doctor` +
    `require_reauth`. Every mutation audited to `audit_logs` AND the
    patient-chart-scoped `clinical_audit_events` collection (events:
    `history.updated`, `history.imported`, `diagnosis.created`,
    `diagnosis.updated`, `diagnosis.resolved`, `diagnosis.reactivated`).
  - **Tenant isolation**: cross-tenant probes return 404.
  - **Frontend**: two new cards rendered inside the Clinical tab —
    `pages/clinical/IntakeHistoryCard.jsx` (20+ editable fields, per-field
    source badges, Re-import button) and `pages/clinical/DiagnosesCard.jsx`
    (problem-list with status + episode filters, add/edit dialog, inline
    resolve/reactivate, primary star badge). `ClinicalTab.jsx` pruned the
    two corresponding Phase-2 placeholders and now surfaces live
    `stat-diagnoses` and `stat-history` in the Clinical Summary row.
  - **Tests**: `backend/tests/test_clinical_phase2.py` — 15/15 passing.
    Phase 1 suite still 9/9.
  - Test-ids: `clinical-history-card`, `history-import-btn`,
    `history-edit-btn`, `history-save-btn`, `history-cancel-btn`,
    `history-last-imported`, `history-field-{key}`,
    `history-input-{key}`, `clinical-diagnoses-card`, `dx-new-btn`,
    `dx-filter-status`, `dx-filter-episode`, `dx-icd10`, `dx-label`,
    `dx-episode`, `dx-body-region`, `dx-onset`, `dx-laterality`,
    `dx-chronicity`, `dx-is-primary`, `dx-notes`, `dx-submit-btn`,
    `dx-row-{id}`, `dx-edit-{id}`, `dx-resolve-{id}`, `dx-reactivate-{id}`,
    `dx-resolve-dialog`, `dx-resolve-notes`, `dx-resolve-submit-btn`,
    `stat-diagnoses`, `stat-history`, `dx-list`, `dx-empty`.

- **Clinical module — Phase 1 (2026-02-21).** New `services/clinical/`
  backend module + new **Clinical** tab in Patient Profile. Establishes the
  patient-chart ownership model: the Patient Profile is the longitudinal
  home of the clinical record; appointments will be the operational
  encounter launch point in Phase 2+. Phase 1 ships the architecture base
  so every downstream clinical entity can attach without rework.
  - **Episode/case CRUD** (`clinical_episode_cases` collection):
    - `GET/POST /api/patients/{id}/clinical/episodes`
    - `GET/PATCH /api/patients/{id}/clinical/episodes/{eid}`
    - `POST /api/patients/{id}/clinical/episodes/{eid}/close`
    - `POST /api/patients/{id}/clinical/episodes/{eid}/reopen`
    - Case types: `new_patient_eval`, `injury_episode`, `recurrence`,
      `maintenance`, `mva`, `workers_comp`, `personal_injury`.
    - Statuses: `active`, `on_hold`, `closed`, `archived`.
    - Fields: responsible_provider_id, patient_id, tenant_id, location_id,
      title, chief_complaint, mechanism_of_injury, onset_date, start_date,
      end_date, closed_reason, tags, plus a `metadata` dict and per-doc
      `history[]` for future linkage fields.
  - **Clinical summary** endpoint
    `GET /api/patients/{id}/clinical/summary` — aggregates episode counts
    (total + open) and returns zero-shaped placeholders for notes,
    diagnoses, treatment_plans, outcomes, media, and encounter_links so
    the UI contract stays stable as Phase 2+ CRUD ships.
  - **Downstream models** declared up-front in `services/clinical/models.py`
    (not yet CRUD'd): `ClinicalNoteBase`, `DiagnosisBase`,
    `TreatmentPlanBase`, `OutcomeEntryBase`, `ClinicalMediaBase`,
    `EncounterLinkBase`, `ClinicalAuditEventBase`. Their collections get
    `(tenant_id, patient_id, episode_id)` indexes on day one so Phase 2+
    doesn't need migrations.
  - **Clinical audit trail**: every episode mutation writes one row to the
    new `clinical_audit_events` collection (patient-scoped projection of
    the global audit stream) so future chart-history UI can render fast
    without filtering the global stream per request.
  - **Access control** — reads gated by
    `require_role("admin", "doctor", "staff")`; writes by
    `require_role("admin", "doctor")` + `require_reauth` (matches
    medical-record reauth posture). Tenant isolation via `scoped_filter`;
    cross-tenant probes always return 404, never 403.
  - **Frontend**: new `pages/clinical/ClinicalTab.jsx` rendered as a
    **Clinical** tab inside Patient Profile (between Intake and Documents).
    Renders the Clinical Summary stats card row, an Episodes & Cases list
    with create / close / reopen dialogs, and ten dashed **Phase 2**
    placeholder cards covering Intake & History, Diagnoses, Initial Exam,
    Follow-up Notes, Re-Exams, Treatment Plans, Imaging & Clinical Media,
    Outcomes, Care Timeline, and Billing Readiness. Writes leverage the
    existing global `ReauthGate` for step-up retry.
  - Test-ids: `patient-clinical-tab`, `tab-clinical`,
    `clinical-summary-stats`, `clinical-new-episode-btn`,
    `clinical-episodes-list`, `clinical-episodes-empty`,
    `clinical-episode-{id}`, `clinical-episode-{id}-close-btn`,
    `clinical-episode-{id}-reopen-btn`, `clinical-episode-create-dialog`,
    `clinical-episode-close-dialog`, `clinical-placeholder-{name}`.
  - Tests: `backend/tests/test_clinical_phase1.py` — 9/9 passing.

### Changed
- **Settings navigation split — standalone pages for Appointment Types,
  Payers, and Fee Schedules (2026-02-21).** `ClinicSettings.jsx` is now
  focused exclusively on clinic profile (identity, contact, address,
  timezone, notes) plus hours of operation. The three business-catalog
  managers that previously lived in the same page — `AppointmentTypesManager`,
  `PayersManager`, and `FeeSchedulesManager` — have been promoted to
  dedicated pages with their own routes, sidebar entries, and
  deep-linkable URLs:
  - `pages/AppointmentTypesPage.jsx` → `/settings/appointment-types`
    (testid `appointment-types-page`, sidebar testid
    `nav-appointment-types`, `ClipboardList` icon).
  - `pages/PayersPage.jsx` → `/settings/payers`
    (testid `payers-page`, sidebar testid `nav-payers`, `Landmark` icon).
  - `pages/FeeSchedulesPage.jsx` → `/settings/fee-schedules`
    (testid `fee-schedules-page`, sidebar testid `nav-fee-schedules`,
    `Coins` icon).
  All four pages remain admin-only and sit inside the collapsible
  **Settings** group in `components/layout/navConfig.js`. Routes are
  registered in `App.js` behind `Shell roles={["admin"]}`. No API
  changes, no behavior changes to the underlying managers — purely a
  navigation + IA refactor so each catalog is directly addressable and
  Clinic Settings stops scrolling past three separate tables.

### Added
- **Versioned intake save wiring + wizard extraction (2026-02-21).**
  - `PatientWizardDialog` (scope=`intake`) now saves through
    `PATCH /api/patients/{patient_id}/intake-forms/{form_id}` instead of
    the legacy flat `patient.clinical_intake` blob. Two new actions
    appear on step 4 when editing an intake form: `Save draft`
    (`wizard-save-draft-btn`) and `Save & complete`
    (`wizard-save-complete-btn`, sets `status: "completed"`).
  - `IntakeFormsTab` now exposes an `Edit draft`
    (`intake-form-edit-<id>`) button on every draft row and the parent
    `PatientDetail` tracks `editingIntakeForm` to seed the wizard with
    that form's `clinical_intake` + `case_details`.
  - **Refactor:** `PatientWizardDialog` + its 4 step renderers were
    moved out of `pages/Patients.jsx` into a dedicated
    `components/patient-wizard/PatientWizardDialog.jsx`. `Patients.jsx`
    now owns only the search/recent-patients page. Both
    `Patients.jsx` and `PatientDetail.jsx` import the wizard from the
    new path. Pure-logic helpers (`patientWizardLogic`) are unchanged
    and still covered by the 39-test Node suite.

### Changed
- **API-wide PATCH migration (2026-02-20).** Every resource update now
  uses `PATCH` semantics with `exclude_unset=True` — only fields
  explicitly present in the request body are applied; omitted fields
  are left alone; passing `null` clears the field. `PUT` routes are
  **removed** (not aliased). Converted endpoints:
  `PATCH /api/patients/{id}`,
  `PATCH /api/patients/{id}/records/{rec_id}/coding`,
  `PATCH /api/appointments/{id}`,
  `PATCH /api/appointment-types/{id}`,
  `PATCH /api/clinic-profiles/{id}`,
  `PATCH /api/privacy/communication-preferences`,
  `PATCH /api/billing/payers/{id}`,
  `PATCH /api/billing/insurance-policies/{id}`,
  `PATCH /api/billing/fee-schedules/{id}/lines`,
  `PATCH /api/billing/claims/{id}/header`,
  `PATCH /api/billing/claims/{id}/diagnoses`,
  `PATCH /api/billing/claims/{id}/lines`,
  `PATCH /api/billing/claims/{id}/assignment`,
  and `PATCH /api/billing/denial-work-items/{id}`.
  Frontend callers (`Patients.jsx`, `ClinicSettings.jsx`,
  `AppointmentTypesManager.jsx`, `BookDialog.jsx`, `useClaims.js`,
  `useBillingAdmin.js`, `useRemittance.js`) all migrated to
  `api.patch(…)`. Backend test suites (15 files) migrated.

### Added
- **Multi-version patient intake forms (2026-02-20).**
  New collection `patient_intake_forms` + sub-router
  `services/patient/intake_forms_router.py`. A patient can now hold
  many intake snapshots (one per encounter / injury / revisit).
  - `GET  /api/patients/{id}/intake-forms` — newest first
  - `POST /api/patients/{id}/intake-forms` — creates a draft;
    `seed_from_patient: true` (default) pre-fills from the patient's
    current `clinical_intake` + `case_details` so the wizard opens
    with what we already know
  - `GET  /api/patients/{id}/intake-forms/{form_id}`
  - `PATCH /api/patients/{id}/intake-forms/{form_id}` — partial
    update (only supplied fields); `status: "completed"` stamps
    `captured_at` / `captured_by` and locks the row immutable
  - `DELETE /api/patients/{id}/intake-forms/{form_id}` — drafts only
  Per-form `clinical_intake`, `case_details`, and `notes` are
  encrypted at rest as JSON blobs (same AES-GCM scheme the rest of
  the patient PHI uses). Cross-tenant isolation enforced via
  `scoped_filter` on every read + write.

  Frontend `IntakeFormsTab` now fetches live forms from the new
  endpoint and shows actual version labels (`Draft · v1`, `v2`, …)
  with the real `captured_at` timestamp. "New intake form" calls
  the backend, creates a draft seeded from the patient, and
  refreshes the list.

### Tests
- `backend/tests/test_patient_intake_forms.py` — 5 passing
  (empty → seeded create, version increments, PATCH exclude_unset
  semantics, draft → completed stamps captured_at + locks, delete
  draft vs 409 on completed, cross-tenant isolation).

- **Patient Detail — Intake vs Documents split (2026-02-20).**
  - The old "Intake" tab contained only consents + upload rows —
    which is really document management. Renamed to
    **"Documents & Attachments"** and kept the consent + upload UI.
  - Introduced a brand-new **"Intake"** tab that shows actual
    intake *forms*: chief complaint, onset, pain score, pain areas,
    symptom count, case-type badge, notes. Uses the shared
    `DateRangeFilter` (defaults to last 30 days, quick picks 60/90
    /180/365/Today/Custom) so users can scope the intake history
    like they can on Records and Appointments. The backend
    currently exposes a single intake blob per patient; the UI is
    already list-shaped (`{id:'current', version_label:'Current
    intake'}`) so switching to a multi-form backend later is a
    drop-in.
  - A new **"New intake form"** CTA on the Intake tab opens the
    existing clinical-intake wizard (same break-glass / unmask /
    re-auth flow).
  - Removed **Edit patient** and **Edit intake** buttons from the
    page-top toolbar. Their equivalents now live inside the tabs:
    "Edit patient info" on Overview, "New intake form" on Intake.
    Top toolbar keeps only Mask/Unmask, Export JSON, and Soft-delete.
  - New testids: `tab-documents`, `patient-intake-forms`,
    `intake-new-form-btn`, `intake-date-range`, `intake-form-{id}`,
    `intake-forms-empty`.

- **Patient Overview — full read-only patient info (2026-02-20).**
  The Overview tab used to show only Address / Emergency contact /
  Intake notes, which felt disjointed from the Edit Patient wizard.
  It now mirrors the wizard sections end-to-end: **Identity**
  (first/middle/last/preferred name, DOB, sex at birth, gender,
  pronouns, marital status, preferred language), **Contact** (mobile
  /home/work phone, email, preferred contact method, comms
  consents), **Address**, **Emergency contact**, **Care
  assignment** (assigned provider resolved via `/auth/providers`,
  preferred location, referral source), **Employment**,
  **Responsible party / Guarantor** (auto-collapses to "Same as
  patient" when applicable), and an **Insurance** summary (primary
  + secondary). An "Edit patient info" CTA on the Overview tab
  opens the same wizard used by the toolbar button and handles
  break-glass/unmask flow. All fields gracefully fall back to "—"
  when empty or masked.

- **Patient Detail IA refactor — tabs + date-range filter (2026-02-20).**
  - `PatientDetail.jsx` no longer renders every section in one long
    vertical scroll. The header + meta row stay on top; below that,
    the page is split into six tabs: **Overview**, **Intake**,
    **Medical Records**, **Appointments**, **Insurance**,
    **Billing & Ledger**. All existing actions (Mask, Edit patient,
    Edit intake, Export JSON, Soft-delete) remain in the top-right
    toolbar exactly as before.
  - New reusable `components/DateRangeFilter.jsx` with quick picks
    (last 30 / 60 / 90 / 180 / 365 days, Today, All time, Custom
    start/end). Default is Last 30 days. Wired into the Medical
    Records tab (filters by `recorded_at`) and Appointments tab
    (filters by `start_time`). Filtering is client-side so no API
    changes are required.
  - All previous `data-testid`s preserved (`record-new-btn`,
    `record-{id}`, `record-charge-capture-{id}`, etc). New testids:
    `patient-detail-tabs`, `tab-overview|intake|records|appointments|insurance|billing`,
    `records-date-range`, `appointments-date-range`,
    `{range}-preset-30|60|90|180|365|today|all|custom`,
    `{range}-from`, `{range}-to`.

- **Sidebar IA refactor (2026-02-20).** Left navigation regrouped into
  four semantic sections — **Operations**, **Financial**, **Settings**,
  **Governance** — driven by a new config module
  `frontend/src/components/layout/navConfig.js` (section grouping,
  display labels, icons, routes, role gating). Labels normalized:
  `AR aging → A/R Aging`, `Post remit → Remittance Posting`,
  `Import 835 → 835 Imports`, `Clinic settings → Clinic Settings`,
  `Audit log → Audit Log`, `Permission matrix → Permissions`,
  `Security config → Security Settings`, `Security → Security Dashboard`.
  Routes are unchanged. Settings + Governance are collapsible with
  state persisted in `localStorage` (`ccms.sidebar.collapsed`). Each
  group renders only when the current role has at least one visible
  item.

### Added
- **Billing Phase 6 — Bulk 835 import + patient statements (2026-02-20).**
  - Backend:
    - `services/billing/remittance_import.py` — 835 X12 parser + JSON
      parser (`schema: ccms.remit.import.v1`, max 2 MB). Staged uploads
      auto-match claims by `clm01` payer control number (primary) and
      patient control number (fallback), resolve payer by NM1/JSON
      name, and expose a `preview → commit` workflow that is idempotent
      and does NOT mutate the ledger until `POST
      /api/billing/remittances/import/{id}/commit`.
    - `services/billing/statement_delivery.py` — Reportlab-based
      patient statement PDF generator (clinic header, aged AR
      summary, per-invoice line items) + Resend email integration
      (falls back to `provider='mock'` when `RESEND_API_KEY` is
      unset so the flow stays testable in dev/preview).
    - Endpoints:
      `POST /api/billing/remittances/import/json`,
      `POST /api/billing/remittances/import/x12`,
      `POST /api/billing/remittances/import/{id}/commit`,
      `GET  /api/billing/remittances/import/{id}`,
      `GET/POST /api/billing/patients/{id}/statements`,
      `GET  /api/billing/patients/{id}/statements/{stmt_id}/pdf`,
      `POST /api/billing/patients/{id}/statements/{stmt_id}/send`.
    - Tenant isolation enforced on every import, PDF, and send call
      (cross-tenant access returns 404).
  - Frontend:
    - New `pages/billing/RemittanceImport.jsx` with dropzone upload,
      preview table, match-method pills, unresolved-payer banner,
      and commit button gated on `unmatched===0 && resolved_payer_id`.
    - New `pages/billing/PatientStatementsCard.jsx` embedded on
      `PatientLedgerPage` — generate statement, download PDF, send
      email. Uses semantic theme tokens only (no raw hex / tailwind
      color classes).
    - `AppShell` gains a persistent "Import 835" nav entry
      (`admin`, `staff`).
    - New `useRemittance.js` hooks:
      `uploadRemittanceImport`, `commitRemittanceImport`,
      `listStatements`, `generateStatement`, `emailStatement`,
      `statementPdfUrl`.
  - Tests: `backend/tests/test_billing_phase6.py` — 18 passing
    covering JSON import happy path, X12 parsing, unmatched-row
    commit block, unresolved-payer block, empty-upload rejection,
    PDF generation, mocked email path, email-missing rejection,
    and cross-tenant isolation for imports + PDF downloads.
    Frontend E2E validated via Playwright in iteration_29 — all
    Phase 6 flows pass (login → nav → upload → preview → commit
    gating; ledger → generate → PDF → email). Only UX nit: after a
    reauth 401, the user re-picks the file once (tracked as optional
    follow-up).

- **Billing Phase 5 follow-up — Denial taxonomy (iteration 29).**
  - New `services/billing/denial_categories.py` mapping ANSI CARC
    codes to six operational categories: `coding`, `eligibility`,
    `authorization`, `timely_filing`, `duplicate`, `other` (with
    stable labels + `normalize_code()` + `derive_category()`).
  - Remittance posting auto-tags every newly-created denial work item
    with the derived category. Line-level denials respect the
    line's own `denial_category` if the payer provided one.
  - `GET /api/billing/denial-work-items` now accepts `status_in` and
    `category` filters (unknown category → 400).
  - `PUT /api/billing/denial-work-items/{id}` accepts
    `denial_category` for operator override. Unknown category → 400.
  - `GET /api/billing/denial-work-items/category-summary` returns a
    full row per category with `count` + `amount_cents`.
    `include_closed=true` toggles between the active lens (default:
    open/in_progress/escalated) and the full ledger.
  - Frontend: `DenialsQueue.jsx` now renders six clickable category
    summary cards (act as one-tap filters), a Category filter
    dropdown, and a Category column with color-coded pills. The
    work dialog gains a Category override field.
  - Hooks: `useDenialWorkItems({status, category})` and
    `useDenialCategorySummary()`.

### Tests
  - `backend/tests/test_billing_phase5_denial_taxonomy.py` —
    14 passing:
    - `derive_category` happy paths + normalization (`97`, `co97`,
      `CO-97` all map to `coding`)
    - Unknown & empty codes fall through to `other`
    - Auto-tagging during remittance post (claim-level + line-level
      + unspecified codes)
    - List filter by category + unknown → 400
    - Operator override via PUT + unknown → 400
    - Category summary emits all categories with stable zeros;
      increments on new denial; `include_closed` toggle.
  - Combined Phase 3 + 4 + 5 + taxonomy pytest: **69 passing**.

### Added
- **Billing Phase 5 — Remittance posting, denials, AR aging, statements
  (iteration 28).**
  - New collections: `remittances`, `remittance_claims`,
    `remittance_lines`, `statements`.
  - Backend endpoints (all tenant-scoped, auditable):
    - `POST /api/billing/remittances` — atomic post of header +
      per-claim + per-line rows + payment (method=`era_posting`) +
      allocations + contractual adjustments + denial work items.
      Enforces: payer consistency across claims, sum(paid) ==
      header total, claim must exist on the same tenant. Advances
      the claim's Phase-4 state machine (submitted → accepted →
      paid/partially_paid/denied) and rolls patient balance forward
      via the standard `_recompute_invoice_balance` helper (no
      hidden mutations).
    - `GET /api/billing/remittances/{id}` — header + claims + lines.
    - `PUT /api/billing/denial-work-items/{id}` — status / assignee
      / resolution notes. Uses the canonical denial state machine
      (`open → in_progress → resolved/escalated → closed`).
    - `GET /api/billing/ar/aging` — buckets `0-30 / 31-60 / 61-90 /
      91-120 / 120+` based on `invoice.issued_at` (fallback
      `created_at`). Optional `payer_id` filter.
    - `GET /api/billing/ar/aging/by-payer` — aggregates aging grouped
      by payer (self-pay surfaced as a row).
    - `POST /api/billing/patients/{pid}/statements` — snapshots open
      invoices into a plain-text statement row. Scaffolding only
      (no PDF, no email yet). List + read endpoints included.
  - Permission registry: added `remit.post` (high sensitivity,
    financial) and `denial.work` to super_admin + billing_specialist.
  - Per user choices: patient responsibility is left on the invoice
    (choice 1b — no extra line minted); denial work items are
    auto-created with `assigned_to_id=null` (choice 2).

### Frontend
  - `RemittancePosting.jsx` — two-section form: remittance header
    + eligible-claims picker with per-row paid/contractual/patient
    /denied/denial-code inputs, live `Total paid` recompute.
  - `RemittanceDetail.jsx` — header + per-claim + per-line tables.
  - `DenialsQueue.jsx` — filterable work queue with inline status +
    assignee + resolution-notes dialog (`denial-edit-*` testids).
  - `ArAgingReport.jsx` — overall bucket bars + per-payer breakdown.
  - `useRemittance.js` — hooks & helpers for remittances, denials,
    aging, statements.
  - Sidebar entries (`AppShell.jsx`): Claims / Denials / AR aging /
    Post remit. Routes wired in `App.js` with RBAC.

### Tests
  - `backend/tests/test_billing_phase5.py` — 17 passing:
    - Aging math (bucket boundaries, date parsing, roll-up)
    - Statement body rendering (deterministic, full balance check)
    - Remittance posting: full-pay closes invoice & advances claim
      submitted→accepted→paid; partial-pay + contractual leaves
      patient balance; denial opens work item auto-unassigned;
      mismatched header total rejected; cross-payer claim rejected
    - Denial mutations: assign + progress status audited; illegal
      transition rejected; unknown assignee rejected
    - AR aging endpoints: bucket label invariance; payer grouping
    - Statements: generate + list + read
    - Tenant isolation: cross-tenant post rejected; cross-tenant
      statement read returns 404
  - Combined Phase 3 + 4 + 5 pytest: **55 passing**.

### Added
- **Billing Phase 4 — Claim submission scaffolding, outcomes, work
  queues, timeline (iteration 27).**
  - New `claim_submissions` collection (tenant-scoped) tracking every
    manual submission attempt with method (`manual_paper`,
    `manual_portal`, `batch_file`), external reference, payload
    (JSON + 837P preview), submitter and timestamp.
  - Claim status machine expanded: added `pending` state between
    `submitted`/`accepted` and terminal adjudication. Transitions:
    submitted → accepted / rejected / pending; accepted → pending /
    paid / partially_paid / denied; pending → accepted / paid /
    partially_paid / denied / rejected.
  - New endpoints (all tenant-scoped, auditable):
    - `POST /api/billing/claims/{id}/submissions` — creates a
      submission record and advances `ready → submitted`. Rejects
      non-ready claims with 409.
    - `GET /api/billing/claims/{id}/submissions` — returns submission
      history (heavy payload fields omitted).
    - `GET /api/billing/claims/{id}/submissions/{sub_id}/payload` —
      returns the full JSON + 837P preview.
    - `POST /api/billing/claims/{id}/submissions/{sub_id}/outcome` —
      records `accepted/rejected/pending/paid/partially_paid/denied`
      and auto-transitions the claim through the canonical state
      machine. Captures payer_reference, denial_code, paid_cents.
      Refuses to re-record on an already-closed submission.
    - `GET /api/billing/claims/{id}/timeline` — merged chronology of
      history entries, scrubber runs and submissions (with outcomes).
    - `PUT /api/billing/claims/{id}/assignment` — sets `assigned_to`
      user id; rejects unknown assignees with 400.
    - `GET /api/billing/claims/queues/{queue_name}` — three named
      queues (`pending-submission`, `rejected`, `follow-up`) with
      filters `payer_id`, `age_days`, `status_in`, `assigned_to`.
      Follow-up rule = `(submitted && last_submission_at < cutoff)
      OR (rejected/denied && updated_at < cutoff)` with
      `DEFAULT_FOLLOWUP_DAYS = 14`.
  - Payload builders in new `services/billing/submission.py`:
    - `build_json_payload()` — flat schema `ccms.claim.v1`.
    - `build_x12_837p_preview()` — lightweight ANSI X12 segments
      (ISA / GS / ST / BHT / NM1 / CLM / HI / LX / SV1 / DTP / SE / GE / IEA).

### Frontend
  - `ClaimWorkflow.jsx` (new) — Assignee input, New submission dialog
    (method, external reference, notes), Outcome dialog (auto-hides
    denial code / paid fields based on selected outcome), Payload
    dialog with JSON / 837P preview tabs, submissions table.
  - `ClaimsQueue.jsx` gains tabs (All / Pending / Rejected / Follow-up)
    and a filter bar (status, payer, age > days, assignee). Named
    queues call the new `/queues/{name}` endpoint; the All tab keeps
    using the original listing endpoint so its behavior is unchanged.
  - `useClaims.js` adds hooks/helpers for submissions, outcomes,
    timeline, assignment, and a `useClaimQueue()` hook.

### Tests
  - `backend/tests/test_billing_phase4.py` — 22 passing
    (status transition matrix including new `pending`, payload
    builders, submission lifecycle, outcome lifecycle, timeline
    merging, assignment + audit, named queue filters, tenant
    isolation).
  - Combined billing Phase 3 + Phase 4 pytest suite: **38 passing**.

### Added
- **Billing Phase 3 — Claims UI wired into app (iteration 26).**
  - New routes `GET /billing/claims` (queue) and `GET /billing/claims/:id`
    (detail) registered in `App.js` under `admin|doctor|staff` RBAC.
  - Sidebar nav entry **Claims** (`FileStack` icon) added in `AppShell.jsx`.
  - `BillingDashboard` header now exposes a secondary "Claims queue"
    button alongside "View invoices".
  - `InvoiceDetail` gains a **Generate claim** action
    (`invoice-generate-claim-btn`) that calls
    `POST /api/billing/claims/from-invoice/{id}` and navigates to the
    resulting claim detail. Disabled on terminal invoices; server
    rejects self-pay/no-payer invoices with a descriptive 409.
  - Orphaned pages `ClaimsQueue.jsx` and `ClaimDetail.jsx` (authored in
    iteration 25) are now fully reachable and themed via the semantic
    `claimStatusTone` tokens in `useClaims.js`.

### Added
- **Billing Phase 2 — Insurance setup & encounter charge capture
  (iteration 25).** Bridges clinical encounters to billable artifacts.
  - **Fee schedules**: new collections `fee_schedules` (tenant-scoped,
    `kind=self_pay|payer`, only one active self-pay per tenant) and
    `fee_schedule_lines` (upsert-by-`(code_type, code)`). Endpoints
    `GET/POST /api/billing/fee-schedules`,
    `GET/PUT /api/billing/fee-schedules/{id}/lines`.
  - **Price resolution precedence** (in `services/billing/charge_capture.py`):
    payer-specific schedule (when insurance + payer) → active self-pay
    schedule → `billing_code_catalog.default_price_cents` → zero
    (surfaced as a warning).
  - **Medical record coding + signing**:
    `PUT /api/patients/{pid}/records/{rid}/coding` accepts
    `{procedures[], diagnoses[], responsibility}` (coding.update
    permission). `POST .../sign` is one-way (idempotent, signed
    records are immutable; captured records cannot be re-coded).
    Super_admin now carries `coding.update` as a bootstrap grant.
  - **Charge capture**:
    `GET /api/billing/encounters/{record_id}/charge-candidates` — dry
    run returns `{lines, warnings, total_cents, can_capture,
    responsibility, payer_id, policy_id}` without side-effects.
    `POST /api/billing/encounters/{record_id}/capture` — commits:
    validates record is signed, has procedures, insurance responsibility
    has an active primary policy. Creates a `draft` invoice with
    `source_encounter_id`, `responsibility`, `payer_id`, `policy_id`
    metadata; each line carries `source_fee_schedule_id` +
    `price_source`. Record transitions to
    `charge_status=captured` with `charge_captured_invoice_id` link.
    **Strict tenant match** even for super_admin (platform admins
    scoped to a tenant cannot accidentally capture another tenant's
    encounters).
  - **Insurance policy lifecycle**: added
    `PUT /api/billing/insurance-policies/{id}` and
    `DELETE .../{id}` (soft-deactivate to `status=inactive`).

- **Billing Phase 2 UI**.
  - `PatientInsuranceManager` — embedded on `PatientDetail` above the
    ledger. Add / edit / deactivate policies, rank picker,
    subscriber relationship, effective & termination dates, warning
    pill when no active primary policy exists.
  - `ChargeCaptureDialog` — launched from each medical-record row via
    a new "Code & capture" button. Procedures editor (CPT code,
    units, modifier), diagnoses editor (ICD-10), responsibility
    selector, live charge preview with price-source attribution per
    line, Save coding → Sign record → Capture charges flow. Shows
    status chips (Signed / Captured).
  - `PayersManager` in Clinic Settings — CRUD on payers
    (commercial / Medicare / Medicaid / workers comp / auto / self-pay
    / other), payer code, electronic payer ID, remit method.
  - `FeeSchedulesManager` in Clinic Settings — create + edit rates.
    Lines editor upserts by code, rate in dollars (stored cents).

### Tests
- `backend/tests/test_billing_phase2.py` — **13/13 passing**. Covers
  fee schedule uniqueness + line upsert idempotency, insurance policy
  update + deactivate, coding locked on signed records, idempotent
  sign, preview using self-pay schedule, insurance missing-policy
  warning & capture block, unsigned record 409, self-pay happy path
  (incl. recapture 409), payer schedule wins over self-pay for
  insurance responsibility, Sunrise admin cannot preview a Default
  encounter (strict tenant match), audit row emitted on capture.
- All previous billing tests remain green: **53/53 passing**
  (`test_billing.py` 40 + `test_billing_phase2.py` 13).

### Dependencies
- None.

- **Billing Phase 1 — Invoices, Patient Ledger, Payments (iteration 24).**
  User-facing billing core on top of the foundation shipped in iteration
  23:
  - **Balance math**: `_recompute_invoice_balance()` is now the single
    source of truth for an invoice's `balance_cents`. It sums live
    allocations (skipping void/failed payments), subtracts processed
    refunds **proportionally** across the invoices a payment touched,
    subtracts adjustments, and auto-advances the invoice status
    (`issued ↔ partially_paid ↔ paid`). Runs on every payment create,
    adjustment, refund, and payment status change. Invoice transitions
    `paid → partially_paid / issued` are now legal for this purpose.
  - **Refunds** post immediately as `processed`, flip the payment to
    `refunded` / `partially_refunded`, re-inflate touched invoice
    balances, and guard against over-refund (sum of existing + new
    refunds cannot exceed the original payment).
  - **Post-hoc allocation**: new `POST /api/billing/payments/{id}/allocations`
    lets an unallocated payment be applied across invoices later;
    allocations cannot exceed the payment's remaining unapplied cents.
  - **Cash / check auto-capture**: payments with method `cash` or
    `check` now post as `captured` directly (money-in-hand at the
    front desk). Card / ACH stays `pending` until gateway confirmation.
  - **Void invoice**: new `POST /api/billing/invoices/{id}/void`
    (requires `billing.void`, MFA) with a mandatory reason; zeroes the
    balance, blocks further adjustments, emits
    `billing.invoice.voided`.
  - **Patient ledger**: new `GET /api/billing/patients/{id}/ledger`
    returns a chronological, denormalised row stream (charges,
    payments, refunds, adjustments, credits, voids) with a
    precomputed running balance and per-kind totals.
  - **RBAC**: `super_admin` picked up `payment.refund`,
    `adjustment.writeoff`, and `billing.void` with `MFA` (no APR) so
    the demo admin can drive the full lifecycle. `billing_specialist` /
    `clinic_manager` retain the full `MFA+APR` gate in production.
  - **Read routes**: list/get endpoints for payers, insurance
    policies, invoices, payments, claims, remittances, denial work
    items, and the ledger moved from `require_permission(...)` to
    `require_role("admin", "doctor", "staff")` — mirroring the
    pattern used by `clinic_profile` and `appointment_types`. This
    lets operators browse billing in the web UI without triggering an
    MFA reauth on every page. **Mutations still go through
    `require_permission()`** with the full authz matrix.

- **Billing UI (`/billing`).**
  - Dashboard (`/billing`) with outstanding-balance / lifetime-billed
    / payments-recorded stat cards, recent-invoices list, and
    recent-payments list.
  - Invoices list (`/billing/invoices`) with status filter and
    ID/patient text search.
  - Invoice detail (`/billing/invoices/:id`) with line items,
    subtotal / adjustments / balance totals cards, and inline actions:
    Issue, Post payment, Adjust / writeoff, Void.
  - `PostPaymentDialog` — multi-invoice allocation with auto-allocate
    (oldest invoice first), real-time remaining/over-allocation
    indicator, optional reference for check # / card last-4.
  - `PatientLedgerCard` — embedded on `PatientDetail` and as a
    standalone route at `/billing/patients/:id/ledger`. Shows the
    chronological ledger with type-tagged rows, per-row running
    balance, and a four-up totals footer.
  - Shared money utilities (`/utils/money.js`) with
    `formatCents` / `parseDollarsToCents` / `clampCents` /
    `sumAmountCents`, and their Jest tests (6 passing).
  - New sidebar nav entry "Billing" visible to admin / doctor / staff.

- **Global ReauthGate — app-wide MFA auto-retry.** A new singleton
  `ReauthProvider` installs an axios response interceptor that
  detects 401s flagged `Re-authentication required` (or carrying the
  `X-Reauth-Required: 1` header) and opens the shared `ReauthDialog`.
  When the user confirms, the *original* request is replayed once
  with the fresh reauth cookie. This means every MFA-gated mutation
  (post payment, apply adjustment, void invoice, refund, write
  medical record, delete patient, …) now has zero per-feature reauth
  wiring — the interceptor catches them globally. Also fixes a
  latent bug flagged by the testing agent where
  `GET /api/patients/{id}/documents` silently failed on
  `PatientDetail` due to the same missing reauth flow.

### Tests
- `backend/tests/test_billing.py` — **40 passing** (added 10 Phase 1
  tests: partial-payment balance progression, adjustment closing,
  post-hoc allocation, allocation-overrun rejection, void + downstream
  lock, patient ledger chronology & totals, cross-tenant ledger denial,
  full-refund reversal with payment → refunded & invoice → issued,
  writeoff success / refund success for admin with reauth, staff
  cannot refund).
- `frontend/src/utils/money.test.js` — **6 passing** (formatCents /
  parseDollarsToCents / clampCents / sumAmountCents edge cases).

### Dependencies
- None.

## [Earlier — iteration 23 billing foundation]

### Added
- **Billing Service foundation (iteration 23).** Introduces the canonical
  billing domain model: payers, patient insurance policies, invoices
  (with sibling invoice_lines), payments + payment_allocations, refunds,
  adjustments (writeoffs / discounts / courtesy / contractual), claims
  (with sibling claim_diagnoses, claim_lines, claim_line_modifiers),
  remittances, and denial work items. PostgreSQL-ready: UUID PKs,
  integer-cents money, no embedded child lists, status vocabularies
  encoded as enums. Lifecycle transitions enforced via
  `services.billing.transitions.advance()` (invoice, payment, claim,
  remittance, denial). New module at `backend/services/billing/` with
  `models.py` (Pydantic + status maps), `transitions.py` (legal-move
  validator), `router.py` (placeholder routes wired to the canonical
  RBAC policy), and `seed.py` (system default CPT + modifier catalog).
  Routes at `/api/billing/{payers,insurance-policies,invoices,payments,
  refunds,adjustments,claims,remittances,denial-work-items}` with full
  tenant scoping, semantic audit rows
  (`billing.*.created`, `billing.*.status_changed`, `billing.*.viewed`,
  `billing.*.list_viewed`), and per-entity history append. New indexes
  for every billing collection keyed on `(tenant_id, ...)`. No
  clearinghouse integration yet — payer/claim adapters will live
  outside the canonical model.
- **RBAC — super_admin bootstrap grants for billing CRUD.** SA now
  carries `charge.create`, `payment.collect`, `insurance.create`,
  `insurance.update`, `claim.read/create/submit/correct_resubmit` so
  the demo admin can drive the billing foundation end-to-end. High-risk
  money-moving actions (`payment.refund`, `adjustment.writeoff`,
  `billing.void`) remain behind `billing_specialist` / `clinic_manager`
  with MFA+APR as defined by the permission matrix.

### Dependencies
- None.

- **Scheduling — "Today" cell is now visually distinct on Week and
  Month views.** Previously the current day blended into the
  background. Now:
  - **Week view** today-cell: primary-tinted background
    (`bg-primary/5`), a `ring-2 ring-inset ring-primary` accent, a
    primary top-border on the header strip, and a small `Today` pill
    next to the day label so the column is unmistakable.
  - **Month view** today-cell: same `bg-primary/5` +
    `ring-2 ring-inset ring-primary` treatment. The existing
    primary-pill date number stays.
  Active cancelled-pill gating + half-column rendering are unchanged.

- **Scheduling + Clinic Settings — Appointment types.** Introduces a
  tenant-scoped catalog of bookable visit types with a per-type default
  duration (minutes). Backend: new service at
  `services/appointment_types/` with admin-only CRUD, soft-delete
  (`is_active=false`) + reactivate. Endpoints mounted at
  `/api/appointment-types` (list | create | update | deactivate |
  reactivate). Case-insensitive name uniqueness per tenant; 422 on
  duration outside 5–480 minutes.
  Frontend:
  - `ClinicSettings` now embeds `AppointmentTypesManager` — inline
    table with create / edit / deactivate / reactivate.
  - `BookDialog` gains an **Appointment type** dropdown (sources
    active types only). Selecting a type fills the Reason field with
    the type name and recomputes End = Start + default duration.
    Subsequent Start edits keep recomputing End until the user
    manually edits End — after which the manual override is preserved.
    "Custom (free text)" keeps the legacy 30-min behavior. Reschedule
    mode treats the saved end-time as already manually set.
  - New `useAppointmentTypes` hook for the modal (fetches only while
    the dialog is open, `active_only=true`).
  Backend tests: `tests/test_appointment_types.py` — 7/7 passing
  covering CRUD lifecycle, duration bounds, blank-name, case-insensitive
  uniqueness, RBAC (doctor & staff read-only), tenant isolation, and
  `active_only` filter.

- **Scheduling — Cancelled indicators now strictly gated by the
  "Show canceled" toggle.** Previously the per-day `cnl` pill on Week
  view and the `canceled` badge on Day view rendered whenever any
  cancelled appointment existed, regardless of toggle state. They now
  render only when `includeCancelled === true`. Month view no longer
  shows any cancelled pill at all (the toggle is scoped away from
  Month/Year views, so surfacing the indicator there would be
  inconsistent). Year view was already pill-free. An `sr-only`
  element preserves the `scheduling-month-cancelled-count-{date}`
  test-id for accessibility-aware automated tests.

- **Scheduling — Cancelled appointments now occupy only the right
  half of their Day-view column.** Rendered via `pointer-events-none`
  so the left half of the same time band stays a fully clickable
  booking surface — staff can rebook the exact same slot without
  visually losing the cancelled history. Active (scheduled) blocks
  continue to occupy the full column width.
- **Scheduling — "Show canceled" toggle is now scoped to Day and
  Week views only.** The toggle is hidden in Month and Year views
  where cancelled appointments are already summarised via the
  dedicated `cnl` pill on each cell. The underlying
  `includeCancelled` state still persists across view switches.

- **Scheduling — Week-view closed-day shading.** `WeekView` now
  reads the active clinic-hours via the already-wired
  `extractDaySpan(hours, date)` helper and, for each day cell:
  - Closed days render a muted `bg-muted/40` background, a
    `scheduling-week-closed-{date}` "CLOSED" pill, `data-closed="true"`,
    and the `+` quick-add is suppressed (the global New-appointment
    CTA still lets staff book exception appointments).
  - Open days render a tiny mono `scheduling-week-hours-{date}`
    label (e.g. `9:00–17:00`) under the header and a quick-add
    that pre-fills with the configured open time rather than a
    blanket 09:00.
- **Provider filter** (`pages/scheduling/ProviderFilter.jsx`). New
  `Select` dropdown mounted in `SchedulingToolbar`
  (`data-testid="scheduling-provider-filter"`). Fetches
  `/auth/providers` once, offers "All providers" + one row per
  provider; selecting a provider flows through `providerId` state
  in `useScheduling` → every subsequent list and counts request
  carries `provider_id=...`. Doctor role still auto-scoped at the
  backend when no explicit provider is chosen.
- **Clinic Settings save — auto-recover POST → PUT on 409.** The
  `ClinicSettings.onSave` handler now catches a 409
  "already-exists" response from `POST /api/clinic-profiles` and
  transparently retries as `PUT /api/clinic-profiles/{id}` —
  fixing the rare race where the UI loaded with a 404 (unconfigured
  state) but a profile had been created in the meantime. Also
  improved error surfacing (joined Pydantic detail array) and a
  `console.error` so future regressions leave a diagnostic
  breadcrumb.
- **Regression sweep** (`testing_agent_v3_fork` iteration 19):
  **25/26 items green**, 14/14 backend pytests still green.
  The single flagged issue (ClinicSettings save round-trip) was
  traced to the POST/409 edge case above and is now resolved
  end-to-end (fresh create path + update path both verified via
  Playwright: Sun switch persists across reload on create;
  Sat switch persists across reload on update).

- **Calendar weeks now start on Sunday.** `dateHelpers.startOfWeek`
  switched from ISO-week (Monday) to locale-common Sunday; the
  `WEEKDAY_SHORT` / `WEEKDAY_LONG` constants reordered accordingly.
  `YearView` mini-months: leading-pad calc updated to
  `first.getDay()` (Sunday = 0) and `MINI_WEEKDAYS` reordered to
  "S M T W T F S". Downstream effects are automatic: Week view,
  Month view grid and YearView mini-months all now render
  **Sun → Sat** columns. Backend `day_of_week` remains the ISO
  0 = Monday convention (no data migration needed); the
  `extractDaySpan` helper in `useClinicHours` already normalises
  `JS Date.getDay()` to that scheme.
- **Clinic Settings UI shipped** (`pages/ClinicSettings.jsx`, route
  `/settings/clinic`, admin-only, sidebar entry "Clinic settings"
  with a `Building2` icon). Unblocks Task 7 for non-engineers.
  - Pre-fills existing profile from `GET /api/clinic-profiles/{loc}`;
    on 404 renders an "unconfigured" notice with sensible defaults
    (uses the location's name + timezone as starting points).
  - Fields: name, address line 1/2, city, state, postal, primary +
    secondary phone, email, website, timezone (12 IANA options), notes.
  - Hours table rendered in **Sunday → Saturday** order (matches the
    calendar views) with per-day open/closed toggle + 15-min step
    time inputs. Display↔backend day_of_week mapping handled in the
    page (Sun=backend 6; Mon=0; … Sat=5) so the ISO-week backend
    contract is unchanged.
  - Client-side validation mirrors the backend (HH:MM format,
    close > open per interval) and surfaces structured Pydantic
    errors in toast. `POST` on unconfigured location, `PUT` on
    update. Location picker shown when the admin sees multiple
    tenant locations.

- **Scheduling automated test coverage (Task 13).**
  - New `backend/tests/test_scheduling_workflows.py` — 3 tests:
    create → range-list → counts reconcile (including reschedule +
    cancel round-trip and cancelled-appt still counted); patient
    cannot book for other patients; patient counts never leak
    cross-tenant. Combined with the earlier
    `test_clinic_profile.py` (6) and `test_appointment_counts.py`
    (5), the scheduling workstream now has **14/14 green pytest
    tests** covering both API surfaces (appointment CRUD + counts
    aggregation + clinic profile CRUD + RBAC + tenant isolation).
  - Frontend regression by `testing_agent_v3_fork`: **16/18
    items green**. Verified: legacy route redirects
    (/appointments → /scheduling, /calendar → /scheduling),
    sidebar single-entry, all 4 view toggles + date navigation,
    range-label updates per view, week/month/year count badges
    match data, **/counts-vs-/appointments network routing is
    exactly as per Task 10** (Day view hits list endpoint;
    Week/Month/Year hit counts endpoint), quick-add pre-fill at
    09:00, day-slot pre-fill, day fallback 07:00–20:00 with
    no-hours notice, outside-window banner + expand toggle,
    BookDialog field set, cancel AlertDialog wiring.
  - **Fixes from the agent report:**
    - `DayView` outside-window banner copy: grammar fix —
      "1 appointment … is hidden" / "N appointments … are hidden".
    - Week + Month quick-add buttons now render on
      `focus-visible` as well as `group-hover:flex`, so keyboard
      users can reach them without relying on pointer hover.

- **Scheduling migration completed (Task 12).** Final sweep
  confirming no stale entry points or broken links survived the
  Appointments → Scheduling collapse:
  - Left-nav exposes a single **Scheduling** item for every role
    (admin / doctor / staff / patient); no "Appointments" or
    "Calendar" leftovers.
  - Legacy `/appointments` and `/calendar` routes redirect with a
    React-Router `<Navigate replace>` to `/scheduling`; query
    strings survive the redirect. Verified via Playwright.
  - Dashboard's "view all" and "book first appointment" CTAs both
    point at `/scheduling`.
  - Legacy `pages/Appointments.jsx` and `pages/Calendar.jsx` files
    were already deleted in the original migration (Task 1); grep
    confirms zero remaining imports or JSX references.
  - Deliberately preserved: the "Appointments" section heading on
    **PatientDetail** (per-patient appointment history is a
    legitimate data entity label, not a route) and the shadcn
    `components/ui/calendar.jsx` primitive (internal date-picker
    used by dialogs, unrelated to the former Calendar page).
  - Copy / tooltip / empty-state sweep: zero "appointments page" /
    "calendar page" phrases anywhere in frontend or backend.

- **Scheduling — direct actions from calendar cells (Task 11).**
  - **Week & Month cells** now expose a compact hover-reveal `+`
    quick-add button (`data-testid="scheduling-week-add-{date}"` /
    `scheduling-month-add-{date}"`) that opens the booking dialog
    pre-filled for that date at 09:00. Admin / doctor / staff only;
    hidden when `canBook` is false.
  - **Month view** refactored from an outer `<button>` cell to a
    `<div>` with independently-focusable children — fixes the
    previously invalid nested-button structure and makes each
    appointment preview clickable. Scheduled previews open the
    reschedule dialog; cancelled previews navigate to Day view
    (since rescheduling a cancelled appt is not meaningful). The
    day-number and count badge are separate buttons that both open
    Day view. The "+N more" affordance still routes to Day view.
  - No behaviour change to Day view's empty-slot click — it
    continues to open the booking dialog pre-filled to that 15-min
    slot (Task 6). Cancel affordance remains available inside the
    reschedule dialog (Task 6).
  - Acceptance criteria all met: empty-slot click creates an appt
    with correct prefilled date/time; appointment click opens the
    correct workflow; day click from summary views navigates to
    Day view.

- **Scheduling summary views now use count aggregation (Task 10).**
  New backend endpoint `GET /api/appointments/counts` runs a single
  MongoDB aggregation pipeline that buckets `start_time` by the
  caller-supplied IANA `tz` via `$dateToString`, groups by local
  date, and returns `[{date, count, samples[]}]`. An
  `include_samples` query parameter (0..10, default 0) decides how
  many lightweight sample appointments are returned per day; samples
  are hydrated with patient + provider names in one extra round-trip
  (same pattern as the list endpoint). Tenant scoping, location
  scoping, and role-based filters (doctor → own provider_id,
  patient → own patient_id) all mirror the list endpoint verbatim.
  Response is cached 30s per
  `(role, tenant, range, tz, samples, provider_id, patient_id,
  location_id, status)` cache key.
  - Week view now fetches counts + 3 samples per day.
  - Month view fetches counts + 2 samples per day.
  - Year view fetches counts-only (365/366 dates, 0 samples).
  - Day view still pulls the full list endpoint since it needs
    complete timing, phone, reason, notes etc. The detail fetch in
    `useScheduling` is now skipped when `view !== "day"` — no more
    duplicate payload on view toggles.
  - Client-side in-memory cache on `useAppointmentCounts` keyed by
    `(view, range, tz, samples, providerId)` so quick view hops
    don't refire the request.
  - Cancel / reschedule / create paths invalidate **both** the
    detail and counts caches so the UI stays consistent.
  - Backend tests (`backend/tests/test_appointment_counts.py`,
    5/5 green): shape + totals reconcile with the list endpoint,
    tenant isolation, `include_samples` cap (0 and 11→422 bound),
    `tz` bucketing smoke-test, patient-role auto-scoping.

- **Scheduling Day view now respects clinic hours (Task 9).** A new
  `useClinicHours` hook resolves the caller's active location via
  `/api/tenancy/me/context` → then pulls `hours[]` from
  `/api/clinic-profiles/{locationId}`. `DayView` uses this to compute
  its visible window as **(open − 2h) → (close + 2h)**, snapped to
  15-minute boundaries.
  - Examples: Wednesday 08:00–18:00 → timeline 06:00–20:00. Saturday
    09:00–13:00 → timeline 07:00–15:00.
  - **Closed days** render a `Clinic closed` pill in the header plus a
    warning banner; the timeline still shows a nominal 07:00–19:00
    window for exception viewing, and any appointments that exist on
    that day are never silently hidden — the banner reveals a
    "Show all appointments" button that expands the window to
    enclose every appointment present.
  - **Outside-window appointments** on open days trigger the same
    expand button (previously just a passive banner). A "Collapse to
    clinic hours" link returns to the configured window.
  - **Missing profile**: when a location has no clinic profile, the
    Day view falls back to 07:00–20:00 and surfaces a subtle
    "Clinic hours not configured" notice pointing admins at the
    upcoming Clinic Settings page.
  - Implementation details: window snapping to 15-min boundaries +
    minimum 15-min window; per-hour labels drawn via computed offsets
    so arbitrary open/close minutes (e.g. 08:30–17:45) render
    correctly; auto-scroll now respects `startM`/`endM` changes when
    the user jumps days.

- **Clinic Profile service** (new `services/clinic_profile/`). Stores
  one profile per location (1:1 with `locations.id`) carrying clinic
  name, address line 1 / 2, city, state, postal code, country,
  primary & secondary phones, email, website, IANA timezone, free-
  form notes, and per-weekday hours of operation. Hours are modelled
  as a list of 7 `DayHours` (0 = Monday) each with `is_closed` and a
  list of `HoursInterval` (`open_time` / `close_time`, HH:MM 24-h) —
  an intervals list so lunch breaks and future holiday overrides can
  be layered in without a breaking change.
  - Endpoints at `/api/clinic-profiles/*`: list, read (by profile id
    OR location id), create (`POST`), update (`PUT`), delete
    (`DELETE`). Read is gated to `admin | doctor | staff`; mutations
    are `admin`-only.
  - Tenant-scoped on every call via `scoped_filter` + `stamp_for_write`;
    location-scoped for non-tenant-wide users. Cross-tenant probes
    return `404` (never `403`) so the endpoint never leaks existence.
  - Validation: HH:MM 24-hour format, `close > open` per interval, no
    overlapping intervals within a day, `is_closed` forbids intervals,
    exactly one entry per `day_of_week` 0..6, valid IANA `timezone`.
  - Audit rows: `clinic_profile.list_viewed`, `clinic_profile.read`,
    `clinic_profile.created`, `clinic_profile.updated` (with field
    list), `clinic_profile.deleted`. Every mutation also appends an
    in-document `history[]` entry.
  - Indexes: unique `(tenant_id, location_id)` + `(tenant_id, name)`.
  - Tests — `backend/tests/test_clinic_profile.py` — **6/6 green**:
    happy-path CRUD + two-interval lunch break, invalid hours
    (format / ordering / overlap / missing day / bad tz /
    `is_closed` + intervals), 409 on duplicate profile per location,
    doctor-can-read-not-write + scoped-staff-can't-see-other-location,
    Sunrise↔Default cross-tenant isolation, audit rows for
    create/update/delete.

- **Scheduling Day view rebuilt as a 15-minute timeline (Task 6).**
  The table-based DayView is replaced by a vertical timeline from
  07:00–20:00 (placeholder clinic hours; 52 slots × 16 px). Each slot
  is a focusable `<button>` — clicking opens the booking dialog
  pre-filled with that slot's start time (via the new `defaultStart`
  prop on `BookDialog`). Hour boundaries carry a darker 2 px border,
  half-hour marks are dashed, quarter-hour marks are subtle — so
  operators can read slot density at a glance.
  - Appointment blocks are absolutely positioned by
    `(start - dayStart) * slotHeight / 15` with a side-by-side column
    layout for overlapping clusters (classic interval scheduling on
    first-free column). Height respects duration with a
    `SLOT_HEIGHT - 2` minimum.
  - Blocks show **patient name, patient phone, start time**, and —
    when the block is tall enough — provider and reason. Cancelled
    appointments render in the destructive-soft palette with a
    line-through. Clicking a block opens the reschedule dialog.
  - **"Cancel appointment"** affordance reintroduced inside
    `BookDialog` in reschedule mode as a ghost-destructive footer
    button; clicking it closes the dialog and raises the existing
    `AlertDialog` confirmation. No new API.
  - A live **current-time indicator** (destructive pill + 2 px bar)
    overlays the timeline when viewing today and the clock is inside
    the visible window; updates every 60 seconds.
  - Timeline auto-scrolls to "now" on mount (or 08:00 on non-today
    days). An out-of-window banner surfaces any appointments that
    fall outside the default 07:00–20:00 window so they're never
    silently hidden.
- **`patient_phone` added to `AppointmentPublic`** (scheduling
  service). The hydration helper now pulls the patient's `phone`
  scalar alongside `first_name`/`last_name` in one Mongo read. Legacy
  records carry `phone` directly; grouped-intake records get it
  back-filled at write time (see PRD §21), so no new decryption path
  is needed. Only staff/doctor/admin + the patient themselves can
  reach the appointments endpoint, so no new audit surface either.

- **Scheduling Month view polish (Task 4)** — `MonthView` cells now
  show up to 2 compact appointment previews (time + patient) and a
  `+N more` hint when the day has more. Count badge remains in the
  cell header. Empty days stay visually calm with an en-dash. Today's
  date is rendered as a primary-filled pill. Adjacent-month filler
  cells are muted. Clicking any cell opens Day view for that date.
- **Scheduling Year view polish (Task 5)** — each day in every
  mini-month is now its own `<button>` that opens Day view for that
  date. Density tint has four buckets (0 / 1–2 / 3–4 / 5+) and the
  exact count is surfaced via `title` tooltip + `aria-label` for
  screen readers. The month header is now a separate `<button>` that
  jumps to Month view — avoiding the previous invalid nested-button
  structure. Per-month totals remain visible at the top-right of
  each card, so macro scanning still works at a glance.

- **Unified Scheduling module** — the separate `Appointments` table page
  and `Calendar` page are merged into a single `/scheduling` experience
  with Day / Week / Month / Year view toggles, shared date-navigation
  (`prev` / `today` / `next`), and a primary `+ New appointment` CTA.
  - Left-nav now shows one **Scheduling** item (icon `CalendarDays`)
    replacing the previous **Appointments** + **Calendar** entries.
  - Legacy routes `/appointments` and `/calendar` now redirect to
    `/scheduling` so bookmarks and deep links keep working.
  - Shared framework: `pages/scheduling/useScheduling.js` (view,
    date, visible range, provider filter placeholder, range-based
    appointment fetch with in-memory cache keyed by view/range,
    cache invalidation on write) + `pages/scheduling/dateHelpers.js`
    (Monday-first week math, month-grid expansion, label formatter)
    + `SchedulingToolbar`, `DayView`, `WeekView`, `MonthView`,
    `YearView`, `BookDialog`.
  - **Week view** renders a 7-day grid. Each cell shows weekday +
    date, a prominent count badge (`0` or `N appts`), up to three
    appointment previews, and a `+N more` link. Clicking the day
    header opens Day view for that date; clicking an appointment
    preview opens the reschedule dialog. Empty days render a dashed
    "No appointments" placeholder.
  - Month view is a Monday-first 6-row grid with per-day appointment
    count badges; clicking a cell opens Day view. Year view shows
    12 mini-month grids with per-day heat tint + per-month totals;
    clicking a month jumps to Month view.
  - Existing auth, permissions, audit, tenant scoping and appointment
    CRUD endpoints are untouched — the new views consume
    `GET /api/appointments?from=&to=` for range-based loading.

### Changed
- **Split patient wizard into two focused flows.** The previous
  4-step wizard mixed demographics, billing, clinical intake, and
  case/consents into a single form — confusing when reception just
  wanted to add a patient and returning staff just wanted to update
  intake.
  - **Add / Edit patient** — scope `"patient"`, visible steps 1–2
    only (Patient Info → Billing & Insurance). Used from the
    `/patients` page "+ New patient" action and the new
    **Edit patient** button on `PatientDetail.jsx`.
  - **Start / Edit intake** — scope `"intake"`, visible steps 3–4
    only (Clinical Intake → Case & Consents). Used from the new
    **Edit intake** button on `PatientDetail.jsx`. Edit-only — no
    "create" scenario for intake alone since intake lives on an
    existing patient record.
  - `PatientWizardDialog` now takes a `scope` prop (`"patient"`
    default or `"intake"`), dynamically titles the dialog (`"New
    patient"` / `"Edit patient"` / `"Edit intake"` / `"Start
    intake"`), counts steps within the visible slice ("Step 1 of 2"),
    and only runs hard validation on the patient scope. Intake scope
    allows partial saves — staff can return and complete later.
  - `PatientDetail.jsx` now renders both `PatientWizardDialog`
    instances with distinct open/close state
    (`editWizardOpen` + `intakeWizardOpen`), each keyed to its scope.
    Buttons carry matching `data-testid`s: `patient-edit-patient-btn`
    and `patient-edit-intake-btn`.
  - Draft autosave is only kept for the patient-scope **create**
    flow; intake and edit flows start from the server record with
    no local draft noise.

### Added
- **Patient Documents — inline thumbnails** for the three image-first
  categories (Insurance card front, Insurance card back, Driver's
  license / ID). `components/PatientDocumentsCard.jsx` now renders a
  `DocImageThumb` per image document that:
  - Streams the file over the authenticated
    `GET /api/patients/:id/documents/:id/download` endpoint (same
    path used for full download, which also emits an audit event).
  - Converts the blob response into a process-local
    `blob:` URL via `URL.createObjectURL`, renders it in an
    `<img loading="lazy" />`, and **revokes the URL on unmount** so
    no PHI lingers in memory or the browser tab's resource list.
  - Shows loading + error states (spinner; "Preview unavailable"
    fallback on fetch failure).
  - Wraps the image in a `<button>` with a visible focus ring so
    keyboard users can open the full-size view (re-uses the existing
    download helper → opens the authenticated blob in a new tab).
  - Falls back gracefully to a compact row when the stored file is
    not an image (e.g. PDF insurance card uploaded).
- The rest of the documents card (referral letter, imaging report,
  intake form, consent receipt, other) continues to use the compact
  row layout — image previews are reserved for categories where the
  visual scan-ability actually helps staff.
- **Chiro Software Theme System (Slate + Teal + Copper)** — adopted the
  binding design system defined in `/app/docs/theme/`:
  - `CHIRO_SOFTWARE_THEME_STANDARD.md` — brand standard.
  - `CHIRO_THEME_ENGINEERING_IMPLEMENTATION_SPEC.md` — engineering source
    of truth.
  - `CHIRO_UI_REVIEW_AND_COMPLIANCE_CHECKLIST.md` — pass/fail review tool.
  - `docs/theme/README.md` — index + rule of adherence.
- **Rewrote `frontend/src/index.css`** to the spec's three-layer token
  architecture: foundation palette (slate / teal / copper / status) +
  typography / spacing / radius / shadow primitives, semantic light +
  dark tokens (shadcn HSL channels + hex), and component alias tokens
  (`--sidebar-active-bg`, `--table-row-hover`, `--dialog-overlay`,
  `--badge-premium-bg`, …).
- **Extended `tailwind.config.js`** — semantic `surface`, `surface-2`,
  `surface-3`, `border-strong`, `success`, `warning`, `info`,
  `accent-strong`, and chart colors; radius scale (`xs`→`xl`); shadow
  scale (`xs`→`lg`); font families (`display`, `body`, `mono`).
- **Typography migration** — Outfit / Manrope / JetBrains Mono wired via
  CSS variables; headings auto-render in Outfit, body in Manrope.
- **Legacy sage utility classes preserved as brand-aliases** —
  `text-sage`, `bg-sage`, `surface-sage`, `text-strong`, `surface-raised`,
  etc. now point to the new slate+teal+copper values so the 22 existing
  pages inherit the new brand without a file-by-file rewrite. A
  future pass will migrate them to semantic Tailwind classes
  (`bg-primary`, `text-foreground`, `bg-card`) per the spec.

### Changed
- **Brand direction** — deprecated the sage + stone palette in favor of
  the premium Slate + Teal + Copper system. Primary brand color moves
  from `#7B9A82` (sage) to `#14757C` (teal-700) in light and `#4CB5BA`
  (teal-400) in dark. Accent warmth shifts to copper
  (`#FAF0EB` / `#6B432B`). Radius base raised from 2px to 8px to match
  the "refined, not playful" shape language.
- **Phase 2 theme-discipline sweep** — replaced **every remaining**
  raw hex (`bg-[#…]`, `text-[#…]`, `border-[#…]`, `accent-[#…]`) and
  raw Tailwind palette class (`stone-*`, `divide-stone-*`) across
  `frontend/src/**` with semantic tokens (`bg-primary`,
  `bg-destructive-soft`, `text-muted-foreground`, `text-warning`,
  `bg-info-soft`, `border-border`, `divide-border`, …). Touched:
  `ProtectedRoute`, `PatientDocumentsCard`, `Login`, `PasswordReset`,
  `Calendar`, `RoleManagement`, `Appointments`, `SecurityConfig`,
  `Security`, `AuditLog`, `Notifications`, `Elevation`,
  `PatientDetail`, `Compliance`, `AccessReview`, `Privacy`,
  `Patients`, `Register`, `Dashboard`, `PermissionMatrix`, `toast`.

### Added (theme guardrail)
- **Theme Preview page (`/settings/theme-preview`)** — a one-screen
  regression canary that renders every Shadcn primitive in its
  default / hover / focus / disabled / error states alongside the
  full semantic token palette and the typography specimen (Outfit
  display · Manrope body · JetBrains Mono technical). Light · Dark ·
  System parity can be confirmed from a single URL. Source:
  `frontend/src/pages/ThemePreview.jsx`; wired into `App.js` behind
  the standard AppShell.
- **Card primitive density pass** — `CardHeader`, `CardContent`,
  `CardFooter` default padding tightened from 24px (`p-6`) to 20px
  (`p-5`) per spec §6 compact operational density. No visual
  regression on Dashboard / Appointments / Compliance KPI tiles.
- **Compat alias deletion** — removed the now-unreferenced backwards-
  compat layer from `index.css`:
    - all legacy utility classes (`.surface-app`, `.surface-raised`,
      `.surface-muted`, `.surface-sage`, `.surface-sage-soft`,
      `.surface-warning`, `.surface-danger-soft`, `.surface-topbar`,
      `.text-strong`, `.text-muted-strong`, `.text-soft`, `.text-sage`,
      `.text-sage-deep`, `.text-danger*`, `.text-warning`, `.bg-sage`,
      `.bg-danger`, `.hover\:bg-sage-hover`, `.hover\:bg-danger-hover`,
      `.border-subtle`, `.border-strong`);
    - all legacy CSS variables (`--surface-app`, `--surface-raised`,
      `--surface-muted`, `--surface-sage*`, `--warning-surface`,
      `--surface-danger-soft`, `--sage-accent*`, `--danger-accent`,
      `--warning-accent`, `--text-strong`, `--text-muted`,
      `--text-soft`, `--text-danger*`, `--border-subtle`,
      `--border-strong`, `--chrome-topbar-bg`).
  - Re-pointed internal alias tokens (`--sidebar-fg`,
    `--sidebar-active-fg`, `--table-header-fg`) and the `::selection`
    color to the canonical `hsl(var(--foreground))` /
    `hsl(var(--muted-foreground))` references.
  - Result: `index.css` is ~40% smaller and speaks exactly one
    vocabulary — foundation primitives → semantic tokens → component
    aliases → three essential utilities (`font-display`, `font-body`,
    `font-mono`, `focus-ring`, `tabular-nums`).
- **Phase 3 — legacy alias retirement (2026-04-20)** — migrated every
  backwards-compat utility class across `frontend/src/**` to direct
  semantic Tailwind utilities. 762 instances swept in one atomic
  pass using word-boundary sed replacements:
    - `text-strong` → `text-foreground` (88×)
    - `text-muted-strong` → `text-muted-foreground` (215×)
    - `text-soft` → `text-muted-foreground/70` (26×)
    - `text-sage-deep`, `text-sage` → `text-primary` (53×)
    - `text-danger-strong`, `text-danger-soft`, `text-danger` → `text-destructive` (34×)
    - `surface-sage` → `bg-primary/10` (38×)
    - `surface-sage-soft` → `bg-primary/5` (1×)
    - `surface-muted` → `bg-muted` (26×)
    - `surface-app` → `bg-background` (21×)
    - `surface-warning` → `bg-warning-soft` (16×)
    - `surface-danger-soft` → `bg-destructive-soft` (21×)
    - `surface-topbar` → `bg-card/90 backdrop-blur` (1×)
    - `bg-sage`, `hover:bg-sage-hover`, `bg-danger`, `hover:bg-danger-hover`
      → `bg-primary`, `hover:bg-[var(--primary-hover)]`, `bg-destructive`,
      `hover:brightness-95` (79× combined)
    - `border-subtle` → `border-border` (102×)
    - `border-strong` → `border-border-strong` (14×)
  The only non-semantic raw strings still in feature code are the
  `--primary-hover`, `--dialog-overlay`, `--sidebar-active-*`,
  `--table-*`, `--badge-premium-*`, `--focus`, `--input-placeholder`,
  and `--calendar-slot-selected` CSS-variable references exposed by
  the theme layer itself. These are intentional — they consume alias
  tokens.

- **AppShell shell hardening** — Sidebar now reads the sidebar alias
  tokens (`--sidebar-bg`, `--sidebar-fg`, `--sidebar-active-bg`,
  `--sidebar-active-fg`, `--sidebar-active-indicator`) instead of
  inline `style={{ borderLeftColor: "var(--sage-accent)" }}` or
  generic `bg-background` / `bg-muted`. `font-['Outfit']` arbitrary
  classes migrated to the `font-display` utility. `text-white` on
  primary surfaces swapped for `text-primary-foreground` so dark-mode
  contrast stays correct.

- **Refactored every core Shadcn primitive** to match the spec:
  `button.jsx`, `input.jsx`, `textarea.jsx`, `select.jsx`, `card.jsx`,
  `dialog.jsx`, `dropdown-menu.jsx`, `tabs.jsx`, `badge.jsx`,
  `table.jsx`, `sonner.jsx`. Each now uses:
  - Semantic tokens only (no raw Tailwind palette, no hex) — controls
    consume `bg-surface`, `bg-card`, `bg-popover`, `text-foreground`,
    `text-muted-foreground`, `border-border` directly.
  - 8px radius on controls (`rounded-sm`), 12px on cards & dialogs
    (`rounded-lg`) per spec §7.
  - 40px default height on buttons and inputs (36/44 for sm/lg) per
    spec §6.
  - 600 weight on button labels, `font-display` on card/dialog titles,
    12px bold-uppercase headers on tables per spec §5.
  - Accessible 2px focus ring with offset against the local surface,
    driven by the `--focus` token, on every keyboard-focusable
    element (spec §10).
  - Tokenized row hover / selected via `--table-row-hover` and
    `--table-row-selected` alias tokens.
  - Copper `premium` badge variant using `--badge-premium-*` alias
    tokens — reserved for billing / admin emphasis per spec §9.
- **Dialog**: overlay now reads `--dialog-overlay` (2px backdrop
  blur), content sits on `bg-card` with `shadow-md` + 12px radius.
- **Sonner**: replaced the broken `next-themes` import with the
  app's own `ThemeContext`, so toasts flip with the user's Light /
  Dark / System preference. Added tokenized `success` / `warning` /
  `info` / `error` variant classes.
- **`scripts/check_theme.py`** — Python CI guard. Scans
  `frontend/src/**` for raw hex arbitrary values, forbidden Tailwind
  palette families (slate / gray / stone / blue / red / etc.), and
  inline `style={{ color: "#…" }}` usages. Exits non-zero on any
  violation. Exempts the theme layer (`index.css`,
  `tailwind.config.js`) and shadcn primitives
  (`components/ui/**`). Runs as part of pre-commit and a new
  `.github/workflows/theme-guard.yml` CI job.
- **`.github/workflows/theme-guard.yml`** — runs `check_theme.py`
  on every PR targeting main/master/develop.
- **`.githooks/pre-commit`** — now runs both `check_docs.py` and
  `check_theme.py --quiet`; blocks commits that introduce palette
  violations (bypass with `--no-verify`).
- **`.github/pull_request_template.md`** — new "Theme compliance"
  section; every UI-touching PR confirms light/dark parity, focus
  states, semantic token usage, and reference to the UI Review
  Compliance Checklist.

- **Tailwind config** — exposed `secondary.hover` token
  (`bg-secondary-hover` utility) to cover the tab/pill pressed state
  used by Privacy / Compliance / PasswordReset.

- **Patient lookup workflow** — the `/patients` page is no longer a
  full-list dump. New `GET /api/patients/search` endpoint with:
  - Global `q` plus per-field `name`, `phone`, `address`, `dob`.
  - SQL-style `%` wildcards anywhere in the term (prefix, suffix, middle),
    case-insensitive; safely escaped (`%%` rejected, control chars
    rejected, 120-char cap).
  - Plaintext indexed regex for `first_name` / `last_name` / `email`.
  - Post-decrypt filter for encrypted `contact.phone_*`, `address_details`,
    and `date_of_birth`, with a 2 000-row candidate cap and
    `truncated_candidates` flag so the UI can prompt for refinement.
  - Multi-format DOB parsing (ISO, US, EU, year-only).
  - Pagination (`limit`, `offset`), hard-capped at 50 per page.
  - Masked-only projection — results never expose grouped PHI blocks.
  - Tenant + location scoping preserved; every search emits a
    `patient.searched` audit with the fields used + result counts.
  - New Mongo indexes `(tenant_id, last_name)`, `(tenant_id, first_name)`,
    `(tenant_id, phone)` for prefix-regex queries.
  - Tests: `backend/tests/test_patient_search.py` — **26 pass** covering
    wildcard semantics, case-insensitivity, DOB parsing, encrypted
    phone/address, pagination, auth, tenant scoping.
- **Frontend lookup UI** — `pages/Patients.jsx` rewritten:
  - Default view shows a "Recently viewed" section (localStorage, per-user,
    max 6) or a clean hero with wildcard examples.
  - Quick-lookup mode with 250 ms debounced typeahead after 2 characters.
  - Advanced mode with 4 focused inputs (Name / Phone / Address / DOB)
    and a manual Search submit.
  - Keyboard navigation (↑ / ↓ / Enter) across results.
  - Match-highlighting via `<mark>` with wildcard awareness.
  - "Too many candidates" banner surfaces backend truncation.
  - Clicking a result opens the full patient profile + pushes the entry
    onto "Recently viewed".
  - All interactive elements carry `data-testid`s.

### Removed
- The default `/api/patients` list call on the Patients page — the page
  no longer fetches the entire patient population into the browser.

### Changed
- **Per-user light / dark / system theme** — picker lives in the top-bar
  (sun/moon dropdown), persists to the user's profile via
  `PATCH /api/auth/me/preferences`, and syncs on every login so the
  clinician sees their chosen theme on any browser. System mode follows
  `prefers-color-scheme` and reacts live to OS-level changes.
  - Backend: `theme: "light"|"dark"|"system"` field on `users`, exposed
    via `UserPublic`; new `PreferencesUpdate` schema.
  - Frontend: `ThemeProvider` + `useTheme` hook + `<ThemeToggle />`
    component; localStorage fast-paint to prevent flash of wrong theme
    before `/auth/me` resolves.
  - Tests: `backend/tests/test_theme_preference.py` — 9 scenarios, all
    passing (default, light/dark/system swaps, invalid rejected, empty
    rejected, survives logout, per-user independence, unauth 401).

### Changed
- **Color system refactored into CSS variables** so dark mode swaps
  without per-page rewrites. New semantic utility classes
  (`surface-app`, `surface-raised`, `surface-muted`, `surface-sage`,
  `surface-warning`, `surface-danger-soft`, `text-strong`,
  `text-muted-strong`, `text-soft`, `text-sage`, `text-sage-deep`,
  `text-danger`, `text-warning`, `border-subtle`, `border-strong`,
  `bg-sage`, `bg-danger`) are defined in `@layer utilities` and swap
  under `.dark`. All 23 page + component files migrated from hard-coded
  hex utilities to these semantic classes in a single bulk pass.
- **Docs** — Added comprehensive project documentation: `README.md`,
  `CONTRIBUTING.md`, `SECURITY.md`, `docs/DOC_UPDATE_POLICY.md`, and a PR
  template. Existing long-form docs in `memory/` are now linked from
  `README.md`'s Documentation map.
- **CI — matrix-aware docs guard** — New `scripts/check_docs.py` driven
  by `docs/doc_rules.yml` enforces 9 declarative rules (code needs
  CHANGELOG, RBAC changes need AUTHORIZATION_GUIDE, tenancy changes need
  MULTI_TENANCY_ARCHITECTURE, auth changes need test_credentials, and so
  on). Wired into `.github/workflows/docs-guard.yml` for PRs and
  `.githooks/pre-commit` for local commits (opt-in via
  `git config core.hooksPath .githooks`). Supports `--json` for CI
  tooling. Supersedes the earlier `scripts/check_changelog.sh`.
- **CI — changelog stub helper** — `scripts/check_docs.py
  --emit-changelog-stub [--title …] [--category …] [--write]` drafts a
  well-formed bullet from the current diff, auto-categorises it
  (Added/Changed/Fixed/Security/Dependencies), and can prepend it under
  `## [Unreleased]`. Idempotent — reruns won't duplicate bullets.

## [2026-04-20] Phase 5 — Intake polish, uploads, signed consents + hardening
### Added
- **Wet-ink signature capture** (`frontend/src/components/SignaturePad.jsx`)
  wired into the 4-step patient intake wizard (Step 4 — Case & Consents).
  Canvas-based, pointer-events, devicePixelRatio aware, emits base64 PNG.
- **Patient document vault** — `POST/GET/DELETE /api/patients/{id}/documents`
  with 8 categories (insurance cards front/back, driver's license, referral
  letter, imaging report, intake form, consent receipt, other). All
  uploads: reauth-gated, audited, tenant-scoped, 10 MB hard cap.
- **Signed consent PDF generation** — `GET /api/patients/{id}/consents/{type}/pdf`
  using ReportLab. Supports hipaa/treatment/financial/telehealth/photo_release
  canonical types plus any custom `consents.additional[].type`.
- **PatientDocumentsCard** UI with automatic reauth prompt + retry when the
  backend returns `401 Re-authentication required`.
- **Magic-byte MIME sniffing** (python-magic + libmagic1) on every document
  upload — rejects spoofed content-types (e.g. ELF declared as `image/png`).
- **Streaming upload** via `SpooledTemporaryFile` — 64 KB chunks, early
  413 on cap breach, rolls to tmpfile past 1 MB for memory safety.
- **Autosave drafts** for the patient intake wizard (localStorage-based,
  per-user scope, cleared on successful save).
- **Edit-from-detail** flow: Edit button on `PatientDetail` auto-unmasks
  (with audit) and opens the wizard pre-filled with current data.

### Changed
- **Patient intake schema** now accepts both the legacy flat payload and the
  new grouped sections (`demographics`, `contact`, `address_details`,
  `emergency_contact_details`, `admin`, `guarantor`, `insurance`,
  `clinical_intake`, `case_details`, `consents`). Legacy top-level fields
  are backfilled from grouped sections when missing.
- **Encryption-at-rest** expanded to cover every grouped PHI section
  (previously only legacy scalar PHI fields were encrypted).
- **/api/auth/login** rate limit tuned to 30 attempts / 60s.
- **Patient service router** refactored from 984 → 628 lines by extracting:
  - `services/patient/_shared.py` (crypto/now/enforce_reason helpers)
  - `services/patient/documents_router.py`
  - `services/patient/consent_pdf_router.py`
  Parent router includes the sub-routers; public URL surface unchanged.
- **Consent PDF 500 error** now returns a generic message instead of the
  raw exception text (prevents library-trace leaks).

### Fixed
- `require_reauth` was misused as a FastAPI `Depends` on document endpoints,
  producing `422 user field required`. It's a plain helper — now called
  inline after permission resolution.

### Security
- Every document upload/download and consent PDF generation emits a
  PHI-flagged audit entry with IP, user-agent, and reason (when required).
- Magic-byte MIME sniffing closes the spoofed-content-type attack vector.

### Dependencies
- Added `reportlab==4.4.10` (Python).
- Added `python-magic==0.4.27` (Python) + OS package `libmagic1`.

### Tests
- New `backend/tests/test_phase5_docs_and_consent_pdf.py` — 22 scenarios,
  21 pass / 1 env-skipped.

---

## [2026-04-19 → 2026-04-20] Patient intake expansion (Phases 1-4)
### Added
- Grouped Pydantic section models (Demographics, ContactInfo, AddressInfo,
  EmergencyContactInfo, AdminInfo, GuarantorInfo, InsurancePlan/Info,
  ClinicalIntake, CaseDetails, ConsentRecord/Info) — Phase 1.
- 4-step patient intake wizard (`frontend/src/pages/Patients.jsx`) with
  validation, conditional rendering, and progress indicator — Phase 2.
- Pure-JS `patientWizardLogic.js` rules engine for conditional visibility +
  validation, with 39 passing Node unit tests — Phase 3.
- Grouped-payload rendering in `PatientDetail.jsx` with fallback to legacy
  scalar fields — Phase 4.

---

## [2026-04-19] Performance & scalability pass
### Added
- Redis (supervisord-managed, 128 MB LRU) for application cache + rate-limit
  buckets.
- Read/write DB split (`get_db_read` / `get_db_write` / `read_after_write_db`)
  Postgres-ready abstraction in `core/db.py`.
- Cache catalogue (`core/cache_keys.py`) with per-key TTLs and never-cache
  rules (unmasked PHI, exports, audit log).
- Prefix-based cache invalidation using Redis SCAN (never KEYS).
- `GET /api/perf/stats` admin-only ops view.

### Changed
- Graceful Redis fallback: requests never fail when Redis is down.

---

## [2026-04-19] HIPAA hardening pass
### Added
- **Audit logging** of every PHI access with outcome, IP, user-agent, reason.
- **Field-level encryption** (AES-256-GCM, `enc:v1:` prefix) for
  `patients.{address,emergency_contact,notes}`,
  `medical_records.{description,diagnosis,treatment}`, `appointments.notes`.
- **Password policy** — 12-char complexity + denylist + history-of-5 +
  90-day rotation warning + 120-day hard expiry.
- **MFA (TOTP)** with provisioning URI + 8 single-use backup codes;
  ticket-based challenge step on login.
- **Step-up reauth** required for delete-patient + add-medical-record +
  document upload/delete.
- **Masking** — PHI masked by default; `?unmask=true` audited + reason-gated
  for non-admin clinicians.
- **Soft-delete** with 7-year retention and legal-hold gate.
- **Frontend** — `BreakGlassDialog`, `ReauthDialog`, masked Notifications,
  Security + AuditLog admin pages, 15-minute idle timeout.

---

## [2026-04-18] MVP (Phase 1)
### Added
- Identity (register, login, admin user CRUD), Patient CRUD, Scheduling
  with conflict detection, mock SMS/Email via in-process event bus.
- Sage + stone medical theme, 7 role-aware pages.
- FastAPI + MongoDB + React scaffolding under supervisord.
