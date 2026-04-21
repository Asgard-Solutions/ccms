"""Initial Exam router — Phase 4.

Endpoints (all mounted under `/api` via server.py):
    GET    /api/clinical/exam-templates/default
    GET    /api/patients/{pid}/clinical/exams
    POST   /api/patients/{pid}/clinical/exams              (from encounter)
    GET    /api/patients/{pid}/clinical/exams/{eid}
    PATCH  /api/patients/{pid}/clinical/exams/{eid}
    POST   /api/patients/{pid}/clinical/exams/{eid}/prefill
    POST   /api/patients/{pid}/clinical/exams/{eid}/mark-sign-ready
    POST   /api/patients/{pid}/clinical/exams/{eid}/unmark-sign-ready
    POST   /api/patients/{pid}/clinical/exams/{eid}/sign
    GET    /api/patients/{pid}/clinical/exams/{eid}/narrative

One Initial Exam per encounter. Create returns the existing exam (with
`existed=true` via HTTP 200) instead of failing when the encounter is already
linked. Signed exams are immutable in Phase 4 (no amendments yet).

Narrative rendering is Initial-Exam-oriented (not SOAP-framed) per the
Phase-4 guardrails.
"""
from __future__ import annotations

import copy
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from core.audit import audit_success
from core.db import get_db_write
from core.deps import require_role
from core.reauth import require_reauth
from core.tenancy import TenantContext, get_tenant_context
from core.tenant_scope import scoped_filter, stamp_for_write
from services.clinical.exam_template import (
    DEFAULT_INITIAL_EXAM_TEMPLATE,
    DEFAULT_TEMPLATE_ID,
)
from services.clinical.exams_models import (
    InitialExamCreate,
    InitialExamNarrative,
    InitialExamPrefillRequest,
    InitialExamPublic,
    InitialExamUpdate,
)
from services.clinical.models import now_iso
from services.clinical.router import _load_patient, _log_clinical_event

router = APIRouter(prefix="", tags=["clinical"])


# ---------------------------------------------------------------------------
# Template endpoint
# ---------------------------------------------------------------------------
@router.get("/clinical/exam-templates/default")
async def get_default_exam_template(
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    _ = ctx  # tenant-agnostic; same system default for every tenant in Phase 4
    _ = user
    return DEFAULT_INITIAL_EXAM_TEMPLATE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _strip(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k not in {"_id", "history_log"}}


async def _load_exam(db, patient_id: str, exam_id: str, ctx: TenantContext) -> dict:
    q = scoped_filter(
        {"id": exam_id, "patient_id": patient_id}, ctx, location_scoped=False,
    )
    if q.get("__deny__"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exam not found")
    doc = await db.clinical_initial_exams.find_one(q, {"_id": 0})
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exam not found")
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
        "parent_type": "initial_exam",
        "parent_id": out.get("id"),
    })
    out["addendum_count"] = addendum_cnt
    out["has_addenda"] = addendum_cnt > 0
    if addendum_cnt:
        latest = await db.clinical_addenda.find_one(
            {
                "tenant_id": tenant_id,
                "parent_type": "initial_exam",
                "parent_id": out.get("id"),
            },
            {"_id": 0, "created_at": 1},
            sort=[("created_at", -1)],
        )
        out["latest_addendum_at"] = latest.get("created_at") if latest else None
    return out


def _merge_prefill(exam: dict, history_doc: dict | None, *, only_empty: bool = True) -> tuple[dict, list[str]]:
    """Prefill `exam['history']` from a `clinical_history` doc. Returns the
    mutated exam + list of fields actually filled (so the API can tell the
    UI what changed). Non-destructive: only empty fields get filled.
    """
    filled: list[str] = []
    if not history_doc:
        return exam, filled
    current = dict(exam.get("history") or {})
    for field in DEFAULT_INITIAL_EXAM_TEMPLATE["sections"][0]["fields"]:
        key = field.get("key")
        prefill_key = field.get("prefill_key")
        if not prefill_key:
            continue
        value = history_doc.get(prefill_key)
        if value is None or value == "" or value == []:
            continue
        if only_empty and (current.get(key) not in (None, "", [])):
            continue
        # Lists in clinical_history (e.g. pain_locations) become comma text.
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value)
        current[key] = value
        filled.append(key)
    exam["history"] = current
    return exam, filled


async def _active_diagnosis_ids(db, tenant_id: str, patient_id: str) -> list[str]:
    cursor = db.clinical_diagnoses.find(
        {"tenant_id": tenant_id, "patient_id": patient_id, "status": "active"},
        {"_id": 0, "id": 1},
    )
    return [d["id"] async for d in cursor]


async def _validate_diagnosis_ids(
    db, ctx: TenantContext, patient_id: str, ids: list[str],
) -> None:
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


# ---------------------------------------------------------------------------
# Create from encounter
# ---------------------------------------------------------------------------
@router.post(
    "/patients/{patient_id}/clinical/exams",
    response_model=InitialExamPublic,
)
async def create_exam_from_encounter(
    patient_id: str,
    payload: InitialExamCreate,
    request: Request,
    response: Response,
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)

    db = get_db_write()
    await _load_patient(db, patient_id, ctx)

    # Encounter must exist, belong to this tenant+patient, and not be cancelled.
    encounter = await db.clinical_encounters.find_one(
        {
            "tenant_id": ctx.tenant_id,
            "patient_id": patient_id,
            "id": payload.encounter_id,
        },
        {"_id": 0},
    )
    if not encounter:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Encounter not found")
    if encounter["status"] == "cancelled":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Cannot start an Initial Exam on a cancelled encounter",
        )

    # One-exam-per-encounter guard: return the existing exam idempotently.
    existing = await db.clinical_initial_exams.find_one(
        {"tenant_id": ctx.tenant_id, "encounter_id": payload.encounter_id},
        {"_id": 0},
    )
    if existing:
        hydrated = await _hydrate(db, ctx.tenant_id, existing)
        response.status_code = 200
        response.headers["X-Exam-Existed"] = "true"
        return hydrated

    # Resolve the template. Phase 4 ships only the system default.
    template_id = payload.template_id or DEFAULT_TEMPLATE_ID
    if template_id != DEFAULT_TEMPLATE_ID:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Only the default template is supported in this phase",
        )
    template_snapshot = copy.deepcopy(DEFAULT_INITIAL_EXAM_TEMPLATE)

    now = now_iso()
    exam_id = str(uuid.uuid4())
    exam = {
        "id": exam_id,
        "location_id": encounter.get("location_id"),
        "patient_id": patient_id,
        "encounter_id": payload.encounter_id,
        "appointment_id": encounter.get("appointment_id"),
        "provider_id": encounter.get("provider_id"),
        "episode_id": encounter.get("episode_id"),
        "date_of_service": encounter.get("date_of_service") or now,
        "status": "draft",
        "template_id": template_id,
        "template_snapshot": template_snapshot,
        "history": {},
        "examination": {},
        "assessment": {},
        "diagnosis_ids": [],
        "new_diagnoses": [],
        "materialized_diagnosis_ids": [],
        "prefilled_from_chart_at": None,
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

    # Prefill from clinical_history + active diagnoses if asked.
    filled_fields: list[str] = []
    if payload.prefill_from_chart:
        hist_doc = await db.clinical_history.find_one(
            {"tenant_id": ctx.tenant_id, "patient_id": patient_id}, {"_id": 0},
        )
        exam, filled_fields = _merge_prefill(exam, hist_doc, only_empty=True)
        exam["diagnosis_ids"] = await _active_diagnosis_ids(db, ctx.tenant_id, patient_id)
        exam["prefilled_from_chart_at"] = now

    exam = stamp_for_write(exam, ctx, location_id=encounter.get("location_id"))
    await db.clinical_initial_exams.insert_one(exam)

    await _log_clinical_event(
        db, ctx,
        actor=user, patient_id=patient_id, episode_id=exam.get("episode_id"),
        event_type="initial_exam.created", entity_type="initial_exam",
        entity_id=exam_id,
        metadata={
            "encounter_id": payload.encounter_id,
            "prefilled": bool(payload.prefill_from_chart),
            "prefilled_fields": filled_fields,
        },
    )
    await audit_success(
        user, "clinical.initial_exam.created", request,
        entity_type="clinical_initial_exam", entity_id=exam_id, phi_accessed=True,
        metadata={
            "patient_id": patient_id,
            "encounter_id": payload.encounter_id,
            "prefilled_fields": filled_fields,
        },
    )
    response.status_code = 201
    return await _hydrate(db, ctx.tenant_id, exam)


# ---------------------------------------------------------------------------
# List + read
# ---------------------------------------------------------------------------
@router.get(
    "/patients/{patient_id}/clinical/exams",
    response_model=list[InitialExamPublic],
)
async def list_exams(
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
        statuses = [s.strip() for s in status_in.split(",") if s.strip()]
        if statuses:
            q["status"] = {"$in": statuses}
    cursor = db.clinical_initial_exams.find(q, {"_id": 0}).sort("date_of_service", -1)
    rows = [d async for d in cursor]
    hydrated = [await _hydrate(db, ctx.tenant_id, d) for d in rows]
    await audit_success(
        user, "clinical.initial_exam.list_viewed", request,
        entity_type="patient", entity_id=patient_id, phi_accessed=True,
        metadata={"count": len(rows)},
    )
    return hydrated


@router.get(
    "/patients/{patient_id}/clinical/exams/{exam_id}",
    response_model=InitialExamPublic,
)
async def get_exam(
    patient_id: str,
    exam_id: str,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    doc = await _load_exam(db, patient_id, exam_id, ctx)
    await audit_success(
        user, "clinical.initial_exam.read", request,
        entity_type="clinical_initial_exam", entity_id=exam_id, phi_accessed=True,
        metadata={"patient_id": patient_id},
    )
    return await _hydrate(db, ctx.tenant_id, doc)


# ---------------------------------------------------------------------------
# PATCH
# ---------------------------------------------------------------------------
@router.patch(
    "/patients/{patient_id}/clinical/exams/{exam_id}",
    response_model=InitialExamPublic,
)
async def patch_exam(
    patient_id: str,
    exam_id: str,
    payload: InitialExamUpdate,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    current = await _load_exam(db, patient_id, exam_id, ctx)
    if current["status"] == "signed":
        raise HTTPException(status.HTTP_409_CONFLICT, "Signed exams are immutable")

    dumped = payload.model_dump(exclude_unset=True)
    if not dumped:
        return await _hydrate(db, ctx.tenant_id, current)

    now = now_iso()
    sets: dict = {}
    for key in ("history", "examination", "assessment"):
        if key in dumped and dumped[key] is not None:
            sets[key] = dumped[key]

    if "diagnosis_ids" in dumped:
        ids = list(dict.fromkeys(dumped["diagnosis_ids"] or []))
        await _validate_diagnosis_ids(db, ctx, patient_id, ids)
        sets["diagnosis_ids"] = ids

    if "new_diagnoses" in dumped:
        sets["new_diagnoses"] = dumped["new_diagnoses"] or []

    sets["updated_at"] = now
    sets["updated_by"] = user["id"]

    fields_changed = sorted(k for k in dumped.keys())
    await db.clinical_initial_exams.update_one(
        {"id": exam_id, "tenant_id": current["tenant_id"]},
        {
            "$set": sets,
            "$push": {
                "history_log": {
                    "at": now, "by": user["id"], "action": "updated",
                    "fields": fields_changed,
                }
            },
        },
    )
    fresh = await db.clinical_initial_exams.find_one(
        {"id": exam_id, "tenant_id": current["tenant_id"]}, {"_id": 0}
    )
    await _log_clinical_event(
        db, ctx,
        actor=user, patient_id=patient_id, episode_id=fresh.get("episode_id"),
        event_type="initial_exam.updated", entity_type="initial_exam", entity_id=exam_id,
        metadata={"fields": fields_changed},
    )
    await audit_success(
        user, "clinical.initial_exam.updated", request,
        entity_type="clinical_initial_exam", entity_id=exam_id, phi_accessed=True,
        metadata={"patient_id": patient_id, "fields": fields_changed},
    )
    return await _hydrate(db, ctx.tenant_id, fresh)


# ---------------------------------------------------------------------------
# Prefill (explicit, non-destructive)
# ---------------------------------------------------------------------------
@router.post(
    "/patients/{patient_id}/clinical/exams/{exam_id}/prefill",
    response_model=InitialExamPublic,
)
async def prefill_exam(
    patient_id: str,
    exam_id: str,
    request: Request,
    payload: InitialExamPrefillRequest | None = None,
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    current = await _load_exam(db, patient_id, exam_id, ctx)
    if current["status"] == "signed":
        raise HTTPException(status.HTTP_409_CONFLICT, "Signed exams are immutable")

    sections = set((payload.sections if payload else None) or ["history", "diagnoses"])
    filled: list[str] = []
    sets: dict = {}
    now = now_iso()

    if "history" in sections:
        hist_doc = await db.clinical_history.find_one(
            {"tenant_id": ctx.tenant_id, "patient_id": patient_id}, {"_id": 0},
        )
        working = dict(current)
        working, filled = _merge_prefill(working, hist_doc, only_empty=True)
        if filled:
            sets["history"] = working["history"]

    if "diagnoses" in sections:
        active_ids = await _active_diagnosis_ids(db, ctx.tenant_id, patient_id)
        existing = set(current.get("diagnosis_ids") or [])
        merged = list(existing | set(active_ids))
        if set(merged) != existing:
            sets["diagnosis_ids"] = merged

    if not sets:
        return await _hydrate(db, ctx.tenant_id, current)

    sets["prefilled_from_chart_at"] = now
    sets["updated_at"] = now
    sets["updated_by"] = user["id"]

    await db.clinical_initial_exams.update_one(
        {"id": exam_id, "tenant_id": current["tenant_id"]},
        {
            "$set": sets,
            "$push": {
                "history_log": {
                    "at": now, "by": user["id"], "action": "prefilled",
                    "filled_fields": filled,
                }
            },
        },
    )
    fresh = await db.clinical_initial_exams.find_one(
        {"id": exam_id, "tenant_id": current["tenant_id"]}, {"_id": 0}
    )
    await _log_clinical_event(
        db, ctx,
        actor=user, patient_id=patient_id, episode_id=fresh.get("episode_id"),
        event_type="initial_exam.prefilled", entity_type="initial_exam", entity_id=exam_id,
        metadata={"filled_fields": filled},
    )
    await audit_success(
        user, "clinical.initial_exam.prefilled", request,
        entity_type="clinical_initial_exam", entity_id=exam_id, phi_accessed=True,
        metadata={"patient_id": patient_id, "filled_fields": filled},
    )
    return await _hydrate(db, ctx.tenant_id, fresh)


# ---------------------------------------------------------------------------
# Sign-ready transitions
# ---------------------------------------------------------------------------
@router.post(
    "/patients/{patient_id}/clinical/exams/{exam_id}/mark-sign-ready",
    response_model=InitialExamPublic,
)
async def mark_sign_ready(
    patient_id: str,
    exam_id: str,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    current = await _load_exam(db, patient_id, exam_id, ctx)
    if current["status"] != "draft":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Only drafts can be marked sign-ready (current status = {current['status']})",
        )

    now = now_iso()
    await db.clinical_initial_exams.update_one(
        {"id": exam_id, "tenant_id": current["tenant_id"]},
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
    fresh = await db.clinical_initial_exams.find_one(
        {"id": exam_id, "tenant_id": current["tenant_id"]}, {"_id": 0}
    )
    await audit_success(
        user, "clinical.initial_exam.mark_sign_ready", request,
        entity_type="clinical_initial_exam", entity_id=exam_id, phi_accessed=True,
    )
    return await _hydrate(db, ctx.tenant_id, fresh)


@router.post(
    "/patients/{patient_id}/clinical/exams/{exam_id}/unmark-sign-ready",
    response_model=InitialExamPublic,
)
async def unmark_sign_ready(
    patient_id: str,
    exam_id: str,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    current = await _load_exam(db, patient_id, exam_id, ctx)
    if current["status"] != "sign_ready":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Only sign_ready exams can be unmarked (current status = {current['status']})",
        )

    now = now_iso()
    await db.clinical_initial_exams.update_one(
        {"id": exam_id, "tenant_id": current["tenant_id"]},
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
    fresh = await db.clinical_initial_exams.find_one(
        {"id": exam_id, "tenant_id": current["tenant_id"]}, {"_id": 0}
    )
    return await _hydrate(db, ctx.tenant_id, fresh)


# ---------------------------------------------------------------------------
# Sign (terminal; materialize new_diagnoses)
# ---------------------------------------------------------------------------
@router.post(
    "/patients/{patient_id}/clinical/exams/{exam_id}/sign",
    response_model=InitialExamPublic,
)
async def sign_exam(
    patient_id: str,
    exam_id: str,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    current = await _load_exam(db, patient_id, exam_id, ctx)
    if current["status"] == "signed":
        raise HTTPException(status.HTTP_409_CONFLICT, "Exam is already signed")
    if current["status"] not in ("draft", "sign_ready"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Cannot sign from status = {current['status']}",
        )

    await _validate_diagnosis_ids(db, ctx, patient_id, current.get("diagnosis_ids") or [])

    now = now_iso()
    materialized_ids: list[str] = []
    materialized_new_diagnoses: list[dict] = []

    # Materialize new_diagnoses into clinical_diagnoses. De-duplicate against
    # the patient's existing ACTIVE problem list by (icd10_code, body_region,
    # laterality). Enforce one-primary-per-grouping semantics at the end.
    existing_active = [
        d async for d in db.clinical_diagnoses.find(
            {"tenant_id": ctx.tenant_id, "patient_id": patient_id, "status": "active"},
            {"_id": 0},
        )
    ]

    def _key(code: str, region: str | None, lat: str | None) -> tuple:
        return ((code or "").upper(), (region or "").lower(), (lat or "").lower())

    existing_keys = {
        _key(d.get("icd10_code"), d.get("body_region"), d.get("laterality")): d["id"]
        for d in existing_active
    }

    for draft in current.get("new_diagnoses") or []:
        code = (draft.get("icd10_code") or "").strip().upper()
        label = (draft.get("label") or "").strip()
        if not code or not label:
            continue
        k = _key(code, draft.get("body_region"), draft.get("laterality"))
        if k in existing_keys:
            # Silent de-dup — point to the already-active diagnosis.
            materialized_ids.append(existing_keys[k])
            continue
        dx_id = str(uuid.uuid4())
        doc = {
            "id": dx_id,
            "patient_id": patient_id,
            "episode_id": current.get("episode_id"),
            "icd10_code": code,
            "label": label,
            "status": "active",
            "is_primary": bool(draft.get("is_primary")),
            "body_region": draft.get("body_region"),
            "laterality": draft.get("laterality"),
            "chronicity": draft.get("chronicity"),
            "onset_date": current.get("date_of_service") and current["date_of_service"][:10],
            "resolved_date": None,
            "resolution_notes": None,
            "notes": None,
            "created_at": now,
            "updated_at": now,
            "created_by": user["id"],
            "updated_by": user["id"],
        }
        doc = stamp_for_write(doc, ctx)
        await db.clinical_diagnoses.insert_one(doc)
        materialized_ids.append(dx_id)
        materialized_new_diagnoses.append(
            {"id": dx_id, "icd10_code": code, "label": label}
        )

    # Enforce one-primary-per-episode-grouping across both existing and new
    # active diagnoses. If multiple are primary within the same grouping,
    # keep only the most-recent one (last wins, which matches what the
    # provider signed off on).
    final_diagnosis_ids = list(dict.fromkeys(
        (current.get("diagnosis_ids") or []) + materialized_ids
    ))
    if final_diagnosis_ids:
        all_rows = [
            d async for d in db.clinical_diagnoses.find(
                {
                    "tenant_id": ctx.tenant_id,
                    "patient_id": patient_id,
                    "id": {"$in": final_diagnosis_ids},
                    "status": "active",
                },
                {"_id": 0},
            )
        ]
        # Group by (episode_id-or-None); within each group, collect primaries
        # sorted by created_at desc.
        groups: dict = {}
        for r in all_rows:
            groups.setdefault(r.get("episode_id"), []).append(r)
        for eid, rows in groups.items():
            primaries = [r for r in rows if r.get("is_primary")]
            if len(primaries) <= 1:
                continue
            primaries.sort(key=lambda r: r.get("created_at") or "", reverse=True)
            keep = primaries[0]["id"]
            to_demote = [r["id"] for r in primaries if r["id"] != keep]
            await db.clinical_diagnoses.update_many(
                {
                    "tenant_id": ctx.tenant_id,
                    "patient_id": patient_id,
                    "id": {"$in": to_demote},
                },
                {"$set": {"is_primary": False}},
            )

    await db.clinical_initial_exams.update_one(
        {"id": exam_id, "tenant_id": current["tenant_id"]},
        {
            "$set": {
                "status": "signed",
                "signed_at": now,
                "signed_by": user["id"],
                "updated_at": now,
                "updated_by": user["id"],
                "materialized_diagnosis_ids": materialized_ids,
                "diagnosis_ids": final_diagnosis_ids,
            },
            "$push": {
                "history_log": {
                    "at": now, "by": user["id"], "action": "signed",
                    "materialized": [m["id"] for m in materialized_new_diagnoses],
                }
            },
        },
    )
    fresh = await db.clinical_initial_exams.find_one(
        {"id": exam_id, "tenant_id": current["tenant_id"]}, {"_id": 0}
    )

    await _log_clinical_event(
        db, ctx,
        actor=user, patient_id=patient_id, episode_id=fresh.get("episode_id"),
        event_type="initial_exam.signed", entity_type="initial_exam", entity_id=exam_id,
        metadata={
            "materialized_diagnoses": [m["id"] for m in materialized_new_diagnoses],
        },
    )
    await audit_success(
        user, "clinical.initial_exam.signed", request,
        entity_type="clinical_initial_exam", entity_id=exam_id, phi_accessed=True,
        metadata={"patient_id": patient_id, "materialized_count": len(materialized_ids)},
    )
    return await _hydrate(db, ctx.tenant_id, fresh)


# ---------------------------------------------------------------------------
# Narrative rendering (Initial-Exam oriented)
# ---------------------------------------------------------------------------
def _render_narrative(exam: dict, *, patient_name: str, provider_name: str | None,
                     diagnoses: list[dict]) -> str:
    """Produce a human-readable, print-friendly narrative. Structured
    subsections render inline, empty fields are omitted to keep the output
    tight."""
    lines: list[str] = []
    lines.append("INITIAL EXAMINATION")
    lines.append("=" * 60)
    lines.append(f"Patient: {patient_name}")
    lines.append(f"Date of service: {exam.get('date_of_service', '')}")
    if provider_name:
        lines.append(f"Provider: {provider_name}")
    lines.append(f"Status: {exam.get('status')}")
    if exam.get("signed_at"):
        lines.append(f"Signed: {exam['signed_at']}")
    lines.append("")

    def add_section(title: str, body: dict | None, keys: list[tuple[str, str]]):
        body = body or {}
        rows = []
        for key, label in keys:
            v = body.get(key)
            if v in (None, "", []):
                continue
            rows.append((label, v))
        if not rows:
            return
        lines.append(title.upper())
        lines.append("-" * len(title))
        for label, v in rows:
            if isinstance(v, str):
                lines.append(f"{label}: {v}")
            else:
                lines.append(f"{label}:")
                lines.append(f"  {v}")
        lines.append("")

    add_section("History", exam.get("history"), [
        ("chief_complaint", "Chief complaint"),
        ("history_of_present_illness", "History of present illness"),
        ("onset_mechanism", "Onset / mechanism"),
        ("medications", "Medications"),
        ("allergies", "Allergies"),
        ("past_medical_history", "Past medical history"),
        ("past_surgical_history", "Past surgical history"),
        ("family_history", "Family history"),
        ("social_history", "Social history"),
        ("occupation_activity", "Occupation / activity"),
        ("review_of_systems", "Review of systems"),
    ])

    # Examination — vitals first, then narrative fields, then structured
    # subsections (ROM, orthopedic tests, muscle strength).
    exam_body = exam.get("examination") or {}
    v = exam_body.get("vitals") or {}
    vitals_rows = []
    mapping = [
        ("blood_pressure", "BP"), ("pulse_bpm", "Pulse"),
        ("respiratory_rate", "RR"), ("temperature_f", "Temp (F)"),
        ("height_in", "Ht (in)"), ("weight_lb", "Wt (lb)"),
        ("o2_sat_pct", "O2 sat (%)"),
    ]
    for k, lbl in mapping:
        if v.get(k) not in (None, ""):
            vitals_rows.append(f"{lbl}: {v[k]}")
    if vitals_rows:
        lines.append("EXAMINATION")
        lines.append("-----------")
        lines.append("Vitals: " + " · ".join(vitals_rows))
    else:
        lines.append("EXAMINATION")
        lines.append("-----------")

    def _add_field(key: str, label: str):
        val = exam_body.get(key)
        if val:
            lines.append(f"{label}: {val}")

    _add_field("observation_inspection", "Observation / inspection")
    _add_field("posture", "Posture")
    _add_field("gait", "Gait")
    _add_field("palpation_findings", "Palpation findings")
    _add_field("segmental_spinal_findings", "Segmental / spinal findings")

    rom = exam_body.get("range_of_motion") or {}
    rom_lines = []
    for region, block in rom.items():
        block = block or {}
        parts = []
        for k in ("flexion", "extension", "left_rotation", "right_rotation",
                  "left_lateral_flexion", "right_lateral_flexion"):
            if block.get(k):
                parts.append(f"{k.replace('_', ' ')} {block[k]}")
        if block.get("notes"):
            parts.append(f"notes: {block['notes']}")
        if parts:
            rom_lines.append(f"  {region.capitalize()}: " + "; ".join(parts))
    if rom_lines:
        lines.append("Range of motion:")
        lines.extend(rom_lines)

    ortho = exam_body.get("orthopedic_tests") or []
    if ortho:
        lines.append("Orthopedic tests:")
        for t in ortho:
            lines.append(
                f"  - {t.get('name', '?')}"
                + (f" ({t.get('region')})" if t.get("region") else "")
                + f" → {t.get('result', 'not recorded')}"
                + (f" — {t.get('notes')}" if t.get("notes") else ""),
            )

    _add_field("neurologic_findings", "Neurologic findings")

    strength = exam_body.get("muscle_strength") or []
    if strength:
        lines.append("Muscle strength:")
        for s in strength:
            side = f" ({s.get('side')})" if s.get("side") else ""
            lines.append(
                f"  - {s.get('muscle', '?')}{side}: "
                + (f"{s.get('grade')}/5" if s.get("grade") is not None else "not recorded")
                + (f" — {s.get('notes')}" if s.get("notes") else "")
            )

    _add_field("sensory_reflex_findings", "Sensory / reflex findings")
    lines.append("")

    add_section("Assessment & Plan", exam.get("assessment"), [
        ("functional_limitations", "Functional limitations"),
        ("assessment_summary", "Assessment summary"),
        ("initial_clinical_impression", "Initial clinical impression"),
        ("treatment_recommendations", "Treatment recommendations"),
    ])

    if diagnoses:
        lines.append("DIAGNOSES")
        lines.append("---------")
        for dx in diagnoses:
            pri = " [PRIMARY]" if dx.get("is_primary") else ""
            status_s = dx.get("status") or ""
            lines.append(
                f"  - {dx.get('icd10_code')} · {dx.get('label')}{pri}"
                + (f" ({status_s})" if status_s else "")
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


@router.get(
    "/patients/{patient_id}/clinical/exams/{exam_id}/narrative",
    response_model=InitialExamNarrative,
)
async def get_narrative(
    patient_id: str,
    exam_id: str,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    exam = await _load_exam(db, patient_id, exam_id, ctx)

    # Patient name (use masked/first+last lookup)
    patient = await db.patients.find_one(
        {"id": patient_id, "tenant_id": ctx.tenant_id},
        {"_id": 0, "first_name": 1, "last_name": 1},
    )
    patient_name = "Unknown patient"
    if patient:
        patient_name = f"{patient.get('first_name', '')} {patient.get('last_name', '')}".strip() or "Unknown patient"

    provider_name = None
    if exam.get("provider_id"):
        prov = await db.users.find_one(
            {"id": exam["provider_id"], "tenant_id": ctx.tenant_id},
            {"_id": 0, "name": 1, "email": 1},
        )
        if prov:
            provider_name = prov.get("name") or prov.get("email")

    dx_rows: list[dict] = []
    ids = list(exam.get("diagnosis_ids") or [])
    if ids:
        dx_rows = [
            d async for d in db.clinical_diagnoses.find(
                {"tenant_id": ctx.tenant_id, "patient_id": patient_id, "id": {"$in": ids}},
                {"_id": 0},
            )
        ]

    narrative = _render_narrative(
        exam, patient_name=patient_name, provider_name=provider_name,
        diagnoses=dx_rows,
    )

    await audit_success(
        user, "clinical.initial_exam.narrative_viewed", request,
        entity_type="clinical_initial_exam", entity_id=exam_id, phi_accessed=True,
        metadata={"patient_id": patient_id},
    )
    return {
        "exam_id": exam_id,
        "patient_id": patient_id,
        "narrative": narrative,
        "generated_at": now_iso(),
    }
