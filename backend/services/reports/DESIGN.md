# Reports module — design & discovery

## Discovery findings (reused, not reinvented)

| Concern               | Reused from                                        |
| --------------------- | -------------------------------------------------- |
| Tenant isolation      | `core.tenancy` (`TenantContext`, `location_filter`) |
| Row scoping           | `core.tenant_scope.scoped_filter`                  |
| Permissions           | `services.authz.policy.require_permission`         |
| Audit                 | `core.audit.audit_success` / `audit_failure`       |
| Result caching        | `core.tenant_cache.TenantCache` (`t:{tid}:report:*`) |
| Background export job | `core.tenant_jobs` + `services.exports`            |
| Signed downloads      | `services.exports.download_export`                 |

## Report categories (driven by existing modules)

* **Operational** — `appointments`, `scheduling`, `patients`
* **Clinical** — `clinical_*` (encounters, notes, exams, treatment plans)
* **Financial** — `billing` (invoices, claims, payments, denials, AR aging)
* **Compliance** — `audit_logs`, `clinical_audit_events`, workforce licenses

## Initial reports delivered (P0)

1. `appointments_list` — Operational, optional PHI (patient name/phone)
2. `provider_productivity` — Operational, no PHI
3. `patient_roster` — Operational, **PHI**
4. `unsigned_clinical_notes` — Clinical, PHI (patient id)
5. `claims_list` — Financial
6. `invoices_list` — Financial
7. `payments_received` — Financial
8. `denials_log` — Financial
9. `audit_activity` — Compliance
10. `license_expiration` — Compliance (NPI/DEA expiry tracking)

## Deferred (P1/P2)

* Patient encounter volume by referral source (no source data captured yet)
* Revenue cycle KPIs (days-in-AR, net collection rate) — richer analytics
* Patient appointment adherence (no-show / cancellation %) — need richer attendance stats

## Password-protected exports (HIPAA)

When a report is flagged `contains_phi=True`, the generated CSV/XLSX/PDF is
wrapped in an **AES-256 password-protected ZIP** (pyzipper). The one-time
password is:
* Generated server-side (24 chars, URL-safe)
* Stored **hashed** on the export row (`password_hash`, sha256)
* Returned to the requester ONCE in the `/exports/{id}` polling response
  when status becomes `ready`
* Never logged, never emailed, never persisted in plaintext

Non-PHI reports download as plain CSV/XLSX/PDF without the zip layer.

## Saved views

Persisted in collection `report_saved_views` with:
`{id, tenant_id, report_name, owner_user_id, name, is_shared, is_default,
  columns, filters, sort, created_at, updated_at}`.

Owner and admins can toggle `is_shared=True` so the view appears in the
"shared views" section for every user of that tenant. `is_default=True`
is scoped per-user per-report (only one default at a time; setting a new
one clears the previous).
