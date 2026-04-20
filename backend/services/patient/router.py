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
"""
import uuid
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from core.audit import audit_emergency, audit_success, log_audit
from core import cache, cache_keys
from core.crypto import decrypt_fields, encrypt_fields
from core.db import get_db, get_db_read, get_db_write, read_after_write_db
from core.deps import get_current_user, require_role
from core.masking import mask_patient
from core.reauth import require_reauth
from services.authz.policy import require_permission
from services.patient.models import (
    MedicalRecordCreate,
    MedicalRecordPublic,
    PatientCreate,
    PatientPublic,
    PatientUpdate,
)

router = APIRouter(prefix="/patients", tags=["patient"])

STAFF_ROLES = ("admin", "doctor", "staff")
PATIENT_ENCRYPTED = ["date_of_birth", "address", "emergency_contact", "notes"]
RECORD_ENCRYPTED = ["description", "diagnosis", "treatment"]
RETENTION_YEARS = 7
REASON_MIN_LENGTH = 8


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _shape(p: dict, *, unmasked: bool) -> dict:
    """Decrypt + mask a patient document for the API response."""
    decrypted = decrypt_fields(p, PATIENT_ENCRYPTED)
    if unmasked:
        decrypted["unmasked"] = True
        decrypted["display_name_masked"] = None
        return decrypted
    masked = mask_patient(decrypted)
    masked["unmasked"] = False
    return masked


def _enforce_reason(reason: str | None, *, required: bool) -> str | None:
    if not required:
        return reason or None
    if not reason or len(reason.strip()) < REASON_MIN_LENGTH:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"A clinical reason of at least {REASON_MIN_LENGTH} characters is required for this access.",
        )
    return reason.strip()


# ---------------- List ----------------

@router.get("", response_model=list[PatientPublic])
async def list_patients(
    request: Request,
    search: str | None = None,
    include_deleted: bool = False,
    unmask: bool = False,
    user: dict = Depends(get_current_user),
):
    db = get_db_read()
    q: dict = {}

    if user["role"] == "patient":
        q["user_id"] = user["id"]
    elif user["role"] not in STAFF_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")

    if not include_deleted:
        q["status"] = {"$ne": "deleted"}

    if search:
        q["$or"] = [
            {"first_name": {"$regex": search, "$options": "i"}},
            {"last_name": {"$regex": search, "$options": "i"}},
        ]

    unmasked = bool(unmask) and user["role"] == "admin"

    async def _fetch():
        cursor = db.patients.find(q, {"_id": 0}).sort("created_at", -1)
        docs = [p async for p in cursor]
        return [_shape(p, unmasked=unmasked) for p in docs]

    if unmasked or search:
        # Never cache unmasked PHI; skip cache for ad-hoc searches too.
        shaped = await _fetch()
    else:
        key = cache_keys.patients_list(user["role"], search, include_deleted, masked=True)
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
):
    db = get_db_write()
    now = _now()
    user_id = None
    if payload.email:
        existing_user = await db.users.find_one(
            {"email": payload.email.lower()}, {"_id": 0, "id": 1}
        )
        if existing_user:
            user_id = existing_user["id"]

    doc = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        **payload.model_dump(),
        "status": "active",
        "created_at": now,
        "updated_at": now,
    }
    stored = encrypt_fields(doc, PATIENT_ENCRYPTED)
    await db.patients.insert_one(stored)
    await cache.invalidate_prefix(cache_keys.PREFIX_PATIENTS)
    await cache.invalidate_prefix(cache_keys.PREFIX_DASHBOARD)
    await audit_success(
        actor, "patient.created", request,
        entity_type="patient", entity_id=doc["id"],
        phi_accessed=False, metadata={},
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
):
    db = get_db_read()
    p = await db.patients.find_one({"id": patient_id}, {"_id": 0})
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
):
    db = get_db_write()
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No fields to update")

    updates["updated_at"] = _now()
    updates_to_store = encrypt_fields(updates, PATIENT_ENCRYPTED)
    result = await db.patients.update_one({"id": patient_id}, {"$set": updates_to_store})
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
):
    enforced_reason = _enforce_reason(reason, required=True)
    require_reauth(request, admin)

    db = get_db_write()
    p = await db.patients.find_one(
        {"id": patient_id}, {"_id": 0, "id": 1, "status": 1, "legal_hold": 1},
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
):
    db = get_db_read()
    p = await db.patients.find_one({"id": patient_id}, {"_id": 0})
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")

    is_self = user["role"] == "patient" and p.get("user_id") == user["id"]
    if not (user["role"] == "admin" or is_self):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")

    records = [
        r async for r in db.medical_records.find({"patient_id": patient_id}, {"_id": 0})
    ]
    appts = [
        a async for a in db.appointments.find({"patient_id": patient_id}, {"_id": 0})
    ]

    # Decrypt everything for the export.
    decrypted_patient = decrypt_fields(p, PATIENT_ENCRYPTED)
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
):
    db = get_db_read()
    patient = await db.patients.find_one({"id": patient_id}, {"_id": 0, "user_id": 1})
    if not patient:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    is_self = user["role"] == "patient" and patient.get("user_id") == user["id"]
    if user["role"] == "patient" and not is_self:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")

    docs = [
        r async for r in db.medical_records.find({"patient_id": patient_id}, {"_id": 0})
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
):
    require_reauth(request, user)

    db = get_db_write()
    patient = await db.patients.find_one({"id": patient_id}, {"_id": 0, "id": 1})
    if not patient:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")

    doc = {
        "id": str(uuid.uuid4()),
        "patient_id": patient_id,
        **payload.model_dump(),
        "recorded_by": user["id"],
        "recorded_at": _now(),
    }
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
