"""Appointment Types router — `/api/appointment-types/*`.

Tenant-scoped catalog (one row per tenant per named type). Not
location-scoped: a "Follow-up" means the same thing across every location
in the tenant. Only admins can mutate; admin/doctor/staff can list so the
Book Appointment modal can render the dropdown.

Endpoints:
    GET    /appointment-types                    → list (all or active-only)
    POST   /appointment-types                    → create (admin)
    PUT    /appointment-types/{id}               → update (admin)
    DELETE /appointment-types/{id}               → soft-delete (admin)
    POST   /appointment-types/{id}/reactivate    → reactivate (admin)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from core.audit import audit_success
from core.deps import require_role
from core.tenancy import TenantContext, require_tenant, tenant_db
from core.tenant_scope import scoped_filter, stamp_for_write
from services.appointment_types.models import (
    AppointmentTypeCreate,
    AppointmentTypePublic,
    AppointmentTypeUpdate,
)

router = APIRouter(prefix="/appointment-types", tags=["appointment-types"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _public(doc: dict) -> dict:
    out = {k: v for k, v in doc.items() if k != "_id"}
    out.pop("history", None)
    return out


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------
@router.get("", response_model=list[AppointmentTypePublic])
async def list_appointment_types(
    request: Request,
    active_only: bool = Query(default=False),
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    q: dict = scoped_filter({}, ctx, location_scoped=False)
    if q.get("__deny__"):
        return []
    if active_only:
        q["is_active"] = True
    cursor = db.appointment_types.find(q, {"_id": 0}).sort(
        [("sort_order", 1), ("name", 1)]
    )
    rows = [_public(doc) async for doc in cursor]
    await audit_success(
        user, "appointment_type.list_viewed", request,
        metadata={"count": len(rows), "active_only": active_only},
    )
    return rows


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------
@router.post("", response_model=AppointmentTypePublic, status_code=201)
async def create_appointment_type(
    payload: AppointmentTypeCreate,
    request: Request,
    user: dict = Depends(require_role("admin")),
    ctx: TenantContext = Depends(require_tenant),
):
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tenant context required")
    db = tenant_db(ctx.tenant_id)

    # Case-insensitive uniqueness on (tenant_id, name).
    existing = await db.appointment_types.find_one(
        {"tenant_id": ctx.tenant_id, "name": {"$regex": f"^{payload.name}$", "$options": "i"}},
        {"_id": 0, "id": 1},
    )
    if existing:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Appointment type '{payload.name}' already exists",
        )

    now = _now()
    tid = str(uuid.uuid4())
    doc = stamp_for_write(
        {
            "id": tid,
            "name": payload.name,
            "default_duration_minutes": payload.default_duration_minutes,
            "description": payload.description,
            "sort_order": payload.sort_order,
            "is_active": payload.is_active,
            "default_follow_up_days": payload.default_follow_up_days,
            "created_at": now,
            "updated_at": now,
            "created_by": user["id"],
            "updated_by": user["id"],
            "history": [{"at": now, "by": user["id"], "action": "created"}],
        },
        ctx,
        location_id=None,
    )
    await db.appointment_types.insert_one(doc)
    await audit_success(
        user, "appointment_type.created", request,
        entity_type="appointment_type", entity_id=tid,
        metadata={
            "name": doc["name"],
            "default_duration_minutes": doc["default_duration_minutes"],
        },
    )
    return _public(doc)


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------
@router.patch("/{type_id}", response_model=AppointmentTypePublic)
async def update_appointment_type(
    type_id: str,
    payload: AppointmentTypeUpdate,
    request: Request,
    user: dict = Depends(require_role("admin")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    q = scoped_filter({"id": type_id}, ctx, location_scoped=False)
    if q.get("__deny__"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Appointment type not found")
    current = await db.appointment_types.find_one(q, {"_id": 0})
    if not current:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Appointment type not found")

    updates: dict = {k: v for k, v in payload.model_dump(exclude_unset=True).items()}

    # Name uniqueness guard (only if name is being changed).
    if "name" in updates and updates["name"] and updates["name"].lower() != current["name"].lower():
        clash = await db.appointment_types.find_one(
            {
                "tenant_id": ctx.tenant_id,
                "id": {"$ne": type_id},
                "name": {"$regex": f"^{updates['name']}$", "$options": "i"},
            },
            {"_id": 0, "id": 1},
        )
        if clash:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Appointment type '{updates['name']}' already exists",
            )

    if not updates:
        return _public(current)

    now = _now()
    updates["updated_at"] = now
    updates["updated_by"] = user["id"]
    history_entry = {
        "at": now, "by": user["id"], "action": "updated",
        "fields": sorted(list(updates.keys() - {"updated_at", "updated_by"})),
    }
    await db.appointment_types.update_one(
        {"id": type_id, "tenant_id": ctx.tenant_id},
        {"$set": updates, "$push": {"history": history_entry}},
    )
    fresh = await db.appointment_types.find_one(
        {"id": type_id, "tenant_id": ctx.tenant_id}, {"_id": 0}
    )
    await audit_success(
        user, "appointment_type.updated", request,
        entity_type="appointment_type", entity_id=type_id,
        metadata={"fields": history_entry["fields"]},
    )
    return _public(fresh)


# ---------------------------------------------------------------------------
# Soft-delete (deactivate)
# ---------------------------------------------------------------------------
@router.delete("/{type_id}", status_code=204)
async def deactivate_appointment_type(
    type_id: str,
    request: Request,
    user: dict = Depends(require_role("admin")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    q = scoped_filter({"id": type_id}, ctx, location_scoped=False)
    if q.get("__deny__"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Appointment type not found")
    current = await db.appointment_types.find_one(
        q, {"_id": 0, "id": 1, "tenant_id": 1, "is_active": 1}
    )
    if not current:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Appointment type not found")
    if not current.get("is_active", True):
        return None  # already inactive — idempotent
    now = _now()
    await db.appointment_types.update_one(
        {"id": type_id, "tenant_id": ctx.tenant_id},
        {
            "$set": {"is_active": False, "updated_at": now, "updated_by": user["id"]},
            "$push": {"history": {"at": now, "by": user["id"], "action": "deactivated"}},
        },
    )
    await audit_success(
        user, "appointment_type.deactivated", request,
        entity_type="appointment_type", entity_id=type_id,
    )
    return None


# ---------------------------------------------------------------------------
# Reactivate
# ---------------------------------------------------------------------------
@router.post("/{type_id}/reactivate", response_model=AppointmentTypePublic)
async def reactivate_appointment_type(
    type_id: str,
    request: Request,
    user: dict = Depends(require_role("admin")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    q = scoped_filter({"id": type_id}, ctx, location_scoped=False)
    if q.get("__deny__"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Appointment type not found")
    current = await db.appointment_types.find_one(q, {"_id": 0})
    if not current:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Appointment type not found")
    if current.get("is_active", True):
        return _public(current)  # already active — idempotent
    now = _now()
    await db.appointment_types.update_one(
        {"id": type_id, "tenant_id": ctx.tenant_id},
        {
            "$set": {"is_active": True, "updated_at": now, "updated_by": user["id"]},
            "$push": {"history": {"at": now, "by": user["id"], "action": "reactivated"}},
        },
    )
    fresh = await db.appointment_types.find_one(
        {"id": type_id, "tenant_id": ctx.tenant_id}, {"_id": 0}
    )
    await audit_success(
        user, "appointment_type.reactivated", request,
        entity_type="appointment_type", entity_id=type_id,
    )
    return _public(fresh)
