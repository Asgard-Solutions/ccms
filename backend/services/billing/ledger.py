"""
Patient ledger — chronological union of all financial rows for one patient.

Rows emitted (each with a `kind`, `occurred_at`, `amount_cents`, and a
`balance_delta` indicating how the row affects the patient's running
balance):

  kind            | balance_delta sign       | source collection
  ---------------------------------------------------------------
  charge          | +  (increases balance)   | invoices (issued or later)
  payment         | -  (decreases balance)   | payment_allocations joined to payments
  adjustment      | -  (decreases balance)   | billing_adjustments
  refund          | +  (increases balance)   | refunds joined to payments
  invoice_void    | -  (zeros out charge)    | invoices (voided)

`amount_cents` is always positive; the sign is carried by `balance_delta`.

This is a read-only aggregate — no writes here. The canonical stored
state lives on `invoices.balance_cents` and `payments.allocated_cents`;
the ledger simply reflects the individual events that produced those
numbers.
"""
from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase


def _public(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k != "_id"}


async def build_patient_ledger(
    db: AsyncIOMotorDatabase,
    *,
    tenant_id: str,
    patient_id: str,
) -> dict[str, Any]:
    """Return `{rows: [...], running_balance_cents, totals: {...}}`.

    Rows are sorted oldest-first so the UI can cumulatively compute the
    running balance on the fly. We also precompute the final running
    balance server-side so callers don't have to.
    """
    invoice_ids: list[str] = []
    rows: list[dict] = []
    totals = {
        "charges_cents": 0,
        "payments_cents": 0,
        "adjustments_cents": 0,
        "refunds_cents": 0,
        "voided_charges_cents": 0,
    }

    # --- Invoices (each issued invoice becomes a `charge` row) ---
    async for inv in db.invoices.find(
        {"tenant_id": tenant_id, "patient_id": patient_id},
        {"_id": 0},
    ):
        invoice_ids.append(inv["id"])
        # A "draft" invoice is NOT a charge yet — the patient is not
        # financially responsible for it until it's issued. But we still
        # include draft rows for operator visibility, flagged as pending.
        is_pending = inv["status"] == "draft"
        occurred_at = inv.get("issued_at") or inv.get("created_at")
        if inv["status"] == "void":
            # Void invoices emit a zero-delta "charge" row plus a
            # `invoice_void` row at update time so the timeline tells the
            # full story.
            rows.append({
                "kind": "charge",
                "id": f"inv-{inv['id']}",
                "invoice_id": inv["id"],
                "occurred_at": occurred_at,
                "amount_cents": inv["total_cents"],
                "balance_delta": 0,          # canceled by the void row
                "status": inv["status"],
                "description": f"Invoice {inv['id'][:8]} (voided)",
                "meta": {"invoice_status": inv["status"]},
            })
            totals["voided_charges_cents"] += inv["total_cents"]
            rows.append({
                "kind": "invoice_void",
                "id": f"void-{inv['id']}",
                "invoice_id": inv["id"],
                "occurred_at": inv.get("updated_at") or occurred_at,
                "amount_cents": inv["total_cents"],
                "balance_delta": -inv["total_cents"],
                "status": "void",
                "description": f"Invoice {inv['id'][:8]} voided",
                "meta": {},
            })
            continue

        rows.append({
            "kind": "charge",
            "id": f"inv-{inv['id']}",
            "invoice_id": inv["id"],
            "occurred_at": occurred_at,
            "amount_cents": inv["total_cents"],
            # Drafts don't move the balance yet; that happens on "issued".
            "balance_delta": 0 if is_pending else inv["total_cents"],
            "status": inv["status"],
            "description": f"Invoice {inv['id'][:8]}",
            "meta": {
                "invoice_status": inv["status"],
                "balance_cents": inv["balance_cents"],
            },
        })
        if not is_pending:
            totals["charges_cents"] += inv["total_cents"]

    # --- Payments (one row per allocation; unallocated payments get a
    #     synthetic row with no invoice_id) ---
    #
    # We load all payments for the patient first, then pull every
    # allocation tied to those payments so the row carries both the
    # payment context (method, status) and the allocation amount.
    payment_map: dict[str, dict] = {}
    async for p in db.payments.find(
        {"tenant_id": tenant_id, "patient_id": patient_id},
        {"_id": 0},
    ):
        payment_map[p["id"]] = p

    if payment_map:
        pids = list(payment_map.keys())
        async for alloc in db.payment_allocations.find(
            {"tenant_id": tenant_id, "payment_id": {"$in": pids}},
            {"_id": 0},
        ):
            p = payment_map[alloc["payment_id"]]
            # A voided/failed payment's allocations no longer move money.
            paid = p["status"] not in ("void", "failed")
            rows.append({
                "kind": "payment",
                "id": f"alloc-{alloc['id']}",
                "payment_id": p["id"],
                "invoice_id": alloc["invoice_id"],
                "occurred_at": p.get("received_at") or p.get("created_at"),
                "amount_cents": alloc["amount_cents"],
                "balance_delta": -alloc["amount_cents"] if paid else 0,
                "status": p["status"],
                "description": f"Payment {p['method']} ({p['id'][:8]})",
                "meta": {"method": p["method"], "reference": p.get("reference")},
            })
            if paid:
                totals["payments_cents"] += alloc["amount_cents"]

        # Patient credit: any payment with unallocated cents shows up so
        # operators can see the balance they could apply.
        for p in payment_map.values():
            unalloc = p["amount_cents"] - p.get("allocated_cents", 0)
            if unalloc > 0 and p["status"] not in ("void", "failed"):
                rows.append({
                    "kind": "credit",
                    "id": f"credit-{p['id']}",
                    "payment_id": p["id"],
                    "invoice_id": None,
                    "occurred_at": p.get("received_at") or p.get("created_at"),
                    "amount_cents": unalloc,
                    "balance_delta": -unalloc,
                    "status": p["status"],
                    "description": f"Unapplied credit ({p['id'][:8]})",
                    "meta": {"method": p["method"]},
                })
                totals["payments_cents"] += unalloc

    # --- Adjustments ---
    if invoice_ids:
        async for adj in db.billing_adjustments.find(
            {"tenant_id": tenant_id, "invoice_id": {"$in": invoice_ids}},
            {"_id": 0},
        ):
            rows.append({
                "kind": "adjustment",
                "id": f"adj-{adj['id']}",
                "invoice_id": adj["invoice_id"],
                "occurred_at": adj["created_at"],
                "amount_cents": adj["amount_cents"],
                "balance_delta": -adj["amount_cents"],
                "status": adj["kind"],
                "description": f"{adj['kind'].replace('_', ' ').title()}: {adj['reason']}",
                "meta": {"kind": adj["kind"], "reason": adj["reason"]},
            })
            totals["adjustments_cents"] += adj["amount_cents"]

    # --- Refunds (reverse payment rows) ---
    if payment_map:
        async for rf in db.refunds.find(
            {"tenant_id": tenant_id, "payment_id": {"$in": list(payment_map.keys())}},
            {"_id": 0},
        ):
            # Only refunds in `processed` state affect the balance; a
            # pending refund is visible but not yet money-moving.
            processed = rf.get("status") == "processed"
            rows.append({
                "kind": "refund",
                "id": f"refund-{rf['id']}",
                "payment_id": rf["payment_id"],
                "invoice_id": None,
                "occurred_at": rf.get("processed_at") or rf["created_at"],
                "amount_cents": rf["amount_cents"],
                "balance_delta": rf["amount_cents"] if processed else 0,
                "status": rf.get("status", "pending"),
                "description": f"Refund: {rf['reason']}",
                "meta": {"reason": rf["reason"]},
            })
            if processed:
                totals["refunds_cents"] += rf["amount_cents"]

    # Sort chronologically (stable — oldest first).
    rows.sort(key=lambda r: (r.get("occurred_at") or "", r["id"]))

    running = 0
    for r in rows:
        running += r["balance_delta"]
        r["running_balance_cents"] = running

    return {
        "patient_id": patient_id,
        "rows": rows,
        "running_balance_cents": running,
        "totals": totals,
    }
