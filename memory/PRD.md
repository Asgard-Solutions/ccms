# CCMS — Product Requirements & Architecture Notes

**Last updated:** 2026-04-20 (Chiro Software theme system adopted)

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
  the new brand without a file-by-file rewrite. Phase 2 will migrate
  those class names to semantic utilities.

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

## 15. Key reference docs
- `/app/memory/HIPAA_COMPLIANCE.md` — full safeguard inventory (implemented vs. external)
- `/app/memory/AUTHORIZATION_GUIDE.md` — RBAC + scopes + policy overlays (2026-02-20)
- `/app/memory/test_credentials.md` — demo accounts
- `/app/test_reports/iteration_2.json` — testing agent report (24/24)
