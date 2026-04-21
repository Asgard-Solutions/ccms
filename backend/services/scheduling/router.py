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
from core.tenancy import TenantContext, get_tenant_context
from core.tenant_scope import scoped_filter, stamp_for_write
from services.authz.policy import require_permission
from services.rooms.models import RoomAssignRequest
from services.scheduling.models import (
    AppointmentCreate,
    AppointmentPublic,
    AppointmentUpdate,
    CheckoutRequest,
    PatientLocationChangeRequest,
    WorkflowTransitionRequest,
)
from services.scheduling.rooms import assign_or_change_room, clear_room
from services.scheduling.workflow import (
    TRANSITIONS,
    apply_patient_location,
    apply_transition,
)

router = APIRouter(prefix="/appointments", tags=["scheduling"])
STAFF_ROLES = ("admin", "doctor", "staff")
ENCRYPTED = ["notes", "checkout_notes", "checkout_summary"]


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
        p["id"]: {
            "name": f"{p['first_name']} {p['last_name']}",
            "phone": p.get("phone"),
        }
        async for p in db.patients.find(
            {"id": {"$in": patient_ids}},
            {"_id": 0, "id": 1, "first_name": 1, "last_name": 1, "phone": 1},
        )
    }
    # Latest intake form per patient (single pass, newest-first).
    # - completed form wins; otherwise a draft is surfaced as "in_progress".
    # - patients with zero intake forms → "not_started".
    intake_by_pid: dict[str, dict] = {}
    async for f in db.patient_intake_forms.find(
        {"patient_id": {"$in": patient_ids}},
        {"_id": 0, "id": 1, "patient_id": 1, "status": 1,
         "captured_at": 1, "captured_by_name": 1, "created_at": 1},
    ).sort("created_at", -1):
        pid = f["patient_id"]
        cur = intake_by_pid.get(pid)
        # A completed form wins over a later draft.
        if cur and cur.get("status") == "completed":
            continue
        if cur is None or f.get("status") == "completed":
            intake_by_pid[pid] = f
    # Rooms lookup (only for appts that carry a current_room_id).
    room_ids = list({a.get("current_room_id") for a in apps if a.get("current_room_id")})
    rooms_by_id: dict[str, dict] = {}
    if room_ids:
        async for r in db.rooms.find(
            {"id": {"$in": room_ids}},
            {"_id": 0, "id": 1, "name": 1, "type": 1},
        ):
            rooms_by_id[r["id"]] = r
    # Appointment type names — only for appts with a type set.
    type_ids = list({a.get("appointment_type_id") for a in apps if a.get("appointment_type_id")})
    types_by_id: dict[str, dict] = {}
    if type_ids:
        async for t in db.appointment_types.find(
            {"id": {"$in": type_ids}},
            {"_id": 0, "id": 1, "name": 1},
        ):
            types_by_id[t["id"]] = t
    for a in apps:
        a["provider_name"] = providers.get(a["provider_id"])
        info = patients.get(a["patient_id"]) or {}
        a["patient_name"] = info.get("name")
        a["patient_phone"] = info.get("phone")
        f = intake_by_pid.get(a["patient_id"])
        if f is None:
            a["intake_status"] = "not_started"
            a["intake_completed_at"] = None
            a["intake_completed_by_name"] = None
            a["intake_form_id"] = None
        elif f.get("status") == "completed":
            a["intake_status"] = "completed"
            a["intake_completed_at"] = f.get("captured_at")
            a["intake_completed_by_name"] = f.get("captured_by_name")
            a["intake_form_id"] = f.get("id")
        else:  # draft
            a["intake_status"] = "in_progress"
            a["intake_completed_at"] = None
            a["intake_completed_by_name"] = None
            a["intake_form_id"] = f.get("id")
        r = rooms_by_id.get(a.get("current_room_id") or "")
        a["current_room_name"] = r.get("name") if r else None
        a["current_room_type"] = r.get("type") if r else None
        t = types_by_id.get(a.get("appointment_type_id") or "")
        a["appointment_type_name"] = t.get("name") if t else None
    return apps


async def _check_conflict(
    provider_id: str, start_iso: str, end_iso: str,
    exclude_id: str | None = None, tenant_id: str | None = None,
) -> None:
    db = get_db_write()  # conflict checks must read latest committed state
    # Block booking against any *active* appointment. Cancelled, canceled,
    # no-show, and already-checked-out visits must NOT block rebooking —
    # the spec requires cancelled slots to remain historically visible
    # but fully reusable, and completed visits have vacated the slot.
    q: dict = {
        "provider_id": provider_id,
        "status": {"$nin": [
            "cancelled", "canceled", "no_show", "checked_out",
        ]},
        "start_time": {"$lt": end_iso},
        "end_time": {"$gt": start_iso},
    }
    if tenant_id:
        q["tenant_id"] = tenant_id
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
    ctx: TenantContext = Depends(get_tenant_context),
):
    ctx.assert_tenant_bound()
    db = get_db_write()
    if payload.end_time <= payload.start_time:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "end_time must be after start_time")

    patient_q = scoped_filter(
        {"id": payload.patient_id, "status": {"$ne": "deleted"}},
        ctx, location_scoped=True,
    )
    if patient_q.get("__deny__"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    patient = await db.patients.find_one(
        patient_q, {"_id": 0, "id": 1, "location_id": 1},
    )
    if not patient:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")

    # Provider must be in the same tenant (platform admin excepted).
    prov_q: dict = {"id": payload.provider_id, "role": "doctor", "status": {"$ne": "disabled"}}
    if ctx.tenant_id and not ctx.is_platform_admin:
        prov_q["tenant_id"] = ctx.tenant_id
    provider = await db.users.find_one(prov_q, {"_id": 0, "id": 1, "name": 1})
    if not provider:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Provider not found")

    # Resolve location_id: payload > patient's location > single user loc.
    location_id = payload.location_id or patient.get("location_id")
    if not location_id and ctx.allowed_location_ids and len(ctx.allowed_location_ids) == 1:
        location_id = ctx.allowed_location_ids[0]
    if location_id and ctx.tenant_id:
        loc = await db.locations.find_one(
            {"id": location_id, "tenant_id": ctx.tenant_id}, {"_id": 0, "id": 1},
        )
        if not loc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid location for this tenant")
        if not ctx.tenant_scope_all and not ctx.is_platform_admin:
            if location_id not in ctx.allowed_location_ids:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "Location not assigned to user")

    start_iso = _to_iso(payload.start_time)
    end_iso = _to_iso(payload.end_time)

    # Validate appointment_type_id BEFORE the conflict check so we return
    # a clear 400 instead of a misleading "slot busy" 409 when the type
    # id is malformed or inactive.
    if payload.appointment_type_id:
        at_q: dict = {"id": payload.appointment_type_id, "is_active": True}
        if ctx.tenant_id and not ctx.is_platform_admin:
            at_q["tenant_id"] = ctx.tenant_id
        at_row = await db.appointment_types.find_one(at_q, {"_id": 0, "id": 1})
        if not at_row:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "Invalid appointment_type_id",
            )

    await _check_conflict(payload.provider_id, start_iso, end_iso, tenant_id=ctx.tenant_id)

    now = _now_iso()
    doc = {
        "id": str(uuid.uuid4()),
        "patient_id": payload.patient_id,
        "provider_id": payload.provider_id,
        "appointment_type_id": payload.appointment_type_id,
        "start_time": start_iso,
        "end_time": end_iso,
        "reason": payload.reason,
        "notes": payload.notes,
        "status": "scheduled",
        "created_by": actor["id"],
        "created_at": now,
        "updated_at": now,
    }
    doc = stamp_for_write(doc, ctx, location_id=location_id)
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
    ctx: TenantContext = Depends(get_tenant_context),
    provider_id: str | None = None,
    patient_id: str | None = None,
    location_id: str | None = None,
    appt_status: str | None = Query(default=None, alias="status"),
    from_date: str | None = Query(default=None, alias="from"),
    to_date: str | None = Query(default=None, alias="to"),
    include_cancelled: bool = Query(
        default=True,
        description="When False, cancelled appointments are excluded from the results.",
    ),
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
    if location_id:
        q["location_id"] = location_id
    if appt_status:
        q["status"] = appt_status
    elif not include_cancelled:
        q["status"] = {"$ne": "cancelled"}
    if from_date or to_date:
        range_q: dict = {}
        if from_date:
            range_q["$gte"] = from_date
        if to_date:
            range_q["$lte"] = to_date
        q["start_time"] = range_q

    # Tenant + location isolation
    q = scoped_filter(q, ctx, location_scoped=True)
    if q.get("__deny__"):
        return []

    async def _fetch():
        cursor = db.appointments.find(q, {"_id": 0}).sort("start_time", 1)
        apps = [decrypt_fields(a, ENCRYPTED) async for a in cursor]
        return await _hydrate(apps)

    cache_key = cache_keys.appointments_query(
        user["role"],
        {
            "provider_id": provider_id,
            "patient_id": patient_id,
            "location_id": location_id,
            "status": appt_status,
            "include_cancelled": include_cancelled,
            "from": from_date,
            "to": to_date,
            "tenant": ctx.tenant_id or "platform",
            # Doctors auto-scope to themselves; bake that into the cache key.
            "doctor_self": user["id"] if (user["role"] == "doctor" and not provider_id and not patient_id) else None,
            "patient_self": user["id"] if user["role"] == "patient" else None,
        },
    )
    return await cache.get_or_set(cache_key, 30, _fetch)


@router.get("/counts")
async def appointment_counts(
    request: Request,
    user: dict = Depends(get_current_user),
    ctx: TenantContext = Depends(get_tenant_context),
    provider_id: str | None = None,
    patient_id: str | None = None,
    location_id: str | None = None,
    appt_status: str | None = Query(default=None, alias="status"),
    from_date: str | None = Query(default=None, alias="from"),
    to_date: str | None = Query(default=None, alias="to"),
    tz: str = Query(default="UTC", description="IANA timezone for local-day bucketing"),
    include_samples: int = Query(default=0, ge=0, le=10,
                                 description="Sample appointments per date (0..10)"),
    include_cancelled: bool = Query(
        default=False,
        description="When True, samples include cancelled appts. `count` is always "
                    "active-only; `cancelled_count` is always returned.",
    ),
):
    """Return appointment counts grouped by local date.

    Response shape per row:
      {
        "date": "YYYY-MM-DD",
        "count": <active-only count>,             # scheduled + completed
        "cancelled_count": <cancelled count>,     # always returned for UX
        "samples": [<N earliest appointments>]    # respects include_cancelled
      }

    `count` never includes cancelled appointments because the operational
    day-to-day question ("how busy are we?") shouldn't inflate with history.
    The frontend can combine `count` + `cancelled_count` into a
    "5 scheduled, 2 canceled" secondary indicator when the user has the
    Show-canceled toggle on.
    """
    db = get_db_read()
    q: dict = {}

    # Role-scoped auto-filters (same policy as list_appointments).
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
    if location_id:
        q["location_id"] = location_id
    if appt_status:
        q["status"] = appt_status
    if from_date or to_date:
        range_q: dict = {}
        if from_date:
            range_q["$gte"] = from_date
        if to_date:
            range_q["$lte"] = to_date
        q["start_time"] = range_q

    q = scoped_filter(q, ctx, location_scoped=True)
    if q.get("__deny__"):
        return []

    async def _aggregate():
        pipeline = [
            {"$match": q},
            {"$sort": {"start_time": 1}},
            {"$addFields": {
                "_parsed_start": {"$dateFromString": {"dateString": "$start_time"}},
            }},
            {"$addFields": {
                "_local_date": {"$dateToString": {
                    "format": "%Y-%m-%d",
                    "date": "$_parsed_start",
                    "timezone": tz,
                }},
                "_is_cancelled": {"$eq": ["$status", "cancelled"]},
            }},
            {"$group": {
                "_id": "$_local_date",
                "count": {"$sum": {"$cond": ["$_is_cancelled", 0, 1]}},
                "cancelled_count": {"$sum": {"$cond": ["$_is_cancelled", 1, 0]}},
                "samples": {"$push": {
                    "id": "$id",
                    "start_time": "$start_time",
                    "end_time": "$end_time",
                    "patient_id": "$patient_id",
                    "provider_id": "$provider_id",
                    "status": "$status",
                }},
            }},
            {"$project": {
                "_id": 0,
                "date": "$_id",
                "count": 1,
                "cancelled_count": 1,
                "samples": ({"$slice": [
                    ({"$filter": {
                        "input": "$samples",
                        "as": "s",
                        "cond": {"$ne": ["$$s.status", "cancelled"]},
                    }} if not include_cancelled else "$samples"),
                    include_samples,
                ]} if include_samples > 0 else {"$literal": []}),
            }},
            {"$sort": {"date": 1}},
        ]
        rows = [doc async for doc in db.appointments.aggregate(pipeline)]

        # Hydrate patient/provider names on samples (if any).
        if include_samples > 0 and rows:
            pids: set[str] = set()
            prids: set[str] = set()
            for r in rows:
                for s in r["samples"]:
                    pids.add(s["patient_id"])
                    prids.add(s["provider_id"])
            patients = {
                p["id"]: f"{p['first_name']} {p['last_name']}"
                async for p in db.patients.find(
                    {"id": {"$in": list(pids)}},
                    {"_id": 0, "id": 1, "first_name": 1, "last_name": 1},
                )
            } if pids else {}
            providers = {
                u["id"]: u["name"]
                async for u in db.users.find(
                    {"id": {"$in": list(prids)}},
                    {"_id": 0, "id": 1, "name": 1},
                )
            } if prids else {}
            for r in rows:
                for s in r["samples"]:
                    s["patient_name"] = patients.get(s["patient_id"])
                    s["provider_name"] = providers.get(s["provider_id"])
        return rows

    cache_key = cache_keys.appointments_query(
        user["role"] + ":counts",
        {
            "provider_id": provider_id,
            "patient_id": patient_id,
            "location_id": location_id,
            "status": appt_status,
            "from": from_date,
            "to": to_date,
            "tz": tz,
            "samples": include_samples,
            "include_cancelled": include_cancelled,
            "tenant": ctx.tenant_id or "platform",
            "doctor_self": user["id"] if (user["role"] == "doctor" and not provider_id and not patient_id) else None,
            "patient_self": user["id"] if user["role"] == "patient" else None,
        },
    )
    return await cache.get_or_set(cache_key, 30, _aggregate)


# ---------------------------------------------------------------------------
# Follow-up suggestions — written by the checkout event-bus hooks.
# Declared BEFORE /{appointment_id} so the path is not shadowed by the
# generic single-appointment fetch route.
# ---------------------------------------------------------------------------

@router.get("/follow-up-suggestions")
async def list_follow_up_suggestions(
    request: Request,
    user: dict = Depends(get_current_user),
    ctx: TenantContext = Depends(get_tenant_context),
    status_filter: str | None = Query(default="pending", alias="status"),
    patient_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
):
    """Return the queue of pending follow-up suggestions created by
    the checkout hook (scaffold for the future follow-up scheduler UI).
    Providers see only their own queue; admin/staff see the full queue."""
    db = get_db_read()
    q: dict = {}
    if ctx.tenant_id and not ctx.is_platform_admin:
        q["tenant_id"] = ctx.tenant_id
    if status_filter and status_filter != "all":
        q["status"] = status_filter
    if patient_id:
        q["patient_id"] = patient_id
    if user.get("role") == "doctor":
        q["provider_id"] = user["id"]
    cursor = db.follow_up_suggestions.find(q, {"_id": 0}).sort("suggested_at", 1).limit(limit)
    rows = [r async for r in cursor]
    pids = list({r["patient_id"] for r in rows if r.get("patient_id")})
    tids = list({r["appointment_type_id"] for r in rows if r.get("appointment_type_id")})
    patients = {
        p["id"]: f"{p['first_name']} {p['last_name']}"
        async for p in db.patients.find(
            {"id": {"$in": pids}},
            {"_id": 0, "id": 1, "first_name": 1, "last_name": 1},
        )
    } if pids else {}
    types = {
        t["id"]: t["name"]
        async for t in db.appointment_types.find(
            {"id": {"$in": tids}}, {"_id": 0, "id": 1, "name": 1},
        )
    } if tids else {}
    for r in rows:
        r["patient_name"] = patients.get(r.get("patient_id"))
        r["appointment_type_name"] = types.get(r.get("appointment_type_id"))
    return rows


@router.post("/follow-up-suggestions/{suggestion_id}/dismiss")
async def dismiss_follow_up_suggestion(
    suggestion_id: str,
    request: Request,
    actor: dict = Depends(
        require_permission("appointment", "update", audit_allow=False)
    ),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = get_db_write()
    q: dict = {"id": suggestion_id}
    if ctx.tenant_id and not ctx.is_platform_admin:
        q["tenant_id"] = ctx.tenant_id
    row = await db.follow_up_suggestions.find_one(q, {"_id": 0})
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Suggestion not found")
    await db.follow_up_suggestions.update_one(
        {"id": suggestion_id},
        {"$set": {
            "status": "dismissed",
            "resolved_at": _now_iso(),
            "resolved_by": actor["id"],
        }},
    )
    await audit_success(
        actor, "follow_up_suggestion.dismissed", request,
        entity_type="follow_up_suggestion", entity_id=suggestion_id,
        metadata={"tenant_id": ctx.tenant_id},
    )
    return {"ok": True}



@router.get("/{appointment_id}", response_model=AppointmentPublic)
async def get_appointment(
    appointment_id: str, request: Request,
    user: dict = Depends(get_current_user),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = get_db_read()
    q = scoped_filter({"id": appointment_id}, ctx, location_scoped=True)
    if q.get("__deny__"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Appointment not found")
    a = await db.appointments.find_one(q, {"_id": 0})
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
    # Attach clinical encounter linkage (read-only projection).
    enc = await db.clinical_encounters.find_one(
        {
            "tenant_id": a.get("tenant_id"),
            "appointment_id": appointment_id,
            "status": {"$ne": "cancelled"},
        },
        {"_id": 0, "id": 1, "status": 1},
    )
    if enc:
        hydrated["clinical_encounter_id"] = enc["id"]
        hydrated["clinical_encounter_status"] = enc["status"]
    return hydrated


@router.patch("/{appointment_id}", response_model=AppointmentPublic)
async def update_appointment(
    appointment_id: str,
    payload: AppointmentUpdate,
    request: Request,
    actor: dict = Depends(require_permission("appointment", "update", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = get_db_write()
    q = scoped_filter({"id": appointment_id}, ctx, location_scoped=True)
    if q.get("__deny__"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Appointment not found")
    current = await db.appointments.find_one(q, {"_id": 0})
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
        await _check_conflict(current["provider_id"], start_iso, end_iso,
                              exclude_id=appointment_id, tenant_id=ctx.tenant_id)
        updates["start_time"] = start_iso
        updates["end_time"] = end_iso
    if payload.reason is not None:
        updates["reason"] = payload.reason
    if payload.notes is not None:
        updates["notes"] = payload.notes
    if payload.status is not None:
        updates["status"] = payload.status
    if payload.appointment_type_id is not None:
        # Validate the type exists + is active + tenant-scoped.
        at_q: dict = {"id": payload.appointment_type_id, "is_active": True}
        if ctx.tenant_id and not ctx.is_platform_admin:
            at_q["tenant_id"] = ctx.tenant_id
        if not await db.appointment_types.find_one(at_q, {"_id": 0, "id": 1}):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "Invalid appointment_type_id",
            )
        updates["appointment_type_id"] = payload.appointment_type_id

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
    appointment_id: str, request: Request,
    user: dict = Depends(get_current_user),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = get_db_write()
    q = scoped_filter({"id": appointment_id}, ctx, location_scoped=True)
    if q.get("__deny__"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Appointment not found")
    a = await db.appointments.find_one(q, {"_id": 0})
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


# ---------------------------------------------------------------------------
# Appointment workflow transitions — Phase 1 backbone.
#
# Each endpoint is a thin wrapper around `apply_transition` which centralises
# the validation rules, audit emission, and physical-location side effects.
# ---------------------------------------------------------------------------

async def _load_for_transition(
    appointment_id: str, ctx: TenantContext,
) -> dict:
    """Fetch the appointment under tenant+location scope or 404."""
    db = get_db_write()  # read-after-write: use write client
    q = scoped_filter({"id": appointment_id}, ctx, location_scoped=True)
    if q.get("__deny__"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Appointment not found")
    a = await db.appointments.find_one(q, {"_id": 0})
    if not a:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Appointment not found")
    return a


async def _run_transition(
    transition_name: str,
    appointment_id: str,
    payload: WorkflowTransitionRequest,
    request: Request,
    actor: dict,
    ctx: TenantContext,
) -> dict:
    spec = TRANSITIONS[transition_name]
    current = await _load_for_transition(appointment_id, ctx)
    updated = await apply_transition(
        appointment_id, spec,
        current=current,
        actor=actor,
        request=request,
        override=payload.override,
        reason=payload.reason,
        location_override=payload.location,
        tenant_id=ctx.tenant_id,
    )
    (hydrated,) = await _hydrate([updated])
    return hydrated


@router.post("/{appointment_id}/check-in", response_model=AppointmentPublic)
async def appointment_check_in(
    appointment_id: str,
    request: Request,
    payload: WorkflowTransitionRequest = WorkflowTransitionRequest(),
    actor: dict = Depends(
        require_permission("appointment", "update", audit_allow=False)
    ),
    ctx: TenantContext = Depends(get_tenant_context),
):
    return await _run_transition("check_in", appointment_id, payload, request, actor, ctx)


@router.post("/{appointment_id}/undo-check-in", response_model=AppointmentPublic)
async def appointment_undo_check_in(
    appointment_id: str,
    request: Request,
    payload: WorkflowTransitionRequest = WorkflowTransitionRequest(),
    actor: dict = Depends(
        require_permission("appointment", "update", audit_allow=False)
    ),
    ctx: TenantContext = Depends(get_tenant_context),
):
    return await _run_transition("undo_check_in", appointment_id, payload, request, actor, ctx)


@router.post("/{appointment_id}/no-show", response_model=AppointmentPublic)
async def appointment_no_show(
    appointment_id: str,
    request: Request,
    payload: WorkflowTransitionRequest = WorkflowTransitionRequest(),
    actor: dict = Depends(
        require_permission("appointment", "update", audit_allow=False)
    ),
    ctx: TenantContext = Depends(get_tenant_context),
):
    return await _run_transition("no_show", appointment_id, payload, request, actor, ctx)


@router.post("/{appointment_id}/ready-for-provider", response_model=AppointmentPublic)
async def appointment_ready_for_provider(
    appointment_id: str,
    request: Request,
    payload: WorkflowTransitionRequest = WorkflowTransitionRequest(),
    actor: dict = Depends(
        require_permission("appointment", "update", audit_allow=False)
    ),
    ctx: TenantContext = Depends(get_tenant_context),
):
    return await _run_transition(
        "ready_for_provider", appointment_id, payload, request, actor, ctx,
    )


@router.post("/{appointment_id}/undo-ready-for-provider", response_model=AppointmentPublic)
async def appointment_undo_ready_for_provider(
    appointment_id: str,
    request: Request,
    payload: WorkflowTransitionRequest = WorkflowTransitionRequest(),
    actor: dict = Depends(
        require_permission("appointment", "update", audit_allow=False)
    ),
    ctx: TenantContext = Depends(get_tenant_context),
):
    """Reverse an accidental Ready-for-Provider click — returns to checked_in."""
    return await _run_transition(
        "undo_ready_for_provider", appointment_id, payload, request, actor, ctx,
    )


@router.post("/{appointment_id}/start-visit", response_model=AppointmentPublic)
async def appointment_start_visit(
    appointment_id: str,
    request: Request,
    payload: WorkflowTransitionRequest = WorkflowTransitionRequest(),
    actor: dict = Depends(
        require_permission("appointment", "update", audit_allow=False)
    ),
    ctx: TenantContext = Depends(get_tenant_context),
):
    return await _run_transition("start_visit", appointment_id, payload, request, actor, ctx)


@router.post("/{appointment_id}/ready-for-checkout", response_model=AppointmentPublic)
async def appointment_ready_for_checkout(
    appointment_id: str,
    request: Request,
    payload: WorkflowTransitionRequest = WorkflowTransitionRequest(),
    actor: dict = Depends(
        require_permission("appointment", "update", audit_allow=False)
    ),
    ctx: TenantContext = Depends(get_tenant_context),
):
    return await _run_transition(
        "ready_for_checkout", appointment_id, payload, request, actor, ctx,
    )


@router.post("/{appointment_id}/undo-ready-for-checkout", response_model=AppointmentPublic)
async def appointment_undo_ready_for_checkout(
    appointment_id: str,
    request: Request,
    payload: WorkflowTransitionRequest = WorkflowTransitionRequest(),
    actor: dict = Depends(
        require_permission("appointment", "update", audit_allow=False)
    ),
    ctx: TenantContext = Depends(get_tenant_context),
):
    """Reverse an accidental Ready-for-Checkout — returns to in_progress."""
    return await _run_transition(
        "undo_ready_for_checkout", appointment_id, payload, request, actor, ctx,
    )


@router.post("/{appointment_id}/complete", response_model=AppointmentPublic)
async def appointment_complete(
    appointment_id: str,
    request: Request,
    payload: WorkflowTransitionRequest = WorkflowTransitionRequest(),
    actor: dict = Depends(
        require_permission("appointment", "update", audit_allow=False)
    ),
    ctx: TenantContext = Depends(get_tenant_context),
):
    return await _run_transition("complete", appointment_id, payload, request, actor, ctx)


@router.post("/{appointment_id}/start-checkout", response_model=AppointmentPublic)
async def appointment_start_checkout(
    appointment_id: str,
    request: Request,
    payload: WorkflowTransitionRequest = WorkflowTransitionRequest(),
    actor: dict = Depends(
        require_permission("appointment", "update", audit_allow=False)
    ),
    ctx: TenantContext = Depends(get_tenant_context),
):
    """Mark the patient as at the checkout counter. Physical-location move
    only — `status` remains `ready_for_checkout`/`completed`. Stamps
    `checkout_started_at`/`_by_user_id` for future handoffs."""
    return await _run_transition(
        "start_checkout", appointment_id, payload, request, actor, ctx,
    )


@router.post("/{appointment_id}/checkout", response_model=AppointmentPublic)
async def appointment_checkout(
    appointment_id: str,
    request: Request,
    payload: CheckoutRequest = CheckoutRequest(),
    actor: dict = Depends(
        require_permission("appointment", "update", audit_allow=False)
    ),
    ctx: TenantContext = Depends(get_tenant_context),
):
    """Complete checkout — `status → checked_out`, optionally capture
    `checkout_notes` / `checkout_summary` in the same atomic write.

    Event-bus publishes `appointment.checkout` with the full updated
    appointment, providing the hook point for future payment-collection,
    invoice-handoff, follow-up-scheduling, and document-print/email
    subscribers without needing to touch this endpoint.
    """
    spec = TRANSITIONS["checkout"]
    current = await _load_for_transition(appointment_id, ctx)
    extra = {
        "checkout_notes": payload.checkout_notes,
        "checkout_summary": payload.checkout_summary,
    }
    updated = await apply_transition(
        appointment_id, spec,
        current=current,
        actor=actor,
        request=request,
        override=payload.override,
        reason=payload.reason,
        location_override=payload.location,
        tenant_id=ctx.tenant_id,
        extra_fields=extra,
    )
    (hydrated,) = await _hydrate([updated])
    return hydrated


@router.post("/{appointment_id}/depart", response_model=AppointmentPublic)
async def appointment_depart(
    appointment_id: str,
    request: Request,
    payload: WorkflowTransitionRequest = WorkflowTransitionRequest(),
    actor: dict = Depends(
        require_permission("appointment", "update", audit_allow=False)
    ),
    ctx: TenantContext = Depends(get_tenant_context),
):
    return await _run_transition("depart", appointment_id, payload, request, actor, ctx)


@router.post("/{appointment_id}/location", response_model=AppointmentPublic)
async def appointment_set_location(
    appointment_id: str,
    payload: PatientLocationChangeRequest,
    request: Request,
    actor: dict = Depends(
        require_permission("appointment", "update", audit_allow=False)
    ),
    ctx: TenantContext = Depends(get_tenant_context),
):
    """Change the patient's physical location without touching lifecycle status."""
    current = await _load_for_transition(appointment_id, ctx)
    updated = await apply_patient_location(
        appointment_id,
        current=current,
        new_location=payload.location,
        actor=actor,
        request=request,
        reason=payload.reason,
        tenant_id=ctx.tenant_id,
    )
    (hydrated,) = await _hydrate([updated])
    return hydrated


# ---------------------------------------------------------------------------
# Room assignment endpoints (Phase 4)
# ---------------------------------------------------------------------------

class _RoomClearRequest(PatientLocationChangeRequest):
    """Reuses `reason` and adds the return-to-waiting toggle."""
    # `location` is inherited but ignored for clear-room; kept for backwards
    # compatibility with the generic request shape.


@router.post("/{appointment_id}/room", response_model=AppointmentPublic)
async def appointment_assign_room(
    appointment_id: str,
    payload: RoomAssignRequest,
    request: Request,
    actor: dict = Depends(
        require_permission("appointment", "update", audit_allow=False)
    ),
    ctx: TenantContext = Depends(get_tenant_context),
):
    """Assign (or change) the current room on an active appointment.

    409 on single-occupancy conflict; pass `force=true` + `reason` to
    override (audited with `forced=true`).
    """
    current = await _load_for_transition(appointment_id, ctx)
    updated = await assign_or_change_room(
        current=current,
        room_id=payload.room_id,
        actor=actor,
        request=request,
        reason=payload.reason,
        force=payload.force,
        tenant_id=ctx.tenant_id,
    )
    (hydrated,) = await _hydrate([updated])
    return hydrated


@router.post("/{appointment_id}/clear-room", response_model=AppointmentPublic)
async def appointment_clear_room(
    appointment_id: str,
    request: Request,
    actor: dict = Depends(
        require_permission("appointment", "update", audit_allow=False)
    ),
    ctx: TenantContext = Depends(get_tenant_context),
    return_to_waiting: bool = Query(
        default=False,
        description="When true, also sets current_location_type=waiting_room.",
    ),
    reason: str | None = Query(default=None),
):
    """Clear the current_room_id. Set `return_to_waiting=true` to also
    move the patient back to the waiting room."""
    current = await _load_for_transition(appointment_id, ctx)
    updated = await clear_room(
        current=current,
        actor=actor,
        request=request,
        reason=reason,
        return_to_waiting=return_to_waiting,
        tenant_id=ctx.tenant_id,
    )
    (hydrated,) = await _hydrate([updated])
    return hydrated


@router.get("/{appointment_id}/room-history")
async def appointment_room_history(
    appointment_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    ctx: TenantContext = Depends(get_tenant_context),
):
    """Return the chronological room/location history for an appointment."""
    await _load_for_transition(appointment_id, ctx)
    db = get_db_read()
    q: dict = {"appointment_id": appointment_id}
    if ctx.tenant_id and not ctx.is_platform_admin:
        q["tenant_id"] = ctx.tenant_id
    rows = [
        r async for r in db.appointment_room_history.find(q, {"_id": 0})
        .sort("at", 1)
    ]
    return rows


