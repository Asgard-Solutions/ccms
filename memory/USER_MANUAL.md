# CCMS — User Manual

Compliance Clinic Management System (CCMS) is a HIPAA-hardened, AI-augmented practice management platform for chiropractic and integrated-care clinics. This manual is intended for **administrators, doctors, and front-desk staff**. The patient-portal manual lives in §11.

---

## Table of contents

1. Getting started
2. Roles & dashboards
3. Patients module
4. Scheduling
5. Front-desk workflows (kiosk + flow board)
6. Clinical documentation (incl. AI Scribe)
7. AI features (semantic search, NL scheduling, visit briefs, template overrides)
8. Billing & RCM (claims, scrubber, clearinghouse, timeline)
9. Payments & ledgers (Helcim)
10. Communications (SMS + Email)
11. Patient portal
12. Reports & analytics
13. Compliance & security
14. Settings & administration
15. Integrations
16. Keyboard shortcuts & testids

---

## 1. Getting started

### Sign in
- **Web app:** `/login`
- Demo personas auto-fill on click (Administrator, Chiropractor, Front desk, Patient portal).
- Each demo card shows **email**, **password**, and **6-digit PIN**.
- Real users may also see **"Sign in with Google"** if their tenant admin has allow-listed their email domain (`/settings/google`).

### Authentication features
- **Cookie-based JWT** session (no token to copy/paste).
- **15-min idle timeout**, automatic step-up reauth for sensitive actions.
- **TOTP MFA** with 8 backup codes (Settings → Security).
- **6-digit PIN** for in-app re-verification (NOT for sign-in). Set / rotate at Settings → Security.
- **Suspicious-login step-up:** new IP / device triggers an email or SMS challenge.
- **Forgot password:** single-use 15-min token over email.

### First-time setup checklist for tenant admins
1. Confirm clinic profile at **Settings → Clinic profile** (NPI, address, tax ID, default time zone).
2. Add **locations** at Settings → Locations.
3. Onboard **providers** at Settings → Users → Add user (role=doctor, NPI, DEA where required).
4. Onboard **front-desk staff** the same way (role=staff).
5. Configure **payer catalog** at Settings → Payers (add payer IDs, clearinghouse route, fee schedules).
6. Configure **integrations**: Twilio (SMS), Resend (email), Helcim (payments), Google OAuth domain allowlist.
7. (Optional) Customize **AI templates** at Settings → AI templates per location/provider.

---

## 2. Roles & dashboards

| Role | Default landing page | Key capabilities |
|---|---|---|
| **Admin** | `/dashboard` | All clinic operations + settings. Cannot un-sign other doctors' notes. |
| **Doctor** | `/dashboard` (My queue) | Sign clinical notes, run AI Scribe, view PHI without break-glass. Cannot edit fee schedules. |
| **Staff** (front desk) | `/dashboard` (Today's schedule) | Check in / room / checkout patients, post payments, send SMS. Cannot sign clinical notes. |
| **Platform admin** | `/platform` | Cross-tenant operations. Out of scope for clinical users. |
| **Patient** | `/portal` | Patient-portal only (see §11). |

The dashboard adapts to role. All users see:
- **Today's schedule** snapshot (location-scoped).
- **My queue** (assigned tasks).
- **Recent activity** (depending on role).
- **Quick actions** strip (Add patient / Book appointment / Charge capture).

---

## 3. Patients module

### Patient list — `/patients`
- Search by name, MRN, phone, email, DOB.
- Filter by status (active / inactive / discharged), tag, location.
- Bulk-export a patient list (CSV) — admin only, audit-logged.

### Patient chart — `/patients/<id>`
The chart is divided into stacked cards:

| Card | Purpose |
|---|---|
| **Header** | Demographics, masked-by-default PHI (click to unmask — audit fires), photo, MRN |
| **Episodes** | Open / closed episodes (injury, MVA, WC, pediatric, self-pay, insurance) |
| **Diagnoses** | Active and resolved ICD-10 dx, primary/secondary, episode-linked |
| **Treatment plans** | Goals, baselines, interventions, discharge criteria |
| **Encounters** | Initial exams, follow-up notes, re-exams, with sign status |
| **Outcomes** | NDI, Oswestry, Pain VAS, etc., with inline SVG trend charts |
| **Care timeline v2** | Unified chronological feed of every clinical event |
| **Imaging & media** | X-rays, MRIs, PDFs (25 MB cap), immutable binary store |
| **Ledger** | Patient balance, invoice list, allocations |
| **Communications** | SMS / email log with PHI-masking toggle |
| **Audit events** | Patient-scoped audit trail |

### Adding a patient
- **From the chart list:** Add patient → fill demographics + emergency contact → save.
- **From booking:** patient created automatically by a portal booking request.
- **From kiosk:** new walk-in fills a public intake form; record gets queued for staff approval.

### PHI handling
- All sensitive fields (DOB, SSN, address, phone, email, payer IDs, member IDs, accident details) are **masked by default** and **AES-256-GCM encrypted at rest**.
- Click the eye icon to unmask — you'll see a **break-glass dialog** asking why; the unmask + reason are audit-logged.
- Soft-delete + 7-year retention is enforced for HIPAA.

---

## 4. Scheduling

### Calendar — `/scheduling`
- **Day view** and **week view**, color-coded by appointment type.
- Drag-and-drop reschedule (admin/staff/doctor with permission).
- Sidebar filters: location, provider, room, status, type.
- Click an empty slot to **create an appointment**.

### Smart scheduling
- **9 visit types** (initial exam, follow-up, re-exam, modality, therapy, etc.) with default duration + follow-up cadence.
- **Room types** auto-assigned (exam / consult / x-ray / therapy).
- **Provider filter / My queue** — doctors see only their own appointments.

### Natural-language scheduling (Quick-book)
- The **NL booking card** lives at the top of the scheduling page.
- Type a plain sentence — examples:
  - *"Book Hannah Whitaker for an adjustment with Dr. Carter next Friday at 10am"* (create)
  - *"Reschedule Hannah's Friday adjustment to next Monday at 11am"* (reschedule)
  - *"Cancel Hannah's Friday appointment"* (cancel)
- The parser uses Claude Sonnet 4.5 to extract intent + entities. UI shows a confirmation card with the resolved patient, provider, type, and slot before committing.
- 409-conflict → inline conflict message.

### Appointment workflow panel
On any appointment, the right-hand panel exposes:
- **Check in** → flips to `checked-in`.
- **Room** → assigns a room, flips to `roomed`.
- **Launch encounter** → spawns the right kind of clinical note (initial exam vs. follow-up) and routes the doctor to the editor.
- **No-show** / **cancel** with reason.
- **Reschedule** opens the move dialog.

### Online booking (patient portal → staff queue)
- Patient submits a booking request from `/portal/book`.
- The request lands in the **Booking-request queue** (`/scheduling/requests`).
- Staff confirm or reschedule into the real calendar; patient is auto-notified by SMS.

### Reminders
- **24-h** SMS reminder, **same-day** SMS, and a **post-visit review request** 1 day after.
- All controlled at Settings → Notifications.

---

## 5. Front-desk workflows

### Today's schedule snapshot
On the dashboard, staff see appointments for the current location with a one-click action column (check-in / room / launch / checkout).

### Flow board — `/scheduling/flow`
- Kanban columns: **Scheduled → Checked-in → Roomed → In encounter → Checkout**.
- Drag patient cards across columns to move state.
- Color-coded by appointment type and wait time.

### Kiosk — `/kiosk` (public, tablet-friendly)
- Patients sign in with last name + DOB.
- They confirm demographics + insurance + sign HIPAA acknowledgment.
- Auto-flips appointment to `checked-in`.
- Tablet auto-locks between patients.

### Checkout queue
- Patients in **In encounter** that have a signed note + posted charge appear in the **Checkout** column.
- Staff post payment, schedule the follow-up rebook, and flip status to `checked-out`.

### Two-way SMS inbox — `/communications/sms`
- Inbound texts threaded by patient.
- Reply, mark as read, escalate to a task.
- PHI is masked by default; staff can unmask with reason.

---

## 6. Clinical documentation

### Note types & routes

| Note | Route | When |
|---|---|---|
| **Initial Exam** | `/patients/<pid>/clinical/exams/<eid>` | First visit / new episode |
| **Follow-up / Daily Visit Note** | `/patients/<pid>/clinical/follow-up/<nid>` | Each subsequent visit |
| **Re-Exam** | `/patients/<pid>/clinical/re-exams/<rid>` | Periodic outcome reassessment |
| **Treatment plan** | `/patients/<pid>/clinical/treatment-plans/<tpid>` | Goal & intervention plan |

All editors share the same shell:

- **Left column** — note fields (Subjective, Objective, ROM/Neuro, Assessment, Plan, Outcomes, Coding).
- **Right column** — AI Scribe panel + prior-context chip.
- **Top bar** — save / preview / sign / addendum.

### Editing rules
- A note is **draft** until you click **Sign**. Once signed, it's immutable.
- After signing, you can append **signed addenda** (reason + narrative). Each addendum is individually signed and audit-logged.
- Doctors can only sign their own notes; admins can review but not sign for others.

### Macros & templates
- **Settings → Clinical templates**: chief complaint, HPI, ROS, OMT kinds. Per-location and per-provider.
- Quick-insert from the editor with `/macro` shortcut.

### AI Scribe panel
The Scribe is the **right-side panel inside Initial Exam + Follow-up editors**. See §7 for full walkthrough.

### Outcome measures
- NDI, Oswestry, Pain VAS, Patient Satisfaction, custom.
- Delivered to patients via the portal or filled by staff in-clinic.
- Render as inline SVG trend lines on the chart and on re-exam notes.

### Imaging & media
- Drag-and-drop x-rays, MRIs, PDFs.
- Server enforces a 25 MB per-file cap and immutable binary storage.
- Media is patient-scoped, audit-logged on every view/download.

---

## 7. AI features

All AI features run on **Claude Sonnet 4.5** (text) and **OpenAI Whisper** (voice). Both routed via the Emergent Universal Key — no separate vendor key required.

### 7.1 AI Scribe (voice or text → SOAP note)
**Where:** Right side of Initial Exam and Follow-up note editors.

**Step-by-step:**
1. **Record** with the mic button or **upload** an audio file (mp3/wav/m4a/aac/flac/ogg).
2. (Optional) Type extra context in the **Addendum** box — useful for skipping the mic during testing.
3. Click **Draft SOAP**. Claude generates a structured Subjective / Objective / Assessment / Plan, pulling **prior-encounter context** for the same patient automatically.
4. Review each section. Click **Apply All** to paste into the editor — coding suggestions auto-fire.
5. Optional: pick a payer + click **Create draft claim** (next §) to send straight to billing.

### 7.2 Inline CPT/ICD coding suggestions
- Auto-fires after **Apply All**.
- Returns CPT codes (with modifier hints), ICD-10 codes (with primary candidate flag), and documentation warnings ("note doesn't mention WB-VAS — consider adding").
- Each suggestion has Accept / Dismiss buttons.

### 7.3 Send to claim from Scribe
- After accepting CPT + ICD codes, pick a payer in the **Create draft claim** block.
- One click creates a draft claim with all accepted lines.
- Billed amounts auto-resolve from the payer's fee schedule (`resolve_charge_price`).
- The Scribe panel then offers a **Submit to clearinghouse** button — see §8.5.

### 7.4 Patient-facing AI visit brief
**Where:** Patient portal landing card after each signed encounter.
- Plain-language summary of the visit (no jargon, no codes).
- Updates whenever the doctor signs an addendum.

### 7.5 Natural-language semantic search
**Where:** `/patients/<pid>/search` (also accessible as a chip on the chart).
- Type a plain question — *"What was Hannah's pain trend over the last 3 visits?"* / *"Which patients haven't returned for their re-exam?"*
- Claude ranks up to 30 chart snippets and returns a 2-3 sentence answer with `[s#]` citations.
- Cached per (patient_id, query_hash) for snappy re-asks.

### 7.6 Context-aware documentation
- Every Initial Exam / Follow-up editor shows a **Prior sections chip** in the top bar.
- One click pulls the patient's most recent prior SOAP into a side drawer for reference while authoring.
- The same prior context is fed silently into every AI Scribe call.

### 7.7 Natural-language scheduling
- See §4 — Quick-book card on `/scheduling`. Supports Create, Reschedule, Cancel.

### 7.8 SOAP-template overrides per location/provider
**Where:** Settings → AI templates (admin only).
- Set custom SOAP system instructions at Tenant → Location → Provider levels.
- Overrides stack: provider override (most specific) wins, falling back to location, falling back to tenant.
- Use this to encode local style — e.g., "Always include outcome score in Plan."

---

## 8. Billing & RCM

### 8.1 Charge capture — `/billing/charge-capture`
- Open after a signed encounter or directly from the chart's encounter row.
- Pick CPT codes + modifiers + diagnosis pointers.
- Fee schedule auto-fills `billed_cents`.
- Output: a **draft claim line** attached to the encounter.

### 8.2 Claims queue — `/billing/claims`
- Four tabs: **All / Pending / Denied / Follow-up**.
- 14-row multi-lifecycle table with assignment, status pill, last action.
- Filters: payer, location, status, assignee, date range.
- Bulk actions: assign, mark for follow-up, export.

### 8.3 Claim detail — `/billing/claims/<id>`
- Header: claim status pill, payer, total billed, links to encounter + patient.
- Line editor: drag-reorder, add/remove lines, adjust units / billed_cents / modifiers.
- Diagnosis editor: add/remove ICD-10, set primary, link to lines.
- **Buttons:**
  - **Validate** — runs the 17-rule scrubber.
  - **Submit** — submits a `ready` claim through the resolved adapter.
  - **Submit to clearinghouse** (one-click) — runs scrubber + transitions + submits in a single call (recommended).
- **Live timeline** card animates with every state change (see §8.6).

### 8.4 Claim scrubber
- 17 rules covering NPI presence, modifier validity, diagnosis pointers, place of service, subluxation-dx for chiropractic, WC case number, and more.
- Outputs **errors** (block submission) and **warnings** (allow submission).
- Failed scrubs put the claim in `validation_failed` status with a list of findings.

### 8.5 One-click Submit to Clearinghouse
**Endpoint:** `POST /api/billing/claims/<id>/quick-submit`

**Pipeline:** scrubber → ready transition → adapter.submit. Single transactional call.

- **Sandbox / disabled adapters** allow scrubber failures (flagged `submitted_with_warnings`) so demos and pre-enrollment testing keep working without putting PHI on the wire.
- **Production adapters** strictly enforce scrubber pass; failures return 422.

The button is available in two places:
- Inside the **AI Scribe panel** after a draft claim is created.
- On the **Claim detail** page next to the existing Submit button.

Status pill on success: `queued · sandbox` (Change Healthcare sandbox), `accepted` (live), or `manual` (NoneAdapter — no clearinghouse routing).

### 8.6 Live submission timeline
**Where:** Bottom card on Claim detail.

- WebSocket connection to `WS /api/billing/ws/claims/<id>/events`.
- Pill shows **Live** (WS), **Connecting**, or **Polling** (fallback).
- 30-second polling runs in parallel; events deduped by id.
- Sandbox claims auto-progress through synthetic 999 → 277CA → outcome → ERA in ~20 s.

### 8.7 Eligibility verification (270/271)
- From a payer-side claim or directly on the patient chart, click **Check eligibility**.
- Returns coverage, plan name, deductible, out-of-pocket, copay.

### 8.8 ERA auto-posting (835)
- Drop a 835 file at Settings → Billing → ERA imports OR ingest from the clearinghouse adapter.
- The system parses CARC/RARC, posts payments, allocates against open invoices, surfaces any shortfalls in **Denials queue**.

### 8.9 Denials queue & heat map
- **`/billing/denials`** — denial codes, reason, counts, filters by category and date.
- **Reports → Denial heat map** — category × month pivot with intensity gradient.

### 8.10 A/R aging — `/billing/ar-aging`
- 0-30 / 31-60 / 61-90 / 91-120 / 120+ buckets per payer + per patient.
- Drill-down to invoice level.

### 8.11 Other queues
- **Follow-up** — claims flagged for human follow-up (with reason + due date).
- **Validation failed** — claims that didn't pass the scrubber.
- **Resubmission queue** — denials awaiting correction & resubmit.

---

## 9. Payments & ledgers

### 9.1 Helcim integration
- HelcimPay.js modal for card-not-present payments.
- Customer Vault for saved payment methods.
- Per-tenant credentials, encrypted with AES-256.
- Webhook signature verification on every callback.

### 9.2 Patient ledger card / page — `/patients/<id>/ledger`
- Running balance, invoice list, payment list, allocations.
- Post payment dialog allocates to invoices automatically (oldest-first by default; manual override).

### 9.3 Statements
- Generate a statement on demand or on schedule (monthly).
- Email + portal delivery.
- Statement auto-pay enrolls the saved card to draft on the due date.

### 9.4 Payment plans
- Split a charge into N installments.
- Recurring auto-charge engine drafts on each due date.
- Plan dashboard at `/billing/payment-plans`.

### 9.5 Treatment-plan auto-pay
- Enroll a treatment plan to auto-charge per-visit.
- Useful for cash-pay packages.

### 9.6 Refunds & voids
- Refund dialog on invoice → posts to Helcim, updates ledger.
- Voids on a same-day charge bypass the refund flow.

---

## 10. Communications

### 10.1 SMS — Twilio
- Two-way inbox at `/communications/sms`.
- Templates at Settings → Communications → SMS templates.
- All outbound messages logged in the patient's communication log; PHI masked by default.

### 10.2 Email — Resend
- Templates at Settings → Communications → Email templates.
- Used for appointment reminders, password reset, statement delivery, MFA challenges.

### 10.3 Notifications
- The notification engine fires on system events (appointment booked, claim denied, payment posted, signed note, etc.).
- Per-role default routing at Settings → Notifications.

---

## 11. Patient portal

### 11.1 Access
- `/portal/login` — phone-first SMS OTP login.
- Patients enter their mobile phone → receive a 6-digit OTP → land on their portal.

### 11.2 Capabilities
- View appointments + book new ones.
- Fill outcome questionnaires and intake forms.
- View statements and pay online.
- Read AI-generated visit briefs.
- Message the clinic via two-way SMS.
- Self-service data export.

### 11.3 Privacy controls
- Patients can toggle SMS reminders, email frequency, and revoke saved cards from the portal directly.
- Every PHI-impacting action is audit-logged.

---

## 12. Reports & analytics

| Report | Route | What it shows |
|---|---|---|
| Billing dashboard | `/dashboard` (admin tab) | Outstanding balance, lifetime billed, payments recorded |
| Today's schedule | `/dashboard` | Snapshot of all appointments + statuses |
| Clinical summary | `/dashboard` (doctor tab) | Visits / exams / plans / re-exams / notes / dx counts |
| **Custom reports** | `/reports` | Filterable, column-pickable, exportable to CSV; saved views |
| Payer analysis / payer mix | `/reports/payer-mix` | Per-payer claim count, billed, paid, outstanding, denied |
| A/R aging | `/billing/ar-aging` | Buckets per payer + per patient |
| Denial heat map | `/reports/denial-heatmap` | Category × month pivot |
| Outcome trends per measure | `/patients/<id>` (Outcomes card) | Inline SVG charts, baseline → current |

All exports are audit-logged.

---

## 13. Compliance & security

### 13.1 HIPAA controls
- AES-256-GCM PHI field encryption at rest.
- Masked-by-default PHI everywhere.
- **Break-glass** dialog with reason capture for any unmask.
- 7-year retention with soft-delete.
- Append-only signed addenda; signed notes are immutable.

### 13.2 Audit log — `/admin/audit`
- Every action: `auth.login`, `clinical.note.signed`, `claim.submitted`, `payment.posted`, `phi.unmasked`, etc.
- Filterable by actor, target, date, event type.
- Export (admin only).

### 13.3 Compliance Ops surface — `/compliance`
- Evidence library, Incident register, Vendor register, Risk register, Policy library, Access review cycles.
- Designed for SOC 2 / HIPAA / ISO 27001 readiness.

### 13.4 Identity hardening
- Bcrypt password hashing.
- Per-user / per-IP brute-force protection.
- Step-up reauth (password OR PIN) for sensitive actions.
- Session epoch bump invalidates stale tokens on email change.
- TOTP MFA + 8 backup codes.
- 6-digit PIN with masked entry, lockout, rotation.

### 13.5 Integrity verifier
- Background job that checks foreign-key integrity, duplicate detection, shape conformance.
- Flagged anomalies surface in **Compliance Ops → Integrity**.

---

## 14. Settings & administration

| Settings page | Route | Who |
|---|---|---|
| Clinic profile | `/settings/clinic` | Admin |
| Locations | `/settings/locations` | Admin |
| Users | `/settings/users` | Admin |
| Roles & permissions | `/settings/roles` | Admin |
| Payers | `/settings/payers` | Admin / Biller |
| Fee schedules | `/settings/fee-schedules` | Admin / Biller |
| Appointment types | `/settings/appointment-types` | Admin |
| Rooms | `/settings/rooms` | Admin |
| Clinical templates | `/settings/clinical-templates` | Admin / Doctor |
| **AI templates** (SOAP overrides) | `/settings/ai-templates` | Admin |
| Notifications | `/settings/notifications` | Admin |
| SMS templates | `/settings/sms` | Admin / Staff |
| Email templates | `/settings/email` | Admin / Staff |
| Helcim payment | `/settings/helcim` | Admin |
| Google OAuth domains | `/settings/google` | Admin |
| Compliance Ops | `/compliance` | Admin / Compliance officer |
| Security | `/settings/security` | Self (current user) |

Workforce management (onboarding, disable, password reset) lives at `/settings/users`.

---

## 15. Integrations

| Integration | Purpose | Where to configure |
|---|---|---|
| **Twilio** (SMS) | Reminders, two-way inbox, OTP | Settings → SMS |
| **Resend** (Email) | Reminders, statements, password reset | Settings → Email |
| **Helcim** | Payments + Customer Vault | Settings → Helcim |
| **Google OAuth (Emergent-managed)** | SSO | Settings → Google |
| **Change Healthcare** (clearinghouse) | 837P submission, 999/277CA acks | Settings → Payers (per-payer route) |
| **Optum** (clearinghouse) | Same as above; alternate route | Same as above |
| **Emergent LLM Universal Key** | Claude Sonnet 4.5 + OpenAI Whisper | No setup — pre-configured |
| **Emergent Object Storage** | Imaging & clinical media | No setup |

---

## 16. Keyboard shortcuts & test IDs

### Shortcuts (when focused inside an editor)
- `Ctrl/Cmd + S` — Save draft
- `Ctrl/Cmd + Enter` — Sign note (requires re-auth)
- `Ctrl/Cmd + K` — Open command palette / search
- `/macro` then template name — quick-insert macro

### Useful test IDs (for QA & automation)
- `login-page`, `login-demo-{role}`, `login-demo-{role}-pin`
- `scribe-quick-submit-btn`, `claim-quick-submit-btn`, `claim-submit-btn`
- `claim-timeline`, `claim-timeline-ws-state`, `claim-timeline-events`
- `nl-book-patient-select`, `nl-book-start-input`, `nl-book-cancel-summary`
- `ai-templates-provider-search`, `ai-templates-overflow-hint`
- `patient-dashboard`, `get-started-btn`

---

## Appendix A — Demo credentials quick reference

See `/app/memory/test_credentials.md` for the full table.

| Role | Email | Password | PIN |
|---|---|---|---|
| Administrator | admin@ccms.app | Admin@ComplianceClinic1 | 100001 |
| Chiropractor | doctor@ccms.app | Doctor@ComplianceClinic1 | 200002 |
| Front desk | staff@ccms.app | Staff@ComplianceClinic1 | 300003 |
| Patient | patient@ccms.app | Patient@ComplianceClinic1 | 400004 |

PIN is for in-app re-verification (not sign-in). Run `python -m scripts.seed_demo_pins` from `/app/backend` to rotate idempotently.

---

## Appendix B — Where to ask for help

- **Bug / feature request:** open an issue with the relevant test ID and a screenshot.
- **HIPAA / compliance question:** Compliance Ops surface → Policies, then escalate to your compliance officer.
- **Billing escalation:** Claim detail → Assign + flag for follow-up + add note.
- **Platform admin:** for cross-tenant or infrastructure issues, contact your platform admin (`/platform`).

---

*Last updated: 2026-05-04. Document lives at `/app/memory/USER_MANUAL.md`.*
