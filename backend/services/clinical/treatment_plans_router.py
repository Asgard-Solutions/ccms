"""Treatment Plan router — Phase 6.

Endpoints (mounted under `/api` via server.py):
    GET    /api/patients/{pid}/clinical/treatment-plans
    POST   /api/patients/{pid}/clinical/treatment-plans
    GET    /api/patients/{pid}/clinical/treatment-plans/{tpid}
    PATCH  /api/patients/{pid}/clinical/treatment-plans/{tpid}
    POST   /api/patients/{pid}/clinical/treatment-plans/{tpid}/set-status

One ACTIVE plan per episode enforced — second active create returns 409
with the existing plan's id in the error detail so the UI can route.
Plans are chart-level artifacts owned by the patient chart. Visit
progress is computed live from signed follow-up notes on the same
episode since the plan's start_date.

Writes require reauth and emit both an audit_logs row + a
clinical_audit_events row.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from core.audit import audit_success
from core.db import get_db_write
from core.deps import require_role
from core.reauth import require_reauth
from core.tenancy import TenantContext, get_tenant_context
from core.tenant_scope import scoped_filter, stamp_for_write
from services.clinical.models import now_iso
from services.clinical.router import _load_patient, _log_clinical_event
from services.clinical.treatment_plans_models import (
    TreatmentPlanCreate,
    TreatmentPlanProgress,
    TreatmentPlanPublic,
    TreatmentPlanSetStatus,
    TreatmentPlanUpdate,
)

router = APIRouter(prefix="/patients", tags=["clinical"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _strip(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k not in {"_id", "history_log"}}


async def _load_plan(db, patient_id: str, tpid: str, ctx: TenantContext) -> dict:
    q = scoped_filter({"id": tpid, "patient_id": patient_id}, ctx, location_scoped=False)
    if q.get("__deny__"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")
    doc = await db.clinical_treatment_plans.find_one(q, {"_id": 0})
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")
    return doc


async def _validate_diagnosis_ids(db, ctx: TenantContext, patient_id: str, ids: list[str]) -> None:
    if not ids:
        return
    cursor = db.clinical_diagnoses.find(
        {
            "tenant_id": ctx.tenant_id,
            "patient_id": patient_id,
            "id": {"$in": list(set(ids))},
        },
        {"_id": 0, "id": 1},
    )
    found = {d["id"] async for d in cursor}
    missing = [i for i in set(ids) if i not in found]
    if missing:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Diagnoses not found on this patient: {missing}",
        )


async def _validate_episode(db, ctx: TenantContext, patient_id: str, episode_id: str | None) -> None:
    if not episode_id:
        return
    ep = await db.clinical_episode_cases.find_one(
        {"tenant_id": ctx.tenant_id, "patient_id": patient_id, "id": episode_id},
        {"_id": 0, "id": 1},
    )
    if not ep:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Episode not found on this patient")


async def _find_active_plan_for_episode(
    db, ctx: TenantContext, patient_id: str, episode_id: str | None,
) -> dict | None:
    q = {
        "tenant_id": ctx.tenant_id,
        "patient_id": patient_id,
        "plan_status": "active",
    }
    # "Episode grouping": two active plans are both allowed only when neither is
    # tied to an episode AND neither shares an episode. We scope by episode_id
    # (including None) for the active-plan guard.
    q["episode_id"] = episode_id
    return await db.clinical_treatment_plans.find_one(q, {"_id": 0})


async def _compute_progress(db, ctx: TenantContext, plan: dict) -> dict:
    total = plan.get("frequency_total_visits")
    q: dict = {
        "tenant_id": ctx.tenant_id,
        "patient_id": plan["patient_id"],
        "status": "signed",
    }
    if plan.get("episode_id"):
        q["episode_id"] = plan["episode_id"]
    start = plan.get("start_date")
    if start:
        q["date_of_service"] = {"$gte": start}
    visits = await db.clinical_follow_up_notes.count_documents(q)

    # Phase 3 Slice 1 — count *future* booked appointments on the same
    # patient/episode so the NextActionsPanel can reason about how many
    # planned visits are still un-scheduled. Read-only aggregation; no
    # writes.
    now = now_iso()
    appt_q: dict = {
        "tenant_id": ctx.tenant_id,
        "patient_id": plan["patient_id"],
        "status": {"$in": ["booked", "confirmed"]},
        "start_time": {"$gte": now},
    }
    if plan.get("episode_id"):
        # Some appointments carry episode_id, most don't yet. Count both.
        appt_q_ep = {**appt_q, "episode_id": plan["episode_id"]}
        scheduled = await db.appointments.count_documents(appt_q_ep)
        if scheduled == 0:
            scheduled = await db.appointments.count_documents(appt_q)
    else:
        scheduled = await db.appointments.count_documents(appt_q)

    pct = None
    if total and total > 0:
        pct = min(100, round((visits / total) * 100))
    return TreatmentPlanProgress(
        visits_completed=visits,
        visits_scheduled=scheduled,
        total_visits=total,
        percent=pct,
    ).model_dump()


async def _hydrate(db, ctx: TenantContext, doc: dict) -> dict:
    out = _strip(doc)
    if out.get("responsible_provider_id"):
        prov = await db.users.find_one(
            {"id": out["responsible_provider_id"], "tenant_id": ctx.tenant_id},
            {"_id": 0, "name": 1, "email": 1},
        )
        if prov:
            out["responsible_provider_name"] = prov.get("name") or prov.get("email")
    if out.get("episode_id"):
        ep = await db.clinical_episode_cases.find_one(
            {"id": out["episode_id"], "tenant_id": ctx.tenant_id},
            {"_id": 0, "title": 1},
        )
        if ep:
            out["episode_title"] = ep.get("title")
    out["progress"] = await _compute_progress(db, ctx, out)
    return out


def _ensure_goal_ids(goals: list[dict]) -> list[dict]:
    out = []
    for g in goals or []:
        g2 = dict(g)
        if not g2.get("id"):
            g2["id"] = str(uuid.uuid4())
        out.append(g2)
    return out


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------
@router.post(
    "/{patient_id}/clinical/treatment-plans",
    response_model=TreatmentPlanPublic,
    status_code=201,
)
async def create_treatment_plan(
    patient_id: str,
    payload: TreatmentPlanCreate,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    await _validate_episode(db, ctx, patient_id, payload.episode_id)
    await _validate_diagnosis_ids(db, ctx, patient_id, payload.diagnosis_ids or [])

    # One active plan per episode
    existing = await _find_active_plan_for_episode(db, ctx, patient_id, payload.episode_id)
    if existing:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"An active treatment plan already exists for this episode "
            f"(id={existing['id']}). Transition it before creating a new one.",
        )

    if payload.responsible_provider_id:
        prov = await db.users.find_one(
            {"id": payload.responsible_provider_id, "tenant_id": ctx.tenant_id},
            {"_id": 0, "id": 1},
        )
        if not prov:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Responsible provider not found")

    now = now_iso()
    tpid = str(uuid.uuid4())
    doc = {
        "id": tpid,
        "patient_id": patient_id,
        "episode_id": payload.episode_id,
        "responsible_provider_id": payload.responsible_provider_id,
        "plan_status": "active",
        "title": payload.title.strip(),
        "diagnosis_ids": list(dict.fromkeys(payload.diagnosis_ids or [])),
        "target_body_regions": list(payload.target_body_regions or []),
        "frequency_visits_per_week": payload.frequency_visits_per_week,
        "frequency_total_visits": payload.frequency_total_visits,
        "expected_duration_weeks": payload.expected_duration_weeks,
        "start_date": payload.start_date or now,
        "re_exam_date": payload.re_exam_date,
        "planned_interventions": [p.model_dump() for p in payload.planned_interventions or []],
        "goals": _ensure_goal_ids([g.model_dump() for g in payload.goals or []]),
        "baselines": payload.baselines.model_dump() if payload.baselines else {},
        "home_care_recommendations": payload.home_care_recommendations,
        "activity_work_recommendations": payload.activity_work_recommendations,
        "discharge_criteria": payload.discharge_criteria,
        "maintenance_transition_notes": payload.maintenance_transition_notes,
        "configured_outcome_measures": list(dict.fromkeys(payload.configured_outcome_measures or [])),
        "discharge_reason": None,
        "discharged_at": None,
        "created_at": now,
        "updated_at": now,
        "created_by": user["id"],
        "updated_by": user["id"],
        "history_log": [{"at": now, "by": user["id"], "action": "created"}],
    }
    doc = stamp_for_write(doc, ctx)
    await db.clinical_treatment_plans.insert_one(doc)

    await _log_clinical_event(
        db, ctx,
        actor=user, patient_id=patient_id, episode_id=payload.episode_id,
        event_type="treatment_plan.created", entity_type="treatment_plan",
        entity_id=tpid, metadata={"title": doc["title"]},
    )
    await audit_success(
        user, "clinical.treatment_plan.created", request,
        entity_type="clinical_treatment_plan", entity_id=tpid, phi_accessed=True,
        metadata={"patient_id": patient_id, "episode_id": payload.episode_id},
    )
    return await _hydrate(db, ctx, doc)


# ---------------------------------------------------------------------------
# List + Read
# ---------------------------------------------------------------------------
@router.get(
    "/{patient_id}/clinical/treatment-plans",
    response_model=list[TreatmentPlanPublic],
)
async def list_treatment_plans(
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
        sts = [s.strip() for s in status_in.split(",") if s.strip()]
        if sts:
            q["plan_status"] = {"$in": sts}
    if episode_id:
        q["episode_id"] = episode_id
    cursor = db.clinical_treatment_plans.find(q, {"_id": 0}).sort("start_date", -1)
    rows = [d async for d in cursor]
    hydrated = [await _hydrate(db, ctx, d) for d in rows]
    await audit_success(
        user, "clinical.treatment_plan.list_viewed", request,
        entity_type="patient", entity_id=patient_id, phi_accessed=True,
        metadata={"count": len(rows)},
    )
    return hydrated


@router.get(
    "/{patient_id}/clinical/treatment-plans/{tpid}",
    response_model=TreatmentPlanPublic,
)
async def get_treatment_plan(
    patient_id: str,
    tpid: str,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    doc = await _load_plan(db, patient_id, tpid, ctx)
    await audit_success(
        user, "clinical.treatment_plan.read", request,
        entity_type="clinical_treatment_plan", entity_id=tpid, phi_accessed=True,
        metadata={"patient_id": patient_id},
    )
    return await _hydrate(db, ctx, doc)


# ---------------------------------------------------------------------------
# PATCH
# ---------------------------------------------------------------------------
@router.patch(
    "/{patient_id}/clinical/treatment-plans/{tpid}",
    response_model=TreatmentPlanPublic,
)
async def patch_treatment_plan(
    patient_id: str,
    tpid: str,
    payload: TreatmentPlanUpdate,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    current = await _load_plan(db, patient_id, tpid, ctx)

    if current["plan_status"] in ("discharged", "cancelled", "completed"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Plan is {current['plan_status']}; use set-status to transition first",
        )

    dumped = payload.model_dump(exclude_unset=True)
    if not dumped:
        return await _hydrate(db, ctx, current)

    if "diagnosis_ids" in dumped:
        ids = list(dict.fromkeys(dumped["diagnosis_ids"] or []))
        await _validate_diagnosis_ids(db, ctx, patient_id, ids)
        dumped["diagnosis_ids"] = ids

    if "responsible_provider_id" in dumped and dumped["responsible_provider_id"]:
        prov = await db.users.find_one(
            {"id": dumped["responsible_provider_id"], "tenant_id": ctx.tenant_id},
            {"_id": 0, "id": 1},
        )
        if not prov:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Responsible provider not found")

    if "goals" in dumped and dumped["goals"] is not None:
        dumped["goals"] = _ensure_goal_ids(dumped["goals"])

    if "title" in dumped and dumped["title"]:
        dumped["title"] = dumped["title"].strip()

    now = now_iso()
    dumped["updated_at"] = now
    dumped["updated_by"] = user["id"]
    fields_changed = sorted(k for k in dumped.keys() if k not in {"updated_at", "updated_by"})

    await db.clinical_treatment_plans.update_one(
        {"id": tpid, "tenant_id": current["tenant_id"]},
        {
            "$set": dumped,
            "$push": {"history_log": {"at": now, "by": user["id"], "action": "updated", "fields": fields_changed}},
        },
    )
    fresh = await db.clinical_treatment_plans.find_one(
        {"id": tpid, "tenant_id": current["tenant_id"]}, {"_id": 0}
    )
    await _log_clinical_event(
        db, ctx, actor=user, patient_id=patient_id, episode_id=fresh.get("episode_id"),
        event_type="treatment_plan.updated", entity_type="treatment_plan", entity_id=tpid,
        metadata={"fields": fields_changed},
    )
    await audit_success(
        user, "clinical.treatment_plan.updated", request,
        entity_type="clinical_treatment_plan", entity_id=tpid, phi_accessed=True,
        metadata={"patient_id": patient_id, "fields": fields_changed},
    )
    return await _hydrate(db, ctx, fresh)


# ---------------------------------------------------------------------------
# Set-status (all transitions with reason)
# ---------------------------------------------------------------------------
@router.post(
    "/{patient_id}/clinical/treatment-plans/{tpid}/set-status",
    response_model=TreatmentPlanPublic,
)
async def set_plan_status(
    patient_id: str,
    tpid: str,
    payload: TreatmentPlanSetStatus,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    current = await _load_plan(db, patient_id, tpid, ctx)

    if current["plan_status"] == payload.plan_status:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Plan is already {payload.plan_status}",
        )

    # If transitioning TO active, no other active plan may exist on the episode.
    if payload.plan_status == "active":
        other = await _find_active_plan_for_episode(db, ctx, patient_id, current.get("episode_id"))
        if other and other["id"] != tpid:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Another active plan (id={other['id']}) exists for this episode",
            )

    now = now_iso()
    sets: dict = {
        "plan_status": payload.plan_status,
        "updated_at": now,
        "updated_by": user["id"],
    }
    if payload.plan_status == "discharged":
        sets["discharge_reason"] = payload.reason
        sets["discharged_at"] = now
    elif current["plan_status"] == "discharged" and payload.plan_status == "active":
        sets["discharge_reason"] = None
        sets["discharged_at"] = None

    await db.clinical_treatment_plans.update_one(
        {"id": tpid, "tenant_id": current["tenant_id"]},
        {
            "$set": sets,
            "$push": {
                "history_log": {
                    "at": now, "by": user["id"], "action": "status_changed",
                    "from": current["plan_status"], "to": payload.plan_status,
                    "reason": payload.reason,
                },
            },
        },
    )
    fresh = await db.clinical_treatment_plans.find_one(
        {"id": tpid, "tenant_id": current["tenant_id"]}, {"_id": 0}
    )
    await _log_clinical_event(
        db, ctx, actor=user, patient_id=patient_id, episode_id=fresh.get("episode_id"),
        event_type="treatment_plan.status_changed", entity_type="treatment_plan", entity_id=tpid,
        metadata={"to": payload.plan_status, "reason": payload.reason},
    )
    await audit_success(
        user, "clinical.treatment_plan.status_changed", request,
        entity_type="clinical_treatment_plan", entity_id=tpid, phi_accessed=True,
        metadata={
            "patient_id": patient_id,
            "from": current["plan_status"],
            "to": payload.plan_status,
            "reason": payload.reason,
        },
    )
    return await _hydrate(db, ctx, fresh)
