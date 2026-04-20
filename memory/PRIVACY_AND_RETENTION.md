# CCMS Privacy & Retention

**Last updated:** 2026-02-18 (Privacy & data-governance phase)
**Complements:** `HIPAA_COMPLIANCE.md` (safeguards), `ACCESS_CONTROL_AND_AUDIT.md` (access + audit), `COMPLIANCE_BASELINE.md` (framework mapping).

> This document describes the **application-layer** privacy workflows. It
> does NOT claim that CCMS is legally CCPA-, GDPR-, or HIPAA-compliant in
> production. That determination belongs to the deploying organisation, its
> Privacy Officer, its lawyers, and an independent auditor.

---

## 1. What was implemented

| Capability | Artefact | Surface |
|---|---|---|
| Data inventory of every data category the app handles | `services/privacy/inventory.py` | `GET /api/privacy/data-inventory` (admin) + `/privacy` admin page, **Data inventory** tab |
| Privacy request intake + workflow (access, delete, correct, restrict, opt-out) | `services/privacy/router.py` + `privacy_requests` collection | `POST/GET/PATCH /api/privacy/requests`, `GET /api/privacy/my-requests`, admin UI at `/privacy` |
| Documented request state model | `ALLOWED_TRANSITIONS` in router + `STATUS_FLOW` in UI | See §3 |
| Consent records + versioned Privacy Notice acceptance | `consent_records` collection, register form checkbox | `POST /api/privacy/consents/accept`, `GET /api/privacy/consents/me`, register page |
| Communication preferences (email / SMS / marketing) | `communication_preferences` collection | `GET/PUT /api/privacy/communication-preferences` |
| Account-data export (self) | `/api/auth/me/export` | Returns profile, prefs, consents, privacy requests, audit event summary |
| Clinical-data export (self or admin) | `/api/patients/{id}/export` (pre-existing) | Full decrypted JSON — profile + records + appointments |
| Soft-delete with 7-year retention clock | `patients.status='deleted'`, `retention_until` | `DELETE /api/patients/{id}` — admin + reauth + reason |
| Legal hold on patient records | `patients.legal_hold` + `legal_hold_reason` | `POST /api/privacy/patients/{id}/legal-hold` (admin + reauth) |
| Legal-hold block on deletion | Re-checked in both `DELETE /patients/{id}` and `fulfill-delete` | Returns 409 Conflict while hold is active |
| Audit trail for every privacy-related action | `core/audit.py` | `auth.password_*`, `privacy.consent_recorded`, `privacy.comm_preferences_updated`, `privacy_request.*`, `patient.legal_hold_updated`, `account.self_exported` |

---

## 2. Data export

### 2.1 Clinical export (PHI)
`GET /api/patients/{id}/export`
- **Who:** admin, or the patient themself (`user_id` link).
- **Contains:** decrypted patient profile, medical records, appointments.
- **Audit:** writes `patient.exported` with `phi_accessed=true` and `metadata={records, appointments}`.
- **Reauth:** not enforced today — flagged as a P1 hardening item (see `COMPLIANCE_BACKLOG.md`); step-up reauth is already required for `DELETE` and record mutation.

### 2.2 Account / identity export (non-PHI)
`GET /api/auth/me/export`
- **Who:** the caller, for themselves.
- **Contains:** account profile whitelist (id, email, name, role, phone, status, MFA flags, password_changed_at, created/updated/last_login), communication preferences, consent history, privacy requests they raised or that target them, and the most recent 100 audit events they were the actor for (action/outcome/created_at/ip only — no entity data).
- **Audit:** writes `account.self_exported` with counts.
- **Security:** whitelists the fields — `password_hash`, `password_history`, `mfa_secret`, `mfa_backup_codes` are never included.

---

## 3. Privacy request workflow

### 3.1 Request types
`export`, `delete`, `correct`, `restrict`, `opt_out` — CCPA-style set. Each is a free-form flag; the **fulfilment path** is the interesting bit.

### 3.2 State machine (`services/privacy/router.py::ALLOWED_TRANSITIONS`)

```
received ──► in_review ──► approved ──► fulfilled
     │           │              │
     │           │              └─► rejected / withdrawn
     │           └─► rejected / withdrawn
     └─► rejected / withdrawn
```

Terminal states (`fulfilled`, `rejected`, `withdrawn`) reject further transitions with HTTP 400. The frontend UI only surfaces legal next states per row. `updated_at` + `closed_at` are stamped at each transition.

### 3.3 Fulfilment paths

| Request type | How it's fulfilled today |
|---|---|
| `export` | Caller or admin runs `GET /api/patients/{id}/export` or `GET /api/auth/me/export`, attaches confirmation to the request (response_notes), transitions to `fulfilled`. |
| `delete` | Admin calls `POST /api/privacy/requests/{id}/fulfill-delete` (requires reauth + no legal hold) → records the link in `fulfillment.linked_patient_id`, then admin completes the actual soft-delete via `DELETE /api/patients/{id}` (independent PHI-aware flow). |
| `correct` | Use `PUT /api/patients/{id}` (admin/staff/self scope applies); admin transitions request to `fulfilled` with response notes. |
| `restrict` | Organisational today — no field-level "do not contact" toggle beyond `communication_preferences`. Deploy-time extension. |
| `opt_out` | Recorded as a withdrawn consent via `POST /api/privacy/consents/accept` with `action="withdrawn"`, plus `PUT /api/privacy/communication-preferences` to flip `marketing_opt_in=false`. |

### 3.4 Non-PHI storage rule
`notes` and `response_notes` are **not** encrypted at rest and **must not** contain PHI. This is a policy + training commitment enforced by convention — the admin UI disclaimer calls this out loudly.

---

## 4. Deletion & retention model

### 4.1 Soft-delete (default)
- `DELETE /api/patients/{id}` sets `status="deleted"`, `deleted_at`, `deleted_by`, `retention_until = now + 7 years`. Requires admin + reauth + ≥ 8-char reason + no `legal_hold`.
- No endpoint in the application performs a destructive delete on patient data. Physical purge is reserved for the retention worker (P0.1 in the backlog) which runs *after* `retention_until` and *only* when `legal_hold == false`.

### 4.2 Legal / medical-retention hold
- `POST /api/privacy/patients/{id}/legal-hold` — admin + reauth. Body: `{hold: bool, reason: string}`.
- While `legal_hold == true`:
  - `DELETE /api/patients/{id}` → 409 Conflict.
  - Privacy-request `fulfill-delete` → 409 Conflict.
  - Retention worker (future) must skip the row.
- Auditor evidence: `patient.legal_hold_updated` rows + the current patient document.

### 4.3 Retention durations (defaults — **configure per deployment**)

| Category | Default | Source of truth in production |
|---|---|---|
| Patient records (post soft-delete) | 7 years | `services/patient/router.py::RETENTION_YEARS` (hardcoded for MVP; must be env-driven or policy-driven before deploy). US state medical boards vary: 6–10 years for adults, majority-age + N for minors. |
| Medical records | Same as patient | Jurisdiction-dependent (see above). For EU/GDPR, align with HIPAA-equivalent local law. |
| Audit log | 7 years (policy) | TTL not yet applied at the DB layer. |
| Notifications / comms log | 2 years (default) | Not enforced today. |
| Consent records | Lifetime of account + audit retention | Indefinite until closure. |
| Privacy requests | Follows audit | No TTL yet. |

**Config placeholders (env / per-tenant policy):**
- `RETENTION_YEARS` — patient post-delete retention (current default 7)
- `RETENTION_AUDIT_YEARS` — audit log retention (future worker)
- `RETENTION_NOTIFICATIONS_YEARS` — comms log retention (future worker)

None of these should be hardcoded when the app ships. A production deployment should load them from:
1. A tenant-scoped settings document (multi-tenant deployments), or
2. Environment variables + a documented organisational Retention Policy, or
3. A regional policy bundle in `core/retention.py` for multi-region deployments.

### 4.4 Right-to-delete (CCPA §1798.105) handling
CCPA grants consumers the right to request deletion, but exempts records the business is **legally required to retain** — medical records typically fall into this category. The application's behaviour:
1. Accept the request (privacy_requests row).
2. Admin reviews with the Privacy Officer + Legal.
3. If legal retention applies, mark `status="rejected"` with `response_notes` explaining the exemption, or transition to `approved → fulfilled` with a soft-delete that preserves the 7-year retention clock (so clinical/legal obligations survive the user-visible deletion).
4. The clinic's Privacy Notice must disclose this limitation.

---

## 5. Consent & communication preferences

### 5.1 Consent records
- Every acceptance or withdrawal is a new row in `consent_records` (append-only — we never mutate past rows). Fields: `policy_type, policy_version, action, accepted_at, ip, user_agent`.
- Register page captures `privacy_notice` v`2026-02-v1` on account creation. Bumping the version re-prompts existing users on next login (to be implemented as a UI check against the latest consent record).

### 5.2 Communication preferences
- `email_opt_in` (default true, transactional only — always on for legally-required messages like appointment confirmations),
- `sms_opt_in` (default false),
- `marketing_opt_in` (default false).
- Any change writes `privacy.comm_preferences_updated` to the audit log.

---

## 6. CCPA-style readiness mapping

| CCPA obligation | In-app support |
|---|---|
| §1798.100 right to know | `/api/auth/me/export` + `/api/patients/{id}/export` + `/api/privacy/data-inventory` |
| §1798.105 right to delete | Soft-delete + privacy-request `delete` + legal-hold exemption |
| §1798.106 right to correct | `PUT /api/patients/{id}` + privacy-request `correct` |
| §1798.110 categories disclosure | Data inventory endpoint + rendered on `/privacy` Data inventory tab |
| §1798.120 opt-out of sale | App does not sell data; opt-out surfaced as `privacy.request_type="opt_out"` + `marketing_opt_in=false` |
| §1798.125 non-discrimination | No feature gating based on rights invocation |
| Verifiable consumer request | Authenticated patients raise via `/api/privacy/requests`; non-authenticated requests must be verified by admin through the intake form (role + identity check documented as operational) |

### What still depends on legal / policy / operations (out of app scope)
- Public-facing Privacy Notice copy (legal + content team)
- 45-day response SLA tracking & communication (SLA timers can be added easily; actual responses handled out-of-app)
- Subprocessor register + DPAs / BAAs (vendor management)
- Verification channels for unauthenticated CCPA requests
- Privacy-impact assessments (DPIA / PIA) per new data flow
- Regional data-residency requirements (multi-region infra)
- Executed retention policy document approved by leadership

---

## 7. Audit evidence checklist

| Question | Query |
|---|---|
| Who consented when to the privacy notice? | `consent_records.find({policy_type:"privacy_notice"})` or `/audit-log?action=privacy.consent_recorded` |
| Show every privacy request this quarter | `/privacy` admin page → status/type filter → OR `/api/privacy/requests` |
| Prove we can produce a user's full data | Run `GET /api/auth/me/export` + `GET /api/patients/{id}/export`, inspect JSON |
| Prove we can honour a delete request | Privacy request `delete` → fulfill-delete + `DELETE /api/patients/{id}` → audit rows `privacy_request.fulfilled` and `patient.soft_deleted` |
| Prove legal hold blocks deletion | Set hold → attempt delete → 409 Conflict → audit row `patient.legal_hold_updated` |
| Data categories disclosure | `GET /api/privacy/data-inventory` (admin) |

---

## 8. Known limitations

- Retention worker that physically purges post-retention records is not yet implemented (P0.1 backlog).
- `notes` / `response_notes` on `privacy_requests` are unencrypted; policy requires no PHI in these fields.
- Privacy Notice version bump does not yet auto-prompt existing users — needs a tiny UI check in `AuthContext` against the latest consent record.
- Request SLA timers (e.g., 45-day CCPA clock) are not surfaced; trivially addable later.
- Communication preferences are honoured by the UI model only; the notification subscriber still mock-sends regardless. Will be honoured when real SMS/Email integration lands.
