"""
Seed admin + demo users on startup (idempotent).
All seeded passwords meet the HIPAA-compliant strength policy.
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
        "name": "Dr. Alicia Monroe",
        "role": "doctor",
        "phone": "+1-555-0102",
    },
    {
        "email": "staff@ccms.app",
        "password": "Staff@ComplianceClinic1",
        "name": "Jamie Reyes",
        "role": "staff",
        "phone": "+1-555-0103",
    },
    {
        "email": "patient@ccms.app",
        "password": "Patient@ComplianceClinic1",
        "name": "Morgan Lee",
        "role": "patient",
        "phone": "+1-555-0104",
    },
]


async def _upsert_user(email: str, password: str, name: str, role: str, phone: str) -> None:
    db = get_db()
    existing = await db.users.find_one({"email": email}, {"_id": 0})
    now = datetime.now(timezone.utc).isoformat()
    hashed = hash_password(password)
    base = {
        "email": email,
        "name": name,
        "role": role,
        "phone": phone,
        "status": "active",
        "updated_at": now,
    }
    if existing is None:
        await db.users.insert_one(
            {
                "id": str(uuid.uuid4()),
                "password_hash": hashed,
                "password_history": [hashed],
                "password_changed_at": now,
                "mfa_enabled": False,
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
        await db.users.update_one({"email": email}, {"$set": updates})


async def seed() -> None:
    from core.crypto import encrypt_text  # avoid import cycle at module load

    admin_email = os.environ.get("ADMIN_EMAIL", "admin@ccms.app")
    admin_password = os.environ.get("ADMIN_PASSWORD", "Admin@ComplianceClinic1")
    await _upsert_user(admin_email, admin_password, "System Admin", "admin", "+1-555-0101")
    for u in DEMO_USERS:
        await _upsert_user(**u)

    # Seed a demo patient record linked to the demo patient user
    db = get_db()
    patient_email = "patient@ccms.app"
    patient_user = await db.users.find_one({"email": patient_email}, {"_id": 0})
    if patient_user:
        await db.patients.update_many(
            {"email": {"$in": ["patient@ccms.local", patient_email]}},
            {"$set": {"user_id": patient_user["id"], "email": patient_email}},
        )
        has_record = await db.patients.find_one(
            {"user_id": patient_user["id"]}, {"_id": 0, "id": 1}
        )
        if not has_record:
            now = datetime.now(timezone.utc).isoformat()
            await db.patients.insert_one(
                {
                    "id": str(uuid.uuid4()),
                    "user_id": patient_user["id"],
                    "first_name": "Morgan",
                    "last_name": "Lee",
                    "date_of_birth": "1990-04-12",
                    "gender": "non-binary",
                    "phone": patient_user.get("phone"),
                    "email": patient_user["email"],
                    # PHI free-text fields are encrypted at rest.
                    "address": encrypt_text("124 Willow Lane, Portland, OR"),
                    "emergency_contact": encrypt_text("Taylor Lee (+1-555-0199)"),
                    "notes": encrypt_text(
                        "Initial intake — chronic lower-back discomfort."
                    ),
                    "status": "active",
                    "created_at": now,
                    "updated_at": now,
                }
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

## Admin
- email: `{admin_email}`
- password: `{admin_password}`
- role: `admin`

## Demo users
| Role    | Email                 | Password                      |
|---------|-----------------------|-------------------------------|
| doctor  | doctor@ccms.app       | Doctor@ComplianceClinic1      |
| staff   | staff@ccms.app        | Staff@ComplianceClinic1       |
| patient | patient@ccms.app      | Patient@ComplianceClinic1     |

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
- GET    /api/audit-logs                — admin only

## Health
- GET    /api/health
"""
    (mem_dir / "test_credentials.md").write_text(content)
