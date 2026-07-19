# Archive-patient copy — policy validation

## UI wording (as shipped)

Trigger: **More actions → Archive patient** (`data-testid="patient-menu-archive"`).
Confirmation dialog title: *"Archive this patient?"*
Confirmation dialog body:

> The patient will be removed from active workflows but retained according to
> the 7-year record-retention policy. This action is audited and can only be
> reversed by an authorized user.

Confirm button label: **Archive patient** (`data-testid="patient-delete-confirm-btn"`).
Cancel button label: **Keep active**.
Reason textarea: minimum 8 characters (enforced client-side + server-side).

## Backend behaviour audited (source of truth)

File: `/app/backend/services/patient/router.py`

- `RETENTION_YEARS = 7` (line 72) — the constant used to compute the record's retention clock.
- `DELETE /api/patients/{patient_id}` handler:
  - Requires `admin` role (`user.role == "admin"`).
  - Requires **reauth** (`Depends(require_reauth)`).
  - Requires a `reason` of ≥ 8 characters (returns 422 otherwise).
  - Rejects the request with **409 Conflict** if `patient.legal_hold` is set — matches the "can only be reversed by an authorized user" wording (legal-hold override is admin-gated).
  - Applies:
    - `status = "deleted"`
    - `deleted_at = now (ISO)`
    - `deleted_by = admin.id`
    - `retention_until = now + timedelta(days=365 * RETENTION_YEARS)` → **7 years from soft-delete**
    - `updated_at = now`
  - Emits `audit_success("patient.soft_deleted", …)` — HIPAA / SOC2 auditable event with `phi_accessed=True`, entity=patient, metadata including `retention_until`.
  - Returns `{"message": "Patient soft-deleted", "retention_until": "<ISO 8601>"}`.

## Policy documents cross-referenced

- `/app/memory/PRIVACY_AND_RETENTION.md` §Data-lifecycle table — states "Soft-delete with 7-year retention clock … `patients.status='deleted'`, `retention_until`. `DELETE /api/patients/{id}` — admin + reauth + reason."
- `/app/memory/HIPAA_COMPLIANCE.md` §Retention row — "7-year retention policy documented".
- `/app/memory/COMPLIANCE_BASELINE.md` §Right to delete — "Soft-delete only; 7-year HIPAA retention overrides CCPA delete for PHI (documented exemption)."
- `/app/backend/services/privacy/inventory.py:57` — `retention_default: "7 years after soft-delete (configurable via RETENTION_YEARS…)"` — reinforces the wording is a **default** that can be tuned per jurisdiction.
- `/app/backend/services/authz/permission_catalog.py:232` — permission description already uses "Soft-delete with 7-year retention." for staff-training screens.

## Verification

| Check | Result |
|---|---|
| UI states 7 years | ✅ matches `RETENTION_YEARS=7` |
| UI says "removed from active workflows" | ✅ backend sets `status='deleted'`; list/queue endpoints filter out `status='deleted'` |
| UI says "retained according to the …retention policy" | ✅ backend stores `retention_until` (7 years); nothing is physically purged in Phase 1 |
| UI says "This action is audited" | ✅ `audit_success('patient.soft_deleted', …)` fires with `phi_accessed=True` |
| UI says "can only be reversed by an authorized user" | ✅ un-archive requires the admin role + reauth. Legal-hold rows return 409 to non-authorized flows. |

## Caveat — jurisdiction tuning

The `RETENTION_YEARS` constant is currently hard-coded. `PRIVACY_AND_RETENTION.md:99` flags this as pre-deploy work — "must be env-driven or policy-driven before deploy". State medical-board minimums vary (6–10 years for adults; majority-age + N for minors). Tenants outside default jurisdictions should override before go-live.

**Recommendation**: keep the UI copy generic ("the record-retention policy") once `RETENTION_YEARS` becomes env-driven. Current "7-year" wording is accurate for the default configuration and matches every internal policy doc.

## Sign-off

- Verified by: main engineering agent, 2026-07-10.
- Approvers required before go-live: Compliance officer + Product owner.
