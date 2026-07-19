# CCMS — End-to-End Patient Workflow

This document walks through a complete patient journey: from first contact through clinical care, billing, and discharge. Roles are called out at each step. Use this as the primary onboarding playbook for new staff and as a verification path for end-to-end testing.

The example patient is **Hannah Whitaker** (existing demo persona) presenting with low-back pain. Substitute any patient.

---

## Phase 0 — Pre-visit

### Step 0.1 — Patient discovers the clinic
- Patient visits the clinic's website, finds the booking link, or calls the front desk.
- If they call, the **front-desk staff** can either book directly in the calendar OR send them the portal booking link by SMS / email.

**Roles:** Patient · Staff (optional)
**Routes:** `/portal/login`, `/portal/book`

### Step 0.2 — Online booking (patient side)
1. Patient signs in to the **patient portal** at `/portal/login` with phone + SMS OTP.
   - First-time visitors are auto-created as a `pending` patient record.
2. They click **Book an appointment** → pick visit type, location, provider preference, preferred slot.
3. The booking submits as a **request** (not a confirmed appointment) to give the clinic control over their schedule.

**Output:** New row in the booking-request queue.

### Step 0.3 — Booking-request queue (front desk)
1. Staff opens `/scheduling/requests`.
2. They see the inbound request with patient info + preferred slot.
3. Either **accept** (slot reserved on the calendar, patient SMS-notified) or **propose alternative** (offer 2-3 alternative slots; patient receives an SMS to pick one).

Optionally, staff can use **Quick-book** on `/scheduling` to book any patient by typing plain English:

> *"Book Hannah Whitaker for an initial exam with Dr. Carter next Tuesday at 9am"*

The Quick-book card resolves the patient, provider, type, and slot, then asks for one click to confirm.

### Step 0.4 — Digital intake forms
Once the appointment is confirmed, the system automatically:

1. Sends an **intake invitation** SMS / email with a portal deep-link.
2. Patient fills in:
   - Demographics + emergency contact
   - Insurance card (front + back photo)
   - Chief complaint + HPI
   - Red-flag screening (saddle anesthesia, severe night pain, weight loss, etc.)
   - Occupation / employer
   - Case-specific intake (MVA accident report or WC claim details if applicable)
   - Outcome questionnaires (NDI, Pain VAS) — these become the **baseline** scores
   - HIPAA notice of privacy practices acknowledgment + e-signature
3. Submitting the form auto-populates:
   - Patient demographics + insurance
   - The future Initial Exam's **Subjective** and **clinical_history** fields
   - Outcome baselines

### Step 0.5 — Pre-visit reminders
- 24-h SMS: *"Reminder: appointment with Dr. Carter tomorrow at 9 am at Riverbend."*
- Same-day SMS at +2 h: *"See you in a couple of hours."*
- Eligibility check fires automatically the morning of the visit (270/271). If coverage is inactive, a notification lands on the staff dashboard so they can call the patient before they show up.

---

## Phase 1 — Arrival

### Step 1.1 — Check-in (kiosk OR front desk)

**Option A — Kiosk** (`/kiosk`, public, tablet-friendly)
1. Patient walks in, taps the tablet at the front desk.
2. Last name + DOB.
3. Confirms demographics + insurance.
4. Re-signs HIPAA acknowledgment.
5. Appointment auto-flips to `checked-in`.

**Option B — Front desk**
1. Staff opens **today's schedule** on the dashboard.
2. Finds the patient row → clicks **Check in**.
3. Confirms insurance is on file.

**Roles:** Patient (kiosk) OR Staff (front desk)

### Step 1.2 — Rooming
1. Staff sees the patient on the **flow board** in the `Checked-in` column.
2. Drags the patient card to the `Roomed` column, picking an available exam room.
3. Patient is now waiting for the doctor.

The doctor's **My queue** automatically surfaces patients in `roomed` for their assigned location.

---

## Phase 2 — The encounter

### Step 2.1 — Launch encounter
1. Doctor opens the patient on the flow board OR clicks the patient card in their dashboard.
2. Clicks **Launch encounter**.
3. The system creates the right kind of clinical note:
   - **Initial Exam** — first visit / new episode.
   - **Follow-up Note** — every subsequent visit.
   - **Re-Exam** — periodic outcome reassessment (typically every 6-12 visits).
4. The doctor lands on the editor.

### Step 2.2 — Author the note (Initial Exam example)

The editor has three columns: form fields (left), AI Scribe panel (right), top toolbar with prior-context chip.

#### Path A — Manual entry
- Doctor fills Subjective / Objective / ROM / Neuro / Assessment / Plan by hand.
- Quick-insert macros via `/macro` (e.g., `/lbp` for the standard low-back-pain template).

#### Path B — AI Scribe (recommended)
1. Click the **mic** in the AI Scribe panel and dictate the visit (auto-transcribed via Whisper).
   - **OR** type extra context in the Addendum box (skip recording entirely).
2. Click **Draft SOAP**. Claude Sonnet 4.5 produces a structured SOAP note pulling **prior-encounter context** automatically — so a follow-up note knows what the previous visit said.
3. Review each section. Tweak as needed.
4. Click **Apply All** — fields populate, **CPT/ICD coding suggestions** auto-fire on the right (e.g., 98941, M54.5).
5. Accept individual suggestions or **Apply All Codes**.

#### Outcomes
- If today is a re-exam visit, the patient just filled NDI / Pain VAS in the waiting room; the new scores stream into the Outcomes card with a delta vs. baseline.

#### Imaging
- Drag x-rays / MRI thumbnails into the **Imaging** card on the chart for permanent attachment.

### Step 2.3 — Treatment plan (Initial Exam only)
After the SOAP, the doctor opens or creates a **Treatment plan**:

1. Diagnoses (ICD-10) — primary + secondary.
2. Goals (e.g., "VAS ≤ 2/10 within 6 weeks").
3. Interventions (manipulation, soft-tissue, modalities, exercises).
4. Cadence (frequency × duration).
5. Discharge criteria.

The plan auto-creates an **episode** linking dx + outcome measures + intended follow-ups.

### Step 2.4 — Sign the note
- Doctor clicks **Sign**.
- A **step-up reauth** dialog appears (password or PIN).
- Once signed, the note is **immutable**. Future edits must be appended as **signed addenda**.
- The system fires:
  - `clinical.note.signed` audit event
  - **Patient-facing AI visit brief** (regenerated for the portal)
  - Outcome auto-emit on Re-Exam (creates a standalone outcome entry)

---

## Phase 3 — Charge capture & checkout

### Step 3.1 — Charge capture
**Path A — From the Scribe**
- The AI Scribe panel showed CPT codes during the encounter.
- The doctor (or staff) clicks **Create draft claim** in the Scribe panel:
  1. Pick the patient's payer (Aetna in our example).
  2. Submit → server creates a draft claim with all accepted CPT/ICD lines and auto-resolves billed amounts from the **payer fee schedule**.
- The **Submit to clearinghouse** button appears next to the **Open claim →** link.

**Path B — Manual** (if the Scribe wasn't used)
- Open `/billing/charge-capture` from the chart's encounter row.
- Pick CPT + modifiers + diagnosis pointers.
- Save → produces the same draft claim.

### Step 3.2 — Checkout (front desk)
The patient drifts back to the front desk. Staff opens the **Checkout** column on the flow board.

1. **Take payment** — patient owes a copay or self-pay amount.
   - Click **Post payment** → HelcimPay.js modal opens.
   - Patient swipes / taps / enters card.
   - Vault save offered (Customer Vault) for future auto-pay.
   - Allocation auto-applies oldest-first; staff can override.
2. **Schedule the next visit** — click **Rebook** in the suggestion panel:
   - The system recommends a follow-up date based on the treatment plan cadence.
   - Staff confirms → SMS reminder is queued for 24-h before the next visit.
3. **Receipt** — emailed automatically; printable from the success dialog.
4. Flow board card flips to **Checked-out**.

---

## Phase 4 — Billing & RCM

### Step 4.1 — Submit the claim (one-click)
- Either:
  - Click **Submit to clearinghouse** in the Scribe panel right after charge capture, OR
  - Open `/billing/claims/<id>` later and click **Submit to clearinghouse** there.
- The **quick-submit** pipeline runs:
  1. **Scrubber** — 17 rules, NPI / modifier / dx / WC checks.
  2. **Ready transition** — claim flips from `draft` → `ready` if the scrubber passes (or if adapter is in sandbox mode).
  3. **Adapter.submit** — Change Healthcare (sandbox) or Optum or NoneAdapter, depending on the payer's `clearinghouse_route`.
- Toast: `change_healthcare (sandbox): queued`.

### Step 4.2 — Watch the live timeline
- On `/billing/claims/<id>`, the **Live timeline** card animates as events come in.
- WebSocket pill shows **Live**.
- For sandbox claims, synthetic events tick in over ~20 seconds:
  - +5 s: 999 functional ack (accepted)
  - +10 s: 277CA claim ack (accepted)
  - +15 s: outcome recorded (accepted)
  - +20 s: ERA posted (paid)
- Claim status pill flips to **PAID**.

For real production claims, the same timeline is driven by the **production-mode ack poller** (60-second loop calling the adapter's `fetch_ack_999` / `fetch_ack_277ca`).

### Step 4.3 — Handle a denial (alternate path)
If the claim is denied:

1. The timeline shows `277ca_rejected` or `denied` events.
2. Claim auto-routes to **Denials queue** (`/billing/denials`) with the CARC/RARC code.
3. The biller opens the denial, reads the reason, and **Resubmits** with corrections (typically: add a modifier, fix a dx, or correct a place-of-service).
4. Resubmission re-enters the lifecycle starting at scrubber.

### Step 4.4 — ERA posting (when payment arrives)
- Real ERAs (835 files) drop into the system either by clearinghouse adapter pull or manual upload at Settings → Billing → ERA imports.
- The system parses CARC/RARC, posts the payment, allocates against the claim's invoice, and surfaces shortfalls in the Denials queue.

---

## Phase 5 — Follow-up visits

### Step 5.1 — Reminder + booking
- 24-h SMS reminder + same-day SMS reminder for the rebooked follow-up.
- Patient may also self-reschedule from the portal.

### Step 5.2 — Subsequent visits — short loop
Every follow-up runs a tighter loop than the initial visit:

1. Check-in (kiosk).
2. Rooming.
3. Doctor launches encounter → opens a **Follow-up Note** at `/patients/<pid>/clinical/follow-up/<nid>`.
4. **AI Scribe** is the recommended path for follow-ups because:
   - Prior context is rich (lots of previous visits).
   - The note structure is repetitive — Claude is great at it.
   - Coding suggestions get sharper with more history.
5. Sign → Charge capture → Checkout → Submit to clearinghouse — same as Phase 3-4.

### Step 5.3 — Re-Exam (every 6-12 visits or per plan cadence)
- Patient fills NDI / Oswestry / Pain VAS in the waiting room (portal questionnaire link sent the day before).
- Doctor opens a **Re-Exam** at `/patients/<pid>/clinical/re-exams/<rid>`.
- Re-Exam editor surfaces:
  - **Outcomes deltas** — baseline vs. current with inline SVG charts.
  - **Plan progress** — goals met, in-progress, or overdue.
  - **Episode timeline** — every encounter, signed note, and outcome since the start.
- Doctor signs the Re-Exam → an outcome entry auto-emits to the chart.

---

## Phase 6 — Discharge

### Step 6.1 — Goals met
When the treatment plan's discharge criteria are met:

1. Doctor opens the treatment plan and clicks **Discharge episode**.
2. Reason capture (goals met / patient lost to follow-up / referred out / non-compliant).
3. Episode flips to `closed`.
4. Diagnoses resolve (`status=resolved`, `resolved_at=now`).
5. Final outcome scores stamp on the closed episode card.

### Step 6.2 — Final statement
- A final statement is generated showing all charges, payments, adjustments, and any remaining balance.
- Delivered via portal + email.
- If there's a balance, the system optionally enrolls them into a **payment plan** with auto-pay.

### Step 6.3 — Post-discharge engagement
- A **review request** SMS / email lands 1 day post-final-visit.
- 90 days later, a **wellness check-in** SMS reminds them to schedule a maintenance visit.

---

## Phase 7 — Compliance & audit (background)

This runs continuously in the background — no human action required for routine cases — but supports audit and HIPAA verification:

- Every **PHI unmask** logs a break-glass event with reason.
- Every **note sign / addendum** is immutable + audit-logged.
- Every **claim transition** emits a `claim.<event>` row.
- Every **payment** is double-entry on the ledger with full audit.
- The **integrity verifier** runs nightly checking foreign keys, duplicates, and shape conformance.
- The **access review cycle** (Compliance Ops) prompts admins to re-attest active users every 90 days.

When audit time comes:
- `/admin/audit` for the global event stream (filterable).
- `/compliance` Compliance Ops surface for evidence library, incident register, vendor register, risk register, policy library, access reviews.
- Integrity verifier output highlights anomalies for remediation.

---

## End-to-end demo script (10 minutes)

The shortest reproducible path through the entire workflow on the demo seed:

1. **Sign in** as **Front desk** (`staff@ccms.app` / `Staff@ComplianceClinic1`).
   - Confirm Hannah Whitaker is on today's schedule. Click **Check in**.
   - Drag her card to **Roomed** on the flow board.
2. **Sign out**, **sign in** as **Doctor** (`doctor@ccms.app` / `Doctor@ComplianceClinic1`).
   - Click Hannah on My queue → **Launch encounter** → opens a Follow-up Note.
   - In the **AI Scribe** panel, type into the Addendum box:
     > *"Patient reports continued low-back pain, 4/10. ROM L4-L5 limited. Performed adjustment; pain reduced to 2/10. Recommend follow-up in 1 week."*
   - Click **Draft SOAP** → wait 3-5 s.
   - Click **Apply All** → CPT (98941) + ICD (M54.5) suggestions auto-appear.
   - Click **Apply All Codes**.
   - In **Create draft claim**, pick **Aetna** (CHC-routed sandbox payer) → **Create draft claim**.
   - Click **Submit to clearinghouse** → toast: `change_healthcare (sandbox): queued`.
   - Click **Sign** → re-auth with PIN `200002`.
3. Click **Open claim →** to navigate to the claim detail.
   - Watch the **Live timeline** animate — pill goes **Polling → Live** in ~2 s; events appear in ~5 / 10 / 15 / 20 s; status flips to **PAID**.
4. **Sign out**, **sign in** as **Patient** (`patient@ccms.app` / `Patient@ComplianceClinic1`).
   - Open the portal → see the **AI Visit brief** card summarizing today's visit in plain English.
   - Click **Statements** → see the new statement.
   - Click **Pay now** → HelcimPay.js modal (skip in demo).
5. **Sign out**, **sign in** as **Admin** (`admin@ccms.app` / `Admin@ComplianceClinic1`).
   - Open `/billing/ar-aging` → confirm Hannah is no longer outstanding.
   - Open `/admin/audit` → see the trail (`auth.login` × 4, `clinical.note.signed`, `claim.submitted`, `phi.unmasked` × 0, etc.).

That single 10-minute run exercises every major module: portal → kiosk → flow board → AI Scribe → SOAP → coding → fee schedule → send-to-claim → quick-submit → live timeline → ERA simulator → patient portal visit brief → ledger → audit.

---

*Last updated: 2026-05-04. Document lives at `/app/memory/PATIENT_WORKFLOW.md`.*
