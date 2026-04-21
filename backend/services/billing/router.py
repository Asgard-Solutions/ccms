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

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response

from core.audit import audit_success
from core.deps import require_role
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
from services.billing.submission import (
    DEFAULT_FOLLOWUP_DAYS,
    build_json_payload,
    build_x12_837p_preview,
    followup_claim_ids,
    followup_threshold_iso,
)
from services.billing.models import (
    AdjustmentCreate,
    AdjustmentPublic,
    AgingBucket,
    ClaimAssignmentUpdate,
    ClaimCreate,
    ClaimPublic,
    ClaimStatus,
    ClaimSubmissionCreate,
    ClaimSubmissionOutcome,
    ClaimSubmissionPublic,
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
    RefundCreate,
    RefundPublic,
    RemittanceClaimPublic,
    RemittanceLinePublic,
    RemittancePostRequest,
    RemittancePublic,
    StatementPublic,
)

router = APIRouter(prefix="/billing", tags=["billing"])


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        {"_id": 0, "id": 1, "first_name": 1, "last_name": 1, "dob": 1},
    )
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
    return {
        "claim_id": claim_id,
        "status": new_status,
        "errors": result["errors"],
        "warnings": result["warnings"],
        "passed": result["passed"],
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
    + modifiers + the most recent scrubber findings."""
    db = tenant_db(ctx.tenant_id)
    claim = await _scoped_one(db.claims, {"id": claim_id}, ctx)
    if not claim:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Claim not found")
    scrub_ctx = await _load_claim_context(db, ctx, claim)
    latest = await db.claim_validation_runs.find_one(
        {"tenant_id": ctx.tenant_id, "claim_id": claim_id},
        {"_id": 0}, sort=[("run_at", -1)],
    )
    return {
        "claim": _public(claim),
        "diagnoses": scrub_ctx.diagnoses,
        "lines": [
            {**ln, "modifiers": [m["modifier_code"]
                                 for m in scrub_ctx.line_modifiers_by_line.get(ln["id"], [])]}
            for ln in scrub_ctx.lines
        ],
        "latest_validation": latest,
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
    """Pull patient + payer + policy + dx + lines for payload builds."""
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
    return patient, payer, policy, diagnoses, lines


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
    """Record a manual submission attempt. Advances the claim
    `ready → submitted` (or `rejected → submitted` for resubmissions).
    Persists both JSON export and 837P preview on the submission row."""
    db = tenant_db(ctx.tenant_id)
    claim = await _scoped_one(db.claims, {"id": claim_id}, ctx)
    if not claim:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Claim not found")

    # Only certain statuses may be submitted. Use the canonical state
    # machine so resubmissions from rejected are blocked unless caller
    # first moved the claim back to ready.
    current = claim["status"]
    if current not in ("ready",):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Claim in status '{current}' cannot be submitted; "
            "transition to 'ready' first.",
        )
    new_status = transitions.http_advance("claim", current, "submitted")

    patient, payer, policy, diagnoses, lines = \
        await _load_submission_context(db, ctx, claim)

    payload_json = build_json_payload(
        claim=claim, diagnoses=diagnoses, lines=lines,
        patient=patient, payer=payer, policy=policy,
    )
    payload_x12 = build_x12_837p_preview(
        claim=claim, diagnoses=diagnoses, lines=lines,
        patient=patient, payer=payer, policy=policy,
    )

    now = _now()
    sub_id = str(uuid.uuid4())
    sub_doc = stamp_for_write({
        "id": sub_id,
        "claim_id": claim_id,
        "method": body.method,
        "external_reference": body.external_reference,
        "submitted_at": now,
        "submitted_by": user["id"],
        "payload_format": "json+x12-837p-preview",
        "payload_json": payload_json,
        "payload_x12": payload_x12,
        "payload_size_bytes": len(payload_x12),
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
             external_reference=body.external_reference,
             from_status=current, to_status=new_status,
         )}},
    )
    await audit_success(
        user, "billing.claim.submission_created", request,
        entity_type="claim", entity_id=claim_id,
        metadata={"submission_id": sub_id, "method": body.method,
                  "external_reference": body.external_reference,
                  "from": current, "to": new_status},
    )
    fresh = await db.claim_submissions.find_one(
        {"id": sub_id, "tenant_id": ctx.tenant_id}, {"_id": 0},
    )
    # Response: exclude heavy payload fields (accessible via dedicated
    # `/payload` endpoint if needed later).
    out = {k: v for k, v in fresh.items()
           if k not in ("payload_json", "payload_x12")}
    return out


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


@router.patch("/claims/{claim_id}/assignment", response_model=ClaimPublic)
async def update_claim_assignment(
    claim_id: str,
    body: ClaimAssignmentUpdate,
    request: Request,
    user: dict = Depends(require_permission("claim", "correct_resubmit")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    claim = await _scoped_one(db.claims, {"id": claim_id}, ctx)
    if not claim:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Claim not found")

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
                  "to": body.assigned_to},
    )
    fresh = await db.claims.find_one(
        {"id": claim_id, "tenant_id": ctx.tenant_id}, {"_id": 0},
    )
    return _public(fresh)


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
      * `rejected` — statuses rejected, denied
      * `follow-up` — stale submitted/rejected/denied claims per the
        follow-up rule in submission.py
    """
    db = tenant_db(ctx.tenant_id)
    q: dict = {"tenant_id": ctx.tenant_id}

    qname = queue_name.replace("_", "-").lower()
    if qname == "pending-submission":
        q["status"] = {"$in": ["ready", "validation_failed"]}
    elif qname == "rejected":
        q["status"] = {"$in": ["rejected", "denied"]}
    elif qname == "follow-up":
        ids = await followup_claim_ids(db, ctx.tenant_id)
        if not ids:
            return []
        q["id"] = {"$in": ids}
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
            q["status"] = {"$in": [s for s in wanted if (base is None or s in base)]}
    if age_days is not None:
        q["created_at"] = {"$lt": followup_threshold_iso(age_days)}

    cursor = db.claims.find(q, {"_id": 0}).sort(
        [("updated_at", -1)]
    ).limit(limit)
    return [c async for c in cursor]



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
    open_invoices = [i async for i in db.invoices.find(
        {"tenant_id": ctx.tenant_id, "patient_id": patient_id,
         "balance_cents": {"$gt": 0}},
        {"_id": 0},
    ).sort([("issued_at", 1)])]

    now = _now()
    body_text = render_statement_body(
        patient=patient, invoices=open_invoices, as_of_iso=now,
    )
    total = sum(int(i.get("balance_cents") or 0) for i in open_invoices)

    stmt_id = str(uuid.uuid4())
    doc = stamp_for_write({
        "id": stmt_id,
        "patient_id": patient_id,
        "generated_at": now,
        "generated_by": user["id"],
        "as_of_date": now[:10],
        "total_balance_cents": total,
        "invoice_count": len(open_invoices),
        "invoice_ids": [i["id"] for i in open_invoices],
        "body": body_text,
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


@router.post(
    "/patients/{patient_id}/statements/{stmt_id}/send",
)
async def email_statement(
    patient_id: str, stmt_id: str, request: Request,
    user: dict = Depends(require_role("admin", "staff")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    stmt, patient = await _load_statement_with_patient(
        db, ctx, patient_id, stmt_id,
    )
    to = (patient or {}).get("email")
    if not to:
        raise HTTPException(422, "Patient has no email on file")

    pdf = render_statement_pdf(statement=stmt, patient=patient)
    html = render_statement_email_html(patient=patient, statement=stmt)
    try:
        sent = await send_statement_email(
            to=to, subject="Your patient statement",
            html_body=html, pdf_bytes=pdf,
            pdf_filename=f"statement-{stmt_id[:8]}.pdf",
        )
    except Exception as exc:
        raise HTTPException(502, f"Email send failed: {exc}")

    now = _now()
    delivery = stamp_for_write({
        "id": str(uuid.uuid4()),
        "statement_id": stmt_id,
        "patient_id": patient_id,
        "to_email": to,
        "provider": sent["provider"],
        "message_id": sent.get("message_id"),
        "sent_by": user["id"],
        "sent_at": now,
        "created_at": now,
        "updated_at": now,
    }, ctx, location_id=None)
    await db.statement_deliveries.insert_one(delivery)
    await audit_success(
        user, "billing.statement.emailed", request,
        entity_type="statement", entity_id=stmt_id,
        metadata={"to_email": to, "provider": sent["provider"],
                  "message_id": sent.get("message_id"),
                  "patient_id": patient_id},
    )
    return {"sent": True,
            "provider": sent["provider"],
            "message_id": sent.get("message_id"),
            "to": to,
            "delivery_id": delivery["id"]}


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

