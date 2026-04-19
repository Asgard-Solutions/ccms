# CCMS Access Control & Audit Evidence

**Last updated:** 2026-02-18 (security hardening phase)
**Scope:** this document is the engineering reference for access control, session handling, MFA, password policy, and audit evidence surfaces in CCMS. It complements (does not replace) `HIPAA_COMPLIANCE.md`, `COMPLIANCE_BASELINE.md` and `CONTROL_INVENTORY.md`.

---

## 0. Why this document

Frameworks that expect clear, demonstrable access + audit controls:

- **SOC 2** — Common Criteria CC6 (logical access), CC7 (monitoring)
- **ISO/IEC 27001:2022** — A.5.15/A.5.17/A.8.2/A.8.3/A.8.5/A.8.15/A.8.16
- **HIPAA** — 45 CFR §164.312(a), (b), (d) — already documented in `HIPAA_COMPLIANCE.md`

Rather than duplicate the per-framework tables, this doc explains **what is implemented, how to test it, and where to harvest evidence from the live app.**

---

## 1. Access control hardening

### 1.1 RBAC enforcement
- Central guard: `core/deps.py::require_role(*roles)` + `get_current_user`
- Every PHI-touching endpoint wraps `Depends(require_role(...))`. Grep: `rg "require_role\("` in `services/` returns the authoritative list.
- Role set: `admin`, `doctor`, `staff`, `patient` (`Literal` type in `services/identity/models.py::Role`).

### 1.2 Admin-only functions
All admin endpoints carry `require_role("admin")`:
- `GET/POST /api/auth/users`, `PATCH /api/auth/users/{id}`, `POST /api/auth/users/{id}/disable`, `/enable`
- `POST /api/auth/users/{id}/mfa/reset` — admin MFA recovery (new)
- `POST /api/auth/users/{id}/mfa/require?required=bool` — admin MFA policy toggle (new)
- `GET /api/audit-logs`, `GET /api/audit-logs/export.csv` — audit view + export
- `GET /api/perf/*`, `GET /api/compliance/overview`

### 1.3 Patient self-access boundaries
- `services/patient/router.py`: when `user.role == "patient"`, queries are scoped by `q["user_id"] = user["id"]`. A patient cannot request another patient's detail, records, export, or appointments.
- Break-glass is irrelevant for patients (they do not have cross-scope read rights).

### 1.4 Sensitive-action re-authentication
- `core/reauth.py::create_reauth_token` — 5-minute password-proof cookie.
- Required for: `DELETE /api/patients/{id}`, `POST /api/patients/{id}/records`.
- The frontend shows a reauth dialog (`components/ReauthDialog.jsx`), then retries with the cookie.

### 1.5 Forced logout on role change / account disable (new)
- `core/security.py::create_access_token` now carries two new claims:
  - `epoch` — per-user monotonically increasing integer (`users.session_epoch`)
  - `sst` — ISO-8601 session start timestamp (absolute lifetime cap)
- `core/deps.py::get_current_user` rejects any access token whose `epoch` does not match the current `users.session_epoch` → immediate logout.
- `users.session_epoch` is **incremented** when:
  - password is changed (`POST /api/auth/change-password`)
  - password is reset via token (`POST /api/auth/password-reset/confirm`)
  - MFA is enabled / disabled by self
  - admin resets MFA on the user (`/users/{id}/mfa/reset`)
  - admin disables the user (`/users/{id}/disable`)
  - admin changes the user's `role` or `status` (`PATCH /users/{id}`)
- For the initiating user's current session we re-issue fresh cookies so they stay signed in here while every other device is kicked out.
- Refresh token also carries `epoch` + `sst` → `/auth/refresh` rejects stale epochs.

### 1.6 Disabled-user behaviour
- `users.status = "disabled"` is checked on both `/auth/me` and `/auth/refresh`. Login with a disabled account returns 403 and writes an `auth.login` failure row with reason `account_disabled`.
- Disabling also bumps `session_epoch`, so *any* in-flight tokens die even before the 60-min access-token expiry.

### 1.7 Least-privilege defaults
- Admin-created users default to `role=staff` (`AdminUserCreate.role` default).
- `mfa_policy_required` is `True` by default for `admin/doctor/staff` created via the admin UI.
- Patient self-registration always produces `role=patient`, `mfa_policy_required=False`.

---

## 2. Session security

### 2.1 Idle timeout
- Frontend: `contexts/AuthContext.jsx` — 15-minute idle timer with a 14-minute warning dialog (`idle-warning` test id).

### 2.2 Absolute session lifetime (new)
- 12-hour hard cap from first login, enforced by `core/deps.py` using the `sst` claim.
- Survives `/auth/refresh` — refresh reuses the original `sst` so an attacker cannot rotate past the cap.

### 2.3 Secure cookie settings
- Every auth cookie: `HttpOnly=True`, `Secure=True`, `SameSite=None`, `Path=/`. Set via `_cookie_kwargs` in `services/identity/router.py`.

### 2.4 Session revocation on password change / privilege change (new)
- Password change + password reset bump `session_epoch`. All previously-issued tokens are invalidated.
- Role / status / MFA changes bump `session_epoch` (see §1.5). Evidence surfaces in audit rows as `metadata.sessions_revoked=true` or `metadata.other_sessions_revoked=true`.

### 2.5 Visible session / audit records for sign-in events (new)
- `GET /api/auth/sessions` returns the current user's recent `auth.login`, `auth.mfa_verified`, `auth.logout` events (IP, UA, outcome, timestamp) pulled from the audit log.
- Surfaced in the frontend at `/security` under the **Recent sign-ins** card.

### 2.6 Suspicious auth event logging
- Every authentication attempt writes an audit row. Failures carry `outcome="failure"` + a structured `reason`:
  - `invalid_credentials`, `account_disabled`, `password_expired`, `bad_code`, `wrong_password`, `wrong_current_password`, `invalid_or_used_token`, `token_expired`, `unknown_email_or_disabled`
- Query via admin UI:
  `/audit-log` → filter "Authentication" → search `failure`, or
  `GET /api/audit-logs?action=auth&outcome=failure`

---

## 3. MFA foundation

### 3.1 Enrolment model
- `users.mfa_enabled`, `users.mfa_secret`, `users.mfa_backup_codes`, `users.mfa_pending_secret`, `users.mfa_pending_backup`.
- Policy flag `users.mfa_policy_required` — admin-settable per user. If set and the user has not enrolled, the frontend should drive them to `/security` to complete MFA setup.

### 3.2 TOTP flow
- `POST /api/auth/mfa/setup` → returns TOTP secret + otpauth URL + 8 single-use backup codes. Writes `auth.mfa_setup_started` audit.
- `POST /api/auth/mfa/verify` → confirms a live TOTP code, flips `mfa_enabled=True`, bumps `session_epoch`. Writes `auth.mfa_enabled` audit.
- `POST /api/auth/mfa/challenge` → post-login challenge using the short-lived `mfa_ticket`, accepts TOTP or backup code; backup codes are consumed (pulled from the list) on use.

### 3.3 Admin-enforceable MFA policy (new)
- `POST /api/auth/users/{id}/mfa/require?required=true|false` — admin toggles `mfa_policy_required`. Writes `user.mfa_policy_updated` audit.

### 3.4 Documented admin recovery / reset flow (new)
- `POST /api/auth/users/{id}/mfa/reset` — admin only:
  1. Sets `mfa_enabled=False`
  2. Clears `mfa_secret`, `mfa_backup_codes`, any pending enrolment
  3. Bumps target `session_epoch` → all target sessions die
  4. Writes a `user.mfa_reset` audit with `metadata.target_email` and `metadata.sessions_revoked=true`
- On next login the user will either re-enrol (if policy is required) or sign in without MFA.

---

## 4. Password + auth policy

### 4.1 Complexity & history
- `core/password_policy.py::validate_strength` — ≥12 chars, upper + lower + digit + symbol, common-password denylist.
- `reject_password_reuse` — rejects any of the last 5 bcrypt hashes.
- Applied at: register, admin create, change-password, password-reset confirm.

### 4.2 Rotation
- 90-day warning surfaced via `password_expiry_status(user.password_changed_at)` — returned on login as `password_rotation_due`.
- 120-day hard expiry blocks login with `password_expired` audit until a reset is performed.

### 4.3 Login lockout / rate limiting
- Per-email durable lockout: 5 failed attempts → 15-minute block (`login_attempts` collection).
- Per-IP sliding window: 30 login attempts / 60 s (`core/rate_limit.py`, Redis-backed with local fallback).
- Password-reset request rate-limited per IP: 5 / 60 s.

### 4.4 Password reset (new)
- `POST /api/auth/password-reset/request` — public. Always responds 200 to prevent enumeration. For known emails, issues a single-use URL-safe token (32 bytes), stored as `sha256(token)` in `password_reset_tokens` with `expires_at = now + 15 min`. TTL-indexed for automatic purge.
- `POST /api/auth/password-reset/confirm` — consumes the token, enforces complexity + history, bumps `session_epoch`, invalidates any sibling outstanding reset tokens, and clears the lockout counter.
- Dev builds return the raw token in the response body (`dev_token`) so the flow can be exercised without email. In production this field **must be stripped** and delivery routed through an email integration with a signed BAA.

### 4.5 Explicit audit logs for auth-sensitive events
All produce `audit_logs` rows (see §5).

---

## 5. Audit evidence

### 5.1 Event catalogue

| Action | Trigger | outcome | phi_accessed |
|---|---|---|---|
| `auth.registered` | self-register | success | false |
| `auth.login` | login (post-verify) | success / failure+reason | false |
| `auth.mfa_challenge_issued` | MFA required after step-1 | success | false |
| `auth.mfa_verified` | correct MFA code / backup | success | false |
| `auth.mfa_verify` | wrong code | failure (`bad_code`) | false |
| `auth.logout` | logout | success | false |
| `auth.password_changed` | self change | success | false |
| `auth.password_change` | wrong current password | failure (`wrong_current_password`) | false |
| `auth.password_reset_requested` | reset request — known email | success | false |
| `auth.password_reset_requested` | reset request — unknown email | failure (`unknown_email_or_disabled`) | false |
| `auth.password_reset_completed` | reset confirmed | success | false |
| `auth.password_reset_confirm` | bad/expired token | failure (`invalid_or_used_token`, `token_expired`) | false |
| `auth.reauth` | step-up verified | success / failure(`wrong_password`) | false |
| `auth.mfa_setup_started` | /mfa/setup | success | false |
| `auth.mfa_enabled` | /mfa/verify success | success | false |
| `auth.mfa_disabled` | self disable | success | false |
| `auth.mfa_enable` | /mfa/verify bad code | failure (`bad_code`) | false |
| `user.created` | admin create | success | false |
| `user.disabled` | admin disable | success (+metadata.sessions_revoked) | false |
| `user.enabled` | admin enable | success | false |
| `user.updated` | admin patch role/status | success (+metadata.sessions_revoked) | false |
| `user.mfa_reset` | admin MFA reset | success (+metadata.target_email, sessions_revoked) | false |
| `user.mfa_policy_updated` | admin require-MFA toggle | success | false |
| `user.list_viewed` | admin lists users | success | false |
| `patient.*` | patient CRUD + exports | varies | true on read/export |
| `patient.unmasked` | admin unmask | success | true |
| `patient.exported` | export JSON | success | true |
| `patient.created|updated|deleted` | mutations | success | false / true on delete |
| `medical_record.created|accessed` | records | success | true |
| `appointment.*` | scheduling mutations | success | false |
| `audit_log.viewed` | admin reads audit log | success | false |
| `audit_log.exported` | admin CSV export | success (+metadata.rows_exported) | false |

### 5.2 Fields captured per row

Every row carries: `id, action, actor_id, actor_email, actor_role, entity_type, entity_id, reason, metadata, outcome, phi_accessed, ip, user_agent, created_at`. IP uses the leftmost `X-Forwarded-For` value (k8s-ingress-safe).

### 5.3 Sensitive-value protection in logs
- Audit rows never contain raw PHI. `metadata` is engineered at each call-site to hold only identifiers + enums + counts.
- Break-glass reasons are free-text but the router enforces `len >= 8`. Staff are trained not to include patient names — documented in the HIPAA policy.
- Password values are never written (even in failure paths — only the reason code is).
- Reset-token raw values are never stored. Only `sha256(token)`.

---

## 6. Admin-only audit UI

### 6.1 Primary view: `/audit-log` (admin)
- **Quick filters:** All / PHI access / Authentication / Patient changes / Break-glass / User admin
- **Advanced filters:** actor email (regex-insensitive), entity id, date-from (UTC datetime), date-to (UTC datetime), row limit (50/100/200/500)
- **Search:** client-side full-text over action / actor_email / entity_id / reason
- **CSV export:** admin-only. Uses the same filter set + expanded limit (up to 50 k). Writes an `audit_log.exported` meta-row.

### 6.2 Secondary views
- `/security` — self-service **Recent sign-ins** card (see §2.5)
- `/compliance` — readiness snapshot with audit-activity counters (24 h / 30 d / PHI / break-glass / failed-logins)

---

## 7. Framework support

| Control need | Implementation | Artefact |
|---|---|---|
| Enforce unique identity + RBAC | `core/deps.py`, per-router role guards | grep `require_role\(` |
| Session lifecycle + revocation | `session_epoch` + `sst` | audit rows `sessions_revoked=true` |
| MFA with admin recovery | `POST /users/{id}/mfa/reset`, `/mfa/require` | audit rows `user.mfa_*` |
| Password hygiene + rotation | `core/password_policy.py` | audit rows `auth.password_*` |
| Secure password recovery | `password_reset_tokens` (sha256, TTL) | audit rows `auth.password_reset_*` |
| Auditable sign-in history | `GET /api/auth/sessions` + `/audit-log` | Security page + Audit page |
| Evidence export for auditors | `GET /api/audit-logs/export.csv` | CSV + `audit_log.exported` |
| Metrics for SLO / incident detection | `core/metrics.py`, `/api/metrics` | Prometheus scrape |

### SOC 2 CC6 / CC7 support
- Unique ID, RBAC, MFA, lockout, audit log, monitoring metrics: **Implemented**
- Access review, alerting pipeline, tamper-proof storage of audit: **Out of app scope / Partial** (see `COMPLIANCE_BACKLOG.md`)

### ISO 27001 A.5 / A.8 support
- A.5.15 Access control, A.5.16 Identity, A.5.17 Authentication info, A.8.3 Information access restriction, A.8.5 Secure authentication, A.8.11 Data masking, A.8.15 Logging: **Implemented**
- A.8.10 Information deletion (retention worker), A.8.16 Monitoring alerts, A.8.24 KMS-backed crypto: **Partial / Missing** (see backlog)

---

## 8. What still depends on infra / policy

| Control | Responsibility | Notes |
|---|---|---|
| TLS termination + cipher policy | Ingress | `SameSite=None` + `Secure` already set app-side |
| WAF, DDoS, bot management | Ingress | App provides basic per-IP rate-limiting only |
| KMS-backed encryption keys + rotation | Infra / Security | App reads `DATA_ENCRYPTION_KEY` env var today |
| Email delivery for password reset | Communication integration | Tokens are generated + audited; email send is external |
| Append-only / WORM audit storage | Infra | App writes audit log immutably from the code path; the DB layer does not yet block mutation |
| Periodic access reviews | Security Officer | Surface: `GET /api/auth/users?include_disabled=true` + audit |
| Identity lifecycle (HRIS webhooks) | IT / HR | Current flow is manual admin action |
| Alerting on suspicious auth (failed-login spikes, new IP) | DevOps / SecOps | Metrics exposed at `/api/metrics`; alerting rules live in infra |
| BAAs / DPAs with vendors (e.g., email) | Legal | Required before real email delivery is wired |

---

## 9. Where to collect evidence from the live system

| Auditor question | API / UI surface |
|---|---|
| “Show me every privileged action by Dr. X for September” | `/audit-log` → actor_email=`drx@`, date_from/to → Export CSV |
| “Show me every PHI access and who did it” | `/audit-log` → PHI filter → CSV |
| “Prove sessions are revoked on password change” | `/audit-log` → action starts with `auth.password_changed` → row has `metadata.other_sessions_revoked=true` |
| “Prove role changes invalidate tokens” | `/audit-log` → action `user.updated` → `metadata.sessions_revoked=true` |
| “Prove MFA can be enforced centrally” | `/audit-log` → action `user.mfa_policy_updated` |
| “Prove password reset is rate-limited + single-use” | Manual run of the flow; `COMPLIANCE_BASELINE.md` §5 references |
| “Prove monitoring exists” | `GET /api/metrics`, `GET /api/perf/stats`, `GET /api/compliance/overview` |
| “Prove a user's own sign-in history” | Sign in as the user → `/security` → Recent sign-ins |

---

## 10. Test checklist (quick verification)

```bash
BASE=$REACT_APP_BACKEND_URL

# 1. Admin disable → old session dies
curl -c admin.txt -X POST "$BASE/api/auth/login" -d '{"email":"admin@ccms.app",...}'
curl -c user.txt  -X POST "$BASE/api/auth/login" -d '{"email":"staff@ccms.app",...}'
curl -b admin.txt -X POST "$BASE/api/auth/users/<id>/disable"
curl -b user.txt "$BASE/api/auth/me"         # → 403 account_disabled
curl -b admin.txt -X POST "$BASE/api/auth/users/<id>/enable"
curl -b user.txt "$BASE/api/auth/me"         # → 401 epoch mismatch

# 2. Password reset — single-use
TOKEN=$(curl -X POST "$BASE/api/auth/password-reset/request" -d '{"email":"staff@ccms.app"}' | jq -r .dev_token)
curl -X POST "$BASE/api/auth/password-reset/confirm" -d "{\"token\":\"$TOKEN\",\"new_password\":\"NewP@ss_Str0ng_88\"}"  # 200
curl -X POST "$BASE/api/auth/password-reset/confirm" -d "{\"token\":\"$TOKEN\",\"new_password\":\"NewP@ss_Str0ng_99\"}"  # 400

# 3. Admin MFA reset
curl -b admin.txt -X POST "$BASE/api/auth/users/<id>/mfa/reset"  # 200
curl -b admin.txt -X POST "$BASE/api/auth/users/<id>/mfa/require?required=true"  # 200

# 4. CSV export (admin)
curl -b admin.txt -o audit.csv "$BASE/api/audit-logs/export.csv?action=auth&limit=5000"

# 5. Recent sign-ins (self)
curl -b user.txt "$BASE/api/auth/sessions?limit=10"
```

Every one of these flows emits at least one audit row.
