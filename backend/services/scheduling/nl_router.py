"""Natural-language scheduling — parse → resolve → confirm → create.

Two endpoints, both admin/doctor/staff:

  • POST /api/scheduling/nl/parse  — turn free-text into a structured
    appointment intent. Returns IDs where unique, candidate lists where
    ambiguous, and a `clarifications[]` array the UI uses to drive the
    confirmation modal.

  • POST /api/scheduling/nl/create — once the user has resolved any
    ambiguities, this is essentially the existing
    `POST /api/appointments` minus the structured-fields tedium.

Tenant-scoped; always audit-logged. The LLM output is treated as a
suggestion — we re-validate every ID and timestamp against the tenant's
own data before touching the appointments collection.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from core.audit import audit_success
from core.deps import require_role
from core.tenancy import TenantContext, get_tenant_context, tenant_db
from services.ai.client import generate, parse_json_safely
from services.ai.prompts import NL_SCHEDULE_SYSTEM

logger = logging.getLogger("ccms.scheduling.nl")

router = APIRouter(prefix="/scheduling/nl", tags=["scheduling-nl"])

MAX_PATIENT_CANDIDATES = 12
MAX_PROVIDERS = 30
MAX_LOCATIONS = 30
MAX_APPT_TYPES = 30


# ---------------------------------------------------------------------------
class _NLRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=2, max_length=400)
    timezone: str | None = Field(default=None, max_length=80)


async def _gather_candidates(tenant_id: str, text: str) -> dict:
    """Pull a small slice of the tenant's directory data the LLM can
    use to resolve names → IDs. Patient list is filtered with a cheap
    contains-match against tokens in the user's text so we don't ship
    the entire patient roster to Claude.
    """
    db = tenant_db(tenant_id)
    base = {"tenant_id": tenant_id}

    tokens = [t.strip().lower() for t in text.split() if len(t) >= 3]
    pat_q: dict = {**base}
    if tokens:
        pat_q["$or"] = [
            {"first_name": {"$regex": tok, "$options": "i"}} for tok in tokens
        ] + [
            {"last_name": {"$regex": tok, "$options": "i"}} for tok in tokens
        ]
    pat_cur = db.patients.find(
        pat_q,
        {"_id": 0, "id": 1, "first_name": 1, "last_name": 1, "dob": 1},
    ).limit(MAX_PATIENT_CANDIDATES)
    patients = [p async for p in pat_cur]

    prov_cur = db.users.find(
        {**base, "role": "doctor", "status": {"$ne": "disabled"}},
        {"_id": 0, "id": 1, "name": 1, "email": 1, "first_name": 1, "last_name": 1},
    ).limit(MAX_PROVIDERS)
    providers = [u async for u in prov_cur]

    loc_cur = db.locations.find(
        {**base, "status": {"$ne": "disabled"}}, {"_id": 0, "id": 1, "name": 1, "code": 1},
    ).limit(MAX_LOCATIONS)
    locations = [loc async for loc in loc_cur]

    type_cur = db.appointment_types.find(
        {**base, "is_active": {"$ne": False}},
        {"_id": 0, "id": 1, "name": 1, "default_duration_minutes": 1},
    ).limit(MAX_APPT_TYPES)
    types = [t async for t in type_cur]

    return {
        "patients": patients, "providers": providers,
        "locations": locations, "appointment_types": types,
    }


def _format_candidates_for_prompt(c: dict, current_iso: str, tz: str) -> str:
    lines = [
        f"current_iso (clinic local): {current_iso}",
        f"timezone: {tz}",
        "",
        "## Patients (top {} candidates by name match)".format(
            len(c["patients"]),
        ),
    ]
    for p in c["patients"]:
        name = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
        dob = p.get("dob") or "—"
        lines.append(f"- id={p['id']} | {name} | dob={dob}")
    if not c["patients"]:
        lines.append("(none — fall back to clarifications)")
    lines.append("")
    lines.append("## Providers")
    for u in c["providers"]:
        nm = u.get("name") or (
            f"{u.get('first_name', '')} {u.get('last_name', '')}".strip()
        ) or u.get("email") or u["id"]
        lines.append(f"- id={u['id']} | {nm}")
    lines.append("")
    lines.append("## Locations")
    for loc in c["locations"]:
        lines.append(
            f"- id={loc['id']} | {loc.get('name', '')} "
            f"(code={loc.get('code', '')})"
        )
    lines.append("")
    lines.append("## Appointment types")
    for t in c["appointment_types"]:
        lines.append(
            f"- id={t['id']} | {t.get('name', '')} "
            f"(default {t.get('default_duration_minutes', 30)} min)"
        )
    return "\n".join(lines)


def _utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
@router.post("/parse")
async def nl_parse(
    request: Request,
    body: _NLRequest = Body(...),
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    candidates = await _gather_candidates(ctx.tenant_id, body.text)
    now_local_iso = datetime.now(timezone.utc).isoformat()
    prompt_user = (
        f"User request: {body.text}\n\n"
        f"{_format_candidates_for_prompt(candidates, now_local_iso, body.timezone or 'UTC')}"
    )
    try:
        result = await generate(
            tenant_id=ctx.tenant_id, actor=user,
            system_prompt=NL_SCHEDULE_SYSTEM,
            user_text=prompt_user,
            surface="nl_schedule_parse",
            response_format="json",
            max_tokens=1200,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("nl-schedule parse failed: %s", str(exc)[:200])
        raise HTTPException(502, "AI scheduling parser unavailable")

    parsed = parse_json_safely(result["text"]) or {}

    # Re-validate IDs against the candidate set so a hallucinated ID
    # never reaches the create endpoint.
    by_id = {
        "patient": {p["id"] for p in candidates["patients"]},
        "provider": {u["id"] for u in candidates["providers"]},
        "location": {loc["id"] for loc in candidates["locations"]},
        "appointment_type": {t["id"] for t in candidates["appointment_types"]},
    }
    for key in ("patient", "provider", "location", "appointment_type"):
        block = parsed.get(key) or {}
        if block.get("id") and block["id"] not in by_id[key]:
            block["id"] = None  # silently strip hallucinated IDs
            block.setdefault("candidates", [])
            parsed[key] = block

    await audit_success(
        user, "ai.nl_schedule.parsed", request,
        entity_type="appointment_intent", entity_id=None,
        metadata={
            "model": result["model"],
            "intent": parsed.get("intent"),
            "confidence": parsed.get("confidence"),
            "has_patient_id": bool((parsed.get("patient") or {}).get("id")),
        },
    )
    return {**parsed, "model": result["model"]}


# ---------------------------------------------------------------------------
class _NLCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patient_id: str
    provider_id: str
    start_iso: str
    duration_minutes: int = Field(ge=5, le=240)
    location_id: str | None = None
    appointment_type_id: str | None = None
    reason: str | None = Field(default=None, max_length=255)
    notes: str | None = Field(default=None, max_length=2000)


@router.post("/create")
async def nl_create(
    request: Request,
    body: _NLCreateRequest = Body(...),
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    # Re-validate every ID against tenant data before mutating state.
    db = tenant_db(ctx.tenant_id)
    pat = await db.patients.find_one(
        {"tenant_id": ctx.tenant_id, "id": body.patient_id}, {"_id": 0, "id": 1},
    )
    if not pat:
        raise HTTPException(404, "Patient not found")
    prov = await db.users.find_one(
        {"tenant_id": ctx.tenant_id, "id": body.provider_id, "role": "doctor"},
        {"_id": 0, "id": 1},
    )
    if not prov:
        raise HTTPException(404, "Provider not found")

    try:
        start = datetime.fromisoformat(body.start_iso.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(422, f"Invalid start_iso `{body.start_iso}`")
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    end = start + timedelta(minutes=body.duration_minutes)

    # Delegate to the canonical appointment-create flow so all the
    # existing event-bus hooks (reminders, billing, etc.) fire.
    from services.scheduling.router import (
        create_appointment, AppointmentCreate,
    )
    payload = AppointmentCreate(
        patient_id=body.patient_id,
        provider_id=body.provider_id,
        start_time=start,
        end_time=end,
        reason=body.reason,
        notes=body.notes,
        location_id=body.location_id,
        appointment_type_id=body.appointment_type_id,
    )
    appt = await create_appointment(
        payload=payload, request=request, actor=user, ctx=ctx,
    )
    await audit_success(
        user, "ai.nl_schedule.created", request,
        entity_type="appointment",
        entity_id=appt.id if hasattr(appt, "id") else (appt or {}).get("id"),
        metadata={
            "patient_id": body.patient_id, "provider_id": body.provider_id,
            "start_iso": _utc_iso(start),
            "duration_minutes": body.duration_minutes,
        },
    )
    return appt
