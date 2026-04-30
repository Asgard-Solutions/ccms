"""
services/billing/eligibility_router.py — Eligibility 270/271 endpoints.

Routes
------
POST /billing/policies/{policy_id}/eligibility-check
GET  /billing/policies/{policy_id}/eligibility-checks
POST /billing/patients/{patient_id}/eligibility-check
GET  /billing/patients/{patient_id}/eligibility-latest
GET  /billing/patients/{patient_id}/eligibility-checks
POST /billing/appointments/{appointment_id}/eligibility-check
GET  /billing/appointments/{appointment_id}/eligibility-latest
GET  /billing/eligibility-checks/{check_id}     (MFA-gated wires)

Surfaces:
  * patient profile  — patient-latest + patient-checks
  * appointment      — appointment-latest + appointment-check
  * check-in         — appointment-latest
  * billing readiness — consumed in clinical/billing_readiness_router
  * claim prep       — consumed via patient-latest in claim router
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from core.audit import audit_failure, audit_success
from core.reauth import require_reauth
from core.tenancy import TenantContext, require_tenant, tenant_db
from services.authz.policy import require_permission
from services.billing.eligibility import (
    EligibilityEngineError,
    default_engine,
)
from services.billing.eligibility_status import (
    DISCLAIMER_TEXT,
    ELIGIBILITY_STATUSES,
    STATUS_LABELS,
    STATUS_TONES,
    classify_result,
    is_expired,
    overlay_expiration,
    policy_snapshot_hash,
)


router = APIRouter(prefix="/billing", tags=["billing", "eligibility"])


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class EligibilityCheckCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service_type_codes: list[str] | None = Field(default=None)
    inquiry_date: str | None = Field(
        default=None, pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Service date being inquired about. Default today.",
    )
    appointment_id: str | None = Field(
        default=None,
        description="Optional appointment to bind this check to.",
    )


class EligibilityBenefit(BaseModel):
    qualifier: str | None = None
    label: str = ""
    service_type: str | None = None
    service_type_label: str = ""
    insurance_type: str | None = None
    plan: str | None = None
    time_period: str | None = None
    amount_cents: int | None = None
    percent: int | None = None


class EligibilityResult(BaseModel):
    transaction_type: Literal["271"] = "271"
    trace_number: str | None = None
    coverage_active: bool = False
    rejected: bool = False
    rejection_reason: str | None = None
    payer_name: str | None = None
    payer_id: str | None = None
    provider_name: str | None = None
    provider_npi: str | None = None
    subscriber_name: str | None = None
    member_id: str | None = None
    date_of_birth: str | None = None
    gender: str | None = None
    plan_name: str | None = None
    effective_date: str | None = None
    termination_date: str | None = None
    copay_cents: int | None = None
    coinsurance_pct: int | None = None
    deductible_cents: int | None = None
    deductible_met_cents: int | None = None
    out_of_pocket_cents: int | None = None
    benefits: list[EligibilityBenefit] = Field(default_factory=list)
    messages: list[str] = Field(default_factory=list)
    requested_service_types: list[str] = Field(default_factory=list)


class EligibilityCheckPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    tenant_id: str
    patient_id: str
    policy_id: str | None = None
    payer_id: str
    provider_id: str | None = None
    appointment_id: str | None = None
    engine: str
    sandbox: bool
    service_type_codes: list[str]
    service_date: str | None = None
    status: str
    effective_status: str | None = None
    disclaimer: str = DISCLAIMER_TEXT
    checked_at: str
    checked_by: str
    result: EligibilityResult
    request_wire: str | None = None
    response_wire: str | None = None


class EligibilityCheckSummary(BaseModel):
    id: str
    checked_at: str
    checked_by: str
    status: str
    effective_status: str | None = None
    payer_id: str
    payer_name: str | None = None
    plan_name: str | None = None
    service_date: str | None = None
    appointment_id: str | None = None
    policy_id: str | None = None
    copay_cents: int | None = None
    deductible_cents: int | None = None
    deductible_met_cents: int | None = None
    coinsurance_pct: int | None = None
    out_of_pocket_cents: int | None = None
    service_type_codes: list[str] = Field(default_factory=list)
    sandbox: bool = False
    rejection_reason: str | None = None


class EligibilityStatusReference(BaseModel):
    """Sent to the UI once at bootstrap so colours / labels stay consistent."""
    statuses: list[str]
    labels: dict[str, str]
    tones: dict[str, str]
    disclaimer: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def _load_patient_policy(
    db: Any, ctx: TenantContext,
    *,
    patient_id: str | None = None,
    policy_id: str | None = None,
) -> tuple[dict, dict, dict, dict | None]:
    """Fetch (policy, patient, payer, provider?) from a patient OR
    policy anchor. Raises 404 with an actionable message when data is
    missing.
    """
    if policy_id:
        policy = await db.patient_insurance_policies.find_one(
            {"id": policy_id, "tenant_id": ctx.tenant_id}, {"_id": 0},
        )
        if not policy:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "Policy not found",
            )
        patient_id = policy["patient_id"]
    else:
        # Patient-anchored — pick the most specific active primary
        # policy; fall back to any active policy on file.
        policies_q = {
            "tenant_id": ctx.tenant_id,
            "patient_id": patient_id,
            "status": "active",
        }
        primaries = [
            p async for p in db.patient_insurance_policies.find(
                {**policies_q, "rank": "primary"}, {"_id": 0},
            )
        ]
        if primaries:
            policy = primaries[0]
        else:
            anys = [
                p async for p in db.patient_insurance_policies.find(
                    policies_q, {"_id": 0},
                )
            ]
            if not anys:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "Missing info: patient has no active insurance policy "
                    "on file.",
                )
            policy = anys[0]

    patient = await db.patients.find_one(
        {"id": policy["patient_id"], "tenant_id": ctx.tenant_id},
        {"_id": 0},
    )
    if not patient:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Patient referenced by policy not found",
        )
    # Missing-info validation (scenario 2).
    missing: list[str] = []
    if not policy.get("member_id"):
        missing.append("member ID")
    if not patient.get("first_name") or not patient.get("last_name"):
        missing.append("patient legal name")
    if not patient.get("date_of_birth"):
        missing.append("patient date of birth")
    if missing:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Missing info: " + ", ".join(missing)
            + ". Update the patient record, then retry.",
        )

    payer = await db.billing_payers.find_one(
        {"id": policy["payer_id"], "tenant_id": ctx.tenant_id},
        {"_id": 0},
    )
    if not payer:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Payer referenced by policy not found",
        )
    provider = None
    provider_id = patient.get("primary_provider_id")
    if provider_id:
        provider = await db.providers.find_one(
            {"id": provider_id, "tenant_id": ctx.tenant_id}, {"_id": 0},
        )
    if not provider:
        provider = await db.providers.find_one(
            {"tenant_id": ctx.tenant_id, "entity_type": "org"},
            {"_id": 0},
        )
    return policy, patient, payer, provider


def _build_submitter_envelope(
    provider: dict | None, tenant_id: str,
) -> dict:
    if not provider:
        return {
            "id": (tenant_id or "CCMS").upper().replace("-", "")[:15] or "CCMS",
            "name": "CCMS BILLING",
            "contact_name": "BILLING",
            "contact_phone": None,
        }
    submitter_id = (provider.get("tax_id") or provider.get("npi")
                    or tenant_id or "CCMS").replace("-", "")
    return {
        "id": submitter_id,
        "name": (provider.get("organization_name")
                 or provider.get("name") or "CCMS BILLING"),
        "contact_name": provider.get("contact_name") or "BILLING",
        "contact_phone": provider.get("phone"),
    }


async def _run_and_persist(
    db: Any, ctx: TenantContext, user: dict, request: Request,
    *,
    policy: dict, patient: dict, payer: dict, provider: dict | None,
    payload: EligibilityCheckCreate,
) -> dict[str, Any]:
    submitter = _build_submitter_envelope(provider, ctx.tenant_id)
    receiver = {
        "id": (payer.get("electronic_payer_id")
               or payer.get("payer_code") or "PAYER"),
        "name": payer.get("name") or "PAYER",
    }
    service_date = payload.inquiry_date or _today_iso()
    engine = default_engine()
    check_id = str(uuid.uuid4())
    error_row: dict | None = None
    try:
        outcome = engine.check(
            submitter=submitter,
            receiver=receiver,
            provider=provider or submitter,
            payer=payer,
            patient=patient,
            policy=policy,
            service_type_codes=payload.service_type_codes,
            inquiry_date=service_date,
        )
    except EligibilityEngineError as exc:
        # Error path — persist an `error` row so the UI still has an
        # anchor and the retry button has something to point at.
        error_row = {
            "id": check_id,
            "tenant_id": ctx.tenant_id,
            "patient_id": policy["patient_id"],
            "policy_id": policy["id"],
            "payer_id": policy["payer_id"],
            "provider_id": (provider or {}).get("id"),
            "appointment_id": payload.appointment_id,
            "engine": "mock",
            "sandbox": True,
            "service_type_codes": payload.service_type_codes or ["30", "33", "98"],
            "service_date": service_date,
            "policy_snapshot_hash": policy_snapshot_hash(policy),
            "status": "error",
            "request_wire": None,
            "response_wire": None,
            "result": {
                "coverage_active": False,
                "messages": [str(exc)],
                "rejected": False,
                "payer_name": payer.get("name"),
                "member_id": policy.get("member_id"),
                "requested_service_types":
                    payload.service_type_codes or ["30", "33", "98"],
                "benefits": [],
            },
            "checked_at": _now_iso(),
            "checked_by": user["id"],
        }
        await db.eligibility_checks.insert_one(error_row)
        await audit_failure(
            user, "billing.eligibility.check_failed", request,
            entity_type="patient_insurance_policy", entity_id=policy["id"],
            metadata={"check_id": check_id, "error": str(exc)[:500]},
        )
        return error_row
    except Exception as exc:  # noqa: BLE001
        await audit_failure(
            user, "billing.eligibility.check_failed", request,
            entity_type="patient_insurance_policy", entity_id=policy["id"],
            metadata={"error": str(exc)[:500]},
        )
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Eligibility check failed: {exc}",
        )

    derived_status = classify_result(outcome["result"])
    doc = {
        "id": check_id,
        "tenant_id": ctx.tenant_id,
        "patient_id": policy["patient_id"],
        "policy_id": policy["id"],
        "payer_id": policy["payer_id"],
        "provider_id": (provider or {}).get("id"),
        "appointment_id": payload.appointment_id,
        "engine": outcome["engine"],
        "sandbox": outcome["sandbox"],
        "service_type_codes": outcome["service_type_codes"],
        "service_date": service_date,
        "policy_snapshot_hash": policy_snapshot_hash(policy),
        "status": derived_status,
        "request_wire": outcome["request_wire"],
        "response_wire": outcome["response_wire"],
        "result": outcome["result"],
        "checked_at": outcome["checked_at"],
        "checked_by": user["id"],
    }
    await db.eligibility_checks.insert_one(doc)
    await audit_success(
        user, "billing.eligibility.checked", request,
        entity_type="patient_insurance_policy", entity_id=policy["id"],
        metadata={
            "check_id": check_id, "status": derived_status,
            "service_date": service_date,
            "appointment_id": payload.appointment_id,
            "engine": outcome["engine"], "sandbox": outcome["sandbox"],
        },
    )
    return doc


def _summary_from_doc(doc: dict, payer_name_map: dict[str, str]) -> dict:
    r = doc.get("result") or {}
    return {
        "id": doc["id"],
        "checked_at": doc["checked_at"],
        "checked_by": doc.get("checked_by", ""),
        "status": doc.get("status") or classify_result(r),
        "effective_status": doc.get("effective_status"),
        "payer_id": doc.get("payer_id"),
        "payer_name": payer_name_map.get(doc.get("payer_id", "")),
        "plan_name": r.get("plan_name"),
        "service_date": doc.get("service_date"),
        "appointment_id": doc.get("appointment_id"),
        "policy_id": doc.get("policy_id"),
        "copay_cents": r.get("copay_cents"),
        "deductible_cents": r.get("deductible_cents"),
        "deductible_met_cents": r.get("deductible_met_cents"),
        "coinsurance_pct": r.get("coinsurance_pct"),
        "out_of_pocket_cents": r.get("out_of_pocket_cents"),
        "service_type_codes": doc.get("service_type_codes") or [],
        "sandbox": bool(doc.get("sandbox")),
        "rejection_reason": r.get("rejection_reason"),
    }


async def _payer_name_map(db: Any, ctx: TenantContext) -> dict[str, str]:
    out: dict[str, str] = {}
    async for p in db.billing_payers.find(
        {"tenant_id": ctx.tenant_id}, {"_id": 0, "id": 1, "name": 1},
    ):
        out[p["id"]] = p.get("name")
    return out


# ---------------------------------------------------------------------------
# Reference endpoint — UI bootstrap
# ---------------------------------------------------------------------------
@router.get(
    "/eligibility/reference",
    response_model=EligibilityStatusReference,
)
async def eligibility_reference(
    user: dict = Depends(require_permission("insurance", "read")),
):
    return {
        "statuses": list(ELIGIBILITY_STATUSES),
        "labels": STATUS_LABELS,
        "tones": STATUS_TONES,
        "disclaimer": DISCLAIMER_TEXT,
    }


# ---------------------------------------------------------------------------
# Policy-anchored endpoints
# ---------------------------------------------------------------------------
@router.post(
    "/policies/{policy_id}/eligibility-check",
    response_model=EligibilityCheckPublic,
    status_code=status.HTTP_201_CREATED,
)
async def run_eligibility_check_policy(
    policy_id: str,
    payload: EligibilityCheckCreate,
    request: Request,
    user: dict = Depends(require_permission("insurance", "read")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    policy, patient, payer, provider = await _load_patient_policy(
        db, ctx, policy_id=policy_id,
    )
    doc = await _run_and_persist(
        db, ctx, user, request,
        policy=policy, patient=patient, payer=payer, provider=provider,
        payload=payload,
    )
    return {k: v for k, v in doc.items() if k != "_id"}


@router.get(
    "/policies/{policy_id}/eligibility-checks",
    response_model=list[EligibilityCheckSummary],
)
async def list_eligibility_checks_for_policy(
    policy_id: str,
    user: dict = Depends(require_permission("insurance", "read")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    policy = await db.patient_insurance_policies.find_one(
        {"id": policy_id, "tenant_id": ctx.tenant_id}, {"_id": 0},
    )
    if not policy:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Policy not found")
    snap = policy_snapshot_hash(policy)
    name_map = await _payer_name_map(db, ctx)
    rows: list[dict] = []
    async for row in db.eligibility_checks.find(
        {"policy_id": policy_id, "tenant_id": ctx.tenant_id},
        {"_id": 0, "request_wire": 0, "response_wire": 0},
    ).sort("checked_at", -1):
        overlaid = overlay_expiration(row, target_policy_snapshot=snap)
        rows.append(_summary_from_doc(overlaid, name_map))
    return rows


# ---------------------------------------------------------------------------
# Patient-anchored endpoints (profile surface)
# ---------------------------------------------------------------------------
@router.post(
    "/patients/{patient_id}/eligibility-check",
    response_model=EligibilityCheckPublic,
    status_code=status.HTTP_201_CREATED,
)
async def run_eligibility_check_patient(
    patient_id: str,
    payload: EligibilityCheckCreate,
    request: Request,
    user: dict = Depends(require_permission("insurance", "read")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    policy, patient, payer, provider = await _load_patient_policy(
        db, ctx, patient_id=patient_id,
    )
    doc = await _run_and_persist(
        db, ctx, user, request,
        policy=policy, patient=patient, payer=payer, provider=provider,
        payload=payload,
    )
    return {k: v for k, v in doc.items() if k != "_id"}


@router.get(
    "/patients/{patient_id}/eligibility-latest",
    response_model=EligibilityCheckSummary | None,
)
async def patient_eligibility_latest(
    patient_id: str,
    service_date: str | None = None,
    user: dict = Depends(require_permission("insurance", "read")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    target = service_date or _today_iso()
    name_map = await _payer_name_map(db, ctx)
    row = await db.eligibility_checks.find_one(
        {"patient_id": patient_id, "tenant_id": ctx.tenant_id},
        {"_id": 0, "request_wire": 0, "response_wire": 0},
        sort=[("checked_at", -1)],
    )
    if not row:
        return None
    overlaid = overlay_expiration(row, target_service_date=target)
    return _summary_from_doc(overlaid, name_map)


@router.get(
    "/patients/{patient_id}/eligibility-checks",
    response_model=list[EligibilityCheckSummary],
)
async def patient_eligibility_history(
    patient_id: str,
    user: dict = Depends(require_permission("insurance", "read")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    name_map = await _payer_name_map(db, ctx)
    rows: list[dict] = []
    async for row in db.eligibility_checks.find(
        {"patient_id": patient_id, "tenant_id": ctx.tenant_id},
        {"_id": 0, "request_wire": 0, "response_wire": 0},
    ).sort("checked_at", -1):
        overlaid = overlay_expiration(row)
        rows.append(_summary_from_doc(overlaid, name_map))
    return rows


# ---------------------------------------------------------------------------
# Appointment-anchored endpoints
# ---------------------------------------------------------------------------
@router.post(
    "/appointments/{appointment_id}/eligibility-check",
    response_model=EligibilityCheckPublic,
    status_code=status.HTTP_201_CREATED,
)
async def run_eligibility_check_appointment(
    appointment_id: str,
    payload: EligibilityCheckCreate,
    request: Request,
    user: dict = Depends(require_permission("insurance", "read")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    appointment = await db.appointments.find_one(
        {"id": appointment_id, "tenant_id": ctx.tenant_id}, {"_id": 0},
    )
    if not appointment:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Appointment not found",
        )
    policy, patient, payer, provider = await _load_patient_policy(
        db, ctx, patient_id=appointment["patient_id"],
    )
    # Force the service date + appointment linkage so the caller does
    # not have to supply them explicitly.
    service_date = (appointment.get("start_time") or "")[:10] or _today_iso()
    payload_locked = payload.model_copy(update={
        "appointment_id": appointment_id,
        "inquiry_date": payload.inquiry_date or service_date,
    })
    doc = await _run_and_persist(
        db, ctx, user, request,
        policy=policy, patient=patient, payer=payer, provider=provider,
        payload=payload_locked,
    )
    return {k: v for k, v in doc.items() if k != "_id"}


@router.get(
    "/appointments/{appointment_id}/eligibility-latest",
    response_model=EligibilityCheckSummary | None,
)
async def appointment_eligibility_latest(
    appointment_id: str,
    user: dict = Depends(require_permission("insurance", "read")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    appointment = await db.appointments.find_one(
        {"id": appointment_id, "tenant_id": ctx.tenant_id}, {"_id": 0},
    )
    if not appointment:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Appointment not found",
        )
    service_date = (appointment.get("start_time") or "")[:10] or _today_iso()
    name_map = await _payer_name_map(db, ctx)
    # Prefer a check explicitly bound to this appointment; fall back
    # to the patient's most recent check matching the same DOS.
    row = await db.eligibility_checks.find_one(
        {"appointment_id": appointment_id, "tenant_id": ctx.tenant_id},
        {"_id": 0, "request_wire": 0, "response_wire": 0},
        sort=[("checked_at", -1)],
    )
    if not row:
        row = await db.eligibility_checks.find_one(
            {
                "patient_id": appointment["patient_id"],
                "tenant_id": ctx.tenant_id,
                "service_date": service_date,
            },
            {"_id": 0, "request_wire": 0, "response_wire": 0},
            sort=[("checked_at", -1)],
        )
    if not row:
        return None
    overlaid = overlay_expiration(row, target_service_date=service_date)
    return _summary_from_doc(overlaid, name_map)


# ---------------------------------------------------------------------------
# Detail — MFA-gated
# ---------------------------------------------------------------------------
@router.get(
    "/eligibility-checks/{check_id}",
    response_model=EligibilityCheckPublic,
)
async def get_eligibility_check(
    check_id: str,
    request: Request,
    user: dict = Depends(require_permission("insurance", "read")),
    ctx: TenantContext = Depends(require_tenant),
):
    """Full 270/271 wires + parsed result. MFA-gated AND restricted to
    admin/billing roles — raw payloads carry PHI we don't expose to
    front-desk/doctor summary readers."""
    role = (user.get("role") or "").lower()
    if role not in ("admin", "billing", "staff"):
        # Doctors and patients never see raw wires.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Raw payer payload requires billing or admin access.",
        )
    require_reauth(request, user)
    db = tenant_db(ctx.tenant_id)
    row = await db.eligibility_checks.find_one(
        {"id": check_id, "tenant_id": ctx.tenant_id}, {"_id": 0},
    )
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Check not found")
    await audit_success(
        user, "billing.eligibility.payload_viewed", request,
        entity_type="eligibility_check", entity_id=check_id,
        metadata={"policy_id": row.get("policy_id"),
                  "appointment_id": row.get("appointment_id")},
    )
    overlaid = overlay_expiration(
        row,
        target_service_date=row.get("service_date"),
        target_policy_snapshot=row.get("policy_snapshot_hash"),
    )
    return overlaid
