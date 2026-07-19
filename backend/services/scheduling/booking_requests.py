"""Booking-request service — patients submit preferred slots; staff
approve or decline.

Two entry points share this module:
  * `POST /api/portal/booking-requests` (patient role, auth_router)
  * `GET / POST /api/booking-requests/*`       (staff — admin/staff)

On approve, we materialise a real `appointments` row using the first
preferred slot (or an override supplied by staff) and flip the request
to ``status=approved``. On decline we stamp a reason and leave an
audit trail.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from core.audit import audit_success
from core.deps import require_role
from core.tenancy import TenantContext, get_tenant_context, tenant_db

COLLECTION = "booking_requests"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class PreferredSlot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_time: str = Field(min_length=10, max_length=32)  # ISO 8601
    reason: str | None = Field(default=None, max_length=140)


class BookingRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_id: str | None = Field(default=None, max_length=64)
    appointment_type_id: str | None = Field(default=None, max_length=64)
    reason: str | None = Field(default=None, max_length=500)
    preferred_slots: list[PreferredSlot] = Field(default_factory=list, max_length=5)
    patient_notes: str | None = Field(default=None, max_length=1000)


class BookingRequestPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    tenant_id: str
    patient_id: str
    provider_id: str | None = None
    appointment_type_id: str | None = None
    reason: str | None = None
    preferred_slots: list[dict] = []
    patient_notes: str | None = None
    status: Literal["pending", "approved", "declined", "cancelled"] = "pending"
    decision_by: str | None = None
    decision_at: str | None = None
    decision_reason: str | None = None
    appointment_id: str | None = None
    created_at: str
    updated_at: str


def _public(doc: dict) -> dict:
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


async def create_request(
    *, tenant_id: str, patient_id: str, payload: BookingRequestCreate,
) -> dict:
    now = _now_iso()
    doc = {
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "patient_id": patient_id,
        "provider_id": payload.provider_id,
        "appointment_type_id": payload.appointment_type_id,
        "reason": (payload.reason or "").strip() or None,
        "preferred_slots": [s.model_dump() for s in payload.preferred_slots],
        "patient_notes": (payload.patient_notes or "").strip() or None,
        "status": "pending",
        "created_at": now,
        "updated_at": now,
    }
    await tenant_db(tenant_id)[COLLECTION].insert_one(dict(doc))
    return _public(doc)


async def list_requests(
    *, tenant_id: str, status_filter: str | None = None,
    patient_id: str | None = None,
) -> list[dict]:
    q: dict = {"tenant_id": tenant_id}
    if status_filter:
        q["status"] = status_filter
    if patient_id:
        q["patient_id"] = patient_id
    cur = tenant_db(tenant_id)[COLLECTION].find(q, {"_id": 0}).sort("created_at", -1)
    return [doc async for doc in cur]


async def get_request(tenant_id: str, request_id: str) -> dict | None:
    return await tenant_db(tenant_id)[COLLECTION].find_one(
        {"tenant_id": tenant_id, "id": request_id}, {"_id": 0},
    )


async def _mark(
    tenant_id: str, request_id: str, *, status_str: str,
    actor: dict, reason: str | None = None,
    appointment_id: str | None = None,
) -> dict:
    now = _now_iso()
    updates = {
        "status": status_str,
        "decision_by": actor.get("email") or actor.get("id"),
        "decision_at": now,
        "updated_at": now,
    }
    if reason is not None:
        updates["decision_reason"] = reason
    if appointment_id is not None:
        updates["appointment_id"] = appointment_id
    await tenant_db(tenant_id)[COLLECTION].update_one(
        {"tenant_id": tenant_id, "id": request_id},
        {"$set": updates},
    )
    doc = await get_request(tenant_id, request_id)
    return _public(doc or {})


# ---------------------------------------------------------------------------
# Staff router
# ---------------------------------------------------------------------------
staff_router = APIRouter(prefix="/booking-requests", tags=["booking-requests"])


@staff_router.get("", response_model=list[BookingRequestPublic])
async def staff_list(
    status_filter: str | None = None,
    user: dict = Depends(require_role("admin", "staff", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    return await list_requests(
        tenant_id=ctx.tenant_id, status_filter=status_filter,
    )


@staff_router.get("/{request_id}", response_model=BookingRequestPublic)
async def staff_get(
    request_id: str,
    user: dict = Depends(require_role("admin", "staff", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    doc = await get_request(ctx.tenant_id, request_id)
    if not doc:
        raise HTTPException(404, "Booking request not found")
    return _public(doc)


class _ApprovePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Staff can either accept one of the preferred slots or supply a
    # completely new start_time. Duration_minutes defaults to 30 if
    # not provided.
    start_time: str = Field(min_length=10, max_length=32)
    duration_minutes: int = Field(default=30, ge=5, le=480)
    location_id: str | None = Field(default=None, max_length=64)
    provider_id: str | None = Field(default=None, max_length=64)
    appointment_type_id: str | None = Field(default=None, max_length=64)
    note_to_patient: str | None = Field(default=None, max_length=500)


@staff_router.post("/{request_id}/approve", response_model=BookingRequestPublic)
async def staff_approve(
    request_id: str,
    request: Request,
    payload: _ApprovePayload = Body(...),
    user: dict = Depends(require_role("admin", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    doc = await get_request(ctx.tenant_id, request_id)
    if not doc:
        raise HTTPException(404, "Booking request not found")
    if doc["status"] != "pending":
        raise HTTPException(409, f"Request already {doc['status']}")

    # Materialise an appointment row directly on the `appointments`
    # collection. We intentionally don't go through the usual
    # scheduling router (which needs RoomAssignRequest, appointment
    # types, etc.) — a booking-approve is a simpler path.
    from datetime import timedelta
    start_iso = payload.start_time
    try:
        start_dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(422, f"Invalid start_time: {exc}") from exc
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    end_dt = start_dt + timedelta(minutes=payload.duration_minutes)

    appt_id = str(uuid.uuid4())
    appt_doc = {
        "id": appt_id,
        "tenant_id": ctx.tenant_id,
        "patient_id": doc["patient_id"],
        "provider_id": (payload.provider_id or doc.get("provider_id")),
        "appointment_type_id":
            (payload.appointment_type_id or doc.get("appointment_type_id")),
        "location_id": payload.location_id,
        "start_time": start_dt.isoformat(),
        "end_time": end_dt.isoformat(),
        "status": "scheduled",
        "source": "booking_request",
        "booking_request_id": request_id,
        "notes": (payload.note_to_patient or doc.get("patient_notes")),
        # Required by the appointments response model.
        "created_by": user.get("id") or user.get("email"),
        "intake_status": "not_started",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    await tenant_db(ctx.tenant_id).appointments.insert_one(dict(appt_doc))

    updated = await _mark(
        ctx.tenant_id, request_id, status_str="approved", actor=user,
        reason=payload.note_to_patient, appointment_id=appt_id,
    )
    await audit_success(
        user, "booking_request.approved", request,
        entity_type="booking_request", entity_id=request_id,
        metadata={"appointment_id": appt_id, "patient_id": doc["patient_id"]},
    )

    # Fire off an SMS confirmation (log-only if no Twilio creds).
    try:
        from services.sms.client import send_sms
        pt = await tenant_db(ctx.tenant_id).patients.find_one(
            {"id": doc["patient_id"]}, {"_id": 0, "phone": 1},
        )
        if pt and pt.get("phone"):
            body = (
                f"Your appointment is confirmed for "
                f"{start_dt.strftime('%b %-d, %Y at %-I:%M %p')}. "
                "Reply STOP to opt out."
            )
            await send_sms(
                tenant_id=ctx.tenant_id, to=pt["phone"],
                body=body, category="booking_confirm",
                related_id=doc["patient_id"],
            )
    except Exception:  # noqa: BLE001 — best-effort notification
        pass

    return updated


class _DeclinePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=500)


@staff_router.post("/{request_id}/decline", response_model=BookingRequestPublic)
async def staff_decline(
    request_id: str,
    request: Request,
    payload: _DeclinePayload = Body(...),
    user: dict = Depends(require_role("admin", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    doc = await get_request(ctx.tenant_id, request_id)
    if not doc:
        raise HTTPException(404, "Booking request not found")
    if doc["status"] != "pending":
        raise HTTPException(409, f"Request already {doc['status']}")
    updated = await _mark(
        ctx.tenant_id, request_id, status_str="declined", actor=user,
        reason=payload.reason,
    )
    await audit_success(
        user, "booking_request.declined", request,
        entity_type="booking_request", entity_id=request_id,
        metadata={"reason": payload.reason[:200]},
    )
    return updated
