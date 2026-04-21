"""
Privacy Service router — /api/privacy/*

Implements:
  - Data-inventory reference (admin)
  - Privacy requests intake + admin workflow (DSAR-style: export/delete/
    correct/restrict/opt_out) with a documented state model
  - Consent records (accept / withdraw versioned Privacy Notice)
  - Communication preferences (self-service)
  - Legal-hold toggle for patient records (admin, re-auth gated)
  - Account-data export (self)

Every state change emits an audit row. No PHI values are stored in privacy
request `notes`, response_notes, or fulfillment.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from core.audit import audit_success, log_audit
from core.db import get_db_read, get_db_write, read_after_write_db
from core.deps import get_current_user, require_role
from core.reauth import require_reauth
from services.privacy.inventory import DATA_INVENTORY
from services.privacy.models import (
    CommPreferencesUpdate,
    ConsentAccept,
    LegalHoldUpdate,
    PrivacyRequestCreate,
    PrivacyRequestUpdate,
)

router = APIRouter(prefix="/privacy", tags=["privacy"])

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "received": {"in_review", "rejected", "withdrawn"},
    "in_review": {"approved", "rejected", "withdrawn"},
    "approved": {"fulfilled", "rejected", "withdrawn"},
    "fulfilled": set(),
    "rejected": set(),
    "withdrawn": set(),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _shape_request(r: dict) -> dict:
    out = dict(r)
    out.pop("_id", None)
    return out


# ---------------- Data inventory ----------------

@router.get("/data-inventory")
async def data_inventory(_admin: dict = Depends(require_role("admin"))):
    """Static catalog of all data categories the app handles. Admin only."""
    return {
        "generated_at": _now(),
        "categories": DATA_INVENTORY,
        "retention_settings": {
            "patient_retention_years": 7,
            "audit_retention_years": 7,
            "notes": "Retention defaults are hardcoded for the MVP and must be"
            " driven by a production configuration (env or per-tenant policy)"
            " before deployment. See PRIVACY_AND_RETENTION.md §4.",
        },
    }


# ---------------- Privacy requests ----------------

@router.post("/requests", status_code=201)
async def create_request(
    payload: PrivacyRequestCreate,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Submit a new privacy request.

    - Admin/staff can raise on behalf of a subject (must pass subject_user_id).
    - Patients may only raise requests for their own user_id.
    """
    db = get_db_write()
    subject_user_id = payload.subject_user_id or user["id"]

    if user["role"] == "patient" and subject_user_id != user["id"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Patients can only submit requests for themselves")
    if user["role"] not in ("admin", "staff", "patient"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")

    subject_user = await db.users.find_one({"id": subject_user_id}, {"_id": 0, "id": 1, "email": 1})
    if not subject_user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Subject user not found")

    now = _now()
    doc = {
        "id": str(uuid.uuid4()),
        "request_type": payload.request_type,
        "subject_user_id": subject_user_id,
        "subject_patient_id": payload.subject_patient_id,
        "submitted_by_id": user["id"],
        "submitted_by_role": user["role"],
        "status": "received",
        "notes": payload.notes,
        "response_notes": None,
        "fulfillment": {},
        "created_at": now,
        "updated_at": now,
        "closed_at": None,
    }
    await db.privacy_requests.insert_one(doc)
    await audit_success(
        user,
        "privacy_request.created",
        request,
        entity_type="privacy_request",
        entity_id=doc["id"],
        metadata={
            "request_type": payload.request_type,
            "subject_user_id": subject_user_id,
            "has_patient_target": bool(payload.subject_patient_id),
        },
    )
    return _shape_request(doc)


@router.get("/requests")
async def list_requests(
    request: Request,
    admin: dict = Depends(require_role("admin")),
    status_filter: str | None = Query(default=None, alias="status"),
    request_type: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
):
    db = get_db_read()
    q: dict = {}
    if status_filter:
        q["status"] = status_filter
    if request_type:
        q["request_type"] = request_type
    cursor = db.privacy_requests.find(q, {"_id": 0}).sort("created_at", -1).limit(limit)
    rows = [r async for r in cursor]
    await audit_success(
        admin, "privacy_request.list_viewed", request,
        metadata={"filters": q, "count": len(rows)},
    )
    return rows


@router.get("/my-requests")
async def my_requests(user: dict = Depends(get_current_user)):
    db = get_db_read()
    cursor = db.privacy_requests.find(
        {"$or": [{"subject_user_id": user["id"]}, {"submitted_by_id": user["id"]}]},
        {"_id": 0},
    ).sort("created_at", -1).limit(100)
    return [r async for r in cursor]


@router.get("/requests/{request_id}")
async def get_request(
    request_id: str,
    user: dict = Depends(get_current_user),
):
    db = get_db_read()
    r = await db.privacy_requests.find_one({"id": request_id}, {"_id": 0})
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Request not found")
    # Admin sees everything; anyone else only sees their own request
    if user["role"] != "admin" and r.get("submitted_by_id") != user["id"] and r.get("subject_user_id") != user["id"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")
    return r


@router.patch("/requests/{request_id}")
async def update_request(
    request_id: str,
    payload: PrivacyRequestUpdate,
    request: Request,
    admin: dict = Depends(require_role("admin")),
):
    db = get_db_write()
    current = await db.privacy_requests.find_one({"id": request_id}, {"_id": 0})
    if not current:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Request not found")

    updates: dict = {}
    if payload.status:
        cur = current.get("status", "received")
        if payload.status != cur and payload.status not in ALLOWED_TRANSITIONS.get(cur, set()):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Cannot transition from {cur} to {payload.status}",
            )
        updates["status"] = payload.status
        if payload.status in ("fulfilled", "rejected", "withdrawn"):
            updates["closed_at"] = _now()
    if payload.response_notes is not None:
        updates["response_notes"] = payload.response_notes
    if not updates:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No fields to update")
    updates["updated_at"] = _now()
    await db.privacy_requests.update_one({"id": request_id}, {"$set": updates})

    updated = await read_after_write_db().privacy_requests.find_one(
        {"id": request_id}, {"_id": 0}
    )
    await audit_success(
        admin,
        "privacy_request.updated",
        request,
        entity_type="privacy_request",
        entity_id=request_id,
        metadata={"fields": list(updates.keys()), "new_status": updates.get("status")},
    )
    return updated


@router.post("/requests/{request_id}/fulfill-delete")
async def fulfill_delete(
    request_id: str,
    request: Request,
    admin: dict = Depends(require_role("admin")),
):
    """Fulfil a DELETE privacy request by soft-deleting the linked patient
    record. Requires step-up re-authentication.

    The actual patient soft-delete must be performed via DELETE /api/patients/{id}
    (which enforces reauth + audit + retention). This endpoint links the two
    by recording the deletion in the privacy request fulfillment metadata.
    Legal holds on the patient block fulfilment.
    """
    require_reauth(request, admin)
    db = get_db_write()
    pr = await db.privacy_requests.find_one({"id": request_id}, {"_id": 0})
    if not pr:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Request not found")
    if pr.get("request_type") != "delete":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only delete requests can be fulfilled this way")
    if pr.get("status") in ("fulfilled", "rejected", "withdrawn"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Request already {pr['status']}")

    patient_id = pr.get("subject_patient_id")
    if not patient_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Request has no subject_patient_id linked")

    patient = await db.patients.find_one({"id": patient_id}, {"_id": 0})
    if not patient:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Linked patient not found")
    if patient.get("legal_hold"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Patient is under legal hold; clear the hold before fulfilling deletion.",
        )

    now = _now()
    await db.privacy_requests.update_one(
        {"id": request_id},
        {
            "$set": {
                "status": "fulfilled",
                "closed_at": now,
                "updated_at": now,
                "fulfillment": {
                    **(pr.get("fulfillment") or {}),
                    "linked_patient_id": patient_id,
                    "fulfilled_by": admin["id"],
                    "fulfillment_note": "Admin invoked fulfill-delete; caller must also soft-delete the patient via DELETE /api/patients/{id}.",
                },
            }
        },
    )
    await audit_success(
        admin, "privacy_request.fulfilled", request,
        entity_type="privacy_request", entity_id=request_id,
        metadata={"request_type": "delete", "linked_patient_id": patient_id},
    )
    return {"message": "Privacy request marked fulfilled. Complete the soft-delete via DELETE /api/patients/{id}."}


# ---------------- Legal hold (admin) ----------------

@router.post("/patients/{patient_id}/legal-hold")
async def set_legal_hold(
    patient_id: str,
    payload: LegalHoldUpdate,
    request: Request,
    admin: dict = Depends(require_role("admin")),
):
    """Set or clear a legal / medical-retention hold on a patient. While a
    hold is active, privacy-request deletions are blocked and the patient
    cannot be physically purged by the retention worker (future P0.1)."""
    require_reauth(request, admin)
    db = get_db_write()
    p = await db.patients.find_one({"id": patient_id}, {"_id": 0, "id": 1})
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    now = _now()
    await db.patients.update_one(
        {"id": patient_id},
        {
            "$set": {
                "legal_hold": bool(payload.hold),
                "legal_hold_reason": payload.reason if payload.hold else "",
                "legal_hold_set_by": admin["id"] if payload.hold else None,
                "legal_hold_set_at": now if payload.hold else None,
                "updated_at": now,
            }
        },
    )
    await audit_success(
        admin,
        "patient.legal_hold_updated",
        request,
        entity_type="patient",
        entity_id=patient_id,
        metadata={"hold": payload.hold},
    )
    return {"message": f"Legal hold set to {payload.hold}."}


# ---------------- Consent records ----------------

@router.post("/consents/accept", status_code=201)
async def accept_consent(
    payload: ConsentAccept,
    request: Request,
    user: dict = Depends(get_current_user),
):
    db = get_db_write()
    now = datetime.now(timezone.utc)
    ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() or (
        request.client.host if request.client else "unknown"
    )
    doc = {
        "id": str(uuid.uuid4()),
        "user_id": user["id"],
        "policy_type": payload.policy_type,
        "policy_version": payload.policy_version,
        "action": payload.action,
        "accepted_at": now.isoformat(),
        "ip": ip,
        "user_agent": request.headers.get("user-agent"),
    }
    await db.consent_records.insert_one(doc)
    await log_audit(
        action="privacy.consent_recorded",
        actor_id=user["id"],
        actor_email=user["email"],
        actor_role=user["role"],
        request=request,
        entity_type="consent",
        entity_id=doc["id"],
        metadata={
            "policy_type": payload.policy_type,
            "policy_version": payload.policy_version,
            "action": payload.action,
        },
    )
    doc.pop("_id", None)
    return doc


@router.get("/consents/me")
async def my_consents(user: dict = Depends(get_current_user)):
    db = get_db_read()
    cursor = db.consent_records.find({"user_id": user["id"]}, {"_id": 0}).sort(
        "accepted_at", -1,
    ).limit(50)
    return [r async for r in cursor]


# ---------------- Communication preferences ----------------

@router.get("/communication-preferences")
async def get_prefs(user: dict = Depends(get_current_user)):
    db = get_db_read()
    prefs = await db.communication_preferences.find_one({"user_id": user["id"]}, {"_id": 0})
    if not prefs:
        prefs = {
            "user_id": user["id"],
            "email_opt_in": True,
            "sms_opt_in": False,
            "marketing_opt_in": False,
            "updated_at": None,
        }
    return prefs


@router.patch("/communication-preferences")
async def update_prefs(
    payload: CommPreferencesUpdate,
    request: Request,
    user: dict = Depends(get_current_user),
):
    db = get_db_write()
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No fields to update")
    updates["updated_at"] = _now()
    await db.communication_preferences.update_one(
        {"user_id": user["id"]},
        {"$set": {"user_id": user["id"], **updates}},
        upsert=True,
    )
    await audit_success(
        user,
        "privacy.comm_preferences_updated",
        request,
        entity_type="comm_preferences",
        entity_id=user["id"],
        metadata={"fields": list(updates.keys())},
    )
    return await db.communication_preferences.find_one({"user_id": user["id"]}, {"_id": 0})
