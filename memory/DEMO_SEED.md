# Demo Clinic Seed — Riverbend Chiropractic & Wellness

This document describes the realistic demo dataset that ships with
CCMS. It replaces the previous placeholder seed data (“Default
Practice”, “System Admin”, “Morgan Lee”) with a believable fictional
chiropractic clinic so the product looks lived-in the moment a demo
user logs in.

> **Guardrail**: every name, address, phone number, policy number,
> employer, adjuster, and clinical detail on this page is fictional.
> No real PHI or real identifiable person data is used. Phone numbers
> use the NANP 555-01xx fiction block.

---

## 1. Where demo/seed data lives

| File | Responsibility |
|------|---------------|
| `services/tenancy/seed.py` | Tenant + location upsert. Default tenant is **Riverbend Chiropractic & Wellness** at **Riverbend — Downtown** (America/Los_Angeles). Also owns the multi-location **Sunrise Chiro Group** demo tenant used by tenancy tests. |
| `services/identity/seed.py` | Login-helper demo accounts (admin / doctor / staff / patient) + the seeded demo patient record (Ethan Parker). Also writes `/app/memory/test_credentials.md`. |
| `services/authz/seed.py` | Role catalog, permission matrix, grant groups. Not clinic-specific. |
| `services/compliance_ops/seed.py` | Policy / control / audit record seeds. Not clinic-specific. |
| `services/billing/seed.py` | CPT / modifier catalog defaults. Not clinic-specific. |
| **`services/demo/seed.py`** | **NEW** — realistic Riverbend clinic dataset: staff roster, payers, patient personas, insurance policies, clinical notes, a one-week appointment board. |
| `scripts/reseed_demo_clinic.py` | Destructive reset for the Riverbend tenant. Wipes test-run pollution then re-runs `seed_demo_clinic()`. Never touches Sunrise or the platform admin. |

All seeders are **idempotent** — safe to re-run on every backend boot.
They upsert on stable business keys (email, payer_code, patient
first_name+last_name+dob, appointment start_time) and refresh fields
in place rather than creating duplicates.

---

## 2. Login-helper demo accounts

These four accounts power the “Demo clinic sign-in” panel on the
login page. Emails + passwords are stable so tests and docs don’t
break. The underlying identities are now realistic Riverbend staff:

| Role label      | Person                 | Email                | Password                     |
|-----------------|------------------------|----------------------|------------------------------|
| Administrator   | **Ava Bennett** (Clinic Administrator) | `admin@ccms.app`   | `Admin@ComplianceClinic1`   |
| Chiropractor    | **Dr. Noah Carter, DC** (Lead Chiropractor, NPI `1841792253`) | `doctor@ccms.app`  | `Doctor@ComplianceClinic1`  |
| Front desk      | **Mia Ramirez** (Front Desk Coordinator) | `staff@ccms.app`   | `Staff@ComplianceClinic1`   |
| Patient portal  | **Ethan Parker** (active-adult wellness / maintenance persona) | `patient@ccms.app` | `Patient@ComplianceClinic1` |

The platform admin (cross-tenant operations) is:

| Person                   | Email                         | Password                      |
|--------------------------|-------------------------------|-------------------------------|
| **Owen Sinclair** (Operations Lead) | `platform-admin@ccms.app` | `Platform@ComplianceClinic1` |

---

## 3. Riverbend staff roster (beyond the login helpers)

Seeded by `services/demo/seed.py` — all share the demo password
`Riverbend@ComplianceClinic1`.

| Email                                   | Person             | Role   | Title                    | NPI        |
|-----------------------------------------|--------------------|--------|--------------------------|------------|
| `olivia.hart@riverbend-chiro.app`       | Olivia Hart        | admin  | Clinic Owner (MBA)       |            |
| `dr.samuel.ito@riverbend-chiro.app`     | Dr. Samuel Ito, DC | doctor | Associate Chiropractor   | 1730598210 |
| `lena.brooks@riverbend-chiro.app`       | Lena Brooks        | staff  | Office Manager           |            |
| `tomas.rivera@riverbend-chiro.app`      | Tomás Rivera       | staff  | Billing Specialist       |            |
| `priya.shah@riverbend-chiro.app`        | Priya Shah         | staff  | Chiropractic Assistant   |            |

---

## 4. Patient personas

Each persona drives coherent downstream demographics, insurance, a
clinical note, and appointments. This is the catalog you can pitch
from during a product demo.

| Persona slug         | Patient          | Scenario |
|----------------------|------------------|----------|
| `self_pay_wellness`  | **Ethan Parker** (he/him, 34) | Active-adult wellness / maintenance. Self-pay. Returns every 4–6 weeks. Shows the “light-touch” portal path. |
| `acute_neck_pain`    | **Hannah Whitaker** (she/her, 34) | New patient, acute C5-C6 facet sprain post-hotel-pillow. Commercial **Cascade Blue Shield** ($30 copay, $1,500 deductible). 6-visit plan. |
| `chronic_lbp_medicare` | **Marcus Reid** (he/him, 67) | Chronic lumbar strain, subluxation complex L4-L5. **Medicare — Oregon** primary (AT modifier, subluxation-primary, initial-treatment-date all required). Active-treatment episode re-opened today. |
| `auto_accident_pip`  | **Isabella “Bella” Cho** (she/her, 41) | Rear-end MVA 6 days ago. Cervical + thoracic strain. **Northwest Auto PIP** (adjuster Angela Price). Attorney-referred. 6-week course. |
| `workers_comp`       | **Derrick Stone** (he/him, 53) | Warehouse lifting injury 3 days ago. Lumbar strain + SI joint. **Oregon SAIF Workers’ Comp** (adjuster Greg Fuentes). Modified-duty note issued. |
| `athlete`            | **Aria Johnson** (she/her, 29) | Marathon-training IT band syndrome with pelvic asymmetry. **PacificCare Commercial** ($25 copay, $1,000 deductible). 2x/week x 3 weeks + home program. |
| `family_head`        | **Claire Morgan** (she/her, 39) | Thoracic deconditioning / desk+kids pattern. **PacificCare Commercial** (subscriber + guarantor for Jaxon below). |
| `minor_dependent`    | **Jaxon “Jax” Morgan** (he/him, 11) | Mild thoracic strain from new gymnastics class. Dependent on Claire’s policy. Demonstrates guardian/guarantor workflow. |

Every persona has:
* complete demographics (preferred name, middle name when relevant,
  pronouns, marital status, language, occupation, employer + phone,
  referral source, multi-line address, emergency contact)
* a primary insurance policy keyed to the right payer
* a clinical note structured as Chief Complaint / Subjective /
  Objective / Assessment / Plan
* one or more appointments in the rolling one-week calendar

---

## 5. Payer catalog

Six payer rows seeded on the Riverbend tenant — covers every pricing
rail the app supports:

| Code      | Payer                          | Type         | Submission           | Enrollment   |
|-----------|--------------------------------|--------------|----------------------|--------------|
| `PAC-COMM`| PacificCare Commercial         | commercial   | EDI via Change HC    | enrolled     |
| `CBS-COMM`| Cascade Blue Shield            | commercial   | EDI via Change HC    | enrolled     |
| `MCR-OR`  | Medicare — Oregon              | medicare     | EDI via Change HC    | enrolled (AT + sublux-primary + initial-treatment required) |
| `SAIF-WC` | Oregon SAIF Workers’ Comp      | workers_comp | portal               | not started  |
| `NWA-PIP` | Northwest Auto PIP             | auto         | portal               | not started  |
| `SELF`    | Self-Pay                       | self_pay     | portal               | n/a          |

---

## 6. Appointment board

Thirteen appointments distributed around “today”:

| When        | Patient           | Visit                                 | Status      | Provider         |
|-------------|-------------------|---------------------------------------|-------------|------------------|
| day −3 08:00| Aria Johnson      | Canceled — schedule conflict          | cancelled   | Dr. Samuel Ito   |
| day −2 17:00| Ethan Parker      | Maintenance adjustment                | completed   | Dr. Noah Carter  |
| day −1 09:00| Marcus Reid       | Medicare initial exam (active tx)     | completed   | Dr. Noah Carter  |
| today 10:00 | Hannah Whitaker   | New patient — acute neck pain         | scheduled   | Dr. Noah Carter  |
| today 11:00 | Isabella Cho      | PIP follow-up adjustment              | scheduled   | Dr. Samuel Ito   |
| today 14:00 | Derrick Stone     | Workers’ comp visit 2                 | scheduled   | Dr. Noah Carter  |
| day +1 08:00| Aria Johnson      | IT band follow-up + IASTM             | scheduled   | Dr. Samuel Ito   |
| day +2 09:00| Marcus Reid       | Active-treatment visit 3              | scheduled   | Dr. Noah Carter  |
| day +2 17:00| Claire Morgan     | Thoracic adjustment                   | scheduled   | Dr. Noah Carter  |
| day +2 17:00| Jaxon Morgan      | Pediatric thoracic check              | scheduled   | Dr. Samuel Ito   |
| day +3 10:00| Hannah Whitaker   | Neck pain follow-up                   | scheduled   | Dr. Noah Carter  |
| day +3 11:00| Isabella Cho      | PIP adjustment + manual therapy       | scheduled   | Dr. Samuel Ito   |
| day +4 09:00| Marcus Reid       | Re-exam at visit 6                    | scheduled   | Dr. Noah Carter  |

Appointments upsert on `(tenant_id, patient_id, start_time)` — safe
to re-run.

---

## 7. How to reseed / reset

* **Automatic**: `server.py` runs `seed_demo_clinic()` on every
  backend boot. If the Riverbend tenant already has the personas,
  nothing changes; fields are refreshed in place.
* **Manual full reset** (recommended before any demo after a long
  development period that accumulated test-run pollution):
  ```bash
  cd /app/backend && python scripts/reseed_demo_clinic.py
  ```
  This:
  1. Deletes every tenant-scoped row on the Riverbend tenant across
     clinical, scheduling, billing, and communication collections.
  2. Deletes the Riverbend staff users (login-helper demo users are
     NOT deleted — they belong to `identity/seed.py`).
  3. Re-runs `seed_demo_clinic()` to rebuild everything.
  The script is destructive but scoped to the Riverbend tenant only.
  Sunrise Chiro Group and the platform admin are untouched.

---

## 8. Known limitations / future enhancements

* Claims + statements + remittance rows are NOT seeded as part of the
  demo catalog. Build them by walking the normal billing UI once
  logged in as Dr. Noah Carter or Ava Bennett. This keeps the demo
  data small and the financial state deterministic.
* No X-rays / document uploads in the seed (would require synthetic
  images + S3 storage). Add on demand if visual attachments become a
  demo story.
* The Sunrise Chiro Group tenant still carries its original test-run
  sample patients (Avery Bennett, Sam Calder, Drew Patel). That tenant
  is test-focused and we keep it stable for test determinism rather
  than polishing it.
* Seeded passwords for the Riverbend staff roster
  (`Riverbend@ComplianceClinic1`) are **not** surfaced on the login
  page — only the four role-based login-helper accounts are. Use this
  doc if you need to log in as e.g. Tomás Rivera for a billing-focused
  demo.

---

## 9. Roadmap toward a “gold demo clinic”

Candidate extensions, in priority order:

1. **Seed a curated set of submitted + paid claims** covering each
   payer rail (commercial / Medicare AT-modifier / WC / PIP) so the
   billing dashboards come alive on day-one demos.
2. **Seed realistic remittance postings** (ERA 835 samples) so the
   AR aging report renders real numbers.
3. **Seed one re-exam clinical note per active-treatment persona**
   so the timeline view shows progress over multiple visits.
4. **Seed a handful of communication rows** (appointment reminders,
   review requests) so the notifications panel is non-empty.
5. **Add a second Riverbend location** (e.g. “Riverbend — Eastside”)
   and spread appointments across both to demonstrate multi-location
   scheduling.
