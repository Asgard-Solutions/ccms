"""
Extended built-in reports (Phase 3 expansion).

Each report reuses the framework in `definitions.py`. We intentionally
avoid reports whose source data is not yet captured anywhere in the app
(referral source, birthday cohort, ICD distribution, CPT volume) —
those will be added once the underlying capture paths exist.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from core.db import get_db_read
from core.tenancy import tenant_db
from core.tenant_scope import scoped_filter
from services.reports.builtin import (
    STATUS_OPTS,
    _base_filter,
    _date_range_filter,
    _hydrate_locations,
    _hydrate_patients,
    _hydrate_users,
    _paged,
    _sort_spec,
)
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
# 11. New patients by date range (Patient)
# ---------------------------------------------------------------------------

async def _run_new_patients(qc: QueryContext) -> RunResult:
    q = _base_filter(qc, location_scoped=True)
    if q.get("__deny__"):
        return RunResult(rows=[], total=0)
    _date_range_filter(q, qc.filters, "created_at")
    # Default to the last 30 days if no range provided — sensible default
    # so non-technical users see something meaningful immediately.
    if not qc.filters.get("from") and not qc.filters.get("to"):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        q["created_at"] = {"$gte": cutoff}

    db = tenant_db(qc.tenant.tenant_id)
    total = await db.patients.count_documents(q)
    cursor = db.patients.find(q, {
        "_id": 0, "id": 1, "first_name": 1, "last_name": 1, "phone": 1, "email": 1,
        "created_at": 1, "location_id": 1, "status": 1,
    }).sort(_sort_spec(qc, _NEW_PATIENTS_DEF))
    cursor = cursor.skip(max(0, (qc.page - 1) * qc.page_size)).limit(qc.page_size)
    raw = [p async for p in cursor]
    locs = await _hydrate_locations(qc.tenant.tenant_id, [p.get("location_id") for p in raw])
    rows = [{
        "id": p["id"],
        "full_name": f"{p.get('first_name', '')} {p.get('last_name', '')}".strip(),
        "phone": p.get("phone"),
        "email": p.get("email"),
        "registered_at": p.get("created_at"),
        "status": p.get("status"),
        "location_name": locs.get(p.get("location_id") or "", None),
    } for p in raw]
    return RunResult(rows=rows, total=total)


_NEW_PATIENTS_DEF = register(ReportDefinition(
    name="new_patients",
    title="New patients",
    category="Patient",
    description="Patients registered within the selected window. Defaults to last 30 days.",
    required_permission=("reporting", "read_financial"),
    columns=[
        Column("registered_at", "Registered", "datetime"),
        Column("full_name", "Name", phi=True),
        Column("phone", "Phone", phi=True),
        Column("email", "Email", phi=True),
        Column("status", "Status", "enum"),
        Column("location_name", "Location"),
    ],
    default_columns=["registered_at", "full_name", "phone", "status", "location_name"],
    filters=[
        Filter("from", "Registered from", "date_range"),
        Filter("to", "Registered to", "date_range"),
    ],
    sort_options=[SortOption("registered_at", "Registered")],
    default_sort="registered_at", default_sort_dir="desc",
    contains_phi=True,
    runner=_run_new_patients,
))


# ---------------------------------------------------------------------------
# 12. Patient contact completeness (Patient)
# ---------------------------------------------------------------------------

async def _run_patient_contact_completeness(qc: QueryContext) -> RunResult:
    q = _base_filter(qc, location_scoped=True)
    if q.get("__deny__"):
        return RunResult(rows=[], total=0)
    q.setdefault("status", {"$ne": "deleted"})
    # At least one of phone/email/dob must be missing
    missing_clauses: list[dict] = [
        {"phone": {"$in": [None, ""]}},
        {"email": {"$in": [None, ""]}},
        {"date_of_birth": {"$in": [None, ""]}},
    ]
    q["$or"] = missing_clauses

    db = tenant_db(qc.tenant.tenant_id)
    total = await db.patients.count_documents(q)
    cursor = db.patients.find(q, {
        "_id": 0, "id": 1, "first_name": 1, "last_name": 1,
        "phone": 1, "email": 1, "date_of_birth": 1, "status": 1,
    }).sort(_sort_spec(qc, _COMPLETENESS_DEF))
    cursor = cursor.skip(max(0, (qc.page - 1) * qc.page_size)).limit(qc.page_size)
    raw = [p async for p in cursor]
    rows = []
    for p in raw:
        missing = []
        if not p.get("phone"):
            missing.append("phone")
        if not p.get("email"):
            missing.append("email")
        if not p.get("date_of_birth"):
            missing.append("dob")
        rows.append({
            "id": p["id"],
            "full_name": f"{p.get('first_name', '')} {p.get('last_name', '')}".strip(),
            "phone": p.get("phone"),
            "email": p.get("email"),
            "date_of_birth": p.get("date_of_birth"),
            "missing_fields": ", ".join(missing),
            "status": p.get("status"),
        })
    return RunResult(rows=rows, total=total)


_COMPLETENESS_DEF = register(ReportDefinition(
    name="patient_contact_completeness",
    title="Patient contact completeness",
    category="Patient",
    description="Active patients missing phone, email, or date of birth. Drives intake follow-up.",
    required_permission=("reporting", "read_financial"),
    columns=[
        Column("full_name", "Patient", phi=True),
        Column("missing_fields", "Missing"),
        Column("phone", "Phone", phi=True),
        Column("email", "Email", phi=True),
        Column("date_of_birth", "DOB", "date", phi=True),
        Column("status", "Status", "enum"),
    ],
    default_columns=["full_name", "missing_fields", "phone", "email", "date_of_birth"],
    filters=[],
    sort_options=[SortOption("last_name", "Last name")],
    default_sort="last_name", default_sort_dir="asc",
    contains_phi=True,
    runner=_run_patient_contact_completeness,
))


# ---------------------------------------------------------------------------
# 13. Active patients summary (Patient)
# ---------------------------------------------------------------------------

async def _run_active_patients_summary(qc: QueryContext) -> RunResult:
    q = _base_filter(qc, location_scoped=True)
    if q.get("__deny__"):
        return RunResult(rows=[], total=0)

    db = tenant_db(qc.tenant.tenant_id)
    pipeline = [
        {"$match": q},
        {"$group": {"_id": {"status": "$status", "location_id": "$location_id"},
                    "count": {"$sum": 1}}},
    ]
    raw = [r async for r in db.patients.aggregate(pipeline)]
    loc_ids = [r["_id"].get("location_id") for r in raw if r["_id"].get("location_id")]
    locs = await _hydrate_locations(qc.tenant.tenant_id, loc_ids)
    rows = [{
        "location_name": locs.get(r["_id"].get("location_id", ""), "—"),
        "status": r["_id"].get("status"),
        "count": r["count"],
    } for r in raw]
    rows.sort(key=lambda x: (x["location_name"] or "", x["status"] or ""))
    # Agg totals
    by_status: dict[str, int] = {}
    for r in rows:
        by_status[r["status"] or "unknown"] = by_status.get(r["status"] or "unknown", 0) + r["count"]
    return RunResult(rows=_paged(rows, qc.page, qc.page_size),
                     total=len(rows),
                     aggregates={"by_status": by_status})


_ACTIVE_DEF = register(ReportDefinition(
    name="active_patients_summary",
    title="Active vs inactive patients",
    category="Patient",
    description="Patient counts by status and location.",
    required_permission=("reporting", "read"),
    columns=[
        Column("location_name", "Location"),
        Column("status", "Status", "enum"),
        Column("count", "Count", "integer", align="right"),
    ],
    default_columns=["location_name", "status", "count"],
    filters=[],
    sort_options=[SortOption("count", "Count")],
    default_sort="count", default_sort_dir="desc",
    runner=_run_active_patients_summary,
))


# ---------------------------------------------------------------------------
# 14. Cancellations & no-shows (Scheduling)
# ---------------------------------------------------------------------------

async def _run_cancellations(qc: QueryContext) -> RunResult:
    q = _base_filter(qc, location_scoped=True)
    if q.get("__deny__"):
        return RunResult(rows=[], total=0)
    statuses = qc.filters.get("statuses") or ["cancelled"]
    q["status"] = {"$in": statuses}
    _date_range_filter(q, qc.filters, "start_time")

    db = tenant_db(qc.tenant.tenant_id)
    total = await db.appointments.count_documents(q)
    cursor = db.appointments.find(q, {"_id": 0}).sort(_sort_spec(qc, _CANCEL_DEF))
    cursor = cursor.skip(max(0, (qc.page - 1) * qc.page_size)).limit(qc.page_size)
    raw = [a async for a in cursor]
    provs = await _hydrate_users(qc.tenant.tenant_id, list({a["provider_id"] for a in raw if a.get("provider_id")}))
    pats = await _hydrate_patients(qc.tenant.tenant_id, list({a["patient_id"] for a in raw}))
    rows = []
    for a in raw:
        p = pats.get(a["patient_id"], {})
        rows.append({
            "id": a.get("id"),
            "start_time": a.get("start_time"),
            "status": a.get("status"),
            "patient_name": f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() or None,
            "provider_name": (provs.get(a.get("provider_id", "")) or {}).get("name"),
            "cancellation_reason": a.get("cancellation_reason"),
            "cancelled_at": a.get("cancelled_at"),
            "cancelled_by": a.get("cancelled_by"),
        })
    return RunResult(rows=rows, total=total)


_CANCEL_DEF = register(ReportDefinition(
    name="cancellations_no_shows",
    title="Cancellations & no-shows",
    category="Scheduling",
    description="Appointments with status cancelled or no-show within the date range.",
    required_permission=("reporting", "read"),
    columns=[
        Column("start_time", "Start", "datetime"),
        Column("status", "Status", "enum"),
        Column("patient_name", "Patient", phi=True),
        Column("provider_name", "Provider"),
        Column("cancellation_reason", "Reason"),
        Column("cancelled_at", "Cancelled at", "datetime", hidden_by_default=True),
        Column("cancelled_by", "Cancelled by", hidden_by_default=True),
    ],
    default_columns=["start_time", "status", "patient_name", "provider_name", "cancellation_reason"],
    filters=[
        Filter("from", "From date", "date_range"),
        Filter("to", "To date", "date_range"),
        Filter("statuses", "Status", "enum",
               options=STATUS_OPTS(["cancelled", "no_show"])),
    ],
    sort_options=[SortOption("start_time", "Start time"),
                  SortOption("cancelled_at", "Cancelled at")],
    default_sort="start_time", default_sort_dir="desc",
    contains_phi=True,
    runner=_run_cancellations,
))


# ---------------------------------------------------------------------------
# 15. Notes-by-provider (Clinical)
# ---------------------------------------------------------------------------

async def _run_notes_by_provider(qc: QueryContext) -> RunResult:
    q = scoped_filter({}, qc.tenant)
    if q.get("__deny__"):
        return RunResult(rows=[], total=0)
    _date_range_filter(q, qc.filters, "date_of_service")

    db = tenant_db(qc.tenant.tenant_id)
    pipeline = [
        {"$match": q},
        {"$group": {
            "_id": "$provider_id",
            "signed": {"$sum": {"$cond": [{"$eq": ["$status", "signed"]}, 1, 0]}},
            "draft": {"$sum": {"$cond": [{"$eq": ["$status", "draft"]}, 1, 0]}},
            "sign_ready": {"$sum": {"$cond": [{"$eq": ["$status", "sign_ready"]}, 1, 0]}},
            "total": {"$sum": 1},
        }},
    ]
    raw = [r async for r in db.clinical_follow_up_notes.aggregate(pipeline) if r["_id"]]
    provs = await _hydrate_users(qc.tenant.tenant_id, [r["_id"] for r in raw])
    rows = [{
        "provider_id": r["_id"],
        "provider_name": (provs.get(r["_id"]) or {}).get("name"),
        "draft": r["draft"], "sign_ready": r["sign_ready"],
        "signed": r["signed"], "total": r["total"],
        "completion_rate": round((r["signed"] / r["total"]) * 100, 1) if r["total"] else 0,
    } for r in raw]
    sort_key = resolve_sort(_NBP_DEF, qc.sort)
    rows.sort(key=lambda x: x.get(sort_key) or 0, reverse=qc.sort_dir == "desc")
    return RunResult(rows=_paged(rows, qc.page, qc.page_size), total=len(rows))


_NBP_DEF = register(ReportDefinition(
    name="notes_by_provider",
    title="Clinical notes by provider",
    category="Clinical",
    description="Follow-up note volume and signing completion by provider.",
    required_permission=("reporting", "read_clinical"),
    columns=[
        Column("provider_name", "Provider"),
        Column("draft", "Draft", "integer", align="right"),
        Column("sign_ready", "Sign-ready", "integer", align="right"),
        Column("signed", "Signed", "integer", align="right"),
        Column("total", "Total", "integer", align="right"),
        Column("completion_rate", "Sign rate (%)", "number", align="right"),
    ],
    default_columns=["provider_name", "draft", "sign_ready", "signed", "total", "completion_rate"],
    filters=[
        Filter("from", "DOS from", "date_range"),
        Filter("to", "DOS to", "date_range"),
    ],
    sort_options=[SortOption("total", "Total"),
                  SortOption("signed", "Signed"),
                  SortOption("completion_rate", "Sign rate")],
    default_sort="total", default_sort_dir="desc",
    runner=_run_notes_by_provider,
))


# ---------------------------------------------------------------------------
# 16. Payments by method summary (Financial)
# ---------------------------------------------------------------------------

async def _run_payments_by_method(qc: QueryContext) -> RunResult:
    q = _base_filter(qc, location_scoped=True)
    if q.get("__deny__"):
        return RunResult(rows=[], total=0)
    _date_range_filter(q, qc.filters, "received_at")

    db = tenant_db(qc.tenant.tenant_id)
    pipeline = [
        {"$match": q},
        {"$group": {"_id": "$method",
                    "count": {"$sum": 1},
                    "total_cents": {"$sum": "$amount_cents"}}},
    ]
    raw = [r async for r in db.payments.aggregate(pipeline)]
    rows = [{
        "method": r["_id"] or "unknown",
        "count": r["count"],
        "total_cents": int(r["total_cents"] or 0),
    } for r in raw]
    total_cents = sum(r["total_cents"] for r in rows)
    rows.sort(key=lambda x: x["total_cents"], reverse=True)
    return RunResult(rows=_paged(rows, qc.page, qc.page_size),
                     total=len(rows),
                     aggregates={"total_cents": total_cents})


_PBM_DEF = register(ReportDefinition(
    name="payments_by_method_summary",
    title="Payments by method",
    category="Financial",
    description="Payment volume and total received by payment method.",
    required_permission=("reporting", "read_financial"),
    columns=[
        Column("method", "Method", "enum"),
        Column("count", "Transactions", "integer", align="right"),
        Column("total_cents", "Total", "currency", align="right"),
    ],
    default_columns=["method", "count", "total_cents"],
    filters=[
        Filter("from", "Received from", "date_range"),
        Filter("to", "Received to", "date_range"),
    ],
    sort_options=[SortOption("total_cents", "Total"),
                  SortOption("count", "Transactions")],
    default_sort="total_cents", default_sort_dir="desc",
    runner=_run_payments_by_method,
))


# ---------------------------------------------------------------------------
# 17. Patient balance report (Financial — PHI)
# ---------------------------------------------------------------------------

async def _run_patient_balance(qc: QueryContext) -> RunResult:
    q = _base_filter(qc, location_scoped=True)
    if q.get("__deny__"):
        return RunResult(rows=[], total=0)
    q["balance_cents"] = {"$gt": 0}
    min_balance = int(qc.filters.get("min_cents") or 0)
    if min_balance:
        q["balance_cents"] = {"$gte": min_balance}

    db = tenant_db(qc.tenant.tenant_id)
    pipeline = [
        {"$match": q},
        {"$group": {"_id": "$patient_id",
                    "balance_cents": {"$sum": "$balance_cents"},
                    "invoice_count": {"$sum": 1}}},
        {"$sort": {"balance_cents": -1}},
    ]
    raw = [r async for r in db.invoices.aggregate(pipeline)]
    total = len(raw)
    page = raw[max(0, (qc.page - 1) * qc.page_size):qc.page * qc.page_size]
    pats = await _hydrate_patients(qc.tenant.tenant_id, [r["_id"] for r in page])
    rows = []
    for r in page:
        p = pats.get(r["_id"], {})
        rows.append({
            "patient_id": r["_id"],
            "patient_name": f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() or None,
            "phone": p.get("phone"),
            "invoice_count": r["invoice_count"],
            "balance_cents": int(r["balance_cents"] or 0),
        })
    agg_total = sum(int(r["balance_cents"] or 0) for r in raw)
    return RunResult(rows=rows, total=total,
                     aggregates={"outstanding_cents": agg_total})


_PAT_BAL_DEF = register(ReportDefinition(
    name="patient_balance",
    title="Patient outstanding balances",
    category="Financial",
    description="Patients with one or more invoices carrying a balance greater than zero.",
    required_permission=("reporting", "read_financial"),
    columns=[
        Column("patient_name", "Patient", phi=True),
        Column("phone", "Phone", phi=True),
        Column("invoice_count", "Invoices", "integer", align="right"),
        Column("balance_cents", "Balance", "currency", align="right"),
    ],
    default_columns=["patient_name", "invoice_count", "balance_cents"],
    filters=[
        Filter("min_cents", "Min balance (cents)", "integer"),
    ],
    sort_options=[SortOption("balance_cents", "Balance")],
    default_sort="balance_cents", default_sort_dir="desc",
    contains_phi=True,
    runner=_run_patient_balance,
))


# ---------------------------------------------------------------------------
# 18. PHI access activity (Compliance)
# ---------------------------------------------------------------------------

async def _run_phi_access(qc: QueryContext) -> RunResult:
    q: dict = {"phi_accessed": True}
    if qc.tenant.tenant_id and not qc.tenant.is_platform_admin:
        q["tenant_id"] = qc.tenant.tenant_id
    if qc.filters.get("actor_email"):
        q["actor_email"] = qc.filters["actor_email"]
    _date_range_filter(q, qc.filters, "created_at")
    db = get_db_read()
    total = await db.audit_logs.count_documents(q)
    cursor = db.audit_logs.find(q, {"_id": 0}).sort(_sort_spec(qc, _PHI_DEF))
    cursor = cursor.skip(max(0, (qc.page - 1) * qc.page_size)).limit(qc.page_size)
    raw = [r async for r in cursor]
    rows = [{
        "created_at": r.get("created_at"),
        "action": r.get("action"),
        "actor_email": r.get("actor_email"),
        "actor_role": r.get("actor_role"),
        "entity_type": r.get("entity_type"),
        "entity_id": r.get("entity_id"),
        "reason": r.get("reason"),
        "ip": r.get("ip"),
    } for r in raw]
    return RunResult(rows=rows, total=total)


_PHI_DEF = register(ReportDefinition(
    name="phi_access_activity",
    title="PHI access activity",
    category="Compliance",
    description="Every audit event that touched protected health information.",
    required_permission=("audit_log", "read"),
    columns=[
        Column("created_at", "When", "datetime"),
        Column("action", "Action"),
        Column("actor_email", "Actor"),
        Column("actor_role", "Role"),
        Column("entity_type", "Entity"),
        Column("entity_id", "Entity ID", hidden_by_default=True),
        Column("reason", "Reason"),
        Column("ip", "IP", hidden_by_default=True),
    ],
    default_columns=["created_at", "action", "actor_email", "entity_type", "reason"],
    filters=[
        Filter("actor_email", "Actor email", "string"),
        Filter("from", "From", "date_range"),
        Filter("to", "To", "date_range"),
    ],
    sort_options=[SortOption("created_at", "When")],
    default_sort="created_at", default_sort_dir="desc",
    runner=_run_phi_access,
))


# ---------------------------------------------------------------------------
# 19. Failed login attempts (Compliance / Security)
# ---------------------------------------------------------------------------

async def _run_failed_logins(qc: QueryContext) -> RunResult:
    q: dict = {"action": "auth.login", "outcome": "failure"}
    if qc.tenant.tenant_id and not qc.tenant.is_platform_admin:
        q["tenant_id"] = qc.tenant.tenant_id
    if qc.filters.get("actor_email"):
        q["actor_email"] = qc.filters["actor_email"]
    _date_range_filter(q, qc.filters, "created_at")
    db = get_db_read()
    total = await db.audit_logs.count_documents(q)
    cursor = db.audit_logs.find(q, {"_id": 0}).sort(_sort_spec(qc, _FAIL_DEF))
    cursor = cursor.skip(max(0, (qc.page - 1) * qc.page_size)).limit(qc.page_size)
    raw = [r async for r in cursor]
    rows = [{
        "created_at": r.get("created_at"),
        "actor_email": r.get("actor_email"),
        "reason": r.get("reason"),
        "ip": r.get("ip"),
        "user_agent": r.get("user_agent"),
    } for r in raw]
    return RunResult(rows=rows, total=total)


_FAIL_DEF = register(ReportDefinition(
    name="failed_logins",
    title="Failed login attempts",
    category="Compliance",
    description="Every auth.login event with outcome=failure. Useful for spotting brute-force attempts.",
    required_permission=("audit_log", "read"),
    columns=[
        Column("created_at", "When", "datetime"),
        Column("actor_email", "Email"),
        Column("reason", "Reason"),
        Column("ip", "IP"),
        Column("user_agent", "User agent", hidden_by_default=True),
    ],
    default_columns=["created_at", "actor_email", "reason", "ip"],
    filters=[
        Filter("actor_email", "Email", "string"),
        Filter("from", "From", "date_range"),
        Filter("to", "To", "date_range"),
    ],
    sort_options=[SortOption("created_at", "When")],
    default_sort="created_at", default_sort_dir="desc",
    runner=_run_failed_logins,
))


# ---------------------------------------------------------------------------
# 20. Export history (Compliance)
# ---------------------------------------------------------------------------

async def _run_export_history(qc: QueryContext) -> RunResult:
    q: dict = {}
    if qc.tenant.tenant_id and not qc.tenant.is_platform_admin:
        q["tenant_id"] = qc.tenant.tenant_id
    if qc.filters.get("type"):
        q["type"] = qc.filters["type"]
    _date_range_filter(q, qc.filters, "created_at")

    db = tenant_db(qc.tenant.tenant_id)
    total = await db.exports.count_documents(q)
    cursor = db.exports.find(q, {"_id": 0, "password_hash": 0, "password_enc": 0}).sort(_sort_spec(qc, _EXPORT_HISTORY_DEF))
    cursor = cursor.skip(max(0, (qc.page - 1) * qc.page_size)).limit(qc.page_size)
    raw = [e async for e in cursor]
    rows = [{
        "id": e.get("id"),
        "type": e.get("type"),
        "report_name": e.get("report_name"),
        "format": e.get("format"),
        "status": e.get("status"),
        "actor_email": e.get("actor_email"),
        "created_at": e.get("created_at"),
        "rows": e.get("rows"),
        "size_bytes": e.get("size_bytes"),
        "password_protected": bool(e.get("password_protected")),
        "protection_kind": e.get("protection_kind"),
        "reason": e.get("reason"),
    } for e in raw]
    return RunResult(rows=rows, total=total)


_EXPORT_HISTORY_DEF = register(ReportDefinition(
    name="export_history",
    title="Export history",
    category="Compliance",
    description="All data exports requested in this tenant, with HIPAA protection status and actor.",
    required_permission=("audit_log", "read"),
    columns=[
        Column("created_at", "When", "datetime"),
        Column("type", "Type", "enum"),
        Column("report_name", "Report"),
        Column("format", "Format", "enum"),
        Column("status", "Status", "enum"),
        Column("actor_email", "Actor"),
        Column("rows", "Rows", "integer", align="right"),
        Column("size_bytes", "Size (bytes)", "integer", align="right", hidden_by_default=True),
        Column("password_protected", "Protected", "boolean"),
        Column("protection_kind", "Protection", hidden_by_default=True),
        Column("reason", "Purpose", hidden_by_default=True),
    ],
    default_columns=["created_at", "report_name", "format", "status", "actor_email", "rows", "password_protected"],
    filters=[
        Filter("type", "Type", "enum",
               options=STATUS_OPTS(["report", "patients", "appointments"])),
        Filter("from", "From", "date_range"),
        Filter("to", "To", "date_range"),
    ],
    sort_options=[SortOption("created_at", "When"),
                  SortOption("size_bytes", "Size"),
                  SortOption("rows", "Rows")],
    default_sort="created_at", default_sort_dir="desc",
    runner=_run_export_history,
))


# ---------------------------------------------------------------------------
# 21. User last-login report (Workforce)
# ---------------------------------------------------------------------------

async def _run_user_last_login(qc: QueryContext) -> RunResult:
    q: dict = {}
    if qc.tenant.tenant_id and not qc.tenant.is_platform_admin:
        q["tenant_id"] = qc.tenant.tenant_id
    if qc.filters.get("role"):
        q["role"] = qc.filters["role"]
    db = tenant_db(qc.tenant.tenant_id)
    total = await db.users.count_documents(q)
    cursor = db.users.find(q, {
        "_id": 0, "id": 1, "name": 1, "email": 1, "role": 1,
        "last_login_at": 1, "created_at": 1, "status": 1,
        "mfa_enabled": 1,
    }).sort(_sort_spec(qc, _LAST_LOGIN_DEF))
    cursor = cursor.skip(max(0, (qc.page - 1) * qc.page_size)).limit(qc.page_size)
    now = datetime.now(timezone.utc)
    rows = []
    async for u in cursor:
        ll = u.get("last_login_at")
        days_inactive = None
        if ll:
            try:
                ts = datetime.fromisoformat(ll.replace("Z", "+00:00"))
                days_inactive = (now - ts).days
            except ValueError:
                days_inactive = None
        rows.append({
            "name": u.get("name"),
            "email": u.get("email"),
            "role": u.get("role"),
            "status": u.get("status"),
            "last_login_at": ll,
            "days_inactive": days_inactive,
            "mfa_enabled": bool(u.get("mfa_enabled")),
        })
    return RunResult(rows=rows, total=total)


_LAST_LOGIN_DEF = register(ReportDefinition(
    name="user_last_login",
    title="User last login",
    category="Workforce",
    description="All workforce users with last-login timestamp, MFA status, and inactivity window.",
    required_permission=("reporting", "read"),
    columns=[
        Column("name", "Name"),
        Column("email", "Email"),
        Column("role", "Role", "enum"),
        Column("status", "Status", "enum"),
        Column("last_login_at", "Last login", "datetime"),
        Column("days_inactive", "Days inactive", "integer", align="right"),
        Column("mfa_enabled", "MFA", "boolean"),
    ],
    default_columns=["name", "email", "role", "last_login_at", "days_inactive", "mfa_enabled"],
    filters=[
        Filter("role", "Role", "enum",
               options=STATUS_OPTS(["admin", "doctor", "staff", "patient", "super_admin"])),
    ],
    sort_options=[SortOption("last_login_at", "Last login"),
                  SortOption("days_inactive", "Inactivity")],
    default_sort="last_login_at", default_sort_dir="desc",
    runner=_run_user_last_login,
))


# ---------------------------------------------------------------------------
# 22. Workforce invitations (Workforce)
# ---------------------------------------------------------------------------

async def _run_workforce_invitations(qc: QueryContext) -> RunResult:
    q: dict = {}
    if qc.tenant.tenant_id and not qc.tenant.is_platform_admin:
        q["tenant_id"] = qc.tenant.tenant_id
    if qc.filters.get("status"):
        q["status"] = qc.filters["status"]
    _date_range_filter(q, qc.filters, "created_at")

    db = tenant_db(qc.tenant.tenant_id)
    total = await db.workforce_invitations.count_documents(q)
    cursor = db.workforce_invitations.find(q, {"_id": 0, "token_hash": 0}).sort(_sort_spec(qc, _INVITE_DEF))
    cursor = cursor.skip(max(0, (qc.page - 1) * qc.page_size)).limit(qc.page_size)
    raw = [i async for i in cursor]
    rows = [{
        "email": i.get("email"),
        "role": i.get("role"),
        "status": i.get("status"),
        "created_at": i.get("created_at"),
        "invited_by": i.get("invited_by_email"),
        "accepted_at": i.get("accepted_at"),
        "expires_at": i.get("expires_at"),
    } for i in raw]
    return RunResult(rows=rows, total=total)


_INVITE_DEF = register(ReportDefinition(
    name="workforce_invitations",
    title="Workforce invitations",
    category="Workforce",
    description="Staff invitations with status (pending, accepted, revoked) and expiry.",
    required_permission=("reporting", "read"),
    columns=[
        Column("email", "Email"),
        Column("role", "Role", "enum"),
        Column("status", "Status", "enum"),
        Column("created_at", "Invited", "datetime"),
        Column("invited_by", "Invited by"),
        Column("accepted_at", "Accepted", "datetime"),
        Column("expires_at", "Expires", "datetime", hidden_by_default=True),
    ],
    default_columns=["email", "role", "status", "created_at", "invited_by", "accepted_at"],
    filters=[
        Filter("status", "Status", "enum",
               options=STATUS_OPTS(["pending", "accepted", "revoked", "expired"])),
        Filter("from", "Invited from", "date_range"),
        Filter("to", "Invited to", "date_range"),
    ],
    sort_options=[SortOption("created_at", "Invited")],
    default_sort="created_at", default_sort_dir="desc",
    runner=_run_workforce_invitations,
))


# ---------------------------------------------------------------------------
# Public convenience list — force import side effects at module load.
# ---------------------------------------------------------------------------

__all__ = [
    "_NEW_PATIENTS_DEF",
    "_COMPLETENESS_DEF",
    "_ACTIVE_DEF",
    "_CANCEL_DEF",
    "_NBP_DEF",
    "_PBM_DEF",
    "_PAT_BAL_DEF",
    "_PHI_DEF",
    "_FAIL_DEF",
    "_EXPORT_HISTORY_DEF",
    "_LAST_LOGIN_DEF",
    "_INVITE_DEF",
]
