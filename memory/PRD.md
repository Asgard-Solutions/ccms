# CCMS — Product Requirements & Architecture Notes

**Last updated:** 2026-04-19 (Phase 1 + HIPAA hardening)

## 1. Original problem statement
Multi-tenant Chiropractic Clinic Management System on a microservices, event-driven architecture. Phase 1 delivered Identity / Patient / Scheduling / Communication. The HIPAA hardening pass added technical safeguards in line with 45 CFR §164.312.

## 2. User personas
| Persona     | Goals                                                                          |
|-------------|--------------------------------------------------------------------------------|
| **Admin**   | Manage users, full oversight, audit log review                                 |
| **Doctor**  | See own appointments, view patients (with break-glass reason), add records    |
| **Staff**   | Manage patients & scheduling, view notification log                            |
| **Patient** | See own profile, own records, own appointments; export own data               |

## 3. Architecture
**Backend** (`/app/backend/`)
- `server.py` — API Gateway under `/api`
- `core/` — `db.py`, `security.py` (bcrypt + JWT), `deps.py` (RBAC), `event_bus.py`, **`audit.py`**, **`crypto.py`** (AES-256-GCM), **`password_policy.py`**, **`mfa.py`** (TOTP + backup codes), **`reauth.py`**, **`masking.py`**
- `services/identity/` — register, login, MFA setup/verify/challenge, refresh, logout, change-password, reauth, admin user CRUD + disable/enable
- `services/patient/` — masked-by-default list/detail, encrypted PHI at rest, break-glass reason, soft-delete with 7-year retention, export
- `services/scheduling/` — encrypted notes, audit trail
- `services/communication/` — masked notification log
- `services/audit/` — admin-only `/api/audit-logs` viewer

**Frontend** (`/app/frontend/src/`)
- `AuthContext` (cookie session + MFA flow + 15-min idle timeout)
- Pages: Login (with MFA step), Register, Dashboard, Patients (mask toggle), PatientDetail (break-glass + reauth + soft-delete + export), Appointments, Calendar, Notifications (mask toggle), **Security**, **AuditLog**
- Components: `BreakGlassDialog`, `ReauthDialog`

## 4. What's implemented
### Phase 1 (2026-04-19)
- Identity, Patient CRUD, Scheduling with conflict detection, mock notifications via in-process event bus
- Sage + stone medical theme, 7 role-aware pages

### HIPAA hardening (2026-04-19)
- **Audit logging** of every PHI access with PHI flag, IP, user-agent, outcome, reason
- **Field-level encryption at rest** (AES-256-GCM) for `patients.{address,emergency_contact,notes}`, `medical_records.{description,diagnosis,treatment}`, `appointments.notes` — verified with `enc:v1:` prefix in raw Mongo
- **Password policy**: 12-char complexity + denylist + history-of-5 + 90-day rotation warning + 120-day hard expiry
- **MFA (TOTP)** with provisioning URI + 8 single-use backup codes, ticket-based challenge step on login
- **Step-up reauth** required for delete-patient + add-medical-record
- **Break-glass**: Doctor/Staff must enter ≥8-char clinical reason to view PHI outside their scope; logged as emergency_access
- **PHI masking** by default in lists + detail; admin unmask is audited
- **Soft-delete + 7-year retention**, **patient data export** (JSON, right-to-access)
- **Account disable / enable** preserving audit history
- **Idle auto-logoff** at 15 minutes (front-end)
- **Brute-force lockout** by email-only identifier (k8s-ingress-safe)

## 5. Verified end-to-end (testing agent 24/24 backend, 7/7 frontend flows)
- Mock event bus → 6 notifications per appointment lifecycle (no regression)
- Admin login → MFA setup → Audit log → Patient unmask audited
- Doctor login → Audit log hidden → Patient detail prompts break-glass dialog
- Patient login → sees only own record → can export own JSON
- Encryption at rest confirmed via direct mongoDB inspection

## 6. Backlog
### P0 (production go-live blockers — operational, not code)
- HIPAA-eligible DB (MongoDB Atlas + BAA, or Postgres in HIPAA-compliant cloud)
- BAAs with all PHI processors
- KMS-backed `DATA_ENCRYPTION_KEY` (currently env-loaded)
- Retention worker that physically purges patients with `retention_until < now`
- Audit log immutability at the storage layer (append-only or pre-hook)

### P1 (next features)
- Billing service subscriber on `appointment.completed`
- Real Twilio SMS + Resend email (require BAAs)
- Reporting service for compliance and ops dashboards
- Patient self-service portal (book / reschedule own appointments)
- Postgres migration (schema is 1:1, mechanical)

### P2 (polish)
- Multi-tenancy with `tenant_id` on every entity + JWT claim
- OpenID Connect / SAML SSO option for clinic IdP
- OpenTelemetry + Prometheus
- Real broker (RabbitMQ/Azure Service Bus) — same publish/subscribe API

## 7. Key reference docs
- `/app/memory/HIPAA_COMPLIANCE.md` — full safeguard inventory (implemented vs. external)
- `/app/memory/test_credentials.md` — demo accounts
- `/app/test_reports/iteration_2.json` — testing agent report (24/24)
