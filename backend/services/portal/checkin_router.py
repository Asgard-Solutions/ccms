"""Patient-portal self check-in endpoint.

The patient must be authenticated (portal OTP) and the appointment must:
  * belong to the logged-in patient
  * be scheduled for today (same UTC calendar day)
  * be in `status in {scheduled}` — we flip it to `arrived`.

We don't enforce a strict "within X minutes" window on the backend — the
front-desk may want to mark a patient checked-in while they're still in
the car. The portal UI will hide the button outside of a ±60-min window
so patients don't tap it accidentally a week ahead.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from core.audit import audit_success
from core.deps import get_current_user
from core.tenancy import tenant_db

router = APIRouter(prefix="/portal", tags=["portal-checkin"])


def _today_utc_date() -> str:
    return datetime.now(timezone.utc).date().isoformat()


@router.post("/appointments/{appointment_id}/check-in")
async def portal_check_in(
    appointment_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    if user.get("role") != "patient":
        raise HTTPException(403, "Patient role required")
    tenant_id = user.get("tenant_id")
    patient_id = user.get("linked_patient_id")
    if not tenant_id or not patient_id:
        raise HTTPException(403, "Portal session not bound to a patient")

    db = tenant_db(tenant_id)
    appt = await db.appointments.find_one(
        {"tenant_id": tenant_id, "id": appointment_id,
         "patient_id": patient_id},
        {"_id": 0},
    )
    if not appt:
        raise HTTPException(404, "Appointment not found")
    start_str = (appt.get("start_time") or "")[:10]
    if start_str != _today_utc_date():
        raise HTTPException(409, "Self check-in is only available on the day of the visit")
    if appt.get("status") not in ("scheduled", "confirmed"):
        raise HTTPException(409, f"Cannot check in — status is {appt.get('status')}")

    now = datetime.now(timezone.utc).isoformat()
    await db.appointments.update_one(
        {"id": appointment_id, "tenant_id": tenant_id},
        {"$set": {
            "status": "arrived",
            "arrived_at": now,
            "arrived_via": "portal",
            "updated_at": now,
        }},
    )
    await audit_success(
        user, "portal.checkin", request,
        entity_type="appointment", entity_id=appointment_id,
        metadata={"patient_id": patient_id},
    )
    appt["status"] = "arrived"
    appt["arrived_at"] = now
    appt["arrived_via"] = "portal"
    return appt
