"""
services/billing/remittance.py — Phase 5 posting + AR helpers.

Responsibilities:
  1. `post_remittance()` — core posting pipeline that writes the
     remittance header, per-claim adjudication rows, per-line details,
     the system `payment` (method=`era_posting`), allocations,
     contractual adjustments, and denial work items — all inside the
     tenant's scope and all audit-ready.
  2. `compute_ar_buckets()` — 0-30 / 31-60 / 61-90 / 91-120 / 120+
     bucket roll-up driven by `invoice.issued_at` (fallback
     `created_at`) and current `balance_cents`.
  3. `render_statement_body()` — produce the plain-text body snapshot
     of a statement; no PDF, no email (scaffolding only).

Financial consistency rule enforced here: *every* mutation either goes
through `_recompute_invoice_balance` in router.py or does not touch
invoice balances directly. The remittance posting relies exclusively
on the standard `payment_allocations` + `billing_adjustments` paths,
then triggers a recompute per affected invoice.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from core.tenancy import TenantContext
from core.tenant_scope import stamp_for_write
from services.billing.denial_categories import derive_category

# Aging buckets — (label, min_days_inclusive, max_days_inclusive)
# The last bucket's upper bound is None -> open-ended 120+.
AGING_BUCKETS: list[tuple[str, int, int | None]] = [
    ("0-30", 0, 30),
    ("31-60", 31, 60),
    ("61-90", 61, 90),
    ("91-120", 91, 120),
    ("120+", 121, None),
]


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _days_between(iso_a: str | None, iso_b: str | None) -> int:
    """Return whole-days (iso_b - iso_a). Robust to None and `Z` suffix."""
    if not iso_a or not iso_b:
        return 0
    try:
        a = datetime.fromisoformat(iso_a.replace("Z", "+00:00"))
        b = datetime.fromisoformat(iso_b.replace("Z", "+00:00"))
    except Exception:
        return 0
    return max(0, (b.date() - a.date()).days)


def _bucket_for_days(days: int) -> str:
    for label, lo, hi in AGING_BUCKETS:
        if hi is None and days >= lo:
            return label
        if hi is not None and lo <= days <= hi:
            return label
    return AGING_BUCKETS[-1][0]


def compute_ar_buckets(
    invoices: Iterable[dict], *, as_of_iso: str | None = None,
) -> dict[str, Any]:
    """Roll up open-balance invoices into aging buckets.

    Only considers invoices with `balance_cents > 0` and status NOT in
    {void, refunded}. Dates are measured against `issued_at` if
    present, otherwise `created_at`.
    """
    as_of = as_of_iso or _iso_now()
    by_bucket: dict[str, dict] = {
        b[0]: {"bucket": b[0], "min_days": b[1], "max_days": b[2],
               "balance_cents": 0, "invoice_count": 0}
        for b in AGING_BUCKETS
    }
    total = 0
    total_count = 0
    for inv in invoices:
        if inv.get("status") in ("void", "refunded"):
            continue
        bal = int(inv.get("balance_cents") or 0)
        if bal <= 0:
            continue
        start = inv.get("issued_at") or inv.get("created_at")
        days = _days_between(start, as_of)
        bucket = _bucket_for_days(days)
        by_bucket[bucket]["balance_cents"] += bal
        by_bucket[bucket]["invoice_count"] += 1
        total += bal
        total_count += 1
    return {
        "as_of": as_of,
        "total_balance_cents": total,
        "total_invoice_count": total_count,
        "buckets": list(by_bucket.values()),
    }


def render_statement_body(
    *, patient: dict, invoices: list[dict], as_of_iso: str,
) -> str:
    """Deterministic plain-text statement — stable for regression.

    Each invoice entry may optionally carry enriched fields injected by
    the statement generator:
      * billed_cents       — gross invoice total
      * insurance_paid_cents — sum of allocations from payments with a
        non-null payer_id (i.e. insurance money)
      * patient_paid_cents — sum of allocations from payments with a
        null payer_id (i.e. patient money)
      * adjustments_cents  — sum of adjustments written off the invoice
      * balance_cents      — unchanged
    """
    name = " ".join(filter(None, [patient.get("first_name"),
                                  patient.get("last_name")])) or "Patient"
    lines: list[str] = [
        "PATIENT STATEMENT",
        f"As of: {as_of_iso[:10]}",
        f"Patient: {name}",
        f"Patient ID: {patient.get('id', '')[:8]}",
        "",
        "Open invoices:",
        "-" * 96,
        (f"  {'Invoice':<12} {'Issued':<12} {'Billed':>10} "
         f"{'Insurance':>12} {'Adjust.':>10} {'Pt.Paid':>10} {'Balance':>10}"),
        "-" * 96,
    ]
    total_billed = 0
    total_ins = 0
    total_adj = 0
    total_pt = 0
    total_bal = 0
    for inv in invoices:
        bal = int(inv.get("balance_cents") or 0)
        if bal <= 0:
            continue
        billed = int(inv.get("billed_cents") or inv.get("total_cents") or 0)
        ins_paid = int(inv.get("insurance_paid_cents") or 0)
        pt_paid = int(inv.get("patient_paid_cents") or 0)
        adj = int(inv.get("adjustments_cents") or inv.get("adjustment_cents") or 0)
        total_billed += billed
        total_ins += ins_paid
        total_pt += pt_paid
        total_adj += adj
        total_bal += bal
        issued = (inv.get("issued_at") or "")[:10] or "—"
        lines.append(
            f"  {inv['id'][:8]:<12} {issued:<12} "
            f"${billed/100:>9.2f} ${ins_paid/100:>11.2f} "
            f"${adj/100:>9.2f} ${pt_paid/100:>9.2f} ${bal/100:>9.2f}"
        )
    lines.append("-" * 96)
    lines.append(
        f"  {'TOTALS':<25} ${total_billed/100:>9.2f} "
        f"${total_ins/100:>11.2f} ${total_adj/100:>9.2f} "
        f"${total_pt/100:>9.2f} ${total_bal/100:>9.2f}"
    )
    lines.append("")
    lines.append(f"AMOUNT DUE FROM PATIENT: ${total_bal / 100:.2f}")
    lines.append("")
    lines.append("Please remit payment within 30 days of receipt.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Remittance posting
# ---------------------------------------------------------------------------
def _outcome_for(claim_paid: int, claim_billed: int, denied: int) -> str:
    if denied > 0 and claim_paid == 0:
        return "denied"
    if claim_paid >= claim_billed and claim_billed > 0:
        return "paid"
    if claim_paid > 0:
        return "partially_paid"
    return "denied"


async def post_remittance(
    db, ctx: TenantContext, actor: dict, body, *,
    recompute_invoice_balance,
) -> dict:
    """Persist a full remittance and its downstream artifacts.

    Returns:
      {remittance_id, payment_id, allocation_count, adjustment_count,
       denial_count, claim_rows, line_rows}

    Raises a ValueError with a descriptive message if inputs are
    inconsistent (claim not found, invoice not found, etc.) — router
    translates to HTTP 409/404 as appropriate.
    """
    now = _iso_now()
    remit_id = str(uuid.uuid4())

    # --- Pre-flight validation: all claims must exist and share payer.
    claim_rows: list[dict] = []
    for item in body.claims:
        claim = await db.claims.find_one(
            {"id": item.claim_id, "tenant_id": ctx.tenant_id}, {"_id": 0},
        )
        if not claim:
            raise ValueError(f"Claim not found: {item.claim_id}")
        if claim.get("payer_id") != body.payer_id:
            raise ValueError(
                f"Claim {item.claim_id[:8]} belongs to a different payer",
            )
        claim_rows.append(claim)

    # --- Totals sanity check against remittance header.
    sum_paid = sum(int(c.paid_cents) for c in body.claims)
    if sum_paid != body.total_paid_cents:
        raise ValueError(
            f"Total paid {body.total_paid_cents} does not equal sum of "
            f"claim paid amounts {sum_paid}",
        )

    # --- Header
    remit_doc = stamp_for_write({
        "id": remit_id,
        "payer_id": body.payer_id,
        "received_at": body.received_at,
        "status": "posted",
        "total_paid_cents": body.total_paid_cents,
        "check_or_eft_number": body.check_or_eft_number,
        "notes": body.notes,
        "posted_by": actor["id"],
        "posted_at": now,
        "created_at": now,
        "updated_at": now,
    }, ctx, location_id=None)
    await db.remittances.insert_one(remit_doc)

    # --- System payment (method=era_posting) — unallocated initially.
    # Patient id is taken from the first claim; if multiple patients
    # span one remittance we still track the payer-level payment but
    # allocate against each claim's source invoice below.
    first_claim = claim_rows[0]
    pay_id = str(uuid.uuid4())
    payment_doc = stamp_for_write({
        "id": pay_id,
        "patient_id": first_claim["patient_id"],
        "payer_id": body.payer_id,
        "method": "era_posting",
        "status": "completed",
        "amount_cents": body.total_paid_cents,
        "allocated_cents": 0,      # recomputed via allocations below
        "currency": "USD",
        "received_at": body.received_at,
        "reference": body.check_or_eft_number,
        "external_txn_id": f"remit:{remit_id}",
        "created_at": now,
        "updated_at": now,
        "created_by": actor["id"],
    }, ctx, location_id=None)
    await db.payments.insert_one(payment_doc)

    # --- Per-claim + per-line posting
    remit_claim_docs: list[dict] = []
    remit_line_docs: list[dict] = []
    alloc_docs: list[dict] = []
    adj_docs: list[dict] = []
    denial_docs: list[dict] = []
    invoice_ids_to_recompute: set[str] = set()

    for claim, item in zip(claim_rows, body.claims):
        rc_id = str(uuid.uuid4())
        remit_claim_docs.append(stamp_for_write({
            "id": rc_id,
            "remittance_id": remit_id,
            "claim_id": claim["id"],
            "payer_control_number": item.payer_control_number,
            "billed_cents": int(item.billed_cents),
            "paid_cents": int(item.paid_cents),
            "contractual_cents": int(item.contractual_cents),
            "patient_resp_cents": int(item.patient_resp_cents),
            "denied_cents": int(item.denied_cents),
            "denial_code": item.denial_code,
            "created_at": now,
        }, ctx, location_id=claim.get("location_id")))

        # Allocate payer payment against the source invoice if it
        # exists. This is the only place invoice balance is impacted
        # — we reuse the existing `_recompute_invoice_balance` flow.
        src_invoice_id = claim.get("source_invoice_id")
        if src_invoice_id and item.paid_cents > 0:
            alloc_docs.append(stamp_for_write({
                "id": str(uuid.uuid4()),
                "payment_id": pay_id,
                "invoice_id": src_invoice_id,
                "invoice_line_id": None,
                "amount_cents": int(item.paid_cents),
                "created_at": now,
            }, ctx, location_id=claim.get("location_id")))
            invoice_ids_to_recompute.add(src_invoice_id)

        # Contractual adjustment — payer-mandated reduction (not a
        # writeoff, not the patient's responsibility). This reduces
        # the invoice balance but is NOT collectible.
        if src_invoice_id and item.contractual_cents > 0:
            adj_docs.append(stamp_for_write({
                "id": str(uuid.uuid4()),
                "invoice_id": src_invoice_id,
                "kind": "contractual",
                "amount_cents": int(item.contractual_cents),
                "reason": f"Contractual writedown from {body.payer_id[:8]}",
                "approved_by_id": actor["id"],
                "created_at": now,
                "updated_at": now,
            }, ctx, location_id=claim.get("location_id")))
            invoice_ids_to_recompute.add(src_invoice_id)

        # Patient responsibility — per user choice 1b, we do NOT
        # mint a separate invoice line. The invoice's remaining
        # balance naturally becomes the patient's to pay; the
        # recompute helper will surface it.

        # Denial — open a work item if the payer denied any amount.
        if item.denied_cents > 0:
            denial_docs.append(stamp_for_write({
                "id": str(uuid.uuid4()),
                "claim_id": claim["id"],
                "claim_line_id": None,
                "denial_code": item.denial_code or "UNSPECIFIED",
                "denial_category": derive_category(item.denial_code),
                "amount_cents": int(item.denied_cents),
                "status": "open",
                "assigned_to_id": None,
                "resolution_notes": None,
                "opened_at": now,
                "closed_at": None,
                "created_at": now,
                "updated_at": now,
            }, ctx, location_id=claim.get("location_id")))

        # Per-line breakdown
        for ln in item.lines:
            rl_id = str(uuid.uuid4())
            remit_line_docs.append(stamp_for_write({
                "id": rl_id,
                "remittance_claim_id": rc_id,
                "claim_line_id": ln.claim_line_id,
                "cpt_code": ln.cpt_code,
                "billed_cents": int(ln.billed_cents),
                "paid_cents": int(ln.paid_cents),
                "contractual_cents": int(ln.contractual_cents),
                "patient_resp_cents": int(ln.patient_resp_cents),
                "denied_cents": int(ln.denied_cents),
                "denial_code": ln.denial_code,
                "denial_category": ln.denial_category,
                "created_at": now,
            }, ctx, location_id=claim.get("location_id")))
            # If a specific line has a denial code but the claim
            # didn't, also open a denial work item for that line.
            if ln.denied_cents > 0 and item.denied_cents == 0:
                denial_docs.append(stamp_for_write({
                    "id": str(uuid.uuid4()),
                    "claim_id": claim["id"],
                    "claim_line_id": ln.claim_line_id,
                    "denial_code": ln.denial_code or "UNSPECIFIED",
                    "denial_category": (ln.denial_category
                                        or derive_category(ln.denial_code)),
                    "amount_cents": int(ln.denied_cents),
                    "status": "open",
                    "assigned_to_id": None,
                    "resolution_notes": None,
                    "opened_at": now,
                    "closed_at": None,
                    "created_at": now,
                    "updated_at": now,
                }, ctx, location_id=claim.get("location_id")))

    if remit_claim_docs:
        await db.remittance_claims.insert_many(remit_claim_docs)
    if remit_line_docs:
        await db.remittance_lines.insert_many(remit_line_docs)
    if alloc_docs:
        await db.payment_allocations.insert_many(alloc_docs)
        await db.payments.update_one(
            {"id": pay_id, "tenant_id": ctx.tenant_id},
            {"$set": {"allocated_cents": sum(a["amount_cents"] for a in alloc_docs),
                      "updated_at": now}},
        )
    if adj_docs:
        await db.billing_adjustments.insert_many(adj_docs)
    if denial_docs:
        await db.denial_work_items.insert_many(denial_docs)

    # Recompute touched invoices
    for inv_id in invoice_ids_to_recompute:
        await recompute_invoice_balance(
            db, inv_id, ctx.tenant_id, actor["id"],
        )

    # Progress each claim through the Phase 4 state machine by the
    # computed outcome. Handles multi-step advances (e.g.
    # submitted -> accepted -> paid) by walking through the acceptance
    # gate when needed. Idempotent: illegal hops fall through silently.
    from services.billing import transitions
    for claim, item in zip(claim_rows, body.claims):
        outcome = _outcome_for(
            int(item.paid_cents), int(item.billed_cents),
            int(item.denied_cents),
        )
        target = {
            "paid": "paid",
            "partially_paid": "partially_paid",
            "denied": "denied",
        }[outcome]
        current = claim["status"]
        # Walk the path if direct hop is illegal.
        path: list[str] = []
        if current == "submitted" and target in ("paid", "partially_paid", "denied"):
            path.append("accepted")
        path.append(target)

        for step in path:
            try:
                new_status = transitions.advance("claim", current, step)
            except transitions.TransitionError:
                break
            set_fields: dict = {
                "status": new_status, "updated_at": now,
                "updated_by": actor["id"],
            }
            if step == "accepted":
                set_fields["accepted_at"] = now
            if step in ("paid", "partially_paid"):
                set_fields["paid_cents"] = int(item.paid_cents)
            if step == "denied" and item.denial_code:
                set_fields["last_denial_code"] = item.denial_code
            await db.claims.update_one(
                {"id": claim["id"], "tenant_id": ctx.tenant_id},
                {"$set": set_fields,
                 "$push": {"history": {
                     "at": now, "by": actor["id"],
                     "action": "remit_posted",
                     "remittance_id": remit_id,
                     "outcome": outcome,
                     "paid_cents": int(item.paid_cents),
                     "contractual_cents": int(item.contractual_cents),
                     "denied_cents": int(item.denied_cents),
                     "from_status": current, "to_status": new_status,
                 }}},
            )
            current = new_status

    return {
        "remittance_id": remit_id,
        "payment_id": pay_id,
        "allocation_count": len(alloc_docs),
        "adjustment_count": len(adj_docs),
        "denial_count": len(denial_docs),
        "claim_row_count": len(remit_claim_docs),
        "line_row_count": len(remit_line_docs),
        "recomputed_invoices": sorted(invoice_ids_to_recompute),
    }
