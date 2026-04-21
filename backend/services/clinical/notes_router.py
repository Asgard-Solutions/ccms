"""Follow-up / Daily Visit Note router — Phase 5.

Endpoints (all mounted under `/api` via server.py):
    GET    /api/patients/{pid}/clinical/notes
    POST   /api/patients/{pid}/clinical/notes                 (from encounter)
    GET    /api/patients/{pid}/clinical/notes/{nid}
    PATCH  /api/patients/{pid}/clinical/notes/{nid}
    POST   /api/patients/{pid}/clinical/notes/{nid}/copy-forward
    POST   /api/patients/{pid}/clinical/notes/{nid}/mark-sign-ready
    POST   /api/patients/{pid}/clinical/notes/{nid}/unmark-sign-ready
    POST   /api/patients/{pid}/clinical/notes/{nid}/sign
    GET    /api/patients/{pid}/clinical/notes/{nid}/narrative
    POST   /api/appointments/{aid}/clinical/notes             (convenience)
    GET    /api/patients/{pid}/clinical/care-timeline

One note per encounter (non-cancelled). POST returns 200 +
`X-Note-Existed: true` header if an exam already exists. Signed notes are
immutable in Phase 5. Copy-forward is non-destructive by default — only
empty destination fields are overwritten unless `force=true`.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from core.audit import audit_success
from core.db import get_db_write
from core.deps import require_role
from core.reauth import require_reauth
from core.tenancy import TenantContext, get_tenant_context
from core.tenant_scope import scoped_filter, stamp_for_write
from services.clinical.models import now_iso
from services.clinical.notes_models import (
    REQUIRED_FIELDS,
    CareTimelineResponse,
    CopyForwardRequest,
    FollowUpNoteCreate,
    FollowUpNoteNarrative,
    FollowUpNotePublic,
    FollowUpNoteUpdate,
)
from services.clinical.router import _load_patient, _log_clinical_event

# Patient-owned authoritative surface
patient_router = APIRouter(prefix="/patients", tags=["clinical"])

# Convenience: launch from appointment
appt_router = APIRouter(prefix="/appointments", tags=["clinical"])# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
SECTION_KEYS = ("subjective", "objective", "assessment", "plan")


def _strip(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k not in {"_id", "history_log"}}


def _field_filled(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, dict)):
        return len(value) > 0
    return True


def _get_by_dotpath(doc: dict, path: str) -> Any:
    parts = path.split(".")
    cur: Any = doc
    for p in parts:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def _compute_completeness(doc: dict) -> dict:
    missing: list[str] = []
    for field in REQUIRED_FIELDS:
        if not _field_filled(_get_by_dotpath(doc, field)):
            missing.append(field)
    filled = len(REQUIRED_FIELDS) - len(missing)
    score = round((filled / len(REQUIRED_FIELDS)) * 100) if REQUIRED_FIELDS else 100
    return {
        "score": score,
        "filled": filled,
        "total": len(REQUIRED_FIELDS),
        "missing_fields": missing,
    }


async def _load_note(db, patient_id: str, note_id: str, ctx: TenantContext) -> dict:
    q = scoped_filter(
        {"id": note_id, "patient_id": patient_id}, ctx, location_scoped=False,
    )
    if q.get("__deny__"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Note not found")
    doc = await db.clinical_follow_up_notes.find_one(q, {"_id": 0})
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Note not found")
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
    out["completeness"] = _compute_completeness(out)
    # Phase 6 — surface the active plan summary read-only on GETs.
    plan_doc = await db.clinical_treatment_plans.find_one(
        {
            "tenant_id": tenant_id, "patient_id": out["patient_id"],
            "episode_id": out.get("episode_id"), "plan_status": "active",
        },
        {"_id": 0},
    )
    if plan_doc:
        # Progress computed on the fly
        visit_q: dict = {
            "tenant_id": tenant_id, "patient_id": out["patient_id"],
            "status": "signed",
        }
        if plan_doc.get("episode_id"):
            visit_q["episode_id"] = plan_doc["episode_id"]
        if plan_doc.get("start_date"):
            visit_q["date_of_service"] = {"$gte": plan_doc["start_date"]}
        visits = await db.clinical_follow_up_notes.count_documents(visit_q)
        total = plan_doc.get("frequency_total_visits")
        pct = min(100, round((visits / total) * 100)) if total else None
        out["active_plan_summary"] = {
            "id": plan_doc["id"],
            "title": plan_doc.get("title"),
            "plan_status": plan_doc.get("plan_status"),
            "frequency_visits_per_week": plan_doc.get("frequency_visits_per_week"),
            "frequency_total_visits": total,
            "expected_duration_weeks": plan_doc.get("expected_duration_weeks"),
            "re_exam_date": plan_doc.get("re_exam_date"),
            "goals": (plan_doc.get("goals") or [])[:3],
            "progress": {
                "visits_completed": visits,
                "total_visits": total,
                "percent": pct,
            },
        }
    else:
        out["active_plan_summary"] = None
    return out


def _merge_copy_forward(target: dict, source: dict, *, force: bool) -> list[str]:
    """Copy subjective/objective/assessment/plan fields from a signed source
    note into the target note. Returns the list of dot-path fields actually
    written. Non-destructive unless `force=True`.

    The rule we apply per-field:
      - If the source value is empty/None, skip.
      - If `force=False` and the target already has a non-empty value, skip.
      - Otherwise overwrite the target value with the source value.
    """
    copied: list[str] = []
    for section_key in SECTION_KEYS:
        src_section = source.get(section_key) or {}
        tgt_section = dict(target.get(section_key) or {})
        if not src_section:
            continue
        for k, v in src_section.items():
            if not _field_filled(v):
                continue
            if not force and _field_filled(tgt_section.get(k)):
                continue
            tgt_section[k] = v
            copied.append(f"{section_key}.{k}")
        target[section_key] = tgt_section
    return copied


async def _load_encounter(db, ctx: TenantContext, patient_id: str, encounter_id: str) -> dict:
    enc = await db.clinical_encounters.find_one(
        {
            "tenant_id": ctx.tenant_id,
            "patient_id": patient_id,
            "id": encounter_id,
        },
        {"_id": 0},
    )
    if not enc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Encounter not found")
    return enc


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------
async def _create_note_for_encounter(
    db,
    *,
    ctx: TenantContext,
    user: dict,
    patient_id: str,
    encounter: dict,
    copy_forward_from_note_id: str | None,
    request: Request,
    response: Response,
) -> dict:
    if encounter["status"] == "cancelled":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Cannot start a follow-up note on a cancelled encounter",
        )

    # One-note-per-encounter guard (idempotent).
    existing = await db.clinical_follow_up_notes.find_one(
        {"tenant_id": ctx.tenant_id, "encounter_id": encounter["id"]},
        {"_id": 0},
    )
    if existing:
        hydrated = await _hydrate(db, ctx.tenant_id, existing)
        response.status_code = 200
        response.headers["X-Note-Existed"] = "true"
        return hydrated

    now = now_iso()
    note_id = str(uuid.uuid4())
    doc = {
        "id": note_id,
        "location_id": encounter.get("location_id"),
        "patient_id": patient_id,
        "encounter_id": encounter["id"],
        "appointment_id": encounter.get("appointment_id"),
        "provider_id": encounter.get("provider_id"),
        "episode_id": encounter.get("episode_id"),
        "treatment_plan_id": None,
        "date_of_service": encounter.get("date_of_service") or now,
        "status": "draft",
        "visit_number": None,
        "subjective": {},
        "objective": {},
        "assessment": {},
        "plan": {},
        "copied_from_note_id": None,
        "copied_fields": [],
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

    copied_fields: list[str] = []
    if copy_forward_from_note_id:
        src = await db.clinical_follow_up_notes.find_one(
            {
                "tenant_id": ctx.tenant_id,
                "patient_id": patient_id,
                "id": copy_forward_from_note_id,
            },
            {"_id": 0},
        )
        if not src:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Source note not found on this patient",
            )
        if src.get("status") != "signed":
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Only signed notes can be copied forward",
            )
        copied_fields = _merge_copy_forward(doc, src, force=False)
        doc["copied_from_note_id"] = copy_forward_from_note_id
        doc["copied_fields"] = copied_fields

    doc = stamp_for_write(doc, ctx, location_id=encounter.get("location_id"))
    await db.clinical_follow_up_notes.insert_one(doc)

    await _log_clinical_event(
        db, ctx,
        actor=user, patient_id=patient_id, episode_id=doc.get("episode_id"),
        event_type="follow_up_note.created", entity_type="follow_up_note",
        entity_id=note_id,
        metadata={
            "encounter_id": encounter["id"],
            "copied_from_note_id": copy_forward_from_note_id,
            "copied_fields": copied_fields,
        },
    )
    await audit_success(
        user, "clinical.follow_up_note.created", request,
        entity_type="clinical_follow_up_note", entity_id=note_id, phi_accessed=True,
        metadata={
            "patient_id": patient_id,
            "encounter_id": encounter["id"],
            "copied_fields": copied_fields,
        },
    )
    response.status_code = 201
    return await _hydrate(db, ctx.tenant_id, doc)


@patient_router.post(
    "/{patient_id}/clinical/notes",
    response_model=FollowUpNotePublic,
)
async def create_follow_up_note(
    patient_id: str,
    payload: FollowUpNoteCreate,
    request: Request,
    response: Response,
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    encounter = await _load_encounter(db, ctx, patient_id, payload.encounter_id)
    return await _create_note_for_encounter(
        db,
        ctx=ctx, user=user, patient_id=patient_id, encounter=encounter,
        copy_forward_from_note_id=payload.copy_forward_from_note_id,
        request=request, response=response,
    )


# Convenience route: launch from appointment without resolving patient first.
@appt_router.post(
    "/{appointment_id}/clinical/notes",
    response_model=FollowUpNotePublic,
)
async def create_follow_up_note_from_appointment(
    appointment_id: str,
    request: Request,
    response: Response,
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
    copy_forward_from_note_id: str | None = Query(default=None),
):
    require_reauth(request, user)
    db = get_db_write()
    # Look up the encounter by appointment (idempotent reuse of the Phase 3 shell).
    enc = await db.clinical_encounters.find_one(
        {
            "tenant_id": ctx.tenant_id,
            "appointment_id": appointment_id,
            "status": {"$ne": "cancelled"},
        },
        {"_id": 0},
    )
    if not enc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "No non-cancelled encounter launched for this appointment",
        )
    await _load_patient(db, enc["patient_id"], ctx)
    return await _create_note_for_encounter(
        db,
        ctx=ctx, user=user, patient_id=enc["patient_id"], encounter=enc,
        copy_forward_from_note_id=copy_forward_from_note_id,
        request=request, response=response,
    )


# ---------------------------------------------------------------------------
# List + Read
# ---------------------------------------------------------------------------
@patient_router.get(
    "/{patient_id}/clinical/notes",
    response_model=list[FollowUpNotePublic],
)
async def list_follow_up_notes(
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
        statuses = [s.strip() for s in status_in.split(",") if s.strip()]
        if statuses:
            q["status"] = {"$in": statuses}
    if episode_id:
        q["episode_id"] = episode_id
    cursor = db.clinical_follow_up_notes.find(q, {"_id": 0}).sort("date_of_service", -1)
    rows = [d async for d in cursor]
    hydrated = [await _hydrate(db, ctx.tenant_id, d) for d in rows]
    await audit_success(
        user, "clinical.follow_up_note.list_viewed", request,
        entity_type="patient", entity_id=patient_id, phi_accessed=True,
        metadata={"count": len(rows)},
    )
    return hydrated


@patient_router.get(
    "/{patient_id}/clinical/notes/{note_id}",
    response_model=FollowUpNotePublic,
)
async def get_follow_up_note(
    patient_id: str,
    note_id: str,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    doc = await _load_note(db, patient_id, note_id, ctx)
    await audit_success(
        user, "clinical.follow_up_note.read", request,
        entity_type="clinical_follow_up_note", entity_id=note_id, phi_accessed=True,
        metadata={"patient_id": patient_id},
    )
    return await _hydrate(db, ctx.tenant_id, doc)


# ---------------------------------------------------------------------------
# PATCH
# ---------------------------------------------------------------------------
@patient_router.patch(
    "/{patient_id}/clinical/notes/{note_id}",
    response_model=FollowUpNotePublic,
)
async def patch_follow_up_note(
    patient_id: str,
    note_id: str,
    payload: FollowUpNoteUpdate,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    current = await _load_note(db, patient_id, note_id, ctx)
    if current["status"] == "signed":
        raise HTTPException(status.HTTP_409_CONFLICT, "Signed notes are immutable")

    dumped = payload.model_dump(exclude_unset=True)
    if not dumped:
        return await _hydrate(db, ctx.tenant_id, current)

    now = now_iso()
    sets: dict = {}
    for key in SECTION_KEYS:
        if key in dumped and dumped[key] is not None:
            sets[key] = dumped[key]
    if "treatment_plan_id" in dumped:
        sets["treatment_plan_id"] = dumped["treatment_plan_id"]

    sets["updated_at"] = now
    sets["updated_by"] = user["id"]
    fields_changed = sorted(k for k in dumped.keys())

    await db.clinical_follow_up_notes.update_one(
        {"id": note_id, "tenant_id": current["tenant_id"]},
        {
            "$set": sets,
            "$push": {
                "history_log": {
                    "at": now, "by": user["id"], "action": "updated",
                    "fields": fields_changed,
                },
            },
        },
    )
    fresh = await db.clinical_follow_up_notes.find_one(
        {"id": note_id, "tenant_id": current["tenant_id"]}, {"_id": 0}
    )
    await _log_clinical_event(
        db, ctx,
        actor=user, patient_id=patient_id, episode_id=fresh.get("episode_id"),
        event_type="follow_up_note.updated", entity_type="follow_up_note",
        entity_id=note_id, metadata={"fields": fields_changed},
    )
    await audit_success(
        user, "clinical.follow_up_note.updated", request,
        entity_type="clinical_follow_up_note", entity_id=note_id, phi_accessed=True,
        metadata={"patient_id": patient_id, "fields": fields_changed},
    )
    return await _hydrate(db, ctx.tenant_id, fresh)


# ---------------------------------------------------------------------------
# Copy-forward (explicit, non-destructive by default)
# ---------------------------------------------------------------------------
@patient_router.post(
    "/{patient_id}/clinical/notes/{note_id}/copy-forward",
    response_model=FollowUpNotePublic,
)
async def copy_forward(
    patient_id: str,
    note_id: str,
    payload: CopyForwardRequest,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    current = await _load_note(db, patient_id, note_id, ctx)
    if current["status"] == "signed":
        raise HTTPException(status.HTTP_409_CONFLICT, "Signed notes are immutable")

    source = await db.clinical_follow_up_notes.find_one(
        {
            "tenant_id": ctx.tenant_id,
            "patient_id": patient_id,
            "id": payload.source_note_id,
        },
        {"_id": 0},
    )
    if not source:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Source note not found on this patient",
        )
    if source.get("status") != "signed":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Only signed notes can be copied forward",
        )
    if source["id"] == note_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Cannot copy a note forward into itself",
        )

    working = dict(current)
    copied_fields = _merge_copy_forward(working, source, force=payload.force)

    if not copied_fields:
        return await _hydrate(db, ctx.tenant_id, current)

    now = now_iso()
    # Merge into existing copied_fields so history is preserved across
    # successive copy-forward operations.
    existing_copied = list(current.get("copied_fields") or [])
    combined = list(dict.fromkeys(existing_copied + copied_fields))
    sets = {
        "subjective": working["subjective"],
        "objective": working["objective"],
        "assessment": working["assessment"],
        "plan": working["plan"],
        "copied_from_note_id": payload.source_note_id,
        "copied_fields": combined,
        "updated_at": now,
        "updated_by": user["id"],
    }
    await db.clinical_follow_up_notes.update_one(
        {"id": note_id, "tenant_id": current["tenant_id"]},
        {
            "$set": sets,
            "$push": {
                "history_log": {
                    "at": now, "by": user["id"], "action": "copy_forward",
                    "from": payload.source_note_id, "fields": copied_fields,
                    "force": payload.force,
                },
            },
        },
    )
    fresh = await db.clinical_follow_up_notes.find_one(
        {"id": note_id, "tenant_id": current["tenant_id"]}, {"_id": 0}
    )
    await _log_clinical_event(
        db, ctx,
        actor=user, patient_id=patient_id, episode_id=fresh.get("episode_id"),
        event_type="follow_up_note.copy_forward", entity_type="follow_up_note",
        entity_id=note_id,
        metadata={"source_note_id": payload.source_note_id, "fields": copied_fields},
    )
    await audit_success(
        user, "clinical.follow_up_note.copy_forward", request,
        entity_type="clinical_follow_up_note", entity_id=note_id, phi_accessed=True,
        metadata={
            "patient_id": patient_id,
            "source_note_id": payload.source_note_id,
            "fields": copied_fields,
            "force": payload.force,
        },
    )
    return await _hydrate(db, ctx.tenant_id, fresh)


# ---------------------------------------------------------------------------
# Sign-ready transitions
# ---------------------------------------------------------------------------
@patient_router.post(
    "/{patient_id}/clinical/notes/{note_id}/mark-sign-ready",
    response_model=FollowUpNotePublic,
)
async def mark_sign_ready(
    patient_id: str,
    note_id: str,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    current = await _load_note(db, patient_id, note_id, ctx)
    if current["status"] != "draft":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Only drafts can be marked sign-ready (status = {current['status']})",
        )
    now = now_iso()
    await db.clinical_follow_up_notes.update_one(
        {"id": note_id, "tenant_id": current["tenant_id"]},
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
    fresh = await db.clinical_follow_up_notes.find_one(
        {"id": note_id, "tenant_id": current["tenant_id"]}, {"_id": 0}
    )
    await audit_success(
        user, "clinical.follow_up_note.mark_sign_ready", request,
        entity_type="clinical_follow_up_note", entity_id=note_id, phi_accessed=True,
    )
    return await _hydrate(db, ctx.tenant_id, fresh)


@patient_router.post(
    "/{patient_id}/clinical/notes/{note_id}/unmark-sign-ready",
    response_model=FollowUpNotePublic,
)
async def unmark_sign_ready(
    patient_id: str,
    note_id: str,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    current = await _load_note(db, patient_id, note_id, ctx)
    if current["status"] != "sign_ready":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Only sign_ready notes can be unmarked (status = {current['status']})",
        )
    now = now_iso()
    await db.clinical_follow_up_notes.update_one(
        {"id": note_id, "tenant_id": current["tenant_id"]},
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
    fresh = await db.clinical_follow_up_notes.find_one(
        {"id": note_id, "tenant_id": current["tenant_id"]}, {"_id": 0}
    )
    return await _hydrate(db, ctx.tenant_id, fresh)


# ---------------------------------------------------------------------------
# Sign (terminal; assigns visit_number)
# ---------------------------------------------------------------------------
@patient_router.post(
    "/{patient_id}/clinical/notes/{note_id}/sign",
    response_model=FollowUpNotePublic,
)
async def sign_follow_up_note(
    patient_id: str,
    note_id: str,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    current = await _load_note(db, patient_id, note_id, ctx)
    if current["status"] == "signed":
        raise HTTPException(status.HTTP_409_CONFLICT, "Note is already signed")
    if current["status"] not in ("draft", "sign_ready"):
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Cannot sign from status = {current['status']}"
        )

    # Compute visit_number: count signed follow-up notes on the same episode
    # (or patient-wide if no episode) + 1.
    visit_q: dict = {
        "tenant_id": ctx.tenant_id,
        "patient_id": patient_id,
        "status": "signed",
    }
    if current.get("episode_id"):
        visit_q["episode_id"] = current["episode_id"]
    prior_signed = await db.clinical_follow_up_notes.count_documents(visit_q)
    visit_number = prior_signed + 1

    now = now_iso()
    await db.clinical_follow_up_notes.update_one(
        {"id": note_id, "tenant_id": current["tenant_id"]},
        {
            "$set": {
                "status": "signed",
                "signed_at": now,
                "signed_by": user["id"],
                "visit_number": visit_number,
                "updated_at": now,
                "updated_by": user["id"],
            },
            "$push": {"history_log": {"at": now, "by": user["id"], "action": "signed", "visit_number": visit_number}},
        },
    )
    fresh = await db.clinical_follow_up_notes.find_one(
        {"id": note_id, "tenant_id": current["tenant_id"]}, {"_id": 0}
    )
    await _log_clinical_event(
        db, ctx,
        actor=user, patient_id=patient_id, episode_id=fresh.get("episode_id"),
        event_type="follow_up_note.signed", entity_type="follow_up_note",
        entity_id=note_id, metadata={"visit_number": visit_number},
    )
    await audit_success(
        user, "clinical.follow_up_note.signed", request,
        entity_type="clinical_follow_up_note", entity_id=note_id, phi_accessed=True,
        metadata={"patient_id": patient_id, "visit_number": visit_number},
    )
    return await _hydrate(db, ctx.tenant_id, fresh)


# ---------------------------------------------------------------------------
# Narrative — SOAP-formatted
# ---------------------------------------------------------------------------
def _render_soap_narrative(
    note: dict,
    *,
    patient_name: str,
    provider_name: str | None,
    diagnoses: list[dict],
) -> str:
    lines: list[str] = []
    lines.append("FOLLOW-UP / DAILY VISIT NOTE")
    lines.append("=" * 60)
    lines.append(f"Patient: {patient_name}")
    lines.append(f"Date of service: {note.get('date_of_service', '')}")
    if provider_name:
        lines.append(f"Provider: {provider_name}")
    if note.get("visit_number"):
        lines.append(f"Visit #: {note['visit_number']}")
    lines.append(f"Status: {note.get('status')}")
    if note.get("signed_at"):
        lines.append(f"Signed: {note['signed_at']}")
    lines.append("")

    # Subjective (S)
    subj = note.get("subjective") or {}
    s_lines: list[str] = []
    if subj.get("interval_history"):
        s_lines.append(f"Interval history: {subj['interval_history']}")
    if subj.get("pain_scale_0_10") is not None:
        s_lines.append(f"Pain scale: {subj['pain_scale_0_10']}/10")
    if subj.get("pain_change"):
        s_lines.append(f"Pain change: {subj['pain_change']}")
    if subj.get("functional_change"):
        s_lines.append(f"Functional change: {subj['functional_change']}")
    if subj.get("adherence_home_care"):
        row = f"Home-care adherence: {subj['adherence_home_care']}"
        if subj.get("adherence_notes"):
            row += f" — {subj['adherence_notes']}"
        s_lines.append(row)
    if s_lines:
        lines.append("SUBJECTIVE (S)")
        lines.append("--------------")
        lines.extend(s_lines)
        lines.append("")

    # Objective (O)
    obj = note.get("objective") or {}
    o_lines: list[str] = []
    v = obj.get("vitals") or {}
    vitals_rows = []
    for k, lbl in [
        ("blood_pressure", "BP"), ("pulse_bpm", "Pulse"),
        ("respiratory_rate", "RR"), ("temperature_f", "Temp (F)"),
        ("height_in", "Ht (in)"), ("weight_lb", "Wt (lb)"),
        ("o2_sat_pct", "O2 sat (%)"),
    ]:
        if v.get(k) not in (None, ""):
            vitals_rows.append(f"{lbl}: {v[k]}")
    if vitals_rows:
        o_lines.append("Vitals: " + " · ".join(vitals_rows))
    for rf in obj.get("region_findings") or []:
        parts = []
        if rf.get("palpation"):
            parts.append(f"palpation: {rf['palpation']}")
        if rf.get("rom_summary"):
            parts.append(f"ROM: {rf['rom_summary']}")
        if rf.get("notes"):
            parts.append(f"notes: {rf['notes']}")
        if parts or rf.get("body_region"):
            o_lines.append(f"  {rf.get('body_region', '?')}: " + "; ".join(parts))
    if obj.get("reassessment_summary"):
        o_lines.append(f"Reassessment: {obj['reassessment_summary']}")
    if o_lines:
        lines.append("OBJECTIVE (O)")
        lines.append("-------------")
        lines.extend(o_lines)
        lines.append("")

    # Assessment (A)
    asm = note.get("assessment") or {}
    a_lines: list[str] = []
    if asm.get("response_to_care"):
        a_lines.append(f"Response to care: {asm['response_to_care'].replace('_', ' ')}")
    if asm.get("clinical_impression"):
        a_lines.append(f"Clinical impression: {asm['clinical_impression']}")
    if a_lines:
        lines.append("ASSESSMENT (A)")
        lines.append("--------------")
        lines.extend(a_lines)
        lines.append("")

    # Plan (P)
    plan = note.get("plan") or {}
    p_lines: list[str] = []
    for t in plan.get("treatment_rendered") or []:
        kind = t.get("kind", "other")
        bits = [kind.replace("_", " ")]
        if t.get("technique"):
            bits.append(f"technique: {t['technique']}")
        if t.get("segments"):
            bits.append("segments: " + ", ".join(t["segments"]))
        if t.get("modality"):
            bits.append(f"modality: {t['modality']}")
        if t.get("region"):
            bits.append(f"region: {t['region']}")
        if t.get("duration_min") is not None:
            bits.append(f"{t['duration_min']}min")
        if t.get("description"):
            bits.append(t["description"])
        if t.get("notes"):
            bits.append(f"notes: {t['notes']}")
        p_lines.append("  - " + " · ".join(bits))
    if plan.get("regions_treated"):
        p_lines.append("Regions treated: " + ", ".join(plan["regions_treated"]))
    if plan.get("home_care_reinforcement"):
        p_lines.append(f"Home-care reinforcement: {plan['home_care_reinforcement']}")
    if plan.get("next_visit_plan"):
        row = f"Next visit: {plan['next_visit_plan']}"
        if plan.get("recommended_interval_days") is not None:
            row += f" (in {plan['recommended_interval_days']} days)"
        p_lines.append(row)
    if p_lines:
        lines.append("PLAN (P)")
        lines.append("--------")
        lines.extend(p_lines)
        lines.append("")

    if diagnoses:
        lines.append("DIAGNOSES")
        lines.append("---------")
        for dx in diagnoses:
            pri = " [PRIMARY]" if dx.get("is_primary") else ""
            lines.append(f"  - {dx.get('icd10_code')} · {dx.get('label')}{pri}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


@patient_router.get(
    "/{patient_id}/clinical/notes/{note_id}/narrative",
    response_model=FollowUpNoteNarrative,
)
async def get_follow_up_narrative(
    patient_id: str,
    note_id: str,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    note = await _load_note(db, patient_id, note_id, ctx)

    patient = await db.patients.find_one(
        {"id": patient_id, "tenant_id": ctx.tenant_id},
        {"_id": 0, "first_name": 1, "last_name": 1},
    )
    patient_name = "Unknown patient"
    if patient:
        patient_name = f"{patient.get('first_name', '')} {patient.get('last_name', '')}".strip() or "Unknown patient"

    provider_name = None
    if note.get("provider_id"):
        prov = await db.users.find_one(
            {"id": note["provider_id"], "tenant_id": ctx.tenant_id},
            {"_id": 0, "name": 1, "email": 1},
        )
        if prov:
            provider_name = prov.get("name") or prov.get("email")

    dx_rows: list[dict] = [
        d async for d in db.clinical_diagnoses.find(
            {
                "tenant_id": ctx.tenant_id, "patient_id": patient_id,
                "status": "active",
            },
            {"_id": 0},
        )
    ]

    narrative = _render_soap_narrative(
        note, patient_name=patient_name, provider_name=provider_name,
        diagnoses=dx_rows,
    )

    await audit_success(
        user, "clinical.follow_up_note.narrative_viewed", request,
        entity_type="clinical_follow_up_note", entity_id=note_id, phi_accessed=True,
        metadata={"patient_id": patient_id},
    )
    return {
        "note_id": note_id,
        "patient_id": patient_id,
        "narrative": narrative,
        "generated_at": now_iso(),
    }


# ---------------------------------------------------------------------------
# Care Timeline — merge encounters + exams + follow-up notes
# ---------------------------------------------------------------------------
@patient_router.get(
    "/{patient_id}/clinical/care-timeline",
    response_model=CareTimelineResponse,
)
async def get_care_timeline(
    patient_id: str,
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)

    q = {"tenant_id": ctx.tenant_id, "patient_id": patient_id}

    # Providers referenced across all three streams — hydrate once.
    provider_ids: set[str] = set()
    encounters = [d async for d in db.clinical_encounters.find(q, {"_id": 0}).sort("date_of_service", -1).limit(limit)]
    exams = [d async for d in db.clinical_initial_exams.find(q, {"_id": 0}).sort("date_of_service", -1).limit(limit)]
    notes = [d async for d in db.clinical_follow_up_notes.find(q, {"_id": 0}).sort("date_of_service", -1).limit(limit)]
    reexams = [d async for d in db.clinical_reexams.find(q, {"_id": 0}).sort("date_of_service", -1).limit(limit)]
    plans = [d async for d in db.clinical_treatment_plans.find(q, {"_id": 0}).sort("start_date", -1).limit(limit)]
    for bucket in (encounters, exams, notes, reexams):
        for r in bucket:
            if r.get("provider_id"):
                provider_ids.add(r["provider_id"])
    for p in plans:
        if p.get("responsible_provider_id"):
            provider_ids.add(p["responsible_provider_id"])

    prov_map: dict[str, str] = {}
    if provider_ids:
        cursor = db.users.find(
            {"id": {"$in": list(provider_ids)}, "tenant_id": ctx.tenant_id},
            {"_id": 0, "id": 1, "name": 1, "email": 1},
        )
        prov_map = {u["id"]: (u.get("name") or u.get("email") or u["id"]) async for u in cursor}

    entries: list[dict] = []
    ENC_TITLE = {
        "new_patient_exam": "New patient exam",
        "follow_up": "Follow-up visit",
        "re_evaluation": "Re-evaluation",
        "treatment_visit": "Treatment visit",
    }
    for e in encounters:
        entries.append({
            "kind": "encounter",
            "id": e["id"],
            "date_of_service": e.get("date_of_service"),
            "status": e.get("status"),
            "title": ENC_TITLE.get(e.get("encounter_type"), "Encounter"),
            "subtitle": e.get("notes") or None,
            "episode_id": e.get("episode_id"),
            "provider_id": e.get("provider_id"),
            "provider_name": prov_map.get(e.get("provider_id")),
            "link_path": f"/patients/{patient_id}?tab=clinical&encounter={e['id']}",
        })
    for x in exams:
        entries.append({
            "kind": "initial_exam",
            "id": x["id"],
            "date_of_service": x.get("date_of_service"),
            "status": x.get("status"),
            "title": "Initial Exam",
            "subtitle": None,
            "episode_id": x.get("episode_id"),
            "provider_id": x.get("provider_id"),
            "provider_name": prov_map.get(x.get("provider_id")),
            "link_path": f"/patients/{patient_id}/clinical/exams/{x['id']}",
        })
    for n in notes:
        subtitle_bits = []
        if n.get("visit_number"):
            subtitle_bits.append(f"Visit #{n['visit_number']}")
        asm = (n.get("assessment") or {}).get("response_to_care")
        if asm:
            subtitle_bits.append(asm.replace("_", " "))
        entries.append({
            "kind": "follow_up_note",
            "id": n["id"],
            "date_of_service": n.get("date_of_service"),
            "status": n.get("status"),
            "title": "Follow-up note",
            "subtitle": " · ".join(subtitle_bits) if subtitle_bits else None,
            "episode_id": n.get("episode_id"),
            "provider_id": n.get("provider_id"),
            "provider_name": prov_map.get(n.get("provider_id")),
            "link_path": f"/patients/{patient_id}/clinical/follow-up/{n['id']}",
        })
    for r in reexams:
        bits = []
        if r.get("visit_number_at_reexam") is not None:
            bits.append(f"After {r['visit_number_at_reexam']} visits")
        if r.get("recommendation_decision"):
            bits.append(r["recommendation_decision"].replace("_", " "))
        entries.append({
            "kind": "re_exam",
            "id": r["id"],
            "date_of_service": r.get("date_of_service"),
            "status": r.get("status"),
            "title": "Re-exam",
            "subtitle": " · ".join(bits) if bits else None,
            "episode_id": r.get("episode_id"),
            "provider_id": r.get("provider_id"),
            "provider_name": prov_map.get(r.get("provider_id")),
            "link_path": f"/patients/{patient_id}/clinical/re-exams/{r['id']}",
        })
    for p in plans:
        bits = []
        if p.get("frequency_visits_per_week"):
            bits.append(f"{p['frequency_visits_per_week']}x/wk")
        if p.get("expected_duration_weeks"):
            bits.append(f"{p['expected_duration_weeks']} wks")
        entries.append({
            "kind": "treatment_plan",
            "id": p["id"],
            "date_of_service": p.get("start_date"),
            "status": p.get("plan_status"),
            "title": p.get("title") or "Treatment plan",
            "subtitle": " · ".join(bits) if bits else None,
            "episode_id": p.get("episode_id"),
            "provider_id": p.get("responsible_provider_id"),
            "provider_name": prov_map.get(p.get("responsible_provider_id")),
            "link_path": f"/patients/{patient_id}/clinical/treatment-plans/{p['id']}",
        })

    # Phase 7 — clinical media (exclude soft-deleted)
    media = [
        d async for d in db.clinical_media.find(
            {**q, "deleted_at": None}, {"_id": 0, "storage_path": 0},
        ).sort("study_date", -1).limit(limit)
    ]
    CAT_TITLE = {
        "xray": "X-ray", "mri_ct_report": "MRI/CT report",
        "ultrasound": "Ultrasound", "clinical_photo": "Clinical photo",
        "outside_record": "Outside record", "other_pdf": "Document",
    }
    for m in media:
        sub_bits = []
        if m.get("body_region"):
            sub_bits.append(m["body_region"])
        if m.get("source"):
            sub_bits.append(m["source"].replace("_", " "))
        entries.append({
            "kind": "clinical_media",
            "id": m["id"],
            "date_of_service": m.get("study_date") or m.get("uploaded_at"),
            "status": "uploaded",
            "title": CAT_TITLE.get(m["category"], "Media"),
            "subtitle": " · ".join(sub_bits) if sub_bits else m.get("original_filename"),
            "episode_id": m.get("episode_id"),
            "provider_id": None,
            "provider_name": None,
            "link_path": f"/patients/{patient_id}/clinical/media/{m['id']}",
        })

    # Phase 7 — non-reexam outcomes (reexam-sourced entries are already
    # represented by the re_exam timeline row, keep timeline readable).
    outcomes = [
        d async for d in db.clinical_outcome_entries.find(
            {**q, "source": {"$ne": "reexam"}}, {"_id": 0},
        ).sort("captured_at", -1).limit(limit)
    ]
    for o in outcomes:
        score = o.get("score")
        mx = o.get("max_score")
        val = f"{score}" + (f"/{mx}" if mx is not None else "")
        entries.append({
            "kind": "outcome_entry",
            "id": o["id"],
            "date_of_service": o.get("captured_at"),
            "status": o.get("source"),
            "title": f"{o.get('label') or o['measure_type']} · {val}",
            "subtitle": o.get("note") or None,
            "episode_id": o.get("episode_id"),
            "provider_id": None,
            "provider_name": None,
            "link_path": None,
        })

    # Phase 7 — derived diagnosis-change events from clinical_audit_events
    dx_events = [
        d async for d in db.clinical_audit_events.find(
            {
                "tenant_id": ctx.tenant_id,
                "patient_id": patient_id,
                "event_type": {"$in": [
                    "diagnosis.created", "diagnosis.updated",
                    "diagnosis.resolved", "diagnosis.activated",
                ]},
            },
            {"_id": 0},
        ).sort("at", -1).limit(limit)
    ]
    for ev in dx_events:
        meta = ev.get("metadata") or {}
        title_bits = []
        if meta.get("icd10_code"):
            title_bits.append(meta["icd10_code"])
        if meta.get("label"):
            title_bits.append(meta["label"])
        action = ev["event_type"].split(".", 1)[1] if "." in ev["event_type"] else "changed"
        entries.append({
            "kind": "diagnosis_change",
            "id": ev.get("id") or ev.get("entity_id") or f"dx-{ev.get('at')}",
            "date_of_service": ev.get("at"),
            "status": action,
            "title": f"Diagnosis {action}" + (
                f" · {' — '.join(title_bits)}" if title_bits else ""
            ),
            "subtitle": None,
            "episode_id": ev.get("episode_id"),
            "provider_id": ev.get("actor_id"),
            "provider_name": prov_map.get(ev.get("actor_id")),
            "link_path": None,
        })

    # Phase 7 — derived intake submission from clinical_audit_events (optional)
    intake_events = [
        d async for d in db.clinical_audit_events.find(
            {
                "tenant_id": ctx.tenant_id,
                "patient_id": patient_id,
                "event_type": {"$in": [
                    "clinical_history.intake_submitted",
                    "clinical_history.intake_received",
                ]},
            },
            {"_id": 0},
        ).sort("at", -1).limit(limit)
    ]
    for ev in intake_events:
        entries.append({
            "kind": "intake_submission",
            "id": ev.get("id") or ev.get("entity_id") or f"intake-{ev.get('at')}",
            "date_of_service": ev.get("at"),
            "status": "submitted",
            "title": "Intake submitted",
            "subtitle": None,
            "episode_id": ev.get("episode_id"),
            "provider_id": None,
            "provider_name": None,
            "link_path": None,
        })

    entries.sort(key=lambda r: (r.get("date_of_service") or ""), reverse=True)
    entries = entries[:limit]

    await audit_success(
        user, "clinical.care_timeline_viewed", request,
        entity_type="patient", entity_id=patient_id, phi_accessed=True,
        metadata={"count": len(entries)},
    )
    return {
        "patient_id": patient_id,
        "entries": entries,
        "generated_at": now_iso(),
    }
