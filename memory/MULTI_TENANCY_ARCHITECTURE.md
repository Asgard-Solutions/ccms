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
