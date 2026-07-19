"""Patient-portal questionnaire endpoints.

  GET  /api/portal/questionnaires            — list assignments for me
  GET  /api/portal/questionnaires/{id}       — single assignment + template
  POST /api/portal/questionnaires/{id}/submit{answers: dict}
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from core.audit import audit_success
from core.deps import get_current_user
from core.tenancy import tenant_db
from services.questionnaires.router import ASSIGN_COLL
from services.questionnaires.templates import (
    TEMPLATES, get_template, score_answers,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


router = APIRouter(prefix="/portal/questionnaires", tags=["portal-questionnaires"])


async def _require_portal_patient(user: dict) -> tuple[str, str]:
    if user.get("role") != "patient":
        raise HTTPException(403, "Patient role required")
    tenant_id = user.get("tenant_id")
    patient_id = user.get("linked_patient_id")
    if not tenant_id or not patient_id:
        raise HTTPException(403, "Portal session not bound to a patient")
    return tenant_id, patient_id


@router.get("")
async def list_my_assignments(
    user: dict = Depends(get_current_user),
):
    tenant_id, patient_id = await _require_portal_patient(user)
    cur = tenant_db(tenant_id)[ASSIGN_COLL].find(
        {"tenant_id": tenant_id, "patient_id": patient_id},
        {"_id": 0},
    ).sort("assigned_at", -1)
    rows = [row async for row in cur]
    # Attach a compact template summary to each row so the portal can
    # render title/description without a second round-trip.
    for r in rows:
        tpl = TEMPLATES.get(r.get("template_id"))
        r["template_title"] = tpl["title"] if tpl else r.get("template_id")
        r["template_description"] = tpl["description"] if tpl else None
    return rows


@router.get("/{assignment_id}")
async def get_my_assignment(
    assignment_id: str,
    user: dict = Depends(get_current_user),
):
    tenant_id, patient_id = await _require_portal_patient(user)
    row = await tenant_db(tenant_id)[ASSIGN_COLL].find_one(
        {"tenant_id": tenant_id, "id": assignment_id,
         "patient_id": patient_id},
        {"_id": 0},
    )
    if not row:
        raise HTTPException(404, "Questionnaire not found")
    tpl = get_template(row.get("template_id"))
    if not tpl:
        raise HTTPException(404, "Template missing")
    return {"assignment": row, "template": tpl}


class _SubmitPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answers: dict = Field(default_factory=dict)


@router.post("/{assignment_id}/submit")
async def submit_my_assignment(
    assignment_id: str,
    request: Request,
    payload: _SubmitPayload = Body(...),
    user: dict = Depends(get_current_user),
):
    tenant_id, patient_id = await _require_portal_patient(user)
    db = tenant_db(tenant_id)
    row = await db[ASSIGN_COLL].find_one(
        {"tenant_id": tenant_id, "id": assignment_id,
         "patient_id": patient_id},
        {"_id": 0},
    )
    if not row:
        raise HTTPException(404, "Questionnaire not found")
    if row.get("status") == "completed":
        raise HTTPException(409, "Already completed")

    tpl = get_template(row["template_id"])
    if not tpl:
        raise HTTPException(422, "Template missing")
    # Reject empty / no-meaningful-answer submissions. For each
    # non-optional item the payload must include a concrete value.
    missing: list[str] = []
    for item in tpl.get("items", []):
        if item.get("optional"):
            continue
        v = (payload.answers or {}).get(item["id"])
        if v is None:
            missing.append(item["id"])
            continue
        if item.get("type") == "activity":
            rating = v.get("rating") if isinstance(v, dict) else v
            if rating is None:
                missing.append(item["id"])
    if missing:
        raise HTTPException(
            422,
            f"Missing answers for required items: {', '.join(missing)}",
        )
    scored = score_answers(row["template_id"], payload.answers)

    # Write an outcome_entries row so existing charts pick it up.
    outcome_id = str(uuid.uuid4())
    now = _now_iso()
    await db.outcome_entries.insert_one({
        "id": outcome_id,
        "tenant_id": tenant_id,
        "patient_id": patient_id,
        "measure_type": tpl["measure_type"],
        "label": tpl["title"],
        "score": scored["score"],
        "min_score": tpl["min_score"],
        "max_score": tpl["max_score"],
        "interpretation": scored["interpretation"],
        "collected_at": now,
        "source": "patient_reported",
        "linked_questionnaire_id": assignment_id,
        "created_by": user["id"],
        "created_at": now,
        "updated_at": now,
    })

    await db[ASSIGN_COLL].update_one(
        {"id": assignment_id, "tenant_id": tenant_id},
        {"$set": {
            "status": "completed",
            "answers": payload.answers,
            "score": scored["score"],
            "interpretation": scored["interpretation"],
            "outcome_entry_id": outcome_id,
            "completed_at": now,
            "updated_at": now,
        }},
    )
    await audit_success(
        user, "portal.questionnaire.submitted", request,
        entity_type="questionnaire_assignment", entity_id=assignment_id,
        metadata={"patient_id": patient_id, "template_id": row["template_id"],
                  "score": scored["score"]},
    )
    return {
        "assignment_id": assignment_id,
        "status": "completed",
        "score": scored["score"],
        "interpretation": scored["interpretation"],
        "outcome_entry_id": outcome_id,
    }
