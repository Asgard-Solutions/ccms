# CCMS — Multi-Tenancy Architecture

**Last updated:** 2026-02-21

## 1. Decision summary

| Axis                                | Choice                                                   |
|-------------------------------------|----------------------------------------------------------|
| What is a tenant?                   | **Practice group / customer organisation**               |
| Where does a location sit?          | As a child entity under a tenant                         |
| Default DB model                    | **Shared DB, shared schema, `tenant_id` on every row**   |
| Future hybrid                       | Selected enterprise tenants may be promoted to a **dedicated cluster** via `TenantDatabaseRouter` — no business-logic changes required |
| Tenant context propagation          | Embedded in JWT (`tid` claim) at login                   |
| Multi-tenant users                  | **Not supported in Phase 1.** Each user belongs to exactly one tenant (or is a platform admin). |
| Platform admin                      | Global role `platform_admin` (tenant_id = NULL); may override active tenant via `X-Tenant-Id` header; every cross-tenant access is audited |
| Location access                     | Via `user_location_assignments`, with the `tenant_scope_all` flag on the user record for tenant-wide visibility |

## 2. Why tenant = organisation (not location)

- A practice group has clinical and financial continuity across its own locations: shared patient charts, cross-location provider coverage, referrals, consolidated group-level billing, and a single admin console. Splitting by location would force synthetic cross-database joins for the most common queries in the app.
- A tenant is the commercial boundary (one contract, one invoice, one BAA). Locations are an operational boundary that must be cheap to add/close/rename.
- Location isolation is still a first-class security property — achieved via `location_id` on rows + the authz scope filter (`assigned_location`, `all_location_patients`). No DB split is needed to guarantee that an Uptown clerk cannot see Downtown records.

## 3. Why not DB-per-location

- Doubles the operational overhead per customer (backups, auth, TLS certs, monitoring per DB) with no isolation gain over row-level scoping.
- Makes group-level dashboards require cross-DB joins.
- Forbids our data-protection controls (encryption, retention sweeper) from running with a single key schedule.
- Makes moving a location between groups (reassignment, M&A) expensive and audit-unfriendly.

## 4. Why not "shared table, trust the frontend"

- Frontend filters are security theatre — an attacker can forge requests with arbitrary tenant/location IDs.
- Our rule: **every tenant-owned query MUST go through `core.tenant_scope.scoped_filter(q, ctx)`**. The helper injects `tenant_id` and (optionally) `location_id` into the Mongo filter, and returns a `__deny__` sentinel for users with no eligible locations. A developer who forgets gets either an immediate empty result (safe) or a raised 403, not a silent leak.

## 5. Entity / relationship model

```
tenants (id, slug, name, type [single|group], status, db_tier [shared|dedicated], created_at, ...)
  └── locations (id, tenant_id, name, code, timezone, status, address, created_at)
  └── users (id, tenant_id, email, role, tenant_scope_all, is_platform_admin, status, ...)
        └── user_location_assignments (id, user_id, tenant_id, location_id, status)
        └── user_roles (id, user_id, role_key, status)            # authz
        └── permission_scopes (id, user_id, permission_key, ...)  # authz overrides

  └── patients (id, tenant_id, location_id, user_id, first_name, ..., phi-encrypted fields)
        └── medical_records (id, tenant_id, patient_id, location_id, ...)
        └── patient_assignments (id, tenant_id, provider_id, patient_id, location_id)

  └── appointments (id, tenant_id, location_id, patient_id, provider_id, start_time, end_time, ...)

  └── notifications (id, tenant_id, appointment_id, ...)

  └── privacy_requests (id, tenant_id, subject_user_id, ...)
  └── consent_records  (id, tenant_id, user_id, ...)

audit_logs (id, tenant_id, actor_id, action, entity_type, entity_id, outcome, phi_accessed, ip, user_agent, created_at)

elevation_requests (id, tenant_id, requester_id, permission_key, status, approver_id, expires_at, used_at)
```

Cardinalities:
- 1 tenant → N locations
- 1 tenant → N users (platform admins stand outside this relation; `tenant_id = NULL`)
- 1 user → N `user_location_assignments` (0..N). If `users.tenant_scope_all = true`, the user sees all locations in their tenant regardless of assignment rows.
- 1 tenant → N patients, N appointments, N audit_logs — all carrying `tenant_id`
- 1 location → N patients, N appointments

## 6. Tenant routing abstraction (hybrid bridge)

All repositories obtain their Motor database through `core.tenancy.tenant_db(tenant_id)`. The implementation is:

```python
class TenantDatabaseRouter:
    def get_db(tenant_id):
        if tenant_id in TENANT_DB_MAP:           # dedicated tenant
            return <client_for_dedicated_uri>[<its_db_name>]
        return <shared_client>[DB_NAME]          # default
```

Today, `TENANT_DB_MAP` is empty and every tenant lives on the shared cluster. Tomorrow, to promote a tenant to dedicated infrastructure:

1. Spin up a MongoDB/PostgreSQL cluster in the target region / with the target BAA.
2. Copy the tenant's rows across (collection-by-collection, filtered by `tenant_id`).
3. Set `TENANT_DB_MAP='{"<tenant_id>": {"uri": "mongodb+srv://...", "db": "ccms_acme"}}'` in the app environment.
4. Update `tenants.db_tier = "dedicated"` on that row.
5. Rolling restart the API gateway. **Zero code changes.**

Dedicated clusters inherit the same indexes, same security controls, same audit schema. The only operational difference is backup policy and isolation boundary.

## 7. Request pipeline

```
request ──► get_current_user()        # decodes JWT, verifies epoch + sst
        ──► get_tenant_context()      # builds TenantContext from JWT.tid + user.tenant_scope_all
        ──► require_permission(r,a)   # default-deny policy engine (services/authz/policy.py)
        ──► scoped_filter(filter, ctx, location_scoped=True)
        ──► Motor query via tenant_db(ctx.tenant_id)
```

Key properties:

- **Cross-tenant IDs return 404, not 403.** The tenant filter runs *before* permission evaluation at the row level — a user in tenant A looking up patient from tenant B simply sees "not found" (no existence disclosure).
- **Writes are stamped** via `stamp_for_write(doc, ctx, location_id=...)`. `tenant_id` is set from the caller's JWT; `location_id` is either provided by the caller (and validated), inherited from the patient, or auto-picked if the caller has exactly one location.
- **Caches are tenant-keyed.** Every cache key (`patients_list`, `providers`, `appointments_query`) includes the tenant id so cache values cannot cross tenants.

## 8. Platform admin semantics

- JWT carries `pa: true` for platform admins. The same claim is reflected on `users.is_platform_admin`.
- They bypass tenant filtering when `X-Tenant-Id` is absent (they see everything).
- When `X-Tenant-Id` is set, they act *as if* they belonged to that tenant — all filters activate.
- Every list call emits an audit row with `platform_admin_access=true` so cross-tenant reads are traceable.
- Platform admins never receive PHI masking bypass automatically — break-glass and MFA overlays still apply.

## 9. Seed data

`services/tenancy/seed.py` creates on every boot (idempotently):

| Tenant                 | Slug              | Type    | Locations                              | Demo users                                                                 |
|------------------------|-------------------|---------|----------------------------------------|----------------------------------------------------------------------------|
| Default Practice       | `default`         | single  | Main Office (HQ)                       | `admin@ccms.app`, `doctor@ccms.app`, `staff@ccms.app`, `patient@ccms.app` |
| Sunrise Chiro Group    | `sunrise-chiro`   | group   | Downtown, Uptown, Eastside             | see matrix below                                                           |

Sunrise demo user matrix (all share password `Sunrise@ComplianceClinic1`):

| Email                         | Role   | Tenant scope        | Location access                  |
|-------------------------------|--------|---------------------|----------------------------------|
| group-admin@sunrise.test      | admin  | entire tenant       | all 3 locations                  |
| downtown-doc@sunrise.test     | doctor | specific location   | Downtown only                    |
| floater-doc@sunrise.test      | doctor | multi-location      | Downtown + Uptown                |
| eastside-staff@sunrise.test   | staff  | specific location   | Eastside only                    |

And a global platform admin: `platform-admin@ccms.app` / `Platform@ComplianceClinic1`.

## 10. Backfill of legacy data

The seed is idempotent and safe on every boot. Its backfill phase stamps the default tenant onto every legacy row that is missing `tenant_id`, across: `users` (except platform admins), `patients`, `appointments`, `medical_records`, `notifications`, `audit_logs`, `consent_records`, `communication_preferences`, `privacy_requests`, `password_reset_tokens`, `login_attempts`, `permission_scopes`, `elevation_requests`, `user_roles`, `user_location_assignments`, `patient_assignments`, `locations`.

`patients`, `appointments`, and `medical_records` also receive `location_id = <default location>` when missing, so legacy single-clinic installs continue to behave exactly as before after upgrade.

## 11. Non-goals (deliberately out of scope)

- **Separate DB per location** — rejected; see §3.
- **Multi-tenant users** — a user works at exactly one tenant in Phase 1. Support can be added via an `n:m user_tenant_roles` table without changing the request pipeline.
- **Tenant-aware DNS subdomains** — `acme.ccms.app → tenant=acme`. Achievable later by adding a middleware that validates the header/subdomain matches the JWT `tid`.

## 12. Future work

- **P1**: Migrate `privacy`, `communication`, and `elevation` routers to the scoped helpers so they enforce tenant filtering natively (they currently rely on the backfilled `tenant_id` field only).
- **P2**: Promote a single enterprise tenant to a dedicated cluster via `TENANT_DB_MAP` + document the data-copy runbook.
- **P2**: Replace the `users.tenant_scope_all` boolean with a derived property from `user_roles` + a `TENANT` role scope.
- **P2**: Add a tenant-selector UI for platform admins; surface the `X-Tenant-Id` override as an explicit UX affordance.

## 13. Tests (iteration 14)

Located at `/app/backend/tests/test_iteration14_tenancy.py`:

- **Tenant context** — each seeded user sees the correct tenant + visible locations.
- **Cross-tenant isolation** — default admin cannot see Sunrise patients/appointments/audit rows and vice-versa; direct GETs by id return 404, not 403.
- **Location scoping** — downtown-doc sees only Downtown rows; floater-doc sees Downtown + Uptown; eastside-staff sees only Eastside; group-admin sees the union.
- **Tenant-aware writes** — patient/appointment creation stamps `tenant_id` + `location_id`; writes to locations the user is not assigned to return 403.
- **Reporting** — group-admin sees patients aggregated across all three locations; location-restricted users see their subset only.
- **Platform admin** — can list every tenant, can create a new tenant (with an auto-created primary location); tenant admins cannot create tenants.
- **Public registration** — assigns the new patient to the default tenant.

## 14. How to add a new tenant-owned feature safely (developer cookbook)

This is the canonical recipe. Deviating from it is the most reliable way to ship a data-leak bug, so please don't.

### 14.1 Define a repository

```python
# backend/services/billing/models.py
from core.repository import TenantScopedRepository

class InvoiceRepository(TenantScopedRepository):
    collection_name = "invoices"
    location_scoped = True   # invoices belong to a specific clinic location
```

Subclassing `TenantScopedRepository` is the one and only way tenant-owned data may be accessed. It is guaranteed to:

- fail closed with `MissingTenantContext` if a `TenantContext` is not supplied,
- inject `tenant_id` (and optional `location_id`) into every query,
- stamp `tenant_id` on every insert,
- audit cross-tenant id probes (`security.cross_tenant_attempt`),
- refuse the empty-filter bulk footgun (`UnsafeQueryError`).

### 14.2 Declare permissions and grant them to roles

Edit `services/authz/constants.py`. Add `PERMISSIONS` entries for `invoice.create/read/update/void` and grant them to the appropriate roles via `ROLE_GRANTS`.

### 14.3 Wire the route

```python
from fastapi import APIRouter, Depends, HTTPException, Request, status
from core.audit import audit_success
from core.tenancy import TenantContext, get_tenant_context
from services.authz.policy import require_permission
from services.billing.models import InvoiceRepository

router = APIRouter(prefix="/invoices", tags=["billing"])
_invoices = InvoiceRepository()

@router.post("", status_code=201)
async def create_invoice(
    payload: InvoiceCreate,
    request: Request,
    user: dict = Depends(require_permission("invoice", "create", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    ctx.assert_tenant_bound()
    doc = await _invoices.insert_one(payload.model_dump(), ctx,
                                     location_id=payload.location_id)
    await audit_success(user, "invoice.created", request,
                        entity_type="invoice", entity_id=doc["id"])
    return doc


@router.get("/{invoice_id}")
async def get_invoice(
    invoice_id: str,
    request: Request,
    user: dict = Depends(require_permission("invoice", "read", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    inv = await _invoices.find_one_by_id(invoice_id, ctx)
    if not inv:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    return inv
```

That is it. No direct `db.invoices.find(...)` anywhere in the new service.

### 14.4 Background jobs / async workers

```python
from core.tenancy import TenantContext
from services.billing.models import InvoiceRepository

async def dunning_worker(tenant_id: str):
    ctx = TenantContext.for_background(tenant_id=tenant_id, actor="dunning")
    repo = InvoiceRepository()
    overdue = await repo.find({"status": "overdue"}, ctx, limit=500)
    ...
```

Never call a repository method from a worker without `for_background()` — without it, `MissingTenantContext` fires immediately.

### 14.5 What NOT to do

- **Never** call `get_db().invoices.find_one({"id": x})` in a route. There is no way to make that safe after the fact.
- **Never** pass user input straight into a Motor filter without `scoped_filter()` or `repo.find()`.
- **Never** skip `ctx.assert_tenant_bound()` on write paths — a platform admin without `X-Tenant-Id` has `tenant_id=None`, and we refuse to blindly write such rows.
- **Never** add an admin-only bypass that reads a raw collection. If a legitimate platform need exists (migration, forensics), build it behind `is_platform_admin` and audit with `platform_admin_access=True`.

### 14.6 Row-level security vs application-level enforcement

MongoDB does not support row-level security policies (Postgres does). Our current enforcement is therefore purely application-layer — but it is:

- **Centralised** in `TenantScopedRepository` and `scoped_filter()`, so a one-line audit of those two files is sufficient to assess the tenant-isolation posture.
- **Defence-in-depth** — the policy engine (`require_permission`) runs *before* the repository filter; the repository filter runs *before* the driver sees the query; `find_one_by_id` emits a security audit on cross-tenant id probes; `update_many({})` raises before touching the DB.
- **PostgreSQL-ready** — when we migrate to Postgres, each `TenantScopedRepository` subclass maps 1:1 to a table whose RLS policy is `tenant_id = current_setting('app.tenant_id')`. The repository becomes a compatibility shim until every call-site is migrated to raw SQL; in the meantime application-level enforcement continues to apply.

## 15. Iteration 15 — repository enforcement + cross-tenant audit (2026-02-21)

- **`core/repository.py::TenantScopedRepository`** — fail-closed wrapper over Motor collections. Methods: `find`, `find_one`, `find_one_by_id`, `count`, `insert_one`, `update_one`, `update_many`, `delete_one`, `delete_many`. Raises `MissingTenantContext` when called without a context; raises `UnsafeQueryError` on empty-filter bulk ops.
- **Pre-built subclasses**: `PatientRepository`, `AppointmentRepository`, `MedicalRecordRepository`, `NotificationRepository`, `AuditLogRepository`.
- **Cross-tenant id probe audit**: `find_one_by_id` performs one unscoped lookup on a 404; if the row exists in a DIFFERENT tenant, emits `security.cross_tenant_attempt` (outcome=failure) with actor_tenant_id + target_tenant_id metadata. Caller still gets 404 — no enumeration leak.
- **`TenantContext.for_background(tenant_id, actor=...)`** — synthetic context for async jobs / schedulers / retention sweeper. Forbidden-to-be-platform-admin; tenant-bound; tenant-wide (no location restriction) by default.
- **Request-state stash**: `get_tenant_context()` caches the resolved context on `request.state.tenant_context` so low-level exception handlers and audit middlewares can read it without re-running auth. `request_id`, `ip`, and `user_agent` are populated on every context.
- **Sunrise demo data seeded** — 2 patients × 3 locations = 6 patients, plus a medical record and a 30-day-out appointment each. The `get-started-in-30-seconds` demo now has something to click.
- **Patient router migrated to repository** — `GET /patients/{id}` now goes through `PatientRepository.find_one_by_id` (exercising the cross-tenant audit). Remaining patient/scheduling/audit routes continue on `scoped_filter` and are safe; migration to the repository pattern is a P1 but not a correctness blocker.
- **Verified (iteration_15)**: 6/6 new tests — demo-data visibility, location-scoping for downtown-doc, cross-tenant probe audit, repository fail-closed, unsafe empty-filter rejection, background context acceptance. Regression 19/19 (iteration_14).



## 16. Iteration 16 — Cache isolation, background jobs, reports & exports (2026-02-21)

### 16.1 Tenant-safe cache (`core/tenant_cache.py`)

- **Key builder** `key_for(tenant_id, *parts)` — the only sanctioned way to build a cache key. Format: `t:<tenant_id>:<seg>[:<seg>…]`; platform-admin-only data uses the `pa:` namespace.
- **Wrapper** `TenantCache.get/set/get_or_set/invalidate/invalidate_tenant` — refuses any key that doesn't start with `t:` or `pa:` (`UnsafeCacheKeyError`). TTL is bounded to `(0, 86400]` — no infinite caches of tenant data.
- **`invalidate_tenant(tenant_id)`** — wipes every cached entry for a tenant in one call. Used after role-epoch bumps, tenant setting changes, or data-export revocations.
- **What MUST NOT be cached**: unmasked PHI, JWTs, reauth tokens, mfa tickets, password-reset tokens, export download tokens.
- **What CAN be cached** (short TTL): masked patient list (30 s), providers per tenant (300 s), appointment list per tenant+location+filter (30 s), effective permissions per user (120 s, invalidate on epoch bump), report result set (300–3600 s).

### 16.2 Background jobs (`core/tenant_jobs.py`)

- **Payload schema**: `{tenant_id (required), job_type, payload, actor_user_id?, location_id?, run_at?}`. `enqueue()` refuses a missing `tenant_id` with `MissingJobContext`.
- **Handler contract**: `async def handler(ctx: TenantContext, payload: dict, meta: dict)`. `ctx` is built via `TenantContext.for_background(tenant_id=..., actor="worker:<job>")` — explicitly non-platform-admin.
- **Auditing**: `job.enqueued`, `job.started`, `job.completed`, `job.failed` audit rows with the tenant_id, actor, and job metadata.
- **Durability**: job rows are persisted in the `jobs` collection so dead jobs are visible + retryable. Same payload shape as a future Celery / SQS worker — zero business-logic change to migrate.

### 16.3 Reporting (`services/reports/`)

- **Registry + single entry point** `run_report(ctx, name, filters)`. `location_ids` are validated against `ctx.allowed_location_ids`; unauthorized scope raises `UnauthorizedReportScopeError` → 403.
- **Built-in reports**:
  - `appointments_by_day` — counts per day for the last N days (max 365).
  - `provider_productivity` — appointments per provider, with tenant-scoped name hydration.
  - `location_performance` — patients + appointments per location (subset or tenant-wide).
- **Cache keying** `t:<tid>:report:<name>:<filters_hash>` TTL 300 s; cache is per-tenant by construction.
- **Audits** — `report.generated` on success, `report.denied` on scope rejection.

### 16.4 Exports (`services/exports/`)

- **Storage layout** — `/app/data/exports/<tenant_id>/<export_id>.csv`. Tenant id is part of the path; there is no shared directory. File names contain no PHI.
- **Lifecycle** — request → audit `export.requested` → enqueue `export.generate` job → job runs as tenant-bound background context → write CSV → status flips to `ready` → audit `export.generated`.
- **Download** — `GET /api/exports/{id}/download?token=<jwt>`. The JWT carries `{sub, tid, eid, exp}`, is signed with `JWT_SECRET`, TTL 15 min. Server re-verifies token signature + tenant match + export tenant + status=`ready`. Emits `export.downloaded` on success, `export.download_denied` on failure.
- **PHI privilege** is stashed on the export row at request time (`include_phi`), so the background worker writes the exact set of columns the requesting user's role is authorized to see — not more (because the worker's own role is `worker`, zero PHI by default), not less (because `include_phi` was captured pre-job from the real actor).
- **Cross-tenant token replay** is detected and audited — even if a token is stolen, it cannot be used by a user in a different tenant.
- **Cleanup** — `cleanup_expired_exports()` runs on boot and is callable at `POST /api/exports/cleanup` (tenant admin). Marks `status=expired`, unlinks the file, emits `export.expired` audit rows.

### 16.5 Platform-admin bypass audit

`require_permission()` now short-circuits for platform admins (no user_roles needed) and emits `authz.platform_admin_bypass` on every allow. The platform team's every action is thus traceable by `action=authz.platform_admin_bypass` in the audit log, regardless of the resource/action they touched.

### 16.6 Tests (iteration 16)

`/app/backend/tests/test_iteration16_cache_jobs_reports_exports.py` — 10/10 pass:
- Cache key builder emits tenant-namespaced keys and rejects unsafe keys + extreme TTLs.
- `POST /reports/location_performance/run` produces different results per tenant.
- Location-restricted users cannot pass a foreign `location_ids` (→ 403).
- Location-restricted users see the report scoped to their own locations only.
- Export create → poll → download succeeds end-to-end for the requester.
- Download token replay by a different tenant is denied (401/403/404).
- Hand-forged expired JWTs are denied (401).
- `enqueue()` without tenant_id raises `MissingJobContext`.
- Audit trail covers `export.requested`, `export.generated`, `export.downloaded`.

### 16.7 Anti-patterns that this iteration forbids

- Caching tenant data under a record id alone (`patient:<id>`) — `TenantCache` refuses the key.
- Running a background job without a tenant — `enqueue()` refuses it and `for_background()` refuses an empty tenant_id.
- Issuing export download URLs that are unauthenticated or long-lived — every link is a signed JWT with 15-min exp, re-verified server-side.
- Storing exports in a shared bucket/directory — path is `/app/data/exports/<tenant_id>/…` and there is no shared location.
- Passing user-supplied `location_ids` to a report without validation — `run_report()` rejects with 403 before the aggregation runs.

