# HIPAA Technical Safeguards — CCMS

**Last updated:** 2026-04-19
**Scope:** This document describes the HIPAA *technical safeguard* controls implemented inside the CCMS application. It explicitly separates **Implemented** (in-app) from **Required but external** (infrastructure, legal, operational) responsibilities.

This document is not a legal compliance attestation. Reaching a compliant production posture requires the organisation to also execute the items in §2.

---

## 1. Implemented (in-app controls)

### 1.1 Access control — 45 CFR §164.312(a)
| Requirement | Implementation |
|---|---|
| Unique user identification | UUID v4 primary key + unique email (`users.email`) |
| Role-based access | `core/deps.py::require_role` on every PHI-touching endpoint; roles: `admin`, `doctor`, `staff`, `patient` |
| Automatic logoff | Frontend idle-timeout = 15 min with warning at 14 min (`AuthContext.jsx`) |
| Session tokens | Access token 60 min, refresh token 7 days, httpOnly + Secure + SameSite=None cookies |
| Account lockout | 5 failed logins per email → 15-min lockout (HTTP 429) |
| Account disable | `POST /api/auth/users/{id}/disable` — rejected at login + on current session (never hard-deletes, preserves audit trail) |
| Step-up re-authentication | `POST /api/auth/reauth` issues a 5-minute `reauth_token` required for: delete patient, add medical record |
| Multi-factor authentication | TOTP (RFC-6238) via `pyotp`; backup codes; enforced challenge on login when enabled |

### 1.2 Audit controls — 45 CFR §164.312(b)
| Requirement | Implementation |
|---|---|
| Audit log for every PHI access | `core/audit.py::log_audit` writes `{action, actor_id, actor_email, actor_role, entity_type, entity_id, reason, metadata, outcome, phi_accessed, ip, user_agent, created_at}` |
| Admin review UI | `/audit-log` page (admin-only) with filters: all / PHI / auth / patient / break-glass |
| Break-glass flagging | `core/audit.py::audit_emergency` sets `metadata.emergency_access = true` and `phi_accessed = true` |
| Tamper resistance | Audit rows are written-once from routers; no API mutates them; MongoDB indexes on (created_at, actor_id, entity_type+entity_id, phi_accessed) for fast compliance queries |
| Retention | 7-year retention policy documented; physical purge handled by DB-layer policy (not auto-deleted in Phase 1 — flagged in §2 as operational) |

### 1.3 Integrity — 45 CFR §164.312(c)
| Requirement | Implementation |
|---|---|
| Authenticated encryption at rest (PHI) | `core/crypto.py` AES-256-GCM (authenticated tag detects tampering). Field-level encryption for: `patients.address`, `patients.emergency_contact`, `patients.notes`, `medical_records.description`, `.diagnosis`, `.treatment`, `appointments.notes` |
| Safe round-trip | `decrypt_fields` used on every read path |
| Encryption key | 32-byte key loaded from `DATA_ENCRYPTION_KEY` (env). Ciphertext tagged `enc:v1:` — enables future key rotation. |
| Idempotent encryption | `encrypt_text` is a no-op on already-encrypted values — seeds + migrations are safe to re-run |

### 1.4 Person or entity authentication — 45 CFR §164.312(d)
| Requirement | Implementation |
|---|---|
| Strong passwords | Minimum 12 chars + upper + lower + digit + symbol + common-password denylist (`core/password_policy.py`) |
| Password history | Last 5 hashes stored; rejection of reuse (`reject_password_reuse`) |
| Rotation | 90-day warning, 120-day hard expiry (login rejected → must reset) |
| MFA | Enforced by policy for admin/doctor/staff; optional for patients |

### 1.5 Transmission security — 45 CFR §164.312(e)
| Requirement | Implementation |
|---|---|
| TLS enforcement | HTTPS-only at the ingress; cookies marked `Secure`; `SameSite=None` prevents mixed-context leaks |
| CORS | Origin-locked to `FRONTEND_URL` with `allow_credentials=True` when the origin is explicit |

### 1.6 Minimum necessary — 45 CFR §164.502(b)
| Requirement | Implementation |
|---|---|
| Default-mask PHI in list views | `core/masking.py` returns `"M. L."`, `"p*****@domain"`, `"19**-**-**"`, `"***-***-0104"`, `"[redacted]"` |
| Click-to-reveal with audit | Non-admin unmask **requires** a `reason` (≥ 8 chars) → `audit_emergency` row |
| Notifications masking | `/api/notifications` masks `to_address` + `body` unless admin passes `unmask=true` with a reason |

### 1.7 Break-glass / Emergency access — 45 CFR §164.312(a)(2)(ii)
| Requirement | Implementation |
|---|---|
| Out-of-scope record access | Doctor/Staff opening any patient record must provide a clinical reason (captured in `BreakGlassDialog.jsx`), sent as `?reason=…` query param |
| Backend enforcement | `_enforce_reason` guards `GET /api/patients/{id}` for doctor/staff |
| Audit | Every such access writes an `audit_emergency` row visible under **Audit Log → Break-glass** |

### 1.8 Data retention & right-to-access — 45 CFR §164.528 + §164.524
| Requirement | Implementation |
|---|---|
| Soft-delete with retention window | `DELETE /api/patients/{id}` sets `status='deleted'`, `deleted_at`, `retention_until = now + 7 years`, requires reauth + reason |
| Patient data export (right to access) | `GET /api/patients/{id}/export` returns full JSON (decrypted) of the patient, their medical records, and appointments — patient-self OR admin |
| Audit of export | Every export emits a `patient.exported` audit row with `phi_accessed=true` |

### 1.9 Observability
- Audit log is the primary operational monitoring surface. Each row includes IP + user-agent for forensic investigation.
- Failed authentication attempts are logged with `outcome=failure` and a `reason` string (`invalid_credentials`, `account_disabled`, `password_expired`, `bad_code`).

---

## 2. Required but external (NOT implemented in-app)

The following controls are **required for a HIPAA-compliant production deployment** but sit outside the application layer. The clinic/operator is responsible for them.

### 2.1 Infrastructure
| Control | Required action |
|---|---|
| HIPAA-eligible database | Move off the dev MongoDB to **MongoDB Atlas with a signed BAA** or **self-managed PostgreSQL inside AWS/GCP/Azure HIPAA-eligible services** |
| BAA — cloud + DB + email provider | Execute Business Associate Agreements with every vendor that stores/processes PHI |
| Encryption-in-transit | TLS 1.2+ everywhere (Emergent ingress already enforces this); internal service-to-service TLS when the in-process bus is replaced by RabbitMQ |
| Encryption-key management | Replace the `DATA_ENCRYPTION_KEY` env var with an HSM-backed KMS (AWS KMS, Azure Key Vault) and rotate on a schedule |
| Backups + DR | Encrypted backups (tested restore), geographic redundancy, documented RPO/RTO |
| Network segmentation | Private subnet for DB; IP allow-list for admin paths |
| WAF / DDoS / rate limiting | Ingress-layer controls; the app ships basic lockout only |

### 2.2 Administrative
| Control | Required action |
|---|---|
| Designated Security Officer + Privacy Officer | Assign named roles; document contact paths |
| Annual risk assessment | Perform and document per §164.308(a)(1)(ii)(A) |
| Workforce training | HIPAA training on hire + annually; record completion |
| Sanction policy | Written policy for workforce violations |
| Access reviews | Quarterly review of `users` + `audit_logs`; disable stale accounts |
| Incident response / breach notification | Written runbook + 60-day notification procedure |
| BAAs with subcontractors | Executed prior to sharing PHI |

### 2.3 Physical
| Control | Required action |
|---|---|
| Workstation security | Full-disk encryption on every clinic machine; auto-lock; clean-desk policy |
| Facility access | Badge + video at datacenter (cloud provider's responsibility under the BAA) |
| Media disposal | NIST 800-88 wipe of any hardware that touched PHI |

### 2.4 Operational gaps to close before production go-live
1. **Real key management** — replace env-based `DATA_ENCRYPTION_KEY` with KMS.
2. **Retention worker** — nightly job that permanently purges `patients` with `retention_until < now` (currently only the timestamp is set).
3. **Audit log immutability at the DB layer** — move audit collection to an append-only store or use MongoDB `$jsonSchema` with a pre-hook that rejects updates.
4. **Real notifications** — swap the mock subscriber for Twilio SMS + Resend/SendGrid email; require BAAs with both.
5. **Session fingerprinting** — bind cookies to IP/UA fingerprint and require reauth on drift.
6. **Formal penetration test** before go-live.

---

## 3. Verification checklist (what the engineer can prove today)

- [x] Every PHI-touching route calls `audit_success` or `audit_emergency` — grep `core/audit` in routers
- [x] Every PHI free-text field goes through `encrypt_fields` on write and `decrypt_fields` on read
- [x] No endpoint returns `_id` (`{"_id": 0}` projection everywhere)
- [x] Password policy enforced on register + change-password + admin-create-user
- [x] Password history prevents reuse of last 5 hashes
- [x] MFA fully wired: setup → verify → challenge on login → backup codes consume-once
- [x] Patient role cannot see other patients (`q["user_id"] = user["id"]`)
- [x] Doctor/Staff access to any patient detail requires a ≥ 8-char reason, logged as break-glass
- [x] `DELETE /api/patients/{id}` requires admin + reauth + reason, sets 7-year retention
- [x] `GET /api/patients/{id}/export` returns full decrypted JSON, patient-self or admin only
- [x] 15-minute frontend idle timeout with user-facing warning
- [x] Dedicated `/audit-log` page (admin) with filters + search
