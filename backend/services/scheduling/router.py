"""
Scheduling Service router — /api/appointments/* (HIPAA-hardened).
Adds:
  - Audit entries for every create/update/cancel (PHI-touching metadata only)
  - Encryption at rest of `notes`
  - Excludes soft-deleted patients from new bookings
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from core.audit import audit_success
from core import cache, cache_keys
from core.crypto import decrypt_fields, encrypt_fields
from core.db import get_db_read, get_db_write, read_after_write_db
from core.deps import get_current_user, require_role
from core.event_bus import publish
from services.authz.policy import require_permission
from services.scheduling.models import (
    AppointmentCreate,
    AppointmentPublic,
    AppointmentUpdate,
)

router = APIRouter(prefix="/appointments", tags=["scheduling"])
STAFF_ROLES = ("admin", "doctor", "staff")
ENCRYPTED = ["notes"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


async def _hydrate(apps: list[dict]) -> list[dict]:
    if not apps:
        return apps
    db = get_db_read()
    provider_ids = list({a["provider_id"] for a in apps})
    patient_ids = list({a["patient_id"] for a in apps})
    providers = {
        u["id"]: u["name"]
        async for u in db.users.find(
            {"id": {"$in": provider_ids}}, {"_id": 0, "id": 1, "name": 1}
        )
    }
    patients = {
        p["id"]: f"{p['first_name']} {p['last_name']}"
        async for p in db.patients.find(
            {"id": {"$in": patient_ids}},
            {"_id": 0, "id": 1, "first_name": 1, "last_name": 1},
        )
    }
    for a in apps:
        a["provider_name"] = providers.get(a["provider_id"])
        a["patient_name"] = patients.get(a["patient_id"])
    return apps


async def _check_conflict(
    provider_id: str, start_iso: str, end_iso: str, exclude_id: str | None = None,
) -> None:
    db = get_db_write()  # conflict checks must read latest committed state
    q: dict = {
        "provider_id": provider_id,
        "status": "scheduled",
        "start_time": {"$lt": end_iso},
        "end_time": {"$gt": start_iso},
    }
    if exclude_id:
        q["id"] = {"$ne": exclude_id}
    clash = await db.appointments.find_one(q, {"_id": 0, "id": 1, "start_time": 1})
    if clash:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Provider already booked at this time (conflicts with appt {clash['id']})",
        )


@router.post("", response_model=AppointmentPublic, status_code=201)
async def create_appointment(
    payload: AppointmentCreate,
    request: Request,
    actor: dict = Depends(require_permission("appointment", "create", audit_allow=False)),
):
    db = get_db_write()
    if payload.end_time <= payload.start_time:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "end_time must be after start_time")

    patient = await db.patients.find_one(
        {"id": payload.patient_id, "status": {"$ne": "deleted"}},
        {"_id": 0, "id": 1},
    )
    if not patient:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    provider = await db.users.find_one(
        {"id": payload.provider_id, "role": "doctor", "status": {"$ne": "disabled"}},
        {"_id": 0, "id": 1, "name": 1},
    )
    if not provider:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Provider not found")

    start_iso = _to_iso(payload.start_time)
    end_iso = _to_iso(payload.end_time)
    await _check_conflict(payload.provider_id, start_iso, end_iso)

    now = _now_iso()
    doc = {
        "id": str(uuid.uuid4()),
        "patient_id": payload.patient_id,
        "provider_id": payload.provider_id,
        "start_time": start_iso,
        "end_time": end_iso,
        "reason": payload.reason,
        "notes": payload.notes,
        "status": "scheduled",
        "created_by": actor["id"],
        "created_at": now,
        "updated_at": now,
    }
    await db.appointments.insert_one(encrypt_fields(doc, ENCRYPTED))
    await cache.invalidate_prefix(cache_keys.PREFIX_APPOINTMENTS)
    await cache.invalidate_prefix(cache_keys.PREFIX_DASHBOARD)

    await publish("appointment.booked", {"appointment": doc, "actor_id": actor["id"]})
    await audit_success(
        actor, "appointment.created", request,
        entity_type="appointment", entity_id=doc["id"],
        phi_accessed=True,
        metadata={"patient_id": payload.patient_id, "provider_id": payload.provider_id},
    )

    (hydrated,) = await _hydrate([dict(doc)])
    return hydrated


@router.get("", response_model=list[AppointmentPublic])
async def list_appointments(
    request: Request,
    user: dict = Depends(get_current_user),
    provider_id: str | None = None,
    patient_id: str | None = None,
    appt_status: str | None = Query(default=None, alias="status"),
    from_date: str | None = Query(default=None, alias="from"),
    to_date: str | None = Query(default=None, alias="to"),
):
    db = get_db_read()
    q: dict = {}

    if user["role"] == "patient":
        patient_record = await db.patients.find_one(
            {"user_id": user["id"]}, {"_id": 0, "id": 1}
        )
        if not patient_record:
            return []
        q["patient_id"] = patient_record["id"]
    elif user["role"] == "doctor":
        if not provider_id and not patient_id:
            q["provider_id"] = user["id"]

    if provider_id:
        q["provider_id"] = provider_id
    if patient_id:
        q["patient_id"] = patient_id
    if appt_status:
        q["status"] = appt_status
    if from_date or to_date:
        range_q: dict = {}
        if from_date:
            range_q["$gte"] = from_date
        if to_date:
            range_q["$lte"] = to_date
        q["start_time"] = range_q

    async def _fetch():
        cursor = db.appointments.find(q, {"_id": 0}).sort("start_time", 1)
        apps = [decrypt_fields(a, ENCRYPTED) async for a in cursor]
        return await _hydrate(apps)

    cache_key = cache_keys.appointments_query(
        user["role"],
        {
            "provider_id": provider_id,
            "patient_id": patient_id,
            "status": appt_status,
            "from": from_date,
            "to": to_date,
            # Doctors auto-scope to themselves; bake that into the cache key.
            "doctor_self": user["id"] if (user["role"] == "doctor" and not provider_id and not patient_id) else None,
            "patient_self": user["id"] if user["role"] == "patient" else None,
        },
    )
    return await cache.get_or_set(cache_key, 30, _fetch)


@router.get("/{appointment_id}", response_model=AppointmentPublic)
async def get_appointment(
    appointment_id: str, request: Request, user: dict = Depends(get_current_user)
):
    db = get_db_read()
    a = await db.appointments.find_one({"id": appointment_id}, {"_id": 0})
    if not a:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Appointment not found")
    if user["role"] == "patient":
        patient_record = await db.patients.find_one(
            {"user_id": user["id"]}, {"_id": 0, "id": 1}
        )
        if not patient_record or patient_record["id"] != a["patient_id"]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")
    a = decrypt_fields(a, ENCRYPTED)
    (hydrated,) = await _hydrate([a])
    return hydrated


@router.put("/{appointment_id}", response_model=AppointmentPublic)
async def update_appointment(
    appointment_id: str,
    payload: AppointmentUpdate,
    request: Request,
    actor: dict = Depends(require_permission("appointment", "update", audit_allow=False)),
):
    db = get_db_write()
    current = await db.appointments.find_one({"id": appointment_id}, {"_id": 0})
    if not current:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Appointment not found")
    if current["status"] == "cancelled":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot modify a cancelled appointment")

    updates: dict = {}
    if payload.start_time or payload.end_time:
        new_start = payload.start_time or datetime.fromisoformat(current["start_time"])
        new_end = payload.end_time or datetime.fromisoformat(current["end_time"])
        if new_end <= new_start:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "end_time must be after start_time")
        start_iso = _to_iso(new_start)
        end_iso = _to_iso(new_end)
        await _check_conflict(current["provider_id"], start_iso, end_iso, exclude_id=appointment_id)
        updates["start_time"] = start_iso
        updates["end_time"] = end_iso
    if payload.reason is not None:
        updates["reason"] = payload.reason
    if payload.notes is not None:
        updates["notes"] = payload.notes
    if payload.status is not None:
        updates["status"] = payload.status

    if not updates:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No fields to update")
    updates["updated_at"] = _now_iso()

    await db.appointments.update_one(
        {"id": appointment_id}, {"$set": encrypt_fields(updates, ENCRYPTED)}
    )
    updated = await read_after_write_db().appointments.find_one(
        {"id": appointment_id}, {"_id": 0}
    )
    updated_dec = decrypt_fields(updated, ENCRYPTED)
    await cache.invalidate_prefix(cache_keys.PREFIX_APPOINTMENTS)
    await cache.invalidate_prefix(cache_keys.PREFIX_DASHBOARD)

    await publish(
        "appointment.updated",
        {"appointment": updated_dec, "previous": current, "actor_id": actor["id"]},
    )
    await audit_success(
        actor, "appointment.updated", request,
        entity_type="appointment", entity_id=appointment_id,
        phi_accessed=True, metadata={"fields": list(updates.keys())},
    )
    (hydrated,) = await _hydrate([updated_dec])
    return hydrated


@router.post("/{appointment_id}/cancel", response_model=AppointmentPublic)
async def cancel_appointment(
    appointment_id: str, request: Request, user: dict = Depends(get_current_user)
):
    db = get_db_write()
    a = await db.appointments.find_one({"id": appointment_id}, {"_id": 0})
    if not a:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Appointment not found")

    if user["role"] == "patient":
        patient_record = await db.patients.find_one(
            {"user_id": user["id"]}, {"_id": 0, "id": 1}
        )
        if not patient_record or patient_record["id"] != a["patient_id"]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")
    elif user["role"] not in STAFF_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")

    if a["status"] == "cancelled":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Already cancelled")

    await db.appointments.update_one(
        {"id": appointment_id},
        {"$set": {"status": "cancelled", "updated_at": _now_iso()}},
    )
    updated = await read_after_write_db().appointments.find_one(
        {"id": appointment_id}, {"_id": 0}
    )
    updated_dec = decrypt_fields(updated, ENCRYPTED)
    await cache.invalidate_prefix(cache_keys.PREFIX_APPOINTMENTS)
    await cache.invalidate_prefix(cache_keys.PREFIX_DASHBOARD)

    await publish("appointment.cancelled", {"appointment": updated_dec, "actor_id": user["id"]})
    await audit_success(
        user, "appointment.cancelled", request,
        entity_type="appointment", entity_id=appointment_id, phi_accessed=True,
    )
    (hydrated,) = await _hydrate([updated_dec])
    return hydrated
