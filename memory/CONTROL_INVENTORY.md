# CCMS Control Inventory

**Last updated:** 2026-02-18
**Source of truth for:** individual-control status, framework mapping, evidence locations.
**Pairs with:** `COMPLIANCE_BASELINE.md` (narrative view) and `COMPLIANCE_BACKLOG.md` (remediation queue).

---

## Legend

- **Type:** `T` technical (code) · `O` operational (process) · `L` legal/policy · `I` infrastructure
- **Status:** `Implemented` · `Partial` · `Missing` · `Out-of-App`
- **Framework codes:** `SOC2-CC6.x` · `SOC2-A` Availability · `SOC2-C` Confidentiality · `SOC2-PI` Processing Integrity · `SOC2-P` Privacy · `CCPA-§` · `ISO-A.x` (Annex A 2022)
- **HIPAA** cross-references live in `HIPAA_COMPLIANCE.md`; included here only where the same control satisfies SOC2/CCPA/ISO

| # | Control name | Frameworks | Type | Status | Code / Evidence | Owner placeholder | Remediation |
|---|---|---|---|---|---|---|---|
| AC-1 | Unique user identification (UUIDs) | SOC2-CC6.1, ISO-A.5.16 | T | Implemented | `services/identity/models.py` — `users.id` UUIDv4, unique email index | Eng · Identity lead | — |
| AC-2 | Role-based access control (admin/doctor/staff/patient) | SOC2-CC6.1, ISO-A.5.15, A.8.3 | T | Implemented | `core/deps.py::require_role`, every router `Depends(require_role(...))` | Eng · Platform | — |
| AC-3 | MFA (TOTP + backup codes) | SOC2-CC6.1, ISO-A.8.5 | T | Implemented | `core/mfa.py`, `services/identity/router.py::mfa_*` | Eng · Identity | — |
| AC-4 | Password policy (complexity + history + rotation) | SOC2-CC6.1, ISO-A.5.17 | T | Implemented | `core/password_policy.py` | Eng · Identity | — |
| AC-5 | Brute-force lockout | SOC2-CC6.1 | T | Implemented | `services/identity/router.py` (per-email lockout), `core/rate_limit.py` (IP sliding window) | Eng · Platform | — |
| AC-6 | Account disable / enable (preserves audit) | SOC2-CC6.3 | T | Implemented | `POST /api/auth/users/{id}/disable` + `/enable` | Eng · Identity | — |
| AC-7 | Step-up reauth for destructive ops | SOC2-CC6.1 | T | Implemented | `core/reauth.py`, enforced on delete-patient + add-medical-record | Eng · Patient | — |
| AC-8 | Session idle timeout + warning | SOC2-CC6.1, ISO-A.8.5 | T | Implemented | `contexts/AuthContext.jsx` — 15 min idle, 14 min warning | Eng · Frontend | — |
| AC-9 | Session cookie hardening (Secure + HttpOnly + SameSite=None) | SOC2-CC6.7 | T | Implemented | `services/identity/router.py::set_auth_cookies` | Eng · Identity | — |
| AC-10 | Privileged-access separation (admin role restricted) | SOC2-CC6.1, ISO-A.8.2 | T | Partial | `require_role("admin")` used; no JIT elevation or approval workflow | Eng · Platform | Add approval-based admin elevation |
| AC-11 | Periodic access review | SOC2-CC6.2, ISO-A.5.15 | O | Missing | — | Security Officer (TBA) | Quarterly review SOP + exportable report |
| AC-12 | HRIS-triggered deprovisioning | SOC2-CC6.3 | O | Out-of-App | — | HR | Wire HRIS webhook if adopted |
| AU-1 | Structured audit log of every PHI access | SOC2-CC7.1, ISO-A.8.15, HIPAA | T | Implemented | `core/audit.py::log_audit` + `services/audit/router.py` | Eng · Platform | — |
| AU-2 | Meta-audit (audit-log viewer viewings) | SOC2-CC7.1 | T | Implemented | `services/audit/router.py::list_audit_logs` — writes `audit_log.viewed` | Eng · Platform | — |
| AU-3 | Tamper resistance of audit log | SOC2-CC7.1, ISO-A.8.15 | T | Partial | No mutation API; DB-layer immutability not enforced | Eng · Platform | Append-only store or schema pre-hook |
| AU-4 | Audit log retention (7 years) | HIPAA, SOC2 | O | Partial | Policy documented; no TTL purge | Ops | Retention worker |
| AU-5 | Break-glass reason capture + flagging | SOC2-CC6.1, HIPAA | T | Implemented | `services/patient/router.py::_enforce_reason`, `audit_emergency` | Eng · Patient | — |
| AU-6 | Alerting pipeline on suspicious auth | SOC2-CC7.1, ISO-A.8.16 | I/O | Missing | Metrics emitted; no alerting rules committed | DevOps | Wire Prometheus alertmanager rules + runbook |
| CR-1 | PHI field-level encryption at rest (AES-256-GCM) | SOC2-C, ISO-A.8.24, HIPAA | T | Implemented | `core/crypto.py`; `patients`, `medical_records`, `appointments` free-text fields | Eng · Platform | — |
| CR-2 | Password hashing (bcrypt) | SOC2-CC6.1, ISO-A.8.24 | T | Implemented | `core/security.py` | Eng · Identity | — |
| CR-3 | Encryption-key management via KMS | ISO-A.8.24 | I | Missing | `DATA_ENCRYPTION_KEY` loaded from env | DevOps + Sec | Move to AWS KMS / Azure Key Vault; enable rotation |
| CR-4 | TLS 1.2+ everywhere | SOC2-CC6.7, ISO-A.8.20 | I | Out-of-App | Ingress terminates TLS | DevOps | Verify cipher suites, HSTS at ingress |
| CR-5 | Internal service mTLS | ISO-A.8.20 | I | Out-of-App | Single-process today; N/A until broker introduced | DevOps | When RabbitMQ added |
| CR-6 | Formal crypto policy document | ISO-A.8.24 | L | Missing | — | Security Officer | Write and version |
| DM-1 | PHI masking by default in list views | SOC2-C, ISO-A.8.11, HIPAA | T | Implemented | `core/masking.py`; default-masked responses | Eng · Platform | — |
| DM-2 | Unmask requires reason + audit row | SOC2-P, HIPAA | T | Implemented | `services/patient/router.py` | Eng · Patient | — |
| DM-3 | Patient right-to-access export | SOC2-P, CCPA-§1798.100/110, HIPAA | T | Implemented | `GET /api/patients/{id}/export` | Eng · Patient | — |
| DM-4 | Right-to-correct (patient self-service) | CCPA-§1798.106 | T | Implemented | `PUT /api/patients/{id}` | Eng · Patient | — |
| DM-5 | Right-to-delete with legal-hold override | CCPA-§1798.105, SOC2-P | T | Partial | Soft-delete only; physical purge job missing | Eng · Patient + Ops | Retention worker |
| DM-6 | Consent capture on registration | SOC2-P, CCPA-§1798.100(b) | T | Missing | No consent checkbox / versioned notice | Eng · Identity + Legal | Add versioned ToS/Privacy acceptance row |
| DM-7 | Privacy Notice surfaced to user | CCPA-§1798.100(b) | L/T | Missing | No footer link / modal | Legal + Eng · Frontend | Publish + link |
| DM-8 | Data categories + purpose taxonomy | SOC2-P | T | Missing | `reason` is free-text | Eng · Platform | Add enum `purpose_code` to audit + break-glass |
| DM-9 | Limit use of sensitive PI toggle | CCPA | T | Missing | No user-facing limit switch | Eng · Frontend | Evaluate after Privacy Notice lands |
| SM-1 | Secrets kept in `.env`, never committed | SOC2-CC6.1, ISO-A.8.9 | T | Implemented | `.gitignore` covers `.env`; `.env.example` practice | DevOps | — |
| SM-2 | Config-as-code vault (Vault/SSM) | ISO-A.8.9 | I | Out-of-App | — | DevOps | Adopt before production |
| SM-3 | Rotation schedule for JWT_SECRET | ISO-A.8.24 | O | Missing | No rotation SOP | Security Officer | Document + wire graceful rotation |
| SM-4 | Separate envs (dev/stg/prod) | SOC2-CC8.1, ISO-A.8.31 | I/O | Partial | Only dev runs in this repo | DevOps | Provision stg + prod with isolated secrets |
| OB-1 | Health endpoint | SOC2-A | T | Implemented | `GET /api/health` | Eng · Platform | — |
| OB-2 | Prometheus metrics (HTTP, cache, DB, rate-limit) | SOC2-A, ISO-A.8.15 | T | Implemented | `core/metrics.py`, `/api/metrics` | Eng · Platform | — |
| OB-3 | Request-level structured logging | SOC2-CC7.1 | T | Partial | Standard Python logging; no JSON structured logger | Eng · Platform | Switch to structlog |
| OB-4 | Centralised log aggregation | ISO-A.8.15 | I | Out-of-App | — | DevOps | Ship to ELK / Datadog |
| OB-5 | Alerting on SLO breach | SOC2-A | I/O | Missing | — | DevOps | Alertmanager rules |
| VD-1 | Dependency SCA (Dependabot / Snyk) | ISO-A.8.8 | O/T | Missing | — | Eng leadership | Enable in CI |
| VD-2 | SAST in CI | ISO-A.8.28 | T | Missing | — | Eng leadership | Bandit/Semgrep for Python, ESLint security plugin |
| VD-3 | DAST / pentest before go-live | ISO-A.8.29 | O | Out-of-App | — | Security Officer | Pre-launch engagement |
| VD-4 | Container image scanning | ISO-A.8.7, A.8.8 | I | Out-of-App | — | DevOps | Trivy/Grype in CI |
| IR-1 | Incident response runbook | SOC2-CC7.2, ISO-A.5.24 | O/L | Missing | — | Security Officer | Draft + tabletop |
| IR-2 | Breach-notification timelines | CCPA, HIPAA | L | Out-of-App | — | Legal | Documented procedure |
| IR-3 | In-app incident reporting for staff | ISO-A.5.24 | T | Missing | — | Eng · Frontend | Add `/incidents/new` once runbook exists |
| BK-1 | Encrypted backups with tested restore | ISO-A.8.13 | I | Out-of-App | — | DevOps | — |
| BK-2 | Exportable evidence for auditors (audit log CSV / JSON dump) | SOC2-CC7.1, ISO-A.5.33 | T | Partial | Can query via API; no one-click auditor export | Eng · Platform | Add CSV export endpoint |
| VR-1 | Vendor BAAs / DPAs | HIPAA, SOC2-CC9.2, ISO-A.5.23 | L | Out-of-App | — | Legal | Ahead of each vendor integration |
| VR-2 | Inventory of subprocessors | CCPA, ISO-A.5.23 | O | Missing | — | Security Officer | Maintain in governance wiki |
| HR-1 | Workforce training on security/privacy | ISO-A.6, SOC2-CC1 | O | Out-of-App | — | HR | Annual cadence |
| HR-2 | Sanction policy for violations | ISO-A.6.4 | L | Out-of-App | — | HR | — |
| CM-1 | Change management through Git + code review | SOC2-CC8.1, ISO-A.8.32 | T/O | Partial | Git exists; no enforced branch protection in-repo | Eng leadership | Enforce protected `main`, required reviews |
| CM-2 | CAB / emergency-change process | SOC2-CC8.1 | O | Out-of-App | — | Eng leadership | Lightweight doc |
| CP-1 | Capacity planning & scalability | SOC2-A, ISO-A.8.6 | T/I | Partial | Metrics + read/write split + Redis; no formal plan | DevOps | Document headroom targets |
| CP-2 | Multi-region failover | SOC2-A | I | Out-of-App | — | DevOps | Post-MVP |

---

## Summary counts

| Status | Count | Notes |
|---|---|---|
| Implemented | 22 | All technical, verifiable in code today |
| Partial | 13 | Hooks exist; remediation queued |
| Missing | 15 | Owned by Engineering; see `COMPLIANCE_BACKLOG.md` |
| Out-of-App | 13 | Owned by Infra / Legal / Ops / HR — tracked here for visibility only |

---

## How to use this inventory

1. **During feature work** — when adding a new endpoint that touches PHI, cross-reference `AC-*`, `AU-*`, `DM-*` rows and ensure equivalents exist.
2. **During audit prep** — evidence locations (Code / Evidence column) are the exact files or paths an auditor can review.
3. **During backlog grooming** — any `Missing` row owned by "Eng" should have a ticket in `COMPLIANCE_BACKLOG.md`.
4. **During vendor onboarding** — `VR-*` rows must be executed before production data flows to the vendor.
