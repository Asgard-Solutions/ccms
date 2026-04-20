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

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from core.audit import audit_success
from core.deps import require_role
from core.tenancy import TenantContext, require_tenant, tenant_db
from core.tenant_scope import scoped_filter, stamp_for_write
from services.authz.policy import require_permission
from services.billing import transitions
from services.billing.ledger import build_patient_ledger
from services.billing.models import (
    AdjustmentCreate,
    AdjustmentPublic,
    ClaimCreate,
    ClaimPublic,
    ClaimStatus,
    DenialWorkItemPublic,
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
    RemittancePublic,
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


@router.put("/payers/{payer_id}", response_model=PayerPublic)
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
        "status": "draft",
        "service_date_from": payload.service_date_from,
        "service_date_to": payload.service_date_to,
        "billed_cents": billed_cents,
        "paid_cents": 0,
        "submitted_at": None,
        "accepted_at": None,
        "last_denial_code": None,
        "notes": payload.notes,
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
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    q = scoped_filter({}, ctx, location_scoped=False)
    if q.get("__deny__"):
        return []
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
@router.put("/insurance-policies/{policy_id}",
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


@router.put("/fee-schedules/{schedule_id}/lines")
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

