"""
services/authz/migration.py — legacy role migration helpers.

The pre-RBAC app used a free-form `user.role` string on each user
("admin", "doctor", "staff", "patient"). The new authz engine reads
from `user_roles` rows instead. `seed_authz()` already runs an
idempotent backfill on every boot, but an admin UI also needs:

  * a DRY-RUN that previews which users would be mapped, ambiguous, or
    unmapped — used for the migration review in the admin UI;
  * a MANUAL APPLY that runs the backfill once on demand and returns
    the same summary (useful after bulk imports).

Both operations are idempotent and safe to run multiple times.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from core.db import get_db_read, get_db_write
from services.authz.constants import LEGACY_ROLE_TO_KEY


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _iter_backfill_candidates(db) -> list[dict]:
    """Return users who have no active user_roles row, with a classification
    of what WOULD happen during backfill."""
    out: list[dict] = []
    async for user in db.users.find(
        {}, {"_id": 0, "id": 1, "role": 1, "email": 1, "tenant_id": 1, "status": 1},
    ):
        existing = await db.user_roles.find_one(
            {"user_id": user["id"], "status": "active"}, {"_id": 0, "id": 1},
        )
        if existing:
            continue  # already migrated → skip entirely
        legacy = (user.get("role") or "").lower().strip()
        mapped = LEGACY_ROLE_TO_KEY.get(legacy) if legacy else None
        out.append({
            "user_id": user["id"],
            "email": user.get("email"),
            "tenant_id": user.get("tenant_id"),
            "status": user.get("status", "active"),
            "legacy_role": legacy or None,
            "mapped_role_key": mapped,
            "classification": (
                "mapped" if mapped
                else "ambiguous" if legacy
                else "unmapped"
            ),
        })
    return out


async def dry_run_legacy_backfill(tenant_id: str | None = None) -> dict:
    """Return a summary of what `apply_legacy_backfill()` WOULD do
    without writing anything. Never mutates state.

    Filters candidates to the given tenant when provided (tenant admins
    shouldn't be able to see other tenants' users).
    """
    db = get_db_read()
    rows = await _iter_backfill_candidates(db)
    if tenant_id:
        rows = [r for r in rows if r.get("tenant_id") == tenant_id]
    mapped = [r for r in rows if r["classification"] == "mapped"]
    ambiguous = [r for r in rows if r["classification"] == "ambiguous"]
    unmapped = [r for r in rows if r["classification"] == "unmapped"]
    return {
        "total_candidates": len(rows),
        "count_mapped": len(mapped),
        "count_ambiguous": len(ambiguous),
        "count_unmapped": len(unmapped),
        "mapped": mapped[:200],
        "ambiguous": ambiguous[:200],
        "unmapped": unmapped[:200],
        "generated_at": _now_iso(),
    }


async def apply_legacy_backfill(
    *, actor_id: str, actor_email: str, tenant_id: str | None = None,
) -> dict:
    """Idempotent: create `user_roles` rows for any user with a legacy
    role but no active assignment. Users without a recognizable legacy
    role are LEFT UNCHANGED (surfaced as `unmapped` in dry run).

    Returns the same summary shape as `dry_run_legacy_backfill` plus
    an `applied_at` timestamp and an `inserted` list of user_ids that
    received a new row in this run.
    """
    db = get_db_write()
    now = _now_iso()
    rows = await _iter_backfill_candidates(db)
    if tenant_id:
        rows = [r for r in rows if r.get("tenant_id") == tenant_id]

    inserted: list[str] = []
    for r in rows:
        if r["classification"] != "mapped":
            continue
        # Double-check for race — re-read existing row since dry-run.
        existing = await db.user_roles.find_one(
            {"user_id": r["user_id"], "status": "active"}, {"_id": 0, "id": 1},
        )
        if existing:
            continue
        await db.user_roles.insert_one({
            "id": str(uuid.uuid4()),
            "user_id": r["user_id"],
            "role_key": r["mapped_role_key"],
            "status": "active",
            "assigned_at": now,
            "assigned_by_id": actor_id,
            "assigned_by_email": actor_email,
            "legacy_mapped_from": r["legacy_role"],
            "legacy_mapped_at": now,
        })
        inserted.append(r["user_id"])

    mapped = [r for r in rows if r["classification"] == "mapped"]
    ambiguous = [r for r in rows if r["classification"] == "ambiguous"]
    unmapped = [r for r in rows if r["classification"] == "unmapped"]
    return {
        "total_candidates": len(rows),
        "count_mapped": len(mapped),
        "count_ambiguous": len(ambiguous),
        "count_unmapped": len(unmapped),
        "inserted_count": len(inserted),
        "inserted": inserted[:200],
        "applied_at": now,
    }
