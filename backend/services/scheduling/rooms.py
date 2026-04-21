"""
Appointment-room assignment logic.

Handles:
  - assign_room   (scheduled / checked_in → roomed, current_room_id set)
  - change_room   (already roomed → new room)
  - clear_room    (current_room_id removed, location may revert)

Single-occupancy enforcement runs BEFORE every write. `force=True` with
an explicit reason overrides the conflict and is audited with
`forced=True` so operators and compliance can inspect every exception.

Every transition writes an `appointment_room_history` document so a
full physical-location trail is available for reporting without having
to replay the audit log.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, Request, status

from core.audit import audit_success
from core.db import get_db_read, get_db_write, read_after_write_db
from core.crypto import decrypt_fields
from core import cache, cache_keys
from core.event_bus import publish

ENCRYPTED_FIELDS = ["notes"]

# Terminal statuses — cannot be re-assigned to a room.
TERMINAL = frozenset({"no_show", "canceled", "cancelled", "checked_out"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _load_room(room_id: str, tenant_id: str | None) -> dict:
    db = get_db_read()
    q: dict = {"id": room_id}
    if tenant_id:
        q["tenant_id"] = tenant_id
    room = await db.rooms.find_one(q, {"_id": 0})
    if not room:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Room not found")
    if not room.get("is_active", True):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Room is deactivated")
    return room


async def _occupant(
    room_id: str, tenant_id: str | None, exclude_appt_id: str | None = None,
) -> dict | None:
    """Return the appointment currently assigned to the room, if any.

    "Currently assigned" = `current_room_id == room_id` AND status not in
    the terminal set. The room's occupant is cleared when the visit ends,
    so this is sufficient without keeping a separate occupancy index.
    """
    db = get_db_read()
    q: dict = {
        "current_room_id": room_id,
        "status": {"$nin": list(TERMINAL)},
    }
    if tenant_id:
        q["tenant_id"] = tenant_id
    if exclude_appt_id:
        q["id"] = {"$ne": exclude_appt_id}
    return await db.appointments.find_one(
        q, {"_id": 0, "id": 1, "patient_id": 1, "patient_name": 1, "status": 1},
    )


async def _write_history(
    *,
    appointment: dict,
    from_room_id: str | None,
    to_room_id: str | None,
    from_location_type: str | None,
    to_location_type: str | None,
    actor_id: str,
    reason: str | None,
    forced: bool,
    tenant_id: str | None,
) -> None:
    doc = {
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "location_id": appointment.get("location_id"),
        "appointment_id": appointment["id"],
        "patient_id": appointment.get("patient_id"),
        "from_room_id": from_room_id,
        "to_room_id": to_room_id,
        "from_location_type": from_location_type,
        "to_location_type": to_location_type,
        "actor_id": actor_id,
        "reason": reason,
        "forced": forced,
        "at": _now_iso(),
    }
    await get_db_write().appointment_room_history.insert_one(dict(doc))


async def assign_or_change_room(
    *,
    current: dict,
    room_id: str,
    actor: dict,
    request: Request,
    reason: str | None,
    force: bool,
    tenant_id: str | None,
) -> dict:
    """Assign or change the room on an active appointment.

    Side effects:
      * sets current_room_id + room_assigned_at/_by
      * sets current_location_type=roomed and stamps location_updated_*
      * writes an appointment_room_history row
      * audits `appointment.room_assigned` / `appointment.room_changed`
        with from/to room ids, forced flag, reason
    """
    if current.get("status") in TERMINAL:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Cannot assign a room to a terminal appointment",
        )

    room = await _load_room(room_id, tenant_id)

    # Scope: a room belongs to one location. The appointment must be at the
    # same physical location. Platform admins bypass.
    if current.get("location_id") and current["location_id"] != room["location_id"]:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Room belongs to a different clinic location",
        )

    # Single-occupancy check. `force=True` overrides (audited).
    conflict = await _occupant(
        room_id, tenant_id, exclude_appt_id=current["id"],
    )
    if conflict and not force:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Room is occupied by another appointment ({conflict['id']})",
        )
    forced = bool(conflict and force)
    if forced and not (reason or "").strip():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Overriding an occupied room requires a reason",
        )

    prev_room_id = current.get("current_room_id")
    prev_location_type = current.get("current_location_type")

    now = _now_iso()
    update = {
        "current_room_id": room_id,
        "room_assigned_at": now,
        "room_assigned_by_user_id": actor["id"],
        "current_location_type": "roomed",
        "location_updated_at": now,
        "location_updated_by_user_id": actor["id"],
        "updated_at": now,
    }
    await get_db_write().appointments.update_one(
        {"id": current["id"]}, {"$set": update}
    )
    fresh = await read_after_write_db().appointments.find_one(
        {"id": current["id"]}, {"_id": 0}
    )
    updated = decrypt_fields(fresh, ENCRYPTED_FIELDS)

    await _write_history(
        appointment=updated,
        from_room_id=prev_room_id,
        to_room_id=room_id,
        from_location_type=prev_location_type,
        to_location_type="roomed",
        actor_id=actor["id"],
        reason=reason,
        forced=forced,
        tenant_id=tenant_id,
    )

    await cache.invalidate_prefix(cache_keys.PREFIX_APPOINTMENTS)

    action = "appointment.room_changed" if prev_room_id else "appointment.room_assigned"
    await publish(action, {
        "appointment": updated,
        "previous_room_id": prev_room_id,
        "room_id": room_id,
        "forced": forced,
        "actor_id": actor["id"],
    })
    await audit_success(
        actor, action, request,
        entity_type="appointment", entity_id=current["id"],
        phi_accessed=False,
        metadata={
            "from_room_id": prev_room_id,
            "to_room_id": room_id,
            "room_name": room.get("name"),
            "room_type": room.get("type"),
            "forced": forced,
            "reason": reason,
            "tenant_id": tenant_id,
        },
    )
    return updated


async def clear_room(
    *,
    current: dict,
    actor: dict,
    request: Request,
    reason: str | None,
    return_to_waiting: bool,
    tenant_id: str | None,
) -> dict:
    """Remove the current room assignment.

    When `return_to_waiting=True` (the default for the "Return to Waiting
    Room" action), we also set `current_location_type='waiting_room'`. When
    False we leave the location alone — useful for checkout/depart flows
    where the room is cleared but the patient has already left.
    """
    prev_room_id = current.get("current_room_id")
    if not prev_room_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Appointment has no room assignment",
        )

    now = _now_iso()
    update: dict = {
        "current_room_id": None,
        "room_assigned_at": None,
        "room_assigned_by_user_id": None,
        "updated_at": now,
    }
    to_location_type = current.get("current_location_type")
    if return_to_waiting:
        update["current_location_type"] = "waiting_room"
        update["location_updated_at"] = now
        update["location_updated_by_user_id"] = actor["id"]
        to_location_type = "waiting_room"

    await get_db_write().appointments.update_one(
        {"id": current["id"]}, {"$set": update}
    )
    fresh = await read_after_write_db().appointments.find_one(
        {"id": current["id"]}, {"_id": 0}
    )
    updated = decrypt_fields(fresh, ENCRYPTED_FIELDS)

    await _write_history(
        appointment=updated,
        from_room_id=prev_room_id,
        to_room_id=None,
        from_location_type=current.get("current_location_type"),
        to_location_type=to_location_type,
        actor_id=actor["id"],
        reason=reason,
        forced=False,
        tenant_id=tenant_id,
    )

    await cache.invalidate_prefix(cache_keys.PREFIX_APPOINTMENTS)
    await publish("appointment.room_cleared", {
        "appointment": updated,
        "previous_room_id": prev_room_id,
        "actor_id": actor["id"],
    })
    await audit_success(
        actor, "appointment.room_cleared", request,
        entity_type="appointment", entity_id=current["id"],
        phi_accessed=False,
        metadata={
            "from_room_id": prev_room_id,
            "to_room_id": None,
            "returned_to_waiting": return_to_waiting,
            "reason": reason,
            "tenant_id": tenant_id,
        },
    )
    return updated
