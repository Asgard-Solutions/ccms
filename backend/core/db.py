"""
Database access module — HIPAA-hardened indexes.
"""
import os
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

_client: AsyncIOMotorClient | None = None
_db: AsyncIOMotorDatabase | None = None


def get_client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return _client


def get_db() -> AsyncIOMotorDatabase:
    global _db
    if _db is None:
        _db = get_client()[os.environ["DB_NAME"]]
    return _db


async def close_client() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None


async def create_indexes() -> None:
    db = get_db()
    # Identity
    await db.users.create_index("email", unique=True)
    await db.users.create_index("role")
    await db.users.create_index("status")
    # Patients
    await db.patients.create_index("email")
    await db.patients.create_index("user_id")
    await db.patients.create_index("status")
    # Medical records
    await db.medical_records.create_index("patient_id")
    await db.medical_records.create_index([("patient_id", 1), ("recorded_at", -1)])
    # Appointments
    await db.appointments.create_index("patient_id")
    await db.appointments.create_index("provider_id")
    await db.appointments.create_index([("provider_id", 1), ("start_time", 1)])
    await db.appointments.create_index("status")
    # Notifications
    await db.notifications.create_index("appointment_id")
    await db.notifications.create_index([("created_at", -1)])
    # Audit logs (HIPAA retention-critical — hot indexes for admin review)
    await db.audit_logs.create_index([("created_at", -1)])
    await db.audit_logs.create_index("actor_id")
    await db.audit_logs.create_index([("entity_type", 1), ("entity_id", 1)])
    await db.audit_logs.create_index("phi_accessed")
    # Auth support
    await db.login_attempts.create_index("identifier")
    await db.password_reset_tokens.create_index(
        "expires_at", expireAfterSeconds=0
    )
