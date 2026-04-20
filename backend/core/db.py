"""
Database access — primary write + secondary read separation.

The runtime here is a single MongoDB instance, so `get_db_write()` and
`get_db_read()` may point at the same physical server. The split is an
*architectural* one: every router uses one or the other explicitly so that
when we deploy with a real replica set (or migrate to PostgreSQL with a read
replica), only the connection setup changes — call-sites do not.

How the split degrades to PostgreSQL later:
  - get_db_write()  →  AsyncSession(bind=primary_engine)
  - get_db_read()   →  AsyncSession(bind=replica_engine)
  - Read-after-write reads should switch to the primary engine for that one
    request, mirroring the `read_after_write_db()` helper below.
"""
import logging
import os

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ReadPreference

from core import metrics

logger = logging.getLogger("ccms.db")

_write_client: AsyncIOMotorClient | None = None
_read_client: AsyncIOMotorClient | None = None

_routing_stats = {"writes": 0, "reads": 0, "read_after_write": 0}


def routing_stats() -> dict:
    return dict(_routing_stats)


def reset_routing_stats() -> None:
    for k in _routing_stats:
        _routing_stats[k] = 0


def _name() -> str:
    return os.environ["DB_NAME"]


def get_write_client() -> AsyncIOMotorClient:
    global _write_client
    if _write_client is None:
        _write_client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return _write_client


def get_read_client() -> AsyncIOMotorClient:
    """Read client. Defaults to MONGO_READ_URL but falls back to MONGO_URL.

    When the URL points at a replica set, we set readPreference=secondaryPreferred
    so reads scatter to replicas with primary as a safety net.
    """
    global _read_client
    if _read_client is None:
        url = os.environ.get("MONGO_READ_URL") or os.environ["MONGO_URL"]
        _read_client = AsyncIOMotorClient(
            url, readPreference="secondaryPreferred"
        )
    return _read_client


def get_db_write() -> AsyncIOMotorDatabase:
    _routing_stats["writes"] += 1
    try:
        metrics.db_queries_total.labels(route="write").inc()
    except Exception:
        pass
    return get_write_client()[_name()]


def get_db_read() -> AsyncIOMotorDatabase:
    _routing_stats["reads"] += 1
    try:
        metrics.db_queries_total.labels(route="read").inc()
    except Exception:
        pass
    return get_read_client()[_name()]


def read_after_write_db() -> AsyncIOMotorDatabase:
    """Strongly-consistent read; always routes to the primary write node."""
    _routing_stats["read_after_write"] += 1
    try:
        metrics.db_queries_total.labels(route="read_after_write").inc()
    except Exception:
        pass
    return get_write_client()[_name()]


# Backwards-compatible aliases for legacy code paths.
def get_db() -> AsyncIOMotorDatabase:
    return get_db_write()


def get_client() -> AsyncIOMotorClient:
    return get_write_client()


async def close_client() -> None:
    global _write_client, _read_client
    for c in (_write_client, _read_client):
        if c is not None:
            c.close()
    _write_client = None
    _read_client = None


async def create_indexes() -> None:
    db = get_db_write()
    await db.users.create_index("email", unique=True)
    await db.users.create_index("role")
    await db.users.create_index("status")
    await db.users.create_index("tenant_id")
    await db.patients.create_index("email")
    await db.patients.create_index("user_id")
    await db.patients.create_index("status")
    await db.patients.create_index([("tenant_id", 1), ("status", 1), ("created_at", -1)])
    await db.patients.create_index([("tenant_id", 1), ("location_id", 1)])
    # Search-driven prefix + contains lookups on plaintext name/phone fields.
    # MongoDB uses regex-anchored indexes efficiently when the regex is
    # a prefix (^Jaco) — wildcard searches fall back to a scan but stay
    # bounded by the _CANDIDATE_CAP in search_router.
    await db.patients.create_index([("tenant_id", 1), ("last_name", 1)])
    await db.patients.create_index([("tenant_id", 1), ("first_name", 1)])
    await db.patients.create_index([("tenant_id", 1), ("phone", 1)])
    await db.medical_records.create_index("patient_id")
    await db.medical_records.create_index([("patient_id", 1), ("recorded_at", -1)])
    await db.medical_records.create_index([("tenant_id", 1), ("patient_id", 1)])
    await db.appointments.create_index("patient_id")
    await db.appointments.create_index("provider_id")
    await db.appointments.create_index([("provider_id", 1), ("start_time", 1)])
    await db.appointments.create_index("status")
    await db.appointments.create_index([("tenant_id", 1), ("location_id", 1), ("start_time", 1)])
    await db.appointments.create_index([("tenant_id", 1), ("provider_id", 1), ("start_time", 1)])
    await db.notifications.create_index("appointment_id")
    await db.notifications.create_index([("created_at", -1)])
    await db.notifications.create_index("tenant_id")
    await db.audit_logs.create_index([("created_at", -1)])
    await db.audit_logs.create_index("actor_id")
    await db.audit_logs.create_index([("entity_type", 1), ("entity_id", 1)])
    await db.audit_logs.create_index("phi_accessed")
    await db.audit_logs.create_index([("tenant_id", 1), ("created_at", -1)])
    await db.login_attempts.create_index("identifier")
    await db.password_reset_tokens.create_index("expires_at", expireAfterSeconds=0)
    await db.privacy_requests.create_index([("created_at", -1)])
    await db.privacy_requests.create_index("status")
    await db.privacy_requests.create_index("subject_user_id")
    await db.consent_records.create_index([("user_id", 1), ("accepted_at", -1)])
    await db.communication_preferences.create_index("user_id", unique=True)
    await db.patients.create_index("legal_hold")
    # Tenancy core
    await db.tenants.create_index("slug", unique=True)
    await db.tenants.create_index("status")
    await db.locations.create_index([("tenant_id", 1), ("status", 1)])
    await db.locations.create_index([("tenant_id", 1), ("name", 1)])
    # Authorization service indexes
    await db.roles.create_index("key", unique=True)
    await db.permissions.create_index("key", unique=True)
    await db.role_permissions.create_index("role_key")
    await db.role_permissions.create_index([("role_key", 1), ("permission_key", 1)])
    await db.user_roles.create_index([("user_id", 1), ("status", 1)])
    await db.user_roles.create_index("role_key")
    await db.locations.create_index("code", unique=True, sparse=True)
    await db.user_location_assignments.create_index([("user_id", 1), ("status", 1)])
    await db.user_location_assignments.create_index("location_id")
    await db.user_location_assignments.create_index([("tenant_id", 1), ("user_id", 1)])
    await db.patient_assignments.create_index([("provider_id", 1), ("status", 1)])
    await db.patient_assignments.create_index([("patient_id", 1), ("status", 1)])
    await db.elevation_requests.create_index([("requester_id", 1), ("status", 1)])
    await db.elevation_requests.create_index([("created_at", -1)])
    await db.permission_scopes.create_index([("user_id", 1), ("status", 1)])
    await db.permission_scopes.create_index("permission_key")
    await db.audit_logs.create_index("action")
    # Jobs & exports (iteration 16)
    await db.jobs.create_index([("tenant_id", 1), ("status", 1), ("created_at", -1)])
    await db.jobs.create_index([("tenant_id", 1), ("job_type", 1)])
    await db.exports.create_index([("tenant_id", 1), ("status", 1)])
    await db.exports.create_index("expires_at")
    # Compliance-ops (iteration 18)
    for coll in ("compliance_controls", "compliance_evidence", "compliance_risks",
                 "compliance_policies", "compliance_incidents", "compliance_vendors",
                 "compliance_data_classes", "compliance_access_reviews"):
        await db[coll].create_index([("tenant_id", 1), ("updated_at", -1)])
    await db.compliance_controls.create_index([("tenant_id", 1), ("family", 1)])
    await db.compliance_evidence.create_index([("tenant_id", 1), ("control_id", 1)])
    await db.compliance_access_reviews.create_index([("tenant_id", 1), ("due_at", 1)])
    # Workforce (iteration 19)
    await db.workforce_invitations.create_index([("tenant_id", 1), ("status", 1), ("created_at", -1)])
    await db.workforce_invitations.create_index("token_hash", unique=True, sparse=True)
    await db.workforce_invitations.create_index([("tenant_id", 1), ("email", 1)])
    await db.patient_proxies.create_index([("tenant_id", 1), ("patient_id", 1), ("status", 1)])
    await db.patient_proxies.create_index([("tenant_id", 1), ("proxy_user_id", 1)])
    await db.break_glass_events.create_index([("tenant_id", 1), ("actor_id", 1), ("status", 1)])
    await db.break_glass_events.create_index([("tenant_id", 1), ("status", 1), ("activated_at", -1)])
    await db.break_glass_events.create_index("attestation_due_at")
    # Clinic profile (iteration 21 — clinic hours)
    await db.clinic_profiles.create_index([("tenant_id", 1), ("location_id", 1)], unique=True)
    await db.clinic_profiles.create_index([("tenant_id", 1), ("name", 1)])
