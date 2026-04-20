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

## 16. Out of scope / deferred
- `privacy`, `communication`, and `elevation` routers rely on the `tenant_id` backfill but do not yet pass queries through `scoped_filter` — safe because we're still single-tenant-per-user but a P1 to harden before onboarding the second paying tenant.
- Multi-tenant user support (one user across N tenants) — P2; requires `user_tenant_roles` table + tenant-switcher UI.
- Subdomain-based tenant routing (`acme.ccms.app → tid=acme`) — P2; ingress + middleware work.
- Unique-per-tenant `location.code` (currently globally unique sparse) — P2.

## 15. Key reference docs
- `/app/memory/HIPAA_COMPLIANCE.md` — full safeguard inventory (implemented vs. external)
- `/app/memory/AUTHORIZATION_GUIDE.md` — RBAC + scopes + policy overlays (2026-02-20)
- `/app/memory/test_credentials.md` — demo accounts
- `/app/test_reports/iteration_2.json` — testing agent report (24/24)
