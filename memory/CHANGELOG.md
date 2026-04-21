# CCMS Changelog

Append-only log of delivered work. Most recent on top.

---

## 2026-02-15 — Access Management Phase 4: Roles screen + grouped Role Editor

**Scope:** Frontend-only. Backend CRUD shipped in Phase 2.

**What shipped**
- **`/admin/roles`** (`AdminRolesPage.jsx`) — card-based role catalog:
  - "Built-in roles" grid (9 common clinic roles, ordered by relevance:
    Clinic Manager → Org Owner → Provider → Front Desk → Clinical Staff
    → Billing Specialist → Auditor → Compliance Officer → Patient
    Portal). View-only with Clone action.
  - "Custom roles" grid — Edit / Clone / Archive. Inline confirm
    dialog for archive; force-unassigns users when in use.
  - Collapsible "Show internal / service roles" toggle for
    `super_admin` + `integration_account`.
  - Each card surfaces permission count, user count, built-in /
    custom / privileged badges.
- **`RoleEditorDialog.jsx`** — create/edit/view a role:
  - Loads `GET /authz/permission-catalog` and renders one accordion
    per module (11 modules).
  - Each module row shows a `X/Y` counter and a "Select all / Clear
    all / Select rest" chip.
  - Each permission row shows plain-English label + helper text,
    sensitivity tag, PHI/Financial/privileged badges, Flame icon on
    destructive/critical permissions.
  - Debounced live plain-English preview under the module list from
    `POST /authz/roles/preview-effective-permissions`.
  - View mode dims unselected permissions and disables all controls
    (built-in roles).
  - Create mode: `POST /authz/roles`. Edit mode: `PATCH /authz/roles/{key}`.
- **Nav**: new "Roles" entry at `/admin/roles`. Old `/roles` kept
  behind "Advanced: Role matrix" label.
- **AdminUsersPage**: "Manage roles" quick link now points to
  `/admin/roles` (was `/roles`).

**Verified**
- Visual smoke test: 9 built-in cards rendered, 1 custom card rendered
  with Edit/Clone/Archive buttons. "Show internal / service roles (2)"
  toggle visible.
- Leftover `custom_inuse_test_71b6c5` role from Phase 2 test runs
  cleaned up via DELETE `?force=true`.
- No regressions in Phase 1 (12/12) or Phase 2 (14/14) backend tests.

**Non-goals in Phase 4**
- Advanced Security Policies panel (MFA/approval/break-glass flags)
  still deferred — covered by existing PermissionMatrix's grants until
  explicitly migrated.
- Migration backfill + Access Change History tab → **Phase 5 (pending)**.

---

## 2026-02-15 — Access Management Phase 3: Users experience (frontend)

**Scope:** Frontend-only. Old pages at `/roles`, `/permissions`, and
`/access-review` retained as deprecated "Advanced" routes for backward
compatibility during the transition.

**What shipped**
- **New primary admin surface** `/admin/users` (`AdminUsersPage.jsx`):
  - Searchable list (name + email), status filter (all / active /
    disabled), role chips per row (+N more), status badge, Edit
    access / Disable / Reactivate actions.
  - "Add user" opens the new 3-step wizard.
  - "Manage roles" quick link to `/roles` (until Phase 4 replaces it).
  - Deprecated-advanced footer linking to `/roles`, `/permissions`,
    `/access-review` with an AlertTriangle icon.
- **`CreateUserDialog.jsx`** — 3-step guided flow:
  - Step 1: Profile (name + email + password ≥12 + phone). Next disabled
    until valid.
  - Step 2: Roles — common roles first; "Show advanced / internal
    roles" toggle reveals `super_admin` + `integration_account`. Each
    role shows built-in/custom/privileged badges and a "Covers:" hint.
    Roles list is lazy-loaded on Step 1 → Step 2 transition to avoid
    triggering a PIN step-up when the dialog first opens.
  - Step 3: Review — plain-English effective-access summary from
    `POST /authz/roles/preview-effective-permissions`, plus any
    high-sensitivity grants surfaced as amber chips.
  - Submit creates user + assigns roles in one flow (uses
    `POST /auth/users` for profile, then `POST /authz/users/{id}/roles`
    per selected role).
- **`EditUserAccessDialog.jsx`** — single-step modal to add/remove role
  assignments for an existing user. Live plain-English preview as
  roles are toggled; Save diffs the selected set against the current
  set and issues `POST /authz/users/{id}/roles` + `DELETE` calls.
- **Nav**: new top-of-admin "Users" entry; "Permissions" and
  "Access Review" relabelled with an "Advanced:" prefix to signal
  they're legacy power-user surfaces.

**Tests** — Backend Phase 1+2 regression: **26/26 green** (12 catalog +
14 custom roles). Frontend verified by testing agent iteration_60:
- `/admin/users` renders, deprecated footer links present.
- Create User Step 1 validation correct (email + password ≥12).
- Step 2 common vs advanced roles correctly split.
- All spec'd `data-testid`s wired.

**Non-goals in Phase 3**
- No grouped Role Editor (Phase 4)
- No legacy-role migration backfill (Phase 5)
- No Access Change History tab (Phase 5)

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
