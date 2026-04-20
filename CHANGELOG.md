# Changelog

All notable, user-visible, or security-relevant changes to CCMS are recorded
here. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project follows a rolling date-based release cadence (no SemVer
public release yet — we're pre-1.0).

> **Update rule** — every merged PR that changes behavior, adds a feature,
> fixes a bug, or changes a dependency MUST append an entry to this file.
> See [`docs/DOC_UPDATE_POLICY.md`](./docs/DOC_UPDATE_POLICY.md).

## [Unreleased]

- **Scheduling summary views now use count aggregation (Task 10).**
  New backend endpoint `GET /api/appointments/counts` runs a single
  MongoDB aggregation pipeline that buckets `start_time` by the
  caller-supplied IANA `tz` via `$dateToString`, groups by local
  date, and returns `[{date, count, samples[]}]`. An
  `include_samples` query parameter (0..10, default 0) decides how
  many lightweight sample appointments are returned per day; samples
  are hydrated with patient + provider names in one extra round-trip
  (same pattern as the list endpoint). Tenant scoping, location
  scoping, and role-based filters (doctor → own provider_id,
  patient → own patient_id) all mirror the list endpoint verbatim.
  Response is cached 30s per
  `(role, tenant, range, tz, samples, provider_id, patient_id,
  location_id, status)` cache key.
  - Week view now fetches counts + 3 samples per day.
  - Month view fetches counts + 2 samples per day.
  - Year view fetches counts-only (365/366 dates, 0 samples).
  - Day view still pulls the full list endpoint since it needs
    complete timing, phone, reason, notes etc. The detail fetch in
    `useScheduling` is now skipped when `view !== "day"` — no more
    duplicate payload on view toggles.
  - Client-side in-memory cache on `useAppointmentCounts` keyed by
    `(view, range, tz, samples, providerId)` so quick view hops
    don't refire the request.
  - Cancel / reschedule / create paths invalidate **both** the
    detail and counts caches so the UI stays consistent.
  - Backend tests (`backend/tests/test_appointment_counts.py`,
    5/5 green): shape + totals reconcile with the list endpoint,
    tenant isolation, `include_samples` cap (0 and 11→422 bound),
    `tz` bucketing smoke-test, patient-role auto-scoping.

- **Scheduling Day view now respects clinic hours (Task 9).** A new
  `useClinicHours` hook resolves the caller's active location via
  `/api/tenancy/me/context` → then pulls `hours[]` from
  `/api/clinic-profiles/{locationId}`. `DayView` uses this to compute
  its visible window as **(open − 2h) → (close + 2h)**, snapped to
  15-minute boundaries.
  - Examples: Wednesday 08:00–18:00 → timeline 06:00–20:00. Saturday
    09:00–13:00 → timeline 07:00–15:00.
  - **Closed days** render a `Clinic closed` pill in the header plus a
    warning banner; the timeline still shows a nominal 07:00–19:00
    window for exception viewing, and any appointments that exist on
    that day are never silently hidden — the banner reveals a
    "Show all appointments" button that expands the window to
    enclose every appointment present.
  - **Outside-window appointments** on open days trigger the same
    expand button (previously just a passive banner). A "Collapse to
    clinic hours" link returns to the configured window.
  - **Missing profile**: when a location has no clinic profile, the
    Day view falls back to 07:00–20:00 and surfaces a subtle
    "Clinic hours not configured" notice pointing admins at the
    upcoming Clinic Settings page.
  - Implementation details: window snapping to 15-min boundaries +
    minimum 15-min window; per-hour labels drawn via computed offsets
    so arbitrary open/close minutes (e.g. 08:30–17:45) render
    correctly; auto-scroll now respects `startM`/`endM` changes when
    the user jumps days.

- **Clinic Profile service** (new `services/clinic_profile/`). Stores
  one profile per location (1:1 with `locations.id`) carrying clinic
  name, address line 1 / 2, city, state, postal code, country,
  primary & secondary phones, email, website, IANA timezone, free-
  form notes, and per-weekday hours of operation. Hours are modelled
  as a list of 7 `DayHours` (0 = Monday) each with `is_closed` and a
  list of `HoursInterval` (`open_time` / `close_time`, HH:MM 24-h) —
  an intervals list so lunch breaks and future holiday overrides can
  be layered in without a breaking change.
  - Endpoints at `/api/clinic-profiles/*`: list, read (by profile id
    OR location id), create (`POST`), update (`PUT`), delete
    (`DELETE`). Read is gated to `admin | doctor | staff`; mutations
    are `admin`-only.
  - Tenant-scoped on every call via `scoped_filter` + `stamp_for_write`;
    location-scoped for non-tenant-wide users. Cross-tenant probes
    return `404` (never `403`) so the endpoint never leaks existence.
  - Validation: HH:MM 24-hour format, `close > open` per interval, no
    overlapping intervals within a day, `is_closed` forbids intervals,
    exactly one entry per `day_of_week` 0..6, valid IANA `timezone`.
  - Audit rows: `clinic_profile.list_viewed`, `clinic_profile.read`,
    `clinic_profile.created`, `clinic_profile.updated` (with field
    list), `clinic_profile.deleted`. Every mutation also appends an
    in-document `history[]` entry.
  - Indexes: unique `(tenant_id, location_id)` + `(tenant_id, name)`.
  - Tests — `backend/tests/test_clinic_profile.py` — **6/6 green**:
    happy-path CRUD + two-interval lunch break, invalid hours
    (format / ordering / overlap / missing day / bad tz /
    `is_closed` + intervals), 409 on duplicate profile per location,
    doctor-can-read-not-write + scoped-staff-can't-see-other-location,
    Sunrise↔Default cross-tenant isolation, audit rows for
    create/update/delete.

- **Scheduling Day view rebuilt as a 15-minute timeline (Task 6).**
  The table-based DayView is replaced by a vertical timeline from
  07:00–20:00 (placeholder clinic hours; 52 slots × 16 px). Each slot
  is a focusable `<button>` — clicking opens the booking dialog
  pre-filled with that slot's start time (via the new `defaultStart`
  prop on `BookDialog`). Hour boundaries carry a darker 2 px border,
  half-hour marks are dashed, quarter-hour marks are subtle — so
  operators can read slot density at a glance.
  - Appointment blocks are absolutely positioned by
    `(start - dayStart) * slotHeight / 15` with a side-by-side column
    layout for overlapping clusters (classic interval scheduling on
    first-free column). Height respects duration with a
    `SLOT_HEIGHT - 2` minimum.
  - Blocks show **patient name, patient phone, start time**, and —
    when the block is tall enough — provider and reason. Cancelled
    appointments render in the destructive-soft palette with a
    line-through. Clicking a block opens the reschedule dialog.
  - **"Cancel appointment"** affordance reintroduced inside
    `BookDialog` in reschedule mode as a ghost-destructive footer
    button; clicking it closes the dialog and raises the existing
    `AlertDialog` confirmation. No new API.
  - A live **current-time indicator** (destructive pill + 2 px bar)
    overlays the timeline when viewing today and the clock is inside
    the visible window; updates every 60 seconds.
  - Timeline auto-scrolls to "now" on mount (or 08:00 on non-today
    days). An out-of-window banner surfaces any appointments that
    fall outside the default 07:00–20:00 window so they're never
    silently hidden.
- **`patient_phone` added to `AppointmentPublic`** (scheduling
  service). The hydration helper now pulls the patient's `phone`
  scalar alongside `first_name`/`last_name` in one Mongo read. Legacy
  records carry `phone` directly; grouped-intake records get it
  back-filled at write time (see PRD §21), so no new decryption path
  is needed. Only staff/doctor/admin + the patient themselves can
  reach the appointments endpoint, so no new audit surface either.

- **Scheduling Month view polish (Task 4)** — `MonthView` cells now
  show up to 2 compact appointment previews (time + patient) and a
  `+N more` hint when the day has more. Count badge remains in the
  cell header. Empty days stay visually calm with an en-dash. Today's
  date is rendered as a primary-filled pill. Adjacent-month filler
  cells are muted. Clicking any cell opens Day view for that date.
- **Scheduling Year view polish (Task 5)** — each day in every
  mini-month is now its own `<button>` that opens Day view for that
  date. Density tint has four buckets (0 / 1–2 / 3–4 / 5+) and the
  exact count is surfaced via `title` tooltip + `aria-label` for
  screen readers. The month header is now a separate `<button>` that
  jumps to Month view — avoiding the previous invalid nested-button
  structure. Per-month totals remain visible at the top-right of
  each card, so macro scanning still works at a glance.

- **Unified Scheduling module** — the separate `Appointments` table page
  and `Calendar` page are merged into a single `/scheduling` experience
  with Day / Week / Month / Year view toggles, shared date-navigation
  (`prev` / `today` / `next`), and a primary `+ New appointment` CTA.
  - Left-nav now shows one **Scheduling** item (icon `CalendarDays`)
    replacing the previous **Appointments** + **Calendar** entries.
  - Legacy routes `/appointments` and `/calendar` now redirect to
    `/scheduling` so bookmarks and deep links keep working.
  - Shared framework: `pages/scheduling/useScheduling.js` (view,
    date, visible range, provider filter placeholder, range-based
    appointment fetch with in-memory cache keyed by view/range,
    cache invalidation on write) + `pages/scheduling/dateHelpers.js`
    (Monday-first week math, month-grid expansion, label formatter)
    + `SchedulingToolbar`, `DayView`, `WeekView`, `MonthView`,
    `YearView`, `BookDialog`.
  - **Week view** renders a 7-day grid. Each cell shows weekday +
    date, a prominent count badge (`0` or `N appts`), up to three
    appointment previews, and a `+N more` link. Clicking the day
    header opens Day view for that date; clicking an appointment
    preview opens the reschedule dialog. Empty days render a dashed
    "No appointments" placeholder.
  - Month view is a Monday-first 6-row grid with per-day appointment
    count badges; clicking a cell opens Day view. Year view shows
    12 mini-month grids with per-day heat tint + per-month totals;
    clicking a month jumps to Month view.
  - Existing auth, permissions, audit, tenant scoping and appointment
    CRUD endpoints are untouched — the new views consume
    `GET /api/appointments?from=&to=` for range-based loading.

### Changed
- **Split patient wizard into two focused flows.** The previous
  4-step wizard mixed demographics, billing, clinical intake, and
  case/consents into a single form — confusing when reception just
  wanted to add a patient and returning staff just wanted to update
  intake.
  - **Add / Edit patient** — scope `"patient"`, visible steps 1–2
    only (Patient Info → Billing & Insurance). Used from the
    `/patients` page "+ New patient" action and the new
    **Edit patient** button on `PatientDetail.jsx`.
  - **Start / Edit intake** — scope `"intake"`, visible steps 3–4
    only (Clinical Intake → Case & Consents). Used from the new
    **Edit intake** button on `PatientDetail.jsx`. Edit-only — no
    "create" scenario for intake alone since intake lives on an
    existing patient record.
  - `PatientWizardDialog` now takes a `scope` prop (`"patient"`
    default or `"intake"`), dynamically titles the dialog (`"New
    patient"` / `"Edit patient"` / `"Edit intake"` / `"Start
    intake"`), counts steps within the visible slice ("Step 1 of 2"),
    and only runs hard validation on the patient scope. Intake scope
    allows partial saves — staff can return and complete later.
  - `PatientDetail.jsx` now renders both `PatientWizardDialog`
    instances with distinct open/close state
    (`editWizardOpen` + `intakeWizardOpen`), each keyed to its scope.
    Buttons carry matching `data-testid`s: `patient-edit-patient-btn`
    and `patient-edit-intake-btn`.
  - Draft autosave is only kept for the patient-scope **create**
    flow; intake and edit flows start from the server record with
    no local draft noise.

### Added
- **Patient Documents — inline thumbnails** for the three image-first
  categories (Insurance card front, Insurance card back, Driver's
  license / ID). `components/PatientDocumentsCard.jsx` now renders a
  `DocImageThumb` per image document that:
  - Streams the file over the authenticated
    `GET /api/patients/:id/documents/:id/download` endpoint (same
    path used for full download, which also emits an audit event).
  - Converts the blob response into a process-local
    `blob:` URL via `URL.createObjectURL`, renders it in an
    `<img loading="lazy" />`, and **revokes the URL on unmount** so
    no PHI lingers in memory or the browser tab's resource list.
  - Shows loading + error states (spinner; "Preview unavailable"
    fallback on fetch failure).
  - Wraps the image in a `<button>` with a visible focus ring so
    keyboard users can open the full-size view (re-uses the existing
    download helper → opens the authenticated blob in a new tab).
  - Falls back gracefully to a compact row when the stored file is
    not an image (e.g. PDF insurance card uploaded).
- The rest of the documents card (referral letter, imaging report,
  intake form, consent receipt, other) continues to use the compact
  row layout — image previews are reserved for categories where the
  visual scan-ability actually helps staff.
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
- **Theme Preview page (`/settings/theme-preview`)** — a one-screen
  regression canary that renders every Shadcn primitive in its
  default / hover / focus / disabled / error states alongside the
  full semantic token palette and the typography specimen (Outfit
  display · Manrope body · JetBrains Mono technical). Light · Dark ·
  System parity can be confirmed from a single URL. Source:
  `frontend/src/pages/ThemePreview.jsx`; wired into `App.js` behind
  the standard AppShell.
- **Card primitive density pass** — `CardHeader`, `CardContent`,
  `CardFooter` default padding tightened from 24px (`p-6`) to 20px
  (`p-5`) per spec §6 compact operational density. No visual
  regression on Dashboard / Appointments / Compliance KPI tiles.
- **Compat alias deletion** — removed the now-unreferenced backwards-
  compat layer from `index.css`:
    - all legacy utility classes (`.surface-app`, `.surface-raised`,
      `.surface-muted`, `.surface-sage`, `.surface-sage-soft`,
      `.surface-warning`, `.surface-danger-soft`, `.surface-topbar`,
      `.text-strong`, `.text-muted-strong`, `.text-soft`, `.text-sage`,
      `.text-sage-deep`, `.text-danger*`, `.text-warning`, `.bg-sage`,
      `.bg-danger`, `.hover\:bg-sage-hover`, `.hover\:bg-danger-hover`,
      `.border-subtle`, `.border-strong`);
    - all legacy CSS variables (`--surface-app`, `--surface-raised`,
      `--surface-muted`, `--surface-sage*`, `--warning-surface`,
      `--surface-danger-soft`, `--sage-accent*`, `--danger-accent`,
      `--warning-accent`, `--text-strong`, `--text-muted`,
      `--text-soft`, `--text-danger*`, `--border-subtle`,
      `--border-strong`, `--chrome-topbar-bg`).
  - Re-pointed internal alias tokens (`--sidebar-fg`,
    `--sidebar-active-fg`, `--table-header-fg`) and the `::selection`
    color to the canonical `hsl(var(--foreground))` /
    `hsl(var(--muted-foreground))` references.
  - Result: `index.css` is ~40% smaller and speaks exactly one
    vocabulary — foundation primitives → semantic tokens → component
    aliases → three essential utilities (`font-display`, `font-body`,
    `font-mono`, `focus-ring`, `tabular-nums`).
- **Phase 3 — legacy alias retirement (2026-04-20)** — migrated every
  backwards-compat utility class across `frontend/src/**` to direct
  semantic Tailwind utilities. 762 instances swept in one atomic
  pass using word-boundary sed replacements:
    - `text-strong` → `text-foreground` (88×)
    - `text-muted-strong` → `text-muted-foreground` (215×)
    - `text-soft` → `text-muted-foreground/70` (26×)
    - `text-sage-deep`, `text-sage` → `text-primary` (53×)
    - `text-danger-strong`, `text-danger-soft`, `text-danger` → `text-destructive` (34×)
    - `surface-sage` → `bg-primary/10` (38×)
    - `surface-sage-soft` → `bg-primary/5` (1×)
    - `surface-muted` → `bg-muted` (26×)
    - `surface-app` → `bg-background` (21×)
    - `surface-warning` → `bg-warning-soft` (16×)
    - `surface-danger-soft` → `bg-destructive-soft` (21×)
    - `surface-topbar` → `bg-card/90 backdrop-blur` (1×)
    - `bg-sage`, `hover:bg-sage-hover`, `bg-danger`, `hover:bg-danger-hover`
      → `bg-primary`, `hover:bg-[var(--primary-hover)]`, `bg-destructive`,
      `hover:brightness-95` (79× combined)
    - `border-subtle` → `border-border` (102×)
    - `border-strong` → `border-border-strong` (14×)
  The only non-semantic raw strings still in feature code are the
  `--primary-hover`, `--dialog-overlay`, `--sidebar-active-*`,
  `--table-*`, `--badge-premium-*`, `--focus`, `--input-placeholder`,
  and `--calendar-slot-selected` CSS-variable references exposed by
  the theme layer itself. These are intentional — they consume alias
  tokens.

- **AppShell shell hardening** — Sidebar now reads the sidebar alias
  tokens (`--sidebar-bg`, `--sidebar-fg`, `--sidebar-active-bg`,
  `--sidebar-active-fg`, `--sidebar-active-indicator`) instead of
  inline `style={{ borderLeftColor: "var(--sage-accent)" }}` or
  generic `bg-background` / `bg-muted`. `font-['Outfit']` arbitrary
  classes migrated to the `font-display` utility. `text-white` on
  primary surfaces swapped for `text-primary-foreground` so dark-mode
  contrast stays correct.

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
