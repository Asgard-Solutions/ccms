# CCMS — Product Requirements & Architecture Notes

**Last updated:** 2026-04-19 (Phase 1 MVP)

## 1. Original problem statement
Design and implement a multi-tenant **Chiropractic Clinic Management System** using a microservices, event-driven architecture supporting clinical ops, patient engagement and financial workflows. Target stack was .NET 8 + PostgreSQL + RabbitMQ; the Emergent runtime is React + FastAPI + MongoDB so the system is built here but **architected to be PostgreSQL-migration-ready** (UUID PKs, normalised relations, no embedded documents).

## 2. User personas (Phase 1)
| Persona     | Goals                                                                          |
|-------------|--------------------------------------------------------------------------------|
| **Admin**   | Manage users (create doctors/staff/patients), full oversight                   |
| **Doctor**  | See own appointments, read patient records, add medical records               |
| **Staff**   | Manage patients and scheduling, view notification log                          |
| **Patient** | See own profile, own appointments, own records; self-register                  |

## 3. Core requirements (static)
- Clear microservice boundaries even in a monolith: Identity / Patient / Scheduling / Communication
- Event-driven scheduling: booking/update/cancel fan out to the Communication service
- JWT + httpOnly cookie authentication with RBAC
- Conflict detection on appointment creation and reschedule
- PostgreSQL-ready data model (UUID PKs, FK string IDs, no embedded entities)

## 4. Architecture
**Backend** (`/app/backend/`)
- `server.py` — API Gateway; mounts all service routers under `/api`
- `core/` — `db.py`, `security.py` (bcrypt + PyJWT HS256), `deps.py` (`get_current_user`, `require_role`), `event_bus.py` (async in-process pub/sub)
- `services/identity/` — `/api/auth/*` (register, login, logout, me, refresh, users, providers)
- `services/patient/` — `/api/patients/*` including medical records timeline
- `services/scheduling/` — `/api/appointments/*` publishing `appointment.booked|updated|cancelled`
- `services/communication/` — `/api/notifications/*` + subscribers that persist mock email/SMS rows

**Frontend** (`/app/frontend/src/`)
- `AuthContext` (cookie-session, 3-state: checking/user/null)
- `AppShell` (role-aware sidebar + top nav), `ProtectedRoute` (role gate)
- Pages: Login, Register, Dashboard, Patients, PatientDetail, Appointments, Calendar, Notifications
- Theme: Sage `#7B9A82` primary on Bone `#FAF9F6` background with Outfit (headings) + Manrope (body)

## 5. Data model (relational intent)
```
users (id uuid pk, email unique, password_hash, name, role, phone, created_at, updated_at)
patients (id uuid pk, user_id fk users nullable, first_name, last_name, dob, gender,
          phone, email, address, emergency_contact, notes, created_at, updated_at)
medical_records (id uuid pk, patient_id fk patients, record_type, title, description,
                 diagnosis, treatment, recorded_by fk users, recorded_at)
appointments (id uuid pk, patient_id fk patients, provider_id fk users,
              start_time, end_time, reason, notes, status, created_by fk users,
              created_at, updated_at)
notifications (id uuid pk, appointment_id fk nullable, patient_id fk nullable,
               channel, to_address, subject, body, event_type, status, created_at)
```

## 6. What's implemented (2026-04-19)
- Identity service: JWT cookie auth (15-min access + 7-day refresh), bcrypt, brute-force lockout (email-keyed, 5 tries / 15 min)
- Admin-seeded users: admin / doctor / staff / patient (see `/app/memory/test_credentials.md`)
- Patient service: full CRUD, search, medical records with hydrated `recorded_by_name`
- Scheduling service: CRUD, conflict detection (409 on overlap, self-exclusion on update), cancel, role-filtered list
- Communication service: 3 event subscribers that persist 2 rows (email+SMS) per event → 6 rows per full lifecycle
- Frontend: Login, Register, Dashboard (role-aware), Patients + PatientDetail, Appointments list + filters, Calendar week view, Notifications log, AlertDialog-confirmed cancellations, toast feedback

## 7. Verified end-to-end flow
1. Staff books appointment → `appointment.booked` → 2 notifications created
2. Reschedule → `appointment.updated` → 2 more notifications
3. Cancel → `appointment.cancelled` → 2 more notifications
4. Duplicate slot on same provider → HTTP 409 conflict
5. Patient role sees only own record + own appointments

## 8. Prioritised backlog (post-MVP)
### P0 (blockers for real deployment)
- Real Postgres migration (replace `motor` with `asyncpg` + `sqlalchemy 2.x` async; collection → table mapping is 1:1)
- Replace in-process event bus with RabbitMQ (keep the same `publish` / `subscribe` signature)

### P1 (next features)
- Billing service: invoices, payment records (subscriber of `appointment.completed`)
- Real SMS/email via Twilio + Resend/SendGrid (swap mock subscriber for real dispatcher)
- Reporting service: read-heavy aggregations (appointments per provider, no-show rate)
- Audit log page (we store rows but don't surface them yet)
- Appointment status lifecycle (`completed` transition, no-show flag)
- Patient self-service: my-appointments portal, reschedule-own, book-own

### P2 (polish)
- Multi-tenancy: add `tenant_id` to every entity + JWT claim
- OAuth2 / OpenID Connect provider instead of email+password only
- OpenTelemetry tracing + Prometheus metrics + Serilog-style structured logging
- Retry + circuit-breaker wrappers for eventual real outbound integrations
- Exportable medical record PDF for patients

## 9. Known constraints
- Communication is **MOCKED** (rows persisted with `status=sent_mock`) — by design for Phase 1
- MongoDB chosen for runtime convenience only; every query uses `{"_id": 0}` and entities use string UUID `id` so migration is mechanical
- JWT cookies require `SameSite=None; Secure` because the preview frontend and backend share the same domain via HTTPS ingress
