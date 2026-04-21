"""Clinical Media router — Phase 7.

Imaging + clinical photos + outside records. Reuses the proven
`core.object_storage` + libmagic pattern from `patient/documents_router.py`.
Metadata is editable post-upload; **file bytes are immutable**.

Endpoints (mounted under `/api`):
    POST   /api/patients/{pid}/clinical/media              (multipart)
    GET    /api/patients/{pid}/clinical/media
    GET    /api/patients/{pid}/clinical/media/{mid}
    PATCH  /api/patients/{pid}/clinical/media/{mid}
    GET    /api/patients/{pid}/clinical/media/{mid}/download
    DELETE /api/patients/{pid}/clinical/media/{mid}        (soft-delete)

`annotation_overlay` is reserved `None` for future markup — schema-only
in Phase 7 (no UI).
"""
from __future__ import annotations

import logging
import tempfile
import uuid
from typing import Literal

import magic
from fastapi import (
    APIRouter, Depends, File, Form, HTTPException, Query, Request,
    Response, UploadFile, status,
)
from pydantic import BaseModel, ConfigDict, Field

from core import object_storage
from core.audit import audit_success
from core.db import get_db_write
from core.deps import require_role
from core.reauth import require_reauth
from core.tenancy import TenantContext, get_tenant_context
from core.tenant_scope import scoped_filter, stamp_for_write
from services.clinical.models import now_iso
from services.clinical.router import _load_patient, _log_clinical_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/patients", tags=["clinical"])

# Size + MIME constants mirror patient/documents_router
MAX_MEDIA_BYTES = 25 * 1024 * 1024  # 25 MB — clinical imaging PDFs run larger than IDs
READ_CHUNK = 64 * 1024
SNIFF_BYTES = 4096
SPOOL_TO_DISK_AT = 1 * 1024 * 1024

ALLOWED_MIMES = {
    "image/jpeg", "image/png", "image/webp", "image/heic", "image/heif",
    "application/pdf",
}
SNIFF_ALIASES = {
    "image/heif-sequence": "image/heif",
    "image/heic-sequence": "image/heic",
    "image/x-hevc": "image/heic",
}

MEDIA_CATEGORY = Literal[
    "xray", "mri_ct_report", "ultrasound", "clinical_photo",
    "outside_record", "other_pdf",
]
MEDIA_SOURCE = Literal[
    "in_clinic", "outside_imaging_center", "patient_provided",
    "records_request",
]


class MediaPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    tenant_id: str
    location_id: str | None = None
    patient_id: str
    episode_id: str | None = None
    episode_title: str | None = None
    appointment_id: str | None = None
    encounter_id: str | None = None
    linked_treatment_plan_id: str | None = None
    linked_diagnosis_ids: list[str] = Field(default_factory=list)
    category: MEDIA_CATEGORY
    source: MEDIA_SOURCE | None = None
    body_region: str | None = None
    study_date: str | None = None
    impression_findings: str | None = None
    original_filename: str
    mime_type: str
    size_bytes: int
    uploaded_by: str | None = None
    uploaded_at: str
    deleted_at: str | None = None
    deleted_by: str | None = None
    annotation_overlay: dict | None = None  # reserved for future annotation UI
    created_at: str
    updated_at: str


class MediaUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: MEDIA_CATEGORY | None = None
    source: MEDIA_SOURCE | None = None
    body_region: str | None = Field(default=None, max_length=120)
    study_date: str | None = None
    impression_findings: str | None = Field(default=None, max_length=4000)
    episode_id: str | None = None
    appointment_id: str | None = None
    encounter_id: str | None = None
    linked_treatment_plan_id: str | None = None
    linked_diagnosis_ids: list[str] | None = None


def _sniff_mime(sample: bytes) -> str:
    try:
        sniffed = magic.from_buffer(sample, mime=True) or ""
    except magic.MagicException as exc:
        logger.warning("libmagic failed to sniff upload: %s", exc)
        return ""
    sniffed = sniffed.lower().split(";")[0].strip()
    return SNIFF_ALIASES.get(sniffed, sniffed)


async def _stream_to_spool(
    file: UploadFile,
) -> tuple[tempfile.SpooledTemporaryFile, int, bytes]:
    spool = tempfile.SpooledTemporaryFile(max_size=SPOOL_TO_DISK_AT, mode="w+b")
    total = 0
    sniff_head = b""
    try:
        while True:
            chunk = await file.read(READ_CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_MEDIA_BYTES:
                spool.close()
                raise HTTPException(
                    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    "File exceeds 25 MB cap",
                )
            if len(sniff_head) < SNIFF_BYTES:
                sniff_head += chunk[: SNIFF_BYTES - len(sniff_head)]
            spool.write(chunk)
    except HTTPException:
        raise
    except Exception:
        spool.close()
        raise
    if total == 0:
        spool.close()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty upload")
    spool.seek(0)
    return spool, total, sniff_head


async def _validate_links(
    db, ctx: TenantContext, patient_id: str,
    *, episode_id: str | None, appointment_id: str | None,
    encounter_id: str | None, plan_id: str | None,
    diagnosis_ids: list[str] | None,
) -> None:
    if episode_id:
        ep = await db.clinical_episode_cases.find_one(
            {"tenant_id": ctx.tenant_id, "patient_id": patient_id, "id": episode_id},
            {"_id": 0, "id": 1},
        )
        if not ep:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Episode not found on patient")
    if encounter_id:
        enc = await db.clinical_encounters.find_one(
            {"tenant_id": ctx.tenant_id, "patient_id": patient_id, "id": encounter_id},
            {"_id": 0, "id": 1},
        )
        if not enc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Encounter not found on patient")
    if appointment_id:
        appt = await db.appointments.find_one(
            {"tenant_id": ctx.tenant_id, "patient_id": patient_id, "id": appointment_id},
            {"_id": 0, "id": 1},
        )
        if not appt:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Appointment not found on patient")
    if plan_id:
        plan = await db.clinical_treatment_plans.find_one(
            {"tenant_id": ctx.tenant_id, "patient_id": patient_id, "id": plan_id},
            {"_id": 0, "id": 1},
        )
        if not plan:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Treatment plan not found on patient")
    if diagnosis_ids:
        ids = list(dict.fromkeys(diagnosis_ids))
        cursor = db.clinical_diagnoses.find(
            {"tenant_id": ctx.tenant_id, "patient_id": patient_id, "id": {"$in": ids}},
            {"_id": 0, "id": 1},
        )
        found = {d["id"] async for d in cursor}
        missing = [i for i in ids if i not in found]
        if missing:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Diagnoses not on this patient: {missing}",
            )


def _strip(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k != "_id" and k != "storage_path"}


async def _hydrate(db, ctx: TenantContext, doc: dict) -> dict:
    out = _strip(doc)
    if out.get("episode_id"):
        ep = await db.clinical_episode_cases.find_one(
            {"id": out["episode_id"], "tenant_id": ctx.tenant_id},
            {"_id": 0, "title": 1},
        )
        if ep:
            out["episode_title"] = ep.get("title")
    return out


async def _load(db, ctx: TenantContext, patient_id: str, mid: str) -> dict:
    q = scoped_filter({"id": mid, "patient_id": patient_id}, ctx, location_scoped=False)
    if q.get("__deny__"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Media not found")
    doc = await db.clinical_media.find_one(q, {"_id": 0})
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Media not found")
    return doc


# ---------------------------------------------------------------------------
# POST /media  — multipart upload
# ---------------------------------------------------------------------------
@router.post("/{patient_id}/clinical/media", response_model=MediaPublic, status_code=201)
async def upload_clinical_media(
    patient_id: str,
    request: Request,
    file: UploadFile = File(...),
    category: str = Form(...),
    source: str | None = Form(None),
    body_region: str | None = Form(None),
    study_date: str | None = Form(None),
    impression_findings: str | None = Form(None),
    episode_id: str | None = Form(None),
    appointment_id: str | None = Form(None),
    encounter_id: str | None = Form(None),
    linked_treatment_plan_id: str | None = Form(None),
    linked_diagnosis_ids: str | None = Form(None),  # comma-separated
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)
    if category not in [
        "xray", "mri_ct_report", "ultrasound", "clinical_photo",
        "outside_record", "other_pdf",
    ]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unsupported category `{category}`")
    if source and source not in [
        "in_clinic", "outside_imaging_center", "patient_provided", "records_request",
    ]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unsupported source `{source}`")

    declared = (file.content_type or "").lower().split(";")[0].strip()
    if declared not in ALLOWED_MIMES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unsupported file type `{declared}`",
        )

    db = get_db_write()
    await _load_patient(db, patient_id, ctx)

    diag_ids = (
        [s.strip() for s in linked_diagnosis_ids.split(",") if s.strip()]
        if linked_diagnosis_ids else []
    )
    await _validate_links(
        db, ctx, patient_id,
        episode_id=episode_id, appointment_id=appointment_id,
        encounter_id=encounter_id, plan_id=linked_treatment_plan_id,
        diagnosis_ids=diag_ids,
    )

    spool, size, sniff_head = await _stream_to_spool(file)
    try:
        sniffed = _sniff_mime(sniff_head)
        if sniffed and sniffed not in ALLOWED_MIMES:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"File content does not match an allowed type (detected `{sniffed}`).",
            )
        if sniffed and sniffed != declared:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Declared content-type `{declared}` does not match sniffed `{sniffed}`.",
            )

        ext = ""
        if file.filename and "." in file.filename:
            ext = file.filename.rsplit(".", 1)[-1][:10]
        mid = str(uuid.uuid4())
        storage_path = object_storage.storage_path_for(
            ctx.tenant_id or "default", patient_id, mid, ext,
        )
        spool.seek(0)
        payload = spool.read()
        try:
            result = object_storage.put_object(storage_path, payload, declared)
        except object_storage.StorageUnavailable as exc:
            logger.error("object storage init/put failed: %s", exc)
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Media storage unavailable")
        except Exception as exc:  # noqa: BLE001
            logger.exception("clinical media upload failed")
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Upload failed: {exc}")

        now = now_iso()
        doc = {
            "id": mid,
            "patient_id": patient_id,
            "episode_id": episode_id,
            "appointment_id": appointment_id,
            "encounter_id": encounter_id,
            "linked_treatment_plan_id": linked_treatment_plan_id,
            "linked_diagnosis_ids": diag_ids,
            "category": category,
            "source": source,
            "body_region": (body_region or "").strip() or None,
            "study_date": study_date,
            "impression_findings": (impression_findings or "").strip() or None,
            "original_filename": (file.filename or "").strip()[:255] or f"{mid}.{ext or 'bin'}",
            "mime_type": declared,
            "size_bytes": result.get("size", size),
            "storage_path": result.get("path") or storage_path,
            "uploaded_by": user["id"],
            "uploaded_at": now,
            "deleted_at": None,
            "deleted_by": None,
            "annotation_overlay": None,
            "created_at": now,
            "updated_at": now,
        }
        doc = stamp_for_write(doc, ctx)
        await db.clinical_media.insert_one(doc)

        await _log_clinical_event(
            db, ctx, actor=user, patient_id=patient_id, episode_id=episode_id,
            event_type="clinical_media.uploaded",
            entity_type="clinical_media", entity_id=mid,
            metadata={"category": category, "mime": declared, "size": doc["size_bytes"]},
        )
        await audit_success(
            user, "clinical.media.uploaded", request,
            entity_type="clinical_media", entity_id=mid, phi_accessed=True,
            metadata={"patient_id": patient_id, "category": category, "size": doc["size_bytes"]},
        )
        return await _hydrate(db, ctx, doc)
    finally:
        spool.close()


# ---------------------------------------------------------------------------
# List / Read
# ---------------------------------------------------------------------------
@router.get("/{patient_id}/clinical/media", response_model=list[MediaPublic])
async def list_media(
    patient_id: str,
    request: Request,
    category: str | None = Query(default=None),
    episode_id: str | None = Query(default=None),
    include_deleted: bool = Query(default=False),
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    q: dict = scoped_filter({"patient_id": patient_id}, ctx, location_scoped=False)
    if q.get("__deny__"):
        return []
    if not include_deleted or user.get("role") != "admin":
        q["deleted_at"] = None
    if category:
        q["category"] = category
    if episode_id:
        q["episode_id"] = episode_id
    cursor = db.clinical_media.find(q, {"_id": 0}).sort("study_date", -1)
    rows = [d async for d in cursor]
    hydrated = [await _hydrate(db, ctx, d) for d in rows]
    await audit_success(
        user, "clinical.media.list_viewed", request,
        entity_type="patient", entity_id=patient_id, phi_accessed=True,
        metadata={"count": len(rows)},
    )
    return hydrated


@router.get("/{patient_id}/clinical/media/{mid}", response_model=MediaPublic)
async def get_media(
    patient_id: str, mid: str, request: Request,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    doc = await _load(db, ctx, patient_id, mid)
    await audit_success(
        user, "clinical.media.read", request,
        entity_type="clinical_media", entity_id=mid, phi_accessed=True,
    )
    return await _hydrate(db, ctx, doc)


@router.patch("/{patient_id}/clinical/media/{mid}", response_model=MediaPublic)
async def patch_media(
    patient_id: str, mid: str, payload: MediaUpdate, request: Request,
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    current = await _load(db, ctx, patient_id, mid)
    if current.get("deleted_at"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Media not found")

    dumped = payload.model_dump(exclude_unset=True)
    if not dumped:
        return await _hydrate(db, ctx, current)

    await _validate_links(
        db, ctx, patient_id,
        episode_id=dumped.get("episode_id", current.get("episode_id")),
        appointment_id=dumped.get("appointment_id", current.get("appointment_id")),
        encounter_id=dumped.get("encounter_id", current.get("encounter_id")),
        plan_id=dumped.get("linked_treatment_plan_id", current.get("linked_treatment_plan_id")),
        diagnosis_ids=dumped.get("linked_diagnosis_ids"),
    )

    now = now_iso()
    dumped["updated_at"] = now
    await db.clinical_media.update_one(
        {"id": mid, "tenant_id": current["tenant_id"]},
        {"$set": dumped},
    )
    fresh = await db.clinical_media.find_one(
        {"id": mid, "tenant_id": current["tenant_id"]}, {"_id": 0},
    )
    await _log_clinical_event(
        db, ctx, actor=user, patient_id=patient_id, episode_id=fresh.get("episode_id"),
        event_type="clinical_media.updated", entity_type="clinical_media", entity_id=mid,
        metadata={"fields": sorted(k for k in dumped.keys() if k != "updated_at")},
    )
    await audit_success(
        user, "clinical.media.updated", request,
        entity_type="clinical_media", entity_id=mid, phi_accessed=True,
        metadata={"patient_id": patient_id},
    )
    return await _hydrate(db, ctx, fresh)


@router.get("/{patient_id}/clinical/media/{mid}/download")
async def download_media(
    patient_id: str, mid: str, request: Request,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    doc = await _load(db, ctx, patient_id, mid)
    if doc.get("deleted_at"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Media not found")
    try:
        data, ct = object_storage.get_object(doc["storage_path"])
    except object_storage.StorageUnavailable as exc:
        logger.error("object storage init/get failed: %s", exc)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Media storage unavailable")
    except Exception as exc:  # noqa: BLE001
        logger.exception("clinical media download failed")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Download failed: {exc}")
    await audit_success(
        user, "clinical.media.downloaded", request,
        entity_type="clinical_media", entity_id=mid, phi_accessed=True,
    )
    mt = doc.get("mime_type") or ct or "application/octet-stream"
    return Response(
        content=data, media_type=mt,
        headers={
            "Content-Disposition":
                "inline" if mt.startswith("image/") or mt == "application/pdf"
                else f"attachment; filename=\"{doc.get('original_filename', 'media')}\"",
        },
    )


@router.delete("/{patient_id}/clinical/media/{mid}", status_code=204)
async def delete_media(
    patient_id: str, mid: str, request: Request,
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    current = await _load(db, ctx, patient_id, mid)
    if current.get("deleted_at"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Media not found")
    now = now_iso()
    await db.clinical_media.update_one(
        {"id": mid, "tenant_id": current["tenant_id"]},
        {"$set": {"deleted_at": now, "deleted_by": user["id"], "updated_at": now}},
    )
    await _log_clinical_event(
        db, ctx, actor=user, patient_id=patient_id, episode_id=current.get("episode_id"),
        event_type="clinical_media.deleted", entity_type="clinical_media", entity_id=mid,
    )
    await audit_success(
        user, "clinical.media.deleted", request,
        entity_type="clinical_media", entity_id=mid, phi_accessed=True,
    )
    return Response(status_code=204)
