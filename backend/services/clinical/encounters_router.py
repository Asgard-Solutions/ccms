"""Clinical encounters router — Phase 3 appointment-launched encounter shell.

Two route prefixes work together:

  /api/appointments/{aid}/clinical/...   (convenience launch + lookup)
  /api/patients/{pid}/clinical/...       (authoritative patient-owned chart)

The patient prefix is the source of truth; the appointment prefix exists so
the calendar / appointment UI never has to resolve to a patient id before
starting a visit.

Launch rules enforced here:
  * Must be admin / doctor / staff to launch; writes also require reauth.
  * Cancelled appointments REQUIRE `exception_reason` AND the launcher must
    be admin or doctor (staff cannot bend the rule). The resulting
    encounter is flagged `is_exception=True` and records who invoked it
    and when, plus the appointment's status at launch.
  * Only ONE non-cancelled encounter per appointment. A second launch
    returns the existing encounter with `existed=true` so retry clicks
    are idempotent from the UI.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from core.audit import audit_success
from core.db import get_db_write
from core.deps import require_role
from core.reauth import require_reauth
from core.tenancy import TenantContext, get_tenant_context
from core.tenant_scope import scoped_filter, stamp_for_write
from services.clinical.encounters_models import (
    EncounterCancel,
    EncounterComplete,
    EncounterLaunchRequest,
    EncounterLaunchResult,
    EncounterPublic,
    EncounterUpdate,
)
from services.clinical.models import now_iso
from services.clinical.router import _load_patient, _log_clinical_event

appt_router = APIRouter(prefix="/appointments", tags=["clinical"])
patient_router = APIRouter(prefix="/patients", tags=["clinical"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _parse_iso(s: str) -> datetime:
    # Appointment times come out of Mongo as strings (e.g. "2026-02-21T09:00:00+00:00").
    # fromisoformat handles the common shape; fall back to stripping trailing 'Z'.
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return datetime.fromisoformat(s[:19])


def _duration_min(start: str, end: str) -> int | None:
    try:
        d = (_parse_iso(end) - _parse_iso(start)).total_seconds() / 60
        return max(0, int(round(d)))
    except Exception:
        return None


async def _load_appointment(db, appointment_id: str, ctx: TenantContext) -> dict:
    q = scoped_filter({"id": appointment_id}, ctx, location_scoped=True)
    if q.get("__deny__"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Appointment not found")
    appt = await db.appointments.find_one(q, {"_id": 0})
    if not appt:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Appointment not found")
    return appt


async def _load_encounter(db, patient_id: str, encounter_id: str, ctx: TenantContext) -> dict:
    q = scoped_filter(
        {"id": encounter_id, "patient_id": patient_id}, ctx, location_scoped=False,
    )
    if q.get("__deny__"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Encounter not found")
    doc = await db.clinical_encounters.find_one(q, {"_id": 0})
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Encounter not found")
    return doc


async def _validate_episode(
    db, ctx: TenantContext, patient_id: str, episode_id: str | None
) -> dict | None:
    if not episode_id:
        return None
    ep = await db.clinical_episode_cases.find_one(
        {"id": episode_id, "patient_id": patient_id, "tenant_id": ctx.tenant_id},
        {"_id": 0},
    )
    if not ep:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Episode not found on this patient",
        )
    return ep


async def _hydrate_encounter(db, tenant_id: str, doc: dict) -> dict:
    """Attach provider_name + episode_title for the response payload."""
    out = {k: v for k, v in doc.items() if k not in {"_id", "history"}}
    if out.get("provider_id"):
        prov = await db.users.find_one(
            {"id": out["provider_id"], "tenant_id": tenant_id},
            {"_id": 0, "name": 1, "email": 1},
        )
        if prov:
            out["provider_name"] = prov.get("name") or prov.get("email")
    if out.get("episode_id"):
        ep = await db.clinical_episode_cases.find_one(
            {"id": out["episode_id"], "tenant_id": tenant_id},
            {"_id": 0, "title": 1},
        )
        if ep:
            out["episode_title"] = ep.get("title")
    return out


def _snapshot_appointment(appt: dict) -> dict:
    return {
        "appointment_id": appt["id"],
        "patient_id": appt["patient_id"],
        "provider_id": appt.get("provider_id"),
        "location_id": appt.get("location_id"),
        "start_time": appt["start_time"],
        "end_time": appt["end_time"],
        "status": appt["status"],
        "reason": appt.get("reason"),
    }


# ---------------------------------------------------------------------------
# Launch — convenience POST under /appointments
# ---------------------------------------------------------------------------
@appt_router.post(
    "/{appointment_id}/clinical/encounters",
    response_model=EncounterLaunchResult,
)
async def launch_encounter(
    appointment_id: str,
    payload: EncounterLaunchRequest,
    request: Request,
    response: Response,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)

    db = get_db_write()
    appt = await _load_appointment(db, appointment_id, ctx)

    # Reuse an existing non-cancelled encounter — launch is idempotent.
    existing = await db.clinical_encounters.find_one(
        {
            "tenant_id": ctx.tenant_id,
            "appointment_id": appointment_id,
            "status": {"$ne": "cancelled"},
        },
        {"_id": 0},
    )
    if existing:
        hydrated = await _hydrate_encounter(db, ctx.tenant_id, existing)
        response.status_code = 200
        return {"encounter": hydrated, "existed": True}

    # Cancelled appointments require an exception reason + elevated role.
    appt_status = appt["status"]
    is_exception = False
    if appt_status == "cancelled":
        if not payload.exception_reason or len(payload.exception_reason.strip()) < 3:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Cancelled appointment requires an exception_reason to launch an encounter",
            )
        if user.get("role") not in ("admin", "doctor"):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Only doctors or admins can launch an exception encounter on a cancelled appointment",
            )
        is_exception = True

    await _validate_episode(db, ctx, appt["patient_id"], payload.episode_id)

    now = now_iso()
    enc_id = str(uuid.uuid4())
    snapshot = _snapshot_appointment(appt)
    encounter = {
        "id": enc_id,
        "location_id": appt.get("location_id"),
        "patient_id": appt["patient_id"],
        "appointment_id": appointment_id,
        "provider_id": appt.get("provider_id"),
        "episode_id": payload.episode_id,
        "encounter_type": payload.encounter_type,
        "status": "in_progress",
        "date_of_service": appt["start_time"],
        "scheduled_start": appt["start_time"],
        "scheduled_end": appt["end_time"],
        "scheduled_duration_min": _duration_min(appt["start_time"], appt["end_time"]),
        "actual_start": now,  # launch = actual start
        "actual_end": None,
        "appointment_snapshot": snapshot,
        "appointment_status_at_launch": appt_status,
        "is_exception": is_exception,
        "exception_reason": payload.exception_reason.strip()
            if (is_exception and payload.exception_reason) else None,
        "exception_invoked_by": user["id"] if is_exception else None,
        "exception_invoked_at": now if is_exception else None,
        "notes": (payload.notes or "").strip() or None,
        "completed_at": None,
        "completed_by": None,
        "cancelled_at": None,
        "cancelled_reason": None,
        "created_at": now,
        "updated_at": now,
        "created_by": user["id"],
        "updated_by": user["id"],
        "history": [
            {
                "at": now, "by": user["id"],
                "action": "launched",
                "encounter_type": payload.encounter_type,
                "is_exception": is_exception,
                "appointment_status_at_launch": appt_status,
            }
        ],
    }
    encounter = stamp_for_write(encounter, ctx, location_id=appt.get("location_id"))

    await db.clinical_encounters.insert_one(encounter)

    await _log_clinical_event(
        db, ctx,
        actor=user, patient_id=appt["patient_id"], episode_id=payload.episode_id,
        event_type="encounter.launched", entity_type="encounter", entity_id=enc_id,
        metadata={
            "appointment_id": appointment_id,
            "encounter_type": payload.encounter_type,
            "is_exception": is_exception,
            "appointment_status_at_launch": appt_status,
            "exception_reason": encounter["exception_reason"],
        },
    )
    await audit_success(
        user, "clinical.encounter.launched", request,
        entity_type="clinical_encounter", entity_id=enc_id, phi_accessed=True,
        metadata={
            "appointment_id": appointment_id,
            "patient_id": appt["patient_id"],
            "encounter_type": payload.encounter_type,
            "is_exception": is_exception,
        },
    )

    hydrated = await _hydrate_encounter(db, ctx.tenant_id, encounter)
    response.status_code = 201
    return {"encounter": hydrated, "existed": False}


@appt_router.get(
    "/{appointment_id}/clinical/encounter",
    response_model=EncounterPublic | None,
)
async def get_encounter_for_appointment(
    appointment_id: str,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = get_db_write()
    await _load_appointment(db, appointment_id, ctx)
    doc = await db.clinical_encounters.find_one(
        {
            "tenant_id": ctx.tenant_id,
            "appointment_id": appointment_id,
            "status": {"$ne": "cancelled"},
        },
        {"_id": 0},
    )
    if not doc:
        return None
    await audit_success(
        user, "clinical.encounter.lookup_by_appointment", request,
        entity_type="clinical_encounter", entity_id=doc["id"], phi_accessed=True,
        metadata={"appointment_id": appointment_id},
    )
    return await _hydrate_encounter(db, ctx.tenant_id, doc)


# ---------------------------------------------------------------------------
# Patient-owned routes (authoritative chart surface)
# ---------------------------------------------------------------------------
@patient_router.get(
    "/{patient_id}/clinical/encounters",
    response_model=list[EncounterPublic],
)
async def list_patient_encounters(
    patient_id: str,
    request: Request,
    status_in: str | None = Query(default=None),
    episode_id: str | None = Query(default=None),
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    q: dict = scoped_filter({"patient_id": patient_id}, ctx, location_scoped=False)
    if q.get("__deny__"):
        return []
    if status_in:
        statuses = [s.strip() for s in status_in.split(",") if s.strip()]
        if statuses:
            q["status"] = {"$in": statuses}
    if episode_id:
        q["episode_id"] = episode_id

    cursor = db.clinical_encounters.find(q, {"_id": 0}).sort("date_of_service", -1)
    rows = [d async for d in cursor]
    hydrated = [await _hydrate_encounter(db, ctx.tenant_id, d) for d in rows]

    await audit_success(
        user, "clinical.encounter.list_viewed", request,
        entity_type="patient", entity_id=patient_id, phi_accessed=True,
        metadata={"count": len(rows)},
    )
    return hydrated


@patient_router.get(
    "/{patient_id}/clinical/encounters/{encounter_id}",
    response_model=EncounterPublic,
)
async def get_encounter(
    patient_id: str,
    encounter_id: str,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    doc = await _load_encounter(db, patient_id, encounter_id, ctx)
    await audit_success(
        user, "clinical.encounter.read", request,
        entity_type="clinical_encounter", entity_id=encounter_id, phi_accessed=True,
        metadata={"patient_id": patient_id},
    )
    return await _hydrate_encounter(db, ctx.tenant_id, doc)


@patient_router.patch(
    "/{patient_id}/clinical/encounters/{encounter_id}",
    response_model=EncounterPublic,
)
async def update_encounter(
    patient_id: str,
    encounter_id: str,
    payload: EncounterUpdate,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    current = await _load_encounter(db, patient_id, encounter_id, ctx)

    if current["status"] != "in_progress":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Encounter is {current['status']}; reopen not supported in Phase 3",
        )

    dumped = payload.model_dump(exclude_unset=True)
    if not dumped:
        return await _hydrate_encounter(db, ctx.tenant_id, current)

    if "episode_id" in dumped:
        await _validate_episode(db, ctx, patient_id, dumped["episode_id"])

    now = now_iso()
    dumped["updated_at"] = now
    dumped["updated_by"] = user["id"]
    fields_changed = sorted([k for k in dumped if k not in {"updated_at", "updated_by"}])

    await db.clinical_encounters.update_one(
        {"id": encounter_id, "tenant_id": current["tenant_id"]},
        {
            "$set": dumped,
            "$push": {
                "history": {
                    "at": now, "by": user["id"],
                    "action": "updated", "fields": fields_changed,
                }
            },
        },
    )
    fresh = await db.clinical_encounters.find_one(
        {"id": encounter_id, "tenant_id": current["tenant_id"]}, {"_id": 0}
    )
    await _log_clinical_event(
        db, ctx,
        actor=user, patient_id=patient_id, episode_id=fresh.get("episode_id"),
        event_type="encounter.updated", entity_type="encounter", entity_id=encounter_id,
        metadata={"fields": fields_changed},
    )
    await audit_success(
        user, "clinical.encounter.updated", request,
        entity_type="clinical_encounter", entity_id=encounter_id, phi_accessed=True,
        metadata={"patient_id": patient_id, "fields": fields_changed},
    )
    return await _hydrate_encounter(db, ctx.tenant_id, fresh)


@patient_router.post(
    "/{patient_id}/clinical/encounters/{encounter_id}/complete",
    response_model=EncounterPublic,
)
async def complete_encounter(
    patient_id: str,
    encounter_id: str,
    payload: EncounterComplete,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    current = await _load_encounter(db, patient_id, encounter_id, ctx)

    if current["status"] != "in_progress":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Only in_progress encounters can be completed; current status = {current['status']}",
        )

    now = now_iso()
    sets = {
        "status": "completed",
        "actual_start": payload.actual_start or current.get("actual_start") or now,
        "actual_end": payload.actual_end or now,
        "notes": (payload.notes.strip() if payload.notes else current.get("notes")),
        "completed_at": now,
        "completed_by": user["id"],
        "updated_at": now,
        "updated_by": user["id"],
    }
    await db.clinical_encounters.update_one(
        {"id": encounter_id, "tenant_id": current["tenant_id"]},
        {
            "$set": sets,
            "$push": {"history": {"at": now, "by": user["id"], "action": "completed"}},
        },
    )
    fresh = await db.clinical_encounters.find_one(
        {"id": encounter_id, "tenant_id": current["tenant_id"]}, {"_id": 0}
    )
    await _log_clinical_event(
        db, ctx,
        actor=user, patient_id=patient_id, episode_id=fresh.get("episode_id"),
        event_type="encounter.completed", entity_type="encounter", entity_id=encounter_id,
    )
    await audit_success(
        user, "clinical.encounter.completed", request,
        entity_type="clinical_encounter", entity_id=encounter_id, phi_accessed=True,
        metadata={"patient_id": patient_id},
    )
    return await _hydrate_encounter(db, ctx.tenant_id, fresh)


@patient_router.post(
    "/{patient_id}/clinical/encounters/{encounter_id}/cancel",
    response_model=EncounterPublic,
)
async def cancel_encounter(
    patient_id: str,
    encounter_id: str,
    payload: EncounterCancel,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    current = await _load_encounter(db, patient_id, encounter_id, ctx)
    if current["status"] == "completed":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Completed encounter cannot be cancelled",
        )
    if current["status"] == "cancelled":
        raise HTTPException(status.HTTP_409_CONFLICT, "Encounter already cancelled")

    now = now_iso()
    await db.clinical_encounters.update_one(
        {"id": encounter_id, "tenant_id": current["tenant_id"]},
        {
            "$set": {
                "status": "cancelled",
                "cancelled_at": now,
                "cancelled_reason": payload.reason.strip(),
                "updated_at": now,
                "updated_by": user["id"],
            },
            "$push": {
                "history": {
                    "at": now, "by": user["id"], "action": "cancelled",
                    "reason": payload.reason.strip(),
                }
            },
        },
    )
    fresh = await db.clinical_encounters.find_one(
        {"id": encounter_id, "tenant_id": current["tenant_id"]}, {"_id": 0}
    )
    await _log_clinical_event(
        db, ctx,
        actor=user, patient_id=patient_id, episode_id=fresh.get("episode_id"),
        event_type="encounter.cancelled", entity_type="encounter", entity_id=encounter_id,
        metadata={"reason": payload.reason.strip()},
    )
    await audit_success(
        user, "clinical.encounter.cancelled", request,
        entity_type="clinical_encounter", entity_id=encounter_id, phi_accessed=True,
        metadata={"patient_id": patient_id, "reason": payload.reason.strip()},
    )
    return await _hydrate_encounter(db, ctx.tenant_id, fresh)
