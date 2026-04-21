"""
Built-in reports — every report defined here is registered at import time.

These reports are intentionally kept small (~30 lines each) and reuse the
same idioms:
  1. Resolve the tenant-scoped mongo filter via `scoped_filter` or a local
     helper in this file.
  2. Run a paged query, return rows in a serialisation-safe shape (no
     ObjectId, no raw secrets).
  3. Compute `total` separately so the frontend can paginate honestly.

A few reports hydrate a tiny lookup (provider names, patient names) for
human-readable output. These lookups are tenant-scoped and bounded to the
row set of the current page, so they stay O(page_size).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from core.db import get_db_read
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
    resolve_sort,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _base_filter(qc: QueryContext, *, location_scoped: bool = False) -> dict:
    q = scoped_filter({}, qc.tenant, location_scoped=location_scoped)
    if qc.filters.get("location_ids"):
        # User explicitly narrowed to a subset — must be inside allowed.
        loc_ids = qc.filters["location_ids"]
        if not qc.tenant.is_platform_admin and not qc.tenant.tenant_scope_all:
            loc_ids = [lid for lid in loc_ids if lid in qc.tenant.allowed_location_ids]
            if not loc_ids:
                return {"__deny__": True}
        q["location_id"] = {"$in": loc_ids}
    return q


def _date_range_filter(q: dict, filters: dict, field: str, from_key: str = "from", to_key: str = "to") -> None:
    if filters.get(from_key) or filters.get(to_key):
        r: dict = {}
        if filters.get(from_key):
            r["$gte"] = filters[from_key]
        if filters.get(to_key):
            r["$lte"] = filters[to_key]
        q[field] = r


def _sort_spec(qc: QueryContext, definition: ReportDefinition) -> list[tuple[str, int]]:
    key = resolve_sort(definition, qc.sort)
    direction = -1 if qc.sort_dir == "desc" else 1
    return [(key, direction)]


async def _hydrate_users(tenant_id: str | None, ids: list[str]) -> dict[str, dict]:
    if not ids:
        return {}
    db = tenant_db(tenant_id)
    cur = db.users.find({"id": {"$in": ids}}, {"_id": 0, "id": 1, "name": 1, "role": 1})
    return {u["id"]: u async for u in cur}


async def _hydrate_patients(tenant_id: str | None, ids: list[str]) -> dict[str, dict]:
    if not ids:
        return {}
    db = tenant_db(tenant_id)
    cur = db.patients.find(
        {"id": {"$in": ids}},
        {"_id": 0, "id": 1, "first_name": 1, "last_name": 1, "phone": 1},
    )
    return {p["id"]: p async for p in cur}


async def _hydrate_locations(tenant_id: str | None, ids: list[str]) -> dict[str, str]:
    if not ids:
        return {}
    db = tenant_db(tenant_id)
    cur = db.locations.find({"id": {"$in": ids}}, {"_id": 0, "id": 1, "name": 1})
    return {loc["id"]: loc["name"] async for loc in cur}


async def _hydrate_payers(tenant_id: str | None, ids: list[str]) -> dict[str, str]:
    if not ids:
        return {}
    db = tenant_db(tenant_id)
    cur = db.payers.find({"id": {"$in": ids}}, {"_id": 0, "id": 1, "name": 1})
    return {p["id"]: p["name"] async for p in cur}


def _paged(rows: list[dict], page: int, page_size: int) -> list[dict]:
    start = max(0, (page - 1) * page_size)
    return rows[start:start + page_size]


def STATUS_OPTS(values):
    return [{"value": v, "label": v.replace("_", " ").title()} for v in values]


# ---------------------------------------------------------------------------
# 1. Appointments list (Operational)
# ---------------------------------------------------------------------------

async def _run_appointments_list(qc: QueryContext) -> RunResult:
    q = _base_filter(qc, location_scoped=True)
    if q.get("__deny__"):
        return RunResult(rows=[], total=0)

    if qc.filters.get("status"):
        q["status"] = qc.filters["status"]
    if qc.filters.get("provider_id"):
        q["provider_id"] = qc.filters["provider_id"]
    _date_range_filter(q, qc.filters, "start_time")

    db = tenant_db(qc.tenant.tenant_id)
    total = await db.appointments.count_documents(q)
    cursor = db.appointments.find(q, {"_id": 0}).sort(_sort_spec(qc, _APPTS_DEF))
    cursor = cursor.skip(max(0, (qc.page - 1) * qc.page_size)).limit(qc.page_size)
    raw = [a async for a in cursor]

    provs = await _hydrate_users(qc.tenant.tenant_id, list({a["provider_id"] for a in raw}))
    pats = await _hydrate_patients(qc.tenant.tenant_id, list({a["patient_id"] for a in raw}))
    locs = await _hydrate_locations(qc.tenant.tenant_id, [a["location_id"] for a in raw if a.get("location_id")])

    rows = []
    for a in raw:
        p = pats.get(a["patient_id"], {})
        rows.append({
            "id": a.get("id"),
            "start_time": a.get("start_time"),
            "end_time": a.get("end_time"),
            "duration_min": _duration_min(a.get("start_time"), a.get("end_time")),
            "status": a.get("status"),
            "reason": a.get("reason"),
            "provider_name": (provs.get(a["provider_id"]) or {}).get("name"),
            "patient_id": a.get("patient_id"),
            "patient_name": f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() or None,
            "patient_phone": p.get("phone"),
            "location_name": locs.get(a.get("location_id", ""), None),
        })

    # Aggregate — counts by status for the summary header
    agg_cur = db.appointments.aggregate([
        {"$match": q},
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
    ])
    by_status = {r["_id"]: r["count"] async for r in agg_cur}

    return RunResult(rows=rows, total=total, aggregates={"by_status": by_status})


def _duration_min(start: str | None, end: str | None) -> int | None:
    if not start or not end:
        return None
    try:
        s = datetime.fromisoformat(start.replace("Z", "+00:00"))
        e = datetime.fromisoformat(end.replace("Z", "+00:00"))
        return int((e - s).total_seconds() // 60)
    except ValueError:
        return None


_APPTS_DEF = register(ReportDefinition(
    name="appointments_list",
    title="Appointments",
    category="Operational",
    description="Scheduled, completed, and cancelled appointments across locations and providers.",
    required_permission=("reporting", "read"),
    columns=[
        Column("start_time", "Start", "datetime"),
        Column("end_time", "End", "datetime", hidden_by_default=True),
        Column("duration_min", "Duration (min)", "integer", align="right"),
        Column("status", "Status", "enum"),
        Column("patient_name", "Patient", phi=True),
        Column("patient_phone", "Phone", phi=True, hidden_by_default=True),
        Column("provider_name", "Provider"),
        Column("reason", "Reason"),
        Column("location_name", "Location"),
    ],
    default_columns=["start_time", "status", "patient_name", "provider_name", "reason", "location_name"],
    filters=[
        Filter("from", "From date", "date_range"),
        Filter("to", "To date", "date_range"),
        Filter("status", "Status", "enum",
               options=STATUS_OPTS(["scheduled", "completed", "cancelled"])),
        Filter("provider_id", "Provider", "string", placeholder="Provider ID"),
    ],
    sort_options=[SortOption("start_time", "Start time"), SortOption("status", "Status")],
    default_sort="start_time", default_sort_dir="desc",
    contains_phi=True,
    runner=_run_appointments_list,
))


# ---------------------------------------------------------------------------
# 2. Provider productivity (Operational)
# ---------------------------------------------------------------------------

async def _run_provider_productivity(qc: QueryContext) -> RunResult:
    q = _base_filter(qc, location_scoped=True)
    if q.get("__deny__"):
        return RunResult(rows=[], total=0)
    _date_range_filter(q, qc.filters, "start_time")

    db = tenant_db(qc.tenant.tenant_id)
    pipeline = [
        {"$match": q},
        {"$group": {
            "_id": "$provider_id",
            "scheduled": {"$sum": {"$cond": [{"$eq": ["$status", "scheduled"]}, 1, 0]}},
            "completed": {"$sum": {"$cond": [{"$eq": ["$status", "completed"]}, 1, 0]}},
            "cancelled": {"$sum": {"$cond": [{"$eq": ["$status", "cancelled"]}, 1, 0]}},
            "total": {"$sum": 1},
        }},
    ]
    raw = [r async for r in db.appointments.aggregate(pipeline) if r["_id"]]
    provs = await _hydrate_users(qc.tenant.tenant_id, [r["_id"] for r in raw])

    rows = [{
        "provider_id": r["_id"],
        "provider_name": (provs.get(r["_id"]) or {}).get("name"),
        "scheduled": r["scheduled"],
        "completed": r["completed"],
        "cancelled": r["cancelled"],
        "total": r["total"],
        "completion_rate": round((r["completed"] / r["total"]) * 100, 1) if r["total"] else 0,
    } for r in raw]

    # Sort in-memory (small list)
    sort_key = resolve_sort(_PROD_DEF, qc.sort)
    rev = qc.sort_dir == "desc"
    rows.sort(key=lambda x: (x.get(sort_key) or 0), reverse=rev)
    total = len(rows)
    return RunResult(rows=_paged(rows, qc.page, qc.page_size), total=total)


_PROD_DEF = register(ReportDefinition(
    name="provider_productivity",
    title="Provider productivity",
    category="Operational",
    description="Appointment volume and completion rate by provider.",
    required_permission=("reporting", "read"),
    columns=[
        Column("provider_name", "Provider"),
        Column("scheduled", "Scheduled", "integer", align="right"),
        Column("completed", "Completed", "integer", align="right"),
        Column("cancelled", "Cancelled", "integer", align="right"),
        Column("total", "Total", "integer", align="right"),
        Column("completion_rate", "Completion rate (%)", "number", align="right"),
    ],
    default_columns=["provider_name", "scheduled", "completed", "cancelled", "total", "completion_rate"],
    filters=[
        Filter("from", "From date", "date_range"),
        Filter("to", "To date", "date_range"),
    ],
    sort_options=[
        SortOption("total", "Total"), SortOption("completed", "Completed"),
        SortOption("completion_rate", "Completion rate"),
    ],
    default_sort="total", default_sort_dir="desc",
    runner=_run_provider_productivity,
))


# ---------------------------------------------------------------------------
# 3. Patient roster (Operational — PHI)
# ---------------------------------------------------------------------------

async def _run_patient_roster(qc: QueryContext) -> RunResult:
    q = _base_filter(qc, location_scoped=True)
    if q.get("__deny__"):
        return RunResult(rows=[], total=0)
    q.setdefault("status", {"$ne": "deleted"})
    if qc.filters.get("status"):
        q["status"] = qc.filters["status"]
    _date_range_filter(q, qc.filters, "created_at")

    db = tenant_db(qc.tenant.tenant_id)
    total = await db.patients.count_documents(q)
    cursor = db.patients.find(q, {
        "_id": 0, "id": 1, "first_name": 1, "last_name": 1, "phone": 1,
        "email": 1, "date_of_birth": 1, "status": 1, "location_id": 1,
        "created_at": 1, "sex": 1,
    }).sort(_sort_spec(qc, _ROSTER_DEF))
    cursor = cursor.skip(max(0, (qc.page - 1) * qc.page_size)).limit(qc.page_size)
    raw = [p async for p in cursor]
    locs = await _hydrate_locations(qc.tenant.tenant_id, [p.get("location_id") for p in raw])
    rows = [{
        "id": p.get("id"),
        "last_name": p.get("last_name"),
        "first_name": p.get("first_name"),
        "full_name": f"{p.get('first_name', '')} {p.get('last_name', '')}".strip(),
        "phone": p.get("phone"),
        "email": p.get("email"),
        "status": p.get("status"),
        "location_name": locs.get(p.get("location_id", ""), None),
        "created_at": p.get("created_at"),
    } for p in raw]
    return RunResult(rows=rows, total=total)


_ROSTER_DEF = register(ReportDefinition(
    name="patient_roster",
    title="Patient roster",
    category="Operational",
    description="Master patient list. Contains PHI — exports are password-protected.",
    required_permission=("reporting", "read_financial"),
    columns=[
        Column("full_name", "Name", phi=True),
        Column("last_name", "Last name", phi=True, hidden_by_default=True),
        Column("first_name", "First name", phi=True, hidden_by_default=True),
        Column("phone", "Phone", phi=True),
        Column("email", "Email", phi=True),
        Column("status", "Status", "enum"),
        Column("location_name", "Location"),
        Column("created_at", "Registered", "datetime"),
    ],
    default_columns=["full_name", "phone", "email", "status", "location_name", "created_at"],
    filters=[
        Filter("status", "Status", "enum", options=STATUS_OPTS(["active", "inactive"])),
        Filter("from", "Registered from", "date_range"),
        Filter("to", "Registered to", "date_range"),
    ],
    sort_options=[
        SortOption("last_name", "Last name"),
        SortOption("created_at", "Registered"),
    ],
    default_sort="created_at", default_sort_dir="desc",
    contains_phi=True,
    runner=_run_patient_roster,
))


# ---------------------------------------------------------------------------
# 4. Unsigned clinical notes (Clinical — PHI identifier only)
# ---------------------------------------------------------------------------

async def _run_unsigned_notes(qc: QueryContext) -> RunResult:
    q = scoped_filter({"status": {"$in": ["draft", "sign_ready"]}}, qc.tenant)
    if q.get("__deny__"):
        return RunResult(rows=[], total=0)
    db = tenant_db(qc.tenant.tenant_id)
    total = await db.clinical_follow_up_notes.count_documents(q)
    cursor = db.clinical_follow_up_notes.find(
        q, {"_id": 0, "id": 1, "patient_id": 1, "provider_id": 1, "status": 1,
            "date_of_service": 1, "encounter_id": 1, "created_at": 1}
    ).sort(_sort_spec(qc, _UNSIGNED_DEF))
    cursor = cursor.skip(max(0, (qc.page - 1) * qc.page_size)).limit(qc.page_size)
    raw = [n async for n in cursor]

    provs = await _hydrate_users(qc.tenant.tenant_id, list({n["provider_id"] for n in raw if n.get("provider_id")}))
    pats = await _hydrate_patients(qc.tenant.tenant_id, list({n["patient_id"] for n in raw}))

    now = datetime.now(timezone.utc)
    rows = []
    for n in raw:
        p = pats.get(n["patient_id"], {})
        created = n.get("created_at")
        age_days = None
        if created:
            try:
                c = datetime.fromisoformat(created.replace("Z", "+00:00"))
                age_days = (now - c).days
            except ValueError:
                age_days = None
        rows.append({
            "id": n.get("id"),
            "date_of_service": n.get("date_of_service"),
            "status": n.get("status"),
            "patient_name": f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() or None,
            "patient_id": n.get("patient_id"),
            "provider_name": (provs.get(n.get("provider_id", "")) or {}).get("name"),
            "age_days": age_days,
            "created_at": created,
        })
    return RunResult(rows=rows, total=total)


_UNSIGNED_DEF = register(ReportDefinition(
    name="unsigned_clinical_notes",
    title="Unsigned clinical notes",
    category="Clinical",
    description="Draft and sign-ready follow-up notes awaiting provider signature.",
    required_permission=("reporting", "read_clinical"),
    columns=[
        Column("date_of_service", "DOS", "date"),
        Column("status", "Status", "enum"),
        Column("patient_name", "Patient", phi=True),
        Column("provider_name", "Provider"),
        Column("age_days", "Age (days)", "integer", align="right"),
        Column("created_at", "Created", "datetime"),
    ],
    default_columns=["date_of_service", "status", "patient_name", "provider_name", "age_days"],
    filters=[],
    sort_options=[SortOption("created_at", "Created"), SortOption("date_of_service", "DOS")],
    default_sort="created_at", default_sort_dir="asc",
    contains_phi=True,
    runner=_run_unsigned_notes,
))


# ---------------------------------------------------------------------------
# 5. Claims list (Financial)
# ---------------------------------------------------------------------------

async def _run_claims_list(qc: QueryContext) -> RunResult:
    q = _base_filter(qc, location_scoped=True)
    if q.get("__deny__"):
        return RunResult(rows=[], total=0)
    if qc.filters.get("status"):
        q["status"] = qc.filters["status"]
    if qc.filters.get("payer_id"):
        q["payer_id"] = qc.filters["payer_id"]
    _date_range_filter(q, qc.filters, "service_date_from")

    db = tenant_db(qc.tenant.tenant_id)
    total = await db.claims.count_documents(q)
    cursor = db.claims.find(q, {"_id": 0}).sort(_sort_spec(qc, _CLAIMS_DEF))
    cursor = cursor.skip(max(0, (qc.page - 1) * qc.page_size)).limit(qc.page_size)
    raw = [c async for c in cursor]

    pats = await _hydrate_patients(qc.tenant.tenant_id, list({c["patient_id"] for c in raw}))
    payers = await _hydrate_payers(qc.tenant.tenant_id, list({c["payer_id"] for c in raw}))

    rows = []
    for c in raw:
        p = pats.get(c["patient_id"], {})
        rows.append({
            "id": c.get("id"),
            "status": c.get("status"),
            "service_date_from": c.get("service_date_from"),
            "service_date_to": c.get("service_date_to"),
            "patient_name": f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() or None,
            "payer_name": payers.get(c["payer_id"]),
            "billed_cents": c.get("billed_cents", 0),
            "paid_cents": c.get("paid_cents", 0),
            "last_denial_code": c.get("last_denial_code"),
            "submitted_at": c.get("submitted_at"),
        })

    # Aggregates
    agg_cur = db.claims.aggregate([
        {"$match": q},
        {"$group": {"_id": None,
                    "billed": {"$sum": "$billed_cents"},
                    "paid": {"$sum": "$paid_cents"}}},
    ])
    agg_doc = await agg_cur.to_list(1)
    agg = (agg_doc or [{}])[0]

    return RunResult(rows=rows, total=total, aggregates={
        "total_billed_cents": int(agg.get("billed", 0) or 0),
        "total_paid_cents": int(agg.get("paid", 0) or 0),
    })


_CLAIM_STATUSES = ["draft", "ready", "submitted", "accepted", "pending",
                   "paid", "partially_paid", "denied", "rejected", "appealed", "closed"]

_CLAIMS_DEF = register(ReportDefinition(
    name="claims_list",
    title="Claims",
    category="Financial",
    description="Insurance claims with status, billed, and paid amounts.",
    required_permission=("reporting", "read_financial"),
    columns=[
        Column("id", "Claim #"),
        Column("status", "Status", "enum"),
        Column("service_date_from", "DOS from", "date"),
        Column("service_date_to", "DOS to", "date", hidden_by_default=True),
        Column("patient_name", "Patient", phi=True),
        Column("payer_name", "Payer"),
        Column("billed_cents", "Billed", "currency", align="right"),
        Column("paid_cents", "Paid", "currency", align="right"),
        Column("last_denial_code", "Denial code", hidden_by_default=True),
        Column("submitted_at", "Submitted", "datetime", hidden_by_default=True),
    ],
    default_columns=["id", "status", "service_date_from", "patient_name", "payer_name", "billed_cents", "paid_cents"],
    filters=[
        Filter("status", "Status", "enum", options=STATUS_OPTS(_CLAIM_STATUSES)),
        Filter("payer_id", "Payer", "string", placeholder="Payer ID"),
        Filter("from", "DOS from", "date_range"),
        Filter("to", "DOS to", "date_range"),
    ],
    sort_options=[
        SortOption("service_date_from", "DOS"),
        SortOption("billed_cents", "Billed"),
        SortOption("paid_cents", "Paid"),
        SortOption("submitted_at", "Submitted"),
    ],
    default_sort="service_date_from", default_sort_dir="desc",
    contains_phi=True,
    runner=_run_claims_list,
))


# ---------------------------------------------------------------------------
# 6. Invoices list (Financial)
# ---------------------------------------------------------------------------

async def _run_invoices_list(qc: QueryContext) -> RunResult:
    q = _base_filter(qc, location_scoped=True)
    if q.get("__deny__"):
        return RunResult(rows=[], total=0)
    if qc.filters.get("status"):
        q["status"] = qc.filters["status"]
    _date_range_filter(q, qc.filters, "issued_at")

    db = tenant_db(qc.tenant.tenant_id)
    total = await db.invoices.count_documents(q)
    cursor = db.invoices.find(q, {"_id": 0}).sort(_sort_spec(qc, _INV_DEF))
    cursor = cursor.skip(max(0, (qc.page - 1) * qc.page_size)).limit(qc.page_size)
    raw = [i async for i in cursor]
    pats = await _hydrate_patients(qc.tenant.tenant_id, list({i["patient_id"] for i in raw}))
    rows = []
    for i in raw:
        p = pats.get(i["patient_id"], {})
        rows.append({
            "id": i.get("id"),
            "status": i.get("status"),
            "issued_at": i.get("issued_at"),
            "due_date": i.get("due_date"),
            "patient_name": f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() or None,
            "total_cents": i.get("total_cents", 0),
            "balance_cents": i.get("balance_cents", 0),
        })

    agg_cur = db.invoices.aggregate([
        {"$match": q},
        {"$group": {"_id": None,
                    "total": {"$sum": "$total_cents"},
                    "balance": {"$sum": "$balance_cents"}}},
    ])
    doc = (await agg_cur.to_list(1) or [{}])[0]
    return RunResult(rows=rows, total=total, aggregates={
        "total_cents": int(doc.get("total", 0) or 0),
        "outstanding_cents": int(doc.get("balance", 0) or 0),
    })


_INV_DEF = register(ReportDefinition(
    name="invoices_list",
    title="Invoices",
    category="Financial",
    description="Invoice roster with balance tracking.",
    required_permission=("reporting", "read_financial"),
    columns=[
        Column("id", "Invoice #"),
        Column("status", "Status", "enum"),
        Column("issued_at", "Issued", "datetime"),
        Column("due_date", "Due", "date"),
        Column("patient_name", "Patient", phi=True),
        Column("total_cents", "Total", "currency", align="right"),
        Column("balance_cents", "Balance", "currency", align="right"),
    ],
    default_columns=["id", "status", "issued_at", "patient_name", "total_cents", "balance_cents"],
    filters=[
        Filter("status", "Status", "enum",
               options=STATUS_OPTS(["draft", "issued", "partially_paid", "paid", "adjusted", "void", "refunded"])),
        Filter("from", "Issued from", "date_range"),
        Filter("to", "Issued to", "date_range"),
    ],
    sort_options=[
        SortOption("issued_at", "Issued"),
        SortOption("balance_cents", "Balance"),
        SortOption("total_cents", "Total"),
    ],
    default_sort="issued_at", default_sort_dir="desc",
    contains_phi=True,
    runner=_run_invoices_list,
))


# ---------------------------------------------------------------------------
# 7. Payments received (Financial)
# ---------------------------------------------------------------------------

async def _run_payments(qc: QueryContext) -> RunResult:
    q = _base_filter(qc, location_scoped=True)
    if q.get("__deny__"):
        return RunResult(rows=[], total=0)
    if qc.filters.get("method"):
        q["method"] = qc.filters["method"]
    _date_range_filter(q, qc.filters, "received_at")

    db = tenant_db(qc.tenant.tenant_id)
    total = await db.payments.count_documents(q)
    cursor = db.payments.find(q, {"_id": 0}).sort(_sort_spec(qc, _PAY_DEF))
    cursor = cursor.skip(max(0, (qc.page - 1) * qc.page_size)).limit(qc.page_size)
    raw = [p async for p in cursor]

    pats = await _hydrate_patients(qc.tenant.tenant_id, list({p["patient_id"] for p in raw if p.get("patient_id")}))
    payers = await _hydrate_payers(qc.tenant.tenant_id, list({p["payer_id"] for p in raw if p.get("payer_id")}))

    rows = []
    for p in raw:
        pt = pats.get(p.get("patient_id", ""), {})
        rows.append({
            "id": p.get("id"),
            "received_at": p.get("received_at") or p.get("created_at"),
            "method": p.get("method"),
            "status": p.get("status"),
            "amount_cents": p.get("amount_cents", 0),
            "allocated_cents": p.get("allocated_cents", 0),
            "patient_name": f"{pt.get('first_name', '')} {pt.get('last_name', '')}".strip() or None,
            "payer_name": payers.get(p.get("payer_id")) if p.get("payer_id") else "Patient",
            "reference": p.get("reference"),
        })

    agg_cur = db.payments.aggregate([
        {"$match": q},
        {"$group": {"_id": None, "total": {"$sum": "$amount_cents"}}},
    ])
    doc = (await agg_cur.to_list(1) or [{}])[0]
    return RunResult(rows=rows, total=total,
                     aggregates={"total_cents": int(doc.get("total", 0) or 0)})


_PAY_DEF = register(ReportDefinition(
    name="payments_received",
    title="Payments received",
    category="Financial",
    description="Patient and insurance payments within the selected date range.",
    required_permission=("reporting", "read_financial"),
    columns=[
        Column("received_at", "Received", "datetime"),
        Column("method", "Method", "enum"),
        Column("amount_cents", "Amount", "currency", align="right"),
        Column("allocated_cents", "Allocated", "currency", align="right", hidden_by_default=True),
        Column("patient_name", "Patient", phi=True),
        Column("payer_name", "Source"),
        Column("status", "Status", "enum"),
        Column("reference", "Reference", hidden_by_default=True),
    ],
    default_columns=["received_at", "method", "amount_cents", "patient_name", "payer_name", "status"],
    filters=[
        Filter("from", "Received from", "date_range"),
        Filter("to", "Received to", "date_range"),
        Filter("method", "Method", "enum", options=STATUS_OPTS([
            "cash", "check", "card_present", "card_not_present", "ach", "era_posting", "hsa_fsa", "other",
        ])),
    ],
    sort_options=[SortOption("received_at", "Received"), SortOption("amount_cents", "Amount")],
    default_sort="received_at", default_sort_dir="desc",
    contains_phi=True,
    runner=_run_payments,
))


# ---------------------------------------------------------------------------
# 8. Denials log (Financial)
# ---------------------------------------------------------------------------

async def _run_denials(qc: QueryContext) -> RunResult:
    q = scoped_filter({}, qc.tenant)
    if q.get("__deny__"):
        return RunResult(rows=[], total=0)
    if qc.filters.get("status"):
        q["status"] = qc.filters["status"]
    _date_range_filter(q, qc.filters, "opened_at")

    db = tenant_db(qc.tenant.tenant_id)
    total = await db.denial_work_items.count_documents(q)
    cursor = db.denial_work_items.find(q, {"_id": 0}).sort(_sort_spec(qc, _DEN_DEF))
    cursor = cursor.skip(max(0, (qc.page - 1) * qc.page_size)).limit(qc.page_size)
    raw = [d async for d in cursor]

    rows = [{
        "id": d.get("id"),
        "opened_at": d.get("opened_at"),
        "closed_at": d.get("closed_at"),
        "status": d.get("status"),
        "denial_code": d.get("denial_code"),
        "denial_category": d.get("denial_category"),
        "amount_cents": d.get("amount_cents", 0),
        "claim_id": d.get("claim_id"),
    } for d in raw]

    agg_cur = db.denial_work_items.aggregate([
        {"$match": q},
        {"$group": {"_id": "$denial_category", "total_cents": {"$sum": "$amount_cents"}, "count": {"$sum": 1}}},
        {"$sort": {"total_cents": -1}},
    ])
    by_cat = [{"category": r["_id"], "count": r["count"], "total_cents": r["total_cents"]} async for r in agg_cur]
    return RunResult(rows=rows, total=total, aggregates={"by_category": by_cat})


_DEN_DEF = register(ReportDefinition(
    name="denials_log",
    title="Denials",
    category="Financial",
    description="Claim denial work items with category and amount.",
    required_permission=("reporting", "read_financial"),
    columns=[
        Column("opened_at", "Opened", "datetime"),
        Column("status", "Status", "enum"),
        Column("denial_code", "Code"),
        Column("denial_category", "Category"),
        Column("amount_cents", "Amount", "currency", align="right"),
        Column("claim_id", "Claim #"),
        Column("closed_at", "Closed", "datetime", hidden_by_default=True),
    ],
    default_columns=["opened_at", "status", "denial_code", "denial_category", "amount_cents", "claim_id"],
    filters=[
        Filter("status", "Status", "enum",
               options=STATUS_OPTS(["open", "in_progress", "escalated", "resolved", "closed"])),
        Filter("from", "Opened from", "date_range"),
        Filter("to", "Opened to", "date_range"),
    ],
    sort_options=[SortOption("opened_at", "Opened"), SortOption("amount_cents", "Amount")],
    default_sort="opened_at", default_sort_dir="desc",
    runner=_run_denials,
))


# ---------------------------------------------------------------------------
# 9. Audit activity (Compliance)
# ---------------------------------------------------------------------------

async def _run_audit_activity(qc: QueryContext) -> RunResult:
    q: dict = {}
    if qc.tenant.tenant_id and not qc.tenant.is_platform_admin:
        q["tenant_id"] = qc.tenant.tenant_id
    if qc.filters.get("action"):
        q["action"] = qc.filters["action"]
    if qc.filters.get("outcome"):
        q["outcome"] = qc.filters["outcome"]
    if qc.filters.get("actor_email"):
        q["actor_email"] = qc.filters["actor_email"]
    _date_range_filter(q, qc.filters, "created_at")

    db = get_db_read()
    total = await db.audit_logs.count_documents(q)
    cursor = db.audit_logs.find(q, {"_id": 0}).sort(_sort_spec(qc, _AUDIT_DEF))
    cursor = cursor.skip(max(0, (qc.page - 1) * qc.page_size)).limit(qc.page_size)
    raw = [a async for a in cursor]
    rows = [{
        "created_at": a.get("created_at"),
        "action": a.get("action"),
        "outcome": a.get("outcome"),
        "actor_email": a.get("actor_email"),
        "actor_role": a.get("actor_role"),
        "entity_type": a.get("entity_type"),
        "entity_id": a.get("entity_id"),
        "reason": a.get("reason"),
        "ip": a.get("ip"),
        "phi_accessed": a.get("phi_accessed", False),
    } for a in raw]
    return RunResult(rows=rows, total=total)


_AUDIT_DEF = register(ReportDefinition(
    name="audit_activity",
    title="Audit activity",
    category="Compliance",
    description="Global audit log stream — every PHI access and privileged action.",
    required_permission=("audit_log", "read"),
    columns=[
        Column("created_at", "When", "datetime"),
        Column("action", "Action"),
        Column("outcome", "Outcome", "enum"),
        Column("actor_email", "Actor"),
        Column("actor_role", "Role"),
        Column("entity_type", "Entity"),
        Column("entity_id", "Entity ID", hidden_by_default=True),
        Column("phi_accessed", "PHI", "boolean"),
        Column("ip", "IP", hidden_by_default=True),
        Column("reason", "Reason", hidden_by_default=True),
    ],
    default_columns=["created_at", "action", "outcome", "actor_email", "entity_type", "phi_accessed"],
    filters=[
        Filter("action", "Action", "string", placeholder="e.g. patient.viewed"),
        Filter("outcome", "Outcome", "enum",
               options=[{"value": "success", "label": "Success"}, {"value": "failure", "label": "Failure"}]),
        Filter("actor_email", "Actor email", "string"),
        Filter("from", "From", "date_range"),
        Filter("to", "To", "date_range"),
    ],
    sort_options=[SortOption("created_at", "When"), SortOption("action", "Action")],
    default_sort="created_at", default_sort_dir="desc",
    runner=_run_audit_activity,
))


# ---------------------------------------------------------------------------
# 10. License expiration (Compliance — workforce credentialing)
# ---------------------------------------------------------------------------

async def _run_license_expiration(qc: QueryContext) -> RunResult:
    q: dict = {}
    if qc.tenant.tenant_id and not qc.tenant.is_platform_admin:
        q["tenant_id"] = qc.tenant.tenant_id
    q["role"] = {"$in": ["doctor", "admin"]}

    db = tenant_db(qc.tenant.tenant_id)
    cursor = db.users.find(q, {
        "_id": 0, "id": 1, "name": 1, "email": 1, "role": 1,
        "npi_number": 1, "dea_number": 1, "dea_expires_at": 1,
        "professional_licenses": 1,
    })
    users = [u async for u in cursor]
    today = datetime.now(timezone.utc).date()
    horizon = today + timedelta(days=int(qc.filters.get("days", 90) or 90))

    rows: list[dict] = []
    for u in users:
        # DEA
        dea_exp = u.get("dea_expires_at")
        if dea_exp:
            try:
                exp = datetime.fromisoformat(dea_exp).date()
                days_left = (exp - today).days
                rows.append({
                    "provider_name": u.get("name"),
                    "email": u.get("email"),
                    "credential": "DEA",
                    "identifier": u.get("dea_number"),
                    "expires_at": dea_exp,
                    "days_until_expiry": days_left,
                    "status": "expired" if days_left < 0 else ("expiring_soon" if days_left <= 30 else "ok"),
                })
            except ValueError:
                pass
        # Professional licenses (state-issued)
        for lic in (u.get("professional_licenses") or []):
            lic_exp = lic.get("expires_at")
            if not lic_exp:
                continue
            try:
                exp = datetime.fromisoformat(lic_exp).date()
                days_left = (exp - today).days
                rows.append({
                    "provider_name": u.get("name"),
                    "email": u.get("email"),
                    "credential": f"{lic.get('state', '??').upper()} {lic.get('kind', 'license').replace('_', ' ')}",
                    "identifier": lic.get("number"),
                    "expires_at": lic_exp,
                    "days_until_expiry": days_left,
                    "status": "expired" if days_left < 0 else ("expiring_soon" if days_left <= 30 else "ok"),
                })
            except ValueError:
                pass

    # Status filter + expiring-soon horizon
    if qc.filters.get("status"):
        rows = [r for r in rows if r["status"] == qc.filters["status"]]
    elif not qc.filters.get("include_all"):
        rows = [r for r in rows if datetime.fromisoformat(r["expires_at"]).date() <= horizon]

    sort_key = resolve_sort(_LIC_DEF, qc.sort)
    rev = qc.sort_dir == "desc"
    rows.sort(key=lambda x: x.get(sort_key) or "", reverse=rev)
    total = len(rows)
    return RunResult(rows=_paged(rows, qc.page, qc.page_size), total=total)


_LIC_DEF = register(ReportDefinition(
    name="license_expiration",
    title="License expiration",
    category="Compliance",
    description="Provider DEA + state professional licenses expiring in the next N days.",
    required_permission=("reporting", "read"),
    columns=[
        Column("provider_name", "Provider"),
        Column("email", "Email"),
        Column("credential", "Credential"),
        Column("identifier", "Number"),
        Column("expires_at", "Expires", "date"),
        Column("days_until_expiry", "Days left", "integer", align="right"),
        Column("status", "Status", "enum"),
    ],
    default_columns=["provider_name", "credential", "identifier", "expires_at", "days_until_expiry", "status"],
    filters=[
        Filter("days", "Horizon (days)", "integer"),
        Filter("status", "Status", "enum",
               options=STATUS_OPTS(["expired", "expiring_soon", "ok"])),
    ],
    sort_options=[
        SortOption("expires_at", "Expiry date"),
        SortOption("days_until_expiry", "Days left"),
        SortOption("provider_name", "Provider"),
    ],
    default_sort="expires_at", default_sort_dir="asc",
    runner=_run_license_expiration,
))


def registered_definitions() -> list[ReportDefinition]:
    """Force import side-effect resolution; returns every registered def."""
    from services.reports.definitions import all_definitions
    return all_definitions()
