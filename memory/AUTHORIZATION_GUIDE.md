# CCMS Authorization System — Design & Admin Guide

**Last updated:** 2026-02-20
**Code paths:** `backend/services/authz/` · `backend/core/metrics.py` · `frontend/src/contexts/PermissionsContext.jsx` · `frontend/src/pages/{RoleManagement,PermissionMatrix,AccessReview,Elevation}.jsx`

---

## 1. Model

Seven persisted entities back every authorization decision. Each has a UUID PK and is PostgreSQL-migration ready.

| Collection                       | Purpose                                                                          | Key indexes                            |
|----------------------------------|----------------------------------------------------------------------------------|----------------------------------------|
| `roles`                          | 11 baseline roles — Super Admin → Integration Service Account                    | `key` (unique)                         |
| `permissions`                    | Catalogue of `resource.action` + sensitivity + flags (`phi`, `export`, …)        | `key` (unique)                         |
| `role_permissions`               | Grant from a role to a permission, with `scope`, `requires_mfa/approval`, `break_glass_allowed` | `(role_key, permission_key)`  |
| `user_roles`                     | Role assignments to users (many-to-many); includes `assigned_at` + `assigned_by` | `(user_id, status)`                    |
| `permission_scopes`              | *(reserved)* Additional row-level/field-level policy overlays — not yet used     |                                        |
| `locations`                      | Clinic locations for `assigned_location` / `all_location_patients` scopes        | `code` (sparse unique)                 |
| `user_location_assignments`      | Users ↔ locations (many-to-many)                                                 | `(user_id, status)` · `location_id`    |
| `patient_assignments`            | Providers ↔ patients (many-to-many)                                              | `(provider_id, status)` · `(patient_id, status)` |
| `elevation_requests`             | Time-bound, approval-gated elevated access                                       | `(requester_id, status)` · `created_at`|

The **audit events** live in the existing `audit_logs` collection. Every authorization decision — *allow* as well as *deny* — is mirrored there (actions `authz.allow`, `authz.denied`, `authz.mfa_required`, `authz.approval_required`, `authz.role_assigned`, `authz.role_revoked`, `elevation.*`).

## 2. Permission naming

`resource.action` for the machine name. `scope`, `requires_mfa`, `requires_approval`, `break_glass_allowed` live as grant-level metadata so the same permission can mean different things to different roles.

Supported resources: **patient**, patient_chart, soap_note, treatment_plan, **appointment**, waitlist, intake_form, **insurance**, eligibility, **billing**, charge, payment, adjustment, **claim**, remit, coding, **document**, message, broadcast, secure_message, **consent**, privacy_request, retention_policy, release_of_information, **audit_log**, phi_access_report, access_review, security_event, **user**, service_account, **role**, permission, **org_settings**, clinic_settings, template, **reporting**, dashboard, **integration**, api_key, webhook, self, session, break_glass.

Supported scopes:
- `self` — only records owned by the caller
- `assigned_patients` — patients in `patient_assignments` where provider = caller
- `assigned_location` — records where `location_id ∈ user_location_assignments`
- `all_location_patients` — broader location-scoped read across a clinic
- `all_org` — unrestricted within the org
- `no_phi` — allowed but PHI fields must be masked (service accounts)
- `phi_limited` — metadata/non-clinical surface only
- `phi_full` — full PHI visibility (break-glass-gated in practice)

## 3. Policy engine

`services/authz/policy.py` exposes three helpers routers use:

```python
from services.authz.policy import require_permission, evaluate, scope_filter

# Route guard (default-deny + MFA gate + approval gate)
@router.get("/patients/{patient_id}")
async def get_patient(
    patient_id: str,
    user: dict = Depends(require_permission(
        "patient", "read", ctx_from_path={"patient_id": "patient_id"},
    )),
):
    ...

# Row-level filter for list endpoints
filter_frag = await scope_filter(user, "patient", "read")
if filter_frag.get("__deny__"):
    return []
cursor = db.patients.find({**other_filters, **filter_frag})

# Direct decision probe (no audit) — used by the frontend <Can>
decision = await evaluate(user, "audit_log", "read", {"patient_id": pid})
```

### Default-deny
`effective_grants()` returns the flattened grants for a user. If a permission is not in that list, the decision is *always* deny. There is no wildcard.

### Dual-run legacy shim
Users seeded before this system (legacy `users.role = admin|doctor|staff|patient`) get a synthetic role assignment computed on the fly until the migration fully populates `user_roles`. The shim logs at `DEBUG`, and the seed backfills `user_roles` for any existing user.

### MFA gate
When a grant has `requires_mfa=true`, the dependency:
1. Asserts a **`reauth_token`** cookie or `x-reauth-token` header is present.
2. If missing, raises `401` with the `X-Reauth-Required: 1` header so the frontend can open the password-reauth dialog.
Actual reauth is issued by `POST /api/auth/reauth` (see existing identity service).

### Approval gate (elevation)
When a grant has `requires_approval=true`, the dependency only allows the call if `evaluate()` resolved the permission **via an active elevation** (different user approved, not expired, not yet used).

### Break-glass
When a grant has `break_glass_allowed=true`, the denial path returns `detail: "Break-glass available"` so the frontend can offer the emergency-access dialog (existing `BreakGlassDialog.jsx`). Break-glass events continue to be logged with `metadata.emergency_access=true`.

## 4. Endpoints

### Authorization self & admin
- `GET /api/authz/me/permissions` — effective permissions for the caller (used by the frontend `PermissionsContext`).
- `POST /api/authz/check` — quick allow/deny probe (non-audited).
- `GET /api/authz/roles` — baseline roles + their grants (admin).
- `GET /api/authz/permissions` — full permission catalogue (admin).
- `GET /api/authz/matrix` — role×permission grid (admin).
- `POST /api/authz/users/{user_id}/roles` — assign role (admin; audited; bumps `session_epoch`).
- `DELETE /api/authz/users/{user_id}/roles/{role_key}` — revoke role.
- `GET|POST /api/authz/locations` — location management (admin for create).
- `POST|DELETE /api/authz/users/{user_id}/locations` — location assignment.
- `POST|DELETE /api/authz/patient-assignments` — provider↔patient assignment.

### Elevation workflow
- `POST /api/authz/elevation/request` — request elevated access (min 10-char reason; 5–240 min TTL).
- `GET /api/authz/elevation` — list (self for non-admins, all for approvers).
- `POST /api/authz/elevation/{id}/decision` — approve/reject (separation of duties enforced).
- `DELETE /api/authz/elevation/{id}` — cancel or revoke.

### Compliance reports (all require `audit_log.read` → MFA reauth)
- `GET /api/access/reports/users-by-role`
- `GET /api/access/reports/permissions-by-role`
- `GET /api/access/reports/privileged-users`
- `GET /api/access/reports/recent-role-changes?days=30`
- `GET /api/access/reports/phi-access-history?days=7`
- `GET /api/access/reports/export-history?days=30`
- `GET /api/access/reports/break-glass-history?days=90`
- `GET /api/access/reports/failed-authz?days=7`
- `GET /api/access/reports/access-review` — compact dashboard summary.

## 5. Admin UI

Four admin pages (admin-only for now; scoped by `role` guard in `App.js` during dual-run):

| Page              | Path                | What it shows                                              |
|-------------------|---------------------|------------------------------------------------------------|
| Role management   | `/roles`            | Users × assigned roles · assign/revoke roles · role catalogue |
| Permission matrix | `/permissions`      | Full role×permission grid, privileged-only filter, scope badges |
| Access review     | `/access-review`    | Summary stats, privileged users, PHI/export/break-glass/failed-authz histories |
| Elevation         | `/elevation`        | Request elevation, approve/reject, cancel                  |

All four pages fetch exclusively from `/api/authz/*` and `/api/access/reports/*`; no frontend-only policy decisions are trusted — the UI mirrors what the backend allows.

## 6. Migration notes

- **Idempotent seed**: `seed_authz()` runs on every boot. Updating `services/authz/constants.py` then restarting the backend is enough to evolve the matrix (system-seeded rows are replaced; `custom=true` rows are preserved).
- **Back-fill for existing users**: the seed writes a `user_roles` row mapping each legacy `users.role` string to its corresponding baseline role, and assigns every existing user to the default location (`HQ / Main Clinic`).
- **Patient records** get `location_id = HQ` as a default. Future migrations will split by clinic.

## 7. High-risk controls (MFA + approval)

Per the permission matrix PDF, the following are always MFA-gated **and** approval-gated (elevation required):

- `role.create / role.update / role.assign`
- `permission.update`
- `user.disable / user.reset_mfa`
- `service_account.create`
- `integration.create / update / disable`
- `api_key.create / api_key.rotate`
- `payment.refund`, `adjustment.writeoff`, `billing.void`
- `privacy_request.fulfill_export / fulfill_delete_anonymize`
- `retention_policy.manage`
- `break_glass.activate`
- `patient.archive`
- `document.purge`
- `session.revoke_other`

`patient.hard_delete` and `audit_log.delete` have **no grants anywhere** — these operations are blocked at the matrix level as an ISO 27001 tamper-evidence control.

## 8. Observability

Prometheus counters (exposed at `/api/metrics`):
- `ccms_authz_allows_total{resource, action}`
- `ccms_authz_denials_total{resource, action}`
- `ccms_elevation_requests_total{status}`

Every authz decision is also mirrored into the structured security JSON log (`component: authz`) so SIEM tools get real-time parity with the durable audit DB.

## 9. Rollout plan (dual-run → single-run)

1. **Now**: matrix + policy engine + admin UI + reports live; routers still call `require_role()` for backwards compatibility. `/api/authz/me/permissions` is authoritative for the frontend.
2. **Next**: migrate each router to `require_permission()` (patient → scheduling → audit → identity). Each migration removes one `require_role()` call and adds a `scope_filter()` in the list handler.
3. **Cut-over**: once all routers are migrated, delete `LEGACY_ROLE_TO_KEY` shim and drop the `users.role` string column.
4. **Post-cut-over**: enable `APP_ENV=production` + set all CUSTOMIZE grants through `role_permissions` rows with `custom=true`.

## 10. Testing

`backend/tests/test_iteration12_authz.py` covers: matrix shape, role catalogue, permission catalogue, `/me/permissions` for each legacy role, `require_permission` denial paths, MFA gate on audit reports (401 without reauth / 200 with reauth), end-to-end elevation (request → approve → separation-of-duties), role assign/revoke with session-epoch bump, reporting endpoints smoke, patient-portal scope containment, denial audit rows.
