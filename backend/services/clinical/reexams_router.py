"""Re-Exam router — Phase 6.

Endpoints (mounted under `/api`):
    GET    /api/patients/{pid}/clinical/re-exams
    POST   /api/patients/{pid}/clinical/re-exams
    GET    /api/patients/{pid}/clinical/re-exams/{rid}
    PATCH  /api/patients/{pid}/clinical/re-exams/{rid}
    POST   /api/patients/{pid}/clinical/re-exams/{rid}/mark-sign-ready
    POST   /api/patients/{pid}/clinical/re-exams/{rid}/unmark-sign-ready
    POST   /api/patients/{pid}/clinical/re-exams/{rid}/sign
    GET    /api/patients/{pid}/clinical/re-exams/{rid}/narrative

One re-exam per encounter (non-cancelled). At create we:
  - auto-link the most recent signed initial exam for the episode/patient,
  - auto-link the active treatment plan for the episode, and
  - FREEZE a baseline_snapshot with plan goals + plan baselines + key
    initial exam findings. This snapshot is immutable for the life of the
    re-exam so comparisons remain defensible even if the plan changes.

Signed re-exams are immutable. Signing with
`recommendation_decision=modify_plan` emits an audit event tagging
`treatment_plan.revised_recommended` — the provider mutates the plan
separately (no auto-mutation).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from core.audit import audit_success
from core.db import get_db_write
from core.deps import require_role
from core.reauth import require_reauth
from core.tenancy import TenantContext, get_tenant_context
from core.tenant_scope import scoped_filter, stamp_for_write
from services.clinical.models import now_iso
from services.clinical.reexams_models import (
    ReExamCreate,
    ReExamNarrative,
    ReExamPublic,
    ReExamUpdate,
)
from services.clinical.router import _load_patient, _log_clinical_event

router = APIRouter(prefix="/patients", tags=["clinical"])


async def _materialize_new_diagnoses(
    db, ctx: TenantContext, *, user: dict, patient_id: str,
    episode_id: str | None, new_diagnoses: list[dict], onset_date: str,
) -> list[str]:
    """Create clinical_diagnoses rows for each `new_diagnoses` draft.
    De-duplicate against existing ACTIVE problem list by
    (icd10_code, body_region, laterality). Mirrors the Initial Exam
    sign-time logic exactly."""
    now = now_iso()
    existing_active = [
        d async for d in db.clinical_diagnoses.find(
            {"tenant_id": ctx.tenant_id, "patient_id": patient_id, "status": "active"},
            {"_id": 0},
        )
    ]

    def _key(code: str, region: str | None, lat: str | None):
        return ((code or "").upper(), (region or "").lower(), (lat or "").lower())

    existing_keys = {
        _key(d.get("icd10_code"), d.get("body_region"), d.get("laterality")): d["id"]
        for d in existing_active
    }

    materialized_ids: list[str] = []
    for draft in new_diagnoses or []:
        code = (draft.get("icd10_code") or "").strip().upper()
        label = (draft.get("label") or "").strip()
        if not code or not label:
            continue
        k = _key(code, draft.get("body_region"), draft.get("laterality"))
        if k in existing_keys:
            materialized_ids.append(existing_keys[k])
            continue
        dx_id = str(uuid.uuid4())
        doc = {
            "id": dx_id,
            "patient_id": patient_id,
            "episode_id": episode_id,
            "icd10_code": code, "label": label, "status": "active",
            "is_primary": bool(draft.get("is_primary")),
            "body_region": draft.get("body_region"),
            "laterality": draft.get("laterality"),
            "chronicity": draft.get("chronicity"),
            "onset_date": (onset_date or "")[:10] if onset_date else None,
            "resolved_date": None, "resolution_notes": None, "notes": None,
            "created_at": now, "updated_at": now,
            "created_by": user["id"], "updated_by": user["id"],
        }
        doc = stamp_for_write(doc, ctx)
        await db.clinical_diagnoses.insert_one(doc)
        materialized_ids.append(dx_id)
    return materialized_ids


def _strip(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k not in {"_id", "history_log"}}


async def _load_reexam(db, patient_id: str, rid: str, ctx: TenantContext) -> dict:
    q = scoped_filter({"id": rid, "patient_id": patient_id}, ctx, location_scoped=False)
    if q.get("__deny__"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Re-exam not found")
    doc = await db.clinical_reexams.find_one(q, {"_id": 0})
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Re-exam not found")
    return doc


async def _hydrate(db, tenant_id: str, doc: dict) -> dict:
    out = _strip(doc)
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
    if out.get("signed_by"):
        u = await db.users.find_one(
            {"id": out["signed_by"], "tenant_id": tenant_id},
            {"_id": 0, "name": 1, "email": 1},
        )
        if u:
            out["signed_by_name"] = u.get("name") or u.get("email")
    # Phase 8 — addendum metadata
    addendum_cnt = await db.clinical_addenda.count_documents({
        "tenant_id": tenant_id,
        "parent_type": "re_exam",
        "parent_id": out.get("id"),
    })
    out["addendum_count"] = addendum_cnt
    out["has_addenda"] = addendum_cnt > 0
    if addendum_cnt:
        latest = await db.clinical_addenda.find_one(
            {
                "tenant_id": tenant_id,
                "parent_type": "re_exam",
                "parent_id": out.get("id"),
            },
            {"_id": 0, "created_at": 1},
            sort=[("created_at", -1)],
        )
        out["latest_addendum_at"] = latest.get("created_at") if latest else None
    return out


async def _build_baseline_snapshot(
    db, ctx: TenantContext, patient_id: str, episode_id: str | None,
) -> tuple[dict, str | None, str | None, str | None]:
    """Return (snapshot, initial_exam_id, treatment_plan_id, prior_reexam_id).

    Baseline snapshot contains plan snapshot (goals + baselines + frequency) AND
    initial exam examination summary when available. Keeps the comparison
    surface stable for the life of the re-exam.
    """
    # Prior re-exam for this episode
    prior_q = {"tenant_id": ctx.tenant_id, "patient_id": patient_id, "status": "signed"}
    if episode_id:
        prior_q["episode_id"] = episode_id
    prior = await db.clinical_reexams.find_one(prior_q, {"_id": 0}, sort=[("date_of_service", -1)])
    prior_id = prior["id"] if prior else None

    # Most recent signed initial exam — prefer same-episode, fallback to patient
    exam_q = {"tenant_id": ctx.tenant_id, "patient_id": patient_id, "status": "signed"}
    if episode_id:
        exam_q["episode_id"] = episode_id
    exam = await db.clinical_initial_exams.find_one(
        exam_q, {"_id": 0}, sort=[("date_of_service", -1)],
    )
    if not exam and episode_id:
        exam = await db.clinical_initial_exams.find_one(
            {"tenant_id": ctx.tenant_id, "patient_id": patient_id, "status": "signed"},
            {"_id": 0}, sort=[("date_of_service", -1)],
        )
    exam_id = exam["id"] if exam else None
    exam_findings = exam.get("examination", {}) if exam else {}
    exam_history = exam.get("history", {}) if exam else {}

    # Active plan (episode-scoped)
    plan_q = {
        "tenant_id": ctx.tenant_id, "patient_id": patient_id,
        "episode_id": episode_id, "plan_status": "active",
    }
    plan = await db.clinical_treatment_plans.find_one(plan_q, {"_id": 0})
    plan_id = plan["id"] if plan else None

    plan_snapshot = None
    if plan:
        plan_snapshot = {
            "id": plan["id"],
            "title": plan["title"],
            "start_date": plan.get("start_date"),
            "re_exam_date": plan.get("re_exam_date"),
            "frequency_visits_per_week": plan.get("frequency_visits_per_week"),
            "frequency_total_visits": plan.get("frequency_total_visits"),
            "expected_duration_weeks": plan.get("expected_duration_weeks"),
            "goals": plan.get("goals") or [],
            "baselines": plan.get("baselines") or {},
            "planned_interventions": plan.get("planned_interventions") or [],
            "target_body_regions": plan.get("target_body_regions") or [],
        }

    snapshot = {
        "plan": plan_snapshot,
        "initial_exam": {
            "id": exam_id,
            "date_of_service": (exam or {}).get("date_of_service"),
            "history": exam_history,
            "examination": exam_findings,
        } if exam_id else None,
        "prior_reexam": (
            {
                "id": prior_id,
                "date_of_service": prior.get("date_of_service"),
                "current_findings": prior.get("current_findings") or {},
                "goal_progress": prior.get("goal_progress") or [],
            } if prior else None
        ),
        "snapshotted_at": now_iso(),
    }
    return snapshot, exam_id, plan_id, prior_id


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------
@router.post(
    "/{patient_id}/clinical/re-exams",
    response_model=ReExamPublic,
)
async def create_reexam(
    patient_id: str,
    payload: ReExamCreate,
    request: Request,
    response: Response,
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)

    enc = await db.clinical_encounters.find_one(
        {"tenant_id": ctx.tenant_id, "patient_id": patient_id, "id": payload.encounter_id},
        {"_id": 0},
    )
    if not enc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Encounter not found")
    if enc["status"] == "cancelled":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Cannot start a re-exam on a cancelled encounter",
        )

    existing = await db.clinical_reexams.find_one(
        {"tenant_id": ctx.tenant_id, "encounter_id": enc["id"]},
        {"_id": 0},
    )
    if existing:
        hydrated = await _hydrate(db, ctx.tenant_id, existing)
        response.status_code = 200
        response.headers["X-ReExam-Existed"] = "true"
        return hydrated

    snapshot, exam_id, plan_id, prior_id = await _build_baseline_snapshot(
        db, ctx, patient_id, enc.get("episode_id"),
    )

    # visit_number_at_reexam = count signed follow-up notes on episode up to today
    visit_q: dict = {
        "tenant_id": ctx.tenant_id,
        "patient_id": patient_id,
        "status": "signed",
    }
    if enc.get("episode_id"):
        visit_q["episode_id"] = enc["episode_id"]
    visit_number = await db.clinical_follow_up_notes.count_documents(visit_q)

    now = now_iso()
    rid = str(uuid.uuid4())
    doc = {
        "id": rid,
        "location_id": enc.get("location_id"),
        "patient_id": patient_id,
        "encounter_id": enc["id"],
        "appointment_id": enc.get("appointment_id"),
        "provider_id": enc.get("provider_id"),
        "episode_id": enc.get("episode_id"),
        "treatment_plan_id": plan_id,
        "initial_exam_id": exam_id,
        "prior_reexam_id": prior_id,
        "date_of_service": enc.get("date_of_service") or now,
        "status": "draft",
        "visit_number_at_reexam": visit_number,
        "baseline_snapshot": snapshot,
        "current_findings": {},
        "goal_progress": [],
        "outcome_updates": [],
        "updated_diagnosis_ids": [],
        "new_diagnoses": [],
        "materialized_diagnosis_ids": [],
        "recommendation_decision": None,
        "recommendation_reason": None,
        "revised_plan_summary": None,
        "marked_sign_ready_at": None,
        "marked_sign_ready_by": None,
        "signed_at": None,
        "signed_by": None,
        "created_at": now,
        "updated_at": now,
        "created_by": user["id"],
        "updated_by": user["id"],
        "history_log": [{"at": now, "by": user["id"], "action": "created"}],
    }
    doc = stamp_for_write(doc, ctx, location_id=enc.get("location_id"))
    await db.clinical_reexams.insert_one(doc)

    await _log_clinical_event(
        db, ctx, actor=user, patient_id=patient_id, episode_id=doc.get("episode_id"),
        event_type="re_exam.created", entity_type="re_exam", entity_id=rid,
        metadata={
            "encounter_id": enc["id"],
            "treatment_plan_id": plan_id,
            "initial_exam_id": exam_id,
        },
    )
    await audit_success(
        user, "clinical.re_exam.created", request,
        entity_type="clinical_reexam", entity_id=rid, phi_accessed=True,
        metadata={
            "patient_id": patient_id, "encounter_id": enc["id"],
            "treatment_plan_id": plan_id, "initial_exam_id": exam_id,
        },
    )
    response.status_code = 201
    return await _hydrate(db, ctx.tenant_id, doc)


# ---------------------------------------------------------------------------
# Read + List
# ---------------------------------------------------------------------------
@router.get(
    "/{patient_id}/clinical/re-exams",
    response_model=list[ReExamPublic],
)
async def list_reexams(
    patient_id: str,
    request: Request,
    status_in: str | None = Query(default=None),
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
            q["status"] = {"$in": sts}
    cursor = db.clinical_reexams.find(q, {"_id": 0}).sort("date_of_service", -1)
    rows = [d async for d in cursor]
    hydrated = [await _hydrate(db, ctx.tenant_id, d) for d in rows]
    await audit_success(
        user, "clinical.re_exam.list_viewed", request,
        entity_type="patient", entity_id=patient_id, phi_accessed=True,
        metadata={"count": len(rows)},
    )
    return hydrated


@router.get(
    "/{patient_id}/clinical/re-exams/{rid}",
    response_model=ReExamPublic,
)
async def get_reexam(
    patient_id: str,
    rid: str,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    doc = await _load_reexam(db, patient_id, rid, ctx)
    await audit_success(
        user, "clinical.re_exam.read", request,
        entity_type="clinical_reexam", entity_id=rid, phi_accessed=True,
        metadata={"patient_id": patient_id},
    )
    return await _hydrate(db, ctx.tenant_id, doc)


# ---------------------------------------------------------------------------
# PATCH
# ---------------------------------------------------------------------------
@router.patch(
    "/{patient_id}/clinical/re-exams/{rid}",
    response_model=ReExamPublic,
)
async def patch_reexam(
    patient_id: str,
    rid: str,
    payload: ReExamUpdate,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    current = await _load_reexam(db, patient_id, rid, ctx)
    if current["status"] == "signed":
        raise HTTPException(status.HTTP_409_CONFLICT, "Signed re-exams are immutable")

    dumped = payload.model_dump(exclude_unset=True)
    if not dumped:
        return await _hydrate(db, ctx.tenant_id, current)

    # Validate updated_diagnosis_ids belong to this patient
    if dumped.get("updated_diagnosis_ids"):
        ids = list(dict.fromkeys(dumped["updated_diagnosis_ids"]))
        cursor = db.clinical_diagnoses.find(
            {"tenant_id": ctx.tenant_id, "patient_id": patient_id, "id": {"$in": ids}},
            {"_id": 0, "id": 1},
        )
        found = {d["id"] async for d in cursor}
        missing = [i for i in ids if i not in found]
        if missing:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Diagnoses not found on this patient: {missing}",
            )
        dumped["updated_diagnosis_ids"] = ids

    # Validate goal_progress goal_ids belong to the baseline plan
    if "goal_progress" in dumped and dumped["goal_progress"] is not None:
        plan = (current.get("baseline_snapshot") or {}).get("plan") or {}
        valid_goal_ids = {g.get("id") for g in (plan.get("goals") or []) if g.get("id")}
        bad = [g["goal_id"] for g in dumped["goal_progress"] if g["goal_id"] not in valid_goal_ids and valid_goal_ids]
        if bad:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"goal_progress references unknown goal_ids: {bad}",
            )

    now = now_iso()
    dumped["updated_at"] = now
    dumped["updated_by"] = user["id"]
    fields_changed = sorted(k for k in dumped.keys() if k not in {"updated_at", "updated_by"})

    await db.clinical_reexams.update_one(
        {"id": rid, "tenant_id": current["tenant_id"]},
        {
            "$set": dumped,
            "$push": {"history_log": {"at": now, "by": user["id"], "action": "updated", "fields": fields_changed}},
        },
    )
    fresh = await db.clinical_reexams.find_one(
        {"id": rid, "tenant_id": current["tenant_id"]}, {"_id": 0}
    )
    await _log_clinical_event(
        db, ctx, actor=user, patient_id=patient_id, episode_id=fresh.get("episode_id"),
        event_type="re_exam.updated", entity_type="re_exam", entity_id=rid,
        metadata={"fields": fields_changed},
    )
    await audit_success(
        user, "clinical.re_exam.updated", request,
        entity_type="clinical_reexam", entity_id=rid, phi_accessed=True,
        metadata={"patient_id": patient_id, "fields": fields_changed},
    )
    return await _hydrate(db, ctx.tenant_id, fresh)


# ---------------------------------------------------------------------------
# Sign-ready transitions
# ---------------------------------------------------------------------------
@router.post(
    "/{patient_id}/clinical/re-exams/{rid}/mark-sign-ready",
    response_model=ReExamPublic,
)
async def mark_reexam_sign_ready(
    patient_id: str,
    rid: str,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    current = await _load_reexam(db, patient_id, rid, ctx)
    if current["status"] != "draft":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Only drafts can be marked sign-ready (status={current['status']})",
        )
    now = now_iso()
    await db.clinical_reexams.update_one(
        {"id": rid, "tenant_id": current["tenant_id"]},
        {
            "$set": {
                "status": "sign_ready",
                "marked_sign_ready_at": now,
                "marked_sign_ready_by": user["id"],
                "updated_at": now,
                "updated_by": user["id"],
            },
            "$push": {"history_log": {"at": now, "by": user["id"], "action": "mark_sign_ready"}},
        },
    )
    fresh = await db.clinical_reexams.find_one(
        {"id": rid, "tenant_id": current["tenant_id"]}, {"_id": 0}
    )
    await audit_success(
        user, "clinical.re_exam.mark_sign_ready", request,
        entity_type="clinical_reexam", entity_id=rid, phi_accessed=True,
    )
    return await _hydrate(db, ctx.tenant_id, fresh)


@router.post(
    "/{patient_id}/clinical/re-exams/{rid}/unmark-sign-ready",
    response_model=ReExamPublic,
)
async def unmark_reexam_sign_ready(
    patient_id: str,
    rid: str,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    current = await _load_reexam(db, patient_id, rid, ctx)
    if current["status"] != "sign_ready":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Only sign_ready re-exams can be unmarked (status={current['status']})",
        )
    now = now_iso()
    await db.clinical_reexams.update_one(
        {"id": rid, "tenant_id": current["tenant_id"]},
        {
            "$set": {
                "status": "draft",
                "marked_sign_ready_at": None,
                "marked_sign_ready_by": None,
                "updated_at": now,
                "updated_by": user["id"],
            },
            "$push": {"history_log": {"at": now, "by": user["id"], "action": "unmark_sign_ready"}},
        },
    )
    fresh = await db.clinical_reexams.find_one(
        {"id": rid, "tenant_id": current["tenant_id"]}, {"_id": 0}
    )
    return await _hydrate(db, ctx.tenant_id, fresh)


# ---------------------------------------------------------------------------
# Sign (terminal). If recommendation_decision=modify_plan we emit an audit
# event only — we DO NOT mutate the treatment plan.
# ---------------------------------------------------------------------------
@router.post(
    "/{patient_id}/clinical/re-exams/{rid}/sign",
    response_model=ReExamPublic,
)
async def sign_reexam(
    patient_id: str,
    rid: str,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    current = await _load_reexam(db, patient_id, rid, ctx)
    if current["status"] == "signed":
        raise HTTPException(status.HTTP_409_CONFLICT, "Re-exam is already signed")
    if current["status"] not in ("draft", "sign_ready"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Cannot sign from status={current['status']}",
        )
    if not current.get("recommendation_decision"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "recommendation_decision is required before signing",
        )

    # Materialize new diagnoses (same semantics as Initial Exam sign).
    materialized_ids: list[str] = []
    if current.get("new_diagnoses"):
        materialized_ids = await _materialize_new_diagnoses(
            db, ctx, user=user, patient_id=patient_id,
            episode_id=current.get("episode_id"),
            new_diagnoses=current["new_diagnoses"],
            onset_date=current.get("date_of_service") or now_iso(),
        )

    # Phase 7 — auto-emit standalone outcome entries for trend reporting.
    from services.clinical.outcomes_router import emit_outcomes_from_reexam
    emitted_outcome_ids = await emit_outcomes_from_reexam(
        db, ctx, reexam=current, user=user,
    )

    now = now_iso()
    await db.clinical_reexams.update_one(
        {"id": rid, "tenant_id": current["tenant_id"]},
        {
            "$set": {
                "status": "signed",
                "signed_at": now,
                "signed_by": user["id"],
                "materialized_diagnosis_ids": materialized_ids,
                "updated_at": now,
                "updated_by": user["id"],
            },
            "$push": {
                "history_log": {
                    "at": now, "by": user["id"], "action": "signed",
                    "recommendation": current["recommendation_decision"],
                    "materialized_diagnosis_ids": materialized_ids,
                },
            },
        },
    )
    fresh = await db.clinical_reexams.find_one(
        {"id": rid, "tenant_id": current["tenant_id"]}, {"_id": 0}
    )

    # Primary signing event
    await _log_clinical_event(
        db, ctx, actor=user, patient_id=patient_id, episode_id=fresh.get("episode_id"),
        event_type="re_exam.signed", entity_type="re_exam", entity_id=rid,
        metadata={
            "recommendation": current["recommendation_decision"],
            "treatment_plan_id": current.get("treatment_plan_id"),
            "materialized_diagnosis_ids": materialized_ids,
        },
    )
    # Side-effect-free "revise plan recommended" signal
    if current["recommendation_decision"] == "modify_plan":
        await _log_clinical_event(
            db, ctx, actor=user, patient_id=patient_id, episode_id=fresh.get("episode_id"),
            event_type="treatment_plan.revised_recommended",
            entity_type="treatment_plan",
            entity_id=current.get("treatment_plan_id") or "unlinked",
            metadata={
                "re_exam_id": rid,
                "reason": current.get("recommendation_reason"),
            },
        )
    await audit_success(
        user, "clinical.re_exam.signed", request,
        entity_type="clinical_reexam", entity_id=rid, phi_accessed=True,
        metadata={
            "patient_id": patient_id,
            "recommendation": current["recommendation_decision"],
            "treatment_plan_id": current.get("treatment_plan_id"),
            "materialized_diagnosis_ids": materialized_ids,
            "emitted_outcome_entry_ids": emitted_outcome_ids,
        },
    )
    # Auto-delete scribe audio (HIPAA retention: keep until signed).
    try:
        from services.scribe.router import delete_audio_for_note
        await delete_audio_for_note(
            tenant_id=ctx.tenant_id, note_id=rid,
            note_type="reexam", actor_id=user["id"],
        )
    except Exception:  # noqa: BLE001
        pass
    return await _hydrate(db, ctx.tenant_id, fresh)


# ---------------------------------------------------------------------------
# Narrative
# ---------------------------------------------------------------------------
def _render_reexam_narrative(
    reexam: dict,
    *,
    patient_name: str,
    provider_name: str | None,
    plan_title: str | None,
) -> str:
    lines: list[str] = []
    lines.append("RE-EXAMINATION NOTE")
    lines.append("=" * 60)
    lines.append(f"Patient: {patient_name}")
    lines.append(f"Date of service: {reexam.get('date_of_service', '')}")
    if provider_name:
        lines.append(f"Provider: {provider_name}")
    if plan_title:
        lines.append(f"Active plan: {plan_title}")
    if reexam.get("visit_number_at_reexam") is not None:
        lines.append(f"Visits completed on episode: {reexam['visit_number_at_reexam']}")
    lines.append(f"Status: {reexam.get('status')}")
    if reexam.get("signed_at"):
        lines.append(f"Signed: {reexam['signed_at']}")
    lines.append("")

    snap = reexam.get("baseline_snapshot") or {}
    plan = snap.get("plan") or {}
    exam = snap.get("initial_exam") or {}

    # Comparison — baseline context
    lines.append("BASELINE (frozen)")
    lines.append("-----------------")
    if plan and plan.get("baselines"):
        b = plan["baselines"]
        if b.get("pain_scale_0_10") is not None:
            lines.append(f"  Pain scale baseline: {b['pain_scale_0_10']}/10")
        if b.get("key_rom_summary"):
            lines.append(f"  ROM summary: {b['key_rom_summary']}")
        for fm in b.get("functional_measures") or []:
            if fm.get("label"):
                lines.append(
                    f"  {fm['label']}: {fm.get('value', '—')}"
                    + (f" {fm['unit']}" if fm.get("unit") else "")
                )
    if exam and exam.get("date_of_service"):
        lines.append(f"  Initial exam: {exam['date_of_service']}")
    if not plan and not exam:
        lines.append("  (no baseline captured at re-exam creation)")
    lines.append("")

    # Current findings (optional)
    cf = reexam.get("current_findings") or {}
    if cf:
        lines.append("UPDATED OBJECTIVE FINDINGS")
        lines.append("--------------------------")
        v = cf.get("vitals") or {}
        for k, lbl in [("blood_pressure", "BP"), ("pulse_bpm", "Pulse")]:
            if v.get(k) not in (None, ""):
                lines.append(f"  {lbl}: {v[k]}")
        for k in ("observation_inspection", "palpation_findings", "gait", "posture",
                  "segmental_spinal_findings", "neurologic_findings",
                  "sensory_reflex_findings"):
            if cf.get(k):
                lines.append(f"  {k.replace('_', ' ').title()}: {cf[k]}")
        lines.append("")

    # Goal progress
    gp = reexam.get("goal_progress") or []
    if gp:
        lines.append("GOAL PROGRESS")
        lines.append("-------------")
        gmap = {g.get("id"): g for g in (plan.get("goals") or [])}
        for entry in gp:
            g = gmap.get(entry["goal_id"]) or {}
            bits = [g.get("description") or entry["goal_id"]]
            if g.get("baseline_value") not in (None, ""):
                bits.append(f"baseline: {g['baseline_value']}{(' ' + g['unit']) if g.get('unit') else ''}")
            if entry.get("current_value") not in (None, ""):
                bits.append(f"current: {entry['current_value']}")
            if g.get("target_value") not in (None, ""):
                bits.append(f"target: {g['target_value']}")
            bits.append(f"status: {entry['status'].replace('_', ' ')}")
            if entry.get("note"):
                bits.append(f"note: {entry['note']}")
            lines.append("  - " + " · ".join(str(x) for x in bits))
        lines.append("")

    # Outcome updates
    ou = reexam.get("outcome_updates") or []
    if ou:
        lines.append("OUTCOME MEASURES")
        lines.append("----------------")
        for o in ou:
            bits = [f"{o.get('label')} ({o.get('measure_type')})"]
            if o.get("score") is not None:
                mx = o.get("max_score")
                bits.append(f"score: {o['score']}{'/' + str(mx) if mx is not None else ''}")
            if o.get("note"):
                bits.append(o["note"])
            lines.append("  - " + " · ".join(str(x) for x in bits))
        lines.append("")

    # Recommendation
    if reexam.get("recommendation_decision"):
        lines.append("RECOMMENDATION")
        lines.append("--------------")
        lines.append(f"  Decision: {reexam['recommendation_decision'].replace('_', ' ')}")
        if reexam.get("recommendation_reason"):
            lines.append(f"  Reason: {reexam['recommendation_reason']}")
        if reexam.get("revised_plan_summary"):
            lines.append(f"  Revised plan summary: {reexam['revised_plan_summary']}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


@router.get(
    "/{patient_id}/clinical/re-exams/{rid}/narrative",
    response_model=ReExamNarrative,
)
async def get_reexam_narrative(
    patient_id: str,
    rid: str,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    reexam = await _load_reexam(db, patient_id, rid, ctx)

    patient = await db.patients.find_one(
        {"id": patient_id, "tenant_id": ctx.tenant_id},
        {"_id": 0, "first_name": 1, "last_name": 1},
    )
    patient_name = "Unknown patient"
    if patient:
        patient_name = f"{patient.get('first_name', '')} {patient.get('last_name', '')}".strip() or "Unknown patient"

    provider_name = None
    if reexam.get("provider_id"):
        prov = await db.users.find_one(
            {"id": reexam["provider_id"], "tenant_id": ctx.tenant_id},
            {"_id": 0, "name": 1, "email": 1},
        )
        if prov:
            provider_name = prov.get("name") or prov.get("email")

    plan_title = (
        (reexam.get("baseline_snapshot") or {}).get("plan") or {}
    ).get("title")

    narrative = _render_reexam_narrative(
        reexam, patient_name=patient_name,
        provider_name=provider_name, plan_title=plan_title,
    )
    await audit_success(
        user, "clinical.re_exam.narrative_viewed", request,
        entity_type="clinical_reexam", entity_id=rid, phi_accessed=True,
        metadata={"patient_id": patient_id},
    )
    return {
        "reexam_id": rid, "patient_id": patient_id,
        "narrative": narrative, "generated_at": now_iso(),
    }
