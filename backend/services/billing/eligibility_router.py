"""
services/billing/eligibility_router.py — Eligibility 270/271 endpoints.

Routes
------
POST /billing/policies/{policy_id}/eligibility-check
    Run a fresh eligibility inquiry against the policy's payer. Builds
    a spec-compliant 270, synthesises (or fetches, when live) a 271,
    persists an `eligibility_checks` row, and returns the canonical
    result + the full 270/271 wires for audit / preview.

GET /billing/policies/{policy_id}/eligibility-checks
    List historical eligibility checks for a policy (reverse
    chronological).

GET /billing/eligibility-checks/{check_id}
    Retrieve a single historical check (full 270/271 wires + parsed
    result). MFA-gated via `require_reauth` because the wire payload
    carries PHI (member_id / DOB in plaintext per X12 spec).
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
from services.billing.eligibility import default_engine


router = APIRouter(prefix="/billing", tags=["billing", "eligibility"])


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class EligibilityCheckCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service_type_codes: list[str] | None = Field(
        default=None,
        description=(
            "Optional list of X12 service-type codes. Default is "
            "['30', '33', '98'] (plan / chiropractic / professional)."
        ),
    )
    inquiry_date: str | None = Field(
        default=None, pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Date of service being inquired about. Default today.",
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


class EligibilityCheckPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    tenant_id: str
    patient_id: str
    policy_id: str
    payer_id: str
    provider_id: str | None = None
    engine: str
    sandbox: bool
    service_type_codes: list[str]
    checked_at: str
    checked_by: str
    result: EligibilityResult
    # Raw wires are omitted from the list endpoint (size) and included
    # on the detail endpoint. `None` on list responses by default.
    request_wire: str | None = None
    response_wire: str | None = None


class EligibilityCheckSummary(BaseModel):
    """Lightweight row for list views."""
    id: str
    checked_at: str
    checked_by: str
    coverage_active: bool
    plan_name: str | None = None
    copay_cents: int | None = None
    deductible_cents: int | None = None
    deductible_met_cents: int | None = None
    service_type_codes: list[str]
    sandbox: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _load_billing_context(
    db: Any, ctx: TenantContext, policy_id: str,
) -> tuple[dict, dict, dict, dict | None]:
    """Fetch (policy, patient, payer, provider?) scoped to the tenant.

    Raises 404 when any required row is missing. `provider` may be
    None — the 270 still builds (NM1*1P will carry the billing
    provider's legal name / NPI as fallback)."""
    policy = await db.patient_insurance_policies.find_one(
        {"id": policy_id, "tenant_id": ctx.tenant_id}, {"_id": 0},
    )
    if not policy:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Policy not found",
        )
    patient = await db.patients.find_one(
        {"id": policy["patient_id"], "tenant_id": ctx.tenant_id},
        {"_id": 0},
    )
    if not patient:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Patient referenced by policy not found",
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
    # Provider — prefer the patient's primary provider; fall back to
    # the billing org. Both land at the 2100B NM1*1P slot on the 270.
    provider = None
    provider_id = patient.get("primary_provider_id")
    if provider_id:
        provider = await db.providers.find_one(
            {"id": provider_id, "tenant_id": ctx.tenant_id}, {"_id": 0},
        )
    if not provider:
        # Billing provider fallback — find the first `entity_type=org`
        # row tagged as the billing group. Every demo tenant has one.
        provider = await db.providers.find_one(
            {"tenant_id": ctx.tenant_id, "entity_type": "org"},
            {"_id": 0},
        )
    return policy, patient, payer, provider


def _build_submitter_envelope(
    provider: dict | None, tenant_id: str,
) -> dict:
    """Build the submitter block from whatever billing provider we
    have. When no provider row is available, fall back to a tenant
    code so the ISA/GS envelope still validates."""
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


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.post(
    "/policies/{policy_id}/eligibility-check",
    response_model=EligibilityCheckPublic,
    status_code=status.HTTP_201_CREATED,
)
async def run_eligibility_check(
    policy_id: str,
    payload: EligibilityCheckCreate,
    request: Request,
    user: dict = Depends(require_permission("insurance", "read")),
    ctx: TenantContext = Depends(require_tenant),
):
    """Run a fresh 270 eligibility inquiry against the policy's payer."""
    db = tenant_db(ctx.tenant_id)
    policy, patient, payer, provider = await _load_billing_context(
        db, ctx, policy_id,
    )
    submitter = _build_submitter_envelope(provider, ctx.tenant_id)
    receiver = {
        "id": (payer.get("electronic_payer_id")
               or payer.get("payer_code") or "PAYER"),
        "name": payer.get("name") or "PAYER",
    }

    engine = default_engine()
    try:
        outcome = engine.check(
            submitter=submitter,
            receiver=receiver,
            provider=provider or submitter,  # fallback shape
            payer=payer,
            patient=patient,
            policy=policy,
            service_type_codes=payload.service_type_codes,
            inquiry_date=payload.inquiry_date,
        )
    except Exception as exc:  # noqa: BLE001
        await audit_failure(
            user, "billing.eligibility.check_failed", request,
            entity_type="patient_insurance_policy", entity_id=policy_id,
            metadata={"error": str(exc)[:500]},
        )
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Eligibility check failed: {exc}",
        )

    check_id = str(uuid.uuid4())
    doc = {
        "id": check_id,
        "tenant_id": ctx.tenant_id,
        "patient_id": policy["patient_id"],
        "policy_id": policy_id,
        "payer_id": policy["payer_id"],
        "provider_id": (provider or {}).get("id"),
        "engine": outcome["engine"],
        "sandbox": outcome["sandbox"],
        "service_type_codes": outcome["service_type_codes"],
        "request_wire": outcome["request_wire"],
        "response_wire": outcome["response_wire"],
        "result": outcome["result"],
        "checked_at": outcome["checked_at"],
        "checked_by": user["id"],
    }
    await db.eligibility_checks.insert_one(doc)
    await audit_success(
        user, "billing.eligibility.checked", request,
        entity_type="patient_insurance_policy", entity_id=policy_id,
        metadata={
            "check_id": check_id,
            "coverage_active": outcome["result"].get("coverage_active"),
            "engine": outcome["engine"],
            "sandbox": outcome["sandbox"],
        },
    )
    return {k: v for k, v in doc.items() if k != "_id"}


@router.get(
    "/policies/{policy_id}/eligibility-checks",
    response_model=list[EligibilityCheckSummary],
)
async def list_eligibility_checks_for_policy(
    policy_id: str,
    request: Request,
    user: dict = Depends(require_permission("insurance", "read")),
    ctx: TenantContext = Depends(require_tenant),
):
    """Historical eligibility checks for a policy (reverse chrono)."""
    db = tenant_db(ctx.tenant_id)
    policy = await db.patient_insurance_policies.find_one(
        {"id": policy_id, "tenant_id": ctx.tenant_id}, {"_id": 0},
    )
    if not policy:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Policy not found")
    cursor = db.eligibility_checks.find(
        {"policy_id": policy_id, "tenant_id": ctx.tenant_id},
        {"_id": 0, "request_wire": 0, "response_wire": 0},
    ).sort("checked_at", -1)
    rows: list[dict] = []
    async for row in cursor:
        r = row.get("result") or {}
        rows.append({
            "id": row["id"],
            "checked_at": row["checked_at"],
            "checked_by": row.get("checked_by", ""),
            "coverage_active": bool(r.get("coverage_active")),
            "plan_name": r.get("plan_name"),
            "copay_cents": r.get("copay_cents"),
            "deductible_cents": r.get("deductible_cents"),
            "deductible_met_cents": r.get("deductible_met_cents"),
            "service_type_codes": row.get("service_type_codes") or [],
            "sandbox": bool(row.get("sandbox")),
        })
    return rows


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
    """Full 270/271 wires + parsed result. MFA-gated."""
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
        metadata={"policy_id": row["policy_id"]},
    )
    return row
