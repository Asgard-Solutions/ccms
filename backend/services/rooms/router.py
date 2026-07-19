"""
Rooms CRUD router — /api/rooms/*.

Tenant + location scoped. Names are case-insensitive unique within a
location. Soft-deactivate via PATCH `{is_active: false}`. Hard deletion is
only allowed when the room has never been assigned (audit + history empty).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from core.audit import audit_success
from core.db import get_db_read, get_db_write
from core.tenancy import TenantContext, get_tenant_context
from core.tenant_scope import scoped_filter, stamp_for_write
from services.authz.policy import require_permission
from services.rooms.models import RoomCreate, RoomPublic, RoomUpdate

router = APIRouter(prefix="/rooms", tags=["rooms"])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _validate_location(
    location_id: str, ctx: TenantContext,
) -> None:
    if not ctx.tenant_id and not ctx.is_platform_admin:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tenant context required")
    db = get_db_read()
    q: dict = {"id": location_id}
    if ctx.tenant_id and not ctx.is_platform_admin:
        q["tenant_id"] = ctx.tenant_id
    loc = await db.locations.find_one(q, {"_id": 0, "id": 1})
    if not loc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid location for this tenant")
    if not ctx.tenant_scope_all and not ctx.is_platform_admin:
        if location_id not in ctx.allowed_location_ids:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Location not assigned to user")


async def _name_in_use(
    *, tenant_id: str | None, location_id: str, name: str,
    exclude_id: str | None = None,
) -> bool:
    db = get_db_read()
    q: dict = {
        "location_id": location_id,
        "name_lower": name.strip().lower(),
    }
    if tenant_id:
        q["tenant_id"] = tenant_id
    if exclude_id:
        q["id"] = {"$ne": exclude_id}
    return bool(await db.rooms.find_one(q, {"_id": 0, "id": 1}))


@router.get("", response_model=list[RoomPublic])
async def list_rooms(
    request: Request,
    ctx: TenantContext = Depends(get_tenant_context),
    user: dict = Depends(require_permission("clinic_settings", "read", audit_allow=False)),
    location_id: str | None = Query(default=None),
    active_only: bool = Query(default=False),
):
    db = get_db_read()
    q: dict = {}
    if location_id:
        q["location_id"] = location_id
    q = scoped_filter(q, ctx, location_scoped=True)
    if q.get("__deny__"):
        return []
    if active_only:
        q["is_active"] = True
    cursor = db.rooms.find(q, {"_id": 0, "name_lower": 0}).sort(
        [("sort_order", 1), ("name", 1)]
    )
    rows = [r async for r in cursor]
    return rows


@router.post("", response_model=RoomPublic, status_code=201)
async def create_room(
    payload: RoomCreate,
    request: Request,
    ctx: TenantContext = Depends(get_tenant_context),
    actor: dict = Depends(
        require_permission("clinic_settings", "update", audit_allow=False)
    ),
):
    await _validate_location(payload.location_id, ctx)
    if await _name_in_use(
        tenant_id=ctx.tenant_id, location_id=payload.location_id, name=payload.name
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "A room with that name already exists in this location",
        )
    now = _now_iso()
    doc = {
        "id": str(uuid.uuid4()),
        "location_id": payload.location_id,
        "name": payload.name,
        "name_lower": payload.name.lower(),
        "type": payload.type,
        "is_active": payload.is_active,
        "sort_order": payload.sort_order,
        "notes": payload.notes,
        "created_at": now,
        "updated_at": now,
    }
    doc = stamp_for_write(doc, ctx, location_id=payload.location_id)
    await get_db_write().rooms.insert_one(dict(doc))
    await audit_success(
        actor, "room.created", request,
        entity_type="room", entity_id=doc["id"],
        metadata={"name": doc["name"], "type": doc["type"],
                  "location_id": payload.location_id},
    )
    return {k: v for k, v in doc.items() if k != "name_lower"}


@router.patch("/{room_id}", response_model=RoomPublic)
async def update_room(
    room_id: str,
    payload: RoomUpdate,
    request: Request,
    ctx: TenantContext = Depends(get_tenant_context),
    actor: dict = Depends(
        require_permission("clinic_settings", "update", audit_allow=False)
    ),
):
    db = get_db_write()
    q = scoped_filter({"id": room_id}, ctx, location_scoped=True)
    if q.get("__deny__"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Room not found")
    existing = await db.rooms.find_one(q, {"_id": 0})
    if not existing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Room not found")

    updates: dict = {}
    patch = payload.model_dump(exclude_unset=True)
    if "name" in patch and patch["name"]:
        if await _name_in_use(
            tenant_id=existing.get("tenant_id"),
            location_id=existing["location_id"],
            name=patch["name"],
            exclude_id=room_id,
        ):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Another room already has that name",
            )
        updates["name"] = patch["name"]
        updates["name_lower"] = patch["name"].lower()
    for k in ("type", "sort_order", "is_active", "notes"):
        if k in patch:
            updates[k] = patch[k]
    if not updates:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No fields to update")
    updates["updated_at"] = _now_iso()
    await db.rooms.update_one({"id": room_id}, {"$set": updates})
    fresh = await db.rooms.find_one({"id": room_id}, {"_id": 0, "name_lower": 0})
    await audit_success(
        actor, "room.updated", request,
        entity_type="room", entity_id=room_id,
        metadata={"fields": list(updates.keys())},
    )
    return fresh


@router.delete("/{room_id}", status_code=204)
async def delete_room(
    room_id: str,
    request: Request,
    ctx: TenantContext = Depends(get_tenant_context),
    actor: dict = Depends(
        require_permission("clinic_settings", "update", audit_allow=False)
    ),
):
    """Hard-delete only if the room has never been used. Otherwise callers
    should deactivate via PATCH — keeping referential integrity for audit."""
    db = get_db_write()
    q = scoped_filter({"id": room_id}, ctx, location_scoped=True)
    if q.get("__deny__"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Room not found")
    existing = await db.rooms.find_one(q, {"_id": 0})
    if not existing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Room not found")

    # If any historical assignment exists, deny — preserves audit trail.
    ref = await db.appointment_room_history.find_one(
        {"to_room_id": room_id}, {"_id": 0, "id": 1},
    )
    if ref:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Room has assignment history — deactivate instead of deleting.",
        )
    occ = await db.appointments.find_one(
        {"current_room_id": room_id}, {"_id": 0, "id": 1},
    )
    if occ:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Room is currently occupied — clear assignment first.",
        )
    await db.rooms.delete_one({"id": room_id})
    await audit_success(
        actor, "room.deleted", request,
        entity_type="room", entity_id=room_id,
        metadata={"name": existing.get("name")},
    )
