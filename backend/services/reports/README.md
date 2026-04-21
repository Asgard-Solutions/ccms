# Reports module

This directory implements the CCMS Reports section — tenant-scoped,
permission-aware, HIPAA-aware reporting with CSV / XLSX / PDF export.

## Layout

```
services/reports/
├── __init__.py         # public API + import side-effects that register all reports
├── definitions.py      # framework: ReportDefinition, Column, Filter, QueryContext…
├── builtin.py          # initial 10 reports (Phase 1)
├── builtin_extra.py    # +12 reports (Phase 3)
├── export_writer.py    # CSV / XLSX / PDF writers + PDF native encryption + AES-256 ZIP
├── views.py            # saved-view CRUD with column whitelist validation
├── router.py           # FastAPI router mounted at /api/reports
└── DESIGN.md           # discovery note + initial design decisions
```

## Flow

```text
┌───────────────┐  POST /reports/{name}/run     ┌──────────────────┐
│ Frontend      │ ─────────────────────────▶    │ router.run()     │
│ ReportViewer  │                               │  permission gate │
└───────┬───────┘                               │  tenant scope    │
        │ rows + columns + aggregates           │  TenantCache     │
        │◀──────────────────────────────────────│  audit.success   │
        │                                       └──────────────────┘
        │
        │ POST /reports/{name}/export
        │    (fmt=csv|excel|pdf, reason?)
        ▼
┌───────────────┐          ┌──────────────────┐     ┌────────────────────┐
│ services/     │ enqueue  │ export.generate_ │ →   │ export_writer.     │
│ exports       │────▶     │ report tenant_job │     │ build_export()     │
└───────┬───────┘          └──────────────────┘     │  CSV/XLSX/PDF      │
        │                                            │  native PDF enc.   │
        │ status + signed token                      │  AES-256 ZIP wrap  │
        ▼                                            └────────┬───────────┘
┌───────────────┐                                             │
│ GET /exports/ │  one-time password reveal                   │
│   {id}/...    │◀────────────────────────────────────────────┘
└───────────────┘
```

## Adding a new report

1. Define `_run_<name>` that takes a `QueryContext` and returns `RunResult`.
2. `register(ReportDefinition(...))` at module import time.
3. Decide:
   * **Category** — Operational / Clinical / Financial / Compliance / Patient / Scheduling / Workforce.
   * **`contains_phi`** — set `True` if *any* column in row output can carry
     patient-identifying data.
   * **`required_permission`** — one of `reporting.read`,
     `reporting.read_clinical`, `reporting.read_financial`, `audit_log.read`.
4. For patient/appointment-centric reports, call `_base_filter(qc,
   location_scoped=True)` to honour tenant + assigned-location scope.
5. Add the module to the top-level import in `__init__.py` if you put it
   in a new file.
6. Add a catalog test stanza — the parametrised suite in
   `tests/test_reports_catalog.py` automatically verifies the new report's
   contract and runner.

## Permission model

| Action                           | Permission                   | MFA  |
| -------------------------------- | ---------------------------- | ---- |
| List catalog                     | `reporting.read`             | —    |
| Run a non-PHI report             | `reporting.read`             | —    |
| Run a financial/PHI report       | `reporting.read_financial`   | —    |
| Run a clinical/PHI report        | `reporting.read_clinical`    | —    |
| Request any export               | `reporting.export`           | Yes  |
| Request a PHI export             | `reporting.export_phi`       | Yes  |
| View audit-based reports         | `audit_log.read`             | —    |

The catalog endpoint filters reports to only those the caller is allowed
to see. Every single endpoint is also gated by `core.tenancy` — platform
admins may read cross-tenant, all other users are clamped to their
`tenant_id` and (when relevant) `allowed_location_ids`.

## PHI protection rules (exports)

| Format  | Protection kind   | How we encrypt                                        |
| ------- | ----------------- | ----------------------------------------------------- |
| PDF     | `pdf_native`      | reportlab StandardEncryption (AES-128 per PDF spec)   |
| XLSX    | `aes_zip`         | pyzipper AES-256 password-protected ZIP wrapper       |
| CSV     | `aes_zip`         | pyzipper AES-256 password-protected ZIP wrapper       |
| Any     | `none`            | Plain file, when the report is not `contains_phi`     |

**Password lifecycle**

1. Generated server-side with 20 URL-safe characters (~120 bits entropy).
2. Stored only as AES-GCM ciphertext (`password_enc`) + SHA-256 hash
   (`password_hash`). Plaintext is **never** persisted in cleartext in
   the database, in logs, or in audit metadata.
3. Decrypted exactly twice:
   * By the background worker to drive the file encryption step.
   * By the *requester's* first successful poll of `/api/exports/{id}` —
     returned in `one_time_password`, then `password_enc` is
     `$unset` from the row so it cannot be replayed.
4. The file itself expires after `EXPORT_TTL_HOURS` and is purged by the
   `services.exports.cleanup_expired_exports` job.

A file is only ever labelled `password_protected=True` when its bytes on
disk genuinely require the password to read. `protection_kind` makes
the technique explicit for downstream consumers.

## Downloads

Downloads go through `GET /api/exports/{id}/download?token=...`. The
token is a short-lived (15-min) HMAC binding `(export_id, tenant_id,
user_id)`. No public URLs; no unscoped links.

## Audit events

| Event                        | Emitted when                                    |
| ---------------------------- | ----------------------------------------------- |
| `report.generated`           | A user runs a report                            |
| `report.export_requested`    | Export job queued                               |
| `report.export_denied`       | PHI export blocked by policy                    |
| `report.export_generated`    | Worker wrote the file (includes protection_kind)|
| `export.password_revealed`   | One-time password surfaced to the requester     |
| `export.downloaded`          | Signed-token download consumed                  |
| `report.view_created/updated/deleted` | Saved-view CRUD by any user            |

None of these events include plaintext passwords — the hashes and the
`protection_kind` are sufficient to reconstruct "was this protected, and
how?" for compliance review.

## Operational notes

* Export files live under `/data/exports/{tenant_id}/`. Permissions:
  `0700` on the directory, `0600` on the files.
* `services.exports.cleanup_expired_exports` (tenant job) sweeps past
  `expires_at` rows and deletes the on-disk artifact.
* The report result cache (`core.tenant_cache.TenantCache`) keys per
  `(tenant_id, report, filters, sort, page, user_id)`. TTL is per
  definition (`cache_ttl_seconds`, default 300s).

### Deferred reports (need data not yet captured)

Reports whose source data is not yet captured anywhere in the app:

* Referral source — no referral field on patient/appointment models
* Birthday / age cohort — shipped via `patient_age_cohort`
* Patient insurance coverage summary — shipped via
  `patient_responsibility_summary` (self-pay/insurance/mixed grouping).
  A richer carrier-level breakdown will land when the embedded
  `insurance` block on patients is promoted to a first-class
  `insurance_policies` collection.
* Appointment-type volume — blocked on the P2 task to persist
  `appointment_type_id` on appointments (currently only UI prefill).
* Open time slots / availability summary — requires a provider schedule
  / availability model that doesn't exist yet.
* Patient communication consent — `comm_preferences` is tracked in
  `services.privacy` audit events but not stored per-patient yet.
* Provider schedule utilisation (% of available hours booked) — same
  availability-model gap as "open time slots".
* Collections summary (external collections status) — no collections
  module yet.

When these data sources land, adding the report is a ~40-line file
using the patterns already in `builtin.py` / `builtin_extra.py` /
`builtin_extra_2.py`.
