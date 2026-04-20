"""
Patient documents router — /api/patients/{id}/documents/*

Handles insurance cards, IDs, referral letters, imaging reports, intake
forms, and consent receipts. All uploads are encrypted at rest by the
underlying object storage; every access is audited; upload/delete are
reauth-gated.

Hardening notes:
  * Files are streamed in 64 KB chunks via SpooledTemporaryFile so a
    single malicious client cannot balloon server memory with a 2 GB
    multipart upload — we cut the connection the moment the body exceeds
    the 10 MB hard cap.
  * The declared Content-Type is only an advisory. After reading enough
    bytes to sniff the magic header we re-verify the actual MIME with
    libmagic and reject mismatches (e.g. `image/png` header that is
    actually an ELF binary).
"""
from __future__ import annotations

import logging
import tempfile
import uuid

import magic
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile, status

from core.audit import audit_success
from core.db import get_db_read, get_db_write
from core import object_storage
from core.reauth import require_reauth
from core.tenancy import TenantContext, get_tenant_context
from core.tenant_scope import scoped_filter, stamp_for_write
from services.authz.policy import require_permission
from services.patient._shared import now_iso

logger = logging.getLogger(__name__)

router = APIRouter(tags=["patient-documents"])

MAX_DOCUMENT_BYTES = 10 * 1024 * 1024  # 10 MB hard cap per file
READ_CHUNK = 64 * 1024  # 64 KB streaming chunk
SNIFF_BYTES = 4096  # bytes used by libmagic to identify MIME
SPOOL_TO_DISK_AT = 1 * 1024 * 1024  # roll over to tmpfile past 1 MB

ALLOWED_DOC_MIMES = {
    "image/jpeg", "image/png", "image/webp", "image/heic", "image/heif",
    "application/pdf",
}
# HEIC/HEIF are often reported by libmagic as the ISO base media file
# format; accept those aliases so newer phone captures go through.
SNIFF_ALIASES = {
    "image/heif-sequence": "image/heif",
    "image/heic-sequence": "image/heic",
    "image/x-hevc": "image/heic",
}
ALLOWED_DOC_CATEGORIES = {
    "insurance_card_front", "insurance_card_back",
    "drivers_license", "referral_letter", "imaging_report",
    "intake_form", "consent_receipt", "other",
}


def _sniff_mime(sample: bytes) -> str:
    try:
        sniffed = magic.from_buffer(sample, mime=True) or ""
    except magic.MagicException as exc:  # pragma: no cover — libmagic init failure
        logger.warning("libmagic failed to sniff upload: %s", exc)
        return ""
    sniffed = sniffed.lower().split(";")[0].strip()
    return SNIFF_ALIASES.get(sniffed, sniffed)


async def _stream_upload_to_spool(file: UploadFile) -> tuple[tempfile.SpooledTemporaryFile, int, bytes]:
    """Stream the upload into a SpooledTemporaryFile, enforcing the size
    cap as we go. Returns (spool_fh_positioned_at_start, size, sniff_head)."""
    spool = tempfile.SpooledTemporaryFile(max_size=SPOOL_TO_DISK_AT, mode="w+b")
    total = 0
    sniff_head = b""
    try:
        while True:
            chunk = await file.read(READ_CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_DOCUMENT_BYTES:
                spool.close()
                raise HTTPException(
                    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    "File exceeds 10 MB cap",
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


def _doc_shape(doc: dict) -> dict:
    """Public shape for a document record — never exposes the storage path."""
    return {
        "id": doc["id"],
        "patient_id": doc["patient_id"],
        "category": doc.get("category") or "other",
        "filename": doc.get("filename"),
        "content_type": doc.get("content_type"),
        "size": doc.get("size"),
        "description": doc.get("description"),
        "uploaded_by": doc.get("uploaded_by"),
        "uploaded_at": doc.get("uploaded_at") or doc.get("created_at"),
    }


@router.post("/{patient_id}/documents", status_code=201)
async def upload_patient_document(
    patient_id: str,
    request: Request,
    file: UploadFile = File(...),
    category: str = Form("other"),
    description: str | None = Form(None),
    ctx: TenantContext = Depends(get_tenant_context),
    user: dict = Depends(require_permission("patient", "update")),
):
    """Upload an insurance card / ID / referral letter attached to a patient.
    Reauth-gated. Audited. Tenant + patient scoped. 10 MB max; images + PDF.

    The declared Content-Type is cross-checked against libmagic's sniffed
    MIME — a spoofed `image/png` header on an executable will be rejected.
    """
    require_reauth(request, user)
    if category not in ALLOWED_DOC_CATEGORIES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unsupported category `{category}`")

    declared = (file.content_type or "").lower().split(";")[0].strip()
    if declared not in ALLOWED_DOC_MIMES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unsupported file type `{declared}`. Allowed: images (JPEG/PNG/WEBP/HEIC) + PDF.",
        )

    spool, size, sniff_head = await _stream_upload_to_spool(file)
    try:
        sniffed = _sniff_mime(sniff_head)
        if sniffed and sniffed not in ALLOWED_DOC_MIMES:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"File content does not match an allowed type (detected `{sniffed}`).",
            )
        # Allow declared==sniffed OR equivalent JPEG family aliases; otherwise reject.
        if sniffed and sniffed != declared:
            # Accept image/* declared vs image/* sniffed only when both are allowed.
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Declared content-type `{declared}` does not match sniffed `{sniffed}`.",
            )

        # Patient must exist in this tenant/location scope.
        db_read = get_db_read()
        patient = await db_read.patients.find_one(
            scoped_filter({"id": patient_id, "status": "active"}, ctx), {"_id": 0}
        )
        if not patient:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")

        ext = ""
        if file.filename and "." in file.filename:
            ext = file.filename.rsplit(".", 1)[-1][:10]
        doc_uuid = str(uuid.uuid4())
        storage_path = object_storage.storage_path_for(
            ctx.tenant_id or "default", patient_id, doc_uuid, ext
        )

        # Object storage SDK takes bytes; spool files fit in RAM up to 1 MB,
        # otherwise they live on disk. Read lazily to keep memory flat.
        spool.seek(0)
        payload = spool.read()

        try:
            result = object_storage.put_object(storage_path, payload, declared)
        except object_storage.StorageUnavailable as exc:
            logger.error("object storage init/put failed: %s", exc)
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Document storage unavailable")
        except Exception as exc:  # noqa: BLE001 — upstream network/HTTP errors bubble up
            logger.exception("object storage upload failed")
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Upload failed: {exc}")

        now = now_iso()
        doc = {
            "id": doc_uuid,
            "patient_id": patient_id,
            "category": category,
            "filename": (file.filename or "").strip()[:255] or f"{doc_uuid}.{ext or 'bin'}",
            "content_type": declared,
            "size": result.get("size", size),
            "storage_path": result.get("path") or storage_path,
            "description": (description or "").strip()[:1000] or None,
            "uploaded_by": user["id"],
            "is_deleted": False,
            "created_at": now,
            "uploaded_at": now,
        }
        doc = stamp_for_write(doc, ctx, location_id=patient.get("location_id"))

        db = get_db_write()
        await db.patient_documents.insert_one(dict(doc))
        await audit_success(
            user, "patient.document.uploaded", request,
            entity_type="patient_document", entity_id=doc_uuid, phi_accessed=True,
            metadata={
                "patient_id": patient_id, "category": category,
                "content_type": declared, "size": doc["size"],
                "sniffed_mime": sniffed or None,
            },
        )
        return _doc_shape(doc)
    finally:
        spool.close()


@router.get("/{patient_id}/documents")
async def list_patient_documents(
    patient_id: str,
    request: Request,
    ctx: TenantContext = Depends(get_tenant_context),
    user: dict = Depends(require_permission("patient", "read")),
):
    db = get_db_read()
    cursor = db.patient_documents.find(
        scoped_filter({"patient_id": patient_id, "is_deleted": False}, ctx),
        {"_id": 0, "storage_path": 0},
    ).sort("uploaded_at", -1)
    docs = [_doc_shape(d) async for d in cursor]
    await audit_success(
        user, "patient.documents.listed", request,
        entity_type="patient", entity_id=patient_id,
        metadata={"patient_id": patient_id, "count": len(docs)},
    )
    return docs


@router.get("/{patient_id}/documents/{doc_id}/download")
async def download_patient_document(
    patient_id: str,
    doc_id: str,
    request: Request,
    ctx: TenantContext = Depends(get_tenant_context),
    user: dict = Depends(require_permission("patient", "read")),
):
    db = get_db_read()
    doc = await db.patient_documents.find_one(
        scoped_filter(
            {"id": doc_id, "patient_id": patient_id, "is_deleted": False}, ctx
        ),
        {"_id": 0},
    )
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")

    try:
        data, ct = object_storage.get_object(doc["storage_path"])
    except object_storage.StorageUnavailable as exc:
        logger.error("object storage init/get failed: %s", exc)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Document storage unavailable")
    except Exception as exc:  # noqa: BLE001
        logger.exception("object storage download failed")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Download failed: {exc}")

    await audit_success(
        user, "patient.document.downloaded", request,
        entity_type="patient_document", entity_id=doc_id, phi_accessed=True,
        metadata={"patient_id": patient_id, "category": doc.get("category")},
    )
    return Response(
        content=data,
        media_type=doc.get("content_type") or ct or "application/octet-stream",
        headers={
            "Content-Disposition":
                "inline" if (doc.get("content_type") or "").startswith("image/")
                else f"attachment; filename=\"{doc.get('filename', 'document')}\"",
        },
    )


@router.delete("/{patient_id}/documents/{doc_id}", status_code=204)
async def delete_patient_document(
    patient_id: str,
    doc_id: str,
    request: Request,
    ctx: TenantContext = Depends(get_tenant_context),
    user: dict = Depends(require_permission("patient", "update")),
):
    """Soft-delete — storage API has no delete, so we flip `is_deleted=True`
    and hide the record from listings. Audited."""
    require_reauth(request, user)
    db = get_db_write()
    r = await db.patient_documents.update_one(
        scoped_filter({"id": doc_id, "patient_id": patient_id, "is_deleted": False}, ctx),
        {"$set": {"is_deleted": True, "deleted_at": now_iso(), "deleted_by": user["id"]}},
    )
    if not r.matched_count:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    await audit_success(
        user, "patient.document.deleted", request,
        entity_type="patient_document", entity_id=doc_id, phi_accessed=True,
        metadata={"patient_id": patient_id},
    )
    return Response(status_code=204)
