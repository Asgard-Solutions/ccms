"""
services/reports/builtin_analytics.py — higher-order analytical reports.

These reports sit a tier above the raw "Claims" / "Denials" tables
(in `builtin.py`) — they pre-aggregate claim + remittance + denial
data into planning / QBR shapes the frontend can chart:

  * Payer mix — one row per payer with claim count, billed, paid,
    outstanding, denial count, denial rate, average days-to-pay.
    (Closes the xlsx gap `Reporting & Analytics → Payer analysis`.)

  * Denial heat map — 2D grid of denial_category × service-month,
    surfaced as `aggregates.matrix` so the UI can render a heatmap
    without doing the pivot client-side.
    (Closes the xlsx gap `Reporting & Analytics → Denial heat map`.)

Both reports are `Financial` and inherit `reporting.read_financial`.
PHI is never surfaced — output is payer-level aggregation.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.tenancy import tenant_db
from core.tenant_scope import scoped_filter
from services.reports.definitions import (
    Column,
    Filter,
    QueryContext,
    ReportDefinition,
    RunResult,
    SortOption,
    register,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _tenant_scope(qc: QueryContext) -> dict:
    q = scoped_filter({}, qc.tenant)
    return {} if q.get("__deny__") else q


def _date_range(q: dict, filters: dict, field: str) -> None:
    rng: dict[str, Any] = {}
    if filters.get("from"):
        rng["$gte"] = filters["from"]
    if filters.get("to"):
        rng["$lte"] = filters["to"]
    if rng:
        q[field] = rng


def _month_key(ts: Any) -> str | None:
    """Return a `YYYY-MM` bucket from an ISO date/datetime string."""
    if not ts:
        return None
    s = str(ts)
    if len(s) >= 7 and s[4] == "-":
        return s[:7]
    return None


def _safe_days_between(a: str | None, b: str | None) -> int | None:
    if not a or not b:
        return None
    try:
        d1 = datetime.fromisoformat(str(a).replace("Z", "+00:00"))
        d2 = datetime.fromisoformat(str(b).replace("Z", "+00:00"))
    except ValueError:
        return None
    if d1.tzinfo is None:
        d1 = d1.replace(tzinfo=timezone.utc)
    if d2.tzinfo is None:
        d2 = d2.replace(tzinfo=timezone.utc)
    delta = (d2 - d1).days
    return delta if delta >= 0 else None


# ---------------------------------------------------------------------------
# 1) Payer mix
# ---------------------------------------------------------------------------
async def _run_payer_mix(qc: QueryContext) -> RunResult:
    """Aggregate claim + denial + remittance data per payer.

    The runner loads claims scoped to the tenant and a sibling payer
    catalog lookup for names / payer_type. Everything below is a
    single Mongo aggregation + two lookups (denials and remittance
    posting timestamps) so the report stays sub-second even on
    thousands of claims.
    """
    q = _tenant_scope(qc)
    _date_range(q, qc.filters, "service_date_from")
    if qc.filters.get("payer_type"):
        # Filter by payer type requires the payer catalog join.
        db = tenant_db(qc.tenant.tenant_id)
        matching_ids = [
            p["id"] async for p in db.billing_payers.find(
                {"tenant_id": qc.tenant.tenant_id,
                 "payer_type": qc.filters["payer_type"]},
                {"_id": 0, "id": 1},
            )
        ]
        if not matching_ids:
            return RunResult(rows=[], total=0)
        q["payer_id"] = {"$in": matching_ids}

    db = tenant_db(qc.tenant.tenant_id)
    # Per-payer claim roll-up
    pipeline = [
        {"$match": q},
        {"$group": {
            "_id": "$payer_id",
            "claim_count":   {"$sum": 1},
            "billed_cents":  {"$sum": {"$ifNull": ["$billed_cents", 0]}},
            "paid_cents":    {"$sum": {"$ifNull": ["$paid_cents", 0]}},
            "denied_count":  {"$sum": {"$cond": [
                {"$in": ["$status",
                         ["denied", "appealed", "partially_paid"]]}, 1, 0]}},
            "paid_count":    {"$sum": {"$cond": [
                {"$in": ["$status", ["paid", "partially_paid"]]}, 1, 0]}},
            "service_dates": {"$push": "$service_date_from"},
            "submitted_at":  {"$push": "$submitted_at"},
            "accepted_at":   {"$push": "$accepted_at"},
        }},
    ]
    raw_rollups = [row async for row in db.claims.aggregate(pipeline)]

    # Sidecar: per-payer denial amount (from denial_work_items).
    denial_pipeline = [
        {"$match": {"tenant_id": qc.tenant.tenant_id}},
        {"$lookup": {
            "from": "claims", "localField": "claim_id",
            "foreignField": "id", "as": "claim",
        }},
        {"$unwind": "$claim"},
        {"$group": {
            "_id": "$claim.payer_id",
            "denial_amount_cents": {"$sum": "$amount_cents"},
        }},
    ]
    denial_by_payer = {
        r["_id"]: r["denial_amount_cents"]
        async for r in db.denial_work_items.aggregate(denial_pipeline)
    }

    # Load payer name catalog in one shot — bounded to our rollup keys.
    payer_ids = [r["_id"] for r in raw_rollups if r["_id"]]
    payer_lookup: dict[str, dict] = {}
    if payer_ids:
        async for p in db.billing_payers.find(
            {"tenant_id": qc.tenant.tenant_id,
             "id": {"$in": payer_ids}},
            {"_id": 0, "id": 1, "name": 1, "payer_type": 1,
             "electronic_payer_id": 1},
        ):
            payer_lookup[p["id"]] = p

    # Turn each rollup into a report row.
    rows: list[dict] = []
    for r in raw_rollups:
        payer = payer_lookup.get(r["_id"], {})
        # Avg days-to-pay — computed from submitted→accepted pairs.
        day_samples: list[int] = []
        for sub, acc in zip(r["submitted_at"], r["accepted_at"]):
            if sub and acc:
                d = _safe_days_between(sub, acc)
                if d is not None:
                    day_samples.append(d)
        avg_days = (
            round(sum(day_samples) / len(day_samples))
            if day_samples else None
        )
        claims = r["claim_count"]
        denied = r["denied_count"]
        billed = r["billed_cents"]
        paid = r["paid_cents"]
        denial_rate = (
            round(denied / claims * 1000) / 10 if claims else 0.0
        )  # 1-decimal percent
        collection_rate = (
            round(paid / billed * 1000) / 10 if billed else 0.0
        )
        rows.append({
            "payer_id": r["_id"],
            "payer_name": payer.get("name") or "(unknown payer)",
            "payer_type": payer.get("payer_type") or "unspecified",
            "electronic_payer_id": payer.get("electronic_payer_id"),
            "claim_count": claims,
            "paid_count": r["paid_count"],
            "denied_count": denied,
            "billed_cents": billed,
            "paid_cents": paid,
            "outstanding_cents": max(0, billed - paid),
            "denial_amount_cents": denial_by_payer.get(r["_id"], 0),
            "denial_rate_pct": denial_rate,
            "collection_rate_pct": collection_rate,
            "avg_days_to_pay": avg_days,
        })

    # Sort + paginate.
    sort_key = qc.sort or "billed_cents"
    if sort_key == "payer_name":
        rows.sort(key=lambda x: (x.get("payer_name") or "").lower(),
                  reverse=(qc.sort_dir == "desc"))
    else:
        rows.sort(key=lambda x: x.get(sort_key) or 0,
                  reverse=(qc.sort_dir == "desc"))

    total = len(rows)
    start = max(0, (qc.page - 1) * qc.page_size)
    page = rows[start:start + qc.page_size]

    # Totals strip for the UI header.
    totals = {
        "claim_count": sum(r["claim_count"] for r in rows),
        "billed_cents": sum(r["billed_cents"] for r in rows),
        "paid_cents": sum(r["paid_cents"] for r in rows),
        "outstanding_cents": sum(r["outstanding_cents"] for r in rows),
        "denial_rate_pct": (
            round(sum(r["denied_count"] for r in rows) /
                  max(1, sum(r["claim_count"] for r in rows)) * 1000) / 10
        ),
        "collection_rate_pct": (
            round(sum(r["paid_cents"] for r in rows) /
                  max(1, sum(r["billed_cents"] for r in rows)) * 1000) / 10
        ),
    }
    return RunResult(
        rows=page, total=total, aggregates={"totals": totals},
    )


_PAYER_MIX_DEF = register(ReportDefinition(
    name="payer_mix",
    title="Payer mix",
    category="Financial",
    description=(
        "Per-payer claim volume, billed / paid / outstanding balances, "
        "denial rate and average days-to-pay. Closes the gap on "
        "payer analysis for QBR / planning."
    ),
    required_permission=("reporting", "read_financial"),
    columns=[
        Column("payer_name", "Payer"),
        Column("payer_type", "Type", "enum"),
        Column("claim_count", "Claims", "integer", align="right"),
        Column("paid_count", "Paid claims", "integer", align="right",
               hidden_by_default=True),
        Column("denied_count", "Denied claims", "integer", align="right"),
        Column("billed_cents", "Billed", "currency", align="right"),
        Column("paid_cents", "Paid", "currency", align="right"),
        Column("outstanding_cents", "Outstanding", "currency",
               align="right"),
        Column("denial_rate_pct", "Denial %", "number", align="right"),
        Column("collection_rate_pct", "Collection %", "number",
               align="right"),
        Column("avg_days_to_pay", "Avg days-to-pay", "integer",
               align="right"),
        Column("denial_amount_cents", "Denied $", "currency",
               align="right", hidden_by_default=True),
        Column("electronic_payer_id", "EDI ID", hidden_by_default=True),
    ],
    default_columns=[
        "payer_name", "payer_type", "claim_count", "billed_cents",
        "paid_cents", "outstanding_cents", "denial_rate_pct",
        "collection_rate_pct", "avg_days_to_pay",
    ],
    filters=[
        Filter("from", "Service date from", "date_range"),
        Filter("to", "Service date to", "date_range"),
        Filter("payer_type", "Payer type", "enum", options=[
            {"value": "commercial", "label": "Commercial"},
            {"value": "medicare", "label": "Medicare"},
            {"value": "medicaid", "label": "Medicaid"},
            {"value": "workers_comp", "label": "Workers' comp"},
            {"value": "auto", "label": "Auto / PIP"},
            {"value": "self_pay", "label": "Self-pay"},
        ]),
    ],
    sort_options=[
        SortOption("billed_cents", "Billed"),
        SortOption("paid_cents", "Paid"),
        SortOption("outstanding_cents", "Outstanding"),
        SortOption("denial_rate_pct", "Denial %"),
        SortOption("claim_count", "Claims"),
        SortOption("payer_name", "Payer"),
    ],
    default_sort="billed_cents", default_sort_dir="desc",
    runner=_run_payer_mix,
))


# ---------------------------------------------------------------------------
# 2) Denial heat map
# ---------------------------------------------------------------------------
async def _run_denial_heat_map(qc: QueryContext) -> RunResult:
    """Two-dimensional grid of denial_category × service-month.

    Data source precedence:
      1. `denial_work_items` — the explicit triage workflow rows
         (preferred when they exist because they carry open/resolved
         status + assigned operator).
      2. `claims` — fall back to any claim in a denied-ish status
         (`denied`, `partially_paid`, `appealed`, or any claim with a
         `last_denial_code` set). This keeps the heat map populated
         even for tenants that haven't spun up the denials workflow.

    The detail `rows` are month-major: one row per (month, category)
    with count + amount. The `aggregates.matrix` field carries the
    pre-pivoted shape `{"rows": [categories], "cols": [months],
    "cells": [[count, amount_cents], ...]}` so the frontend can render
    a heat-map in a single pass.
    """
    db = tenant_db(qc.tenant.tenant_id)

    payer_filter_id = qc.filters.get("payer_id") or None

    # Bucket: category -> month -> cell dict
    cat_index: dict[str, dict[str, dict]] = {}
    months: set[str] = set()

    def _bump(cat: str, month: str, *,
              count: int = 1, amount: int = 0,
              code: str | None = None, status: str | None = None) -> None:
        if not month or len(month) < 7:
            return
        months.add(month)
        cell = cat_index.setdefault(cat, {}).setdefault(
            month, {"count": 0, "amount_cents": 0, "codes": set(),
                    "open": 0, "resolved": 0},
        )
        cell["count"] += count
        cell["amount_cents"] += amount
        if code:
            cell["codes"].add(code)
        if status in ("resolved", "closed"):
            cell["resolved"] += count
        else:
            cell["open"] += count

    # ---- Source 1: denial_work_items (richer — status + assignee) ----
    work_match: dict[str, Any] = {"tenant_id": qc.tenant.tenant_id}
    _date_range(work_match, qc.filters, "opened_at")
    work_pipeline: list[dict] = [
        {"$match": work_match},
        {"$lookup": {
            "from": "claims", "localField": "claim_id",
            "foreignField": "id", "as": "claim",
        }},
        {"$unwind": "$claim"},
    ]
    if payer_filter_id:
        work_pipeline.append(
            {"$match": {"claim.payer_id": payer_filter_id}},
        )
    work_pipeline.append({"$project": {
        "_id": 0,
        "category": {"$ifNull": ["$denial_category", "Uncategorised"]},
        "code": "$denial_code",
        "amount_cents": {"$ifNull": ["$amount_cents", 0]},
        "month": {"$substrBytes": [
            {"$ifNull": ["$claim.service_date_from", "$opened_at"]},
            0, 7,
        ]},
        "status": "$status",
        "claim_id": "$claim_id",
    }})
    covered_claim_ids: set[str] = set()
    async for r in db.denial_work_items.aggregate(work_pipeline):
        _bump(
            r["category"] or "Uncategorised",
            r.get("month") or "",
            amount=r["amount_cents"],
            code=r.get("code"),
            status=r.get("status"),
        )
        if r.get("claim_id"):
            covered_claim_ids.add(r["claim_id"])

    # ---- Source 2: claims without a work-item row (fallback) ----
    claim_match: dict[str, Any] = dict(_tenant_scope(qc))
    _date_range(claim_match, qc.filters, "service_date_from")
    if payer_filter_id:
        claim_match["payer_id"] = payer_filter_id
    claim_match["$or"] = [
        {"status": {"$in": ["denied", "appealed", "partially_paid"]}},
        {"last_denial_code": {"$nin": [None, ""]}},
    ]
    async for c in db.claims.find(claim_match, {
        "_id": 0, "id": 1, "billed_cents": 1, "paid_cents": 1,
        "last_denial_code": 1, "service_date_from": 1, "status": 1,
    }):
        if c["id"] in covered_claim_ids:
            continue
        cat = _classify_denial_code(c.get("last_denial_code"))
        month = _month_key(c.get("service_date_from"))
        amount = max(
            0, (c.get("billed_cents") or 0) - (c.get("paid_cents") or 0),
        )
        _bump(
            cat, month or "",
            amount=amount,
            code=c.get("last_denial_code"),
            # "open" by default — no work-item means nobody triaged.
            status="open",
        )

    # ---- Build matrix + flat rows ----
    sorted_months = sorted(months)
    sorted_cats = sorted(cat_index.keys())
    detail_rows: list[dict] = []
    max_count = 0
    max_amount = 0
    cells: list[list[dict]] = []
    for cat in sorted_cats:
        row_cells: list[dict] = []
        for month in sorted_months:
            cell = cat_index.get(cat, {}).get(month, {})
            count = cell.get("count", 0)
            amount = cell.get("amount_cents", 0)
            max_count = max(max_count, count)
            max_amount = max(max_amount, amount)
            row_cells.append({
                "count": count,
                "amount_cents": amount,
                "open": cell.get("open", 0),
                "resolved": cell.get("resolved", 0),
                "codes": sorted(cell.get("codes", [])),
            })
            if count > 0:
                detail_rows.append({
                    "month": month,
                    "category": cat,
                    "count": count,
                    "amount_cents": amount,
                    "open": cell.get("open", 0),
                    "resolved": cell.get("resolved", 0),
                    "codes": ", ".join(sorted(cell.get("codes", []))),
                })
        cells.append(row_cells)

    # Sort detail rows.
    sort_key = qc.sort or "count"
    reverse = qc.sort_dir == "desc"
    detail_rows.sort(key=lambda x: x.get(sort_key) or 0, reverse=reverse)

    # Paginate.
    total = len(detail_rows)
    start = max(0, (qc.page - 1) * qc.page_size)
    page = detail_rows[start:start + qc.page_size]

    return RunResult(
        rows=page, total=total, aggregates={
            "matrix": {
                "rows": sorted_cats,
                "cols": sorted_months,
                "cells": cells,
                "max_count": max_count,
                "max_amount_cents": max_amount,
            },
            "totals": {
                "denial_count": sum(r["count"] for r in detail_rows),
                "denial_amount_cents": sum(
                    r["amount_cents"] for r in detail_rows),
                "categories": len(sorted_cats),
                "months": len(sorted_months),
            },
        },
    )


# ---------------------------------------------------------------------------
# Denial code → category classifier. Covers the CARC buckets that the
# Riverbend seed uses (CO, PR, OA, PI) plus the usual suspects. Keeps
# the heat map rows legible without needing payer remittance parsers.
# ---------------------------------------------------------------------------
_DENIAL_CATEGORY_BY_CODE: dict[str, str] = {
    # Eligibility / coverage
    "CO-11":  "Eligibility / coverage",
    "CO-27":  "Eligibility / coverage",
    "CO-29":  "Timely filing",
    # Authorization
    "CO-197": "Authorization",
    "CO-198": "Authorization",
    # Bundling / CCI
    "CO-97":  "Bundling / CCI",
    "CO-B15": "Bundling / CCI",
    # Coding / medical necessity
    "CO-16":  "Coding / documentation",
    "CO-50":  "Medical necessity",
    # Coordination of benefits
    "CO-22":  "COB / primary payer",
    # Patient responsibility
    "PR-1":   "Patient deductible",
    "PR-2":   "Patient coinsurance",
    "PR-3":   "Patient copay",
    "PR-45":  "Allowed amount reduction",
    # Contractual
    "OA-23":  "Other adjudication",
    "PI-45":  "Payer contract",
}


def _classify_denial_code(code: str | None) -> str:
    if not code:
        return "Uncategorised"
    up = code.strip().upper()
    if up in _DENIAL_CATEGORY_BY_CODE:
        return _DENIAL_CATEGORY_BY_CODE[up]
    # Prefix fallback — `CO-*` → "Contractual / denial" bucket.
    if up.startswith("CO"):
        return "Contractual (CO)"
    if up.startswith("PR"):
        return "Patient responsibility (PR)"
    if up.startswith("OA"):
        return "Other adjustments (OA)"
    if up.startswith("PI"):
        return "Payer initiated (PI)"
    return "Uncategorised"


_DENIAL_HEAT_MAP_DEF = register(ReportDefinition(
    name="denial_heat_map",
    title="Denial heat map",
    category="Financial",
    description=(
        "Denial volume and dollar amount, pivoted as a heat map of "
        "category × service-month. The flat row view lists every "
        "(month, category) pair; the chart view overlays the pivot "
        "matrix so hot spots (codes, categories, months) surface in "
        "one glance."
    ),
    required_permission=("reporting", "read_financial"),
    columns=[
        Column("month", "Month"),
        Column("category", "Category"),
        Column("count", "Count", "integer", align="right"),
        Column("amount_cents", "Amount", "currency", align="right"),
        Column("open", "Open", "integer", align="right"),
        Column("resolved", "Resolved", "integer", align="right"),
        Column("codes", "Denial codes", hidden_by_default=True),
    ],
    default_columns=[
        "month", "category", "count", "amount_cents", "open", "resolved",
    ],
    filters=[
        Filter("from", "Denial opened from", "date_range"),
        Filter("to", "Denial opened to", "date_range"),
        Filter("payer_id", "Payer", "string",
               placeholder="Payer id (optional)"),
    ],
    sort_options=[
        SortOption("count", "Count"),
        SortOption("amount_cents", "Amount"),
        SortOption("month", "Month"),
        SortOption("category", "Category"),
    ],
    default_sort="count", default_sort_dir="desc",
    runner=_run_denial_heat_map,
))


def registered_definitions():
    """Convenience for tests — returns the two analytics definitions
    added by this module."""
    return [_PAYER_MIX_DEF, _DENIAL_HEAT_MAP_DEF]
