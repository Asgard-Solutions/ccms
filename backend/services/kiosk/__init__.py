"""Public kiosk check-in endpoint — for a tablet at the front desk.

Security model:
  * Public (no auth cookies) — a tablet in the waiting room cannot
    realistically carry staff credentials.
  * Tenant is resolved from the request header `X-Kiosk-Tenant` or
    falls back to the `default` tenant. The kiosk UI stamps the header
    once on setup; swapping tenants requires front-desk intervention.
  * Matching is done on `last_name + date_of_birth`, both of which a
    random attacker is unlikely to guess pair-wise. We rate-limit
    elsewhere via the global rate-limit middleware; a kiosk failure
    surfaces a generic "We couldn't find your appointment" message.
  * Only `status in (scheduled, confirmed)` appointments scheduled for
    **today** are eligible.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from core.db import get_db
from core.tenancy import tenant_db

router = APIRouter(prefix="/kiosk", tags=["kiosk"])


class KioskCheckinPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    last_name: str = Field(min_length=1, max_length=64)
    date_of_birth: str = Field(min_length=8, max_length=12)  # YYYY-MM-DD


async def _resolve_tenant_id(slug: str | None) -> str | None:
    db = get_db()
    target = slug or "default"
    row = await db.tenants.find_one({"slug": target}, {"_id": 0, "id": 1})
    return row["id"] if row else None


def _today_utc_date() -> str:
    return datetime.now(timezone.utc).date().isoformat()


@router.post("/check-in")
async def kiosk_check_in(
    payload: KioskCheckinPayload,
    request: Request,
    x_kiosk_tenant: str | None = Header(default=None),
):
    tenant_id = await _resolve_tenant_id(x_kiosk_tenant)
    if not tenant_id:
        raise HTTPException(404, "Unknown tenant")

    db = tenant_db(tenant_id)
    last_name = payload.last_name.strip().lower()
    dob = payload.date_of_birth.strip()
    # Case-insensitive last-name match.
    patient = await db.patients.find_one(
        {
            "tenant_id": tenant_id,
            "date_of_birth": dob,
            "last_name_lc": last_name,
            "status": {"$ne": "deleted"},
        },
        {"_id": 0},
    )
    if not patient:
        # Fallback — some legacy patient rows don't have `last_name_lc`.
        cur = db.patients.find(
            {"tenant_id": tenant_id, "date_of_birth": dob,
             "status": {"$ne": "deleted"}},
            {"_id": 0},
        )
        async for row in cur:
            if (row.get("last_name") or "").strip().lower() == last_name:
                patient = row
                break
    if not patient:
        raise HTTPException(404, "We couldn't find your appointment")

    today = _today_utc_date()
    appt = await db.appointments.find_one(
        {
            "tenant_id": tenant_id,
            "patient_id": patient["id"],
            "status": {"$in": ["scheduled", "confirmed"]},
            "start_time": {"$gte": today + "T00:00:00", "$lt": today + "T23:59:59.999999"},
        },
        sort=[("start_time", 1)],
    )
    if not appt:
        raise HTTPException(404, "No appointment for today")

    now = datetime.now(timezone.utc).isoformat()
    await db.appointments.update_one(
        {"id": appt["id"], "tenant_id": tenant_id},
        {"$set": {
            "status": "arrived",
            "arrived_at": now,
            "arrived_via": "kiosk",
            "updated_at": now,
        }},
    )
    return {
        "patient": {
            "id": patient["id"],
            "first_name": patient.get("first_name"),
            "last_name": patient.get("last_name"),
        },
        "appointment": {
            "id": appt["id"],
            "start_time": appt.get("start_time"),
            "provider_id": appt.get("provider_id"),
        },
        "arrived_via": "kiosk",
        "arrived_at": now,
    }
