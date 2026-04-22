"""
Billing Router — `/api/billing/*`

Foundation endpoints. Every mutation:
  1. Runs through `require_permission(...)` against the canonical RBAC policy.
  2. Stamps `tenant_id` via `stamp_for_write` so tenancy isolation is enforced.
  3. Writes a semantic audit row via `audit_success` / `audit_failure`.
  4. Uses `transitions.http_advance` for every status change.

No payer-specific business logic lives here. Adapters (e.g. clearinghouse
submission workers) consume the canonical model through these routes.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Literal, get_args

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response

from core.audit import audit_success, audit_failure
from core.deps import get_current_user, require_role
from core.tenancy import TenantContext, require_tenant, tenant_db
from core.tenant_scope import scoped_filter, stamp_for_write
from services.authz.policy import require_permission
from services.billing import transitions
from services.billing.ledger import build_patient_ledger
from services.billing.denial_categories import (
    DENIAL_CATEGORIES,
    DENIAL_CATEGORY_LABELS,
)
from services.billing.remittance import (
    compute_ar_buckets,
    post_remittance,
    render_statement_body,
)
from services.billing.remittance_import import (
    match_claims,
    parse_835,
    parse_json_import,
    resolve_payer_id,
)
from services.billing.statement_delivery import (
    render_statement_email_html,
    render_statement_pdf,
    send_statement_email,
)
from services.billing.clearinghouse import (
    config_summaries,
    get_adapter_for_payer,
)
from services.billing.canonical_status import (
    CANONICAL_LABELS,
    CANONICAL_STATUSES,
    canonical_status,
    raw_statuses_for_canonical,
)
from services.billing.events import emit_claim_event
from services.billing.submission import (
    DEFAULT_FOLLOWUP_DAYS,
    build_json_payload,
    followup_claim_ids,
    followup_threshold_iso,
)
from services.billing.clearinghouse.x12_837p import build_x12_837p_wire
from services.billing.models import (
    AdjustmentCreate,
    AdjustmentPublic,
    AgingBucket,
    ClaimAssignmentUpdate,
    ClaimCreate,
    ClaimEventPublic,
    ClaimPublic,
    ClaimStatus,
    ClaimBulkSubmitRequest,
    ClaimFollowupFlagRequest,
    ClaimSubmissionCreate,
    ClaimSubmissionOutcome,
    ClaimSubmissionPublic,
    ClearinghouseConfigSummary,
    ClearinghouseEnrollmentCreate,
    ClearinghouseEnrollmentPublic,
    ClearinghouseEnrollmentUpdate,
    ClearinghouseReportIngestRequest,
    ClearinghouseReportPublic,
    DenialWorkItemPublic,
    DenialWorkItemUpdate,
    DEFAULT_CURRENCY,
    InvoiceCreate,
    InvoiceLinePublic,
    InvoicePublic,
    InvoiceStatus,
    PatientInsurancePolicyCreate,
    PatientInsurancePolicyPublic,
    PayerCreate,
    PayerPublic,
    PayerUpdate,
    PaymentCreate,
    PaymentPublic,
    PaymentStatus,
    ProviderCreate,
    ProviderPublic,
    ProviderUpdate,
    RefundCreate,
    RefundPublic,
    RemittanceClaimPublic,
    RemittanceLinePublic,
    RemittancePostRequest,
    RemittancePublic,
    ServiceFacilityCreate,
    ServiceFacilityPublic,
    ServiceFacilityUpdate,
    StatementPublic,
)
from services.clinical.billing_readiness_router import evaluate_billing_readiness
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(prefix="/billing", tags=["billing"])


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _days_since_iso(iso_ts: str | None) -> int | None:
    """Return whole-days delta between `iso_ts` and now. Used by the
    Claims Queue to expose `aging_days` on every row without making
    the UI parse timestamps client-side."""
    if not iso_ts:
        return None
    try:
        ts = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - ts
    return max(0, delta.days)


def _public(doc: dict) -> dict:
    out = {k: v for k, v in doc.items() if k != "_id"}
    out.pop("history", None)
    return out


def _history_entry(user: dict, action: str, **extra) -> dict:
    entry = {"at": _now(), "by": user["id"], "action": action}
    entry.update(extra)
    return entry


async def _scoped_one(coll, q: dict, ctx: TenantContext) -> dict | None:
    """Fetch a single row with tenancy scoping applied. Returns None if
    denied or missing."""
    q = scoped_filter(q, ctx, location_scoped=False)
    if q.get("__deny__"):
        return None
    return await coll.find_one(q, {"_id": 0})


# ---------------------------------------------------------------------------
# Financial recompute — the single source of truth for invoice balance
# ---------------------------------------------------------------------------
async def _recompute_invoice_balance(
    db, invoice_id: str, tenant_id: str, actor_id: str,
) -> dict | None:
    """Recompute `balance_cents` and auto-advance invoice status.

    balance = total - applied_payments - adjustments + refunds_reversing_invoice_payments

    Called after every event that can change the ledger against an invoice:
      * payment allocation created
      * adjustment created
      * refund processed
      * payment status changed to void/failed (allocation no longer counts)

    Returns the refreshed invoice doc (or None if the invoice has
    disappeared). Never raises: callers can rely on it being idempotent.
    """
    inv = await db.invoices.find_one(
        {"id": invoice_id, "tenant_id": tenant_id}, {"_id": 0},
    )
    if not inv:
        return None

    # Sum allocations — only from payments that are NOT void/failed.
    # Refunds against a payment reduce the effective applied amount on
    # the invoices that payment touched, proportionally to the original
    # allocation. So if a $100 payment had a 60/40 split across two
    # invoices and $50 was refunded, $30 is reversed from invoice A and
    # $20 from invoice B.
    applied = 0
    async for alloc in db.payment_allocations.find(
        {"tenant_id": tenant_id, "invoice_id": invoice_id}, {"_id": 0},
    ):
        pmt = await db.payments.find_one(
            {"id": alloc["payment_id"], "tenant_id": tenant_id},
            {"_id": 0, "status": 1, "amount_cents": 1},
        )
        if not pmt or pmt.get("status") in ("void", "failed"):
            continue

        # Processed refunds on this payment.
        refunded_on_pmt = 0
        async for rf in db.refunds.find(
            {"tenant_id": tenant_id, "payment_id": alloc["payment_id"],
             "status": "processed"}, {"_id": 0, "amount_cents": 1},
        ):
            refunded_on_pmt += rf["amount_cents"]

        alloc_amount = alloc["amount_cents"]
        if refunded_on_pmt <= 0 or pmt["amount_cents"] <= 0:
            effective = alloc_amount
        else:
            # Proportional reversal, bounded between 0 and the original alloc.
            reversed_share = (
                refunded_on_pmt * alloc_amount
            ) // pmt["amount_cents"]
            effective = max(alloc_amount - reversed_share, 0)
        applied += effective

    # Sum adjustments (writeoffs / discounts / etc).
    adjustments = 0
    async for adj in db.billing_adjustments.find(
        {"tenant_id": tenant_id, "invoice_id": invoice_id}, {"_id": 0},
    ):
        adjustments += adj["amount_cents"]

    total = inv.get("total_cents", 0)
    balance = max(total - applied - adjustments, 0)

    # Auto status progression — but respect terminal states (void, refunded).
    current_status = inv["status"]
    next_status = current_status
    if current_status not in ("void", "refunded", "draft"):
        if balance == 0 and total > 0:
            next_status = "paid"
        elif applied > 0 or adjustments > 0:
            # Some money was applied but balance still owed.
            next_status = "partially_paid"
        else:
            # No money applied, no adjustments — either still issued,
            # or a full refund has wiped prior allocations so we fall
            # back to "issued" from a previously paid state.
            next_status = "issued"

    set_fields = {
        "balance_cents": balance,
        "adjustment_cents": adjustments,
        "updated_at": _now(),
        "updated_by": actor_id,
    }
    history_action = "balance_recomputed"
    if next_status != current_status:
        # Validate the transition via the same helper used by the router.
        try:
            transitions.advance("invoice", current_status, next_status)
            set_fields["status"] = next_status
            history_action = "balance_recomputed_status_advanced"
        except transitions.TransitionError:
            # Fall back to keeping the current status — balance still updates.
            next_status = current_status

    await db.invoices.update_one(
        {"id": invoice_id, "tenant_id": tenant_id},
        {
            "$set": set_fields,
            "$push": {"history": {
                "at": set_fields["updated_at"], "by": actor_id,
                "action": history_action,
                "applied_cents": applied,
                "adjustment_cents": adjustments,
                "balance_cents": balance,
                "from_status": current_status,
                "to_status": next_status,
            }},
        },
    )
    return await db.invoices.find_one(
        {"id": invoice_id, "tenant_id": tenant_id}, {"_id": 0},
    )


# ---------------------------------------------------------------------------
# PAYERS  —  /api/billing/payers
# ---------------------------------------------------------------------------
@router.get("/payers", response_model=list[PayerPublic])
async def list_payers(
    request: Request,
    active_only: bool = Query(default=False),
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    q = scoped_filter({}, ctx, location_scoped=False)
    if q.get("__deny__"):
        return []
    if active_only:
        q["status"] = "active"
    cursor = db.billing_payers.find(q, {"_id": 0}).sort([("name", 1)])
    rows = [_public(d) async for d in cursor]
    await audit_success(
        user, "billing.payer.list_viewed", request,
        metadata={"count": len(rows), "active_only": active_only},
    )
    return rows


@router.post("/payers", response_model=PayerPublic, status_code=201)
async def create_payer(
    payload: PayerCreate,
    request: Request,
    user: dict = Depends(require_permission("clinic_settings", "update")),
    ctx: TenantContext = Depends(require_tenant),
):
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tenant context required")
    db = tenant_db(ctx.tenant_id)
    existing = await db.billing_payers.find_one(
        {"tenant_id": ctx.tenant_id,
         "name": {"$regex": f"^{payload.name}$", "$options": "i"}},
        {"_id": 0, "id": 1},
    )
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Payer '{payload.name}' already exists")

    now = _now()
    pid = str(uuid.uuid4())
    doc = stamp_for_write({
        "id": pid,
        "name": payload.name,
        "payer_type": payload.payer_type,
        "payer_code": payload.payer_code,
        "electronic_payer_id": payload.electronic_payer_id,
        "remit_method": payload.remit_method,
        "notes": payload.notes,
        "status": "active",
        # Phase 2a — clearinghouse routing defaults
        "clearinghouse_route": payload.clearinghouse_route,
        "claim_submission_mode": payload.claim_submission_mode,
        "enrollment_status": payload.enrollment_status,
        "trading_partner_id": payload.trading_partner_id,
        # Phase 6 — clearinghouse-side routing ids + enrollment flag
        "claims_cpid": payload.claims_cpid,
        "realtime_payer_id": payload.realtime_payer_id,
        "enrollment_required": payload.enrollment_required,
        "routing_metadata": payload.routing_metadata,
        "routing_last_resolved_at": None,
        "created_at": now,
        "updated_at": now,
        "created_by": user["id"],
        "updated_by": user["id"],
        "history": [_history_entry(user, "created")],
    }, ctx, location_id=None)
    await db.billing_payers.insert_one(doc)
    await audit_success(
        user, "billing.payer.created", request,
        entity_type="billing_payer", entity_id=pid,
        metadata={"name": payload.name, "payer_type": payload.payer_type},
    )
    return _public(doc)


@router.patch("/payers/{payer_id}", response_model=PayerPublic)
async def update_payer(
    payer_id: str,
    payload: PayerUpdate,
    request: Request,
    user: dict = Depends(require_permission("clinic_settings", "update")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    current = await _scoped_one(db.billing_payers, {"id": payer_id}, ctx)
    if not current:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payer not found")

    updates = {k: v for k, v in payload.model_dump(exclude_unset=True).items()}
    if not updates:
        return _public(current)

    if "name" in updates and updates["name"] and updates["name"].lower() != current["name"].lower():
        clash = await db.billing_payers.find_one(
            {"tenant_id": ctx.tenant_id, "id": {"$ne": payer_id},
             "name": {"$regex": f"^{updates['name']}$", "$options": "i"}},
            {"_id": 0, "id": 1},
        )
        if clash:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"Payer '{updates['name']}' already exists")

    updates["updated_at"] = _now()
    updates["updated_by"] = user["id"]
    await db.billing_payers.update_one(
        {"id": payer_id, "tenant_id": ctx.tenant_id},
        {"$set": updates,
         "$push": {"history": _history_entry(
             user, "updated",
             fields=sorted(list(updates.keys() - {"updated_at", "updated_by"})),
         )}},
    )
    fresh = await db.billing_payers.find_one(
        {"id": payer_id, "tenant_id": ctx.tenant_id}, {"_id": 0},
    )
    await audit_success(
        user, "billing.payer.updated", request,
        entity_type="billing_payer", entity_id=payer_id,
        metadata={"fields": sorted(list(updates.keys()))},
    )
    return _public(fresh)


# ---------------------------------------------------------------------------
# PATIENT INSURANCE POLICIES  —  /api/billing/insurance-policies
# ---------------------------------------------------------------------------
@router.post("/insurance-policies",
             response_model=PatientInsurancePolicyPublic, status_code=201)
async def create_insurance_policy(
    payload: PatientInsurancePolicyCreate,
    request: Request,
    user: dict = Depends(require_permission("insurance", "create")),
    ctx: TenantContext = Depends(require_tenant),
):
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tenant context required")
    db = tenant_db(ctx.tenant_id)

    # Payer must exist in-tenant.
    payer = await _scoped_one(db.billing_payers, {"id": payload.payer_id}, ctx)
    if not payer:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payer not found")

    # Patient must exist in-tenant.
    patient = await _scoped_one(db.patients, {"id": payload.patient_id}, ctx)
    if not patient:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")

    now = _now()
    pid = str(uuid.uuid4())
    doc = stamp_for_write({
        "id": pid,
        "patient_id": payload.patient_id,
        "payer_id": payload.payer_id,
        "rank": payload.rank,
        "subscriber_name": payload.subscriber_name,
        "relationship_to_subscriber": payload.relationship_to_subscriber,
        "member_id": payload.member_id,
        "group_number": payload.group_number,
        "effective_date": payload.effective_date,
        "termination_date": payload.termination_date,
        "status": "active",
        # Phase 5 — structured subscriber identity.
        "subscriber_dob": payload.subscriber_dob,
        "subscriber_gender": payload.subscriber_gender,
        "subscriber_address": payload.subscriber_address,
        "notes": payload.notes,
        "created_at": now,
        "updated_at": now,
        "created_by": user["id"],
        "updated_by": user["id"],
        "history": [_history_entry(user, "created")],
    }, ctx, location_id=None)
    await db.patient_insurance_policies.insert_one(doc)
    await audit_success(
        user, "billing.insurance_policy.created", request,
        entity_type="patient_insurance_policy", entity_id=pid,
        metadata={"patient_id": payload.patient_id, "payer_id": payload.payer_id,
                  "rank": payload.rank},
    )
    return _public(doc)


@router.get("/insurance-policies",
            response_model=list[PatientInsurancePolicyPublic])
async def list_insurance_policies(
    request: Request,
    patient_id: str | None = Query(default=None),
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    q = scoped_filter({}, ctx, location_scoped=False)
    if q.get("__deny__"):
        return []
    if patient_id:
        q["patient_id"] = patient_id
    cursor = db.patient_insurance_policies.find(q, {"_id": 0}).sort(
        [("rank", 1), ("created_at", -1)],
    )
    rows = [_public(d) async for d in cursor]
    await audit_success(
        user, "billing.insurance_policy.list_viewed", request,
        metadata={"count": len(rows), "patient_id": patient_id},
    )
    return rows


# ---------------------------------------------------------------------------
# INVOICES  —  /api/billing/invoices
# ---------------------------------------------------------------------------
def _sum_invoice(lines: list[dict]) -> tuple[int, int]:
    subtotal = sum(ln["total_cents"] for ln in lines)
    return subtotal, subtotal   # total_cents == subtotal for now (no tax)


@router.post("/invoices", response_model=InvoicePublic, status_code=201)
async def create_invoice(
    payload: InvoiceCreate,
    request: Request,
    user: dict = Depends(require_permission("charge", "create")),
    ctx: TenantContext = Depends(require_tenant),
):
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tenant context required")
    db = tenant_db(ctx.tenant_id)

    patient = await _scoped_one(db.patients, {"id": payload.patient_id}, ctx)
    if not patient:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")

    now = _now()
    invoice_id = str(uuid.uuid4())

    line_docs: list[dict] = []
    for i, ln in enumerate(payload.lines, start=1):
        total = ln.unit_price_cents * ln.quantity
        line_docs.append(stamp_for_write({
            "id": str(uuid.uuid4()),
            "invoice_id": invoice_id,
            "sequence": i,
            "code_type": ln.code_type,
            "code": ln.code,
            "description": ln.description,
            "service_date": ln.service_date,
            "quantity": ln.quantity,
            "unit_price_cents": ln.unit_price_cents,
            "total_cents": total,
            "modifiers": ln.modifiers,
            "provider_id": ln.provider_id,
            "created_at": now,
        }, ctx, location_id=payload.location_id))

    subtotal, total = _sum_invoice(line_docs)
    invoice_doc = stamp_for_write({
        "id": invoice_id,
        "location_id": payload.location_id,
        "patient_id": payload.patient_id,
        "appointment_id": payload.appointment_id,
        "status": "draft",
        "issued_at": None,
        "due_date": payload.due_date,
        "currency": payload.currency or DEFAULT_CURRENCY,
        "subtotal_cents": subtotal,
        "tax_cents": 0,
        "adjustment_cents": 0,
        "total_cents": total,
        "balance_cents": total,
        "notes": payload.notes,
        "created_at": now,
        "updated_at": now,
        "created_by": user["id"],
        "updated_by": user["id"],
        "history": [_history_entry(user, "created",
                                   lines=len(line_docs),
                                   total_cents=total)],
    }, ctx, location_id=payload.location_id)

    # Write lines first so a partial invoice never observes parent-only rows.
    if line_docs:
        await db.invoice_lines.insert_many(line_docs)
    await db.invoices.insert_one(invoice_doc)

    await audit_success(
        user, "billing.invoice.created", request,
        entity_type="invoice", entity_id=invoice_id,
        metadata={
            "patient_id": payload.patient_id,
            "lines": len(line_docs),
            "total_cents": total,
            "currency": invoice_doc["currency"],
        },
    )
    return _public(invoice_doc)


@router.get("/invoices", response_model=list[InvoicePublic])
async def list_invoices(
    request: Request,
    patient_id: str | None = Query(default=None),
    status_filter: InvoiceStatus | None = Query(default=None, alias="status"),
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    q = scoped_filter({}, ctx, location_scoped=False)
    if q.get("__deny__"):
        return []
    if patient_id:
        q["patient_id"] = patient_id
    if status_filter:
        q["status"] = status_filter
    cursor = db.invoices.find(q, {"_id": 0}).sort([("created_at", -1)])
    rows = [_public(d) async for d in cursor]
    await audit_success(
        user, "billing.invoice.list_viewed", request,
        metadata={"count": len(rows), "patient_id": patient_id,
                  "status_filter": status_filter},
    )
    return rows


@router.get("/invoices/{invoice_id}", response_model=InvoicePublic)
async def get_invoice(
    invoice_id: str,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    inv = await _scoped_one(db.invoices, {"id": invoice_id}, ctx)
    if not inv:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    await audit_success(
        user, "billing.invoice.viewed", request,
        entity_type="invoice", entity_id=invoice_id,
    )
    return _public(inv)


@router.get("/invoices/{invoice_id}/lines",
            response_model=list[InvoiceLinePublic])
async def list_invoice_lines(
    invoice_id: str,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    inv = await _scoped_one(db.invoices, {"id": invoice_id}, ctx)
    if not inv:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    q = scoped_filter({"invoice_id": invoice_id}, ctx, location_scoped=False)
    cursor = db.invoice_lines.find(q, {"_id": 0}).sort([("sequence", 1)])
    rows = [_public(d) async for d in cursor]
    return rows


@router.post("/invoices/{invoice_id}/status", response_model=InvoicePublic)
async def transition_invoice_status(
    invoice_id: str,
    request: Request,
    desired: InvoiceStatus = Query(...),
    user: dict = Depends(require_permission("charge", "create")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    inv = await _scoped_one(db.invoices, {"id": invoice_id}, ctx)
    if not inv:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")

    new_status = transitions.http_advance("invoice", inv["status"], desired)
    if new_status == inv["status"]:
        return _public(inv)

    now = _now()
    set_fields: dict = {"status": new_status, "updated_at": now,
                        "updated_by": user["id"]}
    if new_status == "issued" and not inv.get("issued_at"):
        set_fields["issued_at"] = now

    await db.invoices.update_one(
        {"id": invoice_id, "tenant_id": ctx.tenant_id},
        {"$set": set_fields,
         "$push": {"history": _history_entry(
             user, "status_changed",
             from_status=inv["status"], to_status=new_status,
         )}},
    )
    fresh = await db.invoices.find_one(
        {"id": invoice_id, "tenant_id": ctx.tenant_id}, {"_id": 0},
    )
    await audit_success(
        user, "billing.invoice.status_changed", request,
        entity_type="invoice", entity_id=invoice_id,
        metadata={"from": inv["status"], "to": new_status},
    )
    return _public(fresh)


# ---------------------------------------------------------------------------
# PAYMENTS  —  /api/billing/payments
# ---------------------------------------------------------------------------
@router.post("/payments", response_model=PaymentPublic, status_code=201)
async def create_payment(
    payload: PaymentCreate,
    request: Request,
    user: dict = Depends(require_permission("payment", "collect")),
    ctx: TenantContext = Depends(require_tenant),
):
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tenant context required")
    db = tenant_db(ctx.tenant_id)

    patient = await _scoped_one(db.patients, {"id": payload.patient_id}, ctx)
    if not patient:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")

    # Validate allocations early: sum ≤ payment amount, each invoice exists.
    allocated_sum = sum(a.amount_cents for a in payload.allocations)
    if allocated_sum > payload.amount_cents:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "allocations exceed payment amount",
        )
    for alloc in payload.allocations:
        inv = await _scoped_one(db.invoices, {"id": alloc.invoice_id}, ctx)
        if not inv:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"invoice {alloc.invoice_id} not found",
            )
        if inv["status"] in ("void", "refunded"):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"cannot allocate to {inv['status']} invoice",
            )

    now = _now()
    payment_id = str(uuid.uuid4())
    payment_doc = stamp_for_write({
        "id": payment_id,
        "location_id": payload.location_id,
        "patient_id": payload.patient_id,
        "payer_id": payload.payer_id,
        "method": payload.method,
        "status": "pending",
        "amount_cents": payload.amount_cents,
        "allocated_cents": allocated_sum,
        "currency": payload.currency or DEFAULT_CURRENCY,
        "received_at": payload.received_at or now,
        "reference": payload.reference,
        "external_txn_id": payload.external_txn_id,
        "created_at": now,
        "updated_at": now,
        "created_by": user["id"],
        "updated_by": user["id"],
        "history": [_history_entry(user, "created",
                                   amount_cents=payload.amount_cents,
                                   allocations=len(payload.allocations))],
    }, ctx, location_id=payload.location_id)

    alloc_docs: list[dict] = []
    for a in payload.allocations:
        alloc_docs.append(stamp_for_write({
            "id": str(uuid.uuid4()),
            "payment_id": payment_id,
            "invoice_id": a.invoice_id,
            "invoice_line_id": a.invoice_line_id,
            "amount_cents": a.amount_cents,
            "created_at": now,
        }, ctx, location_id=payload.location_id))

    await db.payments.insert_one(payment_doc)
    if alloc_docs:
        await db.payment_allocations.insert_many(alloc_docs)

    # Auto-capture cash/check payments — these are money-in-hand at the
    # front desk and don't need a separate gateway step. Card / ACH stays
    # "pending" until the operator records the gateway result.
    if payload.method in ("cash", "check"):
        await db.payments.update_one(
            {"id": payment_id, "tenant_id": ctx.tenant_id},
            {"$set": {"status": "captured", "updated_at": now}},
        )
        payment_doc["status"] = "captured"

    # Recompute balance on every touched invoice. Only issued invoices
    # normally receive allocations; we still call recompute on drafts so
    # the ledger stays consistent if the operator pre-applied a payment.
    for a in payload.allocations:
        await _recompute_invoice_balance(
            db, a.invoice_id, ctx.tenant_id, user["id"],
        )

    await audit_success(
        user, "billing.payment.created", request,
        entity_type="payment", entity_id=payment_id,
        metadata={"patient_id": payload.patient_id,
                  "method": payload.method,
                  "amount_cents": payload.amount_cents,
                  "allocations": len(alloc_docs)},
    )
    return _public(payment_doc)


@router.post("/payments/{payment_id}/status", response_model=PaymentPublic)
async def transition_payment_status(
    payment_id: str,
    request: Request,
    desired: PaymentStatus = Query(...),
    user: dict = Depends(require_permission("payment", "collect")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    pmt = await _scoped_one(db.payments, {"id": payment_id}, ctx)
    if not pmt:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment not found")

    new_status = transitions.http_advance("payment", pmt["status"], desired)
    if new_status == pmt["status"]:
        return _public(pmt)

    now = _now()
    await db.payments.update_one(
        {"id": payment_id, "tenant_id": ctx.tenant_id},
        {"$set": {"status": new_status, "updated_at": now,
                  "updated_by": user["id"]},
         "$push": {"history": _history_entry(
             user, "status_changed",
             from_status=pmt["status"], to_status=new_status,
         )}},
    )
    fresh = await db.payments.find_one(
        {"id": payment_id, "tenant_id": ctx.tenant_id}, {"_id": 0},
    )

    # If the payment went into/out of an "effectively zero" status, the
    # ledger math for all its allocated invoices needs to be redone.
    if pmt["status"] != new_status and (
        new_status in ("void", "failed")
        or pmt["status"] in ("void", "failed")
    ):
        alloc_invoices = set()
        async for a in db.payment_allocations.find(
            {"tenant_id": ctx.tenant_id, "payment_id": payment_id},
            {"_id": 0, "invoice_id": 1},
        ):
            alloc_invoices.add(a["invoice_id"])
        for inv_id in alloc_invoices:
            await _recompute_invoice_balance(db, inv_id, ctx.tenant_id, user["id"])

    await audit_success(
        user, "billing.payment.status_changed", request,
        entity_type="payment", entity_id=payment_id,
        metadata={"from": pmt["status"], "to": new_status},
    )
    return _public(fresh)


@router.get("/payments", response_model=list[PaymentPublic])
async def list_payments(
    request: Request,
    patient_id: str | None = Query(default=None),
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    q = scoped_filter({}, ctx, location_scoped=False)
    if q.get("__deny__"):
        return []
    if patient_id:
        q["patient_id"] = patient_id
    cursor = db.payments.find(q, {"_id": 0}).sort([("received_at", -1)])
    rows = [_public(d) async for d in cursor]
    await audit_success(
        user, "billing.payment.list_viewed", request,
        metadata={"count": len(rows), "patient_id": patient_id},
    )
    return rows


# ---------------------------------------------------------------------------
# REFUNDS  —  /api/billing/refunds
# ---------------------------------------------------------------------------
@router.post("/refunds", response_model=RefundPublic, status_code=201)
async def create_refund(
    payload: RefundCreate,
    request: Request,
    user: dict = Depends(require_permission("payment", "refund")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    pmt = await _scoped_one(db.payments, {"id": payload.payment_id}, ctx)
    if not pmt:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment not found")
    if pmt["status"] in ("void", "failed"):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"cannot refund a {pmt['status']} payment")
    if payload.amount_cents > pmt["amount_cents"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "refund exceeds payment amount")

    # Guard against over-refunding: existing pending/processed refunds
    # against this payment plus the new one must not exceed the payment.
    existing_refunds = 0
    async for r in db.refunds.find(
        {"tenant_id": ctx.tenant_id, "payment_id": pmt["id"]},
        {"_id": 0, "amount_cents": 1, "status": 1},
    ):
        if r.get("status") != "failed":
            existing_refunds += r["amount_cents"]
    if existing_refunds + payload.amount_cents > pmt["amount_cents"]:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "refund would exceed payment remaining balance",
        )

    now = _now()
    rid = str(uuid.uuid4())
    doc = stamp_for_write({
        "id": rid,
        "payment_id": payload.payment_id,
        "amount_cents": payload.amount_cents,
        "reason": payload.reason,
        # Phase 1 posts refunds as immediately processed. When a real
        # payment gateway is added, this will start in "pending" and flip
        # to "processed" on gateway confirmation.
        "status": "processed",
        "processed_at": now,
        "created_at": now,
        "updated_at": now,
        "created_by": user["id"],
        "updated_by": user["id"],
        "history": [_history_entry(user, "created"),
                    _history_entry(user, "processed")],
    }, ctx, location_id=None)
    await db.refunds.insert_one(doc)

    # Advance the payment status reflecting refund state.
    total_refunds = existing_refunds + payload.amount_cents
    if total_refunds >= pmt["amount_cents"]:
        new_pmt_status = "refunded"
    else:
        new_pmt_status = "partially_refunded"
    try:
        allowed = transitions.advance("payment", pmt["status"], new_pmt_status)
    except transitions.TransitionError:
        # If the current payment status doesn't allow the transition
        # (e.g. still pending) we park it — the refund row still posts,
        # but the payment state stays where it is.
        allowed = pmt["status"]
    if allowed != pmt["status"]:
        await db.payments.update_one(
            {"id": pmt["id"], "tenant_id": ctx.tenant_id},
            {"$set": {"status": allowed, "updated_at": now,
                      "updated_by": user["id"]},
             "$push": {"history": _history_entry(
                 user, "refund_applied",
                 from_status=pmt["status"], to_status=allowed,
                 refund_id=rid,
             )}},
        )

    # Recompute balance on every invoice this payment touched — the
    # cash has effectively left, so the invoice balance re-inflates.
    touched: set[str] = set()
    async for a in db.payment_allocations.find(
        {"tenant_id": ctx.tenant_id, "payment_id": pmt["id"]},
        {"_id": 0, "invoice_id": 1},
    ):
        touched.add(a["invoice_id"])
    for inv_id in touched:
        await _recompute_invoice_balance(db, inv_id, ctx.tenant_id, user["id"])

    await audit_success(
        user, "billing.refund.created", request,
        entity_type="refund", entity_id=rid,
        metadata={"payment_id": payload.payment_id,
                  "amount_cents": payload.amount_cents,
                  "new_payment_status": allowed,
                  "invoices_recomputed": len(touched)},
    )
    return _public(doc)


# ---------------------------------------------------------------------------
# ADJUSTMENTS / WRITEOFFS  —  /api/billing/adjustments
# ---------------------------------------------------------------------------
@router.post("/adjustments", response_model=AdjustmentPublic, status_code=201)
async def create_adjustment(
    payload: AdjustmentCreate,
    request: Request,
    user: dict = Depends(require_permission("adjustment", "writeoff")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    inv = await _scoped_one(db.invoices, {"id": payload.invoice_id}, ctx)
    if not inv:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    if inv["status"] in ("void", "refunded"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"cannot adjust a {inv['status']} invoice",
        )

    now = _now()
    aid = str(uuid.uuid4())
    doc = stamp_for_write({
        "id": aid,
        "invoice_id": payload.invoice_id,
        "invoice_line_id": payload.invoice_line_id,
        "kind": payload.kind,
        "amount_cents": payload.amount_cents,
        "reason": payload.reason,
        "approved_by_id": user["id"],
        "created_at": now,
        "updated_at": now,
        "created_by": user["id"],
        "updated_by": user["id"],
        "history": [_history_entry(user, "created", kind=payload.kind)],
    }, ctx, location_id=inv.get("location_id"))
    await db.billing_adjustments.insert_one(doc)
    # Adjustment changes the invoice balance — recompute & maybe auto-advance status.
    await _recompute_invoice_balance(
        db, payload.invoice_id, ctx.tenant_id, user["id"],
    )
    await audit_success(
        user, "billing.adjustment.created", request,
        entity_type="billing_adjustment", entity_id=aid,
        metadata={"invoice_id": payload.invoice_id, "kind": payload.kind,
                  "amount_cents": payload.amount_cents},
    )
    return _public(doc)


# ---------------------------------------------------------------------------
# CLAIMS  —  /api/billing/claims
# ---------------------------------------------------------------------------
@router.post("/claims", response_model=ClaimPublic, status_code=201)
async def create_claim(
    payload: ClaimCreate,
    request: Request,
    user: dict = Depends(require_permission("claim", "create")),
    ctx: TenantContext = Depends(require_tenant),
):
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tenant context required")
    db = tenant_db(ctx.tenant_id)

    # Required foreign keys must resolve within tenant.
    patient = await _scoped_one(db.patients, {"id": payload.patient_id}, ctx)
    if not patient:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    payer = await _scoped_one(db.billing_payers, {"id": payload.payer_id}, ctx)
    if not payer:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payer not found")
    if payload.policy_id:
        pol = await _scoped_one(
            db.patient_insurance_policies, {"id": payload.policy_id}, ctx,
        )
        if not pol:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Policy not found")

    if payload.service_date_from > payload.service_date_to:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "service_date_from must be <= service_date_to",
        )

    now = _now()
    claim_id = str(uuid.uuid4())
    # Phase 5 — PCN: caller-supplied value, else auto-derive from the
    # new uuid so every claim leaves here with a valid CLM01 identifier.
    pcn = (payload.patient_control_number or f"CCMS-{claim_id[:8].upper()}").strip()

    billed_cents = sum(ln.billed_cents * ln.units for ln in payload.lines)
    claim_doc = stamp_for_write({
        "id": claim_id,
        "location_id": payload.location_id,
        "patient_id": payload.patient_id,
        "payer_id": payload.payer_id,
        "policy_id": payload.policy_id,
        "source_invoice_id": payload.source_invoice_id,
        "claim_type": payload.claim_type,
        "place_of_service": payload.place_of_service,
        "frequency_code": payload.frequency_code,
        "billing_provider_id": payload.billing_provider_id,
        "rendering_provider_id": payload.rendering_provider_id,
        "facility_id": payload.facility_id,
        "authorization_number": payload.authorization_number,
        "referral_number": payload.referral_number,
        # Phase 5 — 837P foundational identifiers
        "patient_control_number": pcn,
        "payer_claim_control_number": None,
        "accident_date": payload.accident_date,
        "onset_date": payload.onset_date,
        "initial_treatment_date": payload.initial_treatment_date,
        "status": "draft",
        "service_date_from": payload.service_date_from,
        "service_date_to": payload.service_date_to,
        "billed_cents": billed_cents,
        "paid_cents": 0,
        "submitted_at": None,
        "accepted_at": None,
        "last_denial_code": None,
        "notes": payload.notes,
        "validation_error_count": 0,
        "validation_warning_count": 0,
        "validation_last_run_at": None,
        "created_at": now,
        "updated_at": now,
        "created_by": user["id"],
        "updated_by": user["id"],
        "history": [_history_entry(user, "created",
                                   lines=len(payload.lines),
                                   billed_cents=billed_cents)],
    }, ctx, location_id=payload.location_id)

    diag_docs = [stamp_for_write({
        "id": str(uuid.uuid4()),
        "claim_id": claim_id,
        "sequence": d.sequence,
        "code": d.code,
        "created_at": now,
    }, ctx, location_id=None) for d in payload.diagnoses]

    line_docs: list[dict] = []
    mod_docs: list[dict] = []
    for ln in payload.lines:
        line_id = str(uuid.uuid4())
        line_docs.append(stamp_for_write({
            "id": line_id,
            "claim_id": claim_id,
            "sequence": ln.sequence,
            "invoice_line_id": ln.invoice_line_id,
            "service_date": ln.service_date,
            "code_type": ln.code_type,
            "code": ln.code,
            "units": ln.units,
            "billed_cents": ln.billed_cents,
            "diagnosis_pointers": ln.diagnosis_pointers,
            "created_at": now,
        }, ctx, location_id=payload.location_id))
        for i, mod in enumerate(ln.modifiers, start=1):
            mod_docs.append(stamp_for_write({
                "id": str(uuid.uuid4()),
                "claim_line_id": line_id,
                "sequence": i,
                "modifier_code": mod,
                "created_at": now,
            }, ctx, location_id=None))

    if diag_docs:
        await db.claim_diagnoses.insert_many(diag_docs)
    if line_docs:
        await db.claim_lines.insert_many(line_docs)
    if mod_docs:
        await db.claim_line_modifiers.insert_many(mod_docs)
    await db.claims.insert_one(claim_doc)

    await audit_success(
        user, "billing.claim.created", request,
        entity_type="claim", entity_id=claim_id,
        metadata={"patient_id": payload.patient_id,
                  "payer_id": payload.payer_id,
                  "billed_cents": billed_cents,
                  "lines": len(line_docs)},
    )
    await emit_claim_event(
        db, ctx,
        claim_id=claim_id,
        event_type="created",
        actor_id=user["id"],
        payload={"billed_cents": billed_cents,
                 "line_count": len(line_docs),
                 "diagnosis_count": len(diag_docs)},
        to_status="draft",
        location_id=payload.location_id,
    )
    return _public(claim_doc)


# ---------------------------------------------------------------------------
# Phase 9 — Claim skeleton from a documented clinical encounter
# ---------------------------------------------------------------------------
class ClaimFromEncounterInput(BaseModel):
    """Inputs required to materialise a draft claim skeleton from a
    completed clinical encounter.

    Clinical signals (patient, provider, DOS, diagnoses, procedures) are
    pulled from the encounter + readiness report — they're never taken
    from the caller. Payer + policy + billing metadata are the one-time
    human decisions that cannot be inferred from the chart."""

    model_config = ConfigDict(extra="forbid")
    encounter_id: str = Field(min_length=1)
    payer_id: str = Field(min_length=1)
    policy_id: str | None = None
    location_id: str | None = None
    billing_provider_id: str | None = None
    facility_id: str | None = None
    place_of_service: str = Field(default="11", max_length=2)   # 11 = office (CMS)
    frequency_code: str = Field(default="1", max_length=1)
    authorization_number: str | None = Field(default=None, max_length=60)
    referral_number: str | None = Field(default=None, max_length=60)
    notes: str | None = Field(default=None, max_length=2000)
    # Admin override: proceed even when the encounter is `blocked`.
    force: bool = False


PLACEHOLDER_CPT = "TBD"
# Kind → default CPT hint. Blank strings remain `TBD`; the operator must
# fill in the exact code before submission. These are deliberately
# non-authoritative — they're just soft suggestions surfaced in the notes.
KIND_TO_HINT = {
    "adjustment": "98940",             # CMT 1-2 regions
    "manipulation": "98940",
    "modality": "97014",                # e-stim (unattended)
    "exercise": "97110",                # therapeutic exercise
    "soft_tissue": "97140",             # manual therapy
    "examination": "99203",             # new-pt office visit (low-mod)
}


@router.post("/claims/from-encounter",
             response_model=ClaimPublic, status_code=201)
async def create_claim_from_encounter(
    payload: ClaimFromEncounterInput,
    request: Request,
    user: dict = Depends(require_permission("claim", "create")),
    ctx: TenantContext = Depends(require_tenant),
):
    """Materialise a `draft` claim skeleton from a documented encounter.

    Copies patient, rendering provider, DOS, and documented diagnoses +
    procedures from the encounter. CPT codes default to ``TBD`` (with
    soft hints in line notes) so the operator fills them in the claim
    editor. Billed amounts default to ``0`` (unpriced — fee schedule not
    applied in this phase).

    Returns 409 with the blocking checks if the encounter fails
    readiness, unless ``force=True`` is supplied by an admin."""
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tenant context required")
    db = tenant_db(ctx.tenant_id)

    # Load encounter first so we know the patient_id.
    enc_q = scoped_filter({"id": payload.encounter_id}, ctx, location_scoped=False)
    if enc_q.get("__deny__"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Encounter not found")
    encounter = await db.clinical_encounters.find_one(enc_q, {"_id": 0})
    if not encounter:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Encounter not found")
    patient_id = encounter["patient_id"]

    # Validate payer + policy within tenant.
    payer = await _scoped_one(db.billing_payers, {"id": payload.payer_id}, ctx)
    if not payer:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payer not found")
    if payload.policy_id:
        pol = await _scoped_one(
            db.patient_insurance_policies, {"id": payload.policy_id}, ctx,
        )
        if not pol:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Policy not found")
        if pol["patient_id"] != patient_id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Policy does not belong to this encounter's patient",
            )

    # Evaluate readiness. Blocked encounters cannot be claimed unless
    # `force=True` and the caller is an admin.
    readiness = await evaluate_billing_readiness(
        db, ctx, patient_id, payload.encounter_id,
    )
    if readiness.overall_status == "blocked" and not payload.force:
        blocking = [
            {"key": c.key, "label": c.label, "detail": c.detail}
            for c in readiness.checks
            if c.severity == "fail" and not c.passed
        ]
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"message": "Encounter is not billing-ready", "blocking": blocking},
        )
    if payload.force and user.get("role") != "admin":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only admins can force a blocked encounter into a claim",
        )

    dos = encounter.get("date_of_service")
    if not dos:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Encounter has no date_of_service; cannot build claim",
        )

    # ------------------------------------------------------------ diagnoses
    # Order: primary first (if flagged), then by label/code.
    dx_cursor = db.clinical_diagnoses.find(
        {
            "tenant_id": ctx.tenant_id,
            "patient_id": patient_id,
            "status": "active",
            "episode_id": encounter.get("episode_id"),
        },
        {"_id": 0, "id": 1, "icd10_code": 1, "label": 1, "is_primary": 1},
    )
    dx_rows = [d async for d in dx_cursor]
    if not dx_rows:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "No active ICD-10 diagnoses on the episode; cannot build claim",
        )
    # Deduplicate by upper-cased ICD-10 code, preserving primary first.
    seen_codes: set[str] = set()
    ordered: list[dict] = []
    for row in sorted(dx_rows, key=lambda r: (not r.get("is_primary"), r.get("icd10_code") or "")):
        code = (row.get("icd10_code") or "").strip().upper()
        if not code or code in seen_codes:
            continue
        seen_codes.add(code)
        ordered.append({"id": row["id"], "code": code, "label": row.get("label")})
        if len(ordered) >= 12:   # CMS cap
            break
    diagnoses_payload = [
        {"sequence": i + 1, "code": dx["code"]} for i, dx in enumerate(ordered)
    ]
    first_dx_pointer = [1]

    # ------------------------------------------------------------ lines
    # Convert readiness `procedures` into one line per procedure. CPT
    # code defaults to `TBD` + per-kind hint. Units comes from `count`.
    lines_payload: list[dict] = []
    hint_notes: list[str] = []
    for i, p in enumerate(readiness.procedures):
        hint = KIND_TO_HINT.get(p.kind)
        code = hint or PLACEHOLDER_CPT
        lines_payload.append({
            "sequence": i + 1,
            "invoice_line_id": None,
            "service_date": dos,
            "code_type": "cpt",
            "code": code,
            "units": max(int(p.count or 1), 1),
            "billed_cents": 0,
            "diagnosis_pointers": first_dx_pointer if diagnoses_payload else [],
            "modifiers": [],
        })
        descriptor = p.description or p.kind
        region = f" [{p.body_region}]" if p.body_region else ""
        hint_notes.append(
            f"#{i + 1} {descriptor}{region} → CPT {code}"
            + (" (auto-hint, verify)" if hint else " (placeholder, set CPT)"),
        )

    if not lines_payload:
        # Fall back to a single placeholder line so the claim is editable.
        lines_payload.append({
            "sequence": 1,
            "invoice_line_id": None,
            "service_date": dos,
            "code_type": "cpt",
            "code": PLACEHOLDER_CPT,
            "units": 1,
            "billed_cents": 0,
            "diagnosis_pointers": first_dx_pointer,
            "modifiers": [],
        })
        hint_notes.append("#1 No documented procedures — add lines manually.")

    # ------------------------------------------------------------ persist
    now = _now()
    claim_id = str(uuid.uuid4())
    billed_cents = sum(ln["billed_cents"] * ln["units"] for ln in lines_payload)

    synthesized_notes = "\n".join([
        f"Auto-generated from encounter {payload.encounter_id} "
        f"(readiness: {readiness.overall_status}"
        + (", forced" if payload.force else "") + ")",
        f"Visit: {readiness.visit_type_label or readiness.visit_type or '—'}",
        "Line hints:",
        *hint_notes,
    ] + ([payload.notes] if payload.notes else []))

    claim_doc = stamp_for_write({
        "id": claim_id,
        "location_id": payload.location_id or encounter.get("location_id"),
        "patient_id": patient_id,
        "payer_id": payload.payer_id,
        "policy_id": payload.policy_id,
        "source_invoice_id": None,
        "source_encounter_id": payload.encounter_id,
        "claim_type": "professional",
        "place_of_service": payload.place_of_service,
        "frequency_code": payload.frequency_code,
        "billing_provider_id": payload.billing_provider_id,
        "rendering_provider_id": encounter.get("provider_id"),
        "facility_id": payload.facility_id,
        "authorization_number": payload.authorization_number,
        "referral_number": payload.referral_number,
        "status": "draft",
        "service_date_from": dos,
        "service_date_to": dos,
        "billed_cents": billed_cents,
        "paid_cents": 0,
        "submitted_at": None,
        "accepted_at": None,
        "last_denial_code": None,
        "notes": synthesized_notes,
        "validation_error_count": 0,
        "validation_warning_count": 0,
        "validation_last_run_at": None,
        "created_at": now,
        "updated_at": now,
        "created_by": user["id"],
        "updated_by": user["id"],
        "history": [_history_entry(
            user, "created_from_encounter",
            encounter_id=payload.encounter_id,
            readiness_status=readiness.overall_status,
            forced=payload.force,
            lines=len(lines_payload),
            diagnoses=len(diagnoses_payload),
        )],
    }, ctx, location_id=payload.location_id or encounter.get("location_id"))

    diag_docs = [stamp_for_write({
        "id": str(uuid.uuid4()),
        "claim_id": claim_id,
        "sequence": d["sequence"],
        "code": d["code"],
        "created_at": now,
    }, ctx, location_id=None) for d in diagnoses_payload]

    line_docs: list[dict] = []
    for ln in lines_payload:
        line_docs.append(stamp_for_write({
            "id": str(uuid.uuid4()),
            "claim_id": claim_id,
            "sequence": ln["sequence"],
            "invoice_line_id": None,
            "service_date": ln["service_date"],
            "code_type": ln["code_type"],
            "code": ln["code"],
            "units": ln["units"],
            "billed_cents": ln["billed_cents"],
            "diagnosis_pointers": ln["diagnosis_pointers"],
            "created_at": now,
        }, ctx, location_id=payload.location_id or encounter.get("location_id")))

    if diag_docs:
        await db.claim_diagnoses.insert_many(diag_docs)
    if line_docs:
        await db.claim_lines.insert_many(line_docs)
    await db.claims.insert_one(claim_doc)

    await audit_success(
        user, "billing.claim.created_from_encounter", request,
        entity_type="claim", entity_id=claim_id,
        metadata={
            "patient_id": patient_id,
            "encounter_id": payload.encounter_id,
            "payer_id": payload.payer_id,
            "readiness_status": readiness.overall_status,
            "forced": payload.force,
            "lines": len(line_docs),
            "diagnoses": len(diag_docs),
            "billed_cents": billed_cents,
        },
    )
    return _public(claim_doc)


@router.get("/claims", response_model=list[ClaimPublic])
async def list_claims(
    request: Request,
    patient_id: str | None = Query(default=None),
    status_filter: ClaimStatus | None = Query(default=None, alias="status"),
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    q = scoped_filter({}, ctx, location_scoped=False)
    if q.get("__deny__"):
        return []
    if patient_id:
        q["patient_id"] = patient_id
    if status_filter:
        q["status"] = status_filter
    cursor = db.claims.find(q, {"_id": 0}).sort([("created_at", -1)])
    rows = [_public(d) async for d in cursor]
    await audit_success(
        user, "billing.claim.list_viewed", request,
        metadata={"count": len(rows), "patient_id": patient_id,
                  "status_filter": status_filter},
    )
    return rows


@router.post("/claims/{claim_id}/submit", response_model=ClaimPublic)
async def submit_claim(
    claim_id: str,
    request: Request,
    user: dict = Depends(require_permission("claim", "submit")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    claim = await _scoped_one(db.claims, {"id": claim_id}, ctx)
    if not claim:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Claim not found")

    # submit semantically means: ready → submitted (or draft → submitted via
    # ready). We allow either by chaining.
    current = claim["status"]
    if current == "draft":
        current = transitions.http_advance("claim", current, "ready")
    new_status = transitions.http_advance("claim", current, "submitted")

    now = _now()
    await db.claims.update_one(
        {"id": claim_id, "tenant_id": ctx.tenant_id},
        {"$set": {"status": new_status, "submitted_at": now,
                  "updated_at": now, "updated_by": user["id"]},
         "$push": {"history": _history_entry(
             user, "submitted", from_status=claim["status"],
         )}},
    )
    fresh = await db.claims.find_one(
        {"id": claim_id, "tenant_id": ctx.tenant_id}, {"_id": 0},
    )
    await audit_success(
        user, "billing.claim.submitted", request,
        entity_type="claim", entity_id=claim_id,
        metadata={"from": claim["status"], "to": new_status},
    )
    return _public(fresh)


@router.post("/claims/{claim_id}/status", response_model=ClaimPublic)
async def transition_claim_status(
    claim_id: str,
    request: Request,
    desired: ClaimStatus = Query(...),
    user: dict = Depends(require_permission("claim", "correct_resubmit")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    claim = await _scoped_one(db.claims, {"id": claim_id}, ctx)
    if not claim:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Claim not found")

    new_status = transitions.http_advance("claim", claim["status"], desired)
    if new_status == claim["status"]:
        return _public(claim)

    now = _now()
    await db.claims.update_one(
        {"id": claim_id, "tenant_id": ctx.tenant_id},
        {"$set": {"status": new_status, "updated_at": now,
                  "updated_by": user["id"]},
         "$push": {"history": _history_entry(
             user, "status_changed",
             from_status=claim["status"], to_status=new_status,
         )}},
    )
    fresh = await db.claims.find_one(
        {"id": claim_id, "tenant_id": ctx.tenant_id}, {"_id": 0},
    )
    await audit_success(
        user, "billing.claim.status_changed", request,
        entity_type="claim", entity_id=claim_id,
        metadata={"from": claim["status"], "to": new_status},
    )
    return _public(fresh)


# ---------------------------------------------------------------------------
# REMITTANCES  —  /api/billing/remittances   (read-only placeholder)
# ---------------------------------------------------------------------------
@router.get("/remittances", response_model=list[RemittancePublic])
async def list_remittances(
    request: Request,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    q = scoped_filter({}, ctx, location_scoped=False)
    if q.get("__deny__"):
        return []
    cursor = db.remittances.find(q, {"_id": 0}).sort([("received_at", -1)])
    rows = [_public(d) async for d in cursor]
    await audit_success(
        user, "billing.remittance.list_viewed", request,
        metadata={"count": len(rows)},
    )
    return rows


# ---------------------------------------------------------------------------
# DENIAL WORK ITEMS  —  /api/billing/denial-work-items (read-only placeholder)
# ---------------------------------------------------------------------------
@router.get("/denial-work-items",
            response_model=list[DenialWorkItemPublic])
async def list_denial_work_items(
    request: Request,
    status_in: str | None = Query(default=None, description="comma-separated statuses"),
    category: str | None = Query(default=None, description="filter by denial_category"),
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    q = scoped_filter({}, ctx, location_scoped=False)
    if q.get("__deny__"):
        return []
    if status_in:
        statuses = [s.strip() for s in status_in.split(",") if s.strip()]
        if statuses:
            q["status"] = {"$in": statuses}
    if category:
        if category not in DENIAL_CATEGORIES:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Unknown category '{category}'. Allowed: {DENIAL_CATEGORIES}",
            )
        q["denial_category"] = category
    cursor = db.denial_work_items.find(q, {"_id": 0}).sort([("opened_at", -1)])
    rows = [_public(d) async for d in cursor]
    await audit_success(
        user, "billing.denial.list_viewed", request,
        metadata={"count": len(rows)},
    )
    return rows



# ---------------------------------------------------------------------------
# PATIENT LEDGER  —  /api/billing/patients/{patient_id}/ledger
# ---------------------------------------------------------------------------
@router.get("/patients/{patient_id}/ledger")
async def patient_ledger(
    patient_id: str,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(require_tenant),
):
    """Unified chronological ledger for a single patient.

    The response is intentionally *denormalized* — each row carries the
    fields the UI needs to render without a join. The running balance is
    precomputed server-side so two clients looking at the same patient
    always agree on the cents.
    """
    db = tenant_db(ctx.tenant_id)
    patient = await _scoped_one(db.patients, {"id": patient_id}, ctx)
    if not patient:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")

    payload = await build_patient_ledger(
        db, tenant_id=ctx.tenant_id, patient_id=patient_id,
    )
    await audit_success(
        user, "billing.ledger.viewed", request,
        entity_type="patient", entity_id=patient_id,
        metadata={"rows": len(payload["rows"]),
                  "balance_cents": payload["running_balance_cents"]},
    )
    return payload


# ---------------------------------------------------------------------------
# POST-HOC PAYMENT ALLOCATION  —  /api/billing/payments/{payment_id}/allocations
# ---------------------------------------------------------------------------
@router.post("/payments/{payment_id}/allocations",
             response_model=PaymentPublic)
async def allocate_payment(
    payment_id: str,
    request: Request,
    user: dict = Depends(require_permission("payment", "collect")),
    ctx: TenantContext = Depends(require_tenant),
):
    """Allocate remaining payment balance onto invoices.

    Request body: `[{invoice_id, invoice_line_id?, amount_cents}, ...]`.
    Sum of new allocations must be ≤ payment.amount_cents − already
    allocated. Each invoice must exist and be in a non-terminal state.
    """
    db = tenant_db(ctx.tenant_id)
    pmt = await _scoped_one(db.payments, {"id": payment_id}, ctx)
    if not pmt:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment not found")
    if pmt["status"] in ("void", "failed", "refunded"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"cannot allocate a {pmt['status']} payment",
        )

    # Accept the body as a list of allocation inputs reusing the existing
    # PaymentAllocationInput shape.
    body = await request.json()
    if not isinstance(body, list) or not body:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "body must be a non-empty list of allocations")

    from services.billing.models import PaymentAllocationInput as _Alloc
    allocs = [_Alloc(**row) for row in body]
    added_sum = sum(a.amount_cents for a in allocs)
    remaining = pmt["amount_cents"] - pmt.get("allocated_cents", 0)
    if added_sum > remaining:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"allocations exceed remaining ({added_sum} > {remaining})",
        )

    for a in allocs:
        inv = await _scoped_one(db.invoices, {"id": a.invoice_id}, ctx)
        if not inv:
            raise HTTPException(status.HTTP_404_NOT_FOUND,
                                f"invoice {a.invoice_id} not found")
        if inv["status"] in ("void", "refunded"):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"cannot allocate to {inv['status']} invoice",
            )

    now = _now()
    docs = [stamp_for_write({
        "id": str(uuid.uuid4()),
        "payment_id": payment_id,
        "invoice_id": a.invoice_id,
        "invoice_line_id": a.invoice_line_id,
        "amount_cents": a.amount_cents,
        "created_at": now,
    }, ctx, location_id=pmt.get("location_id")) for a in allocs]
    await db.payment_allocations.insert_many(docs)

    await db.payments.update_one(
        {"id": payment_id, "tenant_id": ctx.tenant_id},
        {"$set": {"allocated_cents": pmt.get("allocated_cents", 0) + added_sum,
                  "updated_at": now, "updated_by": user["id"]},
         "$push": {"history": _history_entry(
             user, "allocated", added_cents=added_sum, count=len(docs),
         )}},
    )

    touched = {a.invoice_id for a in allocs}
    for inv_id in touched:
        await _recompute_invoice_balance(db, inv_id, ctx.tenant_id, user["id"])

    fresh = await db.payments.find_one(
        {"id": payment_id, "tenant_id": ctx.tenant_id}, {"_id": 0},
    )
    await audit_success(
        user, "billing.payment.allocated", request,
        entity_type="payment", entity_id=payment_id,
        metadata={"added_cents": added_sum,
                  "allocations": len(docs),
                  "invoices": list(touched)},
    )
    return _public(fresh)


# ---------------------------------------------------------------------------
# VOID INVOICE  —  /api/billing/invoices/{id}/void
# ---------------------------------------------------------------------------
@router.post("/invoices/{invoice_id}/void", response_model=InvoicePublic)
async def void_invoice(
    invoice_id: str,
    request: Request,
    reason: str = Query(..., min_length=5, max_length=500),
    user: dict = Depends(require_permission("billing", "void")),
    ctx: TenantContext = Depends(require_tenant),
):
    """Void an invoice — terminal state. Requires `billing.void` (MFA+APR
    for billing specialists; MFA-only for super_admin in demo)."""
    db = tenant_db(ctx.tenant_id)
    inv = await _scoped_one(db.invoices, {"id": invoice_id}, ctx)
    if not inv:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    new_status = transitions.http_advance("invoice", inv["status"], "void")
    now = _now()
    await db.invoices.update_one(
        {"id": invoice_id, "tenant_id": ctx.tenant_id},
        {"$set": {"status": new_status, "balance_cents": 0,
                  "updated_at": now, "updated_by": user["id"]},
         "$push": {"history": _history_entry(
             user, "voided", from_status=inv["status"], reason=reason,
         )}},
    )
    fresh = await db.invoices.find_one(
        {"id": invoice_id, "tenant_id": ctx.tenant_id}, {"_id": 0},
    )
    await audit_success(
        user, "billing.invoice.voided", request,
        entity_type="invoice", entity_id=invoice_id,
        metadata={"from": inv["status"], "reason": reason},
    )
    return _public(fresh)


# ===========================================================================
# PHASE 2 — Insurance policies, fee schedules, encounter charge capture
# ===========================================================================


# ---------------------------------------------------------------------------
# INSURANCE POLICIES — update (create already lives above)
# ---------------------------------------------------------------------------
@router.patch("/insurance-policies/{policy_id}",
            response_model=PatientInsurancePolicyPublic)
async def update_insurance_policy(
    policy_id: str,
    request: Request,
    user: dict = Depends(require_permission("insurance", "update")),
    ctx: TenantContext = Depends(require_tenant),
):
    """Partial-update an active patient insurance policy. Accepts the
    same fields as create, all optional."""
    body = await request.json()
    allowed_keys = {
        "payer_id", "rank", "subscriber_name", "relationship_to_subscriber",
        "member_id", "group_number", "effective_date", "termination_date",
        "status", "notes",
    }
    updates = {k: v for k, v in (body or {}).items() if k in allowed_keys}
    if not updates:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No valid fields")

    db = tenant_db(ctx.tenant_id)
    current = await _scoped_one(
        db.patient_insurance_policies, {"id": policy_id}, ctx,
    )
    if not current:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Policy not found")

    if "payer_id" in updates:
        payer = await _scoped_one(
            db.billing_payers, {"id": updates["payer_id"]}, ctx,
        )
        if not payer:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Payer not found")

    now = _now()
    updates["updated_at"] = now
    updates["updated_by"] = user["id"]
    await db.patient_insurance_policies.update_one(
        {"id": policy_id, "tenant_id": ctx.tenant_id},
        {"$set": updates,
         "$push": {"history": _history_entry(
             user, "updated", fields=sorted(list(updates.keys())),
         )}},
    )
    fresh = await db.patient_insurance_policies.find_one(
        {"id": policy_id, "tenant_id": ctx.tenant_id}, {"_id": 0},
    )
    await audit_success(
        user, "billing.insurance_policy.updated", request,
        entity_type="patient_insurance_policy", entity_id=policy_id,
        metadata={"fields": sorted(list(updates.keys()))},
    )
    return _public(fresh)


@router.delete("/insurance-policies/{policy_id}")
async def deactivate_insurance_policy(
    policy_id: str,
    request: Request,
    user: dict = Depends(require_permission("insurance", "update")),
    ctx: TenantContext = Depends(require_tenant),
):
    """Soft-deactivate a policy (status → 'inactive')."""
    db = tenant_db(ctx.tenant_id)
    current = await _scoped_one(
        db.patient_insurance_policies, {"id": policy_id}, ctx,
    )
    if not current:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Policy not found")
    await db.patient_insurance_policies.update_one(
        {"id": policy_id, "tenant_id": ctx.tenant_id},
        {"$set": {"status": "inactive", "updated_at": _now(),
                  "updated_by": user["id"]},
         "$push": {"history": _history_entry(user, "deactivated")}},
    )
    await audit_success(
        user, "billing.insurance_policy.deactivated", request,
        entity_type="patient_insurance_policy", entity_id=policy_id,
    )
    return {"ok": True}


# ---------------------------------------------------------------------------
# FEE SCHEDULES  —  /api/billing/fee-schedules
# ---------------------------------------------------------------------------
@router.get("/fee-schedules")
async def list_fee_schedules(
    request: Request,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    q = scoped_filter({}, ctx, location_scoped=False)
    if q.get("__deny__"):
        return []
    schedules = [d async for d in db.fee_schedules.find(q, {"_id": 0})
                 .sort([("kind", 1), ("name", 1)])]
    # Attach line counts so the UI can render "X codes" at a glance.
    for s in schedules:
        s["line_count"] = await db.fee_schedule_lines.count_documents(
            {"tenant_id": ctx.tenant_id, "fee_schedule_id": s["id"]},
        )
    await audit_success(
        user, "billing.fee_schedule.list_viewed", request,
        metadata={"count": len(schedules)},
    )
    return schedules


@router.post("/fee-schedules", status_code=201)
async def create_fee_schedule(
    request: Request,
    user: dict = Depends(require_permission("clinic_settings", "update")),
    ctx: TenantContext = Depends(require_tenant),
):
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tenant required")
    db = tenant_db(ctx.tenant_id)
    body = await request.json()
    name = (body.get("name") or "").strip()
    if len(name) < 2 or len(name) > 120:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "name must be 2..120 chars")
    kind = body.get("kind")
    if kind not in ("self_pay", "payer"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "kind must be 'self_pay' or 'payer'")
    payer_id = body.get("payer_id")
    if kind == "payer":
        if not payer_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "payer schedule requires payer_id")
        payer = await _scoped_one(db.billing_payers, {"id": payer_id}, ctx)
        if not payer:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Payer not found")
    else:
        payer_id = None

    # Exactly one active self_pay schedule per tenant at a time.
    if kind == "self_pay":
        existing = await db.fee_schedules.find_one(
            {"tenant_id": ctx.tenant_id, "kind": "self_pay", "active": True},
            {"_id": 0, "id": 1},
        )
        if existing:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Tenant already has an active self_pay schedule — "
                "deactivate it first",
            )

    now = _now()
    sid = str(uuid.uuid4())
    doc = stamp_for_write({
        "id": sid,
        "name": name,
        "kind": kind,
        "payer_id": payer_id,
        "active": True,
        "effective_date": body.get("effective_date"),
        "notes": body.get("notes"),
        "created_at": now,
        "updated_at": now,
        "created_by": user["id"],
        "updated_by": user["id"],
        "history": [_history_entry(user, "created")],
    }, ctx, location_id=None)
    await db.fee_schedules.insert_one(doc)
    await audit_success(
        user, "billing.fee_schedule.created", request,
        entity_type="fee_schedule", entity_id=sid,
        metadata={"name": name, "kind": kind, "payer_id": payer_id},
    )
    return _public(doc) | {"line_count": 0}


@router.patch("/fee-schedules/{schedule_id}/lines")
async def upsert_fee_schedule_lines(
    schedule_id: str,
    request: Request,
    user: dict = Depends(require_permission("clinic_settings", "update")),
    ctx: TenantContext = Depends(require_tenant),
):
    """Upsert a batch of `{code_type, code, allowed_cents}` rows."""
    db = tenant_db(ctx.tenant_id)
    schedule = await _scoped_one(db.fee_schedules, {"id": schedule_id}, ctx)
    if not schedule:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Fee schedule not found")

    body = await request.json()
    rows = body if isinstance(body, list) else body.get("lines")
    if not isinstance(rows, list) or not rows:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "body must be a list of lines")

    now = _now()
    written = 0
    for r in rows:
        code = (r.get("code") or "").strip()
        allowed = int(r.get("allowed_cents", 0))
        if not code or allowed < 0:
            continue
        code_type = r.get("code_type", "cpt")
        await db.fee_schedule_lines.update_one(
            {"tenant_id": ctx.tenant_id, "fee_schedule_id": schedule_id,
             "code_type": code_type, "code": code},
            {"$setOnInsert": {
                "id": str(uuid.uuid4()),
                "tenant_id": ctx.tenant_id,
                "fee_schedule_id": schedule_id,
                "code_type": code_type, "code": code,
                "created_at": now,
             },
             "$set": {
                "allowed_cents": allowed,
                "updated_at": now,
                "updated_by": user["id"],
             }},
            upsert=True,
        )
        written += 1

    await db.fee_schedules.update_one(
        {"id": schedule_id, "tenant_id": ctx.tenant_id},
        {"$set": {"updated_at": now, "updated_by": user["id"]},
         "$push": {"history": _history_entry(
             user, "lines_upserted", count=written,
         )}},
    )
    await audit_success(
        user, "billing.fee_schedule.lines_upserted", request,
        entity_type="fee_schedule", entity_id=schedule_id,
        metadata={"lines": written},
    )
    count = await db.fee_schedule_lines.count_documents(
        {"tenant_id": ctx.tenant_id, "fee_schedule_id": schedule_id},
    )
    return {"ok": True, "upserted": written, "line_count": count}


@router.get("/fee-schedules/{schedule_id}/lines")
async def list_fee_schedule_lines(
    schedule_id: str,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    schedule = await _scoped_one(db.fee_schedules, {"id": schedule_id}, ctx)
    if not schedule:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Fee schedule not found")
    rows = [d async for d in db.fee_schedule_lines.find(
        {"tenant_id": ctx.tenant_id, "fee_schedule_id": schedule_id},
        {"_id": 0},
    ).sort([("code", 1)])]
    return rows


# ---------------------------------------------------------------------------
# ENCOUNTER CHARGE CAPTURE  —  /api/billing/encounters/{record_id}/...
# ---------------------------------------------------------------------------
from services.billing.charge_capture import build_charge_candidates  # noqa: E402


async def _load_record_in_tenant(db, ctx: TenantContext, record_id: str):
    """Load a medical record with STRICT tenant match.

    We intentionally do NOT honour `tenant_scope_all` here — charge
    capture must always operate on the caller's active tenant so that
    a platform admin who's scoped to Sunrise doesn't accidentally
    generate Default's invoices.
    """
    if not ctx.tenant_id:
        return None
    return await db.medical_records.find_one(
        {"id": record_id, "tenant_id": ctx.tenant_id}, {"_id": 0},
    )


@router.get("/encounters/{record_id}/charge-candidates")
async def preview_charge_candidates(
    record_id: str,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(require_tenant),
):
    """Dry-run: what would the invoice look like if we captured this
    encounter right now? Honours coding + responsibility + primary
    policy + fee schedule precedence. Does NOT mutate state."""
    db = tenant_db(ctx.tenant_id)
    record = await _load_record_in_tenant(db, ctx, record_id)
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Record not found")

    preview = await build_charge_candidates(
        db, tenant_id=ctx.tenant_id, record=record,
    )
    await audit_success(
        user, "billing.charge_capture.previewed", request,
        entity_type="medical_record", entity_id=record_id,
        metadata={"lines": len(preview["lines"]),
                  "total_cents": preview["total_cents"],
                  "warnings": len(preview["warnings"])},
    )
    return preview


@router.post("/encounters/{record_id}/capture",
             response_model=InvoicePublic, status_code=201)
async def capture_encounter(
    record_id: str,
    request: Request,
    user: dict = Depends(require_permission("charge", "create")),
    ctx: TenantContext = Depends(require_tenant),
):
    """Commit charge capture: turn a signed record's procedures into a
    `draft` invoice. Record transitions to `charge_status=captured`.

    Validations:
      * record must be signed (`signed_at` present)
      * not already captured
      * must have at least one procedure
      * if responsibility = insurance/mixed → active primary policy required
    """
    db = tenant_db(ctx.tenant_id)
    record = await _load_record_in_tenant(db, ctx, record_id)
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Record not found")
    if not record.get("signed_at"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Only signed records can have charges captured",
        )
    if record.get("charge_status") == "captured":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Record already captured",
        )

    preview = await build_charge_candidates(
        db, tenant_id=ctx.tenant_id, record=record,
    )
    if not preview["lines"]:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Record has no procedures to capture",
        )
    if not preview["can_capture"]:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "; ".join(preview["warnings"]) or "Capture blocked",
        )

    now = _now()
    invoice_id = str(uuid.uuid4())
    line_docs: list[dict] = []
    for i, ln in enumerate(preview["lines"], start=1):
        line_docs.append(stamp_for_write({
            "id": str(uuid.uuid4()),
            "invoice_id": invoice_id,
            "sequence": i,
            "code_type": ln["code_type"],
            "code": ln["code"],
            "description": ln["description"],
            "service_date": ln["service_date"],
            "quantity": ln["quantity"],
            "unit_price_cents": ln["unit_price_cents"],
            "total_cents": ln["total_cents"],
            "modifiers": ln["modifiers"],
            "provider_id": record.get("recorded_by"),
            "source_encounter_id": record_id,
            "source_fee_schedule_id": ln.get("fee_schedule_id"),
            "price_source": ln["price_source"],
            "created_at": now,
        }, ctx, location_id=record.get("location_id")))

    total_cents = preview["total_cents"]
    invoice_doc = stamp_for_write({
        "id": invoice_id,
        "location_id": record.get("location_id"),
        "patient_id": record["patient_id"],
        "appointment_id": record.get("appointment_id"),
        "source_encounter_id": record_id,
        "responsibility": preview["responsibility"],
        "payer_id": preview.get("payer_id"),
        "policy_id": preview.get("policy_id"),
        "status": "draft",
        "issued_at": None,
        "due_date": None,
        "currency": DEFAULT_CURRENCY,
        "subtotal_cents": total_cents,
        "tax_cents": 0,
        "adjustment_cents": 0,
        "total_cents": total_cents,
        "balance_cents": total_cents,
        "notes": None,
        "created_at": now,
        "updated_at": now,
        "created_by": user["id"],
        "updated_by": user["id"],
        "history": [_history_entry(
            user, "captured_from_encounter",
            record_id=record_id, lines=len(line_docs),
            total_cents=total_cents,
        )],
    }, ctx, location_id=record.get("location_id"))

    if line_docs:
        await db.invoice_lines.insert_many(line_docs)
    await db.invoices.insert_one(invoice_doc)
    await db.medical_records.update_one(
        {"id": record_id, "tenant_id": ctx.tenant_id},
        {"$set": {"charge_status": "captured",
                  "charge_captured_invoice_id": invoice_id}},
    )

    await audit_success(
        user, "billing.charge_capture.committed", request,
        entity_type="medical_record", entity_id=record_id,
        metadata={"invoice_id": invoice_id,
                  "lines": len(line_docs),
                  "total_cents": total_cents,
                  "responsibility": preview["responsibility"]},
    )
    return _public(invoice_doc)



# ===========================================================================
# PHASE 3 — Claim draft builder + scrubber
# ===========================================================================
from services.billing.scrubber import (  # noqa: E402
    DEFAULT_RULES, ScrubberContext, run_rules,
)


async def _load_claim_context(db, ctx: TenantContext, claim: dict) -> ScrubberContext:
    """Build the `ScrubberContext` for one claim — the rules engine needs
    header + diagnoses + lines + denormalised patient/payer/policy data."""
    claim_id = claim["id"]
    diagnoses = [d async for d in db.claim_diagnoses.find(
        {"tenant_id": ctx.tenant_id, "claim_id": claim_id}, {"_id": 0},
    ).sort([("sequence", 1)])]
    lines = [ln async for ln in db.claim_lines.find(
        {"tenant_id": ctx.tenant_id, "claim_id": claim_id}, {"_id": 0},
    ).sort([("sequence", 1)])]
    mods_by_line: dict[str, list[dict]] = {}
    if lines:
        async for m in db.claim_line_modifiers.find(
            {"tenant_id": ctx.tenant_id,
             "claim_line_id": {"$in": [ln["id"] for ln in lines]}},
            {"_id": 0},
        ).sort([("sequence", 1)]):
            mods_by_line.setdefault(m["claim_line_id"], []).append(m)

    patient = await db.patients.find_one(
        {"tenant_id": ctx.tenant_id, "id": claim.get("patient_id")},
        # Phase 4 — project DOB + gender for the validator. Keep this
        # projection tight so no other PHI leaves the DB.
        {"_id": 0, "id": 1, "first_name": 1, "last_name": 1,
         "dob": 1, "date_of_birth": 1, "gender": 1, "sex": 1,
         "demographics": 1},
    )
    if patient:
        # Normalise DOB + gender so the scrubber can read them without
        # caring which legacy/grouped field they live on.
        if not patient.get("date_of_birth"):
            demo = patient.get("demographics") or {}
            patient["date_of_birth"] = (
                patient.get("dob") or demo.get("date_of_birth") or ""
            )
        if not patient.get("gender"):
            demo = patient.get("demographics") or {}
            patient["gender"] = demo.get("gender") or patient.get("sex") or ""
    payer = await db.billing_payers.find_one(
        {"tenant_id": ctx.tenant_id, "id": claim.get("payer_id")},
        {"_id": 0},
    )
    policy = None
    if claim.get("policy_id"):
        policy = await db.patient_insurance_policies.find_one(
            {"tenant_id": ctx.tenant_id, "id": claim["policy_id"]},
            {"_id": 0},
        )

    return ScrubberContext(
        claim=claim, diagnoses=diagnoses, lines=lines,
        line_modifiers_by_line=mods_by_line,
        patient=patient, payer=payer, policy=policy,
    )


@router.post("/claims/from-invoice/{invoice_id}",
             response_model=ClaimPublic, status_code=201)
async def create_claim_from_invoice(
    invoice_id: str,
    request: Request,
    user: dict = Depends(require_permission("claim", "create")),
    ctx: TenantContext = Depends(require_tenant),
):
    """Derive a draft claim from a captured (insurance-responsibility)
    invoice. Claim header inherits the invoice's payer + policy +
    patient; lines mirror invoice lines with sensible defaults
    (diagnosis pointer → [1], modifiers copied through).
    """
    db = tenant_db(ctx.tenant_id)
    inv = await _scoped_one(db.invoices, {"id": invoice_id}, ctx)
    if not inv:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    if inv.get("responsibility") not in ("insurance", "mixed"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Only invoices with insurance/mixed responsibility can become claims",
        )
    if not inv.get("payer_id"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Invoice has no payer")

    inv_lines = [ln async for ln in db.invoice_lines.find(
        {"tenant_id": ctx.tenant_id, "invoice_id": invoice_id}, {"_id": 0},
    ).sort([("sequence", 1)])]
    if not inv_lines:
        raise HTTPException(status.HTTP_409_CONFLICT, "Invoice has no lines")

    source_record_id = inv.get("source_encounter_id")
    source_record = None
    if source_record_id:
        source_record = await db.medical_records.find_one(
            {"tenant_id": ctx.tenant_id, "id": source_record_id},
            {"_id": 0, "diagnoses": 1, "recorded_by": 1, "location_id": 1},
        )

    now = _now()
    claim_id = str(uuid.uuid4())

    diag_docs: list[dict] = []
    if source_record and source_record.get("diagnoses"):
        for d in source_record["diagnoses"]:
            diag_docs.append(stamp_for_write({
                "id": str(uuid.uuid4()),
                "claim_id": claim_id,
                "sequence": d.get("sequence", len(diag_docs) + 1),
                "code": d.get("code"),
                "created_at": now,
            }, ctx, location_id=None))
    if not diag_docs:
        diag_docs.append(stamp_for_write({
            "id": str(uuid.uuid4()), "claim_id": claim_id,
            "sequence": 1, "code": "",
            "created_at": now,
        }, ctx, location_id=None))

    first_seq = diag_docs[0]["sequence"]

    line_docs: list[dict] = []
    mod_docs: list[dict] = []
    for ln in inv_lines:
        cl_id = str(uuid.uuid4())
        line_docs.append(stamp_for_write({
            "id": cl_id,
            "claim_id": claim_id,
            "sequence": ln["sequence"],
            "invoice_line_id": ln["id"],
            "service_date": ln.get("service_date") or inv.get("issued_at") or now[:10],
            "code_type": ln.get("code_type", "cpt"),
            "code": ln["code"],
            "units": int(ln.get("quantity", 1)),
            "billed_cents": int(ln.get("unit_price_cents", 0)),
            "diagnosis_pointers": [first_seq],
            "created_at": now,
        }, ctx, location_id=inv.get("location_id")))
        for i, mc in enumerate(ln.get("modifiers") or [], start=1):
            mod_docs.append(stamp_for_write({
                "id": str(uuid.uuid4()),
                "claim_line_id": cl_id,
                "sequence": i,
                "modifier_code": mc,
                "created_at": now,
            }, ctx, location_id=None))

    billed_total = sum(ln["billed_cents"] * ln["units"] for ln in line_docs)

    claim_doc = stamp_for_write({
        "id": claim_id,
        "location_id": inv.get("location_id"),
        "patient_id": inv["patient_id"],
        "payer_id": inv["payer_id"],
        "policy_id": inv.get("policy_id"),
        "source_invoice_id": invoice_id,
        "claim_type": "professional",
        "place_of_service": "11",
        "frequency_code": "1",
        "billing_provider_id": None,
        "rendering_provider_id": source_record.get("recorded_by") if source_record else None,
        "facility_id": None,
        "authorization_number": None,
        "referral_number": None,
        "status": "draft",
        "service_date_from": line_docs[0]["service_date"],
        "service_date_to": line_docs[-1]["service_date"],
        "billed_cents": billed_total,
        "paid_cents": 0,
        "submitted_at": None,
        "accepted_at": None,
        "last_denial_code": None,
        "notes": None,
        "validation_error_count": 0,
        "validation_warning_count": 0,
        "validation_last_run_at": None,
        "created_at": now,
        "updated_at": now,
        "created_by": user["id"],
        "updated_by": user["id"],
        "history": [_history_entry(
            user, "drafted_from_invoice",
            invoice_id=invoice_id, lines=len(line_docs),
            billed_cents=billed_total,
        )],
    }, ctx, location_id=inv.get("location_id"))

    if diag_docs:
        await db.claim_diagnoses.insert_many(diag_docs)
    if line_docs:
        await db.claim_lines.insert_many(line_docs)
    if mod_docs:
        await db.claim_line_modifiers.insert_many(mod_docs)
    await db.claims.insert_one(claim_doc)

    await audit_success(
        user, "billing.claim.drafted_from_invoice", request,
        entity_type="claim", entity_id=claim_id,
        metadata={"invoice_id": invoice_id, "billed_cents": billed_total,
                  "lines": len(line_docs)},
    )
    return _public(claim_doc)


@router.post("/claims/{claim_id}/validate")
async def validate_claim(
    claim_id: str,
    request: Request,
    user: dict = Depends(require_permission("claim", "correct_resubmit")),
    ctx: TenantContext = Depends(require_tenant),
):
    """Run the scrubber. Persists findings count on claim header and
    appends a row to `claim_validation_runs`. Auto-transitions claim
    status (draft/validation_failed/ready → validation_failed or ready)."""
    db = tenant_db(ctx.tenant_id)
    claim = await _scoped_one(db.claims, {"id": claim_id}, ctx)
    if not claim:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Claim not found")

    scrub_ctx = await _load_claim_context(db, ctx, claim)
    result = run_rules(scrub_ctx, DEFAULT_RULES)

    now = _now()
    new_status = claim["status"]
    if claim["status"] in ("draft", "validation_failed", "ready"):
        new_status = "ready" if result["passed"] else "validation_failed"

    set_fields = {
        "validation_error_count": len(result["errors"]),
        "validation_warning_count": len(result["warnings"]),
        "validation_last_run_at": now,
        "updated_at": now,
        "updated_by": user["id"],
    }
    if new_status != claim["status"]:
        try:
            transitions.advance("claim", claim["status"], new_status)
            set_fields["status"] = new_status
        except transitions.TransitionError:
            new_status = claim["status"]

    await db.claims.update_one(
        {"id": claim_id, "tenant_id": ctx.tenant_id},
        {"$set": set_fields,
         "$push": {"history": _history_entry(
             user, "validated",
             error_count=len(result["errors"]),
             warning_count=len(result["warnings"]),
             from_status=claim["status"], to_status=new_status,
         )}},
    )

    run_doc = stamp_for_write({
        "id": str(uuid.uuid4()),
        "claim_id": claim_id,
        "run_at": now,
        "run_by": user["id"],
        "errors": result["errors"],
        "warnings": result["warnings"],
        "by_category": result.get("by_category", {}),
        "passed": result["passed"],
        "from_status": claim["status"],
        "to_status": new_status,
        "created_at": now,
    }, ctx, location_id=None)
    await db.claim_validation_runs.insert_one(run_doc)

    await audit_success(
        user, "billing.claim.validated", request,
        entity_type="claim", entity_id=claim_id,
        metadata={"errors": len(result["errors"]),
                  "warnings": len(result["warnings"]),
                  "from_status": claim["status"],
                  "to_status": new_status},
    )
    await emit_claim_event(
        db, ctx,
        claim_id=claim_id,
        event_type="validated",
        actor_id=user["id"],
        payload={"error_count": len(result["errors"]),
                 "warning_count": len(result["warnings"]),
                 "passed": result["passed"],
                 "top_error_codes": [
                     e.get("code") for e in result["errors"][:5]
                 ]},
        from_status=claim["status"],
        to_status=new_status,
        location_id=claim.get("location_id"),
    )
    return {
        "claim_id": claim_id,
        "status": new_status,
        "errors": result["errors"],
        "warnings": result["warnings"],
        "passed": result["passed"],
        "by_category": result.get("by_category", {}),
        "run_at": now,
    }


@router.get("/claims/{claim_id}/validations")
async def list_claim_validations(
    claim_id: str,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    claim = await _scoped_one(db.claims, {"id": claim_id}, ctx)
    if not claim:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Claim not found")
    rows = [d async for d in db.claim_validation_runs.find(
        {"tenant_id": ctx.tenant_id, "claim_id": claim_id}, {"_id": 0},
    ).sort([("run_at", -1)]).limit(20)]
    return rows


@router.get("/claims/{claim_id}/detail")
async def claim_detail(
    claim_id: str,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(require_tenant),
):
    """Everything the UI needs on one page — header + diagnoses + lines
    + modifiers + the most recent scrubber findings + resolved names
    for every foreign-key field so the UI never has to render a raw
    UUID.
    """
    db = tenant_db(ctx.tenant_id)
    claim = await _scoped_one(db.claims, {"id": claim_id}, ctx)
    if not claim:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Claim not found")
    scrub_ctx = await _load_claim_context(db, ctx, claim)
    latest = await db.claim_validation_runs.find_one(
        {"tenant_id": ctx.tenant_id, "claim_id": claim_id},
        {"_id": 0}, sort=[("run_at", -1)],
    )

    # --- Resolve foreign-key references to human-readable strings.
    # Internal IDs stay in storage / transmission; only the display
    # layer gets readable values. Fetch each ref at most once.
    async def _resolve_patient(pid: str | None):
        if not pid:
            return None
        p = await db.patients.find_one(
            {"tenant_id": ctx.tenant_id, "id": pid},
            {"_id": 0, "id": 1, "first_name": 1, "last_name": 1, "mrn": 1},
        )
        if not p:
            return None
        return {
            "id": p["id"],
            "name": (f"{p.get('first_name','')} {p.get('last_name','')}").strip()
                    or None,
            "mrn": p.get("mrn"),
        }

    async def _resolve_payer(pid: str | None):
        if not pid:
            return None
        p = await db.billing_payers.find_one(
            {"tenant_id": ctx.tenant_id, "id": pid},
            {"_id": 0, "id": 1, "name": 1, "payer_type": 1,
             "external_id": 1},
        )
        return p

    async def _resolve_user(uid: str | None):
        if not uid:
            return None
        u = await db.users.find_one(
            {"id": uid},
            {"_id": 0, "id": 1, "name": 1, "display_name": 1,
             "first_name": 1, "last_name": 1, "email": 1, "role": 1},
        )
        if not u:
            return None
        name = (
            u.get("display_name")
            or f"{u.get('first_name','')} {u.get('last_name','')}".strip()
            or u.get("name")
            or u.get("email")
        )
        return {"id": u["id"], "name": name, "role": u.get("role"),
                "email": u.get("email")}

    async def _resolve_provider_row(pid: str | None, kind: str):
        if not pid:
            return None
        hit = await db.providers.find_one(
            {"tenant_id": ctx.tenant_id, "id": pid},
            {"_id": 0, "id": 1, "name": 1, "npi": 1,
             "taxonomy_code": 1, "kind": 1},
        )
        if hit:
            return hit
        # Legacy claims may reference a user id (the old doctor_id
        # shortcut) — surface the user's display name so the UI still
        # shows something meaningful instead of a raw UUID.
        u = await _resolve_user(pid)
        if u:
            return {"id": pid, "name": u["name"], "npi": None,
                    "kind": kind, "source": "user"}
        return None

    async def _resolve_facility(fid: str | None):
        if not fid:
            return None
        f = await db.service_facilities.find_one(
            {"tenant_id": ctx.tenant_id, "id": fid},
            {"_id": 0, "id": 1, "name": 1, "npi": 1},
        )
        return f

    async def _resolve_location(lid: str | None):
        if not lid:
            return None
        loc = await db.locations.find_one(
            {"id": lid},
            {"_id": 0, "id": 1, "name": 1, "code": 1},
        )
        return loc

    refs = {
        "patient": await _resolve_patient(claim.get("patient_id")),
        "payer": await _resolve_payer(claim.get("payer_id")),
        "billing_provider": await _resolve_provider_row(
            claim.get("billing_provider_id"), "billing",
        ),
        "rendering_provider": await _resolve_provider_row(
            claim.get("rendering_provider_id"), "rendering",
        ),
        "facility": await _resolve_facility(claim.get("facility_id")),
        "location": await _resolve_location(claim.get("location_id")),
        "assignee": await _resolve_user(claim.get("assigned_to")),
        "created_by": await _resolve_user(claim.get("created_by")),
        "updated_by": await _resolve_user(claim.get("updated_by")),
    }

    return {
        "claim": _public(claim),
        "diagnoses": scrub_ctx.diagnoses,
        "lines": [
            {**ln, "modifiers": [m["modifier_code"]
                                 for m in scrub_ctx.line_modifiers_by_line.get(ln["id"], [])]}
            for ln in scrub_ctx.lines
        ],
        "latest_validation": latest,
        "refs": refs,
    }


_EDITABLE_STATUSES = {"draft", "validation_failed", "rejected"}


@router.patch("/claims/{claim_id}/header", response_model=ClaimPublic)
async def update_claim_header(
    claim_id: str,
    request: Request,
    user: dict = Depends(require_permission("claim", "correct_resubmit")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    claim = await _scoped_one(db.claims, {"id": claim_id}, ctx)
    if not claim:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Claim not found")
    if claim["status"] not in _EDITABLE_STATUSES:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Claim in status {claim['status']} is not editable",
        )
    body = await request.json()
    allowed = {
        "claim_type", "place_of_service", "frequency_code",
        "billing_provider_id", "rendering_provider_id", "facility_id",
        "authorization_number", "referral_number",
        "service_date_from", "service_date_to",
        "payer_id", "policy_id", "notes",
    }
    updates = {k: v for k, v in (body or {}).items() if k in allowed}
    if not updates:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No editable fields")
    updates["updated_at"] = _now()
    updates["updated_by"] = user["id"]
    await db.claims.update_one(
        {"id": claim_id, "tenant_id": ctx.tenant_id},
        {"$set": updates,
         "$push": {"history": _history_entry(
             user, "header_updated", fields=sorted(list(updates.keys())),
         )}},
    )
    fresh = await db.claims.find_one(
        {"id": claim_id, "tenant_id": ctx.tenant_id}, {"_id": 0},
    )
    await audit_success(
        user, "billing.claim.header_updated", request,
        entity_type="claim", entity_id=claim_id,
        metadata={"fields": sorted(list(updates.keys()))},
    )
    return _public(fresh)


@router.patch("/claims/{claim_id}/diagnoses")
async def replace_claim_diagnoses(
    claim_id: str,
    request: Request,
    user: dict = Depends(require_permission("claim", "correct_resubmit")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    claim = await _scoped_one(db.claims, {"id": claim_id}, ctx)
    if not claim:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Claim not found")
    if claim["status"] not in _EDITABLE_STATUSES:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Claim in status {claim['status']} is not editable",
        )
    body = await request.json()
    if not isinstance(body, list):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "body must be a list of diagnoses")
    if not 1 <= len(body) <= 12:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "claims need 1..12 diagnoses")
    now = _now()
    await db.claim_diagnoses.delete_many(
        {"tenant_id": ctx.tenant_id, "claim_id": claim_id},
    )
    docs = []
    for i, d in enumerate(body, start=1):
        docs.append(stamp_for_write({
            "id": str(uuid.uuid4()),
            "claim_id": claim_id,
            "sequence": int(d.get("sequence", i)),
            "code": (d.get("code") or "").strip(),
            "created_at": now,
        }, ctx, location_id=None))
    if docs:
        await db.claim_diagnoses.insert_many(docs)
    await db.claims.update_one(
        {"id": claim_id, "tenant_id": ctx.tenant_id},
        {"$set": {"updated_at": now, "updated_by": user["id"]},
         "$push": {"history": _history_entry(
             user, "diagnoses_replaced", count=len(docs),
         )}},
    )
    await audit_success(
        user, "billing.claim.diagnoses_replaced", request,
        entity_type="claim", entity_id=claim_id,
        metadata={"count": len(docs)},
    )
    return {"ok": True, "count": len(docs)}


@router.patch("/claims/{claim_id}/lines")
async def replace_claim_lines(
    claim_id: str,
    request: Request,
    user: dict = Depends(require_permission("claim", "correct_resubmit")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    claim = await _scoped_one(db.claims, {"id": claim_id}, ctx)
    if not claim:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Claim not found")
    if claim["status"] not in _EDITABLE_STATUSES:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Claim in status {claim['status']} is not editable",
        )
    body = await request.json()
    if not isinstance(body, list) or not 1 <= len(body) <= 50:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "claims need 1..50 lines")

    now = _now()
    existing_lines = [d async for d in db.claim_lines.find(
        {"tenant_id": ctx.tenant_id, "claim_id": claim_id},
        {"_id": 0, "id": 1},
    )]
    if existing_lines:
        await db.claim_line_modifiers.delete_many({
            "tenant_id": ctx.tenant_id,
            "claim_line_id": {"$in": [x["id"] for x in existing_lines]},
        })
    await db.claim_lines.delete_many(
        {"tenant_id": ctx.tenant_id, "claim_id": claim_id},
    )

    line_docs: list[dict] = []
    mod_docs: list[dict] = []
    total_billed = 0
    for i, ln in enumerate(body, start=1):
        cl_id = str(uuid.uuid4())
        units = max(1, int(ln.get("units", 1)))
        billed = int(ln.get("billed_cents", 0))
        total_billed += units * billed
        line_docs.append(stamp_for_write({
            "id": cl_id,
            "claim_id": claim_id,
            "sequence": int(ln.get("sequence", i)),
            "invoice_line_id": ln.get("invoice_line_id"),
            "service_date": ln.get("service_date"),
            "code_type": ln.get("code_type", "cpt"),
            "code": (ln.get("code") or "").strip(),
            "units": units,
            "billed_cents": billed,
            "diagnosis_pointers": ln.get("diagnosis_pointers") or [],
            "created_at": now,
        }, ctx, location_id=claim.get("location_id")))
        for j, mc in enumerate(ln.get("modifiers") or [], start=1):
            mod_docs.append(stamp_for_write({
                "id": str(uuid.uuid4()),
                "claim_line_id": cl_id,
                "sequence": j,
                "modifier_code": str(mc).strip(),
                "created_at": now,
            }, ctx, location_id=None))

    if line_docs:
        await db.claim_lines.insert_many(line_docs)
    if mod_docs:
        await db.claim_line_modifiers.insert_many(mod_docs)

    await db.claims.update_one(
        {"id": claim_id, "tenant_id": ctx.tenant_id},
        {"$set": {"billed_cents": total_billed,
                  "updated_at": now, "updated_by": user["id"]},
         "$push": {"history": _history_entry(
             user, "lines_replaced",
             count=len(line_docs), billed_cents=total_billed,
         )}},
    )
    await audit_success(
        user, "billing.claim.lines_replaced", request,
        entity_type="claim", entity_id=claim_id,
        metadata={"count": len(line_docs), "billed_cents": total_billed},
    )
    return {"ok": True, "count": len(line_docs), "billed_cents": total_billed}



# ---------------------------------------------------------------------------
# Phase 4 — Submissions, outcomes, timeline, work queues, assignment
# ---------------------------------------------------------------------------
# Map each outcome kind to the next claim status. Reject/accept/pending
# are front-line transitions; paid/denied are back-end adjudication.
_OUTCOME_TO_STATUS: dict[str, str] = {
    "accepted": "accepted",
    "rejected": "rejected",
    "pending": "pending",
    "paid": "paid",
    "partially_paid": "partially_paid",
    "denied": "denied",
}


async def _load_submission_context(db, ctx: TenantContext, claim: dict):
    """Pull patient + payer + policy + dx + lines + resolved providers
    for payload builds. Providers / service facility are resolved by
    the claim's `billing_provider_id` / `rendering_provider_id` /
    `facility_id` — first by collection `id`, then by NPI, so legacy
    free-text NPI values continue to work."""
    tid = ctx.tenant_id
    patient = await db.patients.find_one(
        {"id": claim.get("patient_id"), "tenant_id": tid}, {"_id": 0},
    ) if claim.get("patient_id") else None
    payer = await db.billing_payers.find_one(
        {"id": claim.get("payer_id"), "tenant_id": tid}, {"_id": 0},
    ) if claim.get("payer_id") else None
    policy = await db.patient_insurance_policies.find_one(
        {"id": claim.get("policy_id"), "tenant_id": tid}, {"_id": 0},
    ) if claim.get("policy_id") else None
    diagnoses = [d async for d in db.claim_diagnoses.find(
        {"tenant_id": tid, "claim_id": claim["id"]}, {"_id": 0},
    ).sort([("sequence", 1)])]
    lines = [ln async for ln in db.claim_lines.find(
        {"tenant_id": tid, "claim_id": claim["id"]}, {"_id": 0},
    ).sort([("sequence", 1)])]
    # Attach modifiers to each line (phase 3 stored them separately).
    for ln in lines:
        mods = [m async for m in db.claim_line_modifiers.find(
            {"tenant_id": tid, "claim_line_id": ln["id"]}, {"_id": 0},
        ).sort([("sequence", 1)])]
        ln["modifiers"] = [m["modifier_code"] for m in mods]

    # Phase 7 — resolve billing / rendering provider + service facility
    # for the 837P Loop 2010AA / 2310B / 2310C. Resolution is best-
    # effort: callers that haven't migrated to the providers collection
    # still get a working claim (the wire builder falls back to the
    # legacy `billing_provider_id` field carried on the claim).
    async def _resolve_provider(ref: str | None, *, kind: str | None = None):
        if not ref:
            return None
        q = {"tenant_id": tid, "id": ref}
        if kind:
            q["kind"] = kind
        hit = await db.providers.find_one(q, {"_id": 0})
        if hit:
            return hit
        by_npi = {"tenant_id": tid, "npi": ref}
        if kind:
            by_npi["kind"] = kind
        return await db.providers.find_one(by_npi, {"_id": 0})

    async def _resolve_facility(ref: str | None):
        if not ref:
            return None
        hit = await db.service_facilities.find_one(
            {"tenant_id": tid, "id": ref}, {"_id": 0},
        )
        if hit:
            return hit
        return await db.service_facilities.find_one(
            {"tenant_id": tid, "npi": ref}, {"_id": 0},
        )

    billing_provider = await _resolve_provider(
        claim.get("billing_provider_id"), kind="billing",
    )
    rendering_provider = await _resolve_provider(
        claim.get("rendering_provider_id"), kind="rendering",
    )
    service_facility = await _resolve_facility(claim.get("facility_id"))
    return (patient, payer, policy, diagnoses, lines,
            billing_provider, rendering_provider, service_facility)


@router.post(
    "/claims/{claim_id}/submissions",
    response_model=ClaimSubmissionPublic,
    status_code=201,
)
async def create_claim_submission(
    claim_id: str,
    body: ClaimSubmissionCreate,
    request: Request,
    user: dict = Depends(require_permission("claim", "submit")),
    ctx: TenantContext = Depends(require_tenant),
):
    """Validate + submit a single claim.

    Phase 8 — the scrubber runs before every submission attempt. A
    claim that fails validation is auto-routed to `validation_failed`
    (canonical "needs_fixes") and the endpoint returns 422 with the
    structured findings. Only claims in `ready` that pass the scrubber
    reach the clearinghouse adapter.
    """
    db = tenant_db(ctx.tenant_id)
    claim = await _scoped_one(db.claims, {"id": claim_id}, ctx)
    if not claim:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Claim not found")

    current = claim["status"]
    if current not in ("ready",):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Claim in status '{current}' cannot be submitted; "
            "transition to 'ready' first.",
        )

    # Pre-submit validation gate (Phase 8).
    gate = await _run_validation_gate(db, ctx, user, claim, request)
    if not gate["passed"]:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "VALIDATION_FAILED",
                "message": "Claim failed validation and cannot be submitted.",
                "claim_id": claim_id,
                "validation_run_id": gate["validation_run_id"],
                "errors": gate["errors"],
                "warnings": gate["warnings"],
            },
        )

    submission = await _do_submit_claim(
        db, ctx, user, claim, body, request,
    )
    out = {k: v for k, v in submission.items()
           if k not in ("payload_json", "payload_x12")}
    return out


@router.post("/claims/submit-batch")
async def submit_claim_batch(
    body: ClaimBulkSubmitRequest,
    request: Request,
    user: dict = Depends(require_permission("claim", "submit")),
    ctx: TenantContext = Depends(require_tenant),
):
    """Phase 8 — bulk validate + submit.

    Processes up to 50 claim_ids in one call. Each claim is validated
    first; failures are isolated to that claim only. Returns a
    per-claim summary so the UI can drive a "queued N, failed M,
    skipped K" status line and let operators drill into any row.

    No job queue is involved — submissions run synchronously in
    claim-id order so the response already carries every result.
    """
    db = tenant_db(ctx.tenant_id)
    started = _now()
    correlation_id = f"batch-{uuid.uuid4().hex[:12]}"

    submitted: list[dict] = []
    failed_validation: list[dict] = []
    skipped: list[dict] = []

    await audit_success(
        user, "billing.claim.bulk_submit_started", request,
        entity_type="claim_batch", entity_id=correlation_id,
        metadata={"claim_count": len(body.claim_ids),
                  "method": body.method,
                  "strict": body.strict,
                  "correlation_id": correlation_id},
    )

    for claim_id in body.claim_ids:
        claim = await _scoped_one(db.claims, {"id": claim_id}, ctx)
        if not claim:
            skipped.append({
                "claim_id": claim_id,
                "reason": "not_found",
                "message": "Claim not found in tenant.",
            })
            continue
        if claim["status"] != "ready":
            skipped.append({
                "claim_id": claim_id,
                "reason": "wrong_status",
                "message": (
                    f"Claim status is '{claim['status']}'; "
                    "only 'ready' claims are eligible for batch submission."
                ),
                "status": claim["status"],
            })
            continue

        gate = await _run_validation_gate(db, ctx, user, claim, request)
        if not gate["passed"]:
            failed_validation.append({
                "claim_id": claim_id,
                "validation_run_id": gate["validation_run_id"],
                "error_count": len(gate["errors"]),
                "warning_count": len(gate["warnings"]),
                "top_error_codes": [e.get("code") for e in gate["errors"][:5]],
            })
            continue

        single_body = ClaimSubmissionCreate(
            method=body.method,
            external_reference=body.external_reference,
            notes=body.notes,
        )
        try:
            sub_doc = await _do_submit_claim(
                db, ctx, user, claim, single_body, request,
                correlation_id=correlation_id,
            )
        except HTTPException as exc:
            # Adapter-level transport failure: log & continue. Other
            # claims in the batch still get their shot.
            skipped.append({
                "claim_id": claim_id,
                "reason": "adapter_error",
                "message": exc.detail if isinstance(exc.detail, str)
                           else "Clearinghouse submission failed.",
                "status_code": exc.status_code,
            })
            continue
        submitted.append({
            "claim_id": claim_id,
            "submission_id": sub_doc["id"],
            "adapter_route": sub_doc.get("adapter_route"),
            "adapter_status": sub_doc.get("adapter_status"),
            "adapter_external_id": sub_doc.get("adapter_external_id"),
            "trace_id": sub_doc.get("trace_id"),
            "correlation_id": sub_doc.get("correlation_id"),
            "sandbox": sub_doc.get("sandbox", False),
        })

    if body.strict and failed_validation and not submitted and not skipped:
        # Every single claim failed validation and caller asked for
        # strict mode — surface it as a 422 so the UI can prompt a
        # batch-wide fix workflow instead of a silent "0 submitted".
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "BATCH_VALIDATION_FAILED",
                "message": "Every claim in the batch failed validation.",
                "correlation_id": correlation_id,
                "failed_validation": failed_validation,
            },
        )

    result = {
        "correlation_id": correlation_id,
        "started_at": started,
        "completed_at": _now(),
        "requested": len(body.claim_ids),
        "submitted": submitted,
        "failed_validation": failed_validation,
        "skipped": skipped,
    }
    await audit_success(
        user, "billing.claim.bulk_submit_completed", request,
        entity_type="claim_batch", entity_id=correlation_id,
        metadata={"submitted": len(submitted),
                  "failed_validation": len(failed_validation),
                  "skipped": len(skipped),
                  "correlation_id": correlation_id},
    )
    return result


@router.get("/clearinghouse/transmissions")
async def list_clearinghouse_transmissions(
    claim_id: str | None = Query(default=None),
    adapter_route: str | None = Query(default=None),
    adapter_status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(require_tenant),
):
    """Phase 8 — unified transmission log.

    Returns the N most recent claim submissions across the tenant with
    filters by claim / adapter route / adapter status. This is the
    read-side of Phase 8's "persist request/response artifacts and
    submission history" requirement — `claim_submissions` is already
    our transmission log, this endpoint just exposes it with the
    operator-relevant fields projected.
    """
    db = tenant_db(ctx.tenant_id)
    q = scoped_filter({}, ctx, location_scoped=False)
    if q.get("__deny__"):
        return []
    if claim_id:
        q["claim_id"] = claim_id
    if adapter_route:
        q["adapter_route"] = adapter_route
    if adapter_status:
        q["adapter_status"] = adapter_status
    rows = [r async for r in db.claim_submissions.find(
        q, {"_id": 0, "payload_json": 0, "payload_x12": 0},
    ).sort([("submitted_at", -1)]).limit(limit)]
    return rows


# ---------------------------------------------------------------------------
# Phase 10 — inbound response / report ingest + operational follow-up flag
# ---------------------------------------------------------------------------
# Canonical raw-status transitions driven by inbound artifacts. The
# mapping is intentionally conservative: only `submitted` / `pending`
# claims are moved by an ack. A claim already sitting in `paid` or
# `closed` will NOT be flipped by a late-arriving 277CA rejection —
# the operator must open it manually and retrigger.
_ACK_STATUS_MAP: dict[tuple[str, str], str] = {
    # (report_type, status): target raw claim status
    ("999", "accepted"):         "accepted",
    ("999", "rejected"):         "rejected",
    ("277ca", "accepted"):       "accepted",
    ("277ca", "rejected"):       "rejected",
    # Portal confirmations + batch acks stamp `accepted` when the
    # human (or EDI partner) confirmed receipt.
    ("portal_confirmation", "accepted"): "accepted",
    ("batch_ack",           "accepted"): "accepted",
    # ERA receipt is an audit marker only — payment posting is
    # handled by `services.billing.remittance_import` so we do NOT
    # flip the claim status here.
}


def _report_event_type(report_type: str, status: str) -> str | None:
    """Map (report_type, status) to a canonical `ClaimEventType`
    so the timeline surfaces inbound acks alongside everything else."""
    key = (report_type, status)
    return {
        ("999",   "accepted"): "ack_999_accepted",
        ("999",   "rejected"): "ack_999_rejected",
        ("277ca", "accepted"): "ack_277ca_accepted",
        ("277ca", "rejected"): "ack_277ca_rejected",
        ("era_835_receipt", "info"):     "era_posted",
        ("era_835_receipt", "accepted"): "era_posted",
        ("portal_confirmation", "accepted"): "ack_999_accepted",
        ("batch_ack",           "accepted"): "ack_999_accepted",
    }.get(key)


@router.post(
    "/clearinghouse/reports/ingest",
    response_model=ClearinghouseReportPublic,
    status_code=201,
)
async def ingest_clearinghouse_report(
    body: ClearinghouseReportIngestRequest,
    request: Request,
    user: dict = Depends(require_permission("claim", "submit")),
    ctx: TenantContext = Depends(require_tenant),
):
    """Phase 10 — accept an inbound artifact (999 / 277CA / ERA /
    portal confirmation / batch ack / other) and tie it back to a
    claim.

    Resolution order for the owning claim:
      1. `claim_id`
      2. `submission_id` → `claim_submissions.claim_id`
      3. `adapter_external_id` → most recent submission with that
         adapter tracking id
    Batch-level acks (`report_type == "batch_ack"` without any claim
    link) are accepted and stored but skip status updates.
    """
    db = tenant_db(ctx.tenant_id)

    # Resolve the owning claim and submission.
    claim: dict | None = None
    submission: dict | None = None
    if body.claim_id:
        claim = await _scoped_one(db.claims, {"id": body.claim_id}, ctx)
    if body.submission_id and not submission:
        submission = await db.claim_submissions.find_one(
            {"id": body.submission_id, "tenant_id": ctx.tenant_id},
            {"_id": 0},
        )
    if body.adapter_external_id and not submission:
        submission = await db.claim_submissions.find_one(
            {"adapter_external_id": body.adapter_external_id,
             "tenant_id": ctx.tenant_id},
            sort=[("submitted_at", -1)],
            projection={"_id": 0},
        )
    if submission and not claim:
        claim = await _scoped_one(
            db.claims, {"id": submission["claim_id"]}, ctx,
        )
    if not claim and body.report_type != "batch_ack":
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "No claim matched the provided claim_id / submission_id / "
            "adapter_external_id. Use report_type='batch_ack' for "
            "batch-level acknowledgments without a claim link.",
        )

    now = _now()
    received_at = body.received_at or now
    raw_content = body.raw_content or ""
    raw_hash = (
        hashlib.sha256(raw_content.encode("utf-8")).hexdigest()
        if raw_content else None
    )

    report_id = str(uuid.uuid4())
    report_doc = stamp_for_write({
        "id": report_id,
        "clearinghouse": body.clearinghouse.strip(),
        "report_type": body.report_type,
        "status": body.status,
        "claim_id": (claim or {}).get("id"),
        "submission_id": (submission or {}).get("id"),
        "external_id": (body.adapter_external_id
                        or (submission or {}).get("adapter_external_id")),
        "received_at": received_at,
        "raw_content": raw_content or None,
        "raw_hash": raw_hash,
        "parsed": body.parsed,
        "notes": body.notes,
        "denial_code": body.denial_code,
        "created_at": now,
    }, ctx, location_id=(claim or {}).get("location_id"))
    await db.clearinghouse_reports.insert_one(report_doc)

    await audit_success(
        user, "billing.clearinghouse.report_ingested", request,
        entity_type="claim" if (claim or {}).get("id") else "clearinghouse_report",
        entity_id=(claim or {}).get("id") or report_id,
        metadata={"report_id": report_id,
                  "report_type": body.report_type,
                  "status": body.status,
                  "clearinghouse": body.clearinghouse,
                  "claim_id": (claim or {}).get("id"),
                  "submission_id": (submission or {}).get("id")},
    )

    # Status update (only when we have a claim AND the map applies
    # AND the current status is still eligible for an ack flip).
    status_changed = False
    if claim is not None:
        target = _ACK_STATUS_MAP.get((body.report_type, body.status))
        if target and claim["status"] in ("submitted", "pending"):
            try:
                new_raw = transitions.http_advance(
                    "claim", claim["status"], target,
                )
            except HTTPException:
                new_raw = claim["status"]
            if new_raw != claim["status"]:
                await db.claims.update_one(
                    {"id": claim["id"], "tenant_id": ctx.tenant_id},
                    {"$set": {
                        "status": new_raw,
                        "updated_at": now,
                        "updated_by": user["id"],
                     },
                     "$push": {"history": _history_entry(
                         user, "ack_received",
                         report_type=body.report_type,
                         status=body.status,
                         report_id=report_id,
                         from_status=claim["status"],
                         to_status=new_raw,
                     )}},
                )
                status_changed = True
                claim["status"] = new_raw

        # Timeline event.
        event_type = _report_event_type(body.report_type, body.status)
        if event_type:
            await emit_claim_event(
                db, ctx,
                claim_id=claim["id"],
                event_type=event_type,
                actor_id=user["id"],
                payload={"report_id": report_id,
                         "clearinghouse": body.clearinghouse,
                         "report_type": body.report_type,
                         "report_status": body.status,
                         "denial_code": body.denial_code,
                         "notes": body.notes,
                         "status_changed": status_changed},
                submission_id=(submission or {}).get("id"),
                adapter_route=body.clearinghouse,
                denial_code=body.denial_code,
                from_status=claim["status"] if not status_changed else None,
                to_status=claim["status"] if status_changed else None,
                occurred_at=received_at,
                location_id=claim.get("location_id"),
            )

        # Rejected ack => auto-flag for follow-up so operators see it.
        if body.status == "rejected" and body.report_type in ("999", "277ca"):
            await _auto_flag_followup(
                db, ctx, user, claim,
                reason=f"{body.report_type.upper()} rejection: "
                       f"{body.notes or 'see report'}"[:280],
                source="inbound_rejection",
            )

    fresh = await db.clearinghouse_reports.find_one(
        {"id": report_id, "tenant_id": ctx.tenant_id}, {"_id": 0},
    )
    return fresh


@router.get("/clearinghouse/reports")
async def list_clearinghouse_reports(
    claim_id: str | None = Query(default=None),
    submission_id: str | None = Query(default=None),
    report_type: str | None = Query(default=None),
    status_: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=500),
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(require_tenant),
):
    """List recent inbound artifacts with payload trimming."""
    db = tenant_db(ctx.tenant_id)
    q = scoped_filter({}, ctx, location_scoped=False)
    if q.get("__deny__"):
        return []
    if claim_id:
        q["claim_id"] = claim_id
    if submission_id:
        q["submission_id"] = submission_id
    if report_type:
        q["report_type"] = report_type
    if status_:
        q["status"] = status_
    rows = [r async for r in db.clearinghouse_reports.find(
        q, {"_id": 0},
    ).sort([("received_at", -1)]).limit(limit)]
    return rows


async def _auto_flag_followup(
    db, ctx: TenantContext, user: dict, claim: dict,
    *, reason: str, source: str,
) -> None:
    """Helper used by both the manual-flag endpoint and the inbound
    pipeline. Never raises — a flag is strictly additive, the worst
    case is the row already had one and we refresh it."""
    now = _now()
    # SLA default: next action due in 3 business-ish days.
    from datetime import timedelta
    next_action = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    await db.claims.update_one(
        {"id": claim["id"], "tenant_id": ctx.tenant_id},
        {"$set": {
            "followup_flag": True,
            "followup_reason": reason,
            "followup_flagged_at": now,
            "followup_flagged_by": user["id"],
            "next_action_at": next_action,
            "updated_at": now,
        },
         "$push": {"history": _history_entry(
             user, "flagged_for_followup",
             reason=reason, source=source,
             next_action_at=next_action,
         )}},
    )


@router.post("/claims/{claim_id}/flag-followup", response_model=ClaimPublic)
async def flag_claim_for_followup(
    claim_id: str,
    body: ClaimFollowupFlagRequest,
    request: Request,
    user: dict = Depends(require_permission("claim", "correct_resubmit")),
    ctx: TenantContext = Depends(require_tenant),
):
    """Phase 10 — manually flag a claim so it surfaces on the
    'Follow-up needed' tab with an explicit triage reason."""
    db = tenant_db(ctx.tenant_id)
    claim = await _scoped_one(db.claims, {"id": claim_id}, ctx)
    if not claim:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Claim not found")
    now = _now()
    next_action = body.next_action_at
    if not next_action:
        from datetime import timedelta
        next_action = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    await db.claims.update_one(
        {"id": claim_id, "tenant_id": ctx.tenant_id},
        {"$set": {
            "followup_flag": True,
            "followup_reason": body.reason,
            "followup_flagged_at": now,
            "followup_flagged_by": user["id"],
            "next_action_at": next_action,
            "updated_at": now,
            "updated_by": user["id"],
         },
         "$push": {"history": _history_entry(
             user, "flagged_for_followup",
             reason=body.reason, source="manual",
             next_action_at=next_action,
         )}},
    )
    await audit_success(
        user, "billing.claim.followup_flagged", request,
        entity_type="claim", entity_id=claim_id,
        metadata={"reason": body.reason, "next_action_at": next_action,
                  "source": "manual"},
    )
    fresh = await _scoped_one(db.claims, {"id": claim_id}, ctx)
    return fresh


@router.delete("/claims/{claim_id}/flag-followup", response_model=ClaimPublic)
async def clear_claim_followup_flag(
    claim_id: str,
    request: Request,
    user: dict = Depends(require_permission("claim", "correct_resubmit")),
    ctx: TenantContext = Depends(require_tenant),
):
    """Clear the manual follow-up flag after the claim is triaged.
    The aged / partially-paid / appealed / stale-submit branches of
    the follow-up tab are NOT affected — only the manual flag."""
    db = tenant_db(ctx.tenant_id)
    claim = await _scoped_one(db.claims, {"id": claim_id}, ctx)
    if not claim:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Claim not found")
    now = _now()
    await db.claims.update_one(
        {"id": claim_id, "tenant_id": ctx.tenant_id},
        {"$set": {
            "followup_flag": False,
            "followup_reason": None,
            "followup_flagged_at": None,
            "followup_flagged_by": None,
            "next_action_at": None,
            "updated_at": now,
            "updated_by": user["id"],
         },
         "$push": {"history": _history_entry(
             user, "followup_cleared",
         )}},
    )
    await audit_success(
        user, "billing.claim.followup_cleared", request,
        entity_type="claim", entity_id=claim_id,
        metadata={},
    )
    fresh = await _scoped_one(db.claims, {"id": claim_id}, ctx)
    return fresh


# ---------------------------------------------------------------------------
# Phase 8 — reusable validation + submission pipeline helpers
# ---------------------------------------------------------------------------
async def _run_validation_gate(
    db, ctx: TenantContext, user: dict, claim: dict, request: Request,
) -> dict:
    """Run the scrubber as a pre-submit gate.

    On failure the claim auto-transitions to `validation_failed`
    (canonical "needs_fixes"), a `claim_validation_runs` row is
    persisted, an audit event is written, and the `validated` claim
    event is emitted. Returns a dict with
    `{passed, errors, warnings, validation_run_id}` regardless of
    outcome so callers can propagate findings to the UI.
    """
    claim_id = claim["id"]
    scrub_ctx = await _load_claim_context(db, ctx, claim)
    result = run_rules(scrub_ctx, DEFAULT_RULES)

    now = _now()
    from_status = claim["status"]
    new_status = from_status
    # The gate only flips to `validation_failed` — it never advances
    # to `ready` because that transition is owned by `validate_claim`.
    # This keeps the gate side-effect narrow: it only ever *blocks*.
    if not result["passed"] and from_status == "ready":
        try:
            transitions.advance("claim", from_status, "validation_failed")
            new_status = "validation_failed"
        except transitions.TransitionError:
            new_status = from_status

    set_fields = {
        "validation_error_count": len(result["errors"]),
        "validation_warning_count": len(result["warnings"]),
        "validation_last_run_at": now,
        "updated_at": now,
        "updated_by": user["id"],
    }
    if new_status != from_status:
        set_fields["status"] = new_status

    await db.claims.update_one(
        {"id": claim_id, "tenant_id": ctx.tenant_id},
        {"$set": set_fields,
         "$push": {"history": _history_entry(
             user, "validated",
             gate="submission",
             error_count=len(result["errors"]),
             warning_count=len(result["warnings"]),
             from_status=from_status, to_status=new_status,
         )}},
    )

    run_doc = stamp_for_write({
        "id": str(uuid.uuid4()),
        "claim_id": claim_id,
        "run_at": now,
        "run_by": user["id"],
        "errors": result["errors"],
        "warnings": result["warnings"],
        "by_category": result.get("by_category", {}),
        "passed": result["passed"],
        "from_status": from_status,
        "to_status": new_status,
        "gate": "submission",
        "created_at": now,
    }, ctx, location_id=None)
    await db.claim_validation_runs.insert_one(run_doc)

    await audit_success(
        user, "billing.claim.pre_submit_validated", request,
        entity_type="claim", entity_id=claim_id,
        metadata={"errors": len(result["errors"]),
                  "warnings": len(result["warnings"]),
                  "passed": result["passed"],
                  "from_status": from_status,
                  "to_status": new_status},
    )
    await emit_claim_event(
        db, ctx,
        claim_id=claim_id,
        event_type="validated",
        actor_id=user["id"],
        payload={"gate": "submission",
                 "error_count": len(result["errors"]),
                 "warning_count": len(result["warnings"]),
                 "passed": result["passed"],
                 "top_error_codes": [
                     e.get("code") for e in result["errors"][:5]
                 ]},
        from_status=from_status,
        to_status=new_status,
        location_id=claim.get("location_id"),
    )

    # If status moved, mutate the in-memory dict so downstream callers
    # (e.g. the bulk loop) see the fresh status without a re-read.
    if new_status != from_status:
        claim["status"] = new_status
    return {
        "passed": result["passed"],
        "errors": result["errors"],
        "warnings": result["warnings"],
        "validation_run_id": run_doc["id"],
        "from_status": from_status,
        "to_status": new_status,
    }


async def _do_submit_claim(
    db, ctx: TenantContext, user: dict, claim: dict,
    body: ClaimSubmissionCreate, request: Request,
    *, correlation_id: str | None = None,
) -> dict:
    """Core single-claim submission pipeline.

    Assumes the caller has already gated the claim through validation
    and confirmed `claim.status == "ready"`. Builds payloads, hands to
    the adapter, persists the submission row, transitions the claim,
    audits, and emits a `submitted` / `resubmitted` event. Returns the
    persisted submission dict (payload fields included — the outer
    HTTP handler is responsible for stripping them from the response).
    """
    claim_id = claim["id"]
    current = claim["status"]
    new_status = transitions.http_advance("claim", current, "submitted")

    patient, payer, policy, diagnoses, lines, \
        billing_provider, rendering_provider, service_facility = \
        await _load_submission_context(db, ctx, claim)

    payload_json = build_json_payload(
        claim=claim, diagnoses=diagnoses, lines=lines,
        patient=patient, payer=payer, policy=policy,
    )

    adapter = get_adapter_for_payer(payer)
    identity_fn = getattr(adapter, "submission_identity", None)
    envelope = identity_fn() if callable(identity_fn) else {}
    submitter = {
        "id": envelope.get("submitter_id") or envelope.get("biller_id") or "CCMS",
        "name": envelope.get("receiver_name") or "CCMS BILLING",
        "contact_name": "BILLING",
    }
    receiver = {
        "id": envelope.get("receiver_id") or "PAYER",
        "name": envelope.get("receiver_name")
                 or (payer or {}).get("name") or "PAYER",
    }
    # If no billing_provider row is configured for this tenant, we
    # synthesize a placeholder — but NEVER use the raw claim field as
    # NPI. A zero-NPI placeholder is unambiguously a "not configured"
    # value on the wire; a leaked UUID would be a silent correctness
    # bug. Real EDI submissions (batch_file) to a clearinghouse still
    # require the caller to configure a providers directory; the
    # adapter will reject a zero-NPI payload.
    if billing_provider is None:
        billing_provider = {
            "name": "PROVIDER NOT CONFIGURED",
            "npi": "0000000000",
            "entity_type": "organization",
            "address": None,
            "tax_id": None,
        }
    payload_x12 = build_x12_837p_wire(
        claim=claim, diagnoses=diagnoses, lines=lines,
        patient=patient, payer=payer, policy=policy,
        billing_provider=billing_provider,
        rendering_provider=rendering_provider,
        service_facility=service_facility,
        submitter=submitter,
        receiver=receiver,
        control_numbers={
            "usage_indicator": (
                "P" if getattr(adapter, "_mode", "") == "production" else "T"
            ),
        },
    )

    try:
        adapter_result = await adapter.submit(
            claim_id=claim_id,
            payload_json=payload_json,
            payload_x12=payload_x12,
            method=body.method,
            external_reference=body.external_reference,
            payer=payer or {},
        )
    except Exception:   # pragma: no cover — defensive; no-op adapter never raises
        import logging as _logging
        _logging.getLogger("ccms.billing.clearinghouse").exception(
            "billing.clearinghouse.submit_failed",
            extra={"claim_id": claim_id,
                   "route": (payer or {}).get("clearinghouse_route")},
        )
        await audit_failure(
            action="billing.claim.submission_failed",
            request=request,
            actor_email=user.get("email"),
            reason="adapter_exception",
            metadata={"claim_id": claim_id,
                      "route": (payer or {}).get("clearinghouse_route")},
        )
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Clearinghouse submission failed; please retry.",
        )

    now = _now()
    sub_id = str(uuid.uuid4())
    identity = envelope or {}
    st02_control = f"{uuid.uuid4().int % 10**9:09d}"
    raw_hash = hashlib.sha256((payload_x12 or "").encode("utf-8")).hexdigest()
    sub_doc = stamp_for_write({
        "id": sub_id,
        "claim_id": claim_id,
        "method": body.method,
        "external_reference": body.external_reference,
        "submitted_at": now,
        "submitted_by": user["id"],
        "payload_format": "json+x12-837p-005010X222A1",
        "payload_json": payload_json,
        "payload_x12": payload_x12,
        "payload_size_bytes": len(payload_x12),
        # Phase 2a — adapter handoff metadata.
        "adapter_route": adapter_result.adapter_route,
        "adapter_status": adapter_result.status,
        "adapter_external_id": adapter_result.external_id,
        "adapter_message": adapter_result.message,
        # Phase 8 — transport trace identifiers.
        "trace_id": getattr(adapter_result, "trace_id", None),
        "correlation_id": (
            correlation_id or getattr(adapter_result, "correlation_id", None)
        ),
        "sandbox": bool(getattr(adapter_result, "sandbox", False)),
        "adapter_raw": adapter_result.raw,
        # Phase 6 — envelope identity snapshot + ST02 + raw 837 hash.
        "receiver_id": identity.get("receiver_id"),
        "receiver_name": identity.get("receiver_name"),
        "biller_id": identity.get("biller_id"),
        "submitter_id": identity.get("submitter_id"),
        "st02_control_number": st02_control,
        "raw_837_hash": raw_hash,
        "sent_at": now,
        "received_at": None,
        "outcome": None,
        "outcome_at": None,
        "outcome_by": None,
        "payer_reference": None,
        "denial_code": None,
        "paid_cents": None,
        "notes": body.notes,
        "created_at": now,
        "updated_at": now,
    }, ctx, location_id=claim.get("location_id"))
    await db.claim_submissions.insert_one(sub_doc)

    await db.claims.update_one(
        {"id": claim_id, "tenant_id": ctx.tenant_id},
        {"$set": {
            "status": new_status,
            "submitted_at": now,
            "last_submission_at": now,
            "updated_at": now,
            "updated_by": user["id"],
         },
         "$inc": {"submission_count": 1},
         "$push": {"history": _history_entry(
             user, "submitted",
             method=body.method,
             submission_id=sub_id,
             adapter_route=adapter_result.adapter_route,
             adapter_status=adapter_result.status,
             adapter_external_id=adapter_result.external_id,
             trace_id=getattr(adapter_result, "trace_id", None),
             correlation_id=sub_doc["correlation_id"],
             sandbox=sub_doc["sandbox"],
             external_reference=body.external_reference,
             from_status=current, to_status=new_status,
         )}},
    )
    await audit_success(
        user, "billing.claim.submission_created", request,
        entity_type="claim", entity_id=claim_id,
        metadata={"submission_id": sub_id, "method": body.method,
                  "external_reference": body.external_reference,
                  "from": current, "to": new_status,
                  "adapter_route": adapter_result.adapter_route,
                  "adapter_status": adapter_result.status,
                  "adapter_external_id": adapter_result.external_id,
                  "trace_id": sub_doc["trace_id"],
                  "correlation_id": sub_doc["correlation_id"],
                  "sandbox": sub_doc["sandbox"]},
    )
    await emit_claim_event(
        db, ctx,
        claim_id=claim_id,
        event_type=(
            "resubmitted"
            if (claim.get("submission_count") or 0) >= 1 else "submitted"
        ),
        actor_id=user["id"],
        submission_id=sub_id,
        adapter_route=adapter_result.adapter_route,
        payload={"method": body.method,
                 "external_reference": body.external_reference,
                 "adapter_status": adapter_result.status,
                 "adapter_external_id": adapter_result.external_id,
                 "trace_id": sub_doc["trace_id"],
                 "correlation_id": sub_doc["correlation_id"],
                 "sandbox": sub_doc["sandbox"],
                 "payload_size_bytes": len(payload_x12)},
        from_status=current,
        to_status=new_status,
        occurred_at=adapter_result.submitted_at or now,
        location_id=claim.get("location_id"),
    )
    fresh = await db.claim_submissions.find_one(
        {"id": sub_id, "tenant_id": ctx.tenant_id}, {"_id": 0},
    )
    return fresh


@router.get(
    "/claims/{claim_id}/submissions",
    response_model=list[ClaimSubmissionPublic],
)
async def list_claim_submissions(
    claim_id: str,
    user: dict = Depends(require_permission("claim", "read")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    claim = await _scoped_one(db.claims, {"id": claim_id}, ctx)
    if not claim:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Claim not found")
    rows = [s async for s in db.claim_submissions.find(
        {"tenant_id": ctx.tenant_id, "claim_id": claim_id},
        {"_id": 0, "payload_json": 0, "payload_x12": 0},
    ).sort([("submitted_at", -1)])]
    return rows


@router.get("/claims/{claim_id}/submissions/{sub_id}/payload")
async def read_submission_payload(
    claim_id: str,
    sub_id: str,
    user: dict = Depends(require_permission("claim", "read")),
    ctx: TenantContext = Depends(require_tenant),
):
    """Return the JSON + X12 preview. Separated from the list endpoint
    so the default queue/list responses stay light."""
    db = tenant_db(ctx.tenant_id)
    row = await db.claim_submissions.find_one(
        {"id": sub_id, "claim_id": claim_id,
         "tenant_id": ctx.tenant_id}, {"_id": 0},
    )
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Submission not found")
    return {
        "id": row["id"],
        "claim_id": row["claim_id"],
        "payload_json": row.get("payload_json"),
        "payload_x12": row.get("payload_x12"),
        "payload_format": row.get("payload_format"),
        "payload_size_bytes": row.get("payload_size_bytes"),
    }


@router.post(
    "/claims/{claim_id}/submissions/{sub_id}/outcome",
    response_model=ClaimSubmissionPublic,
)
async def record_submission_outcome(
    claim_id: str,
    sub_id: str,
    body: ClaimSubmissionOutcome,
    request: Request,
    user: dict = Depends(require_permission("claim", "correct_resubmit")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    claim = await _scoped_one(db.claims, {"id": claim_id}, ctx)
    if not claim:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Claim not found")
    sub = await db.claim_submissions.find_one(
        {"id": sub_id, "claim_id": claim_id,
         "tenant_id": ctx.tenant_id}, {"_id": 0},
    )
    if not sub:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Submission not found")
    if sub.get("outcome"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Submission already has outcome '{sub['outcome']}'",
        )

    target_status = _OUTCOME_TO_STATUS[body.outcome]
    new_status = transitions.http_advance(
        "claim", claim["status"], target_status,
    )

    now = _now()
    sub_set = {
        "outcome": body.outcome,
        "outcome_at": now,
        "outcome_by": user["id"],
        "payer_reference": body.payer_reference,
        "denial_code": body.denial_code,
        "paid_cents": body.paid_cents,
        "notes": body.notes or sub.get("notes"),
        "updated_at": now,
    }
    await db.claim_submissions.update_one(
        {"id": sub_id, "tenant_id": ctx.tenant_id}, {"$set": sub_set},
    )

    claim_set: dict = {
        "status": new_status,
        "updated_at": now,
        "updated_by": user["id"],
    }
    if body.outcome == "accepted":
        claim_set["accepted_at"] = now
    if body.outcome in ("paid", "partially_paid") and body.paid_cents:
        claim_set["paid_cents"] = body.paid_cents
    if body.outcome in ("denied", "rejected") and body.denial_code:
        claim_set["last_denial_code"] = body.denial_code

    await db.claims.update_one(
        {"id": claim_id, "tenant_id": ctx.tenant_id},
        {"$set": claim_set,
         "$push": {"history": _history_entry(
             user, "outcome_recorded",
             submission_id=sub_id,
             outcome=body.outcome,
             payer_reference=body.payer_reference,
             denial_code=body.denial_code,
             paid_cents=body.paid_cents,
             from_status=claim["status"], to_status=new_status,
         )}},
    )
    await audit_success(
        user, "billing.claim.outcome_recorded", request,
        entity_type="claim", entity_id=claim_id,
        metadata={"submission_id": sub_id, "outcome": body.outcome,
                  "from": claim["status"], "to": new_status},
    )
    await emit_claim_event(
        db, ctx,
        claim_id=claim_id,
        event_type="outcome_recorded",
        actor_id=user["id"],
        submission_id=sub_id,
        adapter_route=sub.get("adapter_route"),
        denial_code=body.denial_code,
        payload={"outcome": body.outcome,
                 "payer_reference": body.payer_reference,
                 "paid_cents": body.paid_cents},
        from_status=claim["status"],
        to_status=new_status,
        location_id=claim.get("location_id"),
    )
    # A denied/rejected outcome also emits a dedicated `denied` event
    # so the Denials Queue timeline can filter on it without joining.
    if body.outcome in ("denied", "rejected"):
        await emit_claim_event(
            db, ctx,
            claim_id=claim_id,
            event_type="denied",
            actor_id=user["id"],
            submission_id=sub_id,
            adapter_route=sub.get("adapter_route"),
            denial_code=body.denial_code,
            payload={"outcome": body.outcome,
                     "payer_reference": body.payer_reference},
            from_status=claim["status"],
            to_status=new_status,
            location_id=claim.get("location_id"),
        )
    fresh = await db.claim_submissions.find_one(
        {"id": sub_id, "tenant_id": ctx.tenant_id},
        {"_id": 0, "payload_json": 0, "payload_x12": 0},
    )
    return fresh


@router.get("/claims/{claim_id}/timeline")
async def read_claim_timeline(
    claim_id: str,
    user: dict = Depends(require_permission("claim", "read")),
    ctx: TenantContext = Depends(require_tenant),
):
    """Return a unified timeline: claim history entries, scrubber runs,
    and submission records — all sorted chronologically with actor +
    timestamp. Heavy payload fields are intentionally omitted."""
    db = tenant_db(ctx.tenant_id)
    # IMPORTANT: fetch the raw doc (including history) — the default
    # `_public()` helper strips history.
    claim = await db.claims.find_one(
        scoped_filter({"id": claim_id}, ctx, location_scoped=False),
        {"_id": 0},
    )
    if not claim:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Claim not found")

    entries: list[dict] = []
    for h in claim.get("history", []) or []:
        entries.append({
            "kind": "history",
            "at": h.get("at"),
            "by": h.get("by"),
            "action": h.get("action"),
            "from_status": h.get("from_status"),
            "to_status": h.get("to_status"),
            "metadata": {k: v for k, v in h.items()
                         if k not in {"at", "by", "action",
                                      "from_status", "to_status"}},
        })

    async for run in db.claim_validation_runs.find(
        {"tenant_id": ctx.tenant_id, "claim_id": claim_id}, {"_id": 0},
    ).sort([("run_at", -1)]):
        entries.append({
            "kind": "validation_run",
            "at": run.get("run_at"),
            "by": run.get("run_by"),
            "action": "scrubber_run",
            "metadata": {
                "error_count": len(run.get("errors", []) or []),
                "warning_count": len(run.get("warnings", []) or []),
                "top_codes": [e.get("code") for e in (run.get("errors") or [])[:3]],
            },
        })

    async for sub in db.claim_submissions.find(
        {"tenant_id": ctx.tenant_id, "claim_id": claim_id},
        {"_id": 0, "payload_json": 0, "payload_x12": 0},
    ).sort([("submitted_at", -1)]):
        entries.append({
            "kind": "submission",
            "at": sub.get("submitted_at"),
            "by": sub.get("submitted_by"),
            "action": "submission_created",
            "metadata": {
                "submission_id": sub.get("id"),
                "method": sub.get("method"),
                "external_reference": sub.get("external_reference"),
                "payload_size_bytes": sub.get("payload_size_bytes"),
            },
        })
        if sub.get("outcome_at"):
            entries.append({
                "kind": "submission_outcome",
                "at": sub.get("outcome_at"),
                "by": sub.get("outcome_by"),
                "action": "outcome_recorded",
                "metadata": {
                    "submission_id": sub.get("id"),
                    "outcome": sub.get("outcome"),
                    "payer_reference": sub.get("payer_reference"),
                    "denial_code": sub.get("denial_code"),
                    "paid_cents": sub.get("paid_cents"),
                },
            })

    entries.sort(key=lambda e: e.get("at") or "", reverse=True)
    return {
        "claim_id": claim_id,
        "current_status": claim["status"],
        "entries": entries,
    }


# ---------------------------------------------------------------------------
# Phase 2a — Claim event stream (read endpoint)
# ---------------------------------------------------------------------------
@router.get(
    "/claims/{claim_id}/events",
    response_model=list[ClaimEventPublic],
)
async def list_claim_events(
    claim_id: str,
    event_type: str | None = Query(default=None,
                                   description="filter to a single event_type"),
    limit: int = Query(default=200, ge=1, le=1000),
    user: dict = Depends(require_permission("claim", "read")),
    ctx: TenantContext = Depends(require_tenant),
):
    """Return the append-only event log for one claim.

    Ordered newest-first. Populated by `services.billing.events.emit_claim_event`
    and read by the ClaimDetail timeline / Claims Queue filters. This
    stream is the canonical place for clearinghouse-specific
    acknowledgments (999 / 277CA) so the main `ClaimStatus` enum stays
    minimal.
    """
    db = tenant_db(ctx.tenant_id)
    claim = await _scoped_one(db.claims, {"id": claim_id}, ctx)
    if not claim:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Claim not found")
    q: dict = {"tenant_id": ctx.tenant_id, "claim_id": claim_id}
    if event_type:
        q["event_type"] = event_type
    cursor = db.claim_events.find(q, {"_id": 0}).sort(
        [("occurred_at", -1), ("created_at", -1)],
    ).limit(limit)
    return [e async for e in cursor]


@router.patch("/claims/{claim_id}/assignment", response_model=ClaimPublic)
async def update_claim_assignment(
    claim_id: str,
    body: ClaimAssignmentUpdate,
    request: Request,
    user: dict = Depends(require_permission("claim", "assign")),
    ctx: TenantContext = Depends(require_tenant),
):
    """Phase 11 — assign or reassign a claim. No-op + 200 when the
    target assignee is already set (retry-safe). Permission enforced
    via `claim.assign` (managers + billing specialists)."""
    db = tenant_db(ctx.tenant_id)
    claim = await _scoped_one(db.claims, {"id": claim_id}, ctx)
    if not claim:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Claim not found")

    # Idempotent: same assignee → audit as "unchanged" and return.
    if (claim.get("assigned_to") or None) == (body.assigned_to or None):
        return _public(claim)

    # Verify the assignee exists on this tenant (optional hygiene).
    if body.assigned_to:
        assignee = await db.users.find_one(
            {"id": body.assigned_to, "tenant_id": ctx.tenant_id},
            {"_id": 0, "id": 1},
        )
        if not assignee:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Assignee not found on this tenant",
            )

    now = _now()
    await db.claims.update_one(
        {"id": claim_id, "tenant_id": ctx.tenant_id},
        {"$set": {"assigned_to": body.assigned_to,
                  "updated_at": now, "updated_by": user["id"]},
         "$push": {"history": _history_entry(
             user, "assignment_changed",
             from_assignee=claim.get("assigned_to"),
             to_assignee=body.assigned_to,
         )}},
    )
    await audit_success(
        user, "billing.claim.assignment_changed", request,
        entity_type="claim", entity_id=claim_id,
        metadata={"from": claim.get("assigned_to"),
                  "to": body.assigned_to,
                  "action": "unassigned" if body.assigned_to is None
                            else "assigned"},
    )
    fresh = await db.claims.find_one(
        {"id": claim_id, "tenant_id": ctx.tenant_id}, {"_id": 0},
    )
    return _public(fresh)


@router.post("/claims/{claim_id}/assign", response_model=ClaimPublic)
async def assign_claim_convenience(
    claim_id: str,
    body: ClaimAssignmentUpdate,
    request: Request,
    user: dict = Depends(require_permission("claim", "assign")),
    ctx: TenantContext = Depends(require_tenant),
):
    """Phase 11 — POST alias for the PATCH assignment endpoint. Keeps
    clients that prefer strictly-verb-based APIs happy and gives us a
    natural entry point for a future "self-assign" button that hits
    `POST /claims/{id}/assign` with `{assigned_to: current_user_id}`."""
    return await update_claim_assignment(claim_id, body, request, user, ctx)


@router.post("/claims/{claim_id}/unassign", response_model=ClaimPublic)
async def unassign_claim_convenience(
    claim_id: str,
    request: Request,
    user: dict = Depends(require_permission("claim", "assign")),
    ctx: TenantContext = Depends(require_tenant),
):
    """Phase 11 — drop the assignee from a claim. Uses the same audit
    event as `update_claim_assignment` so the timeline shows a clean
    "unassigned" action without a fake intermediate state."""
    return await update_claim_assignment(
        claim_id,
        ClaimAssignmentUpdate(assigned_to=None),
        request, user, ctx,
    )


# ---------------------------------------------------------------------------
# Phase-UI — Paginated, enriched claim queue endpoint.
#
# Companion to the legacy `/claims/queues/{queue_name}` endpoint (kept
# unchanged for backward compat). This new endpoint returns a rich
# shape optimised for the Claims Queue page:
#   * server-side pagination + sorting
#   * human-friendly row enrichment (patient_name / payer_name /
#     assignee_name) done in a single batched lookup
#   * summary cards (shown / ready / needs_fixes / billed_total)
#   * tab counts across every tab in one call so the UI doesn't flicker
#     when the user switches tabs
#   * filter options (payers in tenant, assignees seen on current
#     queue rows)
# ---------------------------------------------------------------------------
_TAB_KEYS = ("all", "pending-submission", "needs-fixes", "rejected",
             "follow-up")
_SORTABLE = {
    "updated_at", "created_at", "service_date_from", "service_date_to",
    "billed_cents", "status", "last_submission_at",
}


async def _tab_base_query(tab: str, db, tenant_id: str) -> dict | None:
    """Return the Mongo query for a given tab, or None if unknown.

    Returning `{"__noop__": True}` short-circuits to an empty result —
    used by `follow-up` when there are no stale claim IDs.
    """
    q: dict = {"tenant_id": tenant_id}
    t = (tab or "all").replace("_", "-").lower()
    if t == "all":
        return q
    if t == "pending-submission":
        q["status"] = {"$in": ["ready", "validation_failed"]}
        return q
    if t == "needs-fixes":
        q["status"] = "validation_failed"
        return q
    if t == "rejected":
        q["status"] = {"$in": ["rejected", "denied"]}
        return q
    if t == "follow-up":
        # Canonical `follow_up` has four raw sources:
        #   1. Partial payment still needs balance / secondary billing
        #   2. Appeal filed, waiting on payer response
        #   3. Stale submitted / rejected / denied (aging out the
        #      follow-up threshold via `followup_claim_ids`)
        #   4. Phase 10 — manually or auto-flagged via `followup_flag`
        stale_ids = await followup_claim_ids(db, tenant_id)
        branches: list[dict] = [
            {"status": {"$in": ["partially_paid", "appealed"]}},
            {"followup_flag": True},
        ]
        if stale_ids:
            branches.append({"id": {"$in": stale_ids}})
        q["$or"] = branches
        return q
    return None


@router.get("/claims/assignable-users")
async def list_assignable_users(
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(require_tenant),
):
    """Tenant-scoped list of billable users (admin / doctor / staff) for
    the ClaimWorkflow AssignmentRow dropdown. Returns readable display
    names, never raw IDs. Disabled users are filtered out."""
    db = tenant_db(ctx.tenant_id)
    q: dict = {
        "role": {"$in": ["admin", "doctor", "staff", "billing_specialist"]},
        "status": {"$ne": "disabled"},
        "tenant_id": ctx.tenant_id,
    }
    out = []
    async for u in db.users.find(
        q, {"_id": 0, "id": 1, "email": 1, "role": 1,
            "name": 1, "display_name": 1,
            "first_name": 1, "last_name": 1},
    ).sort([("first_name", 1), ("last_name", 1)]):
        full = (
            u.get("display_name")
            or f"{u.get('first_name','')} {u.get('last_name','')}".strip()
            or u.get("name")
            or u.get("email")
        )
        out.append({
            "id": u["id"],
            "name": full,
            "role": u.get("role"),
            "email": u.get("email"),
        })
    return out


@router.get("/claims/queue")
async def read_claims_queue(
    tab: str = Query(default="all"),
    page: int = Query(default=1, ge=1, le=10_000),
    page_size: int = Query(default=25, ge=1, le=200),
    sort: str = Query(default="updated_at:desc",
                      description="field:asc|desc"),
    status_in: str | None = Query(default=None),
    canonical_status_in: str | None = Query(
        default=None,
        description="comma-separated canonical buckets "
                    "(draft/ready/submitted/accepted/needs_fixes/"
                    "denied/paid/follow_up)",
    ),
    payer_id: str | None = None,
    assigned_to: str | None = None,
    unassigned: bool = Query(
        default=False,
        description="Phase 11 — when true, filter rows to those with "
                    "no assignee (overrides assigned_to).",
    ),
    age_days: int | None = Query(default=None, ge=0, le=365),
    include_tab_counts: bool = Query(default=True),
    include_filter_options: bool = Query(default=True),
    user: dict = Depends(require_permission("claim", "read")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)

    # Resolve the stale-claim id set once — used both by the
    # follow-up tab query AND by row-level canonical_status tagging.
    stale_ids: list[str] = await followup_claim_ids(db, ctx.tenant_id)
    stale_set = set(stale_ids)

    # Base query for the selected tab.
    q = await _tab_base_query(tab, db, ctx.tenant_id)
    if q is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown tab '{tab}'")
    noop = bool(q.get("__noop__"))

    # Canonical → raw expansion. When the caller asks for canonical
    # `follow_up` specifically we also need the stale-id branch.
    canonical_filter = [
        s.strip() for s in (canonical_status_in or "").split(",") if s.strip()
    ]

    # Overlay filters on top of the tab's own status scope.
    def _apply_filters(qbase: dict) -> dict:
        if qbase.get("__noop__"):
            return qbase
        qf = dict(qbase)
        if payer_id:
            qf["payer_id"] = payer_id
        if unassigned:
            # Phase 11 — match rows where `assigned_to` is absent OR null.
            # Legacy rows may not carry the field at all, hence $exists.
            qf["$and"] = qf.get("$and", []) + [
                {"$or": [{"assigned_to": None},
                         {"assigned_to": {"$exists": False}}]},
            ]
        elif assigned_to:
            qf["assigned_to"] = assigned_to
        if age_days is not None:
            qf["created_at"] = {"$lt": followup_threshold_iso(age_days)}
        if status_in:
            wanted = [s.strip() for s in status_in.split(",") if s.strip()]
            if wanted:
                base = qf.get("status")
                if isinstance(base, dict) and "$in" in base:
                    allowed = [s for s in wanted if s in base["$in"]]
                elif isinstance(base, str):
                    allowed = [s for s in wanted if s == base]
                else:
                    allowed = wanted
                qf["status"] = {"$in": allowed} if allowed else {"$in": ["__none__"]}
        # Phase 3 — canonical status filter. Expand to raw statuses
        # and, when `follow_up` is in the set, also include the stale
        # claim ids so aging claims surface even when their raw
        # status is still `submitted`.
        if canonical_filter:
            raw = raw_statuses_for_canonical(canonical_filter)
            branches: list[dict] = []
            if raw:
                branches.append({"status": {"$in": raw}})
            if "follow_up" in canonical_filter and stale_ids:
                branches.append({"id": {"$in": stale_ids}})
            if branches:
                # AND the canonical scope with the tab / filter scope
                # so tab-level restrictions still apply.
                existing_or = qf.pop("$or", None)
                clause = {"$or": branches} if len(branches) > 1 else branches[0]
                and_parts: list[dict] = []
                if existing_or is not None:
                    and_parts.append({"$or": existing_or})
                and_parts.append(clause)
                if and_parts:
                    qf["$and"] = and_parts
            else:
                qf["status"] = {"$in": ["__none__"]}
        return qf

    filtered_q = _apply_filters(q)

    # Pagination + sort.
    sort_field, _, sort_dir = (sort or "").partition(":")
    if sort_field not in _SORTABLE:
        sort_field = "updated_at"
    direction = -1 if (sort_dir or "desc").lower() != "asc" else 1

    total = 0
    rows: list[dict] = []
    if not noop:
        total = await db.claims.count_documents(filtered_q)
        cursor = db.claims.find(filtered_q, {"_id": 0}).sort(
            [(sort_field, direction), ("id", 1)],
        ).skip((page - 1) * page_size).limit(page_size)
        rows = [c async for c in cursor]

    # Per-query enrichment — one batched lookup each for patient /
    # payer / user / latest event.
    if rows:
        pt_ids = sorted({r["patient_id"] for r in rows if r.get("patient_id")})
        py_ids = sorted({r["payer_id"] for r in rows if r.get("payer_id")})
        as_ids = sorted({r["assigned_to"] for r in rows if r.get("assigned_to")})
        pt_map: dict[str, dict] = {}
        py_map: dict[str, dict] = {}
        as_map: dict[str, dict] = {}
        if pt_ids:
            async for p in db.patients.find(
                {"tenant_id": ctx.tenant_id, "id": {"$in": pt_ids}},
                {"_id": 0, "id": 1, "first_name": 1, "last_name": 1,
                 "mrn": 1},
            ):
                pt_map[p["id"]] = p
        if py_ids:
            async for p in db.billing_payers.find(
                {"tenant_id": ctx.tenant_id, "id": {"$in": py_ids}},
                {"_id": 0, "id": 1, "name": 1, "payer_type": 1},
            ):
                py_map[p["id"]] = p
        if as_ids:
            async for u in db.users.find(
                {"tenant_id": ctx.tenant_id, "id": {"$in": as_ids}},
                {"_id": 0, "id": 1, "first_name": 1, "last_name": 1,
                 "email": 1},
            ):
                as_map[u["id"]] = u
        # Newest event per claim on this page.
        latest: dict[str, dict] = {}
        claim_ids = [r["id"] for r in rows]
        async for ev in db.claim_events.find(
            {"tenant_id": ctx.tenant_id, "claim_id": {"$in": claim_ids}},
            {"_id": 0, "claim_id": 1, "event_type": 1, "occurred_at": 1},
        ).sort([("occurred_at", -1)]):
            cid = ev["claim_id"]
            if cid not in latest:
                latest[cid] = ev

        for r in rows:
            p = pt_map.get(r.get("patient_id") or "")
            if p:
                first = p.get("first_name") or ""
                last = p.get("last_name") or ""
                r["patient_name"] = (f"{first} {last}").strip() or None
                r["patient_mrn"] = p.get("mrn")
            py = py_map.get(r.get("payer_id") or "")
            if py:
                r["payer_name"] = py.get("name")
                r["payer_type"] = py.get("payer_type")
            u = as_map.get(r.get("assigned_to") or "")
            if u:
                first = u.get("first_name") or ""
                last = u.get("last_name") or ""
                name = (f"{first} {last}").strip()
                r["assignee_name"] = name or u.get("email")
            ev = latest.get(r["id"])
            r["last_event"] = ev.get("event_type") if ev else None
            r["last_event_at"] = ev.get("occurred_at") if ev else None
            # Phase 3 — canonical lifecycle tag.
            r["canonical_status"] = canonical_status(
                r, is_stale=r["id"] in stale_set,
            )
            r["canonical_status_label"] = CANONICAL_LABELS.get(
                r["canonical_status"], r["canonical_status"],
            )
            # Phase 10 — aging + operational follow-up enrichment.
            # Aging is computed against the most meaningful anchor
            # for the claim's stage: submitted claims age from the
            # last submission; pre-submit claims age from creation.
            basis_field = None
            basis_ts = None
            if r.get("last_submission_at"):
                basis_field, basis_ts = "last_submission_at", r["last_submission_at"]
            elif r.get("submitted_at"):
                basis_field, basis_ts = "submitted_at", r["submitted_at"]
            elif r.get("updated_at"):
                basis_field, basis_ts = "updated_at", r["updated_at"]
            elif r.get("created_at"):
                basis_field, basis_ts = "created_at", r["created_at"]
            r["aging_basis"] = basis_field
            r["aging_basis_at"] = basis_ts
            r["aging_days"] = _days_since_iso(basis_ts) if basis_ts else None
            # Phase 11 — defensive null handling: legacy rows may be
            # missing the assignment / follow-up fields entirely. We
            # set safe defaults on every row so the UI never has to
            # branch on `undefined`.
            r["assigned_to"] = r.get("assigned_to") or None
            r["followup_flag"] = bool(r.get("followup_flag") or False)
            r["followup_reason"] = r.get("followup_reason")
            r["next_action_at"] = r.get("next_action_at")
            # "Last activity" is whichever of `last_event_at` or
            # `updated_at` is most recent — the UI renders this
            # alongside the assignee.
            last_activity = r.get("last_event_at") or r.get("updated_at")
            r["last_activity_at"] = last_activity

    # Summary cards — aggregate over the current filtered query (NOT
    # limited by page). `shown` reflects the current page.
    summary = {
        "shown": len(rows),
        "total": total,
        "ready": 0,
        "needs_fixes": 0,
        "billed_total_cents": 0,
    }
    if not noop:
        async for row in db.claims.aggregate([
            {"$match": filtered_q},
            {"$group": {
                "_id": None,
                "billed_total": {"$sum": "$billed_cents"},
                "ready": {"$sum": {"$cond": [
                    {"$eq": ["$status", "ready"]}, 1, 0,
                ]}},
                "needs_fixes": {"$sum": {"$cond": [
                    {"$or": [
                        {"$eq": ["$status", "validation_failed"]},
                        {"$gt": [
                            {"$ifNull": ["$validation_error_count", 0]}, 0,
                        ]},
                    ]}, 1, 0,
                ]}},
            }},
        ]):
            summary["billed_total_cents"] = int(row.get("billed_total") or 0)
            summary["ready"] = int(row.get("ready") or 0)
            summary["needs_fixes"] = int(row.get("needs_fixes") or 0)
            break

    # Tab counts + per-tab billed totals — aggregated in one pass per
    # tab so the UI can show filter-aware financials next to each tab
    # label (Phase 12 handoff requirement: "counts and billed totals
    # are real and filter-aware").
    tab_counts: dict[str, int] = {}
    billed_totals: dict[str, int] = {}
    if include_tab_counts:
        for t in _TAB_KEYS:
            tb = await _tab_base_query(t, db, ctx.tenant_id)
            if tb is None or tb.get("__noop__"):
                tab_counts[t] = 0
                billed_totals[t] = 0
                continue
            tab_q = _apply_filters(tb)
            tab_counts[t] = 0
            billed_totals[t] = 0
            async for row in db.claims.aggregate([
                {"$match": tab_q},
                {"$group": {
                    "_id": None,
                    "count": {"$sum": 1},
                    "billed": {"$sum": "$billed_cents"},
                }},
            ]):
                tab_counts[t] = int(row.get("count") or 0)
                billed_totals[t] = int(row.get("billed") or 0)
                break

    # Filter options — payers in tenant + assignees ever seen.
    filter_options: dict = {}
    if include_filter_options:
        payers: list[dict] = []
        async for p in db.billing_payers.find(
            {"tenant_id": ctx.tenant_id, "status": {"$ne": "inactive"}},
            {"_id": 0, "id": 1, "name": 1},
        ).sort([("name", 1)]):
            payers.append(p)
        assignees: list[dict] = []
        assignee_ids = await db.claims.distinct(
            "assigned_to",
            {"tenant_id": ctx.tenant_id,
             "assigned_to": {"$nin": [None, ""]}},
        )
        if assignee_ids:
            async for u in db.users.find(
                {"tenant_id": ctx.tenant_id, "id": {"$in": assignee_ids}},
                {"_id": 0, "id": 1, "first_name": 1, "last_name": 1,
                 "email": 1},
            ):
                name = f"{u.get('first_name','')} {u.get('last_name','')}".strip()
                assignees.append({"id": u["id"],
                                  "name": name or u.get("email") or u["id"]})
        filter_options = {
            "payers": payers,
            "assignees": assignees,
            "statuses": list(get_args(ClaimStatus)),
            # Phase 3 — canonical buckets with display labels for UI.
            "canonical_statuses": [
                {"value": c, "label": CANONICAL_LABELS[c]}
                for c in CANONICAL_STATUSES
            ],
        }

    return {
        "tab": tab,
        "page": page,
        "page_size": page_size,
        "total": total,
        "sort": f"{sort_field}:{'asc' if direction == 1 else 'desc'}",
        "rows": rows,
        "summary": summary,
        "tab_counts": tab_counts,
        "billed_totals": billed_totals,
        "filter_options": filter_options,
    }



@router.get("/claims/queues/{queue_name}", response_model=list[ClaimPublic])
async def read_claim_queue(
    queue_name: str,
    payer_id: str | None = None,
    age_days: int | None = Query(default=None, ge=0, le=365),
    assigned_to: str | None = None,
    status_in: str | None = Query(default=None, description="comma-separated statuses"),
    limit: int = Query(default=100, ge=1, le=500),
    user: dict = Depends(require_permission("claim", "read")),
    ctx: TenantContext = Depends(require_tenant),
):
    """Named work queues:
      * `pending-submission` — statuses ready, validation_failed
      * `needs-fixes`        — status validation_failed (canonical
                               scrubber-blocked claims). Narrower
                               companion to `pending-submission`.
      * `rejected` — statuses rejected, denied
      * `follow-up` — stale submitted/rejected/denied claims per the
        follow-up rule in submission.py

    Each returned row is enriched with `last_event` / `last_event_at`
    sourced from the `claim_events` stream so the UI can surface
    recent activity (validated / submitted / era_posted / ...) without
    a second round-trip.
    """
    db = tenant_db(ctx.tenant_id)
    q: dict = {"tenant_id": ctx.tenant_id}

    qname = queue_name.replace("_", "-").lower()
    if qname == "pending-submission":
        q["status"] = {"$in": ["ready", "validation_failed"]}
    elif qname == "needs-fixes":
        # Canonical definition: the scrubber found blocking errors and
        # the claim has not yet been corrected. `validation_failed`
        # is the only status that unambiguously represents that.
        q["status"] = "validation_failed"
    elif qname == "rejected":
        q["status"] = {"$in": ["rejected", "denied"]}
    elif qname == "follow-up":
        # Phase 3 canonical: follow_up = stale ∪ partially_paid ∪ appealed.
        ids = await followup_claim_ids(db, ctx.tenant_id)
        branches: list[dict] = [
            {"status": {"$in": ["partially_paid", "appealed"]}},
        ]
        if ids:
            branches.append({"id": {"$in": ids}})
        q["$or"] = branches
    else:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            f"Unknown queue '{queue_name}'")

    if payer_id:
        q["payer_id"] = payer_id
    if assigned_to:
        q["assigned_to"] = assigned_to
    if status_in:
        wanted = [s.strip() for s in status_in.split(",") if s.strip()]
        if wanted:
            # Intersect with any queue-provided status filter.
            base = q.get("status", {}).get("$in") if isinstance(q.get("status"), dict) else None
            if isinstance(q.get("status"), str):
                base = [q["status"]]
            q["status"] = {"$in": [s for s in wanted if (base is None or s in base)]}
    if age_days is not None:
        q["created_at"] = {"$lt": followup_threshold_iso(age_days)}

    cursor = db.claims.find(q, {"_id": 0}).sort(
        [("updated_at", -1)]
    ).limit(limit)
    rows = [c async for c in cursor]

    # Phase 2b — enrich each row with the latest claim_events entry.
    # Single round-trip: pull newest events for the returned claim
    # ids, keep the first one per claim (cursor sorted DESC).
    if rows:
        claim_ids = [r["id"] for r in rows]
        latest: dict[str, dict] = {}
        async for ev in db.claim_events.find(
            {"tenant_id": ctx.tenant_id, "claim_id": {"$in": claim_ids}},
            {"_id": 0, "claim_id": 1, "event_type": 1, "occurred_at": 1},
        ).sort([("occurred_at", -1)]):
            cid = ev["claim_id"]
            if cid not in latest:
                latest[cid] = ev
        for r in rows:
            ev = latest.get(r["id"])
            r["last_event"] = ev.get("event_type") if ev else None
            r["last_event_at"] = ev.get("occurred_at") if ev else None

    return rows



# ---------------------------------------------------------------------------
# Phase 5 — Remittances posting, AR aging, statements, denial mgmt
# ---------------------------------------------------------------------------
@router.post("/remittances", response_model=RemittancePublic, status_code=201)
async def create_and_post_remittance(
    body: RemittancePostRequest,
    request: Request,
    user: dict = Depends(require_permission("remit", "post")),
    ctx: TenantContext = Depends(require_tenant),
):
    """Create a remittance header and post it in one atomic call.

    Creates: remittance header + remittance_claims + remittance_lines
    + payment (era_posting) + allocations + contractual adjustments
    + denial_work_items. Invoice balances are refreshed via the
    standard `_recompute_invoice_balance` helper — no hidden mutations.
    """
    db = tenant_db(ctx.tenant_id)
    try:
        result = await post_remittance(
            db, ctx, user, body,
            recompute_invoice_balance=_recompute_invoice_balance,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    await audit_success(
        user, "billing.remittance.posted", request,
        entity_type="remittance", entity_id=result["remittance_id"],
        metadata={
            "payer_id": body.payer_id,
            "total_paid_cents": body.total_paid_cents,
            "claim_count": len(body.claims),
            "denial_count": result["denial_count"],
            "adjustment_count": result["adjustment_count"],
            "payment_id": result["payment_id"],
        },
    )
    # Phase 2a — one `era_posted` event per claim covered by this
    # remittance. Adapters that poll ERAs upstream will fill in the
    # `adapter_route` field once Phase 2c lands; for manually-posted
    # remittances the field remains null.
    for c in body.claims:
        await emit_claim_event(
            db, ctx,
            claim_id=c.claim_id,
            event_type="era_posted",
            actor_id=user["id"],
            remittance_id=result["remittance_id"],
            denial_code=c.denial_code,
            payload={"billed_cents": int(c.billed_cents),
                     "paid_cents": int(c.paid_cents),
                     "contractual_cents": int(c.contractual_cents),
                     "patient_resp_cents": int(c.patient_resp_cents),
                     "denied_cents": int(c.denied_cents)},
        )
    remit = await db.remittances.find_one(
        {"id": result["remittance_id"], "tenant_id": ctx.tenant_id},
        {"_id": 0},
    )
    return _public(remit)


@router.get("/remittances/{remit_id}")
async def read_remittance_detail(
    remit_id: str,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    remit = await db.remittances.find_one(
        {"id": remit_id, "tenant_id": ctx.tenant_id}, {"_id": 0},
    )
    if not remit:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Remittance not found")
    claims = [c async for c in db.remittance_claims.find(
        {"tenant_id": ctx.tenant_id, "remittance_id": remit_id}, {"_id": 0},
    ).sort([("created_at", 1)])]
    claim_ids = [c["id"] for c in claims]
    lines = [ln async for ln in db.remittance_lines.find(
        {"tenant_id": ctx.tenant_id,
         "remittance_claim_id": {"$in": claim_ids}}, {"_id": 0},
    ).sort([("created_at", 1)])]
    return {"remittance": _public(remit),
            "claims": claims, "lines": lines}


# ---------------------------------------------------------------------------
# Denial work items — mutations
# ---------------------------------------------------------------------------
@router.patch(
    "/denial-work-items/{item_id}",
    response_model=DenialWorkItemPublic,
)
async def update_denial_work_item(
    item_id: str,
    body: DenialWorkItemUpdate,
    request: Request,
    user: dict = Depends(require_permission("denial", "work")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    item = await db.denial_work_items.find_one(
        {"id": item_id, "tenant_id": ctx.tenant_id}, {"_id": 0},
    )
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Denial work item not found")

    set_fields: dict = {"updated_at": _now(), "updated_by": user["id"]}
    current_status = item.get("status", "open")

    if body.status and body.status != current_status:
        new_status = transitions.http_advance(
            "denial", current_status, body.status,
        )
        set_fields["status"] = new_status
        if new_status in ("resolved", "closed"):
            set_fields["closed_at"] = _now()
    if body.assigned_to_id is not None:
        if body.assigned_to_id:
            assignee = await db.users.find_one(
                {"id": body.assigned_to_id, "tenant_id": ctx.tenant_id},
                {"_id": 0, "id": 1},
            )
            if not assignee:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "Assignee not found on this tenant",
                )
        set_fields["assigned_to_id"] = body.assigned_to_id
    if body.resolution_notes is not None:
        set_fields["resolution_notes"] = body.resolution_notes
    if body.denial_category is not None:
        if body.denial_category not in DENIAL_CATEGORIES:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Unknown denial_category '{body.denial_category}'. "
                f"Allowed: {DENIAL_CATEGORIES}",
            )
        set_fields["denial_category"] = body.denial_category

    await db.denial_work_items.update_one(
        {"id": item_id, "tenant_id": ctx.tenant_id},
        {"$set": set_fields,
         "$push": {"history": {
             "at": _now(), "by": user["id"], "action": "updated",
             "from_status": current_status,
             "to_status": set_fields.get("status", current_status),
             "assigned_to_id": set_fields.get("assigned_to_id"),
         }}},
    )
    await audit_success(
        user, "billing.denial.updated", request,
        entity_type="denial_work_item", entity_id=item_id,
        metadata={"status_from": current_status,
                  "status_to": set_fields.get("status", current_status),
                  "assigned_to": set_fields.get("assigned_to_id")},
    )
    fresh = await db.denial_work_items.find_one(
        {"id": item_id, "tenant_id": ctx.tenant_id}, {"_id": 0},
    )
    return _public(fresh)


# ---------------------------------------------------------------------------
# AR aging
# ---------------------------------------------------------------------------
@router.get("/ar/aging")
async def read_ar_aging(
    payer_id: str | None = None,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(require_tenant),
):
    """Return aging buckets for open-balance invoices.

    If `payer_id` is supplied, only invoices tied to that payer are
    included.
    """
    db = tenant_db(ctx.tenant_id)
    q: dict = {"tenant_id": ctx.tenant_id, "balance_cents": {"$gt": 0}}
    if payer_id:
        q["payer_id"] = payer_id
    invoices = [i async for i in db.invoices.find(q, {"_id": 0})]
    result = compute_ar_buckets(invoices)
    return result


@router.get("/ar/aging/by-payer")
async def read_ar_aging_by_payer(
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(require_tenant),
):
    """Roll up aging grouped by payer (self-pay shown as `Self-pay`)."""
    db = tenant_db(ctx.tenant_id)
    all_invoices = [i async for i in db.invoices.find(
        {"tenant_id": ctx.tenant_id, "balance_cents": {"$gt": 0}},
        {"_id": 0},
    )]
    by_payer: dict = {}
    for inv in all_invoices:
        by_payer.setdefault(inv.get("payer_id"), []).append(inv)
    out: list[dict] = []
    for pid, invs in by_payer.items():
        payer_name = None
        if pid:
            p = await db.billing_payers.find_one(
                {"id": pid, "tenant_id": ctx.tenant_id},
                {"_id": 0, "name": 1},
            )
            payer_name = p and p.get("name")
        out.append({
            "payer_id": pid,
            "payer_name": payer_name or ("Self-pay" if pid is None else pid[:8]),
            **compute_ar_buckets(invs),
        })
    out.sort(key=lambda r: r["total_balance_cents"], reverse=True)
    return {"rows": out}


# ---------------------------------------------------------------------------
# Statements — scaffolding (no PDF / no email yet)
# ---------------------------------------------------------------------------
async def _build_statement_for_patient(
    db, ctx: TenantContext, user: dict, patient: dict, request: Request,
) -> dict:
    """Generate, persist, and audit a statement for a single patient.

    Creates a statement row even if the patient has no open invoices
    (zero-balance statement), matching the legacy per-patient POST
    behaviour.
    """
    patient_id = patient["id"]
    open_invoices = [i async for i in db.invoices.find(
        {"tenant_id": ctx.tenant_id, "patient_id": patient_id,
         "balance_cents": {"$gt": 0}},
        {"_id": 0},
    ).sort([("issued_at", 1)])]

    enriched_invoices: list[dict] = []
    for inv in open_invoices:
        ins_paid = 0
        pt_paid = 0
        async for alloc in db.payment_allocations.find(
            {"tenant_id": ctx.tenant_id, "invoice_id": inv["id"]},
            {"_id": 0, "payment_id": 1, "amount_cents": 1},
        ):
            pmt = await db.payments.find_one(
                {"id": alloc["payment_id"], "tenant_id": ctx.tenant_id},
                {"_id": 0, "status": 1, "payer_id": 1},
            )
            if not pmt or pmt.get("status") in ("void", "failed"):
                continue
            amt = int(alloc.get("amount_cents") or 0)
            if pmt.get("payer_id"):
                ins_paid += amt
            else:
                pt_paid += amt
        adjustments = 0
        async for adj in db.adjustments.find(
            {"tenant_id": ctx.tenant_id, "invoice_id": inv["id"]},
            {"_id": 0, "amount_cents": 1},
        ):
            adjustments += int(adj.get("amount_cents") or 0)
        enriched_invoices.append({
            **inv,
            "billed_cents": int(inv.get("total_cents") or 0),
            "insurance_paid_cents": ins_paid,
            "patient_paid_cents": pt_paid,
            "adjustments_cents": adjustments,
        })

    now = _now()
    body_text = render_statement_body(
        patient=patient, invoices=enriched_invoices, as_of_iso=now,
    )
    total = sum(int(i.get("balance_cents") or 0) for i in enriched_invoices)

    stmt_id = str(uuid.uuid4())
    doc = stamp_for_write({
        "id": stmt_id,
        "patient_id": patient_id,
        "generated_at": now,
        "generated_by": user["id"],
        "as_of_date": now[:10],
        "total_balance_cents": total,
        "invoice_count": len(enriched_invoices),
        "invoice_ids": [i["id"] for i in enriched_invoices],
        "invoice_breakdown": [
            {
                "invoice_id": i["id"],
                "issued_at": i.get("issued_at"),
                "billed_cents": i["billed_cents"],
                "insurance_paid_cents": i["insurance_paid_cents"],
                "patient_paid_cents": i["patient_paid_cents"],
                "adjustments_cents": i["adjustments_cents"],
                "balance_cents": int(i.get("balance_cents") or 0),
            }
            for i in enriched_invoices
        ],
        "body": body_text,
        "sent_at": None,
        "sent_via": None,
        "sent_to": None,
        "created_at": now,
        "updated_at": now,
    }, ctx, location_id=None)
    await db.statements.insert_one(doc)
    await audit_success(
        user, "billing.statement.generated", request,
        entity_type="statement", entity_id=stmt_id,
        metadata={"patient_id": patient_id,
                  "total_balance_cents": total,
                  "invoice_count": len(open_invoices)},
    )
    fresh = await db.statements.find_one(
        {"id": stmt_id, "tenant_id": ctx.tenant_id}, {"_id": 0},
    )
    return fresh


@router.post(
    "/patients/{patient_id}/statements",
    response_model=StatementPublic, status_code=201,
)
async def create_statement(
    patient_id: str,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    patient = await db.patients.find_one(
        {"id": patient_id, "tenant_id": ctx.tenant_id},
        {"_id": 0, "id": 1, "first_name": 1, "last_name": 1,
         "email": 1, "phone": 1},
    )
    if not patient:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    fresh = await _build_statement_for_patient(db, ctx, user, patient, request)
    return _public(fresh)


@router.get(
    "/patients/{patient_id}/statements",
    response_model=list[StatementPublic],
)
async def list_statements(
    patient_id: str,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    rows = [_public(s) async for s in db.statements.find(
        {"tenant_id": ctx.tenant_id, "patient_id": patient_id}, {"_id": 0},
    ).sort([("generated_at", -1)])]
    return rows


@router.get(
    "/patients/{patient_id}/statements/{stmt_id}",
    response_model=StatementPublic,
)
async def read_statement(
    patient_id: str,
    stmt_id: str,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    row = await db.statements.find_one(
        {"id": stmt_id, "patient_id": patient_id,
         "tenant_id": ctx.tenant_id}, {"_id": 0},
    )
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Statement not found")
    return _public(row)


async def _load_statement_and_patient(db, ctx, patient_id, stmt_id):
    row = await db.statements.find_one(
        {"id": stmt_id, "patient_id": patient_id,
         "tenant_id": ctx.tenant_id}, {"_id": 0},
    )
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Statement not found")
    patient = await db.patients.find_one(
        {"id": patient_id, "tenant_id": ctx.tenant_id},
        {"_id": 0, "id": 1, "first_name": 1, "last_name": 1,
         "email": 1, "phone": 1},
    )
    if not patient:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    return row, patient


# ---------------------------------------------------------------------------
# Patient self-service — /api/billing/me/statements
# Accessible to any authenticated user; rows are filtered to the caller's
# own patient record.
# ---------------------------------------------------------------------------

async def _resolve_my_patient(user: dict, db_patients) -> dict | None:
    """Find the patient record owned by the calling user.

    Matches on:
      1. `patients.user_id == user.id`  (preferred link)
      2. `patients.email == user.email` (fallback for legacy data)
    """
    q: dict = {"tenant_id": user.get("tenant_id"),
               "$or": [{"user_id": user["id"]}]}
    if user.get("email"):
        q["$or"].append({"email": user["email"]})
    return await db_patients.find_one(q, {"_id": 0})


@router.get("/me/statements", response_model=list[StatementPublic])
async def my_statements(
    user: dict = Depends(get_current_user),
):
    db = tenant_db(user.get("tenant_id"))
    me = await _resolve_my_patient(user, db.patients)
    if not me:
        return []
    rows = [_public(s) async for s in db.statements.find(
        {"tenant_id": user.get("tenant_id"), "patient_id": me["id"]},
        {"_id": 0},
    ).sort([("generated_at", -1)])]
    return rows


@router.get("/me/statements/{stmt_id}.pdf", response_class=Response)
async def my_statement_pdf(
    stmt_id: str,
    user: dict = Depends(get_current_user),
):
    db = tenant_db(user.get("tenant_id"))
    me = await _resolve_my_patient(user, db.patients)
    if not me:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Statement not found")
    stmt = await db.statements.find_one(
        {"id": stmt_id, "patient_id": me["id"],
         "tenant_id": user.get("tenant_id")}, {"_id": 0},
    )
    if not stmt:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Statement not found")
    pdf = render_statement_pdf(patient=me, statement=stmt)
    filename = f"statement-{stmt_id[:8]}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename=\"{filename}\""},
    )



@router.get("/denial-work-items/category-summary")
async def read_denial_category_summary(
    include_closed: bool = False,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(require_tenant),
):
    """Group denial work items by `denial_category` with counts and
    amount totals. Open + in_progress + escalated are counted by
    default; pass `include_closed=true` to include the full set.
    """
    db = tenant_db(ctx.tenant_id)
    match: dict = {"tenant_id": ctx.tenant_id}
    if not include_closed:
        match["status"] = {"$in": ["open", "in_progress", "escalated"]}

    pipeline = [
        {"$match": match},
        {"$group": {
            "_id": {"$ifNull": ["$denial_category", "other"]},
            "count": {"$sum": 1},
            "amount_cents": {"$sum": "$amount_cents"},
        }},
    ]
    by_cat: dict[str, dict] = {}
    async for row in db.denial_work_items.aggregate(pipeline):
        cat = row["_id"] or "other"
        by_cat[cat] = {
            "category": cat,
            "label": DENIAL_CATEGORY_LABELS.get(cat, cat),
            "count": int(row["count"]),
            "amount_cents": int(row["amount_cents"] or 0),
        }

    # Emit every known category (zeroed if absent) for stable UI.
    rows: list[dict] = []
    for cat in DENIAL_CATEGORIES:
        rows.append(by_cat.get(cat, {
            "category": cat,
            "label": DENIAL_CATEGORY_LABELS[cat],
            "count": 0,
            "amount_cents": 0,
        }))
    total_count = sum(r["count"] for r in rows)
    total_amount = sum(r["amount_cents"] for r in rows)
    return {
        "rows": rows,
        "total_count": total_count,
        "total_amount_cents": total_amount,
    }



# ---------------------------------------------------------------------------
# Phase 6 — Bulk remittance import (835 / JSON) + statement PDF + email
# ---------------------------------------------------------------------------
_IMPORT_MAX_BYTES = 2 * 1024 * 1024   # 2 MB


async def _stage_import(
    db, ctx: TenantContext, user: dict,
    ir: dict, filename: str, size: int,
) -> dict:
    matches = await match_claims(db, ctx.tenant_id, ir)
    resolved_payer_id = await resolve_payer_id(db, ctx.tenant_id, ir)
    now = _now()
    staged_id = str(uuid.uuid4())
    doc = stamp_for_write({
        "id": staged_id,
        "source": ir["source"],
        "filename": filename,
        "size_bytes": size,
        "status": "staged",
        "uploaded_by": user["id"],
        "resolved_payer_id": resolved_payer_id,
        "header": ir["header"],
        "claims": [{**c, "match": m}
                   for c, m in zip(ir["claims"], matches)],
        "created_at": now,
        "updated_at": now,
    }, ctx, location_id=None)
    await db.remittance_imports.insert_one(doc)

    matched = sum(1 for m in matches if m["matched"])
    return {
        "id": staged_id,
        "source": ir["source"],
        "filename": filename,
        "size_bytes": size,
        "status": "staged",
        "resolved_payer_id": resolved_payer_id,
        "header": ir["header"],
        "claim_count": len(ir["claims"]),
        "matched_count": matched,
        "unmatched_count": len(ir["claims"]) - matched,
        "claims": [{**c, "match": m}
                   for c, m in zip(ir["claims"], matches)],
    }


@router.post("/remittances/import")
async def upload_remittance_import(
    request: Request,
    file: UploadFile = File(...),
    user: dict = Depends(require_permission("remit", "post")),
    ctx: TenantContext = Depends(require_tenant),
):
    """Stage an 835 EDI or JSON remittance file for review.

    No mutations to ledger occur here — the caller must hit
    `POST /remittances/imports/{id}/commit` to actually post.
    """
    raw = await file.read()
    if len(raw) > _IMPORT_MAX_BYTES:
        raise HTTPException(413, "Import file too large (>2 MB)")
    if not raw:
        raise HTTPException(400, "Empty upload")

    filename = file.filename or "remit.unknown"
    is_json = filename.lower().endswith(".json") or raw.lstrip().startswith(b"{")
    try:
        if is_json:
            ir = parse_json_import(raw)
        else:
            ir = parse_835(raw.decode("utf-8", errors="ignore"))
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Parse error: {exc}")

    db = tenant_db(ctx.tenant_id)
    preview = await _stage_import(db, ctx, user, ir, filename, len(raw))
    await audit_success(
        user, "billing.remittance.import_staged", request,
        entity_type="remittance_import", entity_id=preview["id"],
        metadata={"filename": filename, "source": ir["source"],
                  "claim_count": preview["claim_count"],
                  "matched_count": preview["matched_count"]},
    )
    return preview


@router.get("/remittances/imports/{staged_id}")
async def read_remittance_import(
    staged_id: str,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    row = await db.remittance_imports.find_one(
        {"id": staged_id, "tenant_id": ctx.tenant_id}, {"_id": 0},
    )
    if not row:
        raise HTTPException(404, "Import not found")
    return row


@router.post("/remittances/imports/{staged_id}/commit")
async def commit_remittance_import(
    staged_id: str,
    request: Request,
    user: dict = Depends(require_permission("remit", "post")),
    ctx: TenantContext = Depends(require_tenant),
):
    """Convert a staged import into a posted remittance.

    Rejects if any IR claim is unmatched OR if the caller has not
    supplied an override for the payer (via a separate PUT) when
    the header payer could not be resolved.
    """
    db = tenant_db(ctx.tenant_id)
    row = await db.remittance_imports.find_one(
        {"id": staged_id, "tenant_id": ctx.tenant_id}, {"_id": 0},
    )
    if not row:
        raise HTTPException(404, "Import not found")
    if row["status"] != "staged":
        raise HTTPException(409, f"Import already {row['status']}")
    payer_id = row.get("resolved_payer_id")
    if not payer_id:
        raise HTTPException(
            409,
            "Could not resolve payer from the import header. "
            "Create/verify the payer record then retry upload.",
        )

    # Build a RemittancePostRequest from the staged IR + matches.
    claims_payload: list[dict] = []
    for item in row["claims"]:
        m = item.get("match") or {}
        if not m.get("matched") or not m.get("claim_id"):
            raise HTTPException(
                409,
                "One or more import rows are unmatched. Reconcile "
                "them before committing.",
            )
        claims_payload.append({
            "claim_id": m["claim_id"],
            "payer_control_number": item.get("payer_control_number"),
            "billed_cents": int(item.get("billed_cents") or 0),
            "paid_cents": int(item.get("paid_cents") or 0),
            "contractual_cents": int(item.get("contractual_cents") or 0),
            "patient_resp_cents": int(item.get("patient_resp_cents") or 0),
            "denied_cents": int(item.get("denied_cents") or 0),
            "denial_code": item.get("denial_code"),
            "lines": item.get("lines") or [],
        })

    body = RemittancePostRequest(
        payer_id=payer_id,
        received_at=row["header"].get("received_at"),
        check_or_eft_number=row["header"].get("check_or_eft_number"),
        notes=row["header"].get("notes"),
        total_paid_cents=int(row["header"].get("total_paid_cents") or 0),
        claims=claims_payload,
    )

    try:
        result = await post_remittance(
            db, ctx, user, body,
            recompute_invoice_balance=_recompute_invoice_balance,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc))

    await db.remittance_imports.update_one(
        {"id": staged_id, "tenant_id": ctx.tenant_id},
        {"$set": {"status": "committed",
                  "remittance_id": result["remittance_id"],
                  "committed_at": _now(),
                  "committed_by": user["id"],
                  "updated_at": _now()}},
    )
    await audit_success(
        user, "billing.remittance.import_committed", request,
        entity_type="remittance_import", entity_id=staged_id,
        metadata={"remittance_id": result["remittance_id"],
                  "claim_count": len(claims_payload),
                  "denial_count": result["denial_count"]},
    )
    # Phase 2a — emit `era_posted` events for each matched claim so
    # the claim-level timeline reflects clearinghouse-imported ERAs
    # identically to manually-posted remittances.
    for c in claims_payload:
        await emit_claim_event(
            db, ctx,
            claim_id=c["claim_id"],
            event_type="era_posted",
            actor_id=user["id"],
            remittance_id=result["remittance_id"],
            denial_code=c.get("denial_code"),
            payload={"billed_cents": c["billed_cents"],
                     "paid_cents": c["paid_cents"],
                     "contractual_cents": c["contractual_cents"],
                     "patient_resp_cents": c["patient_resp_cents"],
                     "denied_cents": c["denied_cents"],
                     "import_staged_id": staged_id},
        )
    return {
        "import_id": staged_id, "status": "committed",
        **result,
    }


# ---------------------------------------------------------------------------
# Statement PDF + email
# ---------------------------------------------------------------------------
async def _load_statement_with_patient(db, ctx, patient_id, stmt_id):
    stmt = await db.statements.find_one(
        {"id": stmt_id, "patient_id": patient_id,
         "tenant_id": ctx.tenant_id}, {"_id": 0},
    )
    if not stmt:
        raise HTTPException(404, "Statement not found")
    patient = await db.patients.find_one(
        {"id": patient_id, "tenant_id": ctx.tenant_id},
        {"_id": 0, "id": 1, "first_name": 1, "last_name": 1,
         "email": 1, "phone": 1},
    )
    return stmt, patient


@router.get(
    "/patients/{patient_id}/statements/{stmt_id}/pdf",
    responses={200: {"content": {"application/pdf": {}}}},
)
async def download_statement_pdf(
    patient_id: str, stmt_id: str, request: Request,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    stmt, patient = await _load_statement_with_patient(
        db, ctx, patient_id, stmt_id,
    )
    pdf = render_statement_pdf(statement=stmt, patient=patient)
    await audit_success(
        user, "billing.statement.pdf_downloaded", request,
        entity_type="statement", entity_id=stmt_id,
        metadata={"patient_id": patient_id,
                  "bytes": len(pdf)},
    )
    headers = {
        "Content-Disposition":
            f'attachment; filename="statement-{stmt_id[:8]}.pdf"',
    }
    return Response(content=pdf, media_type="application/pdf",
                    headers=headers)


class SendStatementPayload(BaseModel):
    channel: Literal["email", "mail", "portal"] = "email"
    to: str | None = None  # override recipient email


@router.post(
    "/patients/{patient_id}/statements/{stmt_id}/send",
)
async def email_statement(
    patient_id: str, stmt_id: str, request: Request,
    payload: SendStatementPayload | None = None,
    user: dict = Depends(require_role("admin", "staff")),
    ctx: TenantContext = Depends(require_tenant),
):
    """Deliver a statement via one of three channels:

      - email:  sends via Resend (or log-only fallback). Attaches PDF.
                Inserts a row into `statement_deliveries`.
      - mail:   stamps the statement as "queued for physical mail" so
                the staff UI can surface a Ready-to-Mail state. The
                actual print/mail workflow is operational.
      - portal: marks the statement as visible on the patient portal.
                It already is, so this is a bookkeeping signal for
                staff.
    """
    channel = (payload and payload.channel) or "email"
    db = tenant_db(ctx.tenant_id)
    stmt, patient = await _load_statement_with_patient(
        db, ctx, patient_id, stmt_id,
    )
    now = _now()
    sent_to: str | None = None
    provider: str = ""
    delivery_id: str | None = None

    if channel == "email":
        to = (payload and payload.to) or (patient or {}).get("email")
        if not to or "@" not in str(to):
            raise HTTPException(422, "Patient has no email on file")
        pdf = render_statement_pdf(statement=stmt, patient=patient)
        html = render_statement_email_html(patient=patient, statement=stmt)
        try:
            sent = await send_statement_email(
                to=to, subject="Your patient statement",
                html_body=html, pdf_bytes=pdf,
                pdf_filename=f"statement-{stmt_id[:8]}.pdf",
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"Email send failed: {exc}") from exc
        provider = sent["provider"]
        sent_to = to

        delivery = stamp_for_write({
            "id": str(uuid.uuid4()),
            "statement_id": stmt_id,
            "patient_id": patient_id,
            "to_email": to,
            "provider": provider,
            "message_id": sent.get("message_id"),
            "sent_by": user["id"],
            "sent_at": now,
            "created_at": now,
            "updated_at": now,
        }, ctx, location_id=None)
        await db.statement_deliveries.insert_one(delivery)
        delivery_id = delivery["id"]
    elif channel == "mail":
        provider = "queued-for-mail"
        sent_to = "postal"
    else:  # portal
        provider = "patient-portal"
        sent_to = patient.get("id") if patient else None

    # Stamp the statement so the UI shows sent_at / sent_via.
    await db.statements.update_one(
        {"id": stmt_id, "tenant_id": ctx.tenant_id},
        {"$set": {
            "sent_at": now, "sent_via": channel,
            "sent_to": sent_to, "updated_at": now,
        }},
    )

    await audit_success(
        user, "billing.statement.sent", request,
        entity_type="statement", entity_id=stmt_id,
        metadata={"channel": channel, "provider": provider,
                  "patient_id": patient_id, "to": sent_to,
                  "delivery_id": delivery_id},
    )
    return {
        "sent": True, "channel": channel, "provider": provider,
        "to": sent_to, "delivery_id": delivery_id,
    }


@router.get(
    "/patients/{patient_id}/statements/{stmt_id}/deliveries",
)
async def list_statement_deliveries(
    patient_id: str, stmt_id: str,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    rows = [_public(d) async for d in db.statement_deliveries.find(
        {"tenant_id": ctx.tenant_id,
         "patient_id": patient_id, "statement_id": stmt_id}, {"_id": 0},
    ).sort([("sent_at", -1)])]
    return rows



# ---------------------------------------------------------------------------
# Bulk — regenerate and dispatch statements for every patient with an
# outstanding balance whose balance has moved since their last statement.
# Month-end workflow for billing staff.
# ---------------------------------------------------------------------------
class BulkSendOutstandingPayload(BaseModel):
    # Reserved for future filters (location_id, min_balance_cents, …)
    # so the request signature is stable.
    dry_run: bool = False


@router.post("/statements/send-outstanding")
async def send_outstanding_statements(
    request: Request,
    payload: BulkSendOutstandingPayload | None = None,
    user: dict = Depends(require_role("admin", "staff")),
    ctx: TenantContext = Depends(require_tenant),
):
    """Regenerate and dispatch statements for every patient with a
    non-zero outstanding balance whose balance has changed since their
    most recent statement.

    Dispatch channel:
      - email  -> patient.email present
      - mail   -> otherwise (queued for physical mail)

    Returns a summary: generated, sent_email, queued_mail,
    skipped_unchanged, skipped_no_contact.
    """
    dry_run = bool(payload and payload.dry_run)
    db = tenant_db(ctx.tenant_id)

    patient_ids = await db.invoices.distinct(
        "patient_id",
        {"tenant_id": ctx.tenant_id, "balance_cents": {"$gt": 0}},
    )

    generated = 0
    sent_email = 0
    queued_mail = 0
    skipped_unchanged = 0
    skipped_no_contact = 0
    errors: list[dict] = []
    details: list[dict] = []

    for pid in patient_ids:
        patient = await db.patients.find_one(
            {"id": pid, "tenant_id": ctx.tenant_id},
            {"_id": 0, "id": 1, "first_name": 1, "last_name": 1,
             "email": 1, "phone": 1, "deleted_at": 1},
        )
        if not patient or patient.get("deleted_at"):
            continue

        current_balance_cents = 0
        async for inv in db.invoices.find(
            {"tenant_id": ctx.tenant_id, "patient_id": pid,
             "balance_cents": {"$gt": 0}},
            {"_id": 0, "balance_cents": 1},
        ):
            current_balance_cents += int(inv.get("balance_cents") or 0)
        if current_balance_cents <= 0:
            continue

        latest_stmt = await db.statements.find_one(
            {"tenant_id": ctx.tenant_id, "patient_id": pid},
            {"_id": 0, "total_balance_cents": 1, "id": 1},
            sort=[("generated_at", -1)],
        )
        if latest_stmt and int(latest_stmt.get("total_balance_cents") or 0) == current_balance_cents:
            skipped_unchanged += 1
            continue

        if dry_run:
            generated += 1
            details.append({
                "patient_id": pid,
                "balance_cents": current_balance_cents,
                "channel": "email" if patient.get("email") else "mail",
            })
            continue

        try:
            fresh = await _build_statement_for_patient(
                db, ctx, user, patient, request,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append({"patient_id": pid, "error": str(exc)})
            continue
        generated += 1
        stmt_id = fresh["id"]
        now = _now()

        # Dispatch
        to = (patient or {}).get("email")
        channel: str
        sent_to_value: str | None
        provider: str = ""
        delivery_id: str | None = None

        if to and "@" in str(to):
            try:
                pdf = render_statement_pdf(statement=fresh, patient=patient)
                html = render_statement_email_html(
                    patient=patient, statement=fresh,
                )
                sent = await send_statement_email(
                    to=to, subject="Your patient statement",
                    html_body=html, pdf_bytes=pdf,
                    pdf_filename=f"statement-{stmt_id[:8]}.pdf",
                )
                provider = sent["provider"]
                delivery = stamp_for_write({
                    "id": str(uuid.uuid4()),
                    "statement_id": stmt_id,
                    "patient_id": pid,
                    "to_email": to,
                    "provider": provider,
                    "message_id": sent.get("message_id"),
                    "sent_by": user["id"],
                    "sent_at": now,
                    "created_at": now,
                    "updated_at": now,
                }, ctx, location_id=None)
                await db.statement_deliveries.insert_one(delivery)
                delivery_id = delivery["id"]
                sent_email += 1
                channel = "email"
                sent_to_value = to
            except Exception as exc:  # noqa: BLE001
                errors.append({"patient_id": pid, "stmt_id": stmt_id,
                               "error": f"email_failed: {exc}"})
                channel = "mail"
                provider = "queued-for-mail-fallback"
                sent_to_value = "postal"
                queued_mail += 1
        else:
            if not (patient.get("phone") or to):
                skipped_no_contact += 1
            channel = "mail"
            provider = "queued-for-mail"
            sent_to_value = "postal"
            queued_mail += 1

        await db.statements.update_one(
            {"id": stmt_id, "tenant_id": ctx.tenant_id},
            {"$set": {
                "sent_at": now, "sent_via": channel,
                "sent_to": sent_to_value, "updated_at": now,
            }},
        )
        await audit_success(
            user, "billing.statement.sent", request,
            entity_type="statement", entity_id=stmt_id,
            metadata={"channel": channel, "provider": provider,
                      "patient_id": pid, "to": sent_to_value,
                      "delivery_id": delivery_id, "bulk": True},
        )

    await audit_success(
        user, "billing.statement.bulk_send_outstanding", request,
        entity_type="statement", entity_id=None,
        metadata={
            "generated": generated,
            "sent_email": sent_email,
            "queued_mail": queued_mail,
            "skipped_unchanged": skipped_unchanged,
            "skipped_no_contact": skipped_no_contact,
            "errors": len(errors),
            "dry_run": dry_run,
        },
    )

    return {
        "generated": generated,
        "sent_email": sent_email,
        "queued_mail": queued_mail,
        "skipped_unchanged": skipped_unchanged,
        "skipped_no_contact": skipped_no_contact,
        "errors": errors,
        "dry_run": dry_run,
        "details": details if dry_run else [],
    }


# ---------------------------------------------------------------------------
# Phase 2c — Clearinghouse configuration + enrollments
# ---------------------------------------------------------------------------
# All routes below are admin-gated via `clinic_settings.update` — the
# same permission already protecting the Payers settings page. No new
# permission introduced in this phase.
@router.get(
    "/clearinghouse/config",
    response_model=list[ClearinghouseConfigSummary],
)
async def read_clearinghouse_config(
    user: dict = Depends(require_permission("clinic_settings", "update")),
    ctx: TenantContext = Depends(require_tenant),
):
    """Return a secret-free summary of every registered clearinghouse
    adapter's env-sourced configuration.

    Never returns secrets. `has_client_id` / `has_client_secret` + a
    redacted `client_id_hint` are the only knobs the UI exposes —
    operators configure actual credentials via env vars managed by
    operations, not the UI.
    """
    return config_summaries()


@router.get(
    "/clearinghouse/enrollments",
    response_model=list[ClearinghouseEnrollmentPublic],
)
async def list_clearinghouse_enrollments(
    clearinghouse: str | None = Query(default=None),
    payer_id: str | None = Query(default=None),
    user: dict = Depends(require_permission("clinic_settings", "update")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    q = scoped_filter({}, ctx, location_scoped=False)
    if q.get("__deny__"):
        return []
    if clearinghouse:
        q["clearinghouse"] = clearinghouse
    if payer_id:
        q["payer_id"] = payer_id
    cursor = db.clearinghouse_enrollments.find(q, {"_id": 0}).sort(
        [("updated_at", -1)],
    )
    return [d async for d in cursor]


@router.post(
    "/clearinghouse/enrollments",
    response_model=ClearinghouseEnrollmentPublic, status_code=201,
)
async def upsert_clearinghouse_enrollment(
    payload: ClearinghouseEnrollmentCreate,
    request: Request,
    user: dict = Depends(require_permission("clinic_settings", "update")),
    ctx: TenantContext = Depends(require_tenant),
):
    """Create an enrollment row, or update the existing one if the
    (tenant, payer, clearinghouse) triple already exists.

    Idempotent upsert: operators can POST the same payload safely
    during onboarding / import runs without race conditions.
    """
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tenant context required")
    db = tenant_db(ctx.tenant_id)

    payer = await _scoped_one(db.billing_payers, {"id": payload.payer_id}, ctx)
    if not payer:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payer not found")

    now = _now()
    existing = await db.clearinghouse_enrollments.find_one(
        {"tenant_id": ctx.tenant_id,
         "payer_id": payload.payer_id,
         "clearinghouse": payload.clearinghouse},
        {"_id": 0},
    )
    if existing:
        updates = {
            "status": payload.status,
            "submitter_id": payload.submitter_id,
            "trading_partner_id": payload.trading_partner_id,
            "notes": payload.notes,
            "updated_at": now,
            "updated_by": user["id"],
        }
        await db.clearinghouse_enrollments.update_one(
            {"id": existing["id"], "tenant_id": ctx.tenant_id},
            {"$set": updates},
        )
        # Mirror to the payer row (same progression rule as insert).
        _STATE_RANK_U = {"not_started": 0, "in_progress": 1,
                         "enrolled": 2, "suspended": 1}
        cur = _STATE_RANK_U.get(
            payer.get("enrollment_status") or "not_started", 0,
        )
        new = _STATE_RANK_U.get(payload.status, 0)
        if new >= cur:
            payer_set = {
                "enrollment_status": payload.status,
                "clearinghouse_route": payload.clearinghouse,
                "updated_at": now,
                "updated_by": user["id"],
            }
            if payload.trading_partner_id is not None:
                payer_set["trading_partner_id"] = payload.trading_partner_id
            await db.billing_payers.update_one(
                {"id": payload.payer_id, "tenant_id": ctx.tenant_id},
                {"$set": payer_set},
            )
        fresh = await db.clearinghouse_enrollments.find_one(
            {"id": existing["id"], "tenant_id": ctx.tenant_id}, {"_id": 0},
        )
        await audit_success(
            user, "billing.clearinghouse.enrollment_updated", request,
            entity_type="clearinghouse_enrollment", entity_id=existing["id"],
            metadata={"payer_id": payload.payer_id,
                      "clearinghouse": payload.clearinghouse,
                      "status": payload.status},
        )
        return _public(fresh)

    eid = str(uuid.uuid4())
    doc = stamp_for_write({
        "id": eid,
        "payer_id": payload.payer_id,
        "clearinghouse": payload.clearinghouse,
        "status": payload.status,
        "submitter_id": payload.submitter_id,
        "trading_partner_id": payload.trading_partner_id,
        "notes": payload.notes,
        "created_at": now,
        "updated_at": now,
        "created_by": user["id"],
        "updated_by": user["id"],
    }, ctx, location_id=None)
    await db.clearinghouse_enrollments.insert_one(doc)
    # Mirror back to the payer row so the claims submission path can
    # gate on a single field without joining against enrollments. Only
    # updates the payer if the incoming state is an improvement
    # (e.g. `enrolled` beats `in_progress`) OR if the payer's current
    # enrollment state is `not_started` (the default).
    _STATE_RANK = {"not_started": 0, "in_progress": 1, "enrolled": 2, "suspended": 1}
    cur = _STATE_RANK.get(payer.get("enrollment_status") or "not_started", 0)
    new = _STATE_RANK.get(payload.status, 0)
    if new >= cur:
        await db.billing_payers.update_one(
            {"id": payload.payer_id, "tenant_id": ctx.tenant_id},
            {"$set": {
                "clearinghouse_route": payload.clearinghouse,
                "enrollment_status": payload.status,
                "trading_partner_id": payload.trading_partner_id
                    or payer.get("trading_partner_id"),
                "updated_at": now,
                "updated_by": user["id"],
            }},
        )
    await audit_success(
        user, "billing.clearinghouse.enrollment_created", request,
        entity_type="clearinghouse_enrollment", entity_id=eid,
        metadata={"payer_id": payload.payer_id,
                  "clearinghouse": payload.clearinghouse,
                  "status": payload.status},
    )
    return _public(doc)


@router.patch(
    "/clearinghouse/enrollments/{enrollment_id}",
    response_model=ClearinghouseEnrollmentPublic,
)
async def update_clearinghouse_enrollment(
    enrollment_id: str,
    payload: ClearinghouseEnrollmentUpdate,
    request: Request,
    user: dict = Depends(require_permission("clinic_settings", "update")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    existing = await _scoped_one(
        db.clearinghouse_enrollments, {"id": enrollment_id}, ctx,
    )
    if not existing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Enrollment not found")
    updates = {k: v for k, v in payload.model_dump(exclude_unset=True).items()}
    if not updates:
        return _public(existing)
    updates["updated_at"] = _now()
    updates["updated_by"] = user["id"]
    await db.clearinghouse_enrollments.update_one(
        {"id": enrollment_id, "tenant_id": ctx.tenant_id},
        {"$set": updates},
    )
    fresh = await db.clearinghouse_enrollments.find_one(
        {"id": enrollment_id, "tenant_id": ctx.tenant_id}, {"_id": 0},
    )
    # If status moved, mirror to the payer row (same rule as upsert).
    if "status" in updates:
        _STATE_RANK = {"not_started": 0, "in_progress": 1,
                       "enrolled": 2, "suspended": 1}
        payer = await db.billing_payers.find_one(
            {"id": existing["payer_id"], "tenant_id": ctx.tenant_id},
            {"_id": 0},
        )
        if payer:
            cur = _STATE_RANK.get(
                payer.get("enrollment_status") or "not_started", 0,
            )
            new = _STATE_RANK.get(updates["status"], 0)
            if new >= cur:
                payer_set = {
                    "enrollment_status": updates["status"],
                    "clearinghouse_route": existing["clearinghouse"],
                    "updated_at": updates["updated_at"],
                    "updated_by": user["id"],
                }
                if "trading_partner_id" in updates:
                    payer_set["trading_partner_id"] = updates["trading_partner_id"]
                await db.billing_payers.update_one(
                    {"id": existing["payer_id"], "tenant_id": ctx.tenant_id},
                    {"$set": payer_set},
                )
    await audit_success(
        user, "billing.clearinghouse.enrollment_updated", request,
        entity_type="clearinghouse_enrollment", entity_id=enrollment_id,
        metadata={"fields": sorted(list(updates.keys())),
                  "payer_id": existing["payer_id"]},
    )
    return _public(fresh)



# ---------------------------------------------------------------------------
# Phase 5 — Provider directory (billing / rendering / referring)
# ---------------------------------------------------------------------------
# These rows hold the real NPI, Tax-ID, and address data required for
# 837P submission. Claims still keep free-text provider IDs; when a
# matching row exists here the payload builder will resolve it. Admin-
# gated via the existing `clinic_settings.update` permission — no new
# permission introduced.
@router.get("/providers", response_model=list[ProviderPublic])
async def list_providers(
    kind: str | None = Query(default=None),
    user: dict = Depends(require_permission("clinic_settings", "update")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    q = scoped_filter({}, ctx, location_scoped=False)
    if q.get("__deny__"):
        return []
    if kind:
        q["kind"] = kind
    cursor = db.providers.find(q, {"_id": 0}).sort([("name", 1)])
    return [d async for d in cursor]


@router.post("/providers", response_model=ProviderPublic, status_code=201)
async def create_provider(
    payload: ProviderCreate,
    request: Request,
    user: dict = Depends(require_permission("clinic_settings", "update")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    # Uniqueness on (tenant, kind, npi) — clinic-wide NPI catalog.
    dup = await db.providers.find_one(
        {"tenant_id": ctx.tenant_id, "kind": payload.kind, "npi": payload.npi},
        {"_id": 0, "id": 1},
    )
    if dup:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Provider already exists for kind={payload.kind} / npi={payload.npi}",
        )
    now = _now()
    pid = str(uuid.uuid4())
    doc = stamp_for_write({
        "id": pid,
        "kind": payload.kind,
        "name": payload.name,
        "npi": payload.npi,
        "tax_id": payload.tax_id,
        "taxonomy_code": payload.taxonomy_code,
        "phone": payload.phone,
        "address": payload.address,
        "status": "active",
        "notes": payload.notes,
        "created_at": now, "updated_at": now,
        "created_by": user["id"], "updated_by": user["id"],
    }, ctx, location_id=None)
    await db.providers.insert_one(doc)
    await audit_success(
        user, "billing.provider.created", request,
        entity_type="provider", entity_id=pid,
        metadata={"kind": payload.kind, "npi": payload.npi},
    )
    return _public(doc)


@router.patch("/providers/{provider_id}", response_model=ProviderPublic)
async def update_provider(
    provider_id: str,
    payload: ProviderUpdate,
    request: Request,
    user: dict = Depends(require_permission("clinic_settings", "update")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    existing = await _scoped_one(db.providers, {"id": provider_id}, ctx)
    if not existing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Provider not found")
    updates = {k: v for k, v in payload.model_dump(exclude_unset=True).items()}
    if not updates:
        return _public(existing)
    updates["updated_at"] = _now()
    updates["updated_by"] = user["id"]
    await db.providers.update_one(
        {"id": provider_id, "tenant_id": ctx.tenant_id},
        {"$set": updates},
    )
    fresh = await db.providers.find_one(
        {"id": provider_id, "tenant_id": ctx.tenant_id}, {"_id": 0},
    )
    await audit_success(
        user, "billing.provider.updated", request,
        entity_type="provider", entity_id=provider_id,
        metadata={"fields": sorted(updates.keys())},
    )
    return _public(fresh)


# ---------------------------------------------------------------------------
# Phase 5 — Service Facility directory (837P loop 2310C)
# ---------------------------------------------------------------------------
@router.get("/service-facilities", response_model=list[ServiceFacilityPublic])
async def list_service_facilities(
    user: dict = Depends(require_permission("clinic_settings", "update")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    q = scoped_filter({}, ctx, location_scoped=False)
    if q.get("__deny__"):
        return []
    cursor = db.service_facilities.find(q, {"_id": 0}).sort([("name", 1)])
    return [d async for d in cursor]


@router.post(
    "/service-facilities",
    response_model=ServiceFacilityPublic, status_code=201,
)
async def create_service_facility(
    payload: ServiceFacilityCreate,
    request: Request,
    user: dict = Depends(require_permission("clinic_settings", "update")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    now = _now()
    fid = str(uuid.uuid4())
    doc = stamp_for_write({
        "id": fid,
        "name": payload.name,
        "npi": payload.npi,
        "address": payload.address,
        "phone": payload.phone,
        "status": "active",
        "notes": payload.notes,
        "created_at": now, "updated_at": now,
        "created_by": user["id"], "updated_by": user["id"],
    }, ctx, location_id=None)
    await db.service_facilities.insert_one(doc)
    await audit_success(
        user, "billing.service_facility.created", request,
        entity_type="service_facility", entity_id=fid,
        metadata={"name": payload.name, "npi": payload.npi},
    )
    return _public(doc)


@router.patch(
    "/service-facilities/{facility_id}",
    response_model=ServiceFacilityPublic,
)
async def update_service_facility(
    facility_id: str,
    payload: ServiceFacilityUpdate,
    request: Request,
    user: dict = Depends(require_permission("clinic_settings", "update")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    existing = await _scoped_one(db.service_facilities, {"id": facility_id}, ctx)
    if not existing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Service facility not found")
    updates = {k: v for k, v in payload.model_dump(exclude_unset=True).items()}
    if not updates:
        return _public(existing)
    updates["updated_at"] = _now()
    updates["updated_by"] = user["id"]
    await db.service_facilities.update_one(
        {"id": facility_id, "tenant_id": ctx.tenant_id},
        {"$set": updates},
    )
    fresh = await db.service_facilities.find_one(
        {"id": facility_id, "tenant_id": ctx.tenant_id}, {"_id": 0},
    )
    await audit_success(
        user, "billing.service_facility.updated", request,
        entity_type="service_facility", entity_id=facility_id,
        metadata={"fields": sorted(updates.keys())},
    )
    return _public(fresh)

