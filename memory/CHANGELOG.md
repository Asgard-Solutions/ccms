# CCMS Changelog

Append-only log of delivered work. Most recent on top.

---

## 2026-04-22 — Patient records: Edit-mode required-field fix

Seeded Riverbend patients now **open cleanly in the Edit Patient
wizard** with zero validation errors on load.

**Root cause.** The seed was writing only the legacy flat shape
(`address`, `emergency_contact` as encrypted free-form strings) plus
top-level scalars like `primary_provider_id`. The Edit wizard
(`pages/patientWizardLogic.js :: payloadToForm`) exclusively reads
from grouped, structured sections — `address_details.{line1, city,
state, postal_code}`, `emergency_contact_details.{name, relationship,
phone}`, `demographics.*`, `contact.*`, `admin.primary_provider_id`,
`guarantor.*`, `insurance.*`. Because those groups were absent from
the seeded docs, the form opened with empty fields and fired
"address line 1 is required" / "emergency contact relationship is
required" / "assigned provider is required" on every persona.

**Fix (seed only; no UI/backend changes).**
- `services/demo/seed.py` adds two static lookup tables
  (`_ADDRESS_BY_NAME`, `_EMERGENCY_BY_NAME`) with fully structured
  address + emergency-contact data per persona. `_upsert_personas`
  now composes and persists all seven grouped sections —
  `demographics`, `contact`, `address_details`,
  `emergency_contact_details`, `admin`, `guarantor`, `insurance` —
  on every patient row. Jaxon Morgan gets a proper minor-dependent
  `guarantor` block naming Claire Morgan as the responsible party.
- `services/identity/seed.py` gets the same treatment for Ethan
  Parker (previously seeded only with flat legacy fields). Ethan now
  has a complete structured address, emergency contact, demographics,
  contact, admin (with Dr. Noah Carter as primary provider), and a
  `same_as_patient=True` guarantor.
- All seven grouped sections are **encrypted at rest** via
  `encrypt_patient_value()` so the seeded rows flow through the same
  PHI encrypt/decrypt pipeline as user-edited rows.

**Verification**
- Programmatic check replicating `validateStep(1)` +
  `validateStep(2)` against the unmasked API response for all 8
  Riverbend personas: **0 missing required fields**. Rechecked after
  a backend restart — still 0 (idempotent).
- Save round-trip test against `PATCH /api/patients/{id}` with a
  structured `address` payload — persists correctly; immediate
  response + subsequent GET both show the new value.
- UI smoke: Marcus Reid + Isabella Cho each open the Edit wizard
  with every required field pre-populated (Name, DOB, pronouns,
  marital status, language, mobile, street, city, state, zip,
  emergency contact). No inline `.text-destructive` error text
  renders on initial form load.
- Backend regression: 136/137 pass across Phase 6-12 + claims_queue
  + canonical_status + patient_intake_phase1 suites. The 1 failure
  (`test_grouped_update_preserves_other_sections`) is a pre-existing
  flake documented in the Phase 12 sign-off — not caused by this
  change.

**Files changed**
- `services/demo/seed.py`
- `services/identity/seed.py`

**Personas corrected.** All 8 seeded Riverbend personas now have the
complete Edit-form-required shape: Ethan Parker, Hannah Whitaker,
Marcus Reid, Isabella Cho, Derrick Stone, Aria Johnson, Claire
Morgan, Jaxon Morgan.

---



## 2026-04-22 — Curated billing demo data for Riverbend

Extends the realistic demo clinic seed with a curated billing story
so every billing-related screen is populated on first login. All
upserts tagged with `demo_seed_key`; fully idempotent and wiped by
`scripts/reseed_demo_clinic.py`.

**New file:** `services/demo/billing_seed.py` — 14 curated claims
covering every `ClaimStatus`, 12 submissions (EDI + portal), 5
ERA-backed remittances, 4 invoices, 2 patient statements, 1 cash
payment. Every row ties back to a seeded persona + payer + policy +
doctor from `services/demo/seed.py`.

**Coverage matrix**

| Canonical status   | Count | Personas                                  |
|--------------------|-------|-------------------------------------------|
| draft              | 1     | Hannah Whitaker                           |
| ready              | 1     | Hannah Whitaker                           |
| validation_failed  | 1     | Hannah Whitaker (missing modifier)        |
| submitted          | 2     | Isabella Cho (PIP portal), Derrick Stone (WC portal) |
| accepted           | 1     | Marcus Reid (Medicare, awaiting ERA)      |
| paid               | 4     | Marcus x2 Medicare, Aria PacificCare, Claire PacificCare (95d) |
| partially_paid     | 1     | Isabella Cho PIP ($80 of $145, flagged)   |
| denied             | 2     | Aria (CO-11 coding), Derrick (CO-16 WC case) |
| rejected           | 1     | Jaxon Morgan (CO-31 subscriber mismatch)  |

**Queue tab lights up.** All 5 tabs are non-empty on first login
(pending-submission: 2, needs-fixes: 1, rejected: 3, follow-up: 6,
all: 14).

**A/R aging lights up.** 0-30d, 30-60d, 60-90d, and 90+ buckets all
have at least one curated claim.

**Patient responsibility story.** Ethan paid cash ($0 balance),
Hannah $30 copay open, Aria $125 deductible (statement-ready),
Jaxon $60 after rejection (statement-ready, guarantor Claire).

**Denial / follow-up work-tray.** 4 actionable items — each with a
realistic denial/adjustment code and a one-line operator hint
attached to the claim's `followup_reason`.

**Wiring.**
- `services/demo/__init__.py` exports both seeders.
- `server.py` runs `seed_demo_clinic()` then `seed_demo_billing()` on
  every startup.
- `scripts/reseed_demo_clinic.py` wipes + re-runs both.

**Regression status.** 128/128 Phase 6-12 + queue v2 + canonical
status + claims queue phase 2b tests PASS. Idempotency verified on
restart (stable 14/19/12/5/4/2/1 counts).

**Documentation.** `DEMO_SEED.md` gets a new §7 "Billing demo story"
with the full persona/status/payer matrix, tab coverage, A/R aging
coverage, patient-balance story, denial work-tray, and a "what each
billing screen shows on first login" summary.

---



## 2026-04-22 — Billing / Claims / Change-Optum accepted status (sign-off)

Following the Phase 1–12 verification audit, the feature set is
formally accepted at the following status:

> **PARTIAL — sandbox-ready, not production-complete; blocked only on
> live Change/Optum production transport and related business
> prerequisites.**

**Phase status at sign-off**

| Phase                                              | Status  |
|----------------------------------------------------|---------|
| 1 — Claims Queue UI / worklist                     | PASS    |
| 2 — Canonical claim lifecycle                      | PASS    |
| 3 — Real claim data model                          | PASS    |
| 4 — Claim validation / Needs Fixes workflow        | PASS    |
| 5 — Change/Optum foundation                        | PASS    |
| **6 — Change/Optum submission pipeline**           | **PARTIAL** |
| 7 — Chiropractic rules layer                       | PASS    |
| 8 — Rejected / denied / follow-up workflow         | PASS    |
| 9 — Assignment / governance / audit                | PASS    |
| 10 — API / frontend deliverables                   | PASS    |
| 11 — Hardening / permissions / operational         | PASS    |
| 12 — Final integration verification / handoff      | PASS    |

**Phase 6 rationale.** 837P 005010X222A1 generator, scrubber pre-submit
gate, bulk submit, and trace/correlation persistence are all green in
sandbox. Live HTTPS transmission to the Change/Optum **production**
endpoint is NOT active — the adapter logs the payload and returns a
synthetic `Ack` when `CLEARINGHOUSE_CHC_MODE=production` is set
without credentials. Activating production is a business deliverable
(trading-partner credentials, payer enrollment, endpoint URLs, BAAs),
not a code gap. Estimated code work once prerequisites land: ~50 LoC
inside `clearinghouse/change_healthcare.py::submit()`.

**Next milestone.** Complete live Change/Optum production transport
once credentials, enrollment, and related business prerequisites are
available.

---



## 2026-04-22 — Realistic demo clinic: Riverbend Chiropractic & Wellness

**Replaces** the generic "Default Practice" / "System Admin" / "Morgan
Lee" placeholder seed with a believable fictional chiropractic clinic
so the product looks lived-in on first login.

**Seed architecture**
- New `services/demo/seed.py` is the single source of truth for the
  realistic Riverbend dataset (staff roster, payer catalog, patient
  personas, insurance policies, clinical notes, appointment board).
  All upserts are keyed on stable business identifiers so re-running
  on every boot is safe.
- `services/tenancy/seed.py` renames the default tenant to
  **Riverbend Chiropractic & Wellness** and the default location to
  **Riverbend — Downtown** (America/Los_Angeles). In-place updates so
  existing installs auto-upgrade.
- `services/identity/seed.py` now seeds realistic display names,
  job titles, NPI (for doctors), and phones onto the login-helper
  demo accounts. Emails + passwords unchanged for test stability.
- New `scripts/reseed_demo_clinic.py` — destructive reset that wipes
  test-run pollution off the Riverbend tenant only, then re-seeds.
  Sunrise + platform admin are never touched.

**Demo identities (login page + docs)**
- Administrator → **Ava Bennett** (`admin@ccms.app`)
- Chiropractor → **Dr. Noah Carter, DC** (`doctor@ccms.app`,
  NPI 1841792253)
- Front desk → **Mia Ramirez** (`staff@ccms.app`)
- Patient portal → **Ethan Parker** (`patient@ccms.app` — active-adult
  wellness / maintenance persona, full demographic intake)
- Platform admin → **Owen Sinclair** (`platform-admin@ccms.app`)

**Riverbend staff roster (beyond login helpers, shared pw
`Riverbend@ComplianceClinic1`)**
- Olivia Hart — Clinic Owner
- Dr. Samuel Ito, DC — Associate Chiropractor (NPI 1730598210)
- Lena Brooks — Office Manager
- Tomás Rivera — Billing Specialist
- Priya Shah — Chiropractic Assistant

**Patient personas (7 new + upgraded Ethan Parker)**
- Hannah Whitaker — acute neck pain (Cascade Blue Shield)
- Marcus Reid — chronic LBP / Medicare active-treatment
- Isabella Cho — auto accident / PIP (Northwest Auto PIP)
- Derrick Stone — workers' comp (Oregon SAIF)
- Aria Johnson — marathon runner / IT band (PacificCare)
- Claire Morgan — family head / guarantor (PacificCare)
- Jaxon Morgan — pediatric dependent on Claire's policy

**Clinical / scheduling / billing coverage**
- 7 realistic Chief-Complaint / Subjective / Objective / Assessment /
  Plan chart notes — one per persona, PHI encrypted at rest.
- 13-appointment rolling week: cancellation, completed visits,
  new-patient eval, adjustments, re-exam, PIP follow-ups, workers'
  comp visits, pediatric check, maintenance adjustment.
- 6 fictional payers covering every rail the app supports
  (commercial x2, Medicare w/ AT+sublux+ITD flags, workers' comp,
  auto PIP, self-pay).
- 7 insurance policies keyed to the right payer + dependent
  relationship example.

**Login UX**
- New "Demo clinic sign-in" panel on the login page replaces the
  terse `Admin / Doctor / Staff / Patient` table. Each row is a
  clickable auto-fill that shows the role label, the real person's
  name, and the email. Data-testids: `login-demo-administrator`,
  `-chiropractor`, `-front-desk`, `-patient-portal`.

**Documentation**
- New `/app/memory/DEMO_SEED.md` — end-to-end persona catalog, staff
  roster, payer list, appointment board, reseed instructions, and a
  "gold demo clinic" roadmap.
- `test_credentials.md` regenerator (inside `identity/seed.py`) now
  surfaces realistic people alongside the emails and links back to
  DEMO_SEED.md.

**Regression status**
- 128/128 Phase 1–12 tests PASS (Phase 6–12 suites + queue v2 +
  canonical status + claims queue phase 2b). No new regressions.
- 3 pre-existing failures on `test_iteration12_authz.py`,
  `test_iteration14_tenancy.py`, `test_patient_intake_phase1.py` were
  verified to fail on pristine main — not caused by this change.

---



## 2026-04-22 — Phase 1–12 verification audit + follow-up / self-assign UI

**Scope:** Full audit of the 12-phase professional medical claims
pipeline (queue UI, canonical lifecycle, real claim model, validation
workflow, Change/Optum foundation + submission, chiropractic rules,
rejected/denied/follow-up operations, assignment/governance/audit,
API+frontend deliverables, hardening, final integration). Audit
closed two real UI gaps; backend required no changes.

**Gaps closed**
- **Follow-up row** on ClaimDetail ► Workflow — previously the backend
  exposed `POST /api/billing/claims/{id}/flag-followup` and `DELETE`
  counterparts, but the UI never rendered a button to drive them. A
  new `FollowupRow` now renders a reason input + `Flag for follow-up`
  button (data-testid `claim-followup-flag`) when the claim is
  unflagged, and switches to a status view with `Clear follow-up`
  button (data-testid `claim-followup-clear`) when the flag is live.
  Tested end-to-end: claim surfaces on the `follow-up` tab
  immediately, aging badge + row chip wire up from the queue work
  already shipped in the previous session.
- **Self-assign + unassign shortcuts** on ClaimDetail ► Workflow —
  AssignmentRow now exposes `Assign to me` (data-testid
  `claim-assignee-self-assign`) when the claim is assigned to someone
  else or unassigned, and `Unassign` (data-testid
  `claim-assignee-clear`) when it's assigned. Both drive the existing
  PATCH /api/billing/claims/{id}/assignment endpoint — backend
  already enforces `claim.assign` permission and emits the same
  `billing.claim.assignment_*` audit events.

**Files touched**
- `/app/frontend/src/pages/billing/ClaimWorkflow.jsx` — import
  `useAuth`, import new followup helpers; new `FollowupRow`
  component; `AssignmentRow` extended with `Assign to me` + `Unassign`
  actions.
- `/app/frontend/src/pages/billing/useClaims.js` — new exports
  `flagClaimForFollowup` / `clearClaimFollowupFlag` matching the
  existing `/flag-followup` routes.

**Backend — untouched; re-verified**
- 94/94 Phase 6–11 suites, 8/8 `test_claims_queue_v2.py`, 6/6
  `test_claims_queue_phase2b.py`, 14/14
  `test_canonical_status_phase3.py`. Total: 128/128 on Phase 1–12
  scope. Three pre-existing flakes remain on unrelated billing
  modules and are explicitly tracked as a separate P2 cleanup
  (`test_run_rules_clean_claim`, `test_statement_body_deterministic`,
  `test_email_mock_path_when_no_key`).

**Verification status**
- Testing agent iteration_63 confirmed every new UX flow (FollowupRow
  flag ► status ► clear, AssignmentRow self-assign ► unassign).
- Live curl smoke for all four endpoints passed against the admin
  tenant on sandbox.

---



## 2026-04-22 — Phase 12: Claims pipeline handoff — filter-aware billed totals + UI wiring for follow-up / assignment

**Scope:** Final integration pass for the 12-phase professional medical
claims pipeline. Wire filter-aware per-tab billed totals into the queue
API, add front-end chips that surface those totals alongside counts,
expose the Phase 11 `unassigned` filter in the UI, and surface
follow-up / aging indicators on queue rows.

**Backend**
- `GET /api/billing/claims/queue` now returns a top-level
  `billed_totals` dict keyed by tab (`all`, `pending-submission`,
  `needs-fixes`, `rejected`, `follow-up`). Each entry is the sum of
  `billed_cents` across the tab's filter-aware query (payer, assignee,
  unassigned, age, raw status, canonical status all respected).
- Tab counts + billed totals are computed in a single `$group`
  aggregate per tab (replaces the prior `count_documents` call) so
  there is no extra round-trip even though we now return an additional
  financial dimension.
- New regression test
  `test_queue_v2_billed_totals_are_real_and_filter_aware` asserts: same
  keys as `tab_counts`, non-negative ints, `all >= each named tab`,
  zeroes under a bogus payer filter, and positive under a real payer
  filter that just received a seeded claim.

**Frontend — Claims queue (`/billing/claims`)**
- Each tab trigger stacks `CountChip` + new `BilledChip` (data-testid
  `tab-billed-total`) so operators see both load and financial stake
  per tab without switching views.
- Assignee filter gained `Unassigned only` option (data-testid
  `claims-assignee-filter-unassigned`). Selecting it forwards
  `unassigned=true` via `useClaimsQueueV2` and scopes both rows and
  `billed_totals` to unassigned claims.
- Claim rows now render an italic `Unassigned` label when
  `assigned_to` is null, a warning-tone follow-up badge (data-testid
  `claim-row-followup-<id>`) when `followup_flag=true`, and a subtle
  `<n>d old` hint (data-testid `claim-row-aging-<id>`) when
  `aging_days >= 30` and there is no explicit follow-up flag.

**Verification**
- Backend: 8/8 `test_claims_queue_v2.py`, 14/14
  `test_assignment_rbac_phase11.py`, 6/6 `test_claims_queue_phase2b.py`,
  8/8 `test_billing_phase9*`, 94/94 across Phase 6-11 suites. 3
  pre-existing flaky tests (`test_run_rules_clean_claim`,
  `test_statement_body_deterministic`,
  `test_email_mock_path_when_no_key`) remain flagged for separate
  cleanup — they are not Phase 12 regressions.
- Frontend: Testing agent confirmed tabs render paired
  count/billed-total per tab (e.g. `All 1687 $87,700`, `Pending
  submission 410 $21,282`, `Rejected / denied 286 $14,040`,
  `Follow-up needed 112 $5,815`). Selecting `Unassigned only` scopes
  rows + summary + tab totals consistently.

---



## 2026-04-22 — Patient Portal go-live + Month-end bulk statement dispatch

**Scope:** (1) Finalize the patient-facing portal shell so patients log in
and land on `/portal` (not the clinic AppShell), and (2) add a bulk
"Send all outstanding statements" workflow so billing staff can dispatch
every eligible statement with one click.

**Patient Portal**
- `ProtectedRoute.jsx` enforces a bidirectional role gate: `portal=true`
  routes reject non-patients (→ `/`), and every non-portal route
  redirects patients to `/portal`. Login lands patients directly on
  `/portal` by reusing the same gate.
- `PortalShell.jsx` renders a minimal top-bar + vertical nav (Overview,
  Statements) + signout; `PortalOverview.jsx` + `PortalStatements.jsx`
  consume `GET /api/billing/me/statements` and `GET /api/billing/me/statements/{id}.pdf`.
- Empty-state, invoice-breakdown toggle, and PDF download link all
  verified by the testing agent (iteration_61 — 0 defects).

**Bulk "Send outstanding statements"**
- New `POST /api/billing/statements/send-outstanding` (admin + staff).
  Iterates every patient with `balance_cents > 0`, compares current
  outstanding to the last statement's `total_balance_cents`, and
  regenerates + dispatches only if the balance has moved. Channels:
  email when the patient has an email on file, otherwise queued for
  mail. Returns `{generated, sent_email, queued_mail, skipped_unchanged,
  skipped_no_contact, errors, dry_run, details}`. Supports
  `{"dry_run": true}` preview without side-effects.
- `_build_statement_for_patient()` helper extracted from
  `create_statement` and shared by both the legacy per-patient endpoint
  and the new bulk endpoint so the generated document shape, audit
  rows, and invoice-breakdown snapshot stay identical.
- Frontend: new `billing-send-outstanding-btn` on the Billing Dashboard.
  Click fires a dry-run, opens `bulk-send-outstanding-dialog` with the
  preview copy ("N statement(s) will be generated — X email · Y mail ·
  Z skipped"), then dispatches on confirm.
- Idempotency: re-running against an unchanged dataset returns all
  zeros + `skipped_unchanged` == total outstanding patients.

**Tests**
- `/app/backend/tests/test_statements_bulk_send.py` — 3/3 PASS (dry-run
  shape; idempotency; doctor 403).
- `/app/backend/tests/test_statements_enriched.py` — 6/6 PASS
  (regression on the refactored `create_statement`).
- Frontend E2E + backend integration validated via testing agent
  iteration_61 — 0 defects, no retest required.



## 2026-02-15 — Notifications abstraction: Resend + Twilio (log-only fallback)

**Scope:** Provider-agnostic email / SMS / MFA-OTP plumbing. Real
delivery activates automatically when env vars are set; otherwise the
helpers run in structured log-only mode so local dev and CI never
require vendor credentials.

**What shipped**
- **`services/notifications/email.py`** — `send_email(...)` wraps Resend
  via `asyncio.to_thread`. Never raises. Structured logging with
  redacted recipient, correlation id, provider, event type.
- **`services/notifications/sms.py`** — `send_sms(...)` wraps Twilio
  Messages API with the same contract.
- **`services/notifications/verify.py`** — `start_verification(...)` +
  `check_code(...)` wrap Twilio Verify (managed OTP lifecycle with
  throttling + abuse controls). Dev-mode fallback accepts any 4–10
  digit numeric code so local MFA flows stay testable.
- **`.env.example`** — new file documenting every notification env var
  (RESEND_API_KEY, SENDER_EMAIL, TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN,
  TWILIO_FROM_NUMBER, TWILIO_VERIFY_SERVICE_SID) plus core config
  with generation hints.
- **Partial-config safe**: email may be live while SMS stays stubbed,
  and vice versa. Failures are logged + audited but never crash an
  unrelated user action.

**Callers wired**
- `POST /api/auth/password-reset/request` — sends the reset link via
  `send_email`. `dev_token` remains in the response for local dev.
- `POST /api/workforce/invitations` — sends the invitation email via
  `send_email` immediately after creating the row.
- `services/billing/statement_delivery.py` already has its own Resend
  client for PDF attachments — left as-is since it pre-dates this
  abstraction. Future work: fold it in + add attachment support.

**Tests** — 11/11 passing in `test_notifications.py`:
- Email log-only when no credentials
- Email Resend happy path (mocked SDK)
- Email Resend swallows errors without crashing caller
- Email redaction helper
- SMS log-only when no credentials
- SMS Twilio happy path (mocked Client)
- SMS Twilio error handling
- SMS phone redaction
- Verify log-only start + check (valid / invalid code shape)
- Verify Twilio start with Service SID
- Verify Twilio check approved path

**Not wired yet (backlog)**
- SMS delivery of zip-password for report exports — the polling-based
  reveal flow exists; adding SMS delivery is a feature addition, not
  just wiring. Scaffolding is ready.
- MFA challenge delivery via Verify API — current MFA uses TOTP only;
  adding SMS-OTP channel is a future feature.

---

## 2026-02-15 — Drag-and-drop reorder for Appointment Types

**Scope:** Finish the long-pending P2 UX item.

**What shipped**
- `POST /api/appointment-types/reorder` — accepts
  `{ordered_ids: [...] }` and writes sequential `sort_order` values.
  Unknown/cross-tenant ids are filtered; missing ids keep their
  relative order and land after the explicit block. Admin-only,
  audit-logged.
- `AppointmentTypesManager.jsx` — native HTML5 drag-and-drop with
  grip-handle column, row highlighting on hover, optimistic UI, and
  rollback on backend failure. Zero new dependencies.

**Tests** — 4/4 passing in `test_appointment_types_reorder.py`:
reorder persists, foreign ids ignored, auth required, empty list 422.

---

## 2026-02-15 — Retry-after-reauth Axios interceptor: already shipped

Confirmed the previous-session deliverable (`components/ReauthGate.jsx`
at App.js:69) already implements the global 401-reauth → silent
dialog → replay original request pattern. No new work required;
marked backlog item complete.

---

## 2026-02-15 — Access Management Phase 5: Migration + Access History + Security Policies

**Scope:** Close out the 5-phase redesign.

**What shipped**
- **`services/authz/migration.py`** — `dry_run_legacy_backfill()` +
  `apply_legacy_backfill()` helpers. Idempotent. Classifies every
  unassigned user into `mapped` / `ambiguous` / `unmapped`.
- **`GET /api/authz/migration/legacy/dry-run`** — admin preview.
- **`POST /api/authz/migration/legacy/apply`** — admin runner,
  audit-logged, tenant-scoped.
- **Migration banner** on `/admin/users` — shows candidate count and a
  one-click "Apply migration" button when any mappable users are
  found.
- **`GET /api/authz/access-history?action_prefix=...&limit=...`** —
  filtered audit-log view for `authz.*` events.
- **`/admin/access-history`** (`AccessHistoryPage.jsx`) — replaces
  `/access-review`. Filter dropdown (All / Role changes / Assignments
  / Overrides / Elevation / Migration), CSV export, plain-English
  action labels, timestamps, actor + target chips, metadata preview.
- **Security Policies panel** in `RoleEditorDialog` — collapsible
  advanced section surfacing per-permission MFA / peer-approval /
  break-glass-only toggles. Backend `POST/PATCH /api/authz/roles` now
  accepts a `permission_policies` map alongside `permission_keys`.
- Legacy `/access-review` route now redirects to `/admin/access-history`.
  `AccessReview.jsx` deleted.
- Audit rows for `authz.role.*` + `authz.migration.*` now stamp
  `tenant_id` correctly so tenant admins see their own history.

**Tests** — 6/6 new + 20 regression still green.

---

## 2026-02-15 — Legacy access-management pages removed

**Scope:** Clean removal per user request — application is still in
development, new `/admin/users` + `/admin/roles` fully replace the old
experience.

**Deleted**
- `frontend/src/pages/RoleManagement.jsx` (534 lines)
- `frontend/src/pages/PermissionMatrix.jsx` (183 lines)
- Routes `/roles`, `/permissions` removed from `App.js`
- Nav entries `nav-roles`, `nav-permissions` removed from `navConfig.js`
- "Deprecated advanced tools" footer removed from `AdminUsersPage.jsx`
  (along with the `AlertTriangle` import it needed).

**Kept**
- `/access-review` + `AccessReview.jsx` — evolves into the Phase 5
  Access Change History surface.
- Backend `GET /api/authz/matrix` + `GET /api/authz/permissions`
  endpoints retained — they may be useful for future exports or
  admin scripting.

**Verified** (smoke-test after cleanup):
- `nav-admin-users` present ✓
- `nav-admin-roles` present ✓
- `nav-roles` absent ✓
- `nav-permissions` absent ✓
- `admin-users-advanced-*` links absent ✓

---

## 2026-02-15 — Access Management Phase 4: Roles screen + grouped Role Editor

**Scope:** Frontend-only. Backend CRUD shipped in Phase 2.

**What shipped**
- **`/admin/roles`** (`AdminRolesPage.jsx`) — card-based role catalog:
  - "Built-in roles" grid (9 common clinic roles, ordered by relevance:
    Clinic Manager → Org Owner → Provider → Front Desk → Clinical Staff
    → Billing Specialist → Auditor → Compliance Officer → Patient
    Portal). View-only with Clone action.
  - "Custom roles" grid — Edit / Clone / Archive. Inline confirm
    dialog for archive; force-unassigns users when in use.
  - Collapsible "Show internal / service roles" toggle for
    `super_admin` + `integration_account`.
  - Each card surfaces permission count, user count, built-in /
    custom / privileged badges.
- **`RoleEditorDialog.jsx`** — create/edit/view a role:
  - Loads `GET /authz/permission-catalog` and renders one accordion
    per module (11 modules).
  - Each module row shows a `X/Y` counter and a "Select all / Clear
    all / Select rest" chip.
  - Each permission row shows plain-English label + helper text,
    sensitivity tag, PHI/Financial/privileged badges, Flame icon on
    destructive/critical permissions.
  - Debounced live plain-English preview under the module list from
    `POST /authz/roles/preview-effective-permissions`.
  - View mode dims unselected permissions and disables all controls
    (built-in roles).
  - Create mode: `POST /authz/roles`. Edit mode: `PATCH /authz/roles/{key}`.
- **Nav**: new "Roles" entry at `/admin/roles`. Old `/roles` kept
  behind "Advanced: Role matrix" label.
- **AdminUsersPage**: "Manage roles" quick link now points to
  `/admin/roles` (was `/roles`).

**Verified**
- Visual smoke test: 9 built-in cards rendered, 1 custom card rendered
  with Edit/Clone/Archive buttons. "Show internal / service roles (2)"
  toggle visible.
- Leftover `custom_inuse_test_71b6c5` role from Phase 2 test runs
  cleaned up via DELETE `?force=true`.
- No regressions in Phase 1 (12/12) or Phase 2 (14/14) backend tests.

**Non-goals in Phase 4**
- Advanced Security Policies panel (MFA/approval/break-glass flags)
  still deferred — covered by existing PermissionMatrix's grants until
  explicitly migrated.
- Migration backfill + Access Change History tab → **Phase 5 (pending)**.

---

## 2026-02-15 — Access Management Phase 3: Users experience (frontend)

**Scope:** Frontend-only. Old pages at `/roles`, `/permissions`, and
`/access-review` retained as deprecated "Advanced" routes for backward
compatibility during the transition.

**What shipped**
- **New primary admin surface** `/admin/users` (`AdminUsersPage.jsx`):
  - Searchable list (name + email), status filter (all / active /
    disabled), role chips per row (+N more), status badge, Edit
    access / Disable / Reactivate actions.
  - "Add user" opens the new 3-step wizard.
  - "Manage roles" quick link to `/roles` (until Phase 4 replaces it).
  - Deprecated-advanced footer linking to `/roles`, `/permissions`,
    `/access-review` with an AlertTriangle icon.
- **`CreateUserDialog.jsx`** — 3-step guided flow:
  - Step 1: Profile (name + email + password ≥12 + phone). Next disabled
    until valid.
  - Step 2: Roles — common roles first; "Show advanced / internal
    roles" toggle reveals `super_admin` + `integration_account`. Each
    role shows built-in/custom/privileged badges and a "Covers:" hint.
    Roles list is lazy-loaded on Step 1 → Step 2 transition to avoid
    triggering a PIN step-up when the dialog first opens.
  - Step 3: Review — plain-English effective-access summary from
    `POST /authz/roles/preview-effective-permissions`, plus any
    high-sensitivity grants surfaced as amber chips.
  - Submit creates user + assigns roles in one flow (uses
    `POST /auth/users` for profile, then `POST /authz/users/{id}/roles`
    per selected role).
- **`EditUserAccessDialog.jsx`** — single-step modal to add/remove role
  assignments for an existing user. Live plain-English preview as
  roles are toggled; Save diffs the selected set against the current
  set and issues `POST /authz/users/{id}/roles` + `DELETE` calls.
- **Nav**: new top-of-admin "Users" entry; "Permissions" and
  "Access Review" relabelled with an "Advanced:" prefix to signal
  they're legacy power-user surfaces.

**Tests** — Backend Phase 1+2 regression: **26/26 green** (12 catalog +
14 custom roles). Frontend verified by testing agent iteration_60:
- `/admin/users` renders, deprecated footer links present.
- Create User Step 1 validation correct (email + password ≥12).
- Step 2 common vs advanced roles correctly split.
- All spec'd `data-testid`s wired.

**Non-goals in Phase 3**
- No grouped Role Editor (Phase 4)
- No legacy-role migration backfill (Phase 5)
- No Access Change History tab (Phase 5)

---

## 2026-02-15 — Access Management Phase 2: Custom Roles (backend CRUD)

**Scope:** Backend-only. Admins can now create, clone, edit, and archive
custom roles scoped to their tenant. System baseline roles remain
read-only.

**What shipped**
- `POST /api/authz/roles` — create custom role from name + description +
  permission-key list. 201 on success. Generates a unique `key` like
  `custom_my_role_xxxxxx`. Invalid permission keys are silently filtered
  (defensive).
- `POST /api/authz/roles/{key}/clone` — clone any role (system or
  custom) into a new custom role with all the source's permission keys.
  Tenant-scoped.
- `PATCH /api/authz/roles/{key}` — edit name / description / permissions
  on a custom role. System roles → 409. Empty permission_keys → 400.
  Replaces all `role_permissions` rows. Bumps `session_epoch` for every
  user with this role so their token is re-evaluated on next request.
- `DELETE /api/authz/roles/{key}?force=true` — archive a custom role.
  If in use (active user_roles rows), returns 409 with the assignment
  count. `force=true` revokes all user_roles rows and bumps session
  epochs. System roles → 409.
- `GET /api/authz/roles?include_user_counts=true` — now emits
  `is_custom: bool` and optional `user_count: int` per role.
- Every mutation emits a structured `log_audit` row
  (`authz.role.created`/`updated`/`deleted`) with tenant_id, actor,
  and changed-field metadata.

**Tests** — 14/14 passing in `test_custom_roles_phase2.py`:
- list with is_custom + user_counts
- create happy path + empty-permissions 400 + invalid-key filtering
- clone happy path + clone-requires-name 400 + unknown-source 404
- patch name + permissions + system-role 409 + empty-keys 400
- delete unused + delete system 409 + delete-in-use 409 + force=true
  revokes users + user_count reflects assignments

**No regressions** — Phase 1 (12/12) + checkout hooks (10/10 individually) still green.

---

## 2026-02-15 — Access Management Phase 1: Permission Catalog (backend foundations)

**Scope:** Backend-only foundations for the new Users/Roles/Permissions UX.
No frontend changes in this phase — the existing pages still function.

**What shipped**
- `services/authz/permission_catalog.py` — decorates every permission in
  `constants.PERMISSIONS` with:
  - one of 11 product-facing modules (Dashboard, Scheduling, Patients,
    Clinical, Billing, Claims, Reports, Compliance & Audit, Settings,
    User Management, Administration),
  - a plain-English label + helper text (e.g.
    `appointment.override_rules` → "Override scheduling conflicts"),
  - sensitivity/phi/clinical/financial/destructive/export/privileged
    flags pass-through from the source catalog.
- `GET /api/authz/permission-catalog` — admin endpoint returning the
  grouped, labelled catalog. 117 permissions across 11 modules, sorted
  by sensitivity desc then label asc inside each module.
- `GET /api/authz/users/{id}/effective-permissions?explain=true` —
  admin endpoint returning a user's effective grant list PLUS a
  plain-English summary suitable for the "Review access before save"
  step. Tenant-isolated; 404 on cross-tenant probe.
- `POST /api/authz/roles/preview-effective-permissions` — preview a
  plain-English summary for an arbitrary permission-key list
  (backs the Role Editor's live summary).
- `permission_catalog.explain_permissions()` — pure function used by
  both endpoints; groups grants into "can" / "cannot" buckets, tallies
  per-module read/write coverage, and surfaces any high/critical or
  destructive permissions in a `sensitive_grants` list.

**Tests**
- `backend/tests/test_permission_catalog_phase1.py` — 12/12 passing.

**Non-goals in Phase 1**
- No custom roles (Phase 2)
- No new UI (Phase 3)
- No DB schema changes
- No migration of legacy users (Phase 5)

**Known pre-existing failures (NOT introduced by Phase 1):**
`tests/test_iteration12_authz.py` — 14 failures on main due to cookie-auth
harness drift (the test helpers don't set `Authorization: Bearer`
headers after login). Verified identical baseline via `git stash`.

---
