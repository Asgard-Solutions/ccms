"""Clinical diagnoses / problem-list router — Phase 2.

Surface:
    GET    /patients/{pid}/clinical/diagnoses
    POST   /patients/{pid}/clinical/diagnoses
    GET    /patients/{pid}/clinical/diagnoses/{dx_id}
    PATCH  /patients/{pid}/clinical/diagnoses/{dx_id}
    POST   /patients/{pid}/clinical/diagnoses/{dx_id}/resolve
    POST   /patients/{pid}/clinical/diagnoses/{dx_id}/reactivate

Tenant-scoped. Reauth required on writes. Diagnoses may optionally link to
ANY episode/case on the same patient regardless of the episode's status —
important for cleaning up old recurrences or tagging archived cases.

`is_primary` is enforced unique within **(patient, episode_id-or-null, status=active)**:
- Setting is_primary=True on a row auto-clears is_primary on sibling rows
  that share the same episode grouping AND are currently active.
- Resolved rows keep their is_primary flag as a historical marker.
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
from services.clinical.phase2_models import (
    DX_STATUS,
    DiagnosisCreate,
    DiagnosisPublic,
    DiagnosisResolve,
    DiagnosisUpdate,
)
from services.clinical.router import _load_patient, _log_clinical_event

router = APIRouter(prefix="/patients", tags=["clinical"])


def _strip(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k not in {"_id", "history"}}


async def _ensure_episode_same_patient(
    db, ctx: TenantContext, patient_id: str, episode_id: str | None
) -> None:
    if not episode_id:
        return
    ep = await db.clinical_episode_cases.find_one(
        {
            "id": episode_id,
            "patient_id": patient_id,
            "tenant_id": ctx.tenant_id,
        },
        {"_id": 0, "id": 1},
    )
    if not ep:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Episode not found on this patient",
        )


async def _clear_sibling_primary(
    db, *, tenant_id: str, patient_id: str,
    episode_id: str | None, exclude_id: str | None,
) -> int:
    """Unset `is_primary` on every ACTIVE sibling diagnosis sharing the same
    episode grouping. Returns the number of rows touched."""
    q: dict = {
        "tenant_id": tenant_id,
        "patient_id": patient_id,
        "is_primary": True,
        "status": "active",
    }
    # Mongo stores None as null; match the grouping exactly.
    if episode_id is None:
        q["episode_id"] = None
    else:
        q["episode_id"] = episode_id
    if exclude_id:
        q["id"] = {"$ne": exclude_id}
    result = await db.clinical_diagnoses.update_many(q, {"$set": {"is_primary": False}})
    return result.modified_count


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------
@router.get(
    "/{patient_id}/clinical/diagnoses",
    response_model=list[DiagnosisPublic],
)
async def list_diagnoses(
    patient_id: str,
    request: Request,
    status_in: str | None = Query(default=None, description="Comma-separated (active,resolved)"),
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

    cursor = db.clinical_diagnoses.find(q, {"_id": 0}).sort([
        ("is_primary", -1),
        ("created_at", -1),
    ])
    rows = [d async for d in cursor]
    await audit_success(
        user, "clinical.diagnosis.list_viewed", request,
        entity_type="patient", entity_id=patient_id, phi_accessed=True,
        metadata={"count": len(rows)},
    )
    return [_strip(d) for d in rows]


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------
@router.post(
    "/{patient_id}/clinical/diagnoses",
    response_model=DiagnosisPublic,
    status_code=201,
)
async def create_diagnosis(
    patient_id: str,
    payload: DiagnosisCreate,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)

    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    await _ensure_episode_same_patient(db, ctx, patient_id, payload.episode_id)

    now = now_iso()
    dx_id = str(uuid.uuid4())

    doc = {
        "id": dx_id,
        "patient_id": patient_id,
        "episode_id": payload.episode_id,
        "icd10_code": payload.icd10_code.strip().upper(),
        "label": payload.label.strip(),
        "status": "active",
        "is_primary": bool(payload.is_primary),
        "body_region": payload.body_region,
        "laterality": payload.laterality,
        "chronicity": payload.chronicity,
        "onset_date": payload.onset_date,
        "resolved_date": None,
        "resolution_notes": None,
        "notes": payload.notes,
        "created_at": now,
        "updated_at": now,
        "created_by": user["id"],
        "updated_by": user["id"],
    }
    doc = stamp_for_write(doc, ctx)

    if payload.is_primary:
        await _clear_sibling_primary(
            db, tenant_id=ctx.tenant_id, patient_id=patient_id,
            episode_id=payload.episode_id, exclude_id=dx_id,
        )

    await db.clinical_diagnoses.insert_one(doc)

    await _log_clinical_event(
        db, ctx,
        actor=user, patient_id=patient_id, episode_id=payload.episode_id,
        event_type="diagnosis.created", entity_type="diagnosis", entity_id=dx_id,
        metadata={"icd10_code": doc["icd10_code"], "label": doc["label"]},
    )
    await audit_success(
        user, "clinical.diagnosis.created", request,
        entity_type="clinical_diagnosis", entity_id=dx_id, phi_accessed=True,
        metadata={
            "patient_id": patient_id,
            "episode_id": payload.episode_id,
            "icd10_code": doc["icd10_code"],
        },
    )
    return _strip(doc)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------
@router.get(
    "/{patient_id}/clinical/diagnoses/{dx_id}",
    response_model=DiagnosisPublic,
)
async def get_diagnosis(
    patient_id: str,
    dx_id: str,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    q = scoped_filter(
        {"id": dx_id, "patient_id": patient_id}, ctx, location_scoped=False,
    )
    if q.get("__deny__"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Diagnosis not found")
    doc = await db.clinical_diagnoses.find_one(q, {"_id": 0})
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Diagnosis not found")
    await audit_success(
        user, "clinical.diagnosis.read", request,
        entity_type="clinical_diagnosis", entity_id=dx_id, phi_accessed=True,
        metadata={"patient_id": patient_id},
    )
    return _strip(doc)


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------
@router.patch(
    "/{patient_id}/clinical/diagnoses/{dx_id}",
    response_model=DiagnosisPublic,
)
async def update_diagnosis(
    patient_id: str,
    dx_id: str,
    payload: DiagnosisUpdate,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)

    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    q = scoped_filter(
        {"id": dx_id, "patient_id": patient_id}, ctx, location_scoped=False,
    )
    if q.get("__deny__"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Diagnosis not found")
    current = await db.clinical_diagnoses.find_one(q, {"_id": 0})
    if not current:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Diagnosis not found")

    dumped = payload.model_dump(exclude_unset=True)
    if not dumped:
        return _strip(current)

    if "episode_id" in dumped:
        await _ensure_episode_same_patient(db, ctx, patient_id, dumped["episode_id"])

    if "icd10_code" in dumped and dumped["icd10_code"] is not None:
        dumped["icd10_code"] = dumped["icd10_code"].strip().upper()
    if "label" in dumped and dumped["label"] is not None:
        dumped["label"] = dumped["label"].strip()

    # Primary-flag uniqueness: if we're flipping TO True, or we're moving
    # this diagnosis between episodes AND it is (or will be) the primary,
    # clear siblings on the destination grouping.
    final_episode_id = dumped.get("episode_id", current.get("episode_id"))
    final_is_primary = dumped.get("is_primary", current.get("is_primary"))
    if final_is_primary and current["status"] == "active":
        await _clear_sibling_primary(
            db, tenant_id=ctx.tenant_id, patient_id=patient_id,
            episode_id=final_episode_id, exclude_id=dx_id,
        )

    now = now_iso()
    dumped["updated_at"] = now
    dumped["updated_by"] = user["id"]
    fields_changed = sorted([k for k in dumped.keys() if k not in {"updated_at", "updated_by"}])

    await db.clinical_diagnoses.update_one(
        {"id": dx_id, "tenant_id": current["tenant_id"]},
        {"$set": dumped},
    )
    fresh = await db.clinical_diagnoses.find_one(
        {"id": dx_id, "tenant_id": current["tenant_id"]}, {"_id": 0}
    )

    await _log_clinical_event(
        db, ctx,
        actor=user, patient_id=patient_id,
        episode_id=fresh.get("episode_id"),
        event_type="diagnosis.updated", entity_type="diagnosis", entity_id=dx_id,
        metadata={"fields": fields_changed},
    )
    await audit_success(
        user, "clinical.diagnosis.updated", request,
        entity_type="clinical_diagnosis", entity_id=dx_id, phi_accessed=True,
        metadata={"patient_id": patient_id, "fields": fields_changed},
    )
    return _strip(fresh)


# ---------------------------------------------------------------------------
# Resolve
# ---------------------------------------------------------------------------
@router.post(
    "/{patient_id}/clinical/diagnoses/{dx_id}/resolve",
    response_model=DiagnosisPublic,
)
async def resolve_diagnosis(
    patient_id: str,
    dx_id: str,
    payload: DiagnosisResolve,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)

    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    q = scoped_filter(
        {"id": dx_id, "patient_id": patient_id}, ctx, location_scoped=False,
    )
    if q.get("__deny__"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Diagnosis not found")
    current = await db.clinical_diagnoses.find_one(q, {"_id": 0})
    if not current:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Diagnosis not found")
    if current["status"] == "resolved":
        raise HTTPException(status.HTTP_409_CONFLICT, "Diagnosis is already resolved")

    now = now_iso()
    sets = {
        "status": "resolved",
        "resolved_date": payload.resolved_date or now,
        "resolution_notes": payload.resolution_notes,
        "updated_at": now,
        "updated_by": user["id"],
    }
    await db.clinical_diagnoses.update_one(
        {"id": dx_id, "tenant_id": current["tenant_id"]},
        {"$set": sets},
    )
    fresh = await db.clinical_diagnoses.find_one(
        {"id": dx_id, "tenant_id": current["tenant_id"]}, {"_id": 0}
    )

    await _log_clinical_event(
        db, ctx,
        actor=user, patient_id=patient_id, episode_id=fresh.get("episode_id"),
        event_type="diagnosis.resolved", entity_type="diagnosis", entity_id=dx_id,
        metadata={"resolution_notes": payload.resolution_notes},
    )
    await audit_success(
        user, "clinical.diagnosis.resolved", request,
        entity_type="clinical_diagnosis", entity_id=dx_id, phi_accessed=True,
        metadata={"patient_id": patient_id},
    )
    return _strip(fresh)


# ---------------------------------------------------------------------------
# Reactivate
# ---------------------------------------------------------------------------
@router.post(
    "/{patient_id}/clinical/diagnoses/{dx_id}/reactivate",
    response_model=DiagnosisPublic,
)
async def reactivate_diagnosis(
    patient_id: str,
    dx_id: str,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)

    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    q = scoped_filter(
        {"id": dx_id, "patient_id": patient_id}, ctx, location_scoped=False,
    )
    if q.get("__deny__"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Diagnosis not found")
    current = await db.clinical_diagnoses.find_one(q, {"_id": 0})
    if not current:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Diagnosis not found")
    if current["status"] == "active":
        raise HTTPException(status.HTTP_409_CONFLICT, "Diagnosis is already active")

    # Reactivating a diagnosis that WAS primary must re-enforce the
    # uniqueness invariant — clear any other active primaries in the same
    # episode grouping so we never end up with two.
    now = now_iso()
    if current.get("is_primary"):
        await _clear_sibling_primary(
            db, tenant_id=ctx.tenant_id, patient_id=patient_id,
            episode_id=current.get("episode_id"), exclude_id=dx_id,
        )

    await db.clinical_diagnoses.update_one(
        {"id": dx_id, "tenant_id": current["tenant_id"]},
        {"$set": {
            "status": "active",
            "resolved_date": None,
            "resolution_notes": None,
            "updated_at": now,
            "updated_by": user["id"],
        }},
    )
    fresh = await db.clinical_diagnoses.find_one(
        {"id": dx_id, "tenant_id": current["tenant_id"]}, {"_id": 0}
    )

    await _log_clinical_event(
        db, ctx,
        actor=user, patient_id=patient_id, episode_id=fresh.get("episode_id"),
        event_type="diagnosis.reactivated", entity_type="diagnosis", entity_id=dx_id,
    )
    await audit_success(
        user, "clinical.diagnosis.reactivated", request,
        entity_type="clinical_diagnosis", entity_id=dx_id, phi_accessed=True,
        metadata={"patient_id": patient_id},
    )
    return _strip(fresh)
