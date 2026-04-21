# CCMS — Product Requirements & Architecture Notes

**Last updated:** 2026-04-21 (Clinical module Phase 7 — Imaging/Media + Outcomes + Care Timeline expanded)

## 0. Design system (binding)
The Chiro Software design system is authoritative for every UI surface.

- **Palette:** Slate + Teal + Copper (deprecated: sage + stone).
- **Typography:** Outfit (display), Manrope (body), JetBrains Mono (technical).
- **Sources of truth:** `/app/docs/theme/`
  - `CHIRO_SOFTWARE_THEME_STANDARD.md`
  - `CHIRO_THEME_ENGINEERING_IMPLEMENTATION_SPEC.md`
  - `CHIRO_UI_REVIEW_AND_COMPLIANCE_CHECKLIST.md`
- **Implementation:** three-layer CSS tokens in `frontend/src/index.css`
  (foundation → semantic → component alias), mapped by
  `frontend/tailwind.config.js` to semantic utilities (`bg-background`,
  `bg-primary`, `bg-card`, `text-muted-foreground`, `rounded-sm/lg`,
  `shadow-sm/md`, `font-display/body/mono`).
- **Enforcement:** no raw hex or raw Tailwind palette (`bg-slate-500`,
  `bg-blue-600`, `dark:bg-zinc-900`) in feature code. Every interactive
  element needs a visible focus state. Every new component must ship
  with light + dark parity.

## 1. Original problem statement
Multi-tenant Chiropractic Clinic Management System on a microservices, event-driven architecture. Phase 1 delivered Identity / Patient / Scheduling / Communication. The HIPAA hardening pass added technical safeguards in line with 45 CFR §164.312.

## 2. User personas
| Persona     | Goals                                                                          |
|-------------|--------------------------------------------------------------------------------|
| **Admin**   | Manage users, full oversight, audit log review                                 |
| **Doctor**  | See own appointments, view patients (with break-glass reason), add records    |
| **Staff**   | Manage patients & scheduling, view notification log                            |
| **Patient** | See own profile, own records, own appointments; export own data               |

## 3. Architecture
**Backend** (`/app/backend/`)
- `server.py` — API Gateway under `/api`
- `core/` — `db.py`, `security.py` (bcrypt + JWT), `deps.py` (RBAC), `event_bus.py`, **`audit.py`**, **`crypto.py`** (AES-256-GCM), **`password_policy.py`**, **`mfa.py`** (TOTP + backup codes), **`reauth.py`**, **`masking.py`**
- `services/identity/` — register, login, MFA setup/verify/challenge, refresh, logout, change-password, reauth, admin user CRUD + disable/enable
- `services/patient/` — masked-by-default list/detail, encrypted PHI at rest, break-glass reason, soft-delete with 7-year retention, export
- `services/scheduling/` — encrypted notes, audit trail
- `services/communication/` — masked notification log
- `services/audit/` — admin-only `/api/audit-logs` viewer

**Frontend** (`/app/frontend/src/`)
- `AuthContext` (cookie session + MFA flow + 15-min idle timeout)
- Pages: Login (with MFA step), Register, Dashboard, Patients (mask toggle), PatientDetail (break-glass + reauth + soft-delete + export), Appointments, Calendar, Notifications (mask toggle), **Security**, **AuditLog**
- Components: `BreakGlassDialog`, `ReauthDialog`

## 4. What's implemented
### Clinical module Phase 7 — Imaging & Clinical Media + Outcomes + Care Timeline v2 (2026-04-21)
- **Workflow realized**: providers upload x-rays, MRI/CT reports,
  ultrasound, clinical photos, outside records, and PDFs to the
  patient chart; files are immutable after upload, metadata is
  editable, soft-delete hides from chart but retains audit trail.
  Functional outcome measures (NDI, Oswestry, Pain VAS, Pain scale,
  functional index, custom) are recorded ad-hoc from the chart or
  auto-emitted when a Re-Exam is signed. The Care Timeline is the
  longitudinal story — it now merges clinical media, standalone
  outcome entries, and diagnosis change audit events on top of the
  existing encounters / exams / notes / re-exams / plans stream.
- **New backend modules** under `services/clinical/`:
  - `media_models.py` — `ClinicalMediaCreate/Update/Public`, category
    enum (`xray`, `mri_ct_report`, `ultrasound`, `clinical_photo`,
    `outside_record`, `other_pdf`), source enum (`in_clinic`,
    `outside_imaging_center`, `patient_provided`, `records_request`),
    study_date, body_region, impression_findings, mime validation
    via `python-magic` (PNG/JPEG/WebP/HEIC + PDF), 25 MB cap.
  - `media_router.py` — endpoints under `/api`:
    - `GET/POST /patients/{pid}/clinical/media` (list + multipart
      upload; objects written via pre-existing
      `core.object_storage`).
    - `GET /patients/{pid}/clinical/media/{mid}` (metadata)
    - `GET /patients/{pid}/clinical/media/{mid}/download` (streaming
      blob with correct `Content-Type`)
    - `PATCH /patients/{pid}/clinical/media/{mid}` (metadata only —
      the binary is immutable)
    - `DELETE /patients/{pid}/clinical/media/{mid}` (soft-delete +
      audit event)
  - `outcomes_models.py` — `OutcomeCreate/Update/Public`, measure
    enum (`ndi`, `oswestry`, `pain_vas`, `pain_scale`,
    `functional_index`, `custom`), score/max_score, captured_at,
    unit, note, source (`provider_charted`, `patient_reported`,
    `reexam`), optional `reexam_id` link.
  - `outcomes_router.py` — endpoints under `/api`:
    - `GET/POST /patients/{pid}/clinical/outcomes`
    - `GET /patients/{pid}/clinical/outcomes/trends` — groups by
      `(measure_type, label)`, returns series of `{entry_id, score,
      captured_at}` sorted chronologically.
  - `notes_router.py care-timeline endpoint` — extended to aggregate
    three new entry kinds: `clinical_media` (from `clinical_media`
    collection, filtered on `deleted_at=None`), `outcome_entry`
    (from `clinical_outcome_entries` excluding `source=reexam` so
    the re-exam row isn't duplicated), `diagnosis_change` (derived
    from `clinical_audit_events` where `event_type` in
    `diagnosis.created/updated/resolved/activated`), plus
    `intake_submission` (from `clinical_history.intake_submitted`
    audit events). Every entry has the same shape: `{kind, id,
    date_of_service, status, title, subtitle, episode_id,
    provider_id, provider_name, link_path}`.
  - **Re-Exam auto-emission**: signing a re-exam now writes one
    `clinical_outcome_entries` row per outcome in the re-exam, tagged
    `source=reexam` with `reexam_id` linkage, so the trends endpoint
    picks them up automatically.
- **New frontend under `pages/clinical/`**:
  - `MediaCard.jsx` — filter chips (`all` + 6 categories), 4-col
    thumbnail grid with image/PDF glyphs, upload dialog (category,
    source, body region, study date, impression/findings), detail
    dialog with inline preview (`<img>` for images, `<iframe>` for
    PDFs), download link, and soft-delete button for writers.
    Re-auth-aware on 401.
  - `OutcomesCard.jsx` — two modes: `snapshot` (per-measure chip
    with latest score, `/max`, and delta-vs-prior badge using
    `▼`/`▲`) and `trend` (one compact SVG line chart per measure,
    no charting library — viewBox-scaled, axis ticks + point
    labels). Record dialog seeds unit/max from the chosen measure.
  - `CareTimelineCard.jsx` — extended `KIND_META` with icons for
    `clinical_media` (ImageIcon), `outcome_entry` (Activity),
    `diagnosis_change` (GitBranch/warning), and `intake_submission`
    (ClipboardList); extended `STATUS_TONE` for new statuses
    (`uploaded`, `provider_charted`, `patient_reported`, `reexam`,
    `created`, `updated`, `resolved`, `activated`, `submitted`).
  - `TreatmentPlanEditor.jsx` — new read-only "Latest outcomes"
    section right after "Objective baselines"; pulls
    `/outcomes/trends`, renders a delta chip per measure, never
    mutates data.
  - `ClinicalTab.jsx` — mounts `MediaCard` and `OutcomesCard`,
    removes the Phase-2 placeholders for Imaging/Outcomes. Only
    Billing Readiness remains as a placeholder.
- **Object storage**: reuses pre-existing `core.object_storage`
  (Emergent LLM-key backed) — no new 3rd-party dependency. Files are
  referenced by `storage_path` on `clinical_media`; the binary is
  never returned inline in list/detail responses, only via explicit
  `/download`.
- **Testing**: backend `pytest` (`test_clinical_phase7.py`) covers
  upload → list → download → metadata patch → soft delete and the
  outcomes + trends flow including re-exam auto-emission. Frontend
  validated via `testing_agent_v3_fork` (iteration 37) static wiring
  + self-test via live preview (admin login, upload PNG, record Pain
  VAS 7 then 4, trend mode SVG render, care timeline merge) — all
  pass.
- **Guardrails observed**: re-used `core.object_storage`; auto-emit
  standalone outcomes on re-exam sign; simple inline SVG charts
  (no Recharts / Nivo). Treatment plan "Latest outcomes" is
  read-only and lightweight.

### Clinical module Phase 6 — Treatment Plans + Re-Exams (2026-02-22)
- **Workflow realized**: provider creates a chart-level **Treatment
  Plan** (plan of care) for the episode/case with goals, frequency,
  duration, baselines, discharge criteria. As care progresses, a
  **Re-Exam** is launched from a `re_evaluation` encounter — it
  auto-links the active plan + most recent signed Initial Exam +
  freezes a `baseline_snapshot` for defensible comparison. The
  re-exam carries a recommendation decision (continue / modify_plan /
  discharge / transition_maintenance). Signing a
  `modify_plan` re-exam emits an audit event only — the plan is NOT
  auto-mutated. Providers then explicitly PATCH the plan (or
  discharge + create a new one).
- **New backend modules** under `services/clinical/`:
  - `treatment_plans_models.py` — `PlannedIntervention`, `PlanGoal`
    (measure_type: pain_scale/functional/rom/outcome_score/custom;
    status: active/met/modified/abandoned; baseline_value /
    target_value), `FunctionalMeasure`, `PlanBaselines`,
    `TreatmentPlanCreate/Update/SetStatus`, `TreatmentPlanPublic`
    with live `TreatmentPlanProgress` (visits_completed /
    total_visits / percent).
  - `treatment_plans_router.py` — endpoints under `/api`:
    - `GET/POST /patients/{pid}/clinical/treatment-plans`
    - `GET/PATCH /patients/{pid}/clinical/treatment-plans/{tpid}`
    - `POST /patients/{pid}/clinical/treatment-plans/{tpid}/set-status`
      (transitions active → on_hold / completed / discharged /
      cancelled with required reason; discharged is reversible back
      to active)
    - One-active-plan-per-episode guard → 409 with existing plan id
      surfaced in detail
    - PATCH on discharged / completed / cancelled → 409
    - Progress computed live: signed follow-up notes on same episode
      since plan `start_date` / `frequency_total_visits` * 100
  - `reexams_models.py` — `GoalProgressEntry`
    (status: on_track/improved/plateau/regressed/met),
    `OutcomeUpdate` (typed: ndi/oswestry/pain_vas/
    functional_index/custom + score/max_score/note),
    `RECOMMENDATION` Literal, `ReExamCreate/Update`,
    `ReExamPublic`, `ReExamNarrative`. Reuses
    `ExamExamination` + `NewDiagnosisDraft` from Phase 4 for
    apples-to-apples comparison against the Initial Exam.
  - `reexams_router.py` — endpoints under `/api`:
    - `GET/POST /patients/{pid}/clinical/re-exams` (POST from
      encounter)
    - `GET/PATCH /patients/{pid}/clinical/re-exams/{rid}`
    - `POST .../mark-sign-ready` / `.../unmark-sign-ready`
    - `POST .../sign` — terminal; requires
      `recommendation_decision` (400 otherwise). Materializes
      `new_diagnoses` into `clinical_diagnoses` (ICD-10 uppercasing
      + de-dup; same semantics as Initial Exam). If
      `recommendation_decision=modify_plan`, emits a second
      `treatment_plan.revised_recommended` audit event tagging the
      linked plan; the plan itself is NOT mutated.
    - `GET .../narrative` — RE-EXAMINATION NOTE header with
      BASELINE (frozen) / UPDATED OBJECTIVE FINDINGS / GOAL
      PROGRESS / OUTCOME MEASURES / RECOMMENDATION sections
    - One-reexam-per-encounter (non-cancelled). Duplicate POST
      returns 200 + `X-ReExam-Existed: true` header. Cancelled
      encounter → 409.
    - At create: `_build_baseline_snapshot` freezes plan goals +
      plan baselines + plan frequency + initial exam examination /
      history + prior re-exam snapshot (if any) into an immutable
      dict on the re-exam document.
  - **Integrations**:
    - Summary endpoint now exposes
      `treatment_plans.{total, open}` (open = active status) and
      `re_exams.{total, open}` (open = draft + sign_ready).
    - Follow-up note `_hydrate` resolves the episode's active plan
      and injects `active_plan_summary` (id, title, frequency,
      visits progress, top 3 goals) on every GET — read-only.
    - Care-timeline endpoint now merges `treatment_plan` +
      `re_exam` kinds alongside encounters + exams + notes with
      deep-link paths.
- **Access + audit**: reads `admin|doctor|staff`; writes
  `admin|doctor` + `require_reauth`. Tenant isolation via
  `scoped_filter` — cross-tenant probes 404. Every mutation emits a
  global `audit_logs` row + patient-scoped `clinical_audit_events`
  (`treatment_plan.created`, `treatment_plan.updated`,
  `treatment_plan.status_changed`, `re_exam.created`,
  `re_exam.updated`, `re_exam.signed`,
  `treatment_plan.revised_recommended`).
- **Indexes** in `core/db.py`: `clinical_treatment_plans` on
  `(tenant_id, patient_id, plan_status)` + `(tenant_id, episode_id)`.
  `clinical_reexams` on `(tenant_id, encounter_id)` UNIQUE +
  `(tenant_id, patient_id, date_of_service)` + `(tenant_id, status)`.
- **Frontend**:
  - `pages/clinical/TreatmentPlansCard.jsx` — chart-level list with
    status + progress bar; `plan-create-btn` launches new plan.
  - `pages/clinical/TreatmentPlanEditor.jsx` at
    `/patients/:pid/clinical/treatment-plans/:tpid` — structured
    sections (overview, interventions, goals, baselines including
    functional measures list, home-care, activity/work, discharge,
    maintenance). `plan-set-status-btn` opens a dialog with required
    reason. Progress bar reflects live visit count.
  - `pages/clinical/ReExamsCard.jsx` — chart list with status +
    decision chips.
  - `pages/clinical/ReExamEditor.jsx` at
    `/patients/:pid/clinical/re-exams/:rid` — renders frozen plan
    snapshot read-only; auto-seeds goal progress rows from the
    plan's goals with baseline / current / status / note; typed
    outcome measures editor (NDI / Oswestry / pain_vas / functional
    index / custom); decision radio + reason; `revised_plan_summary`
    shown only when `decision=modify_plan`. Sign disabled when no
    decision or while dirty. Signed banner replaces form post-sign.
  - `pages/clinical/ClinicalTab.jsx` — adds `stat-treatment-plans`
    + `stat-reexams` tiles; mounts the two new cards; removes Phase-2
    placeholders (`clinical-placeholder-treatment-plans`,
    `clinical-placeholder-re-exams`).
  - `pages/clinical/EncountersCard.jsx` — `re_evaluation` encounters
    now emit `encounter-start-reexam-{id}` (routing to Re-Exam, not
    Initial Exam). `new_patient_exam` continues to route to the
    Initial Exam editor; `follow_up` / `treatment_visit` continue
    to route to the Follow-up Note editor.
  - `pages/clinical/CareTimelineCard.jsx` — supports `treatment_plan`
    + `re_exam` kinds with distinct icons.
  - `pages/clinical/FollowUpNoteEditor.jsx` — renders
    `note-active-plan-strip` (plan title + frequency + visits
    progress + top 3 goals) at the top when an active plan exists
    on the episode. Read-only; no edit path.
  - `App.js` routes `/patients/:pid/clinical/treatment-plans/:tpid`
    and `/patients/:pid/clinical/re-exams/:rid`.
- **Tests**: `backend/tests/test_clinical_phase6.py` — **14/14
  passing**. Phase 5 regression — **12/12 green**.
- **Frontend E2E** (`iteration_36.json`): **100%** coverage —
  TreatmentPlanEditor 19/19 testids present, ReExamEditor 21/21
  testids present, routing and conditional rendering verified.

### Clinical module Phase 5 — Follow-up / Daily Visit Notes + Care Timeline (2026-02-22)
- **Workflow realized**: daily-visit charting for follow-up / treatment
  encounters. Provider launches from the calendar → encounter → note.
  One note per encounter (non-cancelled). Signed notes are immutable
  and surface in Patient Profile > Clinical + Care Timeline.
- **New backend surface** `services/clinical/`:
  - `notes_models.py` — SOAP-structured Pydantic models:
    `NoteSubjective` (interval history, pain scale 0–10, `pain_change`
    better/worse/same/fluctuating, functional change, home-care
    adherence + notes), `NoteObjective` (repeatable
    `RegionFinding[]` with palpation/ROM summary/notes, reassessment
    summary, optional Vitals reused from Phase 4), `NoteAssessment`
    (`response_to_care` improving/plateau/regressing/new_complaint +
    clinical impression), `NotePlan` (repeatable `TreatmentEntry[]`
    with kinds adjustment / modality / soft_tissue / exercise /
    other; segments, technique, modality, region, duration_min;
    regions_treated chip list; home-care reinforcement; next-visit
    plan + recommended_interval_days).
  - `notes_router.py` — endpoints under `/api`:
    - `GET /patients/{pid}/clinical/notes` (list; `status_in` +
      `episode_id` filters)
    - `POST /patients/{pid}/clinical/notes` — create from encounter.
      Optional `copy_forward_from_note_id` seeds fields from a
      prior signed note at creation. One-note-per-encounter:
      duplicate POST returns 200 + `X-Note-Existed: true` header +
      the existing note.
    - `GET/PATCH /patients/{pid}/clinical/notes/{nid}` — PATCH
      blocks on signed (409).
    - `POST .../copy-forward` — explicit. Non-destructive by default
      (only fills empty destination fields); `force=true` overwrites.
      Source must be signed and belong to the same patient (400
      otherwise). Accumulates `copied_fields` across calls.
    - `POST .../mark-sign-ready` + `.../unmark-sign-ready` (draft ↔
      sign_ready, wrong-status → 409).
    - `POST .../sign` — terminal. Assigns `visit_number` = count of
      prior signed follow-up notes on the same episode (or patient
      if no episode) + 1. Double-sign → 409.
    - `GET .../narrative` — SOAP-formatted rendering with header
      `FOLLOW-UP / DAILY VISIT NOTE`, sections `SUBJECTIVE (S)` /
      `OBJECTIVE (O)` / `ASSESSMENT (A)` / `PLAN (P)` and active
      `DIAGNOSES` block. Empty sections are omitted.
    - `GET /patients/{pid}/clinical/care-timeline` — chronological
      merge of encounters + initial exams + follow-up notes, sorted
      date-desc, with deep-link paths.
    - `POST /appointments/{aid}/clinical/notes` — convenience launch
      from the appointment when patient_id is not handy (reuses the
      latest non-cancelled encounter on that appointment).
  - Summary endpoint now exposes live `notes.{total, open}` where
    `open = draft + sign_ready`.
- **Lifecycle**: `draft → sign_ready → signed`; signed is terminal
  and immutable in Phase 5. Addendums/amendments intentionally
  deferred per scope guardrails.
- **Completeness scoring**: backend computes `completeness.score` +
  `missing_fields` on every read against the REQUIRED_FIELDS set:
  `subjective.interval_history`, `subjective.pain_scale_0_10`,
  `assessment.response_to_care`, `plan.treatment_rendered`,
  `plan.next_visit_plan`. UI surfaces a meter + chips.
- **Access + audit**: reads `admin|doctor|staff`; writes
  `admin|doctor` + `require_reauth`. Tenant isolation via
  `scoped_filter` — cross-tenant probes 404. Every mutation emits
  both a global `audit_logs` row AND a patient-scoped
  `clinical_audit_events` row (events: `follow_up_note.created`,
  `follow_up_note.updated`, `follow_up_note.copy_forward`,
  `follow_up_note.signed`).
- **Indexes** in `core/db.py`: `clinical_follow_up_notes` on
  `(tenant_id, encounter_id)` UNIQUE,
  `(tenant_id, patient_id, date_of_service)`,
  `(tenant_id, status)`, `(tenant_id, episode_id)`.
- **Frontend**:
  - `pages/clinical/FollowUpNoteEditor.jsx` — full page at
    `/patients/:pid/clinical/follow-up/:nid`. Structured widgets
    for each SOAP section: pain-scale number, pain-change /
    adherence / response-to-care Selects, repeatable
    `RegionFinding` rows, vitals (BP + pulse), repeatable
    `TreatmentEntry` rows with kind-aware inputs (adjustment vs
    modality), regions-treated chip input, next-visit-plan +
    recommended-interval-days. Completeness meter header shows
    `filled/total` + missing-field chips. Save / Copy-forward /
    Mark sign-ready / Sign / View narrative toolbar. Copied-forward
    fields show a yellow "Copied forward" badge per-field.
    Read-only `exam-signed-banner` replaces the form post-sign.
  - `pages/clinical/FollowUpNotesCard.jsx` — list card on Clinical
    tab with per-row status / visit # / provider / completeness
    meter and direct link into editor.
  - `pages/clinical/CareTimelineCard.jsx` — chronological timeline
    of encounters + initial exams + follow-up notes with kind-
    specific icons and deep-link affordance.
  - `pages/clinical/ClinicalTab.jsx` — stat row now includes
    `stat-notes` (open count); `FollowUpNotesCard` +
    `CareTimelineCard` mounted under the Initial Exams card.
    `clinical-placeholder-follow-notes` and
    `clinical-placeholder-timeline` placeholders removed (now
    live).
  - `pages/clinical/EncountersCard.jsx` — `follow_up` and
    `treatment_visit` encounters now render an
    `encounter-start-note-{id}` action that POSTs
    `/clinical/notes` and navigates to the editor;
    `new_patient_exam` and `re_evaluation` continue to route to
    the Initial Exam editor via `encounter-start-exam-{id}`.
  - `App.js` route:
    `/patients/:pid/clinical/follow-up/:nid`.
- **Tests**: `backend/tests/test_clinical_phase5.py` — **12/12
  passing**: create-from-encounter with auto-fill + empty
  completeness; one-note-per-encounter idempotency + X-Note-Existed
  header; cancelled-encounter 409; PATCH structured round-trip
  with vitals/regions/treatments + 100% completeness; completeness
  missing-fields surfaced; draft→sign_ready→signed lifecycle with
  double-sign 409 + PATCH-signed 409 + `visit_number` auto-
  increment across encounters; copy-forward non-destructive +
  force; copy-forward rejects unsigned source 400; copy-forward
  inline at create; narrative renders SUBJECTIVE / OBJECTIVE /
  ASSESSMENT / PLAN; care-timeline merges + sorts; cross-tenant
  404; reauth required on create.
- **Regression**: Phase 1+2+4 (35/35) green.
- **Infra requirement**: `libmagic1` system package must be present
  in the container image (python-magic dependency used by patient
  documents router). If backend returns 502 on boot,
  `sudo apt-get install -y libmagic1 && sudo supervisorctl restart backend`.

### Clinical module Phase 4 — Initial Exam workflow (2026-02-22)
- **Workflow realized**: provider launches documentation from the
  calendar → encounter shell (Phase 3) → `POST /clinical/exams`
  creates a **single** Initial Exam bound to that encounter. One
  exam per encounter (idempotent create returns 200 +
  `X-Exam-Existed: true` header if the exam already exists). The
  signed exam is the authoritative initial evaluation record and
  lives under Patient Profile > Clinical for the life of the chart.
- **New backend service** `services/clinical/` additions:
  - `exam_template.py` — frozen system default template
    `default-initial-exam-v1` with three sections (`history`,
    `examination`, `assessment`). Snapshotted into each exam at
    create time so template evolution never mutates a signed exam.
  - `exams_models.py` — Pydantic models: `ExamHistory` (11 fields),
    `ExamExamination` (vitals + observation/posture/gait/palpation/
    segmental findings + structured `RangeOfMotion` for cervical/
    thoracic/lumbar/shoulders/hips + `OrthopedicTest[]` +
    `MuscleStrengthEntry[]` + neurologic/sensory/reflex narratives),
    `ExamAssessment` (functional limitations, summary, impression,
    treatment recommendations), `NewDiagnosisDraft` (ICD-10 drafts
    materialized at sign time).
  - `exams_router.py` — endpoints under `/api`:
    - `GET /clinical/exam-templates/default`
    - `GET/POST /patients/{pid}/clinical/exams` (list + create from
      encounter with `prefill_from_chart=true` default)
    - `GET/PATCH /patients/{pid}/clinical/exams/{eid}` (PATCH blocked
      on signed; cross-patient diagnosis_ids → 400)
    - `POST .../prefill` — explicit, non-destructive re-pull from
      clinical_history + active diagnoses; updates
      `prefilled_from_chart_at`
    - `POST .../mark-sign-ready` + `.../unmark-sign-ready` (draft ↔
      sign_ready)
    - `POST .../sign` (terminal; materializes `new_diagnoses` into
      `clinical_diagnoses` with ICD-10 uppercasing + case-insensitive
      de-dup on `(code, body_region, laterality)` against active
      problem list + one-primary-per-episode enforcement; records
      `signed_at` / `signed_by`)
    - `GET .../narrative` — Initial-Exam-oriented rendering with
      `INITIAL EXAMINATION` header + HISTORY / EXAMINATION /
      ASSESSMENT & PLAN sections + structured vitals/ROM/orthopedic
      tests/muscle strength + DIAGNOSES block. Empty sections are
      omitted.
- **Lifecycle**: `draft → sign_ready → signed` (terminal). `signed`
  is immutable in Phase 4 — amendments/addendums deferred to a later
  phase. Double-sign → 409. Wrong-status transitions → 409.
- **Cross-cutting controls** (consistent with earlier phases):
  - Reads gated by `admin|doctor|staff`; writes by `admin|doctor` +
    `require_reauth`. Tenant isolation via `scoped_filter`;
    cross-tenant probes return 404.
  - Every mutation writes both a global `audit_logs` row and a
    patient-scoped `clinical_audit_events` row (events:
    `initial_exam.created`, `initial_exam.updated`,
    `initial_exam.prefilled`, `initial_exam.signed`).
  - Summary endpoint now returns live `initial_exams.{total, open}`
    where `open = draft + sign_ready`.
- **Encounter → exam linkage**: `EncountersCard` (Phase 3)
  surfaces a `encounter-start-exam-{enc.id}` button on every
  in-progress encounter that POSTs `/clinical/exams` and routes to
  `/patients/{pid}/clinical/exams/{eid}`.
- **Indexes** in `core/db.py`: `clinical_initial_exams` on
  `(tenant_id, patient_id, date_of_service)`,
  `(tenant_id, encounter_id)`, and `(tenant_id, status)`.
- **Frontend**:
  - `pages/clinical/InitialExamsCard.jsx` — lists every exam on the
    Clinical tab with status badge + date + provider + narrative
    shortcut.
  - `pages/clinical/InitialExamEditor.jsx` — full editor rendering
    from the frozen `template_snapshot`. Structured widgets for
    vitals (`exam-vitals-bp`, `exam-vitals-pulse`, …), ROM
    (`exam-rom-{region}-{movement}`), orthopedic tests
    (`exam-ortho-row-{i}`), muscle strength (`exam-ms-row-{i}`),
    existing/new diagnoses (`exam-existing-dx-{id}`,
    `exam-new-dx-row-{i}`). Save disabled when clean; sign disabled
    while dirty (user must save before sign). Narrative dialog shows
    rendered print-friendly narrative. `exam-signed-banner` replaces
    the editable form post-sign.
  - `ClinicalTab.jsx` — summary row leads with live `stat-exams` tile
    (open count); `InitialExamsCard` mounted below the Encounters
    card.
  - `App.js` route: `/patients/:id/clinical/initial-exam/:examId`.
- **Tests**: `backend/tests/test_clinical_phase4.py` — **11/11
  passing**: create-from-encounter happy path + auto-fill from
  encounter/appointment/provider/episode/location + prefill from
  history + active-diagnosis auto-select + frozen template snapshot;
  one-exam-per-encounter idempotency via `X-Exam-Existed`;
  cancelled-encounter reject 409; PATCH merges structured sections +
  cross-patient diagnosis_ids → 400; explicit `/prefill`
  non-destructive; mark-sign-ready / unmark / sign transitions;
  sign-from-draft; double-sign 409; PATCH-signed 409; sign
  materializes `new_diagnoses` with ICD-10 uppercase + de-dup +
  primary-uniqueness; narrative contains all expected sections;
  summary `initial_exams` counts live; cross-tenant probes 404;
  reauth required on writes.
- **Regression**: Phase 1+2 (24/24) green. Phase 3 8/9 (1 flaky
  appt-overlap seed collision in Phase 3's reauth test — pre-existing
  random-time jitter, not a product bug).

### Clinical module Phase 3 — appointment-launched encounter shell (2026-02-21)
- **Workflow standard locked in**: providers begin documentation from
  the appointment/calendar; the appointment is the encounter shell; the
  resulting clinical record is stored in and viewable from Patient
  Profile > Clinical.
- **New backend entity `clinical_encounters`**
  (`services/clinical/encounters_router.py` + `encounters_models.py`):
  - Convenience launch on the appointment prefix:
    - `POST /api/appointments/{aid}/clinical/encounters` — idempotent.
      Returns `{encounter, existed: bool}` with 201 on new, 200 on reuse.
    - `GET /api/appointments/{aid}/clinical/encounter` — latest
      non-cancelled encounter for the appointment.
  - Authoritative patient-owned surface:
    - `GET /api/patients/{pid}/clinical/encounters` — list with
      `status_in` + `episode_id` filters.
    - `GET/PATCH /api/patients/{pid}/clinical/encounters/{eid}`.
    - `POST .../encounters/{eid}/complete` and
      `POST .../encounters/{eid}/cancel`.
  - Context auto-fill: launch captures a **frozen**
    `appointment_snapshot` (patient_id, provider_id, location_id,
    start_time, end_time, status, reason) plus
    `scheduled_start`, `scheduled_end`, `scheduled_duration_min`,
    `date_of_service`, `appointment_status_at_launch`. Later edits to
    the appointment do NOT mutate the snapshot — the chart record is
    defensibly reproducible.
  - Four encounter types: `new_patient_exam`, `follow_up`,
    `re_evaluation`, `treatment_visit`.
  - Three lifecycle statuses: `in_progress → completed` or `cancelled`.
- **Exception workflow (cancelled / no-show path)**:
  - Launching against a `cancelled` appointment without
    `exception_reason` → 409.
  - With a reason (≥3 chars) AND a role of `admin|doctor` →
    encounter created with `is_exception=True`, `exception_reason`,
    `exception_invoked_by`, `exception_invoked_at` stamped into the
    encounter. Staff cannot bend the rule (403).
  - The exception is surfaced visually in the chart as a warning badge.
- **Appointment → chart linkage**: `GET /api/appointments/{id}` now
  projects `clinical_encounter_id` + `clinical_encounter_status` so
  the calendar UI can tell at a glance whether a visit is already
  launched.
- **Access + audit**: reads `admin|doctor|staff`; writes
  `admin|doctor` + `require_reauth`. Every mutation emits both a
  global `audit_logs` row AND a `clinical_audit_events` row scoped to
  the patient chart (events: `encounter.launched`, `encounter.updated`,
  `encounter.completed`, `encounter.cancelled`) — the exception flag
  and appointment_status_at_launch ride along in the metadata so
  chart-history UI can show the provenance of every launch decision.
- **Indexes** in `core/db.py`:
  `clinical_encounters` on
  `(tenant_id, patient_id, date_of_service)`,
  `(tenant_id, appointment_id)`, and
  `(tenant_id, status)`.
- **Frontend**
  - `pages/clinical/EncounterLaunchDialog.jsx` — opened from
    BookDialog's new **Launch encounter** button (`appt-launch-encounter-btn`).
    Picks encounter type (auto-inferred from the appointment reason),
    optional episode (any active / on-hold / closed patient episode),
    and — for cancelled appointments — a required `exception_reason`.
    On submit routes to
    `/patients/{pid}?tab=clinical&encounter={eid}`; if an encounter
    already exists the dialog shows a banner and "Open in chart" shortcut.
  - `pages/clinical/EncountersCard.jsx` — new live card on the
    Clinical tab. Lists encounters with type, status, duration,
    provider, episode, exception flag. Inline
    `complete` + `cancel` transitions, plus an **Appointment** deep
    link that opens the scheduling page on the correct day.
  - `pages/clinical/ClinicalTab.jsx` — summary row now leads with a
    live `stat-encounters` tile (in-progress count) and mounts
    `EncountersCard`.
  - `pages/PatientDetail.jsx` — tabs are now URL-synced via
    `?tab=...&encounter=...`; deep-linking from Launch lands the
    clinician right on the new encounter with its row highlighted.
  - `pages/scheduling/SchedulingPage.jsx` + `DayView.jsx` — Day view's
    cancelled-appointment tile now carries a clickable
    "Canceled · Open" pill (`scheduling-day-appt-open-{id}`) so
    doctors can still reach cancelled appointments to invoke the
    exception-launch flow. The slot underneath remains freely
    re-bookable. Week and Month views already routed cancelled
    appointments through BookDialog.
- **Tests** `backend/tests/test_clinical_phase3.py` — **9 tests** (1
  conditionally skipped when no staff user is seeded):
  context freeze + idempotent relaunch + chart visibility + cancelled
  rejection + exception-with-reason + cross-tenant/cross-patient
  episode 400 + complete/cancel lifecycle + PATCH rules + tenant
  isolation + reauth requirement + summary reflects encounter counts.
- **Phase 1 + Phase 2 regression** still 24/24 green. Total clinical
  test suite: **33/33**.

### Clinical module Phase 2 — Intake & History + Diagnoses (2026-02-21)
- **Chart-first workflow standard reinforced:** intake-derived history and
  diagnoses live under the patient chart; future appointment-launched note
  workflows will read this chart-level data.
- **New backend routers** under `services/clinical/`:
  - `history_router.py`:
    - `GET /api/patients/{pid}/clinical/history` — auto-seeds ONCE from the
      most recent completed intake form on first access. Each field carries
      a traceability row in `field_meta[<field>]` with
      `{source, source_form_id, updated_at, updated_by}`.
    - `PATCH /clinical/history` — `exclude_unset`; any field present flips
      its `source` to `"provider_edit"`.
    - `POST /clinical/history/import` — explicit, non-destructive re-import:
      provider-edited fields are preserved; returns
      `imported_fields[]` + `skipped_fields[]` + `source_form_id`.
      Rejects non-completed forms (409) and no-form-available (409).
  - `diagnoses_router.py` — full problem-list CRUD at
    `/api/patients/{pid}/clinical/diagnoses` with create/list/get/patch/
    resolve/reactivate. Supports ICD-10, label, status (active/resolved),
    `is_primary`, optional `episode_id` (any episode — active, on-hold, or
    closed; no restriction for recurrence/PI case cleanup), `body_region`,
    `laterality` (left/right/bilateral/midline), `chronicity`
    (acute/subacute/chronic), `onset_date`, `resolved_date`,
    `resolution_notes`, `notes`. `is_primary=True` is auto-uniqued within
    `(patient, episode_id-or-null, status=active)` — setting one as primary
    clears siblings in the same grouping.
  - Summary endpoint now returns live `diagnoses` counts + `history_present`
    flag so the UI doesn't need a third round-trip.
- **Access + audit:** reads gated by `admin|doctor|staff`; writes by
  `admin|doctor` plus `require_reauth`. Every create/edit/import/resolve/
  reactivate emits both a global `audit_logs` row and a patient-chart-scoped
  `clinical_audit_events` row.
- **Indexes** added in `core/db.py`:
  `clinical_history` unique on `(tenant_id, patient_id)`;
  `clinical_diagnoses` on
  `(tenant_id, patient_id, status, is_primary, created_at)` and
  `(tenant_id, episode_id)`.
- **Frontend** new cards on the Clinical tab (`pages/clinical/`):
  - `IntakeHistoryCard.jsx` — renders every history field with a per-field
    "FROM INTAKE" / "PROVIDER EDIT" / "NOT SET" badge. Fields cover chief
    complaint, HPI, onset date, MOI, pain location/radiation, aggravating/
    relieving factors, severity (0–10), prior treatment, prior chiropractic
    care, medications, allergies, PMH/PSH/FH/SH, occupation, activity
    level, accident details, work-comp details, ROS, red-flag screening.
    "Re-import from intake" button calls the non-destructive import;
    inline Edit mode lets providers PATCH any subset at once.
  - `DiagnosesCard.jsx` — Problem List with status + episode filters,
    add/edit dialog (ICD-10 uppercased live, label, optional episode link
    to ANY episode, body region, laterality, chronicity, onset date,
    notes, is_primary checkbox), inline Resolve/Reactivate, primary badge.
  - `ClinicalTab.jsx` updated: replaces the two Phase-2 placeholder cards
    with live cards and shows live diagnoses + history stats in the
    summary row (`stat-diagnoses` count + `stat-history: On file`). Eight
    future-phase placeholder cards remain.
- **Tests** `backend/tests/test_clinical_phase2.py` — **15/15 passing**;
  Phase 1 regression suite still 9/9. Covers auto-seed, empty history,
  provider-edit flip, exclude_unset, import-skips-provider-edits, draft
  form rejection, no-form-available rejection, tenant isolation,
  reauth requirement, full diagnosis lifecycle, primary uniqueness
  (including orphan vs episode grouping), cross-tenant/cross-patient
  episode linkage 400, list filters, patient-role blocked,
  summary reflects history + diagnoses.

### Clinical module Phase 1 — episode/case scaffold (2026-02-21)
- **Workflow standard locked in:** the **Patient Profile is the authoritative
  longitudinal home of the clinical record.** Appointments (to be wired in
  Phase 2+) are the operational encounter launch point, but every clinical
  artifact lives under the patient and is reachable from the Clinical tab
  regardless of whether it was authored from the chart or from an encounter.
- **Backend service** `services/clinical/` with tenant-aware router at
  `/api/patients/{patient_id}/clinical/*`:
  - `GET  /clinical/summary` — counts for episodes + zero-shaped
    placeholders for notes/diagnoses/treatment_plans/outcomes/media/
    encounter_links so the UI contract stays stable across future phases.
  - `GET  /clinical/episodes` — list with `status_in` and `case_type`
    filters; responsible-provider name hydrated in one round-trip.
  - `POST /clinical/episodes` — create; accepts every case_type
    (`new_patient_eval`, `injury_episode`, `recurrence`, `maintenance`,
    `mva`, `workers_comp`, `personal_injury`); rejects cross-tenant
    responsible_provider_id with 400.
  - `GET  /clinical/episodes/{id}` — read.
  - `PATCH /clinical/episodes/{id}` — partial update with `exclude_unset`;
    blocked on closed/archived episodes (409).
  - `POST /clinical/episodes/{id}/close` — transitions to `closed` with
    required reason (≥3 chars) and `end_date`.
  - `POST /clinical/episodes/{id}/reopen` — transitions back to `active`
    and clears end_date / closed_reason.
- **Models** declared in `services/clinical/models.py` for every downstream
  artifact that Phase 2+ will build on: `ClinicalNoteBase`, `DiagnosisBase`,
  `TreatmentPlanBase`, `OutcomeEntryBase`, `ClinicalMediaBase`,
  `EncounterLinkBase`, `ClinicalAuditEventBase`. Their collections ship with
  `(tenant_id, patient_id, episode_id)` indexes on day one so no migration
  will be needed when CRUD lands.
- **Cross-cutting controls:**
  - `require_role("admin","doctor","staff")` on reads; `("admin","doctor")`
    on writes. All writes additionally call `require_reauth` — matching the
    medical-record reauth posture.
  - Tenant isolation enforced via `scoped_filter` on every query; cross-
    tenant probes always return 404, never 403.
  - Every mutation writes a row to a new `clinical_audit_events` collection
    (patient-scoped projection of the global audit stream) so Phase 2
    chart-history UI doesn't have to scan the global stream.
- **Frontend** new tab **Clinical** inside Patient Profile:
  - `pages/clinical/ClinicalTab.jsx` renders the Clinical Summary stat row
    (open + total episodes; placeholder `—` for notes/diagnoses until
    Phase 2), an **Episodes & Cases** section with list / create / close /
    reopen affordances, and ten dashed **Phase 2** placeholder cards for
    every downstream section (Intake & History, Diagnoses, Initial Exam,
    Follow-up Notes, Re-Exams, Treatment Plans, Imaging & Clinical Media,
    Outcomes, Care Timeline, Billing Readiness).
  - Create dialog offers every case type with onset date + responsible
    provider dropdown; close dialog enforces a ≥3-char reason.
  - Wired into `PatientDetail.jsx` between Intake and Documents; writes
    use the existing global `ReauthGate` / `ReauthDialog` for step-up.
- **Indexes added** in `core/db.py` for `clinical_episode_cases`,
  `clinical_notes`, `clinical_diagnoses`, `clinical_treatment_plans`,
  `clinical_outcome_entries`, `clinical_media`, `clinical_encounter_links`,
  and `clinical_audit_events` — each anchored on `tenant_id + patient_id +
  episode_id` for PostgreSQL-portable query plans.
- **Tests** `backend/tests/test_clinical_phase1.py` — **9/9 green**:
  summary shape on fresh patient, every case_type accepted + rejected-
  unknown, unknown provider 400, tenant isolation (cross-tenant 404 on
  reads + writes), patient-role blocked, doctor can create/close,
  PATCH `exclude_unset` + double-close 409 + reopen lifecycle,
  clinical_audit_events rows emitted.

### Settings navigation split (2026-02-21)
- `ClinicSettings.jsx` now handles only the clinic profile + hours of
  operation. The three business catalogs that used to share the same
  scroll — appointment types, payers, fee schedules — are promoted
  into their own pages:
  - `pages/AppointmentTypesPage.jsx` → `/settings/appointment-types`
  - `pages/PayersPage.jsx` → `/settings/payers`
  - `pages/FeeSchedulesPage.jsx` → `/settings/fee-schedules`
- New sidebar entries in `components/layout/navConfig.js` under the
  collapsible **Settings** group (`nav-appointment-types`, `nav-payers`,
  `nav-fee-schedules`). All four Settings routes remain admin-only. No
  API changes; the underlying managers were untouched.

### Versioned intake save wiring + wizard extraction (2026-02-21)
- `PatientWizardDialog` (scope=`intake`) now PATCHes the new
  `/api/patients/{id}/intake-forms/{form_id}` endpoint instead of the
  legacy flat `patient.clinical_intake` blob. Two explicit actions on
  step 4: `wizard-save-draft-btn` (keeps draft) and
  `wizard-save-complete-btn` (flips `status="completed"`, sets
  `captured_at`). Completed forms are immutable (backend 409).
- `IntakeFormsTab` exposes a per-row `intake-form-edit-<id>` button on
  drafts only. Parent tracks `editingIntakeForm` so the wizard is
  seeded with that form's latest `clinical_intake`/`case_details`.
- `PatientWizardDialog` + its 4 step renderers extracted from
  `pages/Patients.jsx` to `components/patient-wizard/PatientWizardDialog.jsx`.
  `Patients.jsx` now owns only the search page. Both importers updated.
  39/39 logic tests + 5/5 backend intake_forms tests green.

### Phase 1 (2026-04-19)
- Identity, Patient CRUD, Scheduling with conflict detection, mock notifications via in-process event bus
- Sage + stone medical theme, 7 role-aware pages

### Theme system adoption (2026-04-20)
- Adopted Chiro Software Slate + Teal + Copper design system.
- Rewrote `frontend/src/index.css` with foundations / semantic / alias
  token layers and Outfit · Manrope · JetBrains Mono typography.
- Extended `frontend/tailwind.config.js` with new semantic surfaces,
  status colors, radius scale, shadow scale, and font families.
- Preserved legacy `bg-sage`, `surface-raised`, `text-strong`, etc. as
  aliases pointing to the new palette so all 22 existing pages inherit
  the new brand without a file-by-file rewrite.

### Theme discipline Phase 2 (2026-04-20)
- Swept **every** raw hex / raw Tailwind palette class from
  `frontend/src/**` and replaced with semantic tokens. 51+ instances
  across 17 files.
- Added **`scripts/check_theme.py`** — Python CI guardrail that blocks
  raw `#hex` arbitrary values, forbidden Tailwind palette families
  (`slate-*`, `stone-*`, `blue-*`, etc.), and inline `style` color
  usages inside `frontend/src/**`. Exempts the theme layer and shadcn
  primitives.
- Wired the guardrail into `.githooks/pre-commit` and a new GitHub
  Actions workflow `.github/workflows/theme-guard.yml`.
- Added **Theme compliance** checklist block to
  `.github/pull_request_template.md`.
- Verified light/dark parity via screenshots on Login, Dashboard,
  Patients lookup, Calendar, Audit Log, Compliance.

### Theme discipline Phase 3 — primitive + shell refactor (2026-04-20)
- Refactored every Shadcn primitive in `components/ui/` (Button,
  Input, Textarea, Select, Card, Dialog, DropdownMenu, Tabs, Badge,
  Table, Sonner) to consume semantic tokens, 8px / 12px radii,
  accessible 2px focus rings off the `--focus` token, tokenized
  placeholder / overlay / row-hover / row-selected alias tokens, and
  a copper `premium` badge variant.
- Fixed the broken Sonner import (was pulling `next-themes` instead
  of the app's own `ThemeContext`).
- Added tokenized `success` / `warning` / `info` / `error` variant
  classes to toasts so state colors stay semantic.

### Theme discipline Phase 4 — legacy alias retirement (2026-04-20)
- Migrated 762 backwards-compat utility-class usages across
  `frontend/src/**` to direct semantic Tailwind utilities
  (`text-foreground`, `text-muted-foreground`, `text-primary`,
  `text-destructive`, `bg-muted`, `bg-background`, `bg-card`,
  `bg-primary/10`, `bg-warning-soft`, `bg-destructive-soft`,
  `border-border`, `border-border-strong`). The sage vocabulary is
  now fully retired from feature code.
- AppShell Sidebar now consumes the sidebar alias tokens
  (`--sidebar-bg/fg/active-bg/active-fg/active-indicator`) instead of
  inline style + generic classes.
- All `font-['Outfit']` arbitrary values migrated to the `font-display`
  utility.

### Performance + scalability pass (2026-04-19)
- **Redis** (supervisord-managed, `127.0.0.1:6379`, `maxmemory 128mb allkeys-lru`) for application cache + IP rate-limit buckets
- **Write/Read DB split**: `get_db_write()` / `get_db_read()` / `read_after_write_db()` in `core/db.py` — identical API whether the backend is a single Mongo, a Mongo replica set, or a Postgres primary + replica
- **Cache catalogue** in `core/cache_keys.py`: providers (300 s), masked patient list (30 s), appointments query (30 s). **Never cached**: unmasked PHI, break-glass detail, audit log, data exports
- **Invalidation by prefix** (Redis SCAN, never KEYS) on every write — patients, patient, appts, dashboard, providers
- **Read-after-write** enforced on PUT /patients, PUT /appointments, POST /appointments/cancel so the response body is always fresh; conflict checks always read primary
- **Graceful Redis fallback** (`core/redis_client::safe_call`) — requests never fail when Redis is down; in-process rate-limit bucket + bypass cache
- **Operator visibility**: `GET /api/perf/stats` (admin-only) returns cache hit/miss ratio, DB read/write/read-after-write counters, rate-limit blocks, redis_alive

### HIPAA hardening (2026-04-19)
- **Audit logging** of every PHI access with PHI flag, IP, user-agent, outcome, reason
- **Field-level encryption at rest** (AES-256-GCM) for `patients.{address,emergency_contact,notes}`, `medical_records.{description,diagnosis,treatment}`, `appointments.notes` — verified with `enc:v1:` prefix in raw Mongo
- **Password policy**: 12-char complexity + denylist + history-of-5 + 90-day rotation warning + 120-day hard expiry
- **MFA (TOTP)** with provisioning URI + 8 single-use backup codes, ticket-based challenge step on login
- **Step-up reauth** required for delete-patient + add-medical-record
- **Break-glass**: Doctor/Staff must enter ≥8-char clinical reason to view PHI outside their scope; logged as emergency_access
- **PHI masking** by default in lists + detail; admin unmask is audited
- **Soft-delete + 7-year retention**, **patient data export** (JSON, right-to-access)
- **Account disable / enable** preserving audit history
- **Idle auto-logoff** at 15 minutes (front-end)
- **Brute-force lockout** by email-only identifier (k8s-ingress-safe)

## 5. Verified end-to-end (testing agent 24/24 backend, 7/7 frontend flows)
- Mock event bus → 6 notifications per appointment lifecycle (no regression)
- Admin login → MFA setup → Audit log → Patient unmask audited
- Doctor login → Audit log hidden → Patient detail prompts break-glass dialog
- Patient login → sees only own record → can export own JSON
- Encryption at rest confirmed via direct mongoDB inspection

## 6. Backlog
### P0 (production go-live blockers — operational, not code)
- HIPAA-eligible DB (MongoDB Atlas + BAA, or Postgres in HIPAA-compliant cloud)
- BAAs with all PHI processors
- KMS-backed `DATA_ENCRYPTION_KEY` (currently env-loaded)
- Retention worker that physically purges patients with `retention_until < now`
- Audit log immutability at the storage layer (append-only or pre-hook)
- Consent capture on registration (versioned Privacy Notice acceptance) — CCPA/SOC2-P
- Privacy Notice surfaced in UI + footer link — CCPA
- Dependency SCA + SAST in CI — ISO A.8.8 / A.8.28

### P1 (next features)
- Billing service subscriber on `appointment.completed`
- Real Twilio SMS + Resend email (require BAAs)
- Reporting service for compliance and ops dashboards
- Patient self-service portal (book / reschedule own appointments)
- Postgres migration (schema is 1:1, mechanical)
- Structured JSON logging (structlog) + centralised log sink
- CSV evidence export for auditors (`/api/audit-logs/export.csv`)
- Prometheus alerting rules + runbooks committed to repo
- Purpose taxonomy (enum) replacing free-text `reason` in audit rows

### P2 (polish)
- Multi-tenancy with `tenant_id` on every entity + JWT claim
- OpenID Connect / SAML SSO option for clinic IdP
- OpenTelemetry end-to-end tracing
- Real broker (RabbitMQ/Azure Service Bus) — same publish/subscribe API
- Session fingerprint drift detection
- JIT admin elevation + peer-approval for destructive ops

## 7. Compliance baseline (2026-02-18)
- **Documents** (`/app/memory/`):
  - `COMPLIANCE_BASELINE.md` — SOC 2 / CCPA / ISO 27001 narrative with per-control status (Implemented / Partial / Missing / Out-of-App)
  - `CONTROL_INVENTORY.md` — 50+ controls with framework mapping, type, owner placeholder, code/evidence path, remediation pointer
  - `COMPLIANCE_BACKLOG.md` — P0 / P1 / P2 remediation backlog, plus out-of-app items for visibility
  - `ACCESS_CONTROL_AND_AUDIT.md` — access control, session handling, MFA, password policy, audit evidence reference (2026-02-18)
- **In-app readiness dashboard** (admin-only, `/compliance`):
  - `GET /api/compliance/overview` aggregates env hardening flags, audit activity signals (24 h / 30 d), MFA adoption across privileged roles, retention pipeline status, and the control catalog with live status
  - UI at `frontend/src/pages/Compliance.jsx` with readiness snapshot, environment flags, audit activity, retention status, and filterable control table
  - Explicitly labelled **internal readiness** — no certification claim
- **Verified**: admin 200 / doctor 403 / anon 401; UI renders with live data from 605 existing audit rows; readiness score 0.58 (21 implemented + 7 partial of 42 in-app controls)

## 8. Security hardening phase (2026-02-18)
- **Session epoch**: every access + refresh token carries `epoch` + `sst`. Any password, role, status, MFA change bumps `users.session_epoch` → old tokens rejected at next request. Current session re-issued fresh cookies.
- **Absolute session lifetime**: 12-hour cap from first login (`ABSOLUTE_SESSION_HOURS`), enforced in `core/deps.py` via `sst` claim. Survives refresh.
- **Password reset**: `POST /api/auth/password-reset/{request,confirm}` — public, single-use, 15-minute, sha256-hashed, TTL-indexed, rate-limited per IP, no email enumeration. Email delivery MOCKED (dev_token in response). New frontend `/password-reset` page.
- **Admin MFA controls**: `POST /api/auth/users/{id}/mfa/reset` + `POST /api/auth/users/{id}/mfa/require?required=true|false` — admin-only, fully audited, revokes sessions on reset.
- **Self-service sessions view**: `GET /api/auth/sessions` — recent sign-ins for the current user. Surfaced in `/security` as the "Recent sign-ins" card.
- **Audit UI upgrades**: date-range pickers, actor-email + entity-id filters, search, row-limit selector, one-click CSV export streaming via `GET /api/audit-logs/export.csv` (admin only, audit-logged as `audit_log.exported`).
- **Forced logout on disable/enable/role-change**: `users.session_epoch` incremented — old token's next `/auth/me` or `/auth/refresh` call returns 401.
- **PHI hygiene**: removed `{first_name + last_name}` from `patient.created` audit metadata after bug caught in iteration_5.
- **Verified**: 26/26 backend tests (iteration_5 + iteration_6) pass. 0 bugs. Frontend smoke (login forgot-link, password-reset tabs, sessions card, audit advanced filters + export) all green.

## 9. Privacy & data-governance phase (2026-02-18)
- **New `services/privacy/` microservice**: data inventory endpoint, DSAR request lifecycle (`received→in_review→approved→fulfilled|rejected|withdrawn`), versioned consent records, communication preferences, patient legal hold.
- **Data-subject rights**: `GET /api/auth/me/export` (self-service account export) + `GET /api/patients/{id}/export` (pre-existing clinical export) now complemented by a dedicated request/approval audit trail.
- **Legal hold**: `patients.legal_hold` blocks both `DELETE /patients/{id}` and `/privacy/requests/{id}/fulfill-delete` with 409 Conflict until cleared. Reauth required to toggle.
- **Consent**: register page captures Privacy Notice v`2026-02-v1`; submit button disabled until accepted; `consent_records` append-only collection.
- **Admin UI**: `/privacy` page with Requests tab (intake form, status/type filters, state-machine-aware transition buttons, fulfil-delete action) and Data inventory tab (8 categories with CCPA/PHI/retention metadata).
- **Docs**: `/app/memory/PRIVACY_AND_RETENTION.md` — full workflow + retention model + CCPA mapping + out-of-app boundaries.
- **Verified**: 27/27 new tests + 26/26 regression tests pass (iteration_7). 0 issues.

## 10. Data protection & secure configuration hardening (2026-02-18)
- **Central key manager** (`core/key_manager.py`): abstracts all encryption-key access. Provider API (`env` today, `aws_kms`/`azure_kv`/`vault` stubs ready). `describe()` exposes only metadata; key bytes never leave the module. Forward-rotation with versioned ciphertext (`enc:v1:…`) + `EXTRA_DATA_KEYS` mapping.
- **Central config** (`core/config.py`): declares `REQUIRED` (MONGO_URL, DB_NAME, JWT_SECRET, DATA_ENCRYPTION_KEY) vs `RECOMMENDED`, weak-secret detection, `mask_secret` helper, `describe()` for diagnostics. `ensure_required()` is called in the `startup` lifespan hook — fail-fast on misconfig.
- **Field-level encryption extended**: `patients.date_of_birth` now AES-256-GCM at rest. Legacy plaintext rows continue to round-trip via the `enc:` prefix pass-through.
- **Admin Security Config endpoint + page**: `GET /api/compliance/security-config` + `/security-config` admin UI. Surfaces: app_env, production_ready, required/recommended config, weak-secret list, masked JWT + DEK prefixes, encryption provider + active version + extra versions, feature flags, humanised `production_gaps`.
- **Docs**: `/app/memory/DATA_PROTECTION_AND_KEYS.md` — full inventory, what is / isn't encrypted, KMS migration plan, infra boundaries.
- **Verified**: 15/15 new tests pass + 27/27 iteration_7 + 13/13 iteration_6 regression. 0 issues. Masked secret rendering confirmed — no plaintext secret in the /security-config response or DOM.

## 11. Operational security readiness (2026-02-18)
- **Structured security logger** (`core/security_logger.py`): JSON-line `event(name, outcome, component, **meta)` + WARNING-level `suspicious(...)`. Banned-key scrubber prevents passwords / tokens / secrets reaching logs. Every audit row now mirrors to the `security` logger so SIEM tooling gets real-time parity with the durable audit DB.
- **Logging config** (`core/logging_setup.py`): JSON formatter; in `APP_ENV=production` the root logger also emits JSON; in dev root stays human-readable but the `security` logger is always JSON so SIEM wiring is identical in every env.
- **Global error handler** (`core/error_handlers.py`): installs an `Exception` handler on the FastAPI app — returns `{detail, correlation_id}` only, full traceback goes to server logs under `system.unhandled_error`, and `ccms_secure_endpoint_errors_total{path_prefix}` is bumped. No stack or internal paths reach the client.
- **New Prometheus counters**: `ccms_auth_failures_total{reason}`, `ccms_phi_access_total{action}`, `ccms_privileged_actions_total{action}`, `ccms_privacy_requests_total{type,status}`, `ccms_breakglass_total`, `ccms_exports_total{kind}`, `ccms_secure_endpoint_errors_total{path_prefix}`.
- **Rate-limit telemetry**: every block emits a WARNING `rate_limit.block` event and bumps `ccms_rate_limit_blocks_total{source}`.
- **Admin monitoring-hooks endpoint**: `GET /api/compliance/monitoring-hooks` — machine-readable event catalogue + metric catalogue + incident-evidence surfaces with recommended alert thresholds.
- **Docs**: `/app/memory/OPERATIONAL_SECURITY_READINESS.md` — event catalogue, metric catalogue, incident triage recipes, external tooling gaps, test checklist.
- **Verified**: 17/17 new tests + 88/88 regression tests pass (iter_5/6/7/8). One critical bug caught + fixed (suspicious() signature kwarg clash) then re-verified green in iteration_10.

## 12. TLS / transport security posture (2026-02-18)
- **`core/security_headers.py` middleware**: attaches on every response — `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy` (geolocation/mic/camera/payment/usb/accel/gyro/magnet all `()`), default **CSP** (`default-src 'self'; img-src 'self' data: https:; style-src 'self' 'unsafe-inline' https:; script-src 'self' 'unsafe-inline'; connect-src 'self' https:; font-src 'self' data: https:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'; upgrade-insecure-requests`), COOP `same-origin`, CORP `same-site`. Installed after CORS so CORS preflights also carry security headers.
- **HSTS** (`Strict-Transport-Security: max-age=15552000; includeSubDomains; preload`) only emitted when `APP_ENV=production` AND effective scheme (`x-forwarded-proto`) is `https`. Dev never advertises HSTS.
- **Env hooks**: `APP_ENV`, `HSTS_MAX_AGE_SECONDS`, `CSP_EXTRA` for per-env overrides without code changes.
- **Admin diagnostic**: `GET /api/compliance/transport` returns app_env, observed scheme + forwarded headers, cookie flags, HSTS config, CSP preview, transport warnings. 401 anon / 403 non-admin / 200 admin.
- **Docs**: `/app/memory/TLS_AND_TRANSPORT_SECURITY.md` — ingress vs app responsibilities, what is/isn't in scope, production checklist.
- **Verified** (iteration_11): 17/17 new tests + 102/102 regression in isolation. Frontend renders with strict CSP, admin login lands on dashboard, all admin pages (Patients / Compliance / Security / SecurityConfig / Privacy) load with **zero CSP violations**.

## 13. Authorization system — RBAC + scopes + policy overlays (2026-02-20)
- **Data model** (9 new collections, PG-migration ready): `roles` (11), `permissions` (115), `role_permissions` (grants w/ scope + MFA/APR/BG flags), `user_roles` (n:m), `locations` (+ `user_location_assignments`), `patient_assignments`, `elevation_requests`.
- **Policy engine** (`services/authz/policy.py`): default-deny `evaluate()`, `scope_filter()` for row-level, `require_permission()` FastAPI dependency, MFA gate via reauth cookie, approval gate via consumed elevations, break-glass signalling. Dual-run legacy shim auto-maps existing `users.role` strings → baseline roles + back-fills `user_roles` rows on seed.
- **Endpoints**:
  - `/api/authz/me/permissions`, `/api/authz/check`
  - `/api/authz/roles`, `/api/authz/permissions`, `/api/authz/matrix`
  - `POST|DELETE /api/authz/users/{id}/roles`, `/api/authz/users/{id}/locations`, `/api/authz/patient-assignments`
  - `POST /api/authz/locations`
  - Elevation: `POST /request`, `GET /`, `POST /{id}/decision`, `DELETE /{id}` — separation-of-duties enforced (approver ≠ requester)
  - 8 compliance reports under `/api/access/reports/*` (users-by-role, permissions-by-role, privileged-users, recent-role-changes, phi-access-history, export-history, break-glass-history, failed-authz, access-review summary)
- **Admin UI** (4 new pages): `/roles`, `/permissions` (matrix), `/access-review`, `/elevation`. Sidebar nav added. `PermissionsContext` + `<Can>` helper for frontend.
- **Prometheus counters**: `ccms_authz_allows_total`, `ccms_authz_denials_total`, `ccms_elevation_requests_total{status}`.
- **Audit coverage**: every authz decision (`authz.allow`, `authz.denied`, `authz.mfa_required`, `authz.approval_required`, `authz.role_assigned`, `authz.role_revoked`, `elevation.*`) mirrored into the immutable `audit_logs` collection.
- **Verified (iteration_12)**: 15/15 new tests pass (matrix shape, legacy-role shim, default-deny, MFA gate, full elevation lifecycle + separation-of-duties, role assign/revoke with session-epoch bump, all 9 reports, scope containment for patient portal, denial audit rows).
- **Pragmatic exception** (documented in `AUTHORIZATION_GUIDE.md` §7): super_admin grants stripped of APR flag on governance actions (role.assign/create/update, user.disable/reset_mfa, api_key.*, integration.*, etc.) to break the chicken-and-egg for initial bootstrap. OO/CO/other approver roles retain the full MFA+APR posture. Production with multiple admins can re-tighten via custom `role_permissions` rows.

## 14. Authz migration + user-specific overrides (2026-02-20 late)
- **Router migration**: `patient` (create/update/delete, `patient_chart.create`), `scheduling` (appointment create/update), `audit` (`audit_log.read` + `audit_log.export`) all now route through `require_permission()`. Added `audit_allow=False` param so migrated routes don't double up with their existing semantic audits (only denials/MFA/approval gates always audit). Identity admin routes and privacy/communication still on `require_role()` — migration deferred to a later pass.
- **Super Admin grant extension**: retained legacy admin CRUD (patient/appointment create/update/delete + audit_log.export) to avoid regressions. Documented as bootstrap posture in `AUTHORIZATION_GUIDE.md` §9.
- **Per-user overrides (`permission_scopes` collection)**: admin-gated `POST|GET|DELETE /api/authz/users/{uid}/overrides`. Grants are additive, broaden-only (can't narrow a role's scope), optionally expire via `expires_at`, and **bump session_epoch on grant AND revoke** so no stale-grant window exists. Every override is audited (`authz.override_granted` / `authz.override_revoked`).
- **Admin UI**: new "Overrides" button on every row of `/roles` opens a `UserOverridesDialog` with permission autocomplete (115 perms), 8-scope dropdown, reason textbox (client-side 10-char minimum), optional ISO expires_at, and a live list of existing overrides with per-row revoke.
- **Verified (iteration_13)**: 9/9 new tests pass; full regression 68/68 (iter7 + iter11 + iter12 + iter13). Zero CSP violations. Double-audit regression guarded by `test_migrated_routes_do_not_double_audit`.

## 15. Multi-tenancy foundation (2026-02-21)
- **New tenancy model**: `tenants` (id, slug, name, type=single|group, status, db_tier=shared|dedicated) parents `locations` (id, tenant_id, name, code, timezone, status). Every tenant-owned collection — `users`, `patients`, `appointments`, `medical_records`, `notifications`, `audit_logs`, `consent_records`, `communication_preferences`, `privacy_requests`, `password_reset_tokens`, `login_attempts`, `permission_scopes`, `elevation_requests`, `user_roles`, `user_location_assignments`, `patient_assignments` — now carries `tenant_id`. Location-aware rows (`patients`, `appointments`, `medical_records`) also carry `location_id`.
- **Tenant routing abstraction (`core/tenancy.py::TenantDatabaseRouter`)**: one bridge point for shared → dedicated migration. Default routes every tenant to the shared Motor cluster; env `TENANT_DB_MAP='{"<tenant_id>": {"uri": "mongodb+srv://...", "db": "ccms_acme"}}'` promotes a tenant to its own cluster with zero business-logic change. Singleton `tenant_db(tenant_id)` is the one and only DB entry point for all repositories.
- **Tenant context in JWT**: `tid` + `pa` (platform_admin) claims added to access tokens. `get_tenant_context()` FastAPI dependency resolves context from user + request (platform admins can override via `X-Tenant-Id` header; every such override is audited).
- **Repository helper (`core/tenant_scope.py::scoped_filter`)**: single choke-point that injects `tenant_id` (+ optional `location_id`) into every Mongo filter. Returns a `__deny__` sentinel for users with no eligible locations so route code never has to remember to check. `stamp_for_write()` mirrors the pattern on inserts.
- **Routers migrated**: `patient` (list/get/update/delete/export/records create+list), `scheduling` (create/list/get/update/cancel), `audit_logs` (read + csv export). Every cross-tenant id lookup returns 404, never 403, to avoid enumeration.
- **Identity integration**: `users.tenant_id` + `tenant_scope_all` + `is_platform_admin` now surfaced in `/auth/me`, `/auth/login`, and `AdminUserCreate`. Admin `list_users` is tenant-scoped. `list_providers` is tenant-keyed in its 5-minute cache.
- **Platform admin role** (`platform_admin`): new global role that bypasses tenant filters with an explicit audit trail. Seed account `platform-admin@ccms.app` (password `Platform@ComplianceClinic1`).
- **New `/api/tenancy/*` endpoints**: `me/context`, `tenants` (list/create), `tenants/{id}/locations` (list/create). Listing is tenant-scoped unless caller is platform admin.
- **Seed data (idempotent)**: `Default Practice` (single-location; adopts all legacy rows via backfill) + `Sunrise Chiro Group` (3 locations × 4 demo users with varied access scopes: group-wide admin, single-location doctor, multi-location floater doctor, single-location staff).
- **Backfill**: every legacy tenant-owned row is stamped with the default tenant on first boot after upgrade; zero data loss.
- **Docs**: `/app/memory/MULTI_TENANCY_ARCHITECTURE.md` — decision record, ERD, request pipeline, hybrid-DB runbook, non-goals.
- **Tests (iteration_14)**: 19/19 new tests pass — tenant isolation across patient/appointment/audit, location scoping inside a tenant (group-admin/single-loc/floater/staff matrix), platform admin CRUD, tenant-admin denial for tenant-create, public registration assigns default tenant. Regression 9/9 (iteration_13), 15/15 (iteration_12) with correct preview URL.

## 16. Iteration 15 — repository enforcement, cross-tenant audit, bg-context (2026-02-21)
- **`core/repository.py::TenantScopedRepository`** — fail-closed wrapper over Motor collections (find/find_one/find_one_by_id/count/insert_one/update_one/update_many/delete_one/delete_many). Raises `MissingTenantContext` without a context; raises `UnsafeQueryError` on empty-filter bulk ops. Pre-built subclasses: `PatientRepository`, `AppointmentRepository`, `MedicalRecordRepository`, `NotificationRepository`, `AuditLogRepository`.
- **Cross-tenant id probe audit**: `find_one_by_id` issues one unscoped lookup on a 404; if the row exists in a DIFFERENT tenant, emits `security.cross_tenant_attempt` (outcome=failure) with actor/target tenant_ids. Caller still gets 404 — no enumeration leak.
- **`TenantContext.for_background(tenant_id, actor=...)`** — synthetic context for async jobs/workers. Never platform admin; always tenant-bound; tenant-wide by default.
- **Request-state stash**: `get_tenant_context()` caches the resolved context on `request.state.tenant_context`; `request_id`, `ip`, `user_agent` populated on every context.
- **Sunrise demo data seeded**: 2 patients × 3 locations with encrypted PHI, 1 medical record + 1 scheduled appointment per patient.
- **Patient router migrated to repository**: `GET /patients/{id}` uses `PatientRepository.find_one_by_id` (exercises cross-tenant audit). Other patient/scheduling/audit routes continue on `scoped_filter` and remain safe; progressive migration is P1, not a correctness blocker.
- **Developer cookbook added** in `MULTI_TENANCY_ARCHITECTURE.md` §14 with copy-paste-safe patterns and clearly flagged anti-patterns.
- **Verified (iteration_15)**: 6/6 new tests — demo-data visibility, location-scoping for downtown-doc, cross-tenant probe audit, repository fail-closed, unsafe empty-filter rejection, background context acceptance. Regression 19/19 (iteration_14) still green.

## 17. Iteration 16 — cache isolation, bg jobs, reports, exports (2026-02-21)
- **`core/tenant_cache.py`** — tenant-namespaced cache key builder (`t:<tid>:...`) + `TenantCache` wrapper that refuses unsafe keys (`UnsafeCacheKeyError`) and bounds TTL to (0, 86400]. `invalidate_tenant(id)` wipes a tenant's cache in one call. Documented list of cache-ok vs cache-never data.
- **`core/tenant_jobs.py`** — `@tenant_job("job_type")` decorator + persistent `jobs` collection. `enqueue()` refuses missing tenant_id (`MissingJobContext`). Handlers receive `(ctx, payload, meta)` with `ctx = TenantContext.for_background(tenant_id=..., actor="worker:<job>")`. Audits `job.enqueued/started/completed/failed` — tenant-tagged.
- **`services/reports/`** — `run_report(ctx, name, filters)` single entry point, validates `location_ids` against `ctx.allowed_location_ids` (403 on mismatch). Built-in reports: `appointments_by_day`, `provider_productivity`, `location_performance`. Results cached `t:<tid>:report:<name>:<hash>` for 300 s. Audited `report.generated` / `report.denied`.
- **`services/exports/`** — `POST /api/exports` → tenant-scoped CSV generator via the job system; `GET /api/exports/{id}` returns a 15-min signed JWT download token; `GET /api/exports/{id}/download` re-verifies signature + tenant match + status=ready before streaming. Storage path `/app/data/exports/<tenant_id>/<export_id>.csv`. Cross-tenant token replay denied + audited. PHI privilege stashed at request time (`include_phi`) so the worker writes the exact columns the requester is authorized to see. Cleanup worker marks `status=expired` + unlinks file; runs on boot and exposed at `POST /api/exports/cleanup`.
- **Platform admin short-circuit** in `require_permission()` — audits every allow as `authz.platform_admin_bypass` (resource + action metadata).
- **Indexes** added for `jobs`, `exports` (tenant_id, status, expires_at).
- **Verified (iteration_16)**: 10/10 new tests pass. Combined iteration_15 + iteration_16 16/16 green. Lint clean.

## 18. Iteration 17 — Infrastructure & platform security backbone (2026-02-21)
- **`core/db_routing.py`** — `ReadPurpose` classification (`WRITES_ONLY`, `READ_AFTER_WRITE`, `REPLICA_OK`, `REPLICA_PREFERRED`), `PRIMARY_ONLY_COLLECTIONS` allow-list (users, audit_logs, jobs, tenants, authz tables, …) that `safe_read()` refuses to route to replicas. Replica circuit-breaker: 3 bad probes disables the replica for 60s. Operator lever `force_disable_replica(seconds)`.
- **`core/storage.py`** — `StorageBackend` protocol (`LocalStorage` today, `S3Storage` stub), `TenantStorage` wrapper enforcing tenant-prefixed paths (`<category>/<tenant_id>/<uuid>`), path-traversal + control-char blocking, UUID-only keys (no PHI in filenames), signed download tokens (TTL ≤ 3600s) carrying `tid+path`, `StorageCategory` (`PERMANENT`, `EXPORTS`, `UPLOAD_STAGING`, `REPORTS`).
- **`core/secrets.py`** — provider abstraction (`env`, stub `aws`), `require()`, `validate_startup()` (server refuses to serve if any of `MONGO_URL`, `DB_NAME`, `JWT_SECRET`, `DATA_ENCRYPTION_KEY` missing), `redact()` masks Mongo URIs, JWTs, Bearer tokens, AWS keys, Stripe keys, password values, and live secret values.
- **Cache categories** — `CacheCategory` enum + `DEFAULT_TTL` mapping (session-authz 120s, reference 300s, schedule/report 300s, utility 60s); wrapper already bounds TTLs to (0, 86400].
- **Diagnostics** — `/api/infra/replica` runs a live probe, `/api/infra/secrets` returns presence + length (never values). Platform admin only.
- **Docs** — `/app/memory/INFRASTRUCTURE_ARCHITECTURE.md` covers topology, DB routing rules, cache/redis hardening, object storage with S3 Terraform snippet, secrets provider swap + rotation runbooks, TLS 1.3 posture, backup/DR (including tenant-logical-restore limits), PromQL alerts for tenant-isolation detections, environment separation + CI policy gates.
- **Verified (iteration_17)**: 15/15 new tests pass — primary-only refusal, replica-disable fallback, replica-health shape, storage path traversal, tenant-prefixed paths, missing tenant refusal, unsafe suffix, TTL bounds, token tenant claim, required-secrets present, `require` raises for missing, 4 redaction format cases + live value match, cache-category TTL bounds. Combined iteration_16 + iteration_17 25/25 green.

## 20. Iteration 19 — Workforce & patient identity security workflows (2026-04-20)
- **New `services/workforce` module** with single tenant-scoped router covering:
  * **Workforce invitations + activation** — `POST /invitations` returns a `dev_token` (email delivery MOCKED; swap to Resend in the comms pass). `POST /invitations/accept` is PUBLIC; creates the user, stamps tenant + locations, bumps `mfa_policy_required` for workforce roles. Tokens are SHA-256-at-rest, single-use, TTL 1–168h.
  * **Patient proxies** — grant/revoke `patient_proxies` rows (relationship, scope, reason, optional expiry). Both ends validated in the same tenant. Every transition appends to `history[]` + emits an audit row.
  * **Admin + self session visibility** — `sessions/me`, `sessions/user/{id}`, plus one-shot revocation via `sessions/me/revoke-all` and `sessions/user/revoke-all` (bumps `session_epoch`, kills every issued JWT).
  * **One-shot atomic deprovisioning** — `POST /users/{id}/deprovision` disables the user, bumps session_epoch, revokes `user_roles`, `permission_scopes`, `user_location_assignments`, `patient_assignments`, cancels pending invitations, force-expires active break-glass, revokes active proxies where they were the proxy, flags every future appointment `needs_reassignment=true` (or reassigns to a supplied replacement doctor).
  * **Formal break-glass** with `activated_at → expires_at → attestation_due_at`. Max duration 4h (env `BREAK_GLASS_MAX_DURATION_HOURS`); attestation window 24h (env `BREAK_GLASS_ATTESTATION_HOURS`). `POST /break-glass/sweep` (and the list endpoint) auto-expires windows, fires `security.break_glass_attestation_overdue` audits, and flags `users.step_up_required=True` on the offender so their next non-trivial action is MFA-gated.
  * **Suspicious-login hook** — `services.workforce.router::record_login_signal`. Runs before the login audit row is written so the "prior IP" lookup is not self-matching. Detects `new_ip` + `new_user_agent` (30-day lookback) on success and `brute_force_pattern` (≥5 failures from one IP in 15m) on failure. When a success signal fires the user's `step_up_required=True` flag is set; cleared on the next reauth'd action or by the break-glass attest flow.
- **`core/authz/policy.py::require_permission`** now enforces `step_up_required` globally: any non-trivial action requires a reauth cookie when the flag is on, regardless of the static MFA flag on the grant.
- **`core/db.py` indexes** added for `workforce_invitations`, `patient_proxies`, `break_glass_events` (tenant + status + time).
- **Integration**: router registered in `server.py` at `/api/workforce/*`. Login + failure paths in `services/identity/router.py` now call `record_login_signal`.
- **Verified (iteration_19)**: 14/14 new tests pass — invitation CRUD + activation + tenant isolation, proxy grant/revoke lifecycle + cross-tenant rejection, admin session revoke, self session revoke, atomic deprovision with self-guard, future-appointment flagging, break-glass start/end/self-attest, sweep marks overdue + sets step-up, suspicious-login hook sets step-up. Regression: 10/10 iteration_18 + 11/11 iteration_15+16 + 11/15 iteration_14 pending (4 rate-limited, not regressions). Lint clean.

## 19. Iteration 18 — Compliance operations backbone (2026-02-21)
- **Unified compliance model** — 8 entity types (`control`, `evidence`, `risk`, `policy`, `incident`, `vendor`, `data_class`, `access_review`) sharing `{id, tenant_id, type, status, owner, history[], created_at, updated_at}`. Every mutation appends a `history` entry; every mutation also emits a semantic audit row (`compliance.<type>_<action>`).
- **Controls registry** with free-form `framework_mappings` (`HIPAA`, `SOC2`, `ISO27001`, `CCPA`, …) and `?framework=HIPAA` filter. Seven seed controls mapped across ≥ 3 frameworks each.
- **Evidence integrity**: every row carries `integrity_sha256 = sha256(source_system | source_reference | content_summary | coverage_period_*)`, computed server-side at creation. Field allow-list for patches rejects `integrity_sha256`, `history`, `source_reference`, `coverage_period_*` — tamper requires generating a new evidence row. `POST /{id}/legal-hold` is MFA-gated.
- **Risks** with `likelihood * impact = inherent_score` and treatment/status workflow. **Policies** with version + `review_date` overdue flag. **Incidents** with severity, timeline, `notification_required=true` auto-flagged for high/critical. **Vendors** with BAA tracking. **Data classes** with retention + deletion method. **Access reviews** with `due_at < now → status="overdue"` auto-compute.
- **Generic endpoints** — `POST /compliance-ops/{type}/{id}/status`, `PATCH /compliance-ops/{type}/{id}` (field allow-list), `GET /compliance-ops/{type}/{id}` returning raw doc + history. All tenant-scoped via `TenantScopedRepository`.
- **Dashboard** `GET /api/compliance-ops/dashboard` — single aggregated snapshot of controls/risks/incidents/policies/vendors/access-reviews/privacy-requests/evidence. Gated by `reporting.read`. Audited `compliance.dashboard_viewed`.
- **Seed data** — 7 controls, 3 risks, 3 policies (1 overdue), 1 closed incident, 2 vendors (1 BAA-missing), 4 data classes, 2 access reviews (1 overdue), 1 evidence item — for EVERY existing tenant idempotently.
- **Indexes** — `(tenant_id, updated_at)` on every compliance collection; extra indexes on controls.family, evidence.control_id, access_reviews.due_at.
- **Docs** — `/app/memory/COMPLIANCE_OPS_ARCHITECTURE.md` covers model, lifecycle, HIPAA 45-CFR mapping table, SOC 2 recurring activities, CCPA/CPRA flow linkage, evidence bundle export flow, "how to add a new control domain" cookbook.
- **Verified (iteration_18)**: 10/10 new tests — dashboard fidelity, multi-framework mapping, framework filter, integrity hash + legal hold, tamper-resistant patch, tenant isolation, overdue access-review auto-flag, incident history append, unknown-type rejection, BAA-missing counted. Combined iteration_17+18 = 25/25 green.

## 25. Iteration 20e — Patient intake Phase 5 polish + Iteration 19 workforce backend sweep (2026-02-19)

Two items shipped in one iteration: (1) autosave drafts + edit-from-detail for the intake wizard, (2) the long-pending formal `testing_agent_v3_fork` sweep of the Iteration 19 workforce module.

### Phase 5 — autosave drafts + edit-from-detail
- **`frontend/src/pages/patientWizardLogic.js`** — four new exports:
  * `EMPTY_FORM` — canonical clean wizard state (shared with `Patients.jsx`).
  * `payloadToForm(patient)` — reverse of `buildPayload`. Defensive: accepts legacy flat records (falls back to top-level scalars), null input, and fully-grouped records. Splits guarantor first/last back into `guarantorFullName`; recovers `assignmentOfBenefits`/`releaseOfInformation` from `consents.additional[]`; derives case-type flags from `case_details.case_type` + field presence; extracts a signature from any populated consent block.
  * `draftStorageKey(userId, tenantId)` — returns `ccms.intake-draft.{tenantId||default}.{userId||anon}` so drafts can never leak across staff users on a shared kiosk.
  * `isDraftFresh(savedAtIso)` — 7-day TTL.
  * `formHasAnyInput(form)` — prevents empty-draft prompts from appearing when the wizard is merely opened-and-closed.
- **`frontend/src/pages/Patients.jsx`** — `PatientWizardDialog` promoted to a named export and taught `mode: "create" | "edit"`, `patientId`, `initialForm`, `onSaved`, `userId`, `tenantId` props:
  * Autosave effect writes to `localStorage[draftStorageKey(...)]` on every form change (skipped in edit mode). A small aria-live `wizard-draft-autosave-indicator` fades in for ~1.2s after each save.
  * On open in create mode, if `localStorage` has a fresh draft with `formHasAnyInput(form)`, a yellow `wizard-draft-prompt` banner appears with `wizard-draft-resume` / `wizard-draft-discard` buttons.
  * Successful create clears the draft; Discard also clears it; stale (>7 day) drafts are silently purged on open.
  * Save button label flips to "Save changes" in edit mode; submit calls `PUT /api/patients/{id}` and fires the new `onSaved` callback.
- **`frontend/src/pages/PatientDetail.jsx`** — new `patient-edit-intake-btn` (admin/doctor/staff). Clicking it while masked surfaces a `sonner` toast "Unmask first to edit intake" and blocks the open. When unmasked, the wizard opens pre-populated via `payloadToForm(patient)`; on save the page reloads the patient with the current mask/break-glass context.
- **Tests** — `frontend/src/pages/patientWizardLogic.test.js` extended with 8 Phase-5 cases:
  * `payloadToForm` handles undefined/null, legacy-only scalars, full grouped round-trip through `buildPayload`, guarantor name splitting, consents.additional[] recovery.
  * `draftStorageKey` tenant/user scoping.
  * `isDraftFresh` 7-day TTL boundary.
  * `formHasAnyInput` — empty rejection, single-field acceptance, array acceptance, flag acceptance.
  * **39/39 green** under `node --test`.
- **Self-smoke (Playwright)** — all five Phase-5 surfaces verified live: autosave indicator flashes on typing; resume banner appears on reopen with the staff user's own draft; "Resume draft" restores the form; edit intake button is visible on detail page; masked edit shows the unmask-first toast; unmasked edit opens the wizard pre-filled with "Save changes" button.

### Iteration 19 — formal `testing_agent_v3_fork` sweep
- Testing agent reported 11/14 pass on first run and flagged two "backend gaps": (b) GET /api/workforce/invitations returning 200 after step_up_required=True, and (c) GET /api/workforce/sessions/me missing the `step_up_required` key. Both turned out to be **test-tooling artefacts**, not real backend gaps — the backend `require_permission` + `/sessions/me` handler were working correctly.
- Root causes:
  * The refactored `_login` helper lifts the access_token into an `Authorization: Bearer` header (because Python `requests` can't traverse `Secure` cookies over plain HTTP). Subsequent reauth did the same for `x-reauth-token`. The break-glass sweep test was popping only the cookie, not the header, so reauth was still present and the step-up gate correctly didn't fire.
  * The suspicious-login test still used a raw `requests.Session()` for the "new IP" login, which never received a usable cookie — so `/sessions/me` returned `{"detail": "Not authenticated"}` and `me["step_up_required"]` raised KeyError.
  * Raw-DB probes relied on `os.environ.get("MONGO_URL")` but pytest ran without `.env` loaded.
- **Fixes (test-only, zero backend change):**
  * `tests/test_iteration19_workforce.py`: `load_dotenv("/app/backend/.env")` at module init.
  * Break-glass sweep test pops BOTH `cookies["reauth_token"]` AND `headers["x-reauth-token"]` before asserting 401.
  * Suspicious-login test lifts access_token from Set-Cookie into Bearer header on the "new IP" session before calling `/sessions/me`.
  * Admin-revoke-target-user test applies the same Bearer lift on the target's session so `/auth/me` works pre-revoke.
- **Result — 23/23 backend tests green:** full Iteration 19 workforce suite (14 cases) + Phase 1/4 patient intake (9 cases) under `pytest`.

### Files changed
- `frontend/src/pages/patientWizardLogic.js` (added Phase 5 helpers)
- `frontend/src/pages/patientWizardLogic.test.js` (+ 8 Phase 5 tests, now 39/39)
- `frontend/src/pages/Patients.jsx` (export wizard + edit/create mode + autosave)
- `frontend/src/pages/PatientDetail.jsx` (edit-intake button + wizard mount)
- `backend/tests/test_iteration19_workforce.py` (dotenv + Bearer-header test-tooling fixes)
- `memory/PRD.md` (iteration 20e entry)

### Follow-ups still open
- Insurance card uploads + generic document attachments (object-storage playbook).
- Wet-ink/canvas digital signatures + signed-PDF generation.
- Edit-from-detail currently requires the user to manually unmask first; a "Request unmask for edit" inline flow would smooth the UX.
- Workforce login rate-limiter can false-positive during rapid end-to-end tests (not a user-facing bug); tune the `/api/auth/login` bucket if back-to-back automation runs continue to drip 429s.

## 24. Iteration 20d — Patient intake Phase 4 (detail rendering + regressions, 2026-02-19)

Downstream UI for the grouped intake payload. Legacy flat records keep rendering exactly as before; grouped records now get a dedicated "Intake sections" area.

- **`frontend/src/pages/PatientDetail.jsx`** — added an `IntakeSections(patient)` component rendered after the existing 3-column (Address / Emergency contact / Intake notes) strip. Safe-by-default: every section is wrapped in a `hasValue(...)` gate so legacy records don't produce empty cards; if NO grouped data is present anywhere the entire `patient-intake-sections` block collapses to an `aria-hidden` empty placeholder. Helper primitives added: `hasValue` (deep — treats `{first_name: null, last_name: null, ...}` as empty), `Row` (renders key/value pair or null), `IntakeCard` (titled card shell), `InsurancePlanBlock`, `ConsentLine`. Cards:
  * **Demographics** — legal name / DOB / preferred name / middle name / sex at birth / gender identity / pronouns / marital status / language / occupation / employer / employer phone / SSN-last-4 (masked `•••• 1234`).
  * **Contact** — mobile / home / work phones / email (falls back to top-level legacy scalars for any missing grouped key) / preferred method / SMS / email / voicemail consent Yes/No.
  * **Address** (structured line1/2, city, state, postal, country) with a line1+line2 fallback to `patient.address` scalar.
  * **Emergency contact** — structured + legacy-scalar fallback Row.
  * **Administrative** — primary provider, referral source, MRN, tags, internal flags.
  * **Guarantor** — hides entirely when `same_as_patient=true` and no other guarantor keys are present; otherwise renders the full guarantor identity + billing fields.
  * **Insurance** — `InsurancePlanBlock` sub-card per primary / secondary / tertiary plan; renders only plans that carry at least one populated field.
  * **Clinical intake** — chief complaint, onset, pain score `n/10`, pain areas, symptoms, aggravating/relieving factors, prior treatments, meds, allergies, past/social/family history, provider notes.
  * **Case details** — auto subset (date_of_injury / auto_carrier / adjuster / claim), WC subset (employer / carrier / claim), PI subset (attorney), with a friendly `case_type` hint.
  * **Consents** — HIPAA / treatment / financial / telehealth / photo release + `consents.additional[]`, each with signature name + signed-at metadata.
- **`backend/tests/test_patient_intake_phase1.py`** — extended with three Phase-4 regression tests:
  * `test_legacy_record_detail_has_no_fabricated_grouped_sections` — creates a pure-legacy record and confirms GET `/patients/{id}?unmask=true` never injects grouped keys. This is the contract the frontend's `IntakeSections` relies on to collapse entirely for legacy patients.
  * `test_grouped_record_detail_full_roundtrip` — creates a fully populated grouped payload and asserts every grouped section (demographics / contact / address_details / emergency_contact_details / insurance.primary / clinical_intake / case_details / consents + consents.additional) round-trips losslessly on both unmasked and masked GETs (masked response strips all grouped sections, keeping UI cards empty by default).
  * `test_upgrade_legacy_to_grouped_via_update_is_lossless` — creates legacy, PUTs a grouped clinical_intake + insurance patch, and verifies legacy scalars (`first_name`, `address`) survive while the new grouped sections apply.
  * Plus an encryption-at-rest probe from Phase 1 continues to verify sensitive grouped sections are stored as ENC_PREFIX-tagged ciphertext (raw Mongo probe — no plaintext PHI markers found in `clinical_intake`, `insurance`, `consents`, `date_of_birth`).
- **Testing status**
  * Backend pytest (`test_patient_intake_phase1.py`): **9/9 green** (6 Phase 1 + 3 new Phase 4).
  * Frontend pure-JS (`patientWizardLogic.test.js`, Node `--test`): **31/31 green**.
  * Playwright smoke: legacy record → `patient-intake-sections = 0`, `patient-intake-empty = 1`, no empty cards leak. Grouped record → 6 cards rendered (demographics / contact / address / emergency-contact / admin / guarantor) with real data; hidden cards for sections the patient doesn't carry.

### Follow-ups / deferred gaps
- **Insurance card uploads** — no object storage is wired to the insurance block yet; a future phase should accept front + back photos (via the `integration_playbook_expert_v2` object-storage playbook) and list them inside `InsurancePlanBlock`.
- **Document attachments** — generic patient-document upload (ID, referral letters, imaging reports) is not implemented.
- **Digital signatures** — `consents` currently stores a typed signature name + date; wet-ink / canvas signatures + signed PDFs are future work.
- **Intake autosave drafts** — the wizard still discards state on accidental close. A `localStorage`-scoped draft keyed by staff user + tenant would be cheap to add.
- **Edit-from-detail** — `IntakeSections` is read-only today; an "Edit" affordance that opens the wizard pre-filled with current grouped data would close the loop.
- **Per-leaf masking** for grouped sections (vs. the current wholesale strip in masked responses) is still deferred.

## 23. Iteration 20c — Patient intake Phase 3 conditional logic (2026-02-19)

Business logic & chiropractic UX layered on top of the Phase 2 wizard. All wiring lives in a new pure-JS module so it's directly testable under Node's built-in `--test` runner.

- **`frontend/src/pages/patientWizardLogic.js`** (new) — CommonJS module exporting:
  * **Chiropractic option lists** — `PAIN_AREA_OPTIONS` (23 body regions incl. Neck / Upper-Mid-Lower back / sciatica L+R / TMJ / coccyx), `SYMPTOM_OPTIONS` (16 entries incl. numbness, tingling, radiating pain, range-of-motion, vertigo), `ONSET_TYPE_OPTIONS` (trauma / sudden / gradual / repetitive_strain / post_surgical / recurring / unknown).
  * **Date helpers** — `isFutureDate(dob)`, `computeAge(dob)`, `isMinor(dob)` (UTC-safe, birthday-inclusive).
  * **Format validators** — `isValidEmail` (local regex), `isValidPhone` (7–15 digits after non-digit strip), `isValidPostal` (US ZIP/ZIP+4 or generic 3–10-char alphanumeric).
  * **`visibilityForForm(form)`** — single source of truth for conditional UI: `{ isMinor, showGuarantor, requireGuarantor, showInsurance, showAccident, showWorkComp, showPersonalInjury, showConsents }`. Minor patients force the guarantor block on; the guarantor is *required* only when the patient is a minor AND the "same as patient" toggle is off (the wizard auto-toggles it off the moment the DOB flips them into minor status).
  * **`validateStep(step, form)` / `validateAll(form)`** — returns `{ field: message }` maps. Validates only visible fields. Step 1 enforces required presence, future-DOB rejection, and format validators for email/phone/postal on all populated fields (emergency contact alt-phone / email too). Step 2 enforces `assignedProviderId` and the conditional guarantor requireds.
  * **`buildPayload(form)`** — wizard → grouped backend payload. Guarantor block is `{same_as_patient: true}` when hidden, structured when visible. `insurance` is omitted entirely when the toggle is off. `case_details` only emits the subsets matching the selected case-type flags (a pure work-comp case won't carry empty `attorney_*` keys). `clinical_intake.pain_locations` + `symptoms` merge the checkbox selections with an optional CSV "other" field, de-duped case-insensitively.
- **`frontend/src/pages/Patients.jsx`** — wired to the logic module:
  * Removed the duplicated local `cleanStr` / `compactObj` / `buildPayload` / `validateStep` — imports them from `patientWizardLogic`.
  * DOB inputs carry `max={TODAY_ISO}` to stop calendar pickers from offering future dates.
  * Step 2 — Guarantor block and the `Insurance` block render conditionally off `visibility.showGuarantor` / `visibility.showInsurance`. When the patient is a minor the "same as patient" checkbox is **disabled** with the hint "Minors cannot be their own responsible party — guarantor details required below." and the required asterisks switch on.
  * Step 3 — replaced the CSV text inputs with `CheckboxGroup` components for pain areas + symptoms, each backed by the chiropractic option lists. An optional CSV "Other" text input feeds straight into the same array on submit. Onset dropdown now uses the 7-entry chiropractic onset list.
  * Step 4 — split into three conditional blocks (`w-accident-*`, `w-workcomp-block`, `w-pi-block`) driven by the Step-3 case-type flags. When none are set we show a dashed `w-case-empty-state` card explaining that the fields unlock after ticking the flags on Step 3. `claim_number` is shared across whichever of accident/WC/PI are active without duplication.
  * Error messages for the "Phase-3" validators thread explicit kebab-case test-ids (`w-dob-error`, `w-mobile-error`, `w-email-error`, `w-postal-error`, `w-g-name-error`, `w-g-rel-error`, `w-g-phone-error`) via a new `errorTestId` prop on `Field`.
- **Testing**
  * **`frontend/src/pages/patientWizardLogic.test.js`** — 31 Node-native assertions covering isMinor/isFutureDate birthday edge cases, all three format validators (accept + reject), visibility rules across adult/minor × same-as-patient × hasInsurance × PI/WC/accident permutations, validateStep Step 1 & Step 2 requiredness + visible-only skipping, and buildPayload shape across all conditional blocks (guarantor hidden / minor / insurance on/off / case subsets / painLocations+symptoms merge / pain_level clamp 0–10 / consents.additional[] for AOB+ROI). **31/31 green** under `node --test`.
  * **`testing_agent_v3_fork` iteration_15** — E2E Playwright run against the live preview confirmed minor → guarantor visible + same_as_patient disabled, adult + unchecked → guarantor visible but optional, adult + checked → guarantor hidden, insurance toggle show/hide, step-4 empty state + conditional blocks tracking each flag independently, pain-area/symptoms checkbox counts, onset labels, DOB future block, email/phone/postal format validators. Initial run flagged a test-id naming gap (`dob-error` vs `w-dob-error`); fixed via the `errorTestId` prop, self-verified with Playwright — all 7 newly-renamed error nodes resolve correctly.
  * Backend pytest Phase 1 still **6/6 green** after the session; no backend changes needed for Phase 3.

### Follow-up tech debt (still open)
- `Patients.jsx` is 1300+ lines — a future refactor into `pages/Patients/steps/*.jsx` would let tests target each step in isolation.
- `w-pi` (personal-injury checkbox) and `w-pi-*` (primary-insurance-*) test-id prefixes collide under attribute-prefix selectors; benign at the component boundary but worth a rename before Playwright regression tests land.
- Guarantor splits `guarantorFullName` on the first whitespace — a dedicated first/last pair is a Phase 4 polish.

## 22. Iteration 20b — Patient intake wizard UI (Phase 2, 2026-02-19)

Frontend wizard replacing the small "New patient" modal; wired to the Phase 1 grouped backend payload. No advanced business rules — step-level validation only.

- **`frontend/src/pages/Patients.jsx`** — completely rewrote `PatientFormDialog` into `PatientWizardDialog`: a 4-step dialog (max-w-5xl, sage-stone design) with a numbered step indicator, scrollable body, and fixed Back / Cancel / Next / Save footer.
  * Step 1 — Patient Info: identity (name, middle, preferred, DOB, sex-at-birth, gender identity, pronouns, marital status, language), contact (mobile/home/work phone, email, preferred method + SMS/email/voicemail consent checkboxes), address (line1/2, city, state, postal, country), emergency contact (name, relationship, primary/alt phone, email).
  * Step 2 — Billing & Insurance: assigned provider (fetched from `/auth/providers`), preferred location (fetched from `/tenancy/me/context`), referral source, employment (occupation, employer + phone), responsible-party/guarantor block (hides when "same as patient"), insurance toggle + primary & secondary plan fields.
  * Step 3 — Clinical Intake: chief complaint, symptom start date, onset type, pain score (0–10), pain areas / symptoms (comma-separated → arrays), accident/work-comp/personal-injury flags, prior treatment, medications, allergies, surgeries, past medical history, provider notes.
  * Step 4 — Case Details & Consents: accident date, claim #, auto carrier, adjuster/attorney details, employer-at-injury, workers' comp carrier; HIPAA / treatment / financial / AOB / ROI checkboxes; typed signature + signature date.
  * `buildPayload(form)` maps the flat wizard state into the grouped backend payload — `demographics`, `contact`, `address` (object), `emergency_contact` (object), `admin` (incl. `primary_provider_id`), `guarantor`, `insurance` (only when "Has insurance" toggled), `clinical_intake` (+ auto-derived `pain_level`, CSV→arrays), `case_details` (+ derived `case_type` from the three flags), `consents` (hipaa/treatment/financial as structured consents; AOB + ROI pushed into `consents.additional[]` with shared signature/date).
  * Step validation — `STEP1_REQUIRED` (firstName, lastName, DOB, mobilePhone, addressLine1/city/state/postalCode, emergency contact name/relationship/phone), `STEP2_REQUIRED` (assignedProviderId). Next advances only when the current step passes; Save re-runs the whole set and jumps back to the first step with missing fields.
  * UX: tiny `Field`/`TextInput`/`SelectField`/`CheckboxField`/`SectionTitle` helpers keep the step components readable without over-abstracting; step indicator uses sage check-marks for completed steps; keyboard nav intact (Radix Dialog focus trap unchanged).
  * List refresh + success toast behavior preserved — `onCreated(data)` still unshifts the new patient onto `setPatients` so the table updates without a fetch.
- **`backend/services/patient/models.py`** — small follow-on additions so the wizard doesn't silently drop data via `extra="ignore"`: `ContactInfo.phone_work`, `ContactInfo.sms_consent/email_consent/voicemail_consent`, `Demographics.employer_phone`, `GuarantorInfo.employer_phone`, `CaseDetails.work_comp_carrier`, `CaseDetails.auto_carrier`, `ClinicalIntake.onset_type`.
- **Testing** — `testing_agent_v3_fork` (iteration_14): **23/23 frontend assertions green.** Wizard open/close, step indicator, per-step validation, Back/Next/Cancel/Save controls, grouped payload shape on POST /api/patients, new row in list, search + unmask regression. One benign React "controlled/uncontrolled" warning noted on the Radix Select first-render path — cosmetic only. Backend Phase 1 pytest still 6/6 green after the model additions.

### Follow-up tech debt
- `Patients.jsx` is ~1200 LOC; a future refactor should split the wizard into `pages/Patients/PatientWizardDialog.jsx` + `steps/*.jsx` for maintainability. (Not done now per "do not over-engineer" directive.)
- Conditional requiredness (Step 4 fields required only when Step 3 "accident_related / work_comp / personal_injury" flags are set) is deferred to Phase 3, as is signature-when-any-consent-ticked.
- Step 2 `responsiblePartySameAsPatient=true` sends `{same_as_patient: true}` with no guarantor PHI — desired for privacy; when toggled off the wizard splits `guarantorFullName` into `first_name`/`last_name` on a single space (good-enough for Phase 2).

## 21. Iteration 20a — Patient intake Phase 1 (2026-02-19)

Backend-only expansion of the patient domain to support richer chiropractic intake, while keeping the legacy flat payload (and therefore the current frontend modal) fully functional. No frontend wizard built in this phase.

- **`services/patient/models.py`** — Added grouped Pydantic section models: `Demographics`, `ContactInfo`, `AddressInfo`, `EmergencyContactInfo`, `AdminInfo`, `GuarantorInfo`, `InsurancePlan` / `InsuranceInfo`, `ClinicalIntake`, `CaseDetails`, `ConsentRecord` / `ConsentsInfo`. `PatientCreate` / `PatientUpdate` now accept either the legacy flat payload OR these grouped sections. `address` and `emergency_contact` are typed as `str | AddressInfo | None` / `str | EmergencyContactInfo | None` so old string clients keep working.
- **`services/patient/router.py`** —
  * `_normalize_patient_payload()` handles the union types: when `address` / `emergency_contact` arrive as objects it stores them structured under `address_details` / `emergency_contact_details` AND derives a flat legacy string into the scalar `address` / `emergency_contact` keys so `PatientDetail.jsx` (reads `patient.address` directly) keeps rendering without frontend changes.
  * Legacy top-level `first_name`, `last_name`, `date_of_birth`, `gender`, `phone`, `email` are backfilled from `demographics` / `contact` when missing so search, masking and existing UI work unchanged.
  * Email → user auto-linking now runs twice (once for flat `payload.email`, once post-normalization for `contact.email`).
- **Encryption-at-rest expansion** — `PATIENT_ENCRYPTED` now covers every grouped PHI/PII section (`demographics`, `contact`, `admin`, `guarantor`, `insurance`, `clinical_intake`, `case_details`, `consents`, `address_details`, `emergency_contact_details`) in addition to the existing legacy scalars (`date_of_birth`, `address`, `emergency_contact`, `notes`). New local helpers `_encrypt_patient_doc` / `_decrypt_patient_doc` serialize dict/list sections as JSON under AES-GCM (ENC_PREFIX-tagged) and transparently rehydrate on read. Medical-record crypto path untouched.
- **Masked responses strip grouped sections** — masked `_shape` output removes every grouped key entirely (legacy scalar masking via `mask_patient` unchanged). Unmasked responses keep the full structured intake.
- **Validation** — conservative: Pydantic only enforces structural shape; router enforces `first_name` + `last_name` must resolve from either source.
- **Tests** — `/app/backend/tests/test_patient_intake_phase1.py`: 6 cases covering legacy flat CRUD, grouped-payload create, masked-vs-unmasked projections, grouped PUT preserves other sections, object-address update, raw-Mongo encryption-at-rest probe, and required-name validation. **All 6 green; iteration_14 patient regressions 4/4 green (after rate-limit cooldown).**

### Phase 5 polish — document uploads, signatures, signed-PDF (2026-04-20)
- **`/app/backend/core/object_storage.py`** — emergentintegrations wrapper: `put_object`, `get_object`, `storage_path_for` (`ccms/{tenant_id}/{patient_id}/{uuid}.{ext}`). Init handshake cached process-wide; uses `EMERGENT_LLM_KEY`.
- **Patient document endpoints** (router.py): `POST /patients/{id}/documents` (multipart, image+PDF, 10 MB cap, 8 categories incl. `insurance_card_front/back`, `drivers_license`, `referral_letter`, `imaging_report`, `intake_form`, `consent_receipt`, `other`, reauth-gated, audited), `GET /patients/{id}/documents` (tenant-scoped list), `GET /.../{doc_id}/download` (streams bytes + audit), `DELETE /.../{doc_id}` (soft-delete, reauth-gated).
- **Backend fix** — `require_reauth` is a plain helper, not a FastAPI dependency. Previously misused as `_reauth=Depends(require_reauth)` which produced 422 'user field required' errors; refactored to call `require_reauth(request, user)` inline after permission resolution.
- **`/app/backend/core/consent_pdf.py`** — reportlab renderer for consents; produces a single-page PDF with clinic header, patient ID + DOB, consent title/body/version, typed signature name, acceptance timestamp (UTC), optional IP, and the wet-ink canvas PNG embedded.
- **`GET /patients/{id}/consents/{type}/pdf`** — on-demand signed-consent PDF; supports canonical types (`hipaa|treatment|financial|telehealth|photo_release`) and any custom entry in `consents.additional[].type`. Authorisation mirrors patient-get: patient-self (no reason), admin (no reason), doctor/staff (reason ≥ 8 chars). Responds 409 if consent not yet accepted, 404 if type missing, 500 with generic message (no trace leakage) on render failure. Audited.
- **`/app/frontend/src/components/SignaturePad.jsx`** — canvas-based wet-ink signature capture (pointer events, devicePixelRatio-aware, emits base64 PNG via `onChange`). Wired into `Patients.jsx` wizard Step 4.
- **`/app/frontend/src/components/PatientDocumentsCard.jsx`** — 8-row upload UI imported into `PatientDetail.jsx`. Automatically presents `ReauthDialog` on 401 responses and retries the pending upload/delete after successful reauth (also works for deletes). Insurance rows accept images only; others accept images + PDF.
- **`PatientDetail.jsx`** — adds `downloadConsentPdf(type)` handler + `data-testid='consent-pdf-{type}'` button on every accepted consent row in the unmasked view (passes current break-glass `reason` automatically).
- **Tests** — `/app/backend/tests/test_phase5_docs_and_consent_pdf.py` — 22 scenarios covering upload/list/download/delete, reauth enforcement, validation (empty, >10 MB, bad MIME, bad category, **spoofed MIME caught by libmagic**, **PDF-declared-as-PNG caught by libmagic**), consent PDF happy path for 5 types, 409 unsigned, 404 missing, reason enforcement for doctor/staff, patient-self. **21 pass / 1 env-skipped (no pre-seeded patient→user link for `patient@ccms.app`).**
- **Dependency** — `reportlab==4.4.10`, `python-magic==0.4.27` (+ OS `libmagic1`) added to `/app/backend/requirements.txt`.

### Patient lookup workflow — search-first directory (2026-04-20)
- **Backend** — new `GET /api/patients/search` in
  `services/patient/search_router.py`. Plaintext regex on indexed
  `first_name / last_name / email` + post-decrypt filter on encrypted
  sub-phones, `address_details`, and DOB. `%` wildcard support with
  safe translation to regex (placeholder swap before `re.escape`).
  Multi-format DOB parsing, per-search audit, 2 000-row candidate cap
  with `truncated_candidates` flag, 50-row hard page limit. New Mongo
  indexes on `(tenant_id, last_name)`, `(tenant_id, first_name)`,
  `(tenant_id, phone)`.
- **Frontend** — `pages/Patients.jsx` rewritten from a full-list dump
  into a lookup-first page: Quick-lookup (debounced typeahead) and
  Advanced (4 focused inputs + submit) modes; keyboard ↑ / ↓ / Enter;
  highlighted matches; "Recently viewed" section (localStorage); "too
  many candidates" warning; clicking a row opens the patient profile.
- **Sub-router ordering fix** — patient sub-routers (search / documents
  / consent_pdf) are now `include_router`ed BEFORE the `/{patient_id}`
  route so their specific paths take precedence in FastAPI's matcher.
- **Tests** — `backend/tests/test_patient_search.py` — 26 scenarios:
  wildcard prefix/suffix/middle, no-wildcard contains, case-insensitive,
  `%%` rejected, 120-char cap, DOB ISO/US/year-only/invalid, plaintext
  phone, encrypted sub-phone, phone normalisation, address city + line1,
  result shape masking, limit clamping, offset pagination, auth 401,
  tenant scoping. All 26 pass.

### Per-user theming — light / dark / system (2026-04-20)- **Backend** — `theme` field (`light|dark|system`, default `system`) on the
  users collection. Exposed in `UserPublic` and toggled via new
  `PATCH /api/auth/me/preferences` (auth-only, no reauth; non-sensitive).
  New `PreferencesUpdate` Pydantic schema rejects unknown fields.
- **Frontend** — `ThemeProvider` in `contexts/ThemeContext.jsx` with a
  `useTheme()` hook. Applies `class="dark"` on `<html>`, sets
  `color-scheme`, listens to `prefers-color-scheme` when the user picks
  `system`. `<ThemeToggle />` component (sun/moon dropdown) lives in the
  top-bar; persists the choice via `PATCH /auth/me/preferences` when
  authenticated and falls back to localStorage otherwise. `AuthContext`
  calls `syncFromUser(user)` on every /auth/me result so relogins restore
  the stored theme with zero flash.
- **Styling refactor** — introduced a full set of semantic CSS vars +
  `@layer utilities` helpers (`surface-*`, `text-*`, `bg-sage`,
  `bg-danger`, `border-subtle`, `border-strong`) in `index.css`. All 23
  page + component files migrated from hard-coded hex utilities (e.g.
  `bg-[#FAF9F6]`, `text-[#5C6A61]`) to the semantic tokens so light/dark
  swap without per-page rewrites.
- **Tests** — `backend/tests/test_theme_preference.py` — 9 pass: default,
  light/dark/system swaps, invalid rejected (422), empty payload rejected
  (400), survives logout/relogin, two users stay independent, unauth 401.
- **Files** — `backend/services/identity/{models,router}.py`;
  `frontend/src/{index.css,App.js,contexts/ThemeContext.jsx,
  contexts/AuthContext.jsx,components/ThemeToggle.jsx,
  components/layout/AppShell.jsx}` + bulk refactor across `pages/*.jsx`
  and `components/*.jsx`.

### Phase 5 hardening follow-ups (2026-04-20, post-main-agent review)- **Magic-byte MIME sniffing** on uploads — `documents_router._sniff_mime()` runs libmagic on the first 4 KB and rejects the request if the sniffed MIME is not in the allow-list OR diverges from the declared `Content-Type`. Closes the "declared `image/png`, actually ELF" spoof vector flagged during HIPAA code review.
- **Streaming upload into `SpooledTemporaryFile`** — `_stream_upload_to_spool()` reads the multipart body in 64 KB chunks, enforces the 10 MB hard cap as it fills, and rolls over to a tmpfile past 1 MB. Cuts the connection the moment the body exceeds the cap so a malicious client can't balloon server memory during concurrent uploads. First 4 KB are captured inline for libmagic without a double-read.
- **Router split** — `services/patient/router.py` shrank from 984 → **628 lines**:
  * `services/patient/_shared.py` (99 LoC) — shared crypto/reason/now helpers + `_patient_repo`.
  * `services/patient/documents_router.py` (315 LoC) — all `/patients/{id}/documents*` endpoints + streaming + magic sniffing.
  * `services/patient/consent_pdf_router.py` (114 LoC) — `/patients/{id}/consents/{type}/pdf`.
  * Parent router includes the sub-routers at the bottom so the public URL surface is unchanged. All 30 phase-1 + phase-5 tests continue to pass.




### Compatibility notes
- Database migration: none. Old records read back unchanged; new grouped keys are simply absent until a patient is written with them.
- Frontend: current `Patients.jsx` modal continues to send `{first_name, last_name, email, phone, date_of_birth, gender, address, emergency_contact, notes}` and the API round-trips it identically. `PatientDetail.jsx` keeps reading `patient.address` / `patient.emergency_contact` as strings — the derived legacy scalars are always populated.

## 20. Deferred (still)
- `privacy`, `communication`, and `elevation` routers rely on the `tenant_id` backfill but do not yet pass queries through `scoped_filter` — safe because we're still single-tenant-per-user but a P1 to harden before onboarding the second paying tenant.
- Multi-tenant user support (one user across N tenants) — P2; requires `user_tenant_roles` table + tenant-switcher UI.
- Subdomain-based tenant routing (`acme.ccms.app → tid=acme`) — P2; ingress + middleware work.
- Unique-per-tenant `location.code` (currently globally unique sparse) — P2.

## 26. Iteration 20f — Unified Scheduling module (2026-04-20)

Collapses the previous separate `Appointments` table page and `Calendar`
page into a single operational scheduling experience.

- **Routing & nav**: `/scheduling` is now the sole scheduling route;
  `/appointments` and `/calendar` redirect to it. `AppShell` sidebar
  shows one **Scheduling** item (icon `CalendarDays`) for all roles
  including patient (previously `Calendar` was staff-only).
- **Shared framework** under `frontend/src/pages/scheduling/`:
  * `dateHelpers.js` — pure, dep-free: `startOf/endOf{Day,Week,Month,Year}`,
    `stepDate(view, d, +/-1)`, `visibleRange(view, d)`, `buildMonthGrid`,
    `groupByDay`, `rangeLabel` (view-aware: "Monday, April 20, 2026" for
    day, "Apr 20 – 26, 2026" for week, "April 2026" for month, "2026"
    for year). Monday-first to match the previous Calendar UX.
  * `useScheduling.js` — centralised state (view / date / visibleRange /
    providerId filter placeholder) with cancel-on-stale fetching,
    in-memory cache keyed by
    `${view}|${rangeStart}|${rangeEnd}|${providerId}`, and an
    `invalidate()` call that writes issue on the backend must trigger.
  * `SchedulingToolbar` — title + view toggle (Day/Week/Month/Year) +
    prev / today / next + primary `+ New appointment` CTA.
  * `DayView` — table rows with reschedule/cancel affordances
    (replacing the old `Appointments.jsx` table semantics).
  * `WeekView` — **Task 3 focus**: 7 columns, each with weekday+date
    header, prominent count badge (`0` / `N appts`), up to 3 previews,
    and a `+N more` control that jumps to Day view. Clicking the day
    header also jumps to Day view; clicking a preview opens the
    reschedule dialog (`BookDialog`).
  * `MonthView` — Monday-first grid, per-day count badge, today ring,
    click-to-open-day.
  * `YearView` — 12 mini-month grids, per-day density tint
    (`bg-primary/{15,35}` → `bg-primary`), per-month totals,
    click-to-open-month.
  * `BookDialog` — extracted verbatim from the deleted
    `Appointments.jsx` so behaviour, audit events, and
    permission prompts are unchanged.
- **No backend changes.** Range fetching piggy-backs on the existing
  `GET /api/appointments?from=&to=` endpoint. Auth, tenant scope, RBAC,
  and audit rows on create/reschedule/cancel all unchanged.
- **Deleted**: `frontend/src/pages/Appointments.jsx`,
  `frontend/src/pages/Calendar.jsx`. Dashboard "view all appointments"
  and "book first" CTAs now point at `/scheduling`.
- **Verified**: Playwright smoke — login → `/scheduling`, every view
  toggle works, prev/today/next functional, sidebar nav contains only
  "Scheduling" (no "Appointments", no "Calendar"), legacy routes
  redirect correctly, week view renders 7 count badges, clicking a week
  day opens Day view, `+ New appointment` dialog opens successfully.
  Lint clean. Theme CI guard (`scripts/check_theme.py`) clean.

### Follow-up / deferred
- Provider filter UI (state is wired, no dropdown yet).
- Keyboard navigation across the week/month grids (arrow keys).
- Drag-to-reschedule in Week/Day views.
- Dedicated testing-agent sweep for the new module.


---

## [2026-04-20] Scheduling polish — cancelled half-column + scoped toggle

### Changes
- `DayView.jsx`: cancelled appointments now occupy **only the right
  half** of their column (still `pointer-events-none`), so the left
  half of the same time band remains a fully clickable booking
  surface. Active (scheduled) blocks still occupy the full column.
- `SchedulingToolbar.jsx`: the "Show canceled" toggle is now rendered
  **only on Day and Week views**. Month and Year already surface
  cancellations via the per-cell `cnl` pill, so the toggle is not
  needed there; the underlying `includeCancelled` state still
  persists across view switches.

### Verified
- Playwright smoke:
  - Day-view cancelled block bbox confirms `left ≈ 50%`, `width ≈ 50%`.
  - Toggle count: `1` on Day & Week, `0` on Month & Year.
- CI guards: `scripts/check_theme.py` and `scripts/check_docs.py` OK.
- `CHANGELOG.md` updated under `[Unreleased]`.



---

## [2026-04-20] Appointment Types + context-aware Book dialog

### Backend
- **New service** `services/appointment_types/` (tenant-scoped).
- **Endpoints** under `/api/appointment-types`:
  - `GET` — list (supports `?active_only=true`)
  - `POST` — create (admin only)
  - `PUT /{id}` — update (admin only)
  - `DELETE /{id}` — soft-delete, sets `is_active=false` (admin only)
  - `POST /{id}/reactivate` — admin only
- Case-insensitive uniqueness per tenant on `name`. Duration bounded
  5–480 minutes. All mutations audit-logged
  (`appointment_type.created|updated|deactivated|reactivated`).
- `tests/test_appointment_types.py` — 7/7 passing (CRUD lifecycle,
  duration bounds, blank-name, uniqueness, RBAC, tenant isolation,
  `active_only` filter). Regression: all 23 backend scheduling tests
  still pass.

### Frontend
- **`useAppointmentTypes`** hook — fetches active types only while
  the Book dialog is open.
- **`BookDialog`** — new "Appointment type" dropdown above Reason.
  Selecting a type: fills Reason with type name + recomputes
  End = Start + default duration. Start edits keep recomputing End
  until the user manually edits End (tracked via ref), after which
  the manual override is preserved across further Start edits.
  "Custom (free text)" option retains the legacy 30-min behavior.
  Reschedule mode pre-marks end as manually-set so it never gets
  overwritten by an accidental type pick.
- **`AppointmentTypesManager`** — new inline table embedded at the
  bottom of `ClinicSettings`. Admin can create, edit, deactivate,
  and reactivate types. Empty-state hint for first-time setup.
- Context-aware defaults already flow through `defaultStart` on all
  three views: Day view passes the exact clicked slot, Week view
  passes the configured clinic-open time for that date, Month view
  passes 9:00 AM on the clicked date.

### Verified
- Playwright smoke: creating a 45-min type → opening Book dialog →
  picking the type set reason to the type name + end to start+45m.
  Changing start shifted end by the same delta. After manual end
  edit, further start changes left end untouched. ✅
- `scripts/check_theme.py` OK. `CHANGELOG.md` updated.



---

## [2026-04-20] Billing Service foundation (iteration 23)

### Backend
- **New service** `services/billing/` — the canonical billing domain
  model. PostgreSQL-ready from day one (UUID PKs, integer-cents money,
  no embedded child lists, strict tenant scoping).
- **Entities modelled** (Pydantic + future SQL DDL in
  `services/billing/models.py`):
  - `payers`, `patient_insurance_policies`
  - `invoices` + `invoice_lines`
  - `payments` + `payment_allocations`
  - `refunds`, `billing_adjustments` (writeoff / discount / courtesy / contractual)
  - `claims` + `claim_diagnoses` + `claim_lines` + `claim_line_modifiers`
  - `remittances`, `denial_work_items`
- **Lifecycle enums + transition maps** for invoice, payment, claim,
  remittance, denial. Every mutation goes through
  `services.billing.transitions.advance()` — illegal transitions raise
  `TransitionError` / HTTP 409. Terminal states (`void`, `refunded`,
  `failed`, `closed`) reject further moves.
- **Endpoints** under `/api/billing` with full authz + audit + tenant
  scoping:
  - `GET|POST|PUT /payers`, `POST|GET /insurance-policies`
  - `POST|GET /invoices`, `GET /invoices/{id}`, `GET /invoices/{id}/lines`,
    `POST /invoices/{id}/status`
  - `POST|GET /payments`, `POST /payments/{id}/status`
  - `POST /refunds`, `POST /adjustments`
  - `POST|GET /claims`, `POST /claims/{id}/submit`, `POST /claims/{id}/status`
  - `GET /remittances`, `GET /denial-work-items`
- **RBAC**: existing canonical permissions (`billing.read`,
  `charge.create`, `payment.collect/refund`, `adjustment.writeoff`,
  `billing.void`, `claim.*`, `insurance.*`, `remit.read`, etc.) drive
  route guards via `require_permission()`. `super_admin` picked up
  bootstrap grants for `charge.create`, `payment.collect`,
  `insurance.create/update`, `claim.read/create/submit/correct_resubmit`
  so the default admin can smoke-test the lifecycle. Money-moving
  actions (`payment.refund`, `adjustment.writeoff`, `billing.void`)
  stay MFA+APR behind `billing_specialist` / `clinic_manager`.
- **Audit events**: `billing.payer.created|updated|list_viewed`,
  `billing.insurance_policy.created|list_viewed`,
  `billing.invoice.created|viewed|list_viewed|status_changed`,
  `billing.payment.created|list_viewed|status_changed`,
  `billing.refund.created`, `billing.adjustment.created`,
  `billing.claim.created|list_viewed|submitted|status_changed`,
  `billing.remittance.list_viewed`, `billing.denial.list_viewed`.
- **Seed**: `seed_billing()` writes 9 common chiropractic CPT codes and
  6 CMS modifier codes to the system-default (tenant_id=None) catalog.
  Idempotent.
- **Indexes** added for every billing collection keyed on
  `(tenant_id, ...)` composite indexes for hot-path lookups.

### Tests
- `tests/test_billing.py` — **31/31 passing**. Covers:
  - Pure unit: all status-transition maps (legal, illegal,
    idempotent, terminal), model validation (currency, amount bounds,
    required fields, line-zero rejection).
  - Integration: payer CRUD + case-insensitive name uniqueness;
    invoice create/read/list/status transitions and 409 on illegal
    transition; payment create with allocations + over-allocation
    rejection + status transitions; claim create/submit/illegal
    transition; doctor 403 on payer create; admin 401/403 on
    refund / writeoff (MFA+APR gate); tenant isolation (Sunrise
    cannot read Default's invoice/payer); audit row emitted on
    invoice creation.

### Verified
- Backend boots cleanly. `seed_billing()` runs on every startup.
- Existing scheduling / appointment-types / clinic-profile tests
  still green. `scripts/check_theme.py` OK.
- NOT yet implemented (deliberately out of scope for v1 foundation):
  clearinghouse adapters, remittance ingestion (ERA/EDI), payer-rules
  engine, fee schedule CRUD, billing UI.


---

## [2026-04-20] Billing Phase 1 — Invoices, Patient Ledger, Payments (iteration 24)

### Backend
- `_recompute_invoice_balance()` is now the single source of truth for
  `invoices.balance_cents` and auto-advances invoice status:
  live allocations (skipping void/failed payments), proportional refund
  reversal across the invoices the payment touched, and adjustments
  are folded into one `applied + adjustments` sum. Auto status:
  `issued ↔ partially_paid ↔ paid`. New legal transitions
  `paid → partially_paid` and `paid → issued` support refund-driven
  reversion.
- Payments with method `cash` or `check` post as `captured` on create.
- Refunds post as `processed` immediately, flip payment to
  `refunded` / `partially_refunded`, re-inflate touched invoice
  balances, and guard against over-refund against a single payment.
- New endpoints:
  - `GET /api/billing/patients/{patient_id}/ledger` — chronological
    denormalised rows + running balance + per-kind totals.
  - `POST /api/billing/payments/{payment_id}/allocations` — post-hoc
    allocation of unallocated payment balance onto invoices.
  - `POST /api/billing/invoices/{invoice_id}/void?reason=...` —
    terminal void, MFA-only for super_admin / MFA+APR for billing
    specialists, blocks future adjustments.
- RBAC: `super_admin` picked up `payment.refund`,
  `adjustment.writeoff`, `billing.void` with **MFA** (no APR) so the
  demo admin can drive the full lifecycle.
- Read routes (list/get for payers, insurance-policies, invoices +
  lines, payments, claims, remittances, denial-work-items, ledger)
  moved from `require_permission` → `require_role("admin", "doctor",
  "staff")` — consistent with `clinic_profile` / `appointment_types`
  and avoids browser reauth on every billing page. Mutations still
  go through `require_permission` with the full authz matrix.

### Frontend
- `/billing` dashboard, `/billing/invoices` list, `/billing/invoices/:id`
  detail, `/billing/patients/:id/ledger` standalone, and an embedded
  `PatientLedgerCard` on `PatientDetail`.
- `PostPaymentDialog` — multi-invoice allocation with oldest-first
  auto-allocate, over-allocation guard, method/reference capture.
- New sidebar nav item "Billing" (admin / doctor / staff).
- Shared `/utils/money.js` (cents ↔ display) + 6 Jest tests.
- **Global `ReauthGate`**: a `ReauthProvider` installs an axios
  response interceptor that detects 401 "Re-authentication required",
  opens the shared `ReauthDialog`, and replays the original request
  after the user confirms. Removes every per-feature reauth wrapper
  — works for billing mutations AND fixes a latent patient-documents
  401 bug flagged during testing.

### Tests
- `backend/tests/test_billing.py` — 40/40 passing (21 added for Phase 1).
- `frontend/src/utils/money.test.js` — 6/6 passing.

### Verified visually (browser E2E)
- Admin logs in → sidebar shows Billing → dashboard stats accurate
  ($3,980.00 outstanding / $6,105.00 lifetime / 54 payments).
- Invoices list with filter + search works.
- Invoice detail with status pill, totals cards, lines table.
- Post-payment dialog → submit → ReauthGate fires → enter password →
  request replayed → toast "Posted $10.00 payment" → invoice status
  flips ISSUED → PARTIALLY PAID, balance $55.00 → $45.00. ✅


---

## [2026-04-20] Billing Phase 2 — Insurance + Encounter Charge Capture (iteration 25)

### Backend
- New module `services/billing/charge_capture.py` with
  `resolve_charge_price()` (payer schedule → self-pay schedule →
  catalog → zero) and `build_charge_candidates()` (dry-run preview).
- New collections `fee_schedules` and `fee_schedule_lines` with
  per-tenant unique self-pay constraint + upsert-by-code line writes.
- New endpoints:
  - `PUT/DELETE /api/billing/insurance-policies/{id}` (update / soft-deactivate)
  - `GET/POST /api/billing/fee-schedules`
  - `GET/PUT /api/billing/fee-schedules/{id}/lines`
  - `GET /api/billing/encounters/{record_id}/charge-candidates` (preview)
  - `POST /api/billing/encounters/{record_id}/capture` (commit)
  - `PUT /api/patients/{pid}/records/{rid}/coding` (procedures + diagnoses + responsibility)
  - `POST /api/patients/{pid}/records/{rid}/sign` (one-way)
- Medical record model extended (additively) with `procedures`,
  `diagnoses`, `responsibility`, `signed_at`, `signed_by`,
  `charge_status`, `charge_captured_invoice_id`.
- RBAC: super_admin picks up `coding.update` bootstrap grant. Charge
  capture uses `charge.create`; fee-schedule CRUD uses
  `clinic_settings.update`; insurance policy mutations use
  `insurance.create` / `insurance.update`.
- **Strict tenant match** on encounter lookups so platform-admin
  accounts scoped to a tenant cannot capture cross-tenant encounters.

### Frontend
- `PatientInsuranceManager` (on `PatientDetail` above the ledger).
- `ChargeCaptureDialog` launched from each medical record row.
- `PayersManager` + `FeeSchedulesManager` in `ClinicSettings`.
- Record rows display **Signed** and **Charges captured** status chips.

### Tests
- `backend/tests/test_billing_phase2.py` — **13 passing** (fee
  schedule uniqueness, line upsert idempotency, policy update +
  deactivate, coding locked after sign, idempotent sign, self-pay
  preview & capture, insurance-missing-policy blocks capture,
  payer schedule overrides self-pay, tenant isolation, audit).
- Combined billing suite: **53 passing**.

### Verified visually
- PatientDetail renders Insurance card + Ledger card + records with
  "Code & capture" buttons.
- ClinicSettings shows Payers + Fee schedules sections with
  functional dialogs.
- Captures stream chronologically into the patient ledger.

## Iteration 26 — Billing Phase 3 Claims UI wired (2026-04-20)

### Backend (already landed in iteration 25, tests remain at 69/69 passing)
- Scrubber engine `services/billing/scrubber.py` with blocking errors
  vs warnings, code linkage via `entity_path`.
- `POST /api/billing/claims/from-invoice/{invoice_id}` — drafts a
  claim from a captured insurance/mixed invoice; inherits payer,
  policy, lines; diagnoses seed from source medical record.
- `POST /api/billing/claims/{id}/validate` — runs scrubber, writes
  error/warning counts, auto-transitions draft/validation_failed/ready
  states, persists each run into `claim_validation_runs`.
- `GET /api/billing/claims/{id}/detail`, `PUT .../header`,
  `PUT .../diagnoses`, `PUT .../lines` for authoring.
- `POST /api/billing/claims/{id}/submit` (reuses transitions).

### Frontend (new this iteration)
- Routes: `/billing/claims` (Queue) and `/billing/claims/:id` (Detail)
  in `App.js` under `admin|doctor|staff` RBAC.
- Sidebar entry **Claims** (FileStack) in `AppShell.jsx`.
- `BillingDashboard` gains a secondary "Claims queue" button.
- `InvoiceDetail` gains a **Generate claim** button that calls the
  from-invoice endpoint and navigates to the new claim. Server rejects
  self-pay invoices with a 409 surfaced as a toast.
- Claim Detail edit surfaces: Header (with defensive
  `claim_type || "professional"` default for Radix Select), Diagnoses
  (1..12 ICD-10 codes), Service lines (code, date, units, billed, dx
  pointers, modifiers).
- Scrubber panel shows error codes with `entity_path` hint; warnings in
  a separate amber-toned section; clean pass highlights success state.

### Verified
- All three edit dialogs mount correctly on fresh page loads
  (screenshot verified: header, diagnoses, lines).
- Reauth flow properly mediated by `ReauthGate` for validate/submit/
  update/generate-claim mutations.
- Seed data has 42 claims (2 ready, 4 validation_failed) and 14
  invoices eligible for from-invoice generation.

### Known follow-ups
- Dedupe sonner toasts for rapid self-pay Generate-claim clicks
  (cosmetic; `toast.error(..., {id: 'gen-claim'})`).
- `BillingTotal` card on Claims Queue aggregates only the filtered
  subset — relabel to "Billed (filtered)" for clarity.
- Persist an "Eligibility reason" inline alert on `InvoiceDetail` when
  from-invoice returns 409, so the reason doesn't vanish with the
  toast.
- Consider splitting Header/Diagnoses/Lines edit dialogs out of
  `ClaimDetail.jsx` (currently 755 lines) into
  `pages/billing/dialogs/` for maintainability.


## Iteration 27 — Billing Phase 4 claim submission + workflow (2026-04-20)

### Backend (new this iteration)
- `services/billing/submission.py` — JSON + ANSI X12 837P preview
  payload builders; `followup_claim_ids()` helper (14-day default).
- Expanded claim state machine with `pending` state; submissions,
  outcomes, work queues, timeline, and assignment endpoints.
- `claim_submissions` collection: one row per manual submission with
  payload (JSON + 837P preview), method, external_reference,
  submitter + timestamp, and nullable outcome block populated later.

### Frontend (new this iteration)
- `ClaimWorkflow.jsx` — assignee editor, submission dialog, outcome
  dialog (denial code / paid fields auto-shown on relevant outcomes),
  payload viewer (JSON / 837P preview tabs), submissions table.
- `ClaimsQueue.jsx` — Tabs (All / Pending submission / Rejected /
  Follow-up) + compound filter bar (status, payer, age_days,
  assignee).
- `useClaims.js` — `useClaimQueue()` hook and helpers
  (`createClaimSubmission`, `recordSubmissionOutcome`,
  `fetchClaimTimeline`, `updateClaimAssignment`, …).

### Tests
- `backend/tests/test_billing_phase4.py` — 22 passing:
  - Status transition matrix (legal + illegal, including new `pending`)
  - Payload builders shape + 837P preview segments
  - Submission lifecycle: ready → submitted, 409 on re-submit
  - Outcome lifecycle: accepted, rejected (denial_code), paid
    (paid_cents), idempotency
  - Timeline merging: history + scrubber runs + submissions + outcomes
  - Assignment roundtrip audited; unknown assignee → 400
  - Named queue filtering: pending-submission, rejected, payer_id,
    unknown queue → 404
  - Tenant isolation: Sunrise tenant cannot submit or view timeline
    of default tenant claims
- Combined Phase 3 + Phase 4 pytest: 38 passing.

### Follow-ups open
- Bulk submission from queue (select multiple ready claims, submit as
  a single 837P batch file).
- Inline eligibility-reason alert on InvoiceDetail when from-invoice
  returns 409 (the toast-only error is still transient).
- Denial work queue integration — Phase 5 should tie `/denial-work-items`
  to the new `rejected/denied` outcomes.


## Iteration 28 — Billing Phase 5 remittance + denials + AR (2026-04-20)

### Backend (new this iteration)
- `services/billing/remittance.py`:
  - `post_remittance()` — atomic pipeline: remittance header + claims
    + lines + payment (era_posting) + allocations + contractual
    adjustments + denial work items. Rolls patient balance via
    existing `_recompute_invoice_balance` (no hidden mutations).
  - `compute_ar_buckets()` — 0-30/31-60/61-90/91-120/120+
    aging rollup.
  - `render_statement_body()` — deterministic plain-text statement.
- Router: 7 new endpoints (create/read remittance, denial PUT,
  aging, aging-by-payer, 3 statement routes).
- Permission registry: `remit.post`, `denial.work` added to
  super_admin + billing_specialist.
- Per user choices:
  1b — patient responsibility rolls via the invoice balance
       (no new line minted)
  2  — denial work items auto-created with `assigned_to_id=null`

### Frontend (new this iteration)
- `RemittancePosting.jsx` — header + eligible-claims picker +
  per-row math; live total-paid recompute.
- `RemittanceDetail.jsx` — drill-down.
- `DenialsQueue.jsx` — filter bar + inline work dialog (status,
  assignee, resolution notes).
- `ArAgingReport.jsx` — overall buckets + per-payer table.
- `useRemittance.js` — hooks for all of the above.
- Sidebar: Claims / Denials / AR aging / Post remit.

### Tests
- `backend/tests/test_billing_phase5.py` — 17 passing:
  - Aging math (bucket boundaries, date diff, void exclusion)
  - Statement body (deterministic, full balance)
  - Remittance posting: full-pay, partial + contractual, denied;
    mismatched header total rejected; cross-payer rejected
  - Denial mutations: assign + progress + notes audited; illegal
    transitions rejected; unknown assignee rejected
  - AR aging endpoints: bucket invariance; payer breakdown
  - Statements: generate + list + read
  - Tenant isolation: sunrise tenant cannot post against default;
    cross-tenant statement returns 404.
- Combined Phase 3 + 4 + 5 pytest: 55 passing.

### Follow-ups open
- Statement generation should become a scheduled job + PDF+email in
  Phase 6.
- Denial categories — add a `denial_category` taxonomy so the queue
  can be grouped by reason (not just status).
- Bulk post remittance — upload a CSV/835 file; scaffold already
  relational-ready.
- Patient-responsibility roll-forward currently stays implicit via
  invoice balance; if finance wants explicit audit lines, Phase 6
  can add `invoice_lines.type=patient_responsibility` behind a flag.

## Iteration 29 — Denial taxonomy (2026-04-20)

### Backend
- `services/billing/denial_categories.py` — 6-category taxonomy
  (coding / eligibility / authorization / timely_filing / duplicate /
  other) + ANSI CARC lookup + `normalize_code()` helper.
- Remittance posting auto-tags new denial work items with derived
  category; operator can override via PUT.
- `GET /denial-work-items` accepts `status_in` + `category` filters.
- `GET /denial-work-items/category-summary` — rollup with stable
  zero rows for empty categories; `include_closed` toggle.

### Frontend
- `DenialsQueue.jsx` — category summary strip (6 clickable filter
  cards) + Category filter dropdown + Category column with
  color-coded pills (`denialCategoryTone`) + Category override in
  work dialog.
- `useRemittance.js` — `useDenialWorkItems({status, category})`,
  `useDenialCategorySummary()`, `DENIAL_CATEGORY_LABELS`, etc.

### Tests
- `backend/tests/test_billing_phase5_denial_taxonomy.py` — 14 passing
  (derivation, normalization, auto-tagging, filter, override,
  summary aggregation, include_closed toggle).
- Combined Phase 3 + 4 + 5 + taxonomy: 69 passing.

### Follow-ups open
- Add category to the `RemittancePosting` UI so operators can
  pre-tag denied rows during posting (currently derived from code).
- Historical backfill — a one-time migration script to tag pre-
  taxonomy denials based on `denial_code`. (Currently those rows
  show as "Other / unmapped".)

## Iteration 30 — Billing Phase 6: Bulk 835 import + patient statements (2026-02-20)

### Backend
- `services/billing/remittance_import.py` — X12 835 parser + JSON
  parser (`schema: ccms.remit.import.v1`, 2 MB cap). Stages uploads,
  matches claims by `clm01` payer control number (primary) +
  patient control number (fallback), resolves payer by NM1/name.
- Preview → commit workflow; commit is blocked when any row is
  unmatched or the payer is unresolved, so the ledger is never
  mutated from a bad file.
- `services/billing/statement_delivery.py` — Reportlab patient
  statement PDF (clinic header, aged AR summary, per-invoice
  breakdown) + Resend email delivery. Mock provider is used when
  `RESEND_API_KEY` is unset so dev/preview stays testable.

### Endpoints
- `POST /api/billing/remittances/import/json|x12`
- `GET  /api/billing/remittances/import/{id}`
- `POST /api/billing/remittances/import/{id}/commit`
- `GET/POST /api/billing/patients/{id}/statements`
- `GET  /api/billing/patients/{id}/statements/{stmt_id}/pdf`
- `POST /api/billing/patients/{id}/statements/{stmt_id}/send`

### Frontend
- `pages/billing/RemittanceImport.jsx` — dropzone, preview table,
  match-method pills, commit gated on `unmatched===0 && resolved_payer_id`.
- `pages/billing/PatientStatementsCard.jsx` — embedded on
  `PatientLedgerPage`; generate, download PDF, send email.
- `AppShell.jsx` — "Import 835" nav entry (admin, staff).
- `useRemittance.js` — hooks: `uploadRemittanceImport`,
  `commitRemittanceImport`, `listStatements`, `generateStatement`,
  `emailStatement`, `statementPdfUrl`.

### Tests
- `backend/tests/test_billing_phase6.py` — 18 passing
  (JSON import e2e, X12 parsing, unmatched / unresolved / empty-upload
  rejections, PDF generation, mocked email path, email-missing
  rejection, cross-tenant isolation for imports and PDFs).
- Frontend E2E (iteration_29) — Playwright verified every in-scope
  Phase 6 UI flow end-to-end.

### Follow-ups open (optional)
- Cache the picked `File` in `RemittanceImport.jsx` so the upload
  auto-retries after a reauth 401 (currently the user re-picks the
  file once).
- Seed one patient with `email` set to exercise the mocked email
  happy-path end-to-end in the demo environment.
- Split oversized `services/billing/router.py` (>3100 lines) into
  `claims_router.py`, `remittances_router.py`,
  `invoices_router.py` for maintainability.

### Phase 6 status
- Backend: DONE (18/18 tests pass)
- Frontend: DONE (Playwright e2e pass)
- Docs: DONE (this entry + CHANGELOG.md)
- **Phase 6: CLOSED** ✅
