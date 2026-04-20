"""Clinic Profile router — `/api/clinic-profiles/*`.

- 1:1 with `locations`: a profile is keyed by `location_id`.
- Tenant-scoped on every read/write via `scoped_filter` / `stamp_for_write`.
- Location-scoped for non-tenant-wide users; 403 on cross-location writes.
- Every mutation appends a `history[]` entry on the doc AND emits an
  audit_log row (`clinic_profile.created` / `updated` / `deleted`).

Permissioning:
    - GET (list/detail): admin | doctor | staff (the frontend and the
      scheduling flows need to show office hours). Patients currently do not
      see this endpoint — public hours display is a separate consideration.
    - POST/PUT/DELETE: admin only.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status

from core.audit import audit_success
from core.deps import require_role
from core.tenancy import TenantContext, require_tenant, tenant_db
from core.tenant_scope import scoped_filter, stamp_for_write
from services.clinic_profile.models import (
    ClinicProfileCreate,
    ClinicProfilePublic,
    ClinicProfileUpdate,
    DayHours,
    HoursInterval,
)

router = APIRouter(prefix="/clinic-profiles", tags=["clinic-profile"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _public(doc: dict) -> dict:
    # Strip Mongo _id and any internal/history fields from the public view.
    out = {k: v for k, v in doc.items() if k != "_id"}
    out.pop("history", None)
    # Normalize hours into validated shape (also stabilizes order by day).
    hours = out.get("hours") or []
    out["hours"] = [
        DayHours(
            day_of_week=h["day_of_week"],
            is_closed=h.get("is_closed", False),
            intervals=[HoursInterval(**i) for i in (h.get("intervals") or [])],
        ).model_dump()
        for h in sorted(hours, key=lambda h: h["day_of_week"])
    ]
    return out


async def _ensure_location_access(
    db, tenant_id: str, location_id: str, ctx: TenantContext
) -> dict:
    """Verifies the target location exists in this tenant AND the caller can
    administer it. Returns the location doc."""
    loc = await db.locations.find_one(
        {"id": location_id, "tenant_id": tenant_id}, {"_id": 0}
    )
    if not loc:
        # 404 (not 403) — cross-tenant probes should never leak existence.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Location not found")
    if not (ctx.tenant_scope_all or ctx.is_platform_admin):
        if location_id not in ctx.allowed_location_ids:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Location not in your access scope")
    return loc


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------
@router.get("", response_model=list[ClinicProfilePublic])
async def list_clinic_profiles(
    request: Request,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    q = scoped_filter({}, ctx, location_scoped=True)
    if q.get("__deny__"):
        return []
    cursor = db.clinic_profiles.find(q, {"_id": 0}).sort("name", 1)
    rows = [_public(doc) async for doc in cursor]
    await audit_success(
        user, "clinic_profile.list_viewed", request,
        metadata={"count": len(rows), "tenant_id": ctx.tenant_id},
    )
    return rows


# ---------------------------------------------------------------------------
# Read one (by profile id OR by location id — accept both for UX)
# ---------------------------------------------------------------------------
@router.get("/{profile_or_location_id}", response_model=ClinicProfilePublic)
async def get_clinic_profile(
    profile_or_location_id: str,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    base_q: dict = {
        "$or": [
            {"id": profile_or_location_id},
            {"location_id": profile_or_location_id},
        ]
    }
    q = scoped_filter(base_q, ctx, location_scoped=True)
    if q.get("__deny__"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Clinic profile not found")
    doc = await db.clinic_profiles.find_one(q, {"_id": 0})
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Clinic profile not found")
    await audit_success(
        user, "clinic_profile.read", request,
        entity_type="clinic_profile", entity_id=doc["id"],
        metadata={"location_id": doc["location_id"]},
    )
    return _public(doc)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------
@router.post("", response_model=ClinicProfilePublic, status_code=201)
async def create_clinic_profile(
    payload: ClinicProfileCreate,
    request: Request,
    user: dict = Depends(require_role("admin")),
    ctx: TenantContext = Depends(require_tenant),
):
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tenant context required")
    db = tenant_db(ctx.tenant_id)
    await _ensure_location_access(db, ctx.tenant_id, payload.location_id, ctx)

    # Uniqueness: one profile per (tenant, location).
    existing = await db.clinic_profiles.find_one(
        {"tenant_id": ctx.tenant_id, "location_id": payload.location_id},
        {"_id": 0, "id": 1},
    )
    if existing:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Clinic profile already exists for this location; PUT to update",
        )

    now = _now()
    pid = str(uuid.uuid4())
    doc = stamp_for_write(
        {
            "id": pid,
            "name": payload.name.strip(),
            "address_line1": payload.address_line1,
            "address_line2": payload.address_line2,
            "city": payload.city,
            "state": payload.state,
            "postal_code": payload.postal_code,
            "country": payload.country,
            "primary_phone": payload.primary_phone,
            "secondary_phone": payload.secondary_phone,
            "email": payload.email,
            "website": payload.website,
            "timezone": payload.timezone,
            "notes": payload.notes,
            "hours": [h.model_dump() for h in payload.hours],
            "created_at": now,
            "updated_at": now,
            "created_by": user["id"],
            "updated_by": user["id"],
            "history": [{"at": now, "by": user["id"], "action": "created"}],
        },
        ctx,
        location_id=payload.location_id,
    )
    await db.clinic_profiles.insert_one(doc)
    await audit_success(
        user, "clinic_profile.created", request,
        entity_type="clinic_profile", entity_id=pid,
        metadata={"location_id": payload.location_id, "name": doc["name"]},
    )
    return _public(doc)


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------
@router.put("/{profile_or_location_id}", response_model=ClinicProfilePublic)
async def update_clinic_profile(
    profile_or_location_id: str,
    payload: ClinicProfileUpdate,
    request: Request,
    user: dict = Depends(require_role("admin")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    base_q: dict = {
        "$or": [
            {"id": profile_or_location_id},
            {"location_id": profile_or_location_id},
        ]
    }
    q = scoped_filter(base_q, ctx, location_scoped=True)
    if q.get("__deny__"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Clinic profile not found")
    current = await db.clinic_profiles.find_one(q, {"_id": 0})
    if not current:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Clinic profile not found")

    updates: dict = {}
    dumped = payload.model_dump(exclude_unset=True)
    for k, v in dumped.items():
        if k == "hours" and v is not None:
            updates["hours"] = [h if isinstance(h, dict) else h.model_dump() for h in v]
        elif k == "name" and v is not None:
            updates["name"] = v.strip()
        else:
            updates[k] = v

    if not updates:
        return _public(current)

    now = _now()
    updates["updated_at"] = now
    updates["updated_by"] = user["id"]
    history_entry = {
        "at": now, "by": user["id"], "action": "updated",
        "fields": sorted(list(updates.keys() - {"updated_at", "updated_by"})),
    }

    await db.clinic_profiles.update_one(
        {"id": current["id"], "tenant_id": current["tenant_id"]},
        {"$set": updates, "$push": {"history": history_entry}},
    )
    fresh = await db.clinic_profiles.find_one(
        {"id": current["id"], "tenant_id": current["tenant_id"]}, {"_id": 0}
    )
    await audit_success(
        user, "clinic_profile.updated", request,
        entity_type="clinic_profile", entity_id=current["id"],
        metadata={
            "location_id": current["location_id"],
            "fields": history_entry["fields"],
        },
    )
    return _public(fresh)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------
@router.delete("/{profile_or_location_id}", status_code=204)
async def delete_clinic_profile(
    profile_or_location_id: str,
    request: Request,
    user: dict = Depends(require_role("admin")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    base_q: dict = {
        "$or": [
            {"id": profile_or_location_id},
            {"location_id": profile_or_location_id},
        ]
    }
    q = scoped_filter(base_q, ctx, location_scoped=True)
    if q.get("__deny__"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Clinic profile not found")
    current = await db.clinic_profiles.find_one(q, {"_id": 0, "id": 1, "location_id": 1, "tenant_id": 1})
    if not current:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Clinic profile not found")
    await db.clinic_profiles.delete_one(
        {"id": current["id"], "tenant_id": current["tenant_id"]}
    )
    await audit_success(
        user, "clinic_profile.deleted", request,
        entity_type="clinic_profile", entity_id=current["id"],
        metadata={"location_id": current["location_id"]},
    )
    return None
