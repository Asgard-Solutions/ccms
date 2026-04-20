# Changelog

All notable, user-visible, or security-relevant changes to CCMS are recorded
here. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project follows a rolling date-based release cadence (no SemVer
public release yet — we're pre-1.0).

> **Update rule** — every merged PR that changes behavior, adds a feature,
> fixes a bug, or changes a dependency MUST append an entry to this file.
> See [`docs/DOC_UPDATE_POLICY.md`](./docs/DOC_UPDATE_POLICY.md).

## [Unreleased]

### Added
- **Chiro Software Theme System (Slate + Teal + Copper)** — adopted the
  binding design system defined in `/app/docs/theme/`:
  - `CHIRO_SOFTWARE_THEME_STANDARD.md` — brand standard.
  - `CHIRO_THEME_ENGINEERING_IMPLEMENTATION_SPEC.md` — engineering source
    of truth.
  - `CHIRO_UI_REVIEW_AND_COMPLIANCE_CHECKLIST.md` — pass/fail review tool.
  - `docs/theme/README.md` — index + rule of adherence.
- **Rewrote `frontend/src/index.css`** to the spec's three-layer token
  architecture: foundation palette (slate / teal / copper / status) +
  typography / spacing / radius / shadow primitives, semantic light +
  dark tokens (shadcn HSL channels + hex), and component alias tokens
  (`--sidebar-active-bg`, `--table-row-hover`, `--dialog-overlay`,
  `--badge-premium-bg`, …).
- **Extended `tailwind.config.js`** — semantic `surface`, `surface-2`,
  `surface-3`, `border-strong`, `success`, `warning`, `info`,
  `accent-strong`, and chart colors; radius scale (`xs`→`xl`); shadow
  scale (`xs`→`lg`); font families (`display`, `body`, `mono`).
- **Typography migration** — Outfit / Manrope / JetBrains Mono wired via
  CSS variables; headings auto-render in Outfit, body in Manrope.
- **Legacy sage utility classes preserved as brand-aliases** —
  `text-sage`, `bg-sage`, `surface-sage`, `text-strong`, `surface-raised`,
  etc. now point to the new slate+teal+copper values so the 22 existing
  pages inherit the new brand without a file-by-file rewrite. A
  future pass will migrate them to semantic Tailwind classes
  (`bg-primary`, `text-foreground`, `bg-card`) per the spec.

### Changed
- **Brand direction** — deprecated the sage + stone palette in favor of
  the premium Slate + Teal + Copper system. Primary brand color moves
  from `#7B9A82` (sage) to `#14757C` (teal-700) in light and `#4CB5BA`
  (teal-400) in dark. Accent warmth shifts to copper
  (`#FAF0EB` / `#6B432B`). Radius base raised from 2px to 8px to match
  the "refined, not playful" shape language.
- **Phase 2 theme-discipline sweep** — replaced **every remaining**
  raw hex (`bg-[#…]`, `text-[#…]`, `border-[#…]`, `accent-[#…]`) and
  raw Tailwind palette class (`stone-*`, `divide-stone-*`) across
  `frontend/src/**` with semantic tokens (`bg-primary`,
  `bg-destructive-soft`, `text-muted-foreground`, `text-warning`,
  `bg-info-soft`, `border-border`, `divide-border`, …). Touched:
  `ProtectedRoute`, `PatientDocumentsCard`, `Login`, `PasswordReset`,
  `Calendar`, `RoleManagement`, `Appointments`, `SecurityConfig`,
  `Security`, `AuditLog`, `Notifications`, `Elevation`,
  `PatientDetail`, `Compliance`, `AccessReview`, `Privacy`,
  `Patients`, `Register`, `Dashboard`, `PermissionMatrix`, `toast`.

### Added (theme guardrail)
- **Refactored every core Shadcn primitive** to match the spec:
  `button.jsx`, `input.jsx`, `textarea.jsx`, `select.jsx`, `card.jsx`,
  `dialog.jsx`, `dropdown-menu.jsx`, `tabs.jsx`, `badge.jsx`,
  `table.jsx`, `sonner.jsx`. Each now uses:
  - Semantic tokens only (no raw Tailwind palette, no hex) — controls
    consume `bg-surface`, `bg-card`, `bg-popover`, `text-foreground`,
    `text-muted-foreground`, `border-border` directly.
  - 8px radius on controls (`rounded-sm`), 12px on cards & dialogs
    (`rounded-lg`) per spec §7.
  - 40px default height on buttons and inputs (36/44 for sm/lg) per
    spec §6.
  - 600 weight on button labels, `font-display` on card/dialog titles,
    12px bold-uppercase headers on tables per spec §5.
  - Accessible 2px focus ring with offset against the local surface,
    driven by the `--focus` token, on every keyboard-focusable
    element (spec §10).
  - Tokenized row hover / selected via `--table-row-hover` and
    `--table-row-selected` alias tokens.
  - Copper `premium` badge variant using `--badge-premium-*` alias
    tokens — reserved for billing / admin emphasis per spec §9.
- **Dialog**: overlay now reads `--dialog-overlay` (2px backdrop
  blur), content sits on `bg-card` with `shadow-md` + 12px radius.
- **Sonner**: replaced the broken `next-themes` import with the
  app's own `ThemeContext`, so toasts flip with the user's Light /
  Dark / System preference. Added tokenized `success` / `warning` /
  `info` / `error` variant classes.
- **`scripts/check_theme.py`** — Python CI guard. Scans
  `frontend/src/**` for raw hex arbitrary values, forbidden Tailwind
  palette families (slate / gray / stone / blue / red / etc.), and
  inline `style={{ color: "#…" }}` usages. Exits non-zero on any
  violation. Exempts the theme layer (`index.css`,
  `tailwind.config.js`) and shadcn primitives
  (`components/ui/**`). Runs as part of pre-commit and a new
  `.github/workflows/theme-guard.yml` CI job.
- **`.github/workflows/theme-guard.yml`** — runs `check_theme.py`
  on every PR targeting main/master/develop.
- **`.githooks/pre-commit`** — now runs both `check_docs.py` and
  `check_theme.py --quiet`; blocks commits that introduce palette
  violations (bypass with `--no-verify`).
- **`.github/pull_request_template.md`** — new "Theme compliance"
  section; every UI-touching PR confirms light/dark parity, focus
  states, semantic token usage, and reference to the UI Review
  Compliance Checklist.

- **Tailwind config** — exposed `secondary.hover` token
  (`bg-secondary-hover` utility) to cover the tab/pill pressed state
  used by Privacy / Compliance / PasswordReset.

- **Patient lookup workflow** — the `/patients` page is no longer a
  full-list dump. New `GET /api/patients/search` endpoint with:
  - Global `q` plus per-field `name`, `phone`, `address`, `dob`.
  - SQL-style `%` wildcards anywhere in the term (prefix, suffix, middle),
    case-insensitive; safely escaped (`%%` rejected, control chars
    rejected, 120-char cap).
  - Plaintext indexed regex for `first_name` / `last_name` / `email`.
  - Post-decrypt filter for encrypted `contact.phone_*`, `address_details`,
    and `date_of_birth`, with a 2 000-row candidate cap and
    `truncated_candidates` flag so the UI can prompt for refinement.
  - Multi-format DOB parsing (ISO, US, EU, year-only).
  - Pagination (`limit`, `offset`), hard-capped at 50 per page.
  - Masked-only projection — results never expose grouped PHI blocks.
  - Tenant + location scoping preserved; every search emits a
    `patient.searched` audit with the fields used + result counts.
  - New Mongo indexes `(tenant_id, last_name)`, `(tenant_id, first_name)`,
    `(tenant_id, phone)` for prefix-regex queries.
  - Tests: `backend/tests/test_patient_search.py` — **26 pass** covering
    wildcard semantics, case-insensitivity, DOB parsing, encrypted
    phone/address, pagination, auth, tenant scoping.
- **Frontend lookup UI** — `pages/Patients.jsx` rewritten:
  - Default view shows a "Recently viewed" section (localStorage, per-user,
    max 6) or a clean hero with wildcard examples.
  - Quick-lookup mode with 250 ms debounced typeahead after 2 characters.
  - Advanced mode with 4 focused inputs (Name / Phone / Address / DOB)
    and a manual Search submit.
  - Keyboard navigation (↑ / ↓ / Enter) across results.
  - Match-highlighting via `<mark>` with wildcard awareness.
  - "Too many candidates" banner surfaces backend truncation.
  - Clicking a result opens the full patient profile + pushes the entry
    onto "Recently viewed".
  - All interactive elements carry `data-testid`s.

### Removed
- The default `/api/patients` list call on the Patients page — the page
  no longer fetches the entire patient population into the browser.

### Changed
- **Per-user light / dark / system theme** — picker lives in the top-bar
  (sun/moon dropdown), persists to the user's profile via
  `PATCH /api/auth/me/preferences`, and syncs on every login so the
  clinician sees their chosen theme on any browser. System mode follows
  `prefers-color-scheme` and reacts live to OS-level changes.
  - Backend: `theme: "light"|"dark"|"system"` field on `users`, exposed
    via `UserPublic`; new `PreferencesUpdate` schema.
  - Frontend: `ThemeProvider` + `useTheme` hook + `<ThemeToggle />`
    component; localStorage fast-paint to prevent flash of wrong theme
    before `/auth/me` resolves.
  - Tests: `backend/tests/test_theme_preference.py` — 9 scenarios, all
    passing (default, light/dark/system swaps, invalid rejected, empty
    rejected, survives logout, per-user independence, unauth 401).

### Changed
- **Color system refactored into CSS variables** so dark mode swaps
  without per-page rewrites. New semantic utility classes
  (`surface-app`, `surface-raised`, `surface-muted`, `surface-sage`,
  `surface-warning`, `surface-danger-soft`, `text-strong`,
  `text-muted-strong`, `text-soft`, `text-sage`, `text-sage-deep`,
  `text-danger`, `text-warning`, `border-subtle`, `border-strong`,
  `bg-sage`, `bg-danger`) are defined in `@layer utilities` and swap
  under `.dark`. All 23 page + component files migrated from hard-coded
  hex utilities to these semantic classes in a single bulk pass.
- **Docs** — Added comprehensive project documentation: `README.md`,
  `CONTRIBUTING.md`, `SECURITY.md`, `docs/DOC_UPDATE_POLICY.md`, and a PR
  template. Existing long-form docs in `memory/` are now linked from
  `README.md`'s Documentation map.
- **CI — matrix-aware docs guard** — New `scripts/check_docs.py` driven
  by `docs/doc_rules.yml` enforces 9 declarative rules (code needs
  CHANGELOG, RBAC changes need AUTHORIZATION_GUIDE, tenancy changes need
  MULTI_TENANCY_ARCHITECTURE, auth changes need test_credentials, and so
  on). Wired into `.github/workflows/docs-guard.yml` for PRs and
  `.githooks/pre-commit` for local commits (opt-in via
  `git config core.hooksPath .githooks`). Supports `--json` for CI
  tooling. Supersedes the earlier `scripts/check_changelog.sh`.
- **CI — changelog stub helper** — `scripts/check_docs.py
  --emit-changelog-stub [--title …] [--category …] [--write]` drafts a
  well-formed bullet from the current diff, auto-categorises it
  (Added/Changed/Fixed/Security/Dependencies), and can prepend it under
  `## [Unreleased]`. Idempotent — reruns won't duplicate bullets.

## [2026-04-20] Phase 5 — Intake polish, uploads, signed consents + hardening
### Added
- **Wet-ink signature capture** (`frontend/src/components/SignaturePad.jsx`)
  wired into the 4-step patient intake wizard (Step 4 — Case & Consents).
  Canvas-based, pointer-events, devicePixelRatio aware, emits base64 PNG.
- **Patient document vault** — `POST/GET/DELETE /api/patients/{id}/documents`
  with 8 categories (insurance cards front/back, driver's license, referral
  letter, imaging report, intake form, consent receipt, other). All
  uploads: reauth-gated, audited, tenant-scoped, 10 MB hard cap.
- **Signed consent PDF generation** — `GET /api/patients/{id}/consents/{type}/pdf`
  using ReportLab. Supports hipaa/treatment/financial/telehealth/photo_release
  canonical types plus any custom `consents.additional[].type`.
- **PatientDocumentsCard** UI with automatic reauth prompt + retry when the
  backend returns `401 Re-authentication required`.
- **Magic-byte MIME sniffing** (python-magic + libmagic1) on every document
  upload — rejects spoofed content-types (e.g. ELF declared as `image/png`).
- **Streaming upload** via `SpooledTemporaryFile` — 64 KB chunks, early
  413 on cap breach, rolls to tmpfile past 1 MB for memory safety.
- **Autosave drafts** for the patient intake wizard (localStorage-based,
  per-user scope, cleared on successful save).
- **Edit-from-detail** flow: Edit button on `PatientDetail` auto-unmasks
  (with audit) and opens the wizard pre-filled with current data.

### Changed
- **Patient intake schema** now accepts both the legacy flat payload and the
  new grouped sections (`demographics`, `contact`, `address_details`,
  `emergency_contact_details`, `admin`, `guarantor`, `insurance`,
  `clinical_intake`, `case_details`, `consents`). Legacy top-level fields
  are backfilled from grouped sections when missing.
- **Encryption-at-rest** expanded to cover every grouped PHI section
  (previously only legacy scalar PHI fields were encrypted).
- **/api/auth/login** rate limit tuned to 30 attempts / 60s.
- **Patient service router** refactored from 984 → 628 lines by extracting:
  - `services/patient/_shared.py` (crypto/now/enforce_reason helpers)
  - `services/patient/documents_router.py`
  - `services/patient/consent_pdf_router.py`
  Parent router includes the sub-routers; public URL surface unchanged.
- **Consent PDF 500 error** now returns a generic message instead of the
  raw exception text (prevents library-trace leaks).

### Fixed
- `require_reauth` was misused as a FastAPI `Depends` on document endpoints,
  producing `422 user field required`. It's a plain helper — now called
  inline after permission resolution.

### Security
- Every document upload/download and consent PDF generation emits a
  PHI-flagged audit entry with IP, user-agent, and reason (when required).
- Magic-byte MIME sniffing closes the spoofed-content-type attack vector.

### Dependencies
- Added `reportlab==4.4.10` (Python).
- Added `python-magic==0.4.27` (Python) + OS package `libmagic1`.

### Tests
- New `backend/tests/test_phase5_docs_and_consent_pdf.py` — 22 scenarios,
  21 pass / 1 env-skipped.

---

## [2026-04-19 → 2026-04-20] Patient intake expansion (Phases 1-4)
### Added
- Grouped Pydantic section models (Demographics, ContactInfo, AddressInfo,
  EmergencyContactInfo, AdminInfo, GuarantorInfo, InsurancePlan/Info,
  ClinicalIntake, CaseDetails, ConsentRecord/Info) — Phase 1.
- 4-step patient intake wizard (`frontend/src/pages/Patients.jsx`) with
  validation, conditional rendering, and progress indicator — Phase 2.
- Pure-JS `patientWizardLogic.js` rules engine for conditional visibility +
  validation, with 39 passing Node unit tests — Phase 3.
- Grouped-payload rendering in `PatientDetail.jsx` with fallback to legacy
  scalar fields — Phase 4.

---

## [2026-04-19] Performance & scalability pass
### Added
- Redis (supervisord-managed, 128 MB LRU) for application cache + rate-limit
  buckets.
- Read/write DB split (`get_db_read` / `get_db_write` / `read_after_write_db`)
  Postgres-ready abstraction in `core/db.py`.
- Cache catalogue (`core/cache_keys.py`) with per-key TTLs and never-cache
  rules (unmasked PHI, exports, audit log).
- Prefix-based cache invalidation using Redis SCAN (never KEYS).
- `GET /api/perf/stats` admin-only ops view.

### Changed
- Graceful Redis fallback: requests never fail when Redis is down.

---

## [2026-04-19] HIPAA hardening pass
### Added
- **Audit logging** of every PHI access with outcome, IP, user-agent, reason.
- **Field-level encryption** (AES-256-GCM, `enc:v1:` prefix) for
  `patients.{address,emergency_contact,notes}`,
  `medical_records.{description,diagnosis,treatment}`, `appointments.notes`.
- **Password policy** — 12-char complexity + denylist + history-of-5 +
  90-day rotation warning + 120-day hard expiry.
- **MFA (TOTP)** with provisioning URI + 8 single-use backup codes;
  ticket-based challenge step on login.
- **Step-up reauth** required for delete-patient + add-medical-record +
  document upload/delete.
- **Masking** — PHI masked by default; `?unmask=true` audited + reason-gated
  for non-admin clinicians.
- **Soft-delete** with 7-year retention and legal-hold gate.
- **Frontend** — `BreakGlassDialog`, `ReauthDialog`, masked Notifications,
  Security + AuditLog admin pages, 15-minute idle timeout.

---

## [2026-04-18] MVP (Phase 1)
### Added
- Identity (register, login, admin user CRUD), Patient CRUD, Scheduling
  with conflict detection, mock SMS/Email via in-process event bus.
- Sage + stone medical theme, 7 role-aware pages.
- FastAPI + MongoDB + React scaffolding under supervisord.
