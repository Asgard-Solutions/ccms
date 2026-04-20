"""Seed baseline roles, permissions, role_permissions, and default location."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from core.db import get_db_write
from services.authz.constants import (
    BASELINE_ROLES,
    LEGACY_ROLE_TO_KEY,
    PERMISSIONS,
    ROLE_GRANTS,
    permission_key,
)

logger = logging.getLogger("ccms.authz.seed")

DEFAULT_LOCATION_CODE = "HQ"
DEFAULT_LOCATION_NAME = "Main Clinic"


def _perm_label(p: dict) -> str:
    return p.get("label") or f"{p['resource']}.{p['action']}"


async def seed_authz() -> None:
    """Idempotent — safe to call on every boot."""
    db = get_db_write()
    now = datetime.now(timezone.utc).isoformat()

    # ---- roles ----
    for r in BASELINE_ROLES:
        await db.roles.update_one(
            {"key": r["key"]},
            {
                "$setOnInsert": {"id": str(uuid.uuid4()), "created_at": now},
                "$set": {
                    "key": r["key"],
                    "abbr": r["abbr"],
                    "name": r["name"],
                    "description": r["description"],
                    "is_system": r.get("is_system", True),
                    "privileged": r.get("privileged", False),
                    "service_account": r.get("service_account", False),
                    "legacy_role": r.get("legacy_role"),
                    "updated_at": now,
                },
            },
            upsert=True,
        )

    # ---- permissions ----
    for p in PERMISSIONS:
        key = _perm_label(p)
        await db.permissions.update_one(
            {"key": key},
            {
                "$setOnInsert": {"id": str(uuid.uuid4()), "created_at": now},
                "$set": {
                    "key": key,
                    "resource": p["resource"],
                    "action": p["action"],
                    "sensitivity": p.get("sensitivity", "medium"),
                    "phi": bool(p.get("phi")),
                    "clinical": bool(p.get("clinical")),
                    "financial": bool(p.get("financial")),
                    "export": bool(p.get("export")),
                    "destructive": bool(p.get("destructive")),
                    "updated_at": now,
                },
            },
            upsert=True,
        )

    # ---- role_permissions (grants) ----
    # Strategy: idempotent replace per-role so the matrix can evolve via code.
    for role_key, grants in ROLE_GRANTS.items():
        await db.role_permissions.delete_many(
            {"role_key": role_key, "custom": {"$ne": True}},
        )
        docs = []
        for grant in grants:
            flags = (grant.get("flags") or "").upper()
            docs.append({
                "id": str(uuid.uuid4()),
                "role_key": role_key,
                "permission_key": permission_key(grant["resource"], grant["action"]),
                "scope": grant.get("scope", "all_org"),
                "requires_mfa": "MFA" in flags,
                "requires_approval": "APR" in flags,
                "break_glass_allowed": "BG" in flags,
                "custom": False,
                "created_at": now,
            })
        if docs:
            await db.role_permissions.insert_many(docs)

    # ---- default location ----
    existing_loc = await db.locations.find_one({"code": DEFAULT_LOCATION_CODE}, {"_id": 0})
    if not existing_loc:
        loc_id = str(uuid.uuid4())
        await db.locations.insert_one({
            "id": loc_id,
            "name": DEFAULT_LOCATION_NAME,
            "code": DEFAULT_LOCATION_CODE,
            "timezone": "America/Los_Angeles",
            "status": "active",
            "created_at": now,
        })
        logger.info("authz.seed: created default location %s", loc_id)
    else:
        loc_id = existing_loc["id"]

    # ---- back-fill user_roles for seeded legacy users ----
    # If a user has a legacy `role` string but no `user_roles` rows, create one
    # so the new policy engine resolves them correctly immediately.
    async for user in db.users.find({}, {"_id": 0, "id": 1, "role": 1, "email": 1}):
        existing = await db.user_roles.find_one(
            {"user_id": user["id"], "status": "active"}, {"_id": 0, "id": 1},
        )
        if existing:
            continue
        mapped = LEGACY_ROLE_TO_KEY.get((user.get("role") or "").lower())
        if not mapped:
            continue
        await db.user_roles.insert_one({
            "id": str(uuid.uuid4()),
            "user_id": user["id"],
            "role_key": mapped,
            "status": "active",
            "assigned_at": now,
            "assigned_by_id": "seed",
            "assigned_by_email": "seed",
        })
        # Assign default location
        loc_already = await db.user_location_assignments.find_one(
            {"user_id": user["id"], "location_id": loc_id},
            {"_id": 0, "id": 1},
        )
        if not loc_already:
            await db.user_location_assignments.insert_one({
                "id": str(uuid.uuid4()),
                "user_id": user["id"],
                "location_id": loc_id,
                "status": "active",
                "assigned_at": now,
            })

    # ---- back-fill location_id on patients ----
    await db.patients.update_many(
        {"location_id": {"$exists": False}},
        {"$set": {"location_id": loc_id}},
    )

    # ---- back-fill patient_assignments for seeded doctor + demo patient ----
    doctor = await db.users.find_one({"role": "doctor"}, {"_id": 0, "id": 1})
    demo_patient = await db.patients.find_one({"email": "patient@ccms.app"}, {"_id": 0, "id": 1})
    if doctor and demo_patient:
        existing_pa = await db.patient_assignments.find_one(
            {"provider_id": doctor["id"], "patient_id": demo_patient["id"]},
            {"_id": 0, "id": 1},
        )
        if not existing_pa:
            await db.patient_assignments.insert_one({
                "id": str(uuid.uuid4()),
                "provider_id": doctor["id"],
                "patient_id": demo_patient["id"],
                "location_id": loc_id,
                "status": "active",
                "assigned_at": now,
            })

    logger.info("authz.seed complete: %d roles / %d perms / %d grant groups",
                len(BASELINE_ROLES), len(PERMISSIONS), len(ROLE_GRANTS))
