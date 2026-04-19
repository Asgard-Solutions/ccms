"""
Patient Service router — /api/patients/*
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status

from core.db import get_db
from core.deps import get_current_user, require_role
from services.patient.models import (
    PatientCreate,
    PatientUpdate,
    PatientPublic,
    MedicalRecordCreate,
    MedicalRecordPublic,
)

router = APIRouter(prefix="/patients", tags=["patient"])

STAFF_ROLES = ("admin", "doctor", "staff")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.get("", response_model=list[PatientPublic])
async def list_patients(
    search: str | None = None,
    user: dict = Depends(get_current_user),
):
    db = get_db()
    q: dict = {}

    if user["role"] == "patient":
        # Patients can only see their own record (if linked)
        q["user_id"] = user["id"]
    elif user["role"] not in STAFF_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")

    if search:
        q["$or"] = [
            {"first_name": {"$regex": search, "$options": "i"}},
            {"last_name": {"$regex": search, "$options": "i"}},
            {"email": {"$regex": search, "$options": "i"}},
            {"phone": {"$regex": search, "$options": "i"}},
        ]

    cursor = db.patients.find(q, {"_id": 0}).sort("created_at", -1)
    return [p async for p in cursor]


@router.post("", response_model=PatientPublic, status_code=201)
async def create_patient(
    payload: PatientCreate,
    _actor: dict = Depends(require_role(*STAFF_ROLES)),
):
    db = get_db()
    now = _now()
    doc = {
        "id": str(uuid.uuid4()),
        "user_id": None,
        **payload.model_dump(),
        "created_at": now,
        "updated_at": now,
    }
    await db.patients.insert_one(doc)
    doc.pop("_id", None)
    return doc


@router.get("/{patient_id}", response_model=PatientPublic)
async def get_patient(patient_id: str, user: dict = Depends(get_current_user)):
    db = get_db()
    p = await db.patients.find_one({"id": patient_id}, {"_id": 0})
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    if user["role"] == "patient" and p.get("user_id") != user["id"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")
    return p


@router.put("/{patient_id}", response_model=PatientPublic)
async def update_patient(
    patient_id: str,
    payload: PatientUpdate,
    _actor: dict = Depends(require_role(*STAFF_ROLES)),
):
    db = get_db()
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No fields to update")
    updates["updated_at"] = _now()
    result = await db.patients.update_one({"id": patient_id}, {"$set": updates})
    if result.matched_count == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    p = await db.patients.find_one({"id": patient_id}, {"_id": 0})
    return p


@router.delete("/{patient_id}")
async def delete_patient(
    patient_id: str,
    _admin: dict = Depends(require_role("admin")),
):
    db = get_db()
    result = await db.patients.delete_one({"id": patient_id})
    if result.deleted_count == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    # Cascade: medical records
    await db.medical_records.delete_many({"patient_id": patient_id})
    return {"message": "Patient deleted"}


# ----- Medical Records -----

async def _hydrate_recorded_by(records: list[dict]) -> list[dict]:
    if not records:
        return records
    db = get_db()
    user_ids = list({r["recorded_by"] for r in records if r.get("recorded_by")})
    users = {
        u["id"]: u["name"]
        async for u in db.users.find({"id": {"$in": user_ids}}, {"_id": 0, "id": 1, "name": 1})
    }
    for r in records:
        r["recorded_by_name"] = users.get(r.get("recorded_by"))
    return records


@router.get("/{patient_id}/records", response_model=list[MedicalRecordPublic])
async def list_records(patient_id: str, user: dict = Depends(get_current_user)):
    db = get_db()
    patient = await db.patients.find_one({"id": patient_id}, {"_id": 0, "user_id": 1})
    if not patient:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    if user["role"] == "patient" and patient.get("user_id") != user["id"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")

    records = [
        r async for r in db.medical_records.find({"patient_id": patient_id}, {"_id": 0})
        .sort("recorded_at", -1)
    ]
    return await _hydrate_recorded_by(records)


@router.post(
    "/{patient_id}/records",
    response_model=MedicalRecordPublic,
    status_code=201,
)
async def add_record(
    patient_id: str,
    payload: MedicalRecordCreate,
    user: dict = Depends(require_role("admin", "doctor")),
):
    db = get_db()
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
    await db.medical_records.insert_one(doc)
    doc.pop("_id", None)
    doc["recorded_by_name"] = user["name"]
    return doc
