"""Multi-version patient intake forms.

Each patient can have many intake forms (one per encounter / visit / injury).
The collection is `patient_intake_forms` and rows are scoped by
`tenant_id` + `location_id` like the rest of the patient sub-records.

Status lifecycle:
  draft      → editable; `captured_at` is None until the form completes.
  completed  → immutable; any further edits should `POST` a new form.

PATCH semantics (exclude_unset):
  Only the keys explicitly supplied in the request body are applied. Passing
  the key with `null` clears that field; omitting the key leaves it alone.
"""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status

from core.audit import audit_success
from core.db import get_db_read, get_db_write
from core.deps import get_current_user
from core.tenancy import TenantContext, get_tenant_context
from core.tenant_scope import scoped_filter, stamp_for_write
from services.authz.policy import require_permission
from services.patient._shared import (
    decrypt_patient_value,
    encrypt_patient_value,
    now_iso,
)
from services.patient.intake_forms_models import (
    IntakeFormCreate,
    IntakeFormPatch,
    IntakeFormPublic,
)

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/{patient_id}/intake-forms", tags=["patient-intake"])

INTAKE_FORM_ENCRYPTED = ["clinical_intake", "case_details", "notes"]


def _encrypt_form(doc: dict) -> dict:
    out = dict(doc)
    for key in INTAKE_FORM_ENCRYPTED:
        if key in out and out[key] is not None:
            out[key] = encrypt_patient_value(out[key])
    return out


def _decrypt_form(doc: dict) -> dict:
    out = dict(doc)
    for key in INTAKE_FORM_ENCRYPTED:
        if key in out and out[key] is not None:
            out[key] = decrypt_patient_value(out[key])
    return out


async def _load_patient(patient_id: str, ctx: TenantContext) -> dict:
    db = get_db_read()
    q = scoped_filter({"id": patient_id}, ctx, location_scoped=True)
    if q.get("__deny__"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    p = await db.patients.find_one(q, {"_id": 0, "id": 1, "location_id": 1,
                                       "clinical_intake": 1, "case_details": 1})
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    return p


def _form_query(patient_id: str, form_id: str, ctx: TenantContext) -> dict:
    q: dict = {"id": form_id, "patient_id": patient_id}
    if ctx.tenant_id and not ctx.is_platform_admin:
        q["tenant_id"] = ctx.tenant_id
    return q


def _shape(doc: dict) -> dict:
    """Return a decrypted, JSON-safe copy of an intake form document."""
    out = _decrypt_form({k: v for k, v in doc.items() if k != "_id"})
    return out


@router.get("", response_model=list[IntakeFormPublic])
async def list_intake_forms(
    patient_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    ctx: TenantContext = Depends(get_tenant_context),
):
    """All intake forms for a patient, newest first."""
    await _load_patient(patient_id, ctx)
    db = get_db_read()
    q: dict = {"patient_id": patient_id}
    if ctx.tenant_id and not ctx.is_platform_admin:
        q["tenant_id"] = ctx.tenant_id
    docs = [
        _shape(d)
        async for d in db.patient_intake_forms.find(q, {"_id": 0})
        .sort("created_at", -1)
    ]
    await audit_success(
        user, "intake_form.list_viewed", request,
        entity_type="patient", entity_id=patient_id,
        phi_accessed=True, metadata={"count": len(docs)},
    )
    return docs


@router.post("", response_model=IntakeFormPublic, status_code=201)
async def create_intake_form(
    patient_id: str,
    payload: IntakeFormCreate,
    request: Request,
    user: dict = Depends(require_permission("patient_chart", "create", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    """Create a new intake form (draft). Optionally seeds from patient's
    current `clinical_intake` / `case_details` blobs so the form opens
    pre-filled with what we already know."""
    patient = await _load_patient(patient_id, ctx)
    db = get_db_write()

    clinical = payload.clinical_intake.model_dump() if payload.clinical_intake else None
    case = payload.case_details.model_dump() if payload.case_details else None
    if payload.seed_from_patient:
        if clinical is None:
            clinical = decrypt_patient_value(patient.get("clinical_intake")) or None
        if case is None:
            case = decrypt_patient_value(patient.get("case_details")) or None

    # Version = count of existing forms + 1 (tenant-scoped).
    count_q: dict = {"patient_id": patient_id}
    if ctx.tenant_id and not ctx.is_platform_admin:
        count_q["tenant_id"] = ctx.tenant_id
    version = await db.patient_intake_forms.count_documents(count_q) + 1

    now = now_iso()
    doc = {
        "id": str(uuid.uuid4()),
        "patient_id": patient_id,
        "status": "draft",
        "version": version,
        "captured_by": user["id"],
        "captured_by_name": user.get("name"),
        "captured_at": None,
        "created_at": now,
        "updated_at": now,
        "clinical_intake": clinical,
        "case_details": case,
        "notes": payload.notes,
    }
    doc = stamp_for_write(doc, ctx, location_id=patient.get("location_id"))
    await db.patient_intake_forms.insert_one(_encrypt_form(doc))
    await audit_success(
        user, "intake_form.created", request,
        entity_type="patient", entity_id=patient_id, phi_accessed=True,
        metadata={"intake_form_id": doc["id"], "version": version,
                  "seeded": payload.seed_from_patient},
    )
    return _shape(doc)


@router.get("/{form_id}", response_model=IntakeFormPublic)
async def get_intake_form(
    patient_id: str,
    form_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    ctx: TenantContext = Depends(get_tenant_context),
):
    await _load_patient(patient_id, ctx)
    db = get_db_read()
    doc = await db.patient_intake_forms.find_one(
        _form_query(patient_id, form_id, ctx), {"_id": 0},
    )
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Intake form not found")
    await audit_success(
        user, "intake_form.viewed", request,
        entity_type="intake_form", entity_id=form_id,
        phi_accessed=True, metadata={"patient_id": patient_id},
    )
    return _shape(doc)


@router.patch("/{form_id}", response_model=IntakeFormPublic)
async def patch_intake_form(
    patient_id: str,
    form_id: str,
    payload: IntakeFormPatch,
    request: Request,
    user: dict = Depends(require_permission("patient_chart", "update", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    """Partial-update an intake form. Completed forms are immutable — any
    edit attempt on a completed form returns 409."""
    await _load_patient(patient_id, ctx)
    db = get_db_write()

    q = _form_query(patient_id, form_id, ctx)
    existing = await db.patient_intake_forms.find_one(q, {"_id": 0})
    if not existing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Intake form not found")
    if existing.get("status") == "completed":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Completed intake forms are immutable — create a new form instead.",
        )

    patch = payload.model_dump(exclude_unset=True)
    if not patch:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No fields to update")

    updates: dict = {}
    if "clinical_intake" in patch:
        updates["clinical_intake"] = (
            patch["clinical_intake"] if patch["clinical_intake"] is None
            else patch["clinical_intake"]
        )
    if "case_details" in patch:
        updates["case_details"] = patch["case_details"]
    if "notes" in patch:
        updates["notes"] = patch["notes"]

    # Status transition — draft → completed sets captured_at.
    now = now_iso()
    if "status" in patch and patch["status"] != existing.get("status"):
        updates["status"] = patch["status"]
        if patch["status"] == "completed":
            updates["captured_at"] = now
            updates["captured_by"] = user["id"]
            updates["captured_by_name"] = user.get("name")

    updates["updated_at"] = now
    await db.patient_intake_forms.update_one(q, {"$set": _encrypt_form(updates)})
    fresh = await db.patient_intake_forms.find_one(q, {"_id": 0})
    await audit_success(
        user, "intake_form.updated", request,
        entity_type="intake_form", entity_id=form_id,
        phi_accessed=True,
        metadata={"patient_id": patient_id, "fields": list(updates.keys())},
    )
    return _shape(fresh)


@router.delete("/{form_id}", status_code=204)
async def delete_intake_form(
    patient_id: str,
    form_id: str,
    request: Request,
    user: dict = Depends(require_permission("patient_chart", "manage", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    """Hard-delete a DRAFT intake form. Completed forms are retained per
    the clinical-records retention policy and can only be superseded."""
    await _load_patient(patient_id, ctx)
    db = get_db_write()
    q = _form_query(patient_id, form_id, ctx)
    existing = await db.patient_intake_forms.find_one(q, {"_id": 0})
    if not existing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Intake form not found")
    if existing.get("status") == "completed":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Completed intake forms cannot be deleted.",
        )
    await db.patient_intake_forms.delete_one(q)
    await audit_success(
        user, "intake_form.deleted", request,
        entity_type="intake_form", entity_id=form_id,
        phi_accessed=True, metadata={"patient_id": patient_id},
    )
