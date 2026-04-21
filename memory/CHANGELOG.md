# CCMS Changelog

Append-only log of delivered work. Most recent on top.

---

## 2026-02-15 — Access Management Phase 2: Custom Roles (backend CRUD)

**Scope:** Backend-only. Admins can now create, clone, edit, and archive
custom roles scoped to their tenant. System baseline roles remain
read-only.

**What shipped**
- `POST /api/authz/roles` — create custom role from name + description +
  permission-key list. 201 on success. Generates a unique `key` like
  `custom_my_role_xxxxxx`. Invalid permission keys are silently filtered
  (defensive).
- `POST /api/authz/roles/{key}/clone` — clone any role (system or
  custom) into a new custom role with all the source's permission keys.
  Tenant-scoped.
- `PATCH /api/authz/roles/{key}` — edit name / description / permissions
  on a custom role. System roles → 409. Empty permission_keys → 400.
  Replaces all `role_permissions` rows. Bumps `session_epoch` for every
  user with this role so their token is re-evaluated on next request.
- `DELETE /api/authz/roles/{key}?force=true` — archive a custom role.
  If in use (active user_roles rows), returns 409 with the assignment
  count. `force=true` revokes all user_roles rows and bumps session
  epochs. System roles → 409.
- `GET /api/authz/roles?include_user_counts=true` — now emits
  `is_custom: bool` and optional `user_count: int` per role.
- Every mutation emits a structured `log_audit` row
  (`authz.role.created`/`updated`/`deleted`) with tenant_id, actor,
  and changed-field metadata.

**Tests** — 14/14 passing in `test_custom_roles_phase2.py`:
- list with is_custom + user_counts
- create happy path + empty-permissions 400 + invalid-key filtering
- clone happy path + clone-requires-name 400 + unknown-source 404
- patch name + permissions + system-role 409 + empty-keys 400
- delete unused + delete system 409 + delete-in-use 409 + force=true
  revokes users + user_count reflects assignments

**No regressions** — Phase 1 (12/12) + checkout hooks (10/10 individually) still green.

---

## 2026-02-15 — Access Management Phase 1: Permission Catalog (backend foundations)

**Scope:** Backend-only foundations for the new Users/Roles/Permissions UX.
No frontend changes in this phase — the existing pages still function.

**What shipped**
- `services/authz/permission_catalog.py` — decorates every permission in
  `constants.PERMISSIONS` with:
  - one of 11 product-facing modules (Dashboard, Scheduling, Patients,
    Clinical, Billing, Claims, Reports, Compliance & Audit, Settings,
    User Management, Administration),
  - a plain-English label + helper text (e.g.
    `appointment.override_rules` → "Override scheduling conflicts"),
  - sensitivity/phi/clinical/financial/destructive/export/privileged
    flags pass-through from the source catalog.
- `GET /api/authz/permission-catalog` — admin endpoint returning the
  grouped, labelled catalog. 117 permissions across 11 modules, sorted
  by sensitivity desc then label asc inside each module.
- `GET /api/authz/users/{id}/effective-permissions?explain=true` —
  admin endpoint returning a user's effective grant list PLUS a
  plain-English summary suitable for the "Review access before save"
  step. Tenant-isolated; 404 on cross-tenant probe.
- `POST /api/authz/roles/preview-effective-permissions` — preview a
  plain-English summary for an arbitrary permission-key list
  (backs the Role Editor's live summary).
- `permission_catalog.explain_permissions()` — pure function used by
  both endpoints; groups grants into "can" / "cannot" buckets, tallies
  per-module read/write coverage, and surfaces any high/critical or
  destructive permissions in a `sensitive_grants` list.

**Tests**
- `backend/tests/test_permission_catalog_phase1.py` — 12/12 passing.

**Non-goals in Phase 1**
- No custom roles (Phase 2)
- No new UI (Phase 3)
- No DB schema changes
- No migration of legacy users (Phase 5)

**Known pre-existing failures (NOT introduced by Phase 1):**
`tests/test_iteration12_authz.py` — 14 failures on main due to cookie-auth
harness drift (the test helpers don't set `Authorization: Bearer`
headers after login). Verified identical baseline via `git stash`.

---
