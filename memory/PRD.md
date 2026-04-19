# CCMS — Product Requirements & Architecture Notes

**Last updated:** 2026-02-18 (Compliance foundation baseline)

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
### Phase 1 (2026-04-19)
- Identity, Patient CRUD, Scheduling with conflict detection, mock notifications via in-process event bus
- Sage + stone medical theme, 7 role-aware pages

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

## 11. Key reference docs
- `/app/memory/HIPAA_COMPLIANCE.md` — full safeguard inventory (implemented vs. external)
- `/app/memory/test_credentials.md` — demo accounts
- `/app/test_reports/iteration_2.json` — testing agent report (24/24)
