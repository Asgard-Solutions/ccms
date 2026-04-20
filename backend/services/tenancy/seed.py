"""
Tenancy seed — idempotent.

On every boot:
1. Ensures the "Default Practice" tenant exists.
2. Backfills `tenant_id` on every legacy row that is missing one.
3. Backfills the single existing location (if any) to belong to this tenant;
   else creates a "Main Office" location.
4. Ensures the demo multi-location tenant "Sunrise Chiro Group" exists, with
   three locations (Downtown, Uptown, Eastside) and four demo users with
   varied access scopes.
5. Ensures a `platform_admin@ccms.app` global platform user exists.

This seed is the single authoritative place that converts legacy single-DB
installs into multi-tenant rows without data loss.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.db import get_db_write
from core.security import hash_password

logger = logging.getLogger("ccms.tenancy.seed")

DEFAULT_TENANT_SLUG = "default"
DEFAULT_TENANT_NAME = "Default Practice"

GROUP_TENANT_SLUG = "sunrise-chiro"
GROUP_TENANT_NAME = "Sunrise Chiro Group"

GROUP_LOCATIONS = [
    {"name": "Downtown Clinic", "code": "SUN-DT", "timezone": "America/Los_Angeles"},
    {"name": "Uptown Clinic", "code": "SUN-UP", "timezone": "America/Los_Angeles"},
    {"name": "Eastside Clinic", "code": "SUN-ES", "timezone": "America/Los_Angeles"},
]

# Password shared by demo users — meets the 12-char + complexity policy.
DEMO_PASSWORD = "Sunrise@ComplianceClinic1"

GROUP_USERS = [
    {
        "email": "group-admin@sunrise.ccms.app",
        "name": "Parker Hayes",
        "role": "admin",              # tenant-wide admin
        "phone": "+1-555-0201",
        "tenant_scope_all": True,     # sees ALL locations in the tenant
        "locations": [],              # ignored when scope_all is True
    },
    {
        "email": "downtown-doc@sunrise.ccms.app",
        "name": "Dr. Casey Nguyen",
        "role": "doctor",
        "phone": "+1-555-0202",
        "tenant_scope_all": False,
        "locations": ["Downtown Clinic"],
    },
    {
        "email": "floater-doc@sunrise.ccms.app",
        "name": "Dr. Jules Okafor",
        "role": "doctor",
        "phone": "+1-555-0203",
        "tenant_scope_all": False,
        "locations": ["Downtown Clinic", "Uptown Clinic"],
    },
    {
        "email": "eastside-staff@sunrise.ccms.app",
        "name": "Riley Thompson",
        "role": "staff",
        "phone": "+1-555-0204",
        "tenant_scope_all": False,
        "locations": ["Eastside Clinic"],
    },
]

PLATFORM_ADMIN_EMAIL = "platform-admin@ccms.app"
PLATFORM_ADMIN_PASSWORD = "Platform@ComplianceClinic1"

# Every tenant-owned collection that must carry `tenant_id`.
TENANT_SCOPED_COLLECTIONS = [
    "users",
    "patients",
    "appointments",
    "medical_records",
    "notifications",
    "audit_logs",
    "consent_records",
    "communication_preferences",
    "privacy_requests",
    "password_reset_tokens",
    "login_attempts",
    "permission_scopes",
    "elevation_requests",
    "user_roles",
    "user_location_assignments",
    "patient_assignments",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _upsert_tenant(slug: str, name: str, type_: str) -> str:
    db = get_db_write()
    existing = await db.tenants.find_one({"slug": slug}, {"_id": 0})
    if existing:
        return existing["id"]
    tenant_id = str(uuid.uuid4())
    now = _now()
    await db.tenants.insert_one({
        "id": tenant_id,
        "slug": slug,
        "name": name,
        "type": type_,
        "status": "active",
        "db_tier": "shared",
        "created_at": now,
        "updated_at": now,
    })
    logger.info("tenancy.seed: created tenant slug=%s id=%s", slug, tenant_id)
    return tenant_id


async def _ensure_location(tenant_id: str, name: str, code: str | None, tz: str) -> str:
    db = get_db_write()
    q: dict = {"tenant_id": tenant_id, "name": name}
    existing = await db.locations.find_one(q, {"_id": 0})
    if existing:
        # Make sure tenant_id is stamped.
        if not existing.get("tenant_id"):
            await db.locations.update_one(
                {"id": existing["id"]}, {"$set": {"tenant_id": tenant_id}},
            )
        return existing["id"]
    loc_id = str(uuid.uuid4())
    now = _now()
    await db.locations.insert_one({
        "id": loc_id,
        "tenant_id": tenant_id,
        "name": name,
        "code": code,
        "timezone": tz,
        "status": "active",
        "created_at": now,
        "updated_at": now,
    })
    return loc_id


async def _backfill_default_tenant(default_tenant_id: str, default_location_id: str) -> None:
    """Attach tenant_id (and location_id where applicable) to every legacy row."""
    db = get_db_write()

    # Any existing `location` docs WITHOUT a tenant_id belong to the default.
    res = await db.locations.update_many(
        {"tenant_id": {"$exists": False}},
        {"$set": {"tenant_id": default_tenant_id}},
    )
    if res.modified_count:
        logger.info("tenancy.seed: backfilled %d legacy locations → default tenant",
                    res.modified_count)

    # All legacy users without tenant_id belong to the default tenant UNLESS
    # they were explicitly created under a group tenant already.
    await db.users.update_many(
        {"tenant_id": {"$exists": False}, "role": {"$ne": "platform_admin"}},
        {"$set": {"tenant_id": default_tenant_id}},
    )

    # Every other tenant-owned collection.
    for coll in TENANT_SCOPED_COLLECTIONS:
        if coll == "users":
            continue  # already handled above
        res = await db[coll].update_many(
            {"tenant_id": {"$exists": False}},
            {"$set": {"tenant_id": default_tenant_id}},
        )
        if res.modified_count:
            logger.info("tenancy.seed: backfilled %d %s → default tenant",
                        res.modified_count, coll)

    # Patients & appointments may be missing location_id — assign to default.
    for coll in ("patients", "appointments", "medical_records"):
        await db[coll].update_many(
            {"tenant_id": default_tenant_id, "location_id": {"$exists": False}},
            {"$set": {"location_id": default_location_id}},
        )


async def _upsert_group_demo(group_tenant_id: str, location_name_to_id: dict[str, str]) -> None:
    db = get_db_write()
    now = _now()

    for spec in GROUP_USERS:
        existing = await db.users.find_one({"email": spec["email"]}, {"_id": 0})
        hashed = hash_password(DEMO_PASSWORD)
        base = {
            "email": spec["email"],
            "name": spec["name"],
            "role": spec["role"],
            "phone": spec["phone"],
            "status": "active",
            "tenant_id": group_tenant_id,
            "tenant_scope_all": spec["tenant_scope_all"],
            "mfa_enabled": False,
            "mfa_policy_required": False,
            "updated_at": now,
        }
        if existing is None:
            user_id = str(uuid.uuid4())
            await db.users.insert_one({
                "id": user_id,
                "password_hash": hashed,
                "password_history": [hashed],
                "password_changed_at": now,
                "session_epoch": 0,
                "created_at": now,
                **base,
            })
        else:
            user_id = existing["id"]
            updates = dict(base)
            # Ensure seeded password still works after re-seed.
            from core.security import verify_password
            if not verify_password(DEMO_PASSWORD, existing["password_hash"]):
                updates["password_hash"] = hashed
                updates["password_history"] = (existing.get("password_history") or [])[-4:] + [hashed]
                updates["password_changed_at"] = now
            await db.users.update_one({"id": user_id}, {"$set": updates})

        # Location assignments.
        # First wipe the user's assignments in THIS tenant so the list stays
        # in sync with the spec (important on re-seed after editing GROUP_USERS).
        location_ids = [location_name_to_id[n] for n in spec["locations"] if n in location_name_to_id]
        await db.user_location_assignments.update_many(
            {"user_id": user_id, "tenant_id": group_tenant_id, "status": "active"},
            {"$set": {"status": "inactive"}},
        )
        for loc_id in location_ids:
            await db.user_location_assignments.insert_one({
                "id": str(uuid.uuid4()),
                "user_id": user_id,
                "tenant_id": group_tenant_id,
                "location_id": loc_id,
                "status": "active",
                "assigned_at": now,
                "assigned_by_id": "seed",
            })


async def _upsert_platform_admin() -> None:
    db = get_db_write()
    existing = await db.users.find_one({"email": PLATFORM_ADMIN_EMAIL}, {"_id": 0})
    hashed = hash_password(PLATFORM_ADMIN_PASSWORD)
    now = _now()
    base = {
        "email": PLATFORM_ADMIN_EMAIL,
        "name": "Platform Operator",
        "role": "platform_admin",
        "phone": "+1-555-0099",
        "status": "active",
        "tenant_id": None,              # global user, not tenant-bound
        "tenant_scope_all": True,
        "is_platform_admin": True,
        "mfa_enabled": False,
        "mfa_policy_required": False,
        "updated_at": now,
    }
    if existing is None:
        await db.users.insert_one({
            "id": str(uuid.uuid4()),
            "password_hash": hashed,
            "password_history": [hashed],
            "password_changed_at": now,
            "session_epoch": 0,
            "created_at": now,
            **base,
        })
    else:
        from core.security import verify_password
        updates = dict(base)
        if not verify_password(PLATFORM_ADMIN_PASSWORD, existing["password_hash"]):
            updates["password_hash"] = hashed
            updates["password_history"] = (existing.get("password_history") or [])[-4:] + [hashed]
            updates["password_changed_at"] = now
        await db.users.update_one({"id": existing["id"]}, {"$set": updates})


async def _seed_group_sample_data(group_tenant_id: str, location_name_to_id: dict[str, str]) -> None:
    """Create 2 patients + 1 appointment + 1 note per Sunrise location
    (idempotent — only runs when the location has zero existing patients).
    This gives iteration_15 tests and live demos something to show without
    forcing the operator to click around."""
    from core.crypto import encrypt_text

    db = get_db_write()
    now = _now()

    # Find the doctors.
    downtown_doc = await db.users.find_one(
        {"email": "downtown-doc@sunrise.ccms.app"}, {"_id": 0, "id": 1, "name": 1},
    )
    floater_doc = await db.users.find_one(
        {"email": "floater-doc@sunrise.ccms.app"}, {"_id": 0, "id": 1, "name": 1},
    )
    if not (downtown_doc and floater_doc):
        return

    doctor_per_location = {
        "Downtown Clinic": downtown_doc["id"],
        "Uptown Clinic": floater_doc["id"],
        "Eastside Clinic": floater_doc["id"],
    }

    patient_samples = [
        ("Downtown Clinic", "Avery", "Bennett", "1985-06-14"),
        ("Downtown Clinic", "Sam",   "Calder",  "1979-11-02"),
        ("Uptown Clinic",   "Drew",  "Patel",   "1992-03-21"),
        ("Uptown Clinic",   "Quinn", "Vasquez", "1988-08-09"),
        ("Eastside Clinic", "Robin", "Harper",  "1975-12-30"),
        ("Eastside Clinic", "Jordan","Okafor",  "1996-05-17"),
    ]

    for loc_name, first, last, dob in patient_samples:
        loc_id = location_name_to_id.get(loc_name)
        if not loc_id:
            continue
        existing = await db.patients.find_one(
            {"tenant_id": group_tenant_id, "location_id": loc_id,
             "first_name": first, "last_name": last},
            {"_id": 0, "id": 1},
        )
        if existing:
            continue
        patient_id = str(uuid.uuid4())
        await db.patients.insert_one({
            "id": patient_id,
            "tenant_id": group_tenant_id,
            "location_id": loc_id,
            "user_id": None,
            "first_name": first,
            "last_name": last,
            "date_of_birth": encrypt_text(dob),
            "gender": "prefer-not-to-say",
            "phone": "+1-555-0" + str(abs(hash(first + last)) % 10000).zfill(4),
            "email": f"{first.lower()}.{last.lower()}@sunrise.ccms.app",
            "address": encrypt_text(f"100 {loc_name}, OR"),
            "emergency_contact": encrypt_text(f"{last} family +1-555-9999"),
            "notes": encrypt_text("Initial intake — sample demo data."),
            "status": "active",
            "created_at": now,
            "updated_at": now,
        })

        # Sample medical record (note) — encrypts PHI fields.
        doctor_id = doctor_per_location[loc_name]
        await db.medical_records.insert_one({
            "id": str(uuid.uuid4()),
            "tenant_id": group_tenant_id,
            "location_id": loc_id,
            "patient_id": patient_id,
            "record_type": "assessment",
            "title": "Initial spinal assessment",
            "description": encrypt_text("Range of motion 80%. Mild scoliosis."),
            "diagnosis": encrypt_text("Chronic lumbar strain."),
            "treatment": encrypt_text("12-visit adjustment plan."),
            "recorded_by": doctor_id,
            "recorded_at": now,
        })

        # Sample appointment 30 days out.
        from datetime import timedelta
        start = (datetime.now(timezone.utc) + timedelta(days=30 + abs(hash(patient_id)) % 14))
        start = start.replace(hour=10, minute=0, second=0, microsecond=0)
        end = start + timedelta(minutes=30)
        await db.appointments.insert_one({
            "id": str(uuid.uuid4()),
            "tenant_id": group_tenant_id,
            "location_id": loc_id,
            "patient_id": patient_id,
            "provider_id": doctor_id,
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "reason": "Follow-up adjustment",
            "notes": encrypt_text("Demo seed"),
            "status": "scheduled",
            "created_by": doctor_id,
            "created_at": now,
            "updated_at": now,
        })


async def seed_tenancy() -> None:
    """Idempotent — runs on every boot."""
    # 1. Default tenant for legacy data
    default_tenant_id = await _upsert_tenant(DEFAULT_TENANT_SLUG, DEFAULT_TENANT_NAME, "single")

    # 2. Adopt any existing location (from the legacy authz seed) OR create one.
    db = get_db_write()
    legacy_loc = await db.locations.find_one(
        {"tenant_id": {"$in": [None, default_tenant_id]}}, {"_id": 0},
    ) or await db.locations.find_one({"tenant_id": {"$exists": False}}, {"_id": 0})
    if legacy_loc:
        default_location_id = legacy_loc["id"]
        await db.locations.update_one(
            {"id": default_location_id},
            {"$set": {"tenant_id": default_tenant_id}},
        )
    else:
        default_location_id = await _ensure_location(
            default_tenant_id, "Main Office", "HQ", "America/Los_Angeles",
        )

    # 3. Backfill tenant_id across every legacy row.
    await _backfill_default_tenant(default_tenant_id, default_location_id)

    # 4. Ensure demo group tenant + locations + users.
    group_tenant_id = await _upsert_tenant(GROUP_TENANT_SLUG, GROUP_TENANT_NAME, "group")
    location_name_to_id: dict[str, str] = {}
    for spec in GROUP_LOCATIONS:
        loc_id = await _ensure_location(
            group_tenant_id, spec["name"], spec["code"], spec["timezone"],
        )
        location_name_to_id[spec["name"]] = loc_id
    await _upsert_group_demo(group_tenant_id, location_name_to_id)
    await _seed_group_sample_data(group_tenant_id, location_name_to_id)

    # 5. Platform admin.
    await _upsert_platform_admin()

    logger.info("tenancy.seed complete — default=%s group=%s", default_tenant_id, group_tenant_id)


async def _write_credentials_appendix() -> None:
    """Append tenancy demo users to test_credentials.md (idempotent)."""
    path = Path("/app/memory/test_credentials.md")
    if not path.exists():
        return
    marker = "## Tenancy demo accounts"
    text = path.read_text()
    if marker in text:
        return
    text = text.rstrip() + f"""


{marker}

### Platform admin (sees all tenants)
- email: `{PLATFORM_ADMIN_EMAIL}`
- password: `{PLATFORM_ADMIN_PASSWORD}`
- role: `platform_admin` (tenant_id = None)

### Sunrise Chiro Group (multi-location demo)
All demo users share the password: `{DEMO_PASSWORD}`

| Email                          | Role   | Tenant scope          | Locations                     |
|--------------------------------|--------|-----------------------|-------------------------------|
| group-admin@sunrise.ccms.app   | admin  | entire tenant         | all                           |
| downtown-doc@sunrise.ccms.app  | doctor | specific location     | Downtown Clinic               |
| floater-doc@sunrise.ccms.app   | doctor | multi-location        | Downtown + Uptown             |
| eastside-staff@sunrise.ccms.app| staff  | specific location     | Eastside Clinic               |

### Tenant endpoints
- GET  /api/tenancy/me/context                            — current user's tenant + visible locations
- GET  /api/tenancy/tenants                               — list tenants (tenant-scoped unless platform admin)
- POST /api/tenancy/tenants                               — platform admin only
- GET  /api/tenancy/tenants/{{id}}/locations               — tenant-scoped; further filtered by user's location access
- POST /api/tenancy/tenants/{{id}}/locations               — tenant admin or platform admin
"""
    path.write_text(text)
