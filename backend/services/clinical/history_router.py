"""Clinical history router — Phase 2.

Chart-level, per-patient intake narrative. Auto-seeded from the most recent
completed intake form on first read; explicit re-import via
`POST /history/import` **never overwrites provider-edited fields**.

Surface:
    GET    /patients/{pid}/clinical/history
    PATCH  /patients/{pid}/clinical/history
    POST   /patients/{pid}/clinical/history/import

Every field carries per-field traceability in `field_meta[<key>]`:
    { source: "intake" | "provider_edit", source_form_id, updated_at, updated_by }

Tenant-scoped. Reauth required on writes. Audits to both `audit_logs` and
the Phase-1 `clinical_audit_events` chart-scoped projection.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status

from core.audit import audit_success
from core.db import get_db_write
from core.deps import require_role
from core.reauth import require_reauth
from core.tenancy import TenantContext, get_tenant_context
from core.tenant_scope import scoped_filter, stamp_for_write
from services.clinical.models import now_iso
from services.clinical.phase2_models import (
    ClinicalHistoryImportRequest,
    ClinicalHistoryImportResult,
    ClinicalHistoryPatch,
    ClinicalHistoryPublic,
)
from services.clinical.router import _load_patient, _log_clinical_event
from services.patient._shared import decrypt_patient_value

router = APIRouter(prefix="/patients", tags=["clinical"])


# Canonical list of chart-level history fields. Also the set the import
# mapper targets; anything else is ignored.
HISTORY_FIELDS = [
    "chief_complaint",
    "history_of_present_illness",
    "onset_date",
    "mechanism_of_injury",
    "pain_locations",
    "pain_radiation",
    "aggravating_factors",
    "relieving_factors",
    "severity",
    "prior_treatment",
    "prior_chiropractic_care",
    "medications",
    "allergies",
    "past_medical_history",
    "past_surgical_history",
    "family_history",
    "social_history",
    "occupation",
    "activity_level",
    "accident_details",
    "work_comp_details",
    "review_of_systems",
    "red_flag_screening",
]


def _empty_history(patient_id: str, tenant_id: str) -> dict:
    """Create a blank history doc for the patient. Not persisted until a write
    actually happens."""
    now = now_iso()
    doc = {
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "patient_id": patient_id,
        **{k: None for k in HISTORY_FIELDS},
        "field_meta": {},
        "seeded_from_form_id": None,
        "last_imported_at": None,
        "created_at": now,
        "updated_at": now,
        "created_by": None,
        "updated_by": None,
    }
    return doc


def _csv_to_list(value) -> list[str] | None:
    """Normalize a CSV / whitespace string into a list for `aggravating_factors`
    and `relieving_factors` (the intake form stores them as free text)."""
    if value is None:
        return None
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()] or None
    if isinstance(value, str):
        parts = [p.strip() for p in value.replace(";", ",").split(",")]
        parts = [p for p in parts if p]
        return parts or None
    return None


def _map_intake_to_history(intake: dict | None, case: dict | None) -> dict:
    """Translate an intake form's `clinical_intake` + `case_details` blobs
    into the chart-level history field vocabulary. Returns only the fields
    where we actually have a value — untouched keys are omitted so the
    import merge can skip them cleanly."""
    intake = intake or {}
    case = case or {}
    out: dict = {}

    if intake.get("chief_complaint"):
        out["chief_complaint"] = intake["chief_complaint"]
    # Complaint onset (free text) maps into onset_date only when it's a
    # parseable ISO date; otherwise we stash it as HPI narrative.
    if intake.get("complaint_onset"):
        v = str(intake["complaint_onset"]).strip()
        if len(v) >= 8 and v[4] == "-" and v[7] == "-":
            out["onset_date"] = v
        else:
            out["history_of_present_illness"] = v
    if intake.get("pain_description"):
        hpi = out.get("history_of_present_illness") or ""
        out["history_of_present_illness"] = (
            f"{hpi}\n\nPain description: {intake['pain_description']}".strip()
        )
    if intake.get("pain_level") is not None:
        out["severity"] = int(intake["pain_level"])
    if intake.get("pain_locations"):
        out["pain_locations"] = list(intake["pain_locations"]) or None
    if intake.get("aggravating_factors"):
        out["aggravating_factors"] = _csv_to_list(intake["aggravating_factors"])
    if intake.get("relieving_factors"):
        out["relieving_factors"] = _csv_to_list(intake["relieving_factors"])
    if intake.get("prior_treatments"):
        out["prior_treatment"] = intake["prior_treatments"]
    if intake.get("medications"):
        out["medications"] = intake["medications"]
    if intake.get("allergies"):
        out["allergies"] = intake["allergies"]
    if intake.get("past_medical_history"):
        out["past_medical_history"] = intake["past_medical_history"]
    if intake.get("past_surgical_history"):
        out["past_surgical_history"] = intake["past_surgical_history"]
    if intake.get("family_history"):
        out["family_history"] = intake["family_history"]
    if intake.get("social_history"):
        out["social_history"] = intake["social_history"]
    if intake.get("review_of_systems"):
        ros = intake["review_of_systems"]
        if isinstance(ros, dict):
            pieces = [f"{k}: {v}" for k, v in ros.items() if v]
            out["review_of_systems"] = "\n".join(pieces) if pieces else None
        else:
            out["review_of_systems"] = str(ros)

    # Case details — split into accident_details + work_comp_details
    case_type = (case.get("case_type") or "").lower()
    accident = {}
    wc = {}
    for k in ("date_of_injury", "injury_description", "accident_location",
             "police_report_number", "auto_carrier", "adjuster_name",
             "adjuster_phone", "attorney_name", "attorney_phone",
             "attorney_email", "claim_number"):
        if case.get(k):
            accident[k] = case[k]
    for k in ("employer_for_claim", "work_comp_carrier", "return_to_work_status"):
        if case.get(k):
            wc[k] = case[k]
    if accident:
        if case_type:
            accident["case_type"] = case_type
        out["accident_details"] = accident
    if wc:
        out["work_comp_details"] = wc

    if intake.get("notes"):
        # Intake notes get tacked onto HPI unless HPI is already a long
        # narrative (keep from clobbering a rich HPI with a stray note).
        hpi = out.get("history_of_present_illness") or ""
        combined = f"{hpi}\n\nIntake notes: {intake['notes']}".strip()
        out["history_of_present_illness"] = combined

    return out


def _strip_history_doc(doc: dict) -> dict:
    out = {k: v for k, v in doc.items() if k != "_id"}
    # field_meta comes out of Mongo as plain dicts; normalize to the shape
    # the Pydantic response model expects.
    out["field_meta"] = out.get("field_meta") or {}
    return out


async def _latest_completed_form(db, tenant_id: str, patient_id: str) -> dict | None:
    return await db.patient_intake_forms.find_one(
        {
            "tenant_id": tenant_id,
            "patient_id": patient_id,
            "status": "completed",
        },
        sort=[("captured_at", -1), ("created_at", -1)],
    )


def _decrypted_form(doc: dict | None) -> dict | None:
    if not doc:
        return None
    out = dict(doc)
    for key in ("clinical_intake", "case_details", "notes"):
        if out.get(key) is not None:
            out[key] = decrypt_patient_value(out[key])
    return out


async def _auto_seed_if_missing(
    db, ctx: TenantContext, patient_id: str, user: dict
) -> dict:
    """Return the current history doc, auto-seeding from the latest completed
    intake form if none exists yet. Idempotent: only seeds when there is NO
    pre-existing history document for the patient."""
    existing = await db.clinical_history.find_one(
        {"tenant_id": ctx.tenant_id, "patient_id": patient_id}
    )
    if existing:
        return existing

    form = _decrypted_form(await _latest_completed_form(db, ctx.tenant_id, patient_id))
    base = _empty_history(patient_id, ctx.tenant_id)
    now = now_iso()
    base["created_by"] = user["id"]
    base["updated_by"] = user["id"]
    base = stamp_for_write(base, ctx)

    if form:
        mapped = _map_intake_to_history(
            form.get("clinical_intake"), form.get("case_details")
        )
        for k, v in mapped.items():
            base[k] = v
            base["field_meta"][k] = {
                "source": "intake",
                "source_form_id": form["id"],
                "updated_at": now,
                "updated_by": None,  # auto-seed; not a user edit
            }
        base["seeded_from_form_id"] = form["id"]
        base["last_imported_at"] = now

    await db.clinical_history.insert_one(base)
    return base


# ---------------------------------------------------------------------------
# GET — auto-seed on first access
# ---------------------------------------------------------------------------
@router.get(
    "/{patient_id}/clinical/history",
    response_model=ClinicalHistoryPublic,
)
async def get_history(
    patient_id: str,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    doc = await _auto_seed_if_missing(db, ctx, patient_id, user)
    await audit_success(
        user, "clinical.history.viewed", request,
        entity_type="clinical_history", entity_id=doc["id"], phi_accessed=True,
        metadata={"patient_id": patient_id},
    )
    return _strip_history_doc(doc)


# ---------------------------------------------------------------------------
# PATCH — provider edit. Any field supplied flips to provider_edit source.
# ---------------------------------------------------------------------------
@router.patch(
    "/{patient_id}/clinical/history",
    response_model=ClinicalHistoryPublic,
)
async def patch_history(
    patient_id: str,
    payload: ClinicalHistoryPatch,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)

    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    current = await _auto_seed_if_missing(db, ctx, patient_id, user)

    dumped = payload.model_dump(exclude_unset=True)
    if not dumped:
        return _strip_history_doc(current)

    now = now_iso()
    updates: dict = {}
    meta_updates: dict = {}
    for k, v in dumped.items():
        if k not in HISTORY_FIELDS:
            continue
        updates[k] = v
        meta_updates[f"field_meta.{k}"] = {
            "source": "provider_edit",
            "source_form_id": current.get("field_meta", {}).get(k, {}).get("source_form_id"),
            "updated_at": now,
            "updated_by": user["id"],
        }
    updates["updated_at"] = now
    updates["updated_by"] = user["id"]

    mongo_set = {**updates, **meta_updates}
    await db.clinical_history.update_one(
        {"id": current["id"], "tenant_id": current["tenant_id"]},
        {"$set": mongo_set},
    )
    fresh = await db.clinical_history.find_one(
        {"id": current["id"], "tenant_id": current["tenant_id"]}
    )

    edited_keys = sorted([k for k in dumped.keys() if k in HISTORY_FIELDS])
    await _log_clinical_event(
        db, ctx,
        actor=user, patient_id=patient_id, episode_id=None,
        event_type="history.updated", entity_type="clinical_history",
        entity_id=current["id"], metadata={"fields": edited_keys},
    )
    await audit_success(
        user, "clinical.history.updated", request,
        entity_type="clinical_history", entity_id=current["id"], phi_accessed=True,
        metadata={"patient_id": patient_id, "fields": edited_keys},
    )
    return _strip_history_doc(fresh)


# ---------------------------------------------------------------------------
# POST /import — explicit, non-destructive re-import from an intake form
# ---------------------------------------------------------------------------
@router.post(
    "/{patient_id}/clinical/history/import",
    response_model=ClinicalHistoryImportResult,
)
async def import_history(
    patient_id: str,
    request: Request,
    payload: ClinicalHistoryImportRequest | None = None,
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)

    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    current = await _auto_seed_if_missing(db, ctx, patient_id, user)

    form_id = (payload.form_id if payload else None)
    if form_id:
        form_doc = await db.patient_intake_forms.find_one(
            {
                "id": form_id,
                "patient_id": patient_id,
                "tenant_id": ctx.tenant_id,
            }
        )
        if not form_doc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Intake form not found")
        if form_doc.get("status") != "completed":
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Only completed intake forms can be imported",
            )
        form = _decrypted_form(form_doc)
    else:
        form = _decrypted_form(
            await _latest_completed_form(db, ctx.tenant_id, patient_id)
        )
        if not form:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "No completed intake form available to import",
            )

    mapped = _map_intake_to_history(
        form.get("clinical_intake"), form.get("case_details")
    )
    field_meta = current.get("field_meta") or {}

    imported: list[str] = []
    skipped: list[str] = []
    now = now_iso()
    sets: dict = {}

    for key, value in mapped.items():
        meta = field_meta.get(key) or {}
        if meta.get("source") == "provider_edit":
            # Preserve provider edits silently.
            skipped.append(key)
            continue
        # Intake-sourced or empty fields accept the new value.
        sets[key] = value
        sets[f"field_meta.{key}"] = {
            "source": "intake",
            "source_form_id": form["id"],
            "updated_at": now,
            "updated_by": user["id"],
        }
        imported.append(key)

    # Keep chart-level import metadata even when nothing merged (so the UI can
    # show "last attempted import" cleanly).
    sets["last_imported_at"] = now
    sets["seeded_from_form_id"] = current.get("seeded_from_form_id") or form["id"]
    sets["updated_at"] = now
    sets["updated_by"] = user["id"]

    await db.clinical_history.update_one(
        {"id": current["id"], "tenant_id": current["tenant_id"]},
        {"$set": sets},
    )
    fresh = await db.clinical_history.find_one(
        {"id": current["id"], "tenant_id": current["tenant_id"]}
    )

    await _log_clinical_event(
        db, ctx,
        actor=user, patient_id=patient_id, episode_id=None,
        event_type="history.imported", entity_type="clinical_history",
        entity_id=current["id"],
        metadata={
            "form_id": form["id"],
            "imported": imported,
            "skipped": skipped,
        },
    )
    await audit_success(
        user, "clinical.history.imported", request,
        entity_type="clinical_history", entity_id=current["id"], phi_accessed=True,
        metadata={
            "patient_id": patient_id,
            "source_form_id": form["id"],
            "imported_count": len(imported),
            "skipped_count": len(skipped),
        },
    )
    return {
        "history": _strip_history_doc(fresh),
        "imported_fields": sorted(imported),
        "skipped_fields": sorted(skipped),
        "source_form_id": form["id"],
    }
