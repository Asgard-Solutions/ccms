"""Questionnaire assignments — staff assign, patient completes.

Collection: ``questionnaire_assignments``
    {id, tenant_id, patient_id, template_id, assigned_by, assigned_at,
     due_at, status (pending|completed|expired), sent_via, completed_at,
     answers, score, interpretation, outcome_entry_id}

Staff routes (``/api/questionnaires/*``):
    GET    /questionnaires/templates             — catalog
    POST   /questionnaires/assign                — create one assignment
    GET    /questionnaires/assignments           — list (filter by patient_id / status)

Patient routes live in ``services.portal.questionnaire_router``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from core.audit import audit_success
from core.deps import require_role
from core.tenancy import TenantContext, get_tenant_context, tenant_db
from services.questionnaires.templates import TEMPLATES, list_templates

ASSIGN_COLL = "questionnaire_assignments"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


router = APIRouter(prefix="/questionnaires", tags=["questionnaires"])


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------
@router.get("/templates")
async def templates_list(
    user: dict = Depends(require_role("admin", "doctor", "staff")),
):
    return list_templates()


@router.get("/templates/{template_id}")
async def template_detail(
    template_id: str,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
):
    tpl = TEMPLATES.get(template_id)
    if not tpl:
        raise HTTPException(404, "Template not found")
    return tpl


# ---------------------------------------------------------------------------
# Assign
# ---------------------------------------------------------------------------
class _AssignPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patient_id: str = Field(min_length=1, max_length=64)
    template_id: str = Field(min_length=1, max_length=64)
    due_in_hours: int = Field(default=72, ge=1, le=24 * 30)
    send_sms: bool = True


@router.post("/assign", status_code=201)
async def assign(
    request: Request,
    payload: _AssignPayload = Body(...),
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    if payload.template_id not in TEMPLATES:
        raise HTTPException(422, "Unknown template_id")

    db = tenant_db(ctx.tenant_id)
    patient = await db.patients.find_one(
        {"tenant_id": ctx.tenant_id, "id": payload.patient_id},
        {"_id": 0, "id": 1, "phone": 1, "first_name": 1, "last_name": 1},
    )
    if not patient:
        raise HTTPException(404, "Patient not found")

    from datetime import timedelta
    due_at = (
        datetime.now(timezone.utc) + timedelta(hours=payload.due_in_hours)
    ).isoformat()
    now = _now_iso()
    aid = str(uuid.uuid4())
    doc = {
        "id": aid,
        "tenant_id": ctx.tenant_id,
        "patient_id": payload.patient_id,
        "template_id": payload.template_id,
        "assigned_by": user.get("email") or user.get("id"),
        "assigned_at": now,
        "due_at": due_at,
        "status": "pending",
        "sent_via": None,
        "completed_at": None,
        "answers": None,
        "score": None,
        "interpretation": None,
        "outcome_entry_id": None,
        "created_at": now,
        "updated_at": now,
    }
    await db[ASSIGN_COLL].insert_one(dict(doc))
    doc.pop("_id", None)

    # Fire an SMS invitation (log-only if no credentials).
    if payload.send_sms and patient.get("phone"):
        try:
            from services.sms.client import send_sms
            tpl = TEMPLATES[payload.template_id]
            link = f"/portal/questionnaires/{aid}"
            body = (
                f"Hi {patient.get('first_name') or ''}, please complete your "
                f"{tpl['title']} before your next visit. "
                f"Open: {link} · Reply STOP to opt out."
            )
            result = await send_sms(
                tenant_id=ctx.tenant_id, to=patient["phone"],
                body=body, category="questionnaire_invite",
                related_id=payload.patient_id,
            )
            await db[ASSIGN_COLL].update_one(
                {"id": aid, "tenant_id": ctx.tenant_id},
                {"$set": {"sent_via": result.get("provider"),
                          "updated_at": _now_iso()}},
            )
            doc["sent_via"] = result.get("provider")
        except Exception:  # noqa: BLE001 — best-effort
            pass

    await audit_success(
        user, "questionnaire.assigned", request,
        entity_type="questionnaire_assignment", entity_id=aid,
        metadata={"patient_id": payload.patient_id,
                  "template_id": payload.template_id},
    )
    return doc


@router.get("/assignments")
async def staff_list_assignments(
    patient_id: str | None = None,
    status_filter: str | None = None,
    limit: int = 200,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    q: dict = {"tenant_id": ctx.tenant_id}
    if patient_id:
        q["patient_id"] = patient_id
    if status_filter:
        q["status"] = status_filter
    db = tenant_db(ctx.tenant_id)
    cur = db[ASSIGN_COLL].find(q, {"_id": 0}).sort("assigned_at", -1).limit(limit)
    return [row async for row in cur]
