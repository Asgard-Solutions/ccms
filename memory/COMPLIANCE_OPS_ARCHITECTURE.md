# CCMS — Compliance Operations Backbone

**Last updated:** 2026-02-21

This document describes the compliance operating system inside the CCMS product — the data model, workflows, and admin dashboard that supports HIPAA technical safeguards, SOC 2 evidence collection, CCPA/CPRA privacy operations, and ISO 27001 control management.

> **Scope caveat.** This backbone creates the *technical* foundation for those compliance programs. It does **not** by itself constitute legal compliance, a certification, workforce training, contractual controls, or policy review outside the system. Those remain the job of your security/compliance team; the goal here is that they never have to reconstruct evidence by hand.

## 1. Model overview

All compliance entities share a common shape:

```
{id, tenant_id, type, status, owner_user_id,
 created_at, updated_at, history:[{at, actor_id, actor_email, action, note}],
 …type-specific fields}
```

- **Tenant-scoped.** Every row carries `tenant_id` and routes through `TenantScopedRepository`. Default-deny at the query layer.
- **History as audit trail.** Every mutation (`created`, `status_change`, `fields_patched`, `legal_hold_toggle`) appends a history entry. The top-level `audit_logs` collection also receives a semantic row (`compliance.control_created`, `compliance.evidence_legal_hold`, …) so external auditors can query either surface.
- **Field allow-list for patches.** `_WRITABLE_FIELDS` explicitly lists the fields that clients can mutate per entity. Anything not on the list (especially `integrity_sha256`, `history`, `tenant_id`, `id`) is rejected with a 400. That means even a compromised admin account cannot silently alter integrity metadata.

### 1.1 Entities

| Type             | Collection                     | Lifecycle                                                  |
|------------------|--------------------------------|-------------------------------------------------------------|
| `control`        | `compliance_controls`          | planned → in_progress → implemented → needs_review → (exception_approved / retired) |
| `evidence`       | `compliance_evidence`          | append-only; `legal_hold` togglable (MFA-gated)             |
| `risk`           | `compliance_risks`             | open → mitigating → mitigated / accepted / transferred / closed |
| `policy`         | `compliance_policies`          | draft → approved → retired                                  |
| `incident`       | `compliance_incidents`         | triage → investigating → contained → eradicated → recovered → closed |
| `vendor`         | `compliance_vendors`           | under_review → active → terminated                          |
| `data_class`     | `compliance_data_classes`      | (no workflow; catalog only)                                 |
| `access_review`  | `compliance_access_reviews`    | scheduled → in_progress → complete (auto-overdue when due_at < now) |

## 2. Control registry

### 2.1 Framework mappings

A single control row maps to ≥1 frameworks via `framework_mappings`:

```json
{
  "name": "Audit log completeness for PHI access",
  "framework_mappings": {
    "HIPAA":    ["164.312(b)"],
    "SOC2":     ["CC4.1", "CC7.2"],
    "ISO27001": ["A.12.4"]
  }
}
```

Mapping keys are free-form strings on purpose — future frameworks (NIST CSF, PCI, HITRUST) just need a new key. The `?framework=HIPAA` query filter uses a `$exists` probe against `framework_mappings.<key>`, so adding a framework is zero schema work.

### 2.2 Seed controls (representative — not exhaustive)

| Control                                          | Family            | Frameworks |
|--------------------------------------------------|-------------------|------------|
| Unique user IDs + MFA for admin roles            | access_control    | HIPAA 164.312(a)(2)(i), 164.312(d) · SOC2 CC6.1/CC6.2 · ISO A.9.2/A.9.4 |
| Audit log completeness for PHI access            | audit             | HIPAA 164.312(b) · SOC2 CC4.1/CC7.2 · ISO A.12.4 |
| Row-level tenant isolation                        | access_control    | SOC2 CC6.1/CC6.6 · ISO A.9.4/A.13.2 · HIPAA 164.312(a)(1) |
| Encryption at rest + in transit                  | cryptography      | HIPAA 164.312(a)(2)(iv), 164.312(e)(1) · SOC2 CC6.7 · ISO A.10.1/A.13.1 |
| Automated backups + restore tests                | backup            | HIPAA 164.308(a)(7) · SOC2 A1.2/A1.3 · ISO A.12.3/A.17 |
| Incident response plan + tabletop drills         | incident_response | HIPAA 164.308(a)(6) · SOC2 CC7.3/CC7.4 · ISO A.16.1 |
| Privacy request handling (CCPA/CPRA)             | privacy           | CCPA 1798.100/105/106/110 · SOC2 P3.1 · ISO A.18.1 |

## 3. Evidence

### 3.1 Integrity

Every evidence row carries `integrity_sha256 = sha256(source_system | source_reference | content_summary | period_start | period_end)`. This is computed at creation time inside the server; the client cannot forge it.

Patches to evidence are deliberately restricted: only `content_summary` and `access_restriction` are editable. Integrity-relevant fields (`integrity_sha256`, `source_reference`, `coverage_period_*`, `generated_at`, `history`) are rejected by the field allow-list. The only way to "rotate" an evidence row is to create a new one.

### 3.2 Legal hold

`POST /api/compliance-ops/evidence/{id}/legal-hold?on=true|false` is gated by `reporting.export` (MFA-required). When on, retention sweepers must skip the row.

### 3.3 Evidence sources (automated wiring candidates)

| Source                   | Audit event / surface                               |
|--------------------------|-----------------------------------------------------|
| `audit_log`              | `audit_logs` with `phi_accessed=true`               |
| `access_review`          | `compliance_access_reviews` completed rows          |
| `config_snapshot`        | `/api/infra/replica`, `/api/infra/secrets` diffs    |
| `backup_test`            | DR runbook drill record                             |
| `security_alert`         | Prometheus alert history                            |
| `export_log`             | `audit_logs action=export.downloaded`               |
| `key_rotation`           | `audit_logs action=security.key_rotated`            |
| `secret_rotation`        | `audit_logs action=security.secret_rotated`         |
| `dr_exercise`            | incident drill records                              |
| `policy_attestation`     | `compliance_policies` approved rows                 |
| `vendor_review`          | `compliance_vendors.last_reviewed_at` diffs         |
| `manual_upload`          | operator-uploaded PDF/CSV via `TenantStorage`       |

## 4. HIPAA safeguards backbone — mapped to the product

| 45 CFR citation        | Product mechanism                                            |
|------------------------|---------------------------------------------------------------|
| 164.308(a)(1) Risk Mgmt| `compliance_risks` register with likelihood×impact           |
| 164.308(a)(3) Workforce| `compliance_access_reviews` + revocation path                |
| 164.308(a)(5) Training | *Out of scope* — use `policy_attestation` evidence uploads   |
| 164.308(a)(6) IR       | `compliance_incidents` with drill records                    |
| 164.308(a)(7) Contingency| backup + DR control + `backup_test` evidence                |
| 164.308(a)(8) Evaluation| quarterly control review via `review_cadence_days`         |
| 164.312(a)(1) Access   | row-level tenant isolation control                           |
| 164.312(a)(2)(i)       | unique user id control + MFA                                 |
| 164.312(a)(2)(ii) ERA  | **break-glass** workflow: `authz/elevation_requests` + `audit_emergency` |
| 164.312(a)(2)(iv) Encryption| AES-256 encrypted fields on patients/notes             |
| 164.312(b) Audit       | `audit_logs` with tenant+actor+IP+user-agent                 |
| 164.312(c) Integrity   | evidence `integrity_sha256` + soft-delete on patients        |
| 164.312(d) Authentication| MFA-required-on-role + session_epoch                       |
| 164.312(e) Transmission| TLS 1.3 edge + rediss/TLS-Mongo transport                    |

## 5. SOC 2 — Trust Services Criteria coverage

- **Security (Common Criteria)** — implemented through authz (CC6.1-CC6.8), audit (CC4.1/CC7.2), change management evidence (CC8.1), system description data model.
- **Availability** — backup/DR control with `backup_test` evidence + incident records + replica health monitoring.
- **Confidentiality** — encryption control + tenant isolation + export field-redaction.
- **Privacy** — CCPA/CPRA privacy request pipeline + consent records + data inventory.

### 5.1 Recurring activities

| Cadence      | Activity                                  | System driver                     |
|--------------|-------------------------------------------|-----------------------------------|
| Monthly      | user access review                        | `POST /compliance-ops/access-reviews` |
| Monthly      | privileged-access review                  | same, `scope=platform_admins`     |
| Quarterly    | incident response exercise                | `compliance_incidents` tabletop   |
| Quarterly    | backup/restore validation                 | `backup_test` evidence            |
| Annually     | vendor review                             | `vendor.next_review_at`           |
| Annually     | policy review                             | `policy.review_date`              |
| Ad-hoc       | vulnerability remediation                 | `audit_logs action=security.vuln_*` |

## 6. CCPA/CPRA privacy operations

Privacy requests live in the existing `services/privacy/` service. The compliance-ops dashboard surfaces pending counts. Workflow states:

```
received → verifying → processing → fulfilled
                     ↘ denied (with reason + retention override)
```

Every privacy request:
- Records requester identity-verification outcome in the audit log.
- Runs through `TenantStorage.put(..., StorageCategory.EXPORTS)` for the "access" fulfillment package with a 15-minute signed download URL.
- Honours HIPAA/medical retention overrides: deletion is a *soft-delete* with `retention_until` set from the `data_classes` row for that data type, EXCEPT when the tenant has a legal hold on the record (`legal_hold=true`).
- Never crosses tenant boundaries — the requester is identified by `(tenant_id, subject_user_id)`.

## 7. Dashboard (`GET /api/compliance-ops/dashboard`)

Single JSON response feeds the platform admin UI:

```json
{
  "controls": {"total": 7, "planned": 1, "needs_review": 0},
  "risks": {"open": 2, "accepted": 0, "high_severity_open": 0},
  "incidents": {"open": 0, "high_severity_open": 0},
  "policies": {"overdue": 1},
  "vendors": {"baa_missing": 1, "review_due": 0},
  "access_reviews": {"scheduled": 2, "overdue": 1},
  "privacy_requests": {"pending": 0},
  "evidence": {"total": 1, "last_90_days": 1},
  "generated_at": "2026-02-21T03:03:37Z"
}
```

Gated by `reporting.read`, tenant-scoped, always audited (`compliance.dashboard_viewed`).

## 8. Evidence bundle export

A compliance operator exports a time-bounded evidence pack via the existing `services/exports` pipeline — type `compliance_evidence_bundle` (to be added when needed). The flow:

1. `POST /api/exports {type: "compliance_evidence_bundle", filters: {period_start, period_end, control_ids}}`
2. Background job `export.generate` runs `_export_compliance_evidence(ctx, filters, include_phi)` which:
   - Pulls matching evidence rows (integrity hashes included).
   - Serializes to CSV under `/app/data/storage/exports/<tenant_id>/<uuid>.csv`.
   - Flips export status to `ready`.
3. 15-minute signed JWT download URL issued; download is audited via `export.downloaded`.

Every export carries the control+evidence rows *with their integrity hashes*, so an auditor can independently recompute the hash and verify the row was not tampered post-generation.

## 9. Tests (iteration 18)

`/app/backend/tests/test_iteration18_compliance_ops.py` — **10/10 pass**:

- Dashboard reflects seed (controls, overdue policies, BAA-missing vendors, overdue access reviews, evidence).
- Control list filters by framework (`?framework=HIPAA`).
- Multi-framework mapping roundtrip: HIPAA+SOC2+ISO27001+CCPA on a single control.
- Evidence `integrity_sha256` is 64-char hex + `legal_hold` toggle works (MFA-gated).
- Evidence patches that attempt to mutate `integrity_sha256` or `history` return 400 ("not editable").
- Cross-tenant risk creation — default admin cannot see Sunrise risk ids.
- Overdue access reviews auto-flagged when `due_at < now`.
- Incident status transition appends a `status_change` history entry on top of `created`.
- Unknown entity type → 404 on patch.
- Seeded Twilio vendor with `baa_required=true, baa_in_place=false` is counted in `dashboard.vendors.baa_missing`.

## 10. What is NOT in this iteration

- **Soft-delete orchestrator.** Retention sweeper needs a tenant-scoped background job that reads `compliance_data_classes` and prunes expired rows. Scaffolding for this exists via `core.tenant_jobs`; a `retention.sweep` job type + Mongo update aligned to `data_classes.retention_days` is a single file for a follow-up iteration.
- **Vendor BAA document attachment.** Path via `TenantStorage.put(StorageCategory.PERMANENT, …)` with the artifact id stored on the vendor row. Again, ~30 lines when needed.
- **Automated evidence ingestion from CI/monitoring.** The evidence API is ready; CI-side hooks (GitHub Action, Prometheus webhook) post to `POST /api/compliance-ops/evidence` when a drill / rotation / scan completes.
- **Physical document acknowledgments + workforce training records.** Out of scope for the product; can live in a vendor-provided LMS or be ingested via `manual_upload` evidence rows.

## 11. How to add a new control domain

1. Add a Pydantic model (`XPublic`, `XCreate`) to `services/compliance_ops/__init__.py`.
2. Add a `_Repo("compliance_x")` line in `router.py` + register in `ENTITY_TO_REPO`.
3. Add a `_WRITABLE_FIELDS["x"]` allow-list.
4. Add endpoint `POST /compliance-ops/x` + `GET /compliance-ops/x`.
5. Add seed rows in `seed.py`.
6. Add an assertion to `test_iteration18_compliance_ops.py::test_dashboard_reflects_seed`.
7. Update this document.

The generic `POST /{entity_type}/{id}/status`, `PATCH /{entity_type}/{id}`, and `GET /{entity_type}/{id}` endpoints work automatically once the entity is registered.
