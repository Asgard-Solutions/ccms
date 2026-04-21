"""Clinical addenda router — Phase 8.

Addenda live alongside a signed parent artifact (note/exam/re-exam).
Workflow:
    1. Parent must be signed. Create addendum in `draft` state.
    2. Author or admin can edit the draft.
    3. Sign — locks the addendum content immutably.
    4. Only drafts can be deleted (by the author).

Strict authorship (per product decision Phase 8, option 2a):
    - Anyone with role in {admin, doctor} can CREATE an addendum on a
      signed parent artifact (clinicians routinely append clarifying
      notes to peers' charting).
    - Only the addendum's author OR an admin can EDIT / DELETE / SIGN
      that addendum. Non-authoring doctors cannot finalize someone
      else's addendum — this mirrors the strict signing posture
      required by HIPAA-aware EHRs.

Endpoints (mounted under `/api`):
    GET    /patients/{pid}/clinical/{parent_type}/{parent_id}/addenda
    POST   /patients/{pid}/clinical/{parent_type}/{parent_id}/addenda
    GET    /patients/{pid}/clinical/addenda/{addendum_id}
    PATCH  /patients/{pid}/clinical/addenda/{addendum_id}
    POST   /patients/{pid}/clinical/addenda/{addendum_id}/sign
    DELETE /patients/{pid}/clinical/addenda/{addendum_id}

    GET    /patients/{pid}/clinical/addenda                 -- chart-level list
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status

from core.audit import audit_success
from core.db import get_db_write
from core.deps import require_role
from core.reauth import require_reauth
from core.tenancy import TenantContext, get_tenant_context
from core.tenant_scope import scoped_filter, stamp_for_write
from services.clinical.addenda_models import (
    ADDENDUM_PARENT_TYPE,
    ClinicalAddendumCreate,
    ClinicalAddendumPublic,
    ClinicalAddendumUpdate,
)
from services.clinical.models import now_iso
from services.clinical.router import _load_patient, _log_clinical_event

router = APIRouter(prefix="/patients", tags=["clinical"])

# Map parent_type → collection name. Used to fetch / validate the parent
# artifact exists and is signed before an addendum is authored.
PARENT_COLLECTIONS: dict[str, str] = {
    "follow_up_note": "clinical_follow_up_notes",
    "initial_exam": "clinical_initial_exams",
    "re_exam": "clinical_reexams",
}


def _strip(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k not in {"_id", "history_log"}}


async def _load_parent(
    db, parent_type: str, parent_id: str, patient_id: str, ctx: TenantContext,
) -> dict:
    """Look up the parent artifact; confirm it belongs to the patient and
    tenant. Returns the parent doc or raises 404. Parent signed-ness is
    checked by callers that need it (POST requires signed; GET list is
    permissive so patients in the chart UI can see an empty list prior
    to signing)."""
    if parent_type not in PARENT_COLLECTIONS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown parent type")
    coll = PARENT_COLLECTIONS[parent_type]
    q = scoped_filter(
        {"id": parent_id, "patient_id": patient_id}, ctx, location_scoped=False,
    )
    if q.get("__deny__"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Parent artifact not found")
    doc = await db[coll].find_one(q, {"_id": 0})
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Parent artifact not found")
    return doc


async def _load_addendum(
    db, addendum_id: str, patient_id: str, ctx: TenantContext,
) -> dict:
    q = scoped_filter(
        {"id": addendum_id, "patient_id": patient_id}, ctx, location_scoped=False,
    )
    if q.get("__deny__"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Addendum not found")
    doc = await db.clinical_addenda.find_one(q, {"_id": 0})
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Addendum not found")
    return doc


async def _hydrate(db, tenant_id: str, doc: dict) -> dict:
    out = _strip(doc)
    name_ids: set[str] = set()
    if doc.get("author_id"):
        name_ids.add(doc["author_id"])
    if doc.get("signed_by"):
        name_ids.add(doc["signed_by"])
    if name_ids:
        cursor = db.users.find(
            {"id": {"$in": list(name_ids)}, "tenant_id": tenant_id},
            {"_id": 0, "id": 1, "name": 1, "email": 1},
        )
        name_map = {
            u["id"]: (u.get("name") or u.get("email") or u["id"])
            async for u in cursor
        }
        if doc.get("author_id"):
            out["author_name"] = name_map.get(doc["author_id"])
        if doc.get("signed_by"):
            out["signed_by_name"] = name_map.get(doc["signed_by"])
    return out


def _assert_author_or_admin(addendum: dict, user: dict, action: str) -> None:
    if user.get("role") == "admin":
        return
    if addendum.get("author_id") == user.get("id"):
        return
    raise HTTPException(
        status.HTTP_403_FORBIDDEN,
        f"Only the addendum author or an admin may {action} this addendum",
    )


# ---------------------------------------------------------------------------
# LIST per parent
# ---------------------------------------------------------------------------
@router.get(
    "/{patient_id}/clinical/{parent_type}/{parent_id}/addenda",
    response_model=list[ClinicalAddendumPublic],
)
async def list_parent_addenda(
    patient_id: str,
    parent_id: str,
    request: Request,
    parent_type: str = Path(..., pattern="^(follow_up_note|initial_exam|re_exam)$"),
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    await _load_parent(db, parent_type, parent_id, patient_id, ctx)

    cursor = db.clinical_addenda.find(
        {
            "tenant_id": ctx.tenant_id,
            "patient_id": patient_id,
            "parent_type": parent_type,
            "parent_id": parent_id,
        },
        {"_id": 0},
    ).sort("created_at", 1)
    docs = [d async for d in cursor]

    await audit_success(
        user, "clinical.addendum.list_viewed", request,
        entity_type="clinical_addendum_list", entity_id=parent_id, phi_accessed=True,
        metadata={"patient_id": patient_id, "parent_type": parent_type, "count": len(docs)},
    )
    return [await _hydrate(db, ctx.tenant_id, d) for d in docs]


# ---------------------------------------------------------------------------
# LIST chart-level
# ---------------------------------------------------------------------------
@router.get(
    "/{patient_id}/clinical/addenda",
    response_model=list[ClinicalAddendumPublic],
)
async def list_chart_addenda(
    patient_id: str,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)

    cursor = db.clinical_addenda.find(
        {"tenant_id": ctx.tenant_id, "patient_id": patient_id},
        {"_id": 0},
    ).sort("created_at", -1)
    docs = [d async for d in cursor]
    return [await _hydrate(db, ctx.tenant_id, d) for d in docs]


# ---------------------------------------------------------------------------
# CREATE (parent must be signed)
# ---------------------------------------------------------------------------
@router.post(
    "/{patient_id}/clinical/{parent_type}/{parent_id}/addenda",
    response_model=ClinicalAddendumPublic,
    status_code=201,
)
async def create_addendum(
    patient_id: str,
    parent_id: str,
    payload: ClinicalAddendumCreate,
    request: Request,
    parent_type: str = Path(..., pattern="^(follow_up_note|initial_exam|re_exam)$"),
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)

    db = get_db_write()
    patient = await _load_patient(db, patient_id, ctx)
    parent = await _load_parent(db, parent_type, parent_id, patient_id, ctx)

    if parent.get("status") != "signed":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Parent artifact must be signed before an addendum can be authored",
        )

    now = now_iso()
    doc: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "patient_id": patient_id,
        "parent_type": parent_type,
        "parent_id": parent_id,
        "parent_signed_by": parent.get("signed_by"),
        "encounter_id": parent.get("encounter_id"),
        "episode_id": parent.get("episode_id"),
        "reason": payload.reason.strip(),
        "narrative": payload.narrative.strip(),
        "status": "draft",
        "signed_at": None,
        "signed_by": None,
        "author_id": user["id"],
        "created_at": now,
        "updated_at": now,
        "created_by": user["id"],
        "updated_by": user["id"],
        "history_log": [{"at": now, "by": user["id"], "action": "created"}],
    }
    doc = stamp_for_write(doc, ctx, location_id=patient.get("location_id"))
    await db.clinical_addenda.insert_one(doc)

    await _log_clinical_event(
        db, ctx,
        actor=user, patient_id=patient_id, episode_id=doc.get("episode_id"),
        event_type="addendum.created",
        entity_type="clinical_addendum",
        entity_id=doc["id"],
        metadata={
            "parent_type": parent_type, "parent_id": parent_id,
            "reason": doc["reason"],
        },
    )
    await audit_success(
        user, "clinical.addendum.created", request,
        entity_type="clinical_addendum", entity_id=doc["id"], phi_accessed=True,
        metadata={
            "patient_id": patient_id,
            "parent_type": parent_type, "parent_id": parent_id,
        },
    )
    return await _hydrate(db, ctx.tenant_id, doc)


# ---------------------------------------------------------------------------
# GET single
# ---------------------------------------------------------------------------
@router.get(
    "/{patient_id}/clinical/addenda/{addendum_id}",
    response_model=ClinicalAddendumPublic,
)
async def get_addendum(
    patient_id: str,
    addendum_id: str,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    doc = await _load_addendum(db, addendum_id, patient_id, ctx)
    await audit_success(
        user, "clinical.addendum.read", request,
        entity_type="clinical_addendum", entity_id=addendum_id, phi_accessed=True,
        metadata={"patient_id": patient_id},
    )
    return await _hydrate(db, ctx.tenant_id, doc)


# ---------------------------------------------------------------------------
# PATCH (draft only, author or admin)
# ---------------------------------------------------------------------------
@router.patch(
    "/{patient_id}/clinical/addenda/{addendum_id}",
    response_model=ClinicalAddendumPublic,
)
async def patch_addendum(
    patient_id: str,
    addendum_id: str,
    payload: ClinicalAddendumUpdate,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)

    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    current = await _load_addendum(db, addendum_id, patient_id, ctx)

    if current.get("status") == "signed":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Signed addenda are immutable",
        )
    _assert_author_or_admin(current, user, "edit")

    dumped = payload.model_dump(exclude_unset=True)
    sets: dict = {}
    if "narrative" in dumped and dumped["narrative"] is not None:
        sets["narrative"] = dumped["narrative"].strip()
    if "reason" in dumped and dumped["reason"] is not None:
        sets["reason"] = dumped["reason"].strip()
    if not sets:
        return await _hydrate(db, ctx.tenant_id, current)

    now = now_iso()
    sets["updated_at"] = now
    sets["updated_by"] = user["id"]

    await db.clinical_addenda.update_one(
        {"id": addendum_id, "tenant_id": current["tenant_id"]},
        {
            "$set": sets,
            "$push": {
                "history_log": {
                    "at": now, "by": user["id"], "action": "edited",
                    "fields": sorted(sets.keys() - {"updated_at", "updated_by"}),
                },
            },
        },
    )
    fresh = await db.clinical_addenda.find_one(
        {"id": addendum_id, "tenant_id": current["tenant_id"]}, {"_id": 0}
    )

    await _log_clinical_event(
        db, ctx,
        actor=user, patient_id=patient_id, episode_id=current.get("episode_id"),
        event_type="addendum.edited",
        entity_type="clinical_addendum", entity_id=addendum_id,
        metadata={"parent_type": current.get("parent_type"), "parent_id": current.get("parent_id")},
    )
    await audit_success(
        user, "clinical.addendum.edited", request,
        entity_type="clinical_addendum", entity_id=addendum_id, phi_accessed=True,
        metadata={"patient_id": patient_id},
    )
    return await _hydrate(db, ctx.tenant_id, fresh)


# ---------------------------------------------------------------------------
# SIGN (author or admin; draft → signed)
# ---------------------------------------------------------------------------
@router.post(
    "/{patient_id}/clinical/addenda/{addendum_id}/sign",
    response_model=ClinicalAddendumPublic,
)
async def sign_addendum(
    patient_id: str,
    addendum_id: str,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)

    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    current = await _load_addendum(db, addendum_id, patient_id, ctx)

    if current.get("status") == "signed":
        raise HTTPException(status.HTTP_409_CONFLICT, "Addendum is already signed")
    if current.get("status") != "draft":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Cannot sign addendum from status '{current.get('status')}'",
        )
    _assert_author_or_admin(current, user, "sign")

    now = now_iso()
    await db.clinical_addenda.update_one(
        {"id": addendum_id, "tenant_id": current["tenant_id"]},
        {
            "$set": {
                "status": "signed",
                "signed_at": now,
                "signed_by": user["id"],
                "updated_at": now,
                "updated_by": user["id"],
            },
            "$push": {
                "history_log": {"at": now, "by": user["id"], "action": "signed"},
            },
        },
    )
    fresh = await db.clinical_addenda.find_one(
        {"id": addendum_id, "tenant_id": current["tenant_id"]}, {"_id": 0}
    )

    await _log_clinical_event(
        db, ctx,
        actor=user, patient_id=patient_id, episode_id=current.get("episode_id"),
        event_type="addendum.signed",
        entity_type="clinical_addendum", entity_id=addendum_id,
        metadata={
            "parent_type": current.get("parent_type"),
            "parent_id": current.get("parent_id"),
        },
    )
    await audit_success(
        user, "clinical.addendum.signed", request,
        entity_type="clinical_addendum", entity_id=addendum_id, phi_accessed=True,
        metadata={"patient_id": patient_id},
    )
    return await _hydrate(db, ctx.tenant_id, fresh)


# ---------------------------------------------------------------------------
# DELETE (draft only, author or admin)
# ---------------------------------------------------------------------------
@router.delete(
    "/{patient_id}/clinical/addenda/{addendum_id}",
    status_code=204,
)
async def delete_addendum(
    patient_id: str,
    addendum_id: str,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)

    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    current = await _load_addendum(db, addendum_id, patient_id, ctx)

    if current.get("status") == "signed":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Signed addenda cannot be deleted",
        )
    _assert_author_or_admin(current, user, "delete")

    await db.clinical_addenda.delete_one(
        {"id": addendum_id, "tenant_id": current["tenant_id"]},
    )
    await _log_clinical_event(
        db, ctx,
        actor=user, patient_id=patient_id, episode_id=current.get("episode_id"),
        event_type="addendum.deleted",
        entity_type="clinical_addendum", entity_id=addendum_id,
        metadata={
            "parent_type": current.get("parent_type"),
            "parent_id": current.get("parent_id"),
        },
    )
    await audit_success(
        user, "clinical.addendum.deleted", request,
        entity_type="clinical_addendum", entity_id=addendum_id, phi_accessed=True,
        metadata={"patient_id": patient_id},
    )
    # 204 No Content
