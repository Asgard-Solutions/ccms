# CCMS Compliance Baseline — SOC 2 / CCPA / ISO 27001 Readiness

**Last updated:** 2026-02-18
**Document owner:** Engineering (this file) + future Security Officer (to be assigned, out of app scope)
**Status:** Baseline / readiness snapshot only

---

## 0. Purpose and scope

This document establishes the **technical and architectural baseline** for future audit readiness of the Chiropractic Clinic Management System (CCMS) against three frameworks:

- **SOC 2** — Trust Services Criteria (Security, Availability, Processing Integrity, Confidentiality, Privacy)
- **CCPA** — California Consumer Privacy Act data-rights obligations
- **ISO/IEC 27001:2022** — Information Security Management System control domains (Annex A)

### What this document IS
- A structured review of what the application already implements
- A clear separation of **in-app controls** vs **infrastructure / operational / legal controls**
- An honest labelling of status per control area
- A foundation for the control inventory (`CONTROL_INVENTORY.md`) and the engineering backlog (`COMPLIANCE_BACKLOG.md`)

### What this document IS NOT
- A certification claim. CCMS is **NOT** SOC 2 audited, **NOT** CCPA-verified, and **NOT** ISO 27001 certified.
- A legal attestation. Certification requires independent auditors, organisational governance, policies, contracts, and evidence over time — none of which a codebase alone can produce.
- A HIPAA document. See `HIPAA_COMPLIANCE.md` for the existing HIPAA-specific safeguard inventory. This file is complementary.

### Scope of review
- Backend: `/app/backend/` (FastAPI gateway + microservice routers + `core/` cross-cutting concerns)
- Frontend: `/app/frontend/` (React SPA, cookie-based session, idle timeout, break-glass UX)
- Data layer: MongoDB (write-primary + read-preferred pattern, PostgreSQL-ready schema) + Redis (cache + rate-limit)
- Event layer: in-process event bus
- Documentation already in `/app/memory/`

---

## 1. Legend — status labels

| Label | Meaning |
|---|---|
| **Implemented** | Enforced in code today, verifiable via tests or inspection |
| **Partially Implemented** | Some aspects are enforced; others are placeholders or operational |
| **Not Implemented** | Not in the codebase; must be added to reach readiness |
| **Out of App Scope** | Must exist, but belongs to infrastructure, legal, HR, or operational governance — not to this repo |

---

## 2. SOC 2 — Trust Services Criteria mapping

### 2.1 Security (Common Criteria)

| TSC area | Control | Status | Evidence / Notes |
|---|---|---|---|
| CC6.1 Logical access | Unique user identification (UUIDs, unique email) | Implemented | `users.email` unique index; UUID PKs |
| CC6.1 | RBAC on every PHI-touching route | Implemented | `core/deps.py::require_role`; roles: admin / doctor / staff / patient |
| CC6.1 | MFA (TOTP + backup codes) | Implemented | `core/mfa.py`, enforced challenge on login when enabled |
| CC6.1 | Password policy + history + rotation | Implemented | `core/password_policy.py` (12-char complexity, last-5 history, 120-day hard expiry) |
| CC6.1 | Account lockout / brute-force | Implemented | 5 failed logins / email → 15-min lockout |
| CC6.1 | Account disable (not hard delete) | Implemented | `POST /api/auth/users/{id}/disable` |
| CC6.1 | Step-up re-authentication for sensitive ops | Implemented | `core/reauth.py`; required for delete-patient + add-medical-record |
| CC6.2 Provisioning | Admin-driven user create / role change | Implemented | `/api/auth/users`, `PATCH /api/auth/users/{id}` (cache invalidation on role change) |
| CC6.2 | Periodic access review | Not Implemented | No scheduled review report; added to backlog |
| CC6.3 Removal | Account disable on termination | Partially Implemented | Manual admin action; no HRIS-triggered flow |
| CC6.6 External threats | WAF / DDoS protection | Out of App Scope | Ingress responsibility |
| CC6.7 Transmission | TLS enforcement | Partially Implemented | App uses Secure cookies + SameSite=None; TLS termination is ingress-layer |
| CC6.8 Malicious software | Dependency scanning, SCA | Not Implemented | No automated SCA/SAST in CI; added to backlog |
| CC7.1 System monitoring | Structured logging, Prometheus metrics | Implemented | `core/metrics.py`, `/api/metrics` endpoint, request-duration histogram |
| CC7.1 | Anomaly detection on auth | Partially Implemented | Lockouts logged; no alerting pipeline |
| CC7.2 Incident response | Runbook + notification procedure | Out of App Scope | Organisational; documented placeholder in backlog |
| CC7.3 Evaluation | Security assessments / pentest | Out of App Scope | Planned before go-live |
| CC7.4 Recovery | Backup + DR tested | Out of App Scope | Infra responsibility |
| CC8.1 Change management | Code review, change tracking | Partially Implemented | Git history exists; no mandated PR review rule in-repo |
| CC9.1 Risk mitigation | Risk assessment | Out of App Scope | Annual organisational process |
| CC9.2 Vendors | Vendor risk review, BAAs | Out of App Scope | Legal / procurement |

### 2.2 Availability

| Control | Status | Evidence |
|---|---|---|
| Graceful degradation when Redis down | Implemented | `core/redis_client.py::safe_call` — cache bypass + in-process rate-limit fallback |
| Primary/replica DB split ready | Implemented | `core/db.py` (`get_db_write` / `get_db_read` / `read_after_write_db`) |
| Health endpoint | Implemented | `GET /api/health` |
| Metrics exposed for SLO tracking | Implemented | `/api/metrics` Prometheus histogram |
| Backup + DR | Out of App Scope | Infra |
| Multi-region failover | Out of App Scope | Infra |
| Capacity planning | Out of App Scope | Ops |

### 2.3 Processing Integrity

| Control | Status | Evidence |
|---|---|---|
| Input validation (Pydantic models) | Implemented | Every router uses Pydantic request models |
| Appointment conflict detection on primary | Implemented | Services/scheduling: conflict check reads from write client to avoid replica-lag double-booking |
| Read-after-write for UI consistency | Implemented | `core/db.py::read_after_write_db()` used on PUT/Cancel responses |
| End-to-end transaction integrity | Partially Implemented | Mongo single-document atomicity only — no multi-doc transactions |
| Data quality monitoring | Not Implemented | No reconciliation jobs; added to backlog |

### 2.4 Confidentiality

| Control | Status | Evidence |
|---|---|---|
| Field-level encryption at rest (AES-256-GCM) | Implemented | `core/crypto.py` — patients, medical_records, appointments PHI fields |
| PHI masking by default on list/detail | Implemented | `core/masking.py` — unmask is admin-only + audit-logged |
| Unmask requires reason ≥ 8 chars | Implemented | `services/patient/router.py::_enforce_reason` |
| Secrets in environment only | Implemented | `/app/backend/.env` (never checked in; `.gitignore` updated) |
| Key management via KMS/HSM | Not Implemented | `DATA_ENCRYPTION_KEY` env-loaded; rotation not wired |
| Internal service mTLS | Out of App Scope | Infra (currently in-process bus) |

### 2.5 Privacy (SOC 2 Privacy Criteria)

| Control | Status | Evidence |
|---|---|---|
| Data subject right to access | Implemented | `GET /api/patients/{id}/export` — full decrypted JSON, self or admin, audited |
| Data subject right to deletion | Partially Implemented | Soft-delete with 7-yr retention; no hard-delete worker yet |
| Consent capture on registration | Not Implemented | No explicit consent checkbox / versioned privacy notice |
| Privacy notice versioning | Not Implemented | No in-app privacy notice / ToS acceptance record |
| Data minimisation | Implemented | Default-mask PHI + minimum-necessary views per role |
| Purpose limitation | Partially Implemented | Break-glass reason captures *why* but there is no structured purpose taxonomy |

---

## 3. CCPA mapping (consumer data rights)

CCPA applies primarily to California residents. This section treats **patients as consumers** where applicable. PHI is also covered by HIPAA; we default to the stricter rule.

| CCPA obligation | Status | Evidence / Notes |
|---|---|---|
| Right to know (data collected, purposes, categories) | Not Implemented | No user-facing "Download my data summary" + categories disclosure; `/export` returns raw JSON but lacks a plain-language categories view |
| Right to access (portable data export) | Implemented | `GET /api/patients/{id}/export` returns JSON (portable format) |
| Right to delete | Partially Implemented | Soft-delete only; 7-year HIPAA retention overrides CCPA delete for PHI (documented exemption) |
| Right to correct | Implemented | `PUT /api/patients/{id}` — patient can correct own profile |
| Right to opt-out of sale/sharing | Out of App Scope | CCMS does not sell or share patient data with 3rd parties today — must be documented in a Privacy Notice |
| Right to limit use of sensitive PI | Not Implemented | No in-app toggle for restricting use of sensitive categories |
| Right to non-discrimination | Implemented | No feature gating based on rights invocation |
| Verifiable consumer request process | Partially Implemented | Self-service via authenticated session; no alternative verification channel for unauthenticated requests |
| Notice at collection | Not Implemented | No visible privacy notice on registration form |
| 45-day response SLA | Out of App Scope | Operational; no request-tracking ticket system in-app |
| Retention disclosure | Partially Implemented | 7-yr retention documented internally, not user-visible |
| Vendor / service-provider contracts | Out of App Scope | Legal |
| Privacy policy link in UI | Not Implemented | No footer link today |
| Audit log of rights exercises | Implemented | Every export writes a `patient.exported` audit row with `phi_accessed=true` |

---

## 4. ISO/IEC 27001:2022 — Annex A control domain mapping

ISO 27001 Annex A 2022 groups controls into 4 themes (Organisational, People, Physical, Technological). We focus on technological + the subset of organisational that has app-layer touch-points.

### 4.1 A.5 — Organisational controls
| Control | Status | Notes |
|---|---|---|
| A.5.1 Policies for information security | Out of App Scope | Governance document set |
| A.5.2 Roles & responsibilities | Out of App Scope | Org chart + RACI |
| A.5.7 Threat intelligence | Not Implemented | No upstream CVE subscription / Dependabot |
| A.5.8 Information security in project management | Partially Implemented | This baseline doc is the starting artefact |
| A.5.9 Inventory of information & assets | Partially Implemented | Schema documented in `models.py` per service; no DLP classification tags |
| A.5.10 Acceptable use | Out of App Scope | HR policy |
| A.5.15 Access control | Implemented | RBAC, MFA, lockout — see §2.1 |
| A.5.16 Identity management | Implemented | Unique UUIDs, email uniqueness |
| A.5.17 Authentication information | Implemented | bcrypt-hashed passwords, password history, rotation policy |
| A.5.23 Information security for use of cloud services | Out of App Scope | Vendor BAAs / DPAs |
| A.5.24–A.5.30 Incident management & continuity | Out of App Scope | Org runbooks, tabletop drills |
| A.5.33 Protection of records | Implemented | Audit log retention design + soft-delete + encryption |

### 4.2 A.6 — People controls
All **Out of App Scope** (hiring, onboarding, training, sanctions, remote work policy, NDAs). The app does not enforce these; HR + Security Officer do.

### 4.3 A.7 — Physical controls
All **Out of App Scope** (facility, equipment, clear-desk). Cloud provider responsibility under BAA/DPA.

### 4.4 A.8 — Technological controls (the most app-relevant block)

| Control | Status | Evidence |
|---|---|---|
| A.8.1 User endpoint devices | Out of App Scope | MDM / device management |
| A.8.2 Privileged access rights | Partially Implemented | Admin role separated; no just-in-time elevation (break-glass exists for PHI, not for admin) |
| A.8.3 Information access restriction | Implemented | RBAC + masking + per-user scoping |
| A.8.4 Access to source code | Out of App Scope | Git hosting permissions |
| A.8.5 Secure authentication | Implemented | MFA, password policy, lockout, session cookies with Secure+SameSite |
| A.8.6 Capacity management | Partially Implemented | Metrics exposed; no autoscaling config in-repo |
| A.8.7 Protection against malware | Out of App Scope | Infra AV + image scanning |
| A.8.8 Management of technical vulnerabilities | Not Implemented | No SCA / SAST / DAST pipeline wired |
| A.8.9 Configuration management | Partially Implemented | `.env` driven config; no centralised config-as-code vault |
| A.8.10 Information deletion | Partially Implemented | Soft-delete with retention window; no automated purge worker |
| A.8.11 Data masking | Implemented | `core/masking.py` — default-masked lists + unmask-with-reason |
| A.8.12 Data leakage prevention | Partially Implemented | Audit of exports + unmask; no egress monitoring |
| A.8.13 Backups | Out of App Scope | Infra |
| A.8.14 Redundancy | Partially Implemented | Replica-ready DB code path; single node in dev |
| A.8.15 Logging | Implemented | Structured audit log + Prometheus metrics |
| A.8.16 Monitoring activities | Partially Implemented | Metrics emitted; no alerting rules in-repo |
| A.8.17 Clock synchronisation | Out of App Scope | NTP at infra layer |
| A.8.18 Use of privileged utility programs | Out of App Scope | Ops-managed |
| A.8.20 Network security | Out of App Scope | Ingress / VPC |
| A.8.21 Security of network services | Out of App Scope | Ingress |
| A.8.22 Segregation of networks | Out of App Scope | Infra |
| A.8.23 Web filtering | Out of App Scope | Infra |
| A.8.24 Use of cryptography | Partially Implemented | AES-256-GCM for PHI fields + bcrypt for passwords; no formal crypto policy doc; no KMS-backed keys |
| A.8.25 Secure development life cycle | Partially Implemented | Code review + tests exist; no formal SDLC checklist in repo |
| A.8.26 Application security requirements | Implemented | This document + HIPAA_COMPLIANCE.md |
| A.8.27 Secure system architecture | Implemented | Microservice boundaries, write/read split, CQRS-lite |
| A.8.28 Secure coding | Partially Implemented | Linting on PR (`ruff`, `eslint`); no security-specific rule set |
| A.8.29 Security testing | Partially Implemented | Unit + integration tests via testing agent; no pentest |
| A.8.30 Outsourced development | Out of App Scope | Contract-level |
| A.8.31 Separation of environments | Partially Implemented | `.env`-driven; only dev env in-repo |
| A.8.32 Change management | Partially Implemented | Git-based; no formal CAB process |
| A.8.33 Test information | Implemented | Seed data is synthetic; no production PHI used in tests |
| A.8.34 Protection of IS during audit | Out of App Scope | Auditor access process |

---

## 5. App-layer vs external control boundaries — summary

| Layer | Who owns it | Example controls |
|---|---|---|
| **Application** (this repo) | Engineering | RBAC, MFA, encryption-in-use, audit, masking, rate-limit, metrics, export/delete endpoints, session handling |
| **Infrastructure** | Platform/DevOps | TLS termination, WAF, VPC, KMS, backups, DR, NTP, logging sink, IDS |
| **Operational** | Security Officer + Ops | Access reviews, retention worker runs, incident response, vendor risk, change mgmt, monitoring alerts |
| **Legal/Policy** | Legal + Privacy Officer | BAAs, DPAs, privacy notice, consent language, policies, training, sanctions |
| **People/HR** | HR | Onboarding, NDAs, training completion, offboarding |

Items marked **Out of App Scope** in §2–§4 belong to one of the bottom four rows. The engineering team must ensure hooks exist (export, disable, audit), but the execution of the non-app layers is organisational.

---

## 6. Verification today — what can be demonstrated from code alone

The following can be demonstrated in < 30 minutes by running the app:

1. Login + MFA challenge (seeded admin account)
2. Audit log viewer (`/audit-log`) shows every PHI access, outcome, IP, UA
3. Patient list is masked by default; unmask prompts a reason and writes a `phi_accessed=true` row
4. Doctor/staff opening a patient out of scope triggers the break-glass dialog
5. Admin deleting a patient requires reauth + reason and sets `retention_until = now + 7y`
6. Patient-self / admin can pull the `/export` JSON for any patient
7. `/api/metrics` returns a Prometheus payload with HTTP, cache, rate-limit, DB counters
8. `/api/health` returns `healthy`
9. The new `/compliance` admin page (added by this task) surfaces environment hardening flags, audit activity summary, retention status, MFA adoption, and per-control status.

Everything else on this list requires infrastructure artefacts, auditor evidence, or policy documents that do not live in this repo.

---

## 7. Next steps

1. Operationalise the in-app **Compliance Dashboard** (`/compliance`) — this task.
2. Execute the engineering backlog (`COMPLIANCE_BACKLOG.md`) P0 and P1 items (retention worker, consent capture, SCA in CI, alerting pipeline).
3. Assign a Security Officer + Privacy Officer (operational).
4. Engage an auditor for a SOC 2 Type I readiness review once the P0/P1 engineering backlog is closed.
5. Draft a public-facing Privacy Notice and link it from Login + Register + Dashboard footer (legal input required).
