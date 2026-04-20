"""
Patient Service router — /api/patients/* (HIPAA-hardened).

Controls:
  - PHI free-text fields are encrypted at rest (AES-256-GCM).
  - List + detail default to MASKED PHI. `?unmask=true` returns cleartext but is
    always audited and requires a `reason` (≥ 8 chars) for non-admin callers.
  - Non-admin detail access requires a `reason` query parameter (break-glass).
  - DELETE is a 7-year soft-delete (status='deleted', retention_until=+7y) and
    requires step-up re-authentication via core.reauth.
  - Adding medical records requires reauth (sensitive PHI mutation).
  - Export endpoint returns a signed JSON blob of everything we hold on the
    patient (right-to-access).

Sub-routers (included at the bottom, same /patients prefix):
  - `documents_router` — insurance card / ID / referral uploads + downloads.
  - `consent_pdf_router` — signed-consent PDF generation.
"""
import logging
import uuid
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from core.audit import audit_emergency, audit_success
from core import cache, cache_keys
from core.crypto import decrypt_fields, encrypt_fields
from core.db import get_db_read, get_db_write, read_after_write_db
from core.deps import get_current_user
from core.masking import mask_patient
from core.reauth import require_reauth
from core.tenancy import TenantContext, get_tenant_context
from core.tenant_scope import scoped_filter, stamp_for_write
from services.authz.policy import require_permission
from services.patient._shared import (
    PATIENT_ENCRYPTED,
    REASON_MIN_LENGTH,
    _patient_repo,
    decrypt_patient_doc,
    encrypt_patient_doc,
    enforce_reason,
    now_iso,
)
from services.patient.consent_pdf_router import router as consent_pdf_router
from services.patient.documents_router import router as documents_router
from services.patient.search_router import router as search_router
from services.patient.models import (
    MedicalRecordCoding,
    MedicalRecordCreate,
    MedicalRecordPublic,
    PatientCreate,
    PatientPublic,
    PatientUpdate,
)

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/patients", tags=["patient"])

# Sub-routers are included here — before `/{patient_id}` routes below — so
# their specific paths (e.g. `/search`, `/{id}/documents`) take precedence
# over the generic `/{patient_id}` matcher.
router.include_router(search_router)
router.include_router(documents_router)
router.include_router(consent_pdf_router)

STAFF_ROLES = ("admin", "doctor", "staff")

RECORD_ENCRYPTED = ["description", "diagnosis", "treatment"]
RETENTION_YEARS = 7

# Output-only shape keys that the frontend/schema may read.
PATIENT_GROUPED_KEYS = [
    "demographics",
    "contact",
    "address_details",
    "emergency_contact_details",
    "admin",
    "guarantor",
    "insurance",
    "clinical_intake",
    "case_details",
    "consents",
]


def _now() -> str:
    return now_iso()


def _address_to_string(addr: dict) -> str | None:
    """Flatten a structured address into the single-line legacy format the
    existing UI renders. Returns None if the address is empty."""
    if not isinstance(addr, dict):
        return None
    parts = [
        addr.get("line1"),
        addr.get("line2"),
        ", ".join(
            p for p in [addr.get("city"), addr.get("state")] if p
        ) or None,
        addr.get("postal_code"),
        addr.get("country"),
    ]
    joined = ", ".join(p for p in parts if p)
    return joined or None


def _emergency_contact_to_string(ec: dict) -> str | None:
    if not isinstance(ec, dict):
        return None
    label_parts = [ec.get("name")]
    if ec.get("relationship"):
        label_parts.append(f"({ec['relationship']})")
    contact_parts = [p for p in [ec.get("phone"), ec.get("email")] if p]
    head = " ".join(p for p in label_parts if p).strip()
    tail = " / ".join(contact_parts)
    combined = " · ".join(p for p in [head, tail] if p)
    return combined or None


def _normalize_patient_payload(body: dict) -> dict:
    """Accept both the legacy flat payload and the new grouped payload.

    - If `address` or `emergency_contact` arrive as structured objects we
      persist them under `address_details` / `emergency_contact_details`
      AND derive a flat legacy string for backward compatibility.
    - If the top-level `first_name` / `last_name` / `date_of_birth` /
      `gender` / `phone` / `email` are missing but available inside the
      grouped sections (`demographics` / `contact`), backfill them so
      search, display and the legacy UI continue to work unchanged.
    """
    out = dict(body)

    # address — union[str, dict]
    addr = out.get("address")
    if isinstance(addr, dict):
        cleaned = {k: v for k, v in addr.items() if v is not None}
        out["address_details"] = cleaned or None
        out["address"] = _address_to_string(cleaned)

    # emergency_contact — union[str, dict]
    ec = out.get("emergency_contact")
    if isinstance(ec, dict):
        cleaned = {k: v for k, v in ec.items() if v is not None}
        out["emergency_contact_details"] = cleaned or None
        out["emergency_contact"] = _emergency_contact_to_string(cleaned)

    # Backfill legacy top-level fields from grouped sections when missing.
    demo = out.get("demographics") or {}
    contact = out.get("contact") or {}
    for key in ("first_name", "last_name", "date_of_birth", "gender"):
        if not out.get(key) and demo.get(key):
            out[key] = demo[key]
    for key in ("phone", "email"):
        if not out.get(key) and contact.get(key):
            out[key] = contact[key]

    return out


def _shape(p: dict, *, unmasked: bool) -> dict:
    """Decrypt + mask a patient document for the API response.

    Masked responses strip the richer grouped intake sections entirely —
    masking each nested PHI leaf is left to the future wizard layer. The
    legacy scalar `address` / `emergency_contact` fields remain populated
    for the current UI and are masked by `mask_patient` as before.
    """
    decrypted = decrypt_patient_doc(p)
    if unmasked:
        decrypted["unmasked"] = True
        decrypted["display_name_masked"] = None
        return decrypted
    masked = mask_patient(decrypted)
    masked["unmasked"] = False
    for key in PATIENT_GROUPED_KEYS:
        masked.pop(key, None)
    return masked


def _enforce_reason(reason: str | None, *, required: bool) -> str | None:
    return enforce_reason(reason, required=required)


# Keep module-level back-compat aliases for existing imports elsewhere.
_encrypt_patient_doc = encrypt_patient_doc
_decrypt_patient_doc = decrypt_patient_doc


# ---------------- List ----------------

@router.get("", response_model=list[PatientPublic])
async def list_patients(
    request: Request,
    search: str | None = None,
    include_deleted: bool = False,
    unmask: bool = False,
    user: dict = Depends(get_current_user),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = get_db_read()
    q: dict = {}

    if user["role"] == "patient":
        q["user_id"] = user["id"]
    elif user["role"] not in STAFF_ROLES and not ctx.is_platform_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")

    if not include_deleted:
        q["status"] = {"$ne": "deleted"}

    if search:
        q["$or"] = [
            {"first_name": {"$regex": search, "$options": "i"}},
            {"last_name": {"$regex": search, "$options": "i"}},
        ]

    # Strict tenant + location isolation. Patients are location-scoped.
    q = scoped_filter(q, ctx, location_scoped=True)
    if q.get("__deny__"):
        return []

    unmasked = bool(unmask) and user["role"] == "admin"

    async def _fetch():
        cursor = db.patients.find(q, {"_id": 0}).sort("created_at", -1)
        docs = [p async for p in cursor]
        return [_shape(p, unmasked=unmasked) for p in docs]

    if unmasked or search:
        # Never cache unmasked PHI; skip cache for ad-hoc searches too.
        shaped = await _fetch()
    else:
        tenant_key = ctx.tenant_id or "platform"
        key = cache_keys.patients_list(user["role"], search, include_deleted, masked=True) + f":{tenant_key}"
        shaped = await cache.get_or_set(key, 30, _fetch)

    await audit_success(
        user,
        "patient.list_viewed",
        request,
        phi_accessed=True,
        metadata={"count": len(shaped), "unmasked": unmasked, "include_deleted": include_deleted},
    )
    return shaped


# ---------------- Create ----------------

@router.post("", response_model=PatientPublic, status_code=201)
async def create_patient(
    payload: PatientCreate,
    request: Request,
    actor: dict = Depends(require_permission("patient", "create", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    ctx.assert_tenant_bound()
    db = get_db_write()
    now = _now()
    user_id = None
    if payload.email:
        existing_user = await db.users.find_one(
            {"email": payload.email.lower()}, {"_id": 0, "id": 1}
        )
        if existing_user:
            user_id = existing_user["id"]

    # Resolve location_id. Required for tenant-scoped users who have any
    # location restriction; platform admin may omit for cross-location rows.
    location_id = payload.location_id
    if not location_id:
        if ctx.allowed_location_ids and not ctx.tenant_scope_all:
            # Default to the user's (only) location if they have just one.
            if len(ctx.allowed_location_ids) == 1:
                location_id = ctx.allowed_location_ids[0]
            else:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "location_id is required when you have multiple location assignments.",
                )
        elif ctx.tenant_scope_all and ctx.tenant_id:
            # Pick any active location in the tenant as a sensible default.
            first_loc = await db.locations.find_one(
                {"tenant_id": ctx.tenant_id, "status": "active"}, {"_id": 0, "id": 1},
            )
            if first_loc:
                location_id = first_loc["id"]

    # Validate location belongs to tenant & user is allowed there.
    if location_id and ctx.tenant_id:
        loc = await db.locations.find_one(
            {"id": location_id, "tenant_id": ctx.tenant_id}, {"_id": 0, "id": 1},
        )
        if not loc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid location for this tenant")
        if not ctx.tenant_scope_all and not ctx.is_platform_admin:
            if location_id not in ctx.allowed_location_ids:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "Location not assigned to user")

    body = payload.model_dump()
    body.pop("location_id", None)
    body = _normalize_patient_payload(body)

    # Validate legacy required names (allow them to come from either the
    # flat payload or the grouped `demographics` section).
    if not body.get("first_name") or not body.get("last_name"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "first_name and last_name are required (either at the top level or within `demographics`).",
        )

    # Re-resolve email-based user linkage in case email came from `contact`.
    if not user_id and body.get("email"):
        existing_user = await db.users.find_one(
            {"email": body["email"].lower()}, {"_id": 0, "id": 1}
        )
        if existing_user:
            user_id = existing_user["id"]

    doc = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        **body,
        "status": "active",
        "created_at": now,
        "updated_at": now,
    }
    doc = stamp_for_write(doc, ctx, location_id=location_id)
    stored = _encrypt_patient_doc(doc)
    await db.patients.insert_one(stored)
    await cache.invalidate_prefix(cache_keys.PREFIX_PATIENTS)
    await cache.invalidate_prefix(cache_keys.PREFIX_DASHBOARD)
    await audit_success(
        actor, "patient.created", request,
        entity_type="patient", entity_id=doc["id"],
        phi_accessed=False, metadata={"tenant_id": doc.get("tenant_id"), "location_id": location_id},
    )
    return _shape(stored, unmasked=True)


# ---------------- Get ----------------

@router.get("/{patient_id}", response_model=PatientPublic)
async def get_patient(
    patient_id: str,
    request: Request,
    unmask: bool = False,
    reason: str | None = Query(default=None),
    user: dict = Depends(get_current_user),
    ctx: TenantContext = Depends(get_tenant_context),
):
    # Repository performs scoped lookup AND audits cross-tenant id probes.
    p = await _patient_repo.find_one_by_id(patient_id, ctx)
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")

    is_self = user["role"] == "patient" and p.get("user_id") == user["id"]
    if user["role"] == "patient" and not is_self:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")

    # Break-glass: non-admin + non-self roles MUST provide a reason.
    reason_required = user["role"] in ("doctor", "staff")
    enforced_reason = _enforce_reason(reason, required=reason_required)

    unmasked = False
    if unmask:
        if user["role"] == "admin":
            unmasked = True
        elif is_self:
            unmasked = True
        elif user["role"] in ("doctor", "staff"):
            # Must also provide a reason (already enforced above).
            unmasked = True
        else:
            unmasked = False

    if unmasked and user["role"] != "admin" and not is_self:
        await audit_emergency(
            user, action="patient.unmasked", entity_type="patient",
            entity_id=patient_id, reason=enforced_reason or "unspecified",
            request=request,
        )
    else:
        await audit_success(
            user, "patient.viewed", request,
            entity_type="patient", entity_id=patient_id,
            reason=enforced_reason, phi_accessed=True,
            metadata={"unmasked": unmasked},
        )
    return _shape(p, unmasked=unmasked)


# ---------------- Update ----------------

@router.put("/{patient_id}", response_model=PatientPublic)
async def update_patient(
    patient_id: str,
    payload: PatientUpdate,
    request: Request,
    actor: dict = Depends(require_permission("patient", "update", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = get_db_write()
    raw_updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not raw_updates:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No fields to update")

    updates = _normalize_patient_payload(raw_updates)
    updates["updated_at"] = _now()
    updates_to_store = _encrypt_patient_doc(updates)
    q = scoped_filter({"id": patient_id}, ctx, location_scoped=True)
    if q.get("__deny__"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    result = await db.patients.update_one(q, {"$set": updates_to_store})
    if result.matched_count == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")

    # Read-after-write consistency for the response, then invalidate caches.
    p = await read_after_write_db().patients.find_one({"id": patient_id}, {"_id": 0})
    await cache.invalidate_prefix(cache_keys.PREFIX_PATIENTS)
    await cache.invalidate_prefix(cache_keys.PREFIX_PATIENT)
    await cache.invalidate_prefix(cache_keys.PREFIX_APPOINTMENTS)
    await audit_success(
        actor, "patient.updated", request,
        entity_type="patient", entity_id=patient_id,
        phi_accessed=True, metadata={"fields": list(updates.keys())},
    )
    return _shape(p, unmasked=True)


# ---------------- Soft-delete ----------------

@router.delete("/{patient_id}")
async def delete_patient(
    patient_id: str,
    request: Request,
    reason: str = Query(default=""),
    admin: dict = Depends(require_permission("patient", "delete", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    enforced_reason = _enforce_reason(reason, required=True)
    require_reauth(request, admin)

    db = get_db_write()
    q = scoped_filter({"id": patient_id}, ctx, location_scoped=True)
    if q.get("__deny__"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    p = await db.patients.find_one(
        q, {"_id": 0, "id": 1, "status": 1, "legal_hold": 1},
    )
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    if p.get("legal_hold"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Patient is under legal hold; clear the hold before deletion.",
        )

    now = datetime.now(timezone.utc)
    retention_until = now + timedelta(days=365 * RETENTION_YEARS)
    await db.patients.update_one(
        {"id": patient_id},
        {
            "$set": {
                "status": "deleted",
                "deleted_at": now.isoformat(),
                "deleted_by": admin["id"],
                "retention_until": retention_until.isoformat(),
                "updated_at": now.isoformat(),
            }
        },
    )
    await audit_success(
        admin, "patient.soft_deleted", request,
        entity_type="patient", entity_id=patient_id,
        reason=enforced_reason, phi_accessed=True,
        metadata={"retention_until": retention_until.isoformat()},
    )
    return {
        "message": "Patient soft-deleted",
        "retention_until": retention_until.isoformat(),
    }


# ---------------- Export (patient right-to-access) ----------------

@router.get("/{patient_id}/export")
async def export_patient(
    patient_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = get_db_read()
    q = scoped_filter({"id": patient_id}, ctx, location_scoped=True)
    if q.get("__deny__"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    p = await db.patients.find_one(q, {"_id": 0})
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")

    is_self = user["role"] == "patient" and p.get("user_id") == user["id"]
    if not (user["role"] == "admin" or is_self or ctx.is_platform_admin):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")

    # Child records are also tenant-scoped via explicit tenant_id filter.
    record_filter = {"patient_id": patient_id}
    appt_filter = {"patient_id": patient_id}
    if ctx.tenant_id and not ctx.is_platform_admin:
        record_filter["tenant_id"] = ctx.tenant_id
        appt_filter["tenant_id"] = ctx.tenant_id
    records = [
        r async for r in db.medical_records.find(record_filter, {"_id": 0})
    ]
    appts = [
        a async for a in db.appointments.find(appt_filter, {"_id": 0})
    ]

    # Decrypt everything for the export.
    decrypted_patient = _decrypt_patient_doc(p)
    decrypted_records = [decrypt_fields(r, RECORD_ENCRYPTED) for r in records]

    await audit_success(
        user, "patient.exported", request,
        entity_type="patient", entity_id=patient_id, phi_accessed=True,
        metadata={"records": len(records), "appointments": len(appts)},
    )
    return {
        "exported_at": _now(),
        "exported_by": {"id": user["id"], "email": user["email"], "role": user["role"]},
        "patient": decrypted_patient,
        "medical_records": decrypted_records,
        "appointments": appts,
    }


# ---------------- Medical records ----------------

async def _hydrate_recorded_by(records: list[dict]) -> list[dict]:
    if not records:
        return records
    db = get_db_read()
    user_ids = list({r["recorded_by"] for r in records if r.get("recorded_by")})
    users = {
        u["id"]: u["name"]
        async for u in db.users.find({"id": {"$in": user_ids}}, {"_id": 0, "id": 1, "name": 1})
    }
    for r in records:
        r["recorded_by_name"] = users.get(r.get("recorded_by"))
    return records


@router.get("/{patient_id}/records", response_model=list[MedicalRecordPublic])
async def list_records(
    patient_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = get_db_read()
    patient_q = scoped_filter({"id": patient_id}, ctx, location_scoped=True)
    if patient_q.get("__deny__"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    patient = await db.patients.find_one(patient_q, {"_id": 0, "user_id": 1})
    if not patient:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    is_self = user["role"] == "patient" and patient.get("user_id") == user["id"]
    if user["role"] == "patient" and not is_self:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")

    record_filter: dict = {"patient_id": patient_id}
    if ctx.tenant_id and not ctx.is_platform_admin:
        record_filter["tenant_id"] = ctx.tenant_id
    docs = [
        r async for r in db.medical_records.find(record_filter, {"_id": 0})
        .sort("recorded_at", -1)
    ]
    decrypted = [decrypt_fields(r, RECORD_ENCRYPTED) for r in docs]
    await _hydrate_recorded_by(decrypted)
    await audit_success(
        user, "medical_record.list_viewed", request,
        entity_type="patient", entity_id=patient_id, phi_accessed=True,
        metadata={"count": len(decrypted)},
    )
    return decrypted


@router.post(
    "/{patient_id}/records",
    response_model=MedicalRecordPublic,
    status_code=201,
)
async def add_record(
    patient_id: str,
    payload: MedicalRecordCreate,
    request: Request,
    user: dict = Depends(require_permission("patient_chart", "create", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)

    db = get_db_write()
    patient_q = scoped_filter({"id": patient_id}, ctx, location_scoped=True)
    if patient_q.get("__deny__"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    patient = await db.patients.find_one(patient_q, {"_id": 0, "id": 1, "location_id": 1})
    if not patient:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")

    doc = {
        "id": str(uuid.uuid4()),
        "patient_id": patient_id,
        **payload.model_dump(),
        "recorded_by": user["id"],
        "recorded_at": _now(),
    }
    doc = stamp_for_write(doc, ctx, location_id=patient.get("location_id"))
    await db.medical_records.insert_one(encrypt_fields(doc, RECORD_ENCRYPTED))
    await cache.invalidate_prefix(cache_keys.PREFIX_PATIENT)
    await audit_success(
        user, "medical_record.created", request,
        entity_type="medical_record", entity_id=doc["id"],
        phi_accessed=True,
        metadata={"patient_id": patient_id, "record_type": payload.record_type},
    )
    hydrated = decrypt_fields(doc, RECORD_ENCRYPTED)
    hydrated["recorded_by_name"] = user["name"]
    return hydrated



# ---------------------------------------------------------------------------
# Charge-capture coding + signing (iteration 25 — Phase 2)
# ---------------------------------------------------------------------------
@router.put(
    "/{patient_id}/records/{record_id}/coding",
    response_model=MedicalRecordPublic,
)
async def update_record_coding(
    patient_id: str,
    record_id: str,
    payload: MedicalRecordCoding,
    request: Request,
    user: dict = Depends(require_permission("coding", "update", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    """Attach / replace structured procedures + diagnoses + responsibility.

    Does **not** mark the record as signed. A signed record cannot have
    its coding edited — use `POST .../sign` only after the coding is
    final. Captured records are also immutable at the coding layer.
    """
    db = get_db_write()
    patient_q = scoped_filter({"id": patient_id}, ctx, location_scoped=True)
    if patient_q.get("__deny__"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    patient = await db.patients.find_one(patient_q, {"_id": 0, "id": 1})
    if not patient:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")

    rec_q: dict = {"id": record_id, "patient_id": patient_id}
    if ctx.tenant_id and not ctx.is_platform_admin:
        rec_q["tenant_id"] = ctx.tenant_id
    record = await db.medical_records.find_one(rec_q, {"_id": 0})
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Record not found")
    if record.get("signed_at"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Signed records are locked — unsign before editing coding",
        )
    if record.get("charge_status") == "captured":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Captured records cannot be re-coded",
        )

    procedures = [p.model_dump() for p in payload.procedures]
    diagnoses = [d.model_dump() for d in payload.diagnoses]

    await db.medical_records.update_one(
        rec_q,
        {"$set": {
            "procedures": procedures,
            "diagnoses": diagnoses,
            "responsibility": payload.responsibility,
            "charge_status": record.get("charge_status") or "not_captured",
        }},
    )
    fresh = await db.medical_records.find_one(rec_q, {"_id": 0})
    hydrated = decrypt_fields(fresh, RECORD_ENCRYPTED)
    hydrated["recorded_by_name"] = user.get("name")
    await audit_success(
        user, "medical_record.coding_updated", request,
        entity_type="medical_record", entity_id=record_id,
        metadata={"patient_id": patient_id,
                  "procedures": len(procedures),
                  "diagnoses": len(diagnoses),
                  "responsibility": payload.responsibility},
    )
    return hydrated


@router.post(
    "/{patient_id}/records/{record_id}/sign",
    response_model=MedicalRecordPublic,
)
async def sign_record(
    patient_id: str,
    record_id: str,
    request: Request,
    user: dict = Depends(require_permission("patient_chart", "update", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    """Sign a medical record. Signed records are immutable and eligible
    for charge capture. Requires `patient_chart.update` permission (same
    as record creation) and does NOT automatically generate charges —
    that is a separate operator action in billing."""
    require_reauth(request, user)

    db = get_db_write()
    patient_q = scoped_filter({"id": patient_id}, ctx, location_scoped=True)
    if patient_q.get("__deny__"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    patient = await db.patients.find_one(patient_q, {"_id": 0, "id": 1})
    if not patient:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")

    rec_q: dict = {"id": record_id, "patient_id": patient_id}
    if ctx.tenant_id and not ctx.is_platform_admin:
        rec_q["tenant_id"] = ctx.tenant_id
    record = await db.medical_records.find_one(rec_q, {"_id": 0})
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Record not found")
    if record.get("signed_at"):
        return decrypt_fields(record, RECORD_ENCRYPTED)  # idempotent

    now = _now()
    await db.medical_records.update_one(
        rec_q,
        {"$set": {"signed_at": now, "signed_by": user["id"]}},
    )
    fresh = await db.medical_records.find_one(rec_q, {"_id": 0})
    hydrated = decrypt_fields(fresh, RECORD_ENCRYPTED)
    hydrated["recorded_by_name"] = user.get("name")
    await audit_success(
        user, "medical_record.signed", request,
        entity_type="medical_record", entity_id=record_id,
        metadata={"patient_id": patient_id,
                  "procedures": len(record.get("procedures") or []),
                  "responsibility": record.get("responsibility")},
    )
    return hydrated


# ---------------------------------------------------------------------------
# Sub-router mounts — already included at the top of this module so their
# specific paths take precedence over `/{patient_id}`. This block is kept
# empty for documentation clarity; do not add `include_router` calls here.
# ---------------------------------------------------------------------------
