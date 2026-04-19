"""
Seed the initial admin + demo users on startup (idempotent).
Also writes /app/memory/test_credentials.md so the testing agent can read them.
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
        "password": "Doctor@123",
        "name": "Dr. Alicia Monroe",
        "role": "doctor",
        "phone": "+1-555-0102",
    },
    {
        "email": "staff@ccms.app",
        "password": "Staff@123",
        "name": "Jamie Reyes",
        "role": "staff",
        "phone": "+1-555-0103",
    },
    {
        "email": "patient@ccms.app",
        "password": "Patient@123",
        "name": "Morgan Lee",
        "role": "patient",
        "phone": "+1-555-0104",
    },
]


async def _upsert_user(email: str, password: str, name: str, role: str, phone: str | None) -> None:
    db = get_db()
    existing = await db.users.find_one({"email": email}, {"_id": 0})
    now = datetime.now(timezone.utc).isoformat()
    if existing is None:
        await db.users.insert_one(
            {
                "id": str(uuid.uuid4()),
                "email": email,
                "password_hash": hash_password(password),
                "name": name,
                "role": role,
                "phone": phone,
                "created_at": now,
                "updated_at": now,
            }
        )
    elif not verify_password(password, existing["password_hash"]):
        await db.users.update_one(
            {"email": email},
            {"$set": {"password_hash": hash_password(password), "updated_at": now}},
        )


async def seed() -> None:
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@ccms.local")
    admin_password = os.environ.get("ADMIN_PASSWORD", "Admin@123")
    await _upsert_user(admin_email, admin_password, "System Admin", "admin", "+1-555-0101")

    for u in DEMO_USERS:
        await _upsert_user(**u)

    # Seed a demo patient record linked to the demo patient user
    db = get_db()
    patient_email = "patient@ccms.app"
    patient_user = await db.users.find_one({"email": patient_email}, {"_id": 0})
    if patient_user:
        # Re-link any stale Morgan Lee record left behind by earlier seeds.
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
                    "address": "124 Willow Lane, Portland, OR",
                    "emergency_contact": "Taylor Lee (+1-555-0199)",
                    "notes": "Initial intake — chronic lower-back discomfort.",
                    "created_at": now,
                    "updated_at": now,
                }
            )

    await _write_credentials_file(admin_email, admin_password)


async def _write_credentials_file(admin_email: str, admin_password: str) -> None:
    mem_dir = Path("/app/memory")
    mem_dir.mkdir(parents=True, exist_ok=True)
    content = f"""# CCMS Test Credentials

> Cookie-based JWT auth. All requests must send cookies (`withCredentials: true`).

## Admin
- email: `{admin_email}`
- password: `{admin_password}`
- role: `admin`

## Demo Users
| Role    | Email                  | Password      |
|---------|------------------------|---------------|
| doctor  | doctor@ccms.app        | Doctor@123    |
| staff   | staff@ccms.app         | Staff@123     |
| patient | patient@ccms.app       | Patient@123   |

## Auth endpoints
- POST `/api/auth/register`        — public self-register (always creates a `patient` role)
- POST `/api/auth/login`           — returns user + sets `access_token` & `refresh_token` cookies
- POST `/api/auth/logout`          — clears cookies
- GET  `/api/auth/me`              — current authenticated user
- POST `/api/auth/refresh`         — refresh access token using the refresh cookie
- GET  `/api/auth/users`           — admin only: list users (optional `?role=doctor`)
- POST `/api/auth/users`           — admin only: create user with any role
- GET  `/api/auth/providers`       — any authenticated user: list doctors

## Patient endpoints
- GET    `/api/patients`                  — admin|doctor|staff
- POST   `/api/patients`                  — admin|doctor|staff
- GET    `/api/patients/{{id}}`             — admin|doctor|staff (or patient-self)
- PUT    `/api/patients/{{id}}`             — admin|doctor|staff
- DELETE `/api/patients/{{id}}`             — admin
- GET    `/api/patients/{{id}}/records`     — admin|doctor|staff (or patient-self)
- POST   `/api/patients/{{id}}/records`     — admin|doctor

## Scheduling endpoints
- POST   `/api/appointments`               — admin|doctor|staff
- GET    `/api/appointments`               — authenticated; patients see only their own
- GET    `/api/appointments/{{id}}`          — authenticated; patients see only their own
- PUT    `/api/appointments/{{id}}`          — admin|doctor|staff (reschedule/update)
- POST   `/api/appointments/{{id}}/cancel`   — admin|doctor|staff|patient-owner

## Communication endpoints
- GET    `/api/notifications`              — admin|staff

## Health
- GET    `/api/health`
"""
    (mem_dir / "test_credentials.md").write_text(content)
