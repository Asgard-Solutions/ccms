"""Patient-portal booking-request endpoints.

  POST /api/portal/booking-requests
  GET  /api/portal/booking-requests
  POST /api/portal/booking-requests/{id}/cancel

and portal overview (upcoming appointments + at-a-glance):

  GET /api/portal/overview
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from core.audit import audit_success
from core.deps import get_current_user
from core.tenancy import tenant_db
from services.scheduling.booking_requests import (
    BookingRequestCreate, BookingRequestPublic, COLLECTION as BOOKING_COLL,
    create_request, get_request, list_requests,
)


router = APIRouter(prefix="/portal", tags=["portal"])


async def _require_portal_patient(user: dict) -> tuple[str, str]:
    if user.get("role") != "patient":
        raise HTTPException(403, "Patient role required")
    tenant_id = user.get("tenant_id")
    patient_id = user.get("linked_patient_id")
    if not tenant_id or not patient_id:
        raise HTTPException(403, "Portal session not bound to a patient")
    return tenant_id, patient_id


@router.get("/overview")
async def portal_overview(user: dict = Depends(get_current_user)):
    tenant_id, patient_id = await _require_portal_patient(user)
    db = tenant_db(tenant_id)
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    # Upcoming appointments (next 10, regardless of how far out — so
    # that a patient who just had a booking request approved for a few
    # weeks out still sees it confirmed on their overview).
    upcoming = []
    cur = db.appointments.find(
        {
            "tenant_id": tenant_id,
            "patient_id": patient_id,
            "start_time": {"$gte": now_iso[:10] + "T00:00:00"},
            "status": {"$nin": ["cancelled", "no_show", "completed"]},
        },
        {"_id": 0, "id": 1, "start_time": 1, "end_time": 1,
         "status": 1, "provider_id": 1, "location_id": 1,
         "appointment_type_id": 1, "arrived_at": 1, "arrived_via": 1},
    ).sort("start_time", 1).limit(10)
    async for row in cur:
        upcoming.append(row)

    # Pending booking requests.
    pending_requests = await list_requests(
        tenant_id=tenant_id, status_filter="pending",
        patient_id=patient_id,
    )

    # Pending questionnaires.
    pending_q = []
    from services.questionnaires.router import ASSIGN_COLL
    from services.questionnaires.templates import TEMPLATES
    qcur = db[ASSIGN_COLL].find(
        {"tenant_id": tenant_id, "patient_id": patient_id,
         "status": "pending"},
        {"_id": 0},
    ).sort("assigned_at", -1).limit(10)
    async for row in qcur:
        tpl = TEMPLATES.get(row.get("template_id"))
        row["template_title"] = tpl["title"] if tpl else row.get("template_id")
        pending_q.append(row)

    return {
        "upcoming_appointments": upcoming,
        "pending_booking_requests": pending_requests,
        "pending_questionnaires": pending_q,
    }


@router.post("/booking-requests", response_model=BookingRequestPublic, status_code=201)
async def portal_create_booking(
    request: Request,
    payload: BookingRequestCreate = Body(...),
    user: dict = Depends(get_current_user),
):
    tenant_id, patient_id = await _require_portal_patient(user)
    doc = await create_request(
        tenant_id=tenant_id, patient_id=patient_id, payload=payload,
    )
    await audit_success(
        user, "portal.booking_request.created", request,
        entity_type="booking_request", entity_id=doc["id"],
        metadata={"patient_id": patient_id,
                  "provider_id": payload.provider_id},
    )
    return doc


@router.get("/booking-requests")
async def portal_list_bookings(
    user: dict = Depends(get_current_user),
):
    tenant_id, patient_id = await _require_portal_patient(user)
    return await list_requests(
        tenant_id=tenant_id, patient_id=patient_id,
    )


@router.post("/booking-requests/{request_id}/cancel",
             response_model=BookingRequestPublic)
async def portal_cancel_booking(
    request_id: str, request: Request,
    user: dict = Depends(get_current_user),
):
    tenant_id, patient_id = await _require_portal_patient(user)
    doc = await get_request(tenant_id, request_id)
    if not doc or doc.get("patient_id") != patient_id:
        raise HTTPException(404, "Booking request not found")
    if doc["status"] != "pending":
        raise HTTPException(409, f"Request already {doc['status']}")
    now = datetime.now(timezone.utc).isoformat()
    await tenant_db(tenant_id)[BOOKING_COLL].update_one(
        {"id": request_id, "tenant_id": tenant_id},
        {"$set": {"status": "cancelled", "updated_at": now,
                  "decision_at": now,
                  "decision_by": user.get("email") or user.get("id")}},
    )
    updated = await get_request(tenant_id, request_id)
    await audit_success(
        user, "portal.booking_request.cancelled", request,
        entity_type="booking_request", entity_id=request_id,
        metadata={"patient_id": patient_id},
    )
    updated = dict(updated or {})
    updated.pop("_id", None)
    return updated


# ---------------------------------------------------------------------------
# Staff-facing: list appointments + providers + appointment-types for the
# booking UI. Very small — just the subset the portal needs for dropdowns.
# ---------------------------------------------------------------------------
@router.get("/providers")
async def portal_list_providers(
    user: dict = Depends(get_current_user),
):
    tenant_id, _ = await _require_portal_patient(user)
    from core.db import get_db
    admin_db = get_db()
    cur = admin_db.users.find(
        {"tenant_id": tenant_id, "role": "doctor", "status": "active"},
        {"_id": 0, "id": 1, "name": 1, "email": 1},
    ).sort("name", 1)
    return [row async for row in cur]


@router.get("/appointment-types")
async def portal_list_appt_types(
    user: dict = Depends(get_current_user),
):
    tenant_id, _ = await _require_portal_patient(user)
    db = tenant_db(tenant_id)
    cur = db.appointment_types.find(
        {"tenant_id": tenant_id, "active": {"$ne": False}},
        {"_id": 0, "id": 1, "name": 1, "duration_minutes": 1, "color": 1},
    ).sort("name", 1)
    return [row async for row in cur]
