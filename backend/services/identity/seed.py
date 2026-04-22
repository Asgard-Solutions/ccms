"""
Seed admin + demo users on startup (idempotent).
All seeded passwords meet the HIPAA-compliant strength policy.

Demo identities belong to the fictional Riverbend Chiropractic &
Wellness clinic (see /app/memory/DEMO_SEED.md). Emails + passwords are
stable (tests + login helper depend on them); display names, phones,
job titles, and signature credentials are realistic so the app never
looks like a dev sandbox on first login.
"""
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.db import get_db
from core.security import hash_password, verify_password

DEMO_USERS = [
    {
        "email": "doctor@ccms.app",
        "password": "Doctor@ComplianceClinic1",
        "name": "Dr. Noah Carter",
        "role": "doctor",
        "phone": "+1-503-555-0142",
        "title": "Lead Chiropractor, DC",
        "credentials": "DC, CCSP",
        "npi": "1841792253",
        "display_name": "Dr. Noah Carter, DC",
    },
    {
        "email": "staff@ccms.app",
        "password": "Staff@ComplianceClinic1",
        "name": "Mia Ramirez",
        "role": "staff",
        "phone": "+1-503-555-0158",
        "title": "Front Desk Coordinator",
        "display_name": "Mia Ramirez",
    },
    {
        "email": "patient@ccms.app",
        "password": "Patient@ComplianceClinic1",
        "name": "Ethan Parker",
        "role": "patient",
        "phone": "+1-503-555-0190",
        "display_name": "Ethan Parker",
    },
]

ADMIN_PROFILE = {
    "name": "Ava Bennett",
    "title": "Clinic Administrator",
    "display_name": "Ava Bennett",
    "phone": "+1-503-555-0101",
}


async def _upsert_user(
    email: str, password: str, name: str, role: str, phone: str,
    *,
    title: str | None = None,
    credentials: str | None = None,
    npi: str | None = None,
    display_name: str | None = None,
    **_ignored,
) -> None:
    db = get_db()
    existing = await db.users.find_one({"email": email}, {"_id": 0})
    now = datetime.now(timezone.utc).isoformat()
    hashed = hash_password(password)

    # Legacy demo users all live under the Riverbend Chiropractic &
    # Wellness tenant (slug=default).
    default_tenant = await db.tenants.find_one({"slug": "default"}, {"_id": 0, "id": 1})
    default_tenant_id = default_tenant["id"] if default_tenant else None

    base = {
        "email": email,
        "name": name,
        "role": role,
        "phone": phone,
        "status": "active",
        "tenant_id": default_tenant_id,
        # Admin sees all locations within its tenant; others are location-restricted.
        "tenant_scope_all": role in ("admin", "super_admin"),
        "updated_at": now,
    }
    # Realistic professional profile fields — optional, idempotently refreshed.
    if title is not None:
        base["title"] = title
    if credentials is not None:
        base["credentials"] = credentials
    if npi is not None:
        base["npi"] = npi
    if display_name is not None:
        base["display_name"] = display_name
    if existing is None:
        await db.users.insert_one(
            {
                "id": str(uuid.uuid4()),
                "password_hash": hashed,
                "password_history": [hashed],
                "password_changed_at": now,
                "mfa_enabled": False,
                "mfa_policy_required": False,
                "session_epoch": 0,
                "created_at": now,
                **base,
            }
        )
    else:
        updates = dict(base)
        if not verify_password(password, existing["password_hash"]):
            updates["password_hash"] = hashed
            updates["password_history"] = (
                (existing.get("password_history") or [])[-4:] + [hashed]
            )
            updates["password_changed_at"] = now
        else:
            # Backfill password_history for users seeded before the policy existed,
            # so the very first change-password call cannot reuse the seeded password.
            if not existing.get("password_history"):
                updates["password_history"] = [existing["password_hash"]]
            if not existing.get("password_changed_at"):
                updates["password_changed_at"] = existing.get("created_at", now)
        # Backfill security-hardening fields on legacy rows.
        if existing.get("session_epoch") is None:
            updates["session_epoch"] = 0
        if existing.get("mfa_policy_required") is None:
            updates["mfa_policy_required"] = False
        await db.users.update_one({"email": email}, {"$set": updates})


async def seed() -> None:
    from core.crypto import encrypt_text  # avoid import cycle at module load
    from services.patient._shared import encrypt_patient_value  # noqa: WPS433

    admin_email = os.environ.get("ADMIN_EMAIL", "admin@ccms.app")
    admin_password = os.environ.get("ADMIN_PASSWORD", "Admin@ComplianceClinic1")
    await _upsert_user(
        admin_email, admin_password,
        ADMIN_PROFILE["name"], "admin", ADMIN_PROFILE["phone"],
        title=ADMIN_PROFILE["title"],
        display_name=ADMIN_PROFILE["display_name"],
    )
    for u in DEMO_USERS:
        await _upsert_user(**u)

    # Seed the demo patient record (Ethan Parker — active-adult
    # self-pay maintenance persona). Realistic intake so the app looks
    # lived-in on the first login. See /app/memory/DEMO_SEED.md.
    db = get_db()
    patient_email = "patient@ccms.app"
    patient_user = await db.users.find_one({"email": patient_email}, {"_id": 0})
    doctor_user = await db.users.find_one(
        {"email": "doctor@ccms.app"}, {"_id": 0, "id": 1},
    )
    doctor_id = doctor_user["id"] if doctor_user else None
    if patient_user:
        await db.patients.update_many(
            {"email": {"$in": ["patient@ccms.local", patient_email]}},
            {"$set": {"user_id": patient_user["id"], "email": patient_email}},
        )
        existing_record = await db.patients.find_one(
            {"user_id": patient_user["id"]}, {"_id": 0, "id": 1, "first_name": 1},
        )
        now = datetime.now(timezone.utc).isoformat()
        default_tenant = await db.tenants.find_one({"slug": "default"}, {"_id": 0, "id": 1})
        default_location = await db.locations.find_one(
            {"tenant_id": default_tenant["id"]} if default_tenant else {},
            {"_id": 0, "id": 1},
        ) if default_tenant else None

        patient_doc = {
            "tenant_id": default_tenant["id"] if default_tenant else None,
            "location_id": default_location["id"] if default_location else None,
            "first_name": "Ethan",
            "middle_name": "James",
            "last_name": "Parker",
            "preferred_name": "Ethan",
            "date_of_birth": "1991-08-17",
            "gender": "male",
            "pronouns": "he/him",
            "marital_status": "married",
            "language": "English",
            "phone": patient_user.get("phone") or "+1-503-555-0190",
            "phone_work": "+1-503-555-0233",
            "email": patient_user["email"],
            "preferred_contact_method": "email",
            "occupation": "Software Engineer",
            "employer": "Cascade Analytics",
            "primary_provider_id": doctor_id,
            # PHI free-text fields — encrypted at rest.
            "address": encrypt_text(
                "842 NW Lovejoy St, Apt 4B, Portland, OR 97209"
            ),
            "emergency_contact": encrypt_text(
                "Sarah Parker (Spouse) — +1-503-555-0191"
            ),
            "notes": encrypt_text(
                "Active-adult wellness patient. Returns every 4–6 weeks "
                "for maintenance adjustments after resolving a 2024 "
                "lumbar strain episode. No current acute complaint."
            ),
            # Grouped sections — the Edit Patient wizard reads from
            # these and shows inline validation errors when missing.
            # Encrypted at rest as JSON blobs (see
            # services/patient/_shared.py :: PATIENT_SECTION_ENCRYPTED).
            "demographics": encrypt_patient_value({
                "first_name": "Ethan",
                "middle_name": "James",
                "last_name": "Parker",
                "preferred_name": "Ethan",
                "date_of_birth": "1991-08-17",
                "gender": "male",
                "sex_at_birth": "male",
                "pronouns": "he/him",
                "marital_status": "married",
                "language": "English",
                "occupation": "Software Engineer",
                "employer": "Cascade Analytics",
                "employer_phone": "+1-503-555-0233",
            }),
            "contact": encrypt_patient_value({
                "phone": patient_user.get("phone") or "+1-503-555-0190",
                "phone_alt": None,
                "phone_work": "+1-503-555-0233",
                "email": patient_user["email"],
                "preferred_contact_method": "email",
                "sms_consent": True,
                "email_consent": True,
                "voicemail_consent": True,
            }),
            "address_details": encrypt_patient_value({
                "line1": "842 NW Lovejoy St",
                "line2": "Apt 4B",
                "city": "Portland",
                "state": "OR",
                "postal_code": "97209",
                "country": "USA",
            }),
            "emergency_contact_details": encrypt_patient_value({
                "name": "Sarah Parker",
                "relationship": "Spouse",
                "phone": "+1-503-555-0191",
                "phone_alt": None,
                "email": "sarah.parker@example.com",
            }),
            "admin": encrypt_patient_value({
                "primary_provider_id": doctor_id,
                "referral_source": "Employee demo account",
                "tags": ["self_pay_wellness"],
            }),
            "guarantor": encrypt_patient_value({"same_as_patient": True}),
            "insurance": None,
            "status": "active",
            "updated_at": now,
        }

        if existing_record is None:
            await db.patients.insert_one({
                "id": str(uuid.uuid4()),
                "user_id": patient_user["id"],
                "created_at": now,
                **patient_doc,
            })
        else:
            # Refresh in place so a re-seed after an upgrade pulls the
            # realistic persona onto the legacy "Morgan Lee" row.
            await db.patients.update_one(
                {"id": existing_record["id"]},
                {"$set": patient_doc},
            )

    await _write_credentials_file(admin_email, admin_password)


async def _write_credentials_file(admin_email: str, admin_password: str) -> None:
    mem_dir = Path("/app/memory")
    mem_dir.mkdir(parents=True, exist_ok=True)
    content = f"""# CCMS Test Credentials (HIPAA-hardened build)

> Cookie-based JWT auth + MFA optional for non-admin roles.
> Passwords all meet the 12-char complexity policy. Required MFA prompt appears
> at login for users who have enrolled TOTP. Admin/doctor/staff roles see an
> MFA-setup banner until they enrol.
>
> All demo accounts map to fictional identities inside
> **Riverbend Chiropractic & Wellness** (Portland, OR). See
> `/app/memory/DEMO_SEED.md` for the full persona catalog.

## Demo clinic sign-in quick reference

| Role label     | Person             | Email              | Password                   |
|----------------|--------------------|--------------------|----------------------------|
| Administrator  | Ava Bennett        | `{admin_email}`      | `{admin_password}` |
| Chiropractor   | Dr. Noah Carter    | `doctor@ccms.app`  | `Doctor@ComplianceClinic1` |
| Front desk     | Mia Ramirez        | `staff@ccms.app`   | `Staff@ComplianceClinic1`  |
| Patient portal | Ethan Parker       | `patient@ccms.app` | `Patient@ComplianceClinic1`|

## Auth endpoints
- POST /api/auth/register          — public; creates a `patient`
- POST /api/auth/login             — returns `{{user, mfa_required, mfa_ticket, password_rotation_due}}`
- POST /api/auth/mfa/challenge     — finalise MFA login with TOTP/backup code
- POST /api/auth/logout
- GET  /api/auth/me
- POST /api/auth/refresh
- POST /api/auth/change-password
- POST /api/auth/reauth            — password → short-lived reauth cookie/token
- POST /api/auth/mfa/setup         — returns TOTP secret + otpauth URL + 8 backup codes
- POST /api/auth/mfa/verify        — enable MFA by confirming a TOTP code
- POST /api/auth/mfa/disable       — disable MFA (requires password)
- GET  /api/auth/users             — admin only (supports `include_disabled=true`)
- POST /api/auth/users             — admin only: create user
- POST /api/auth/users/{{id}}/disable — admin only
- POST /api/auth/users/{{id}}/enable  — admin only
- GET  /api/auth/providers         — authenticated
- GET  /api/auth/sessions          — recent sign-ins for current user (auth events from audit log)
- POST /api/auth/password-reset/request  — public; issues a single-use, 15-min token (dev_token returned in non-prod)
- POST /api/auth/password-reset/confirm  — public; consumes token + rotates session_epoch
- POST /api/auth/users/{{id}}/mfa/reset    — admin; disables MFA + revokes all user's sessions
- POST /api/auth/users/{{id}}/mfa/require?required=true|false  — admin; toggles mfa_policy_required

## Patient endpoints
- GET    /api/patients                  — list (masked PHI by default)
- POST   /api/patients
- GET    /api/patients/{{id}}           — requires `reason` for non-admin break-glass
- PUT    /api/patients/{{id}}
- DELETE /api/patients/{{id}}           — soft-delete with 7-year retention (admin + reauth)
- GET    /api/patients/{{id}}/records
- POST   /api/patients/{{id}}/records   — admin/doctor (requires reauth)
- GET    /api/patients/{{id}}/export    — admin or patient-self: full JSON export

## Scheduling endpoints
- POST   /api/appointments
- GET    /api/appointments
- GET    /api/appointments/{{id}}
- PUT    /api/appointments/{{id}}
- POST   /api/appointments/{{id}}/cancel

## Communication endpoints
- GET    /api/notifications             — admin|staff (masked unless unmask=true)

## Audit endpoints
- GET    /api/audit-logs                — admin only (filters: actor_id, actor_email, entity_type, entity_id, action, outcome, phi_accessed, date_from, date_to, limit)
- GET    /api/audit-logs/export.csv     — admin only; streams CSV (same filter set)

## Health
- GET    /api/health


## Tenancy demo accounts (multi-tenant build)

### Platform admin (sees all tenants)
- email: `platform-admin@ccms.app`
- password: `Platform@ComplianceClinic1`
- role: `platform_admin` (tenant_id = None) — Owen Sinclair, Operations Lead

### Sunrise Chiro Group (multi-location demo)
All demo users share the password: `Sunrise@ComplianceClinic1`

| Email                            | Person           | Role   | Tenant scope         | Locations                     |
|----------------------------------|------------------|--------|----------------------|-------------------------------|
| group-admin@sunrise.ccms.app     | Parker Hayes     | admin  | entire tenant        | all                           |
| downtown-doc@sunrise.ccms.app    | Dr. Casey Nguyen | doctor | specific location    | Downtown Clinic               |
| floater-doc@sunrise.ccms.app     | Dr. Jules Okafor | doctor | multi-location       | Downtown + Uptown             |
| eastside-staff@sunrise.ccms.app  | Riley Thompson   | staff  | specific location    | Eastside Clinic               |

### Tenant endpoints
- GET  /api/tenancy/me/context                       — current user's tenant + visible locations
- GET  /api/tenancy/tenants                          — list tenants (tenant-scoped unless platform admin)
- POST /api/tenancy/tenants                          — platform admin only
- GET  /api/tenancy/tenants/{{id}}/locations          — tenant-scoped; further filtered by user's location access
- POST /api/tenancy/tenants/{{id}}/locations          — tenant admin or platform admin
"""
    (mem_dir / "test_credentials.md").write_text(content)
