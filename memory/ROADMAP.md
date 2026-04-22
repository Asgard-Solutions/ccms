# CCMS Roadmap

Prioritized backlog for remaining P0/P1/P2 work. Most-recent updates on top.

## 2026-02-15

### Access Management redesign (5-phase plan)
| Phase | Status | Scope |
|-------|--------|-------|
| 1 — Permission catalog (backend) | ✅ Done | 11 modules, 117 decorated perms, 3 endpoints, 12/12 tests |
| 2 — Custom roles CRUD (backend)  | ✅ Done | create / clone / patch / archive + session epoch bump, 14/14 tests |
| 3 — Users screen + Create flow (frontend) | ✅ Done | `/admin/users`, 3-step dialog, Edit Access dialog, nav wired |
| 4 — Roles screen + Role Editor (frontend) | ✅ Done | `/admin/roles` card grid, grouped accordion editor, clone/archive flows |
| 5 — Migration + Access History + Security Policies | ✅ Done | legacy backfill, `/admin/access-history`, Security Policies panel |

### Recently completed P2 items
- ✅ Patient portal shell at `/portal` with role gating (patients
  redirected in; non-patients locked out) — 2026-04-22.
- ✅ Month-end bulk "Send outstanding statements" workflow —
  `POST /api/billing/statements/send-outstanding` + Billing Dashboard
  button with dry-run preview dialog; idempotent on unchanged
  balances — 2026-04-22.
- ✅ Global retry-after-reauth Axios interceptor (already shipped in an earlier session; confirmed wired).
- ✅ Drag-and-drop reorder for Appointment Types — backend `POST /api/appointment-types/reorder` + native HTML5 DnD UI, zero deps.
- ✅ Resend + Twilio integration layer — `services/notifications/{email,sms,verify}.py` with log-only fallback; password-reset + workforce-invitation callers wired; `.env.example` documents all required env vars.

### Phase 5 sub-tasks (P0 to unblock full rollout)
- Dry-run migration script: map legacy `user.role` strings → baseline
  role keys via `LEGACY_ROLE_TO_KEY`. Log ambiguous mappings; emit
  summary (`count_mapped`, `count_ambiguous`, `count_unmapped`).
- Idempotent backfill runner in `services/authz/seed.py` (extend existing
  back-fill block so it also writes `is_custom=False` on legacy roles +
  stamps `legacy_mapped_at` on user_roles rows).
- New `/admin/access-history` page surfacing `GET /api/audit-logs`
  filtered on action prefix `authz.role.*` / `authz.override.*` /
  `authz.elevation.*`. Sortable table; CSV export reuses existing
  `/api/audit-logs/export.csv`.
- Deprecation banner on existing `/roles`, `/permissions`, and
  `/access-review` pages pointing to `/admin/users` + `/admin/roles`.
- (Optional) Rename internal `custom_{slug}_{hex}` role keys scheme
  to `tenant-prefixed` for clarity when multi-tenant grows.

### Open action items
- Resolve CreateUserDialog double-PIN-step-up friction by deferring
  `/authz/roles` fetch until step 2 — **done** in Phase 3 polish.
- Audit stray custom roles from test runs (`Test Role *`,
  `Patch Test *`, `InUse Test *`, `Delete Test *`) — cleanup currently
  manual via `DELETE /api/authz/roles/{key}?force=true`. Consider a
  scheduled purge or `prefix=test_` + TTL policy.

## Pre-existing backlog (unchanged by this work)

### P0 (production go-live)
- HIPAA-eligible DB (Atlas + BAA, or Postgres in HIPAA-compliant cloud)
- BAAs with all PHI processors
- KMS-backed `DATA_ENCRYPTION_KEY`
- Retention worker for `retention_until < now`
- Audit log immutability at storage layer
- Consent capture on registration (versioned)
- Privacy Notice in UI + footer

### P1 (features)
- Global retry-after-reauth Axios interceptor (UX polish)
- Real Twilio SMS + Resend email (require BAAs)
- Postgres migration (schema is 1:1, mechanical)
- Patient self-service portal
- Structured JSON logging (structlog)
- CSV evidence export for auditors
- Prometheus alerting rules + runbooks
- Drag-and-drop ordering for Appointment Types

### P2 (polish)
- OpenID Connect / SAML SSO
- WebAuthn / passkeys
- Session fingerprint drift detection
- JIT admin elevation + peer-approval
- OpenTelemetry tracing
- Real broker (RabbitMQ / ServiceBus) swap
