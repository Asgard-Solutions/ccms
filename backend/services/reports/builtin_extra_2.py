"""
Third wave of reports — closes the remaining gaps from the product
wishlist. Reuses the same patterns as `builtin.py` and `builtin_extra.py`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.db import get_db_read
from core.tenancy import tenant_db
from core.tenant_scope import scoped_filter
from services.reports.builtin import (
    STATUS_OPTS,
    _base_filter,
    _date_range_filter,
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
# 23. Billing adjustments (write-offs, discounts, courtesy, contractual)
# ---------------------------------------------------------------------------

async def _run_billing_adjustments(qc: QueryContext) -> RunResult:
    q = scoped_filter({}, qc.tenant)
    if q.get("__deny__"):
        return RunResult(rows=[], total=0)
    if qc.filters.get("kind"):
        q["kind"] = qc.filters["kind"]
    _date_range_filter(q, qc.filters, "created_at")

    db = tenant_db(qc.tenant.tenant_id)
    total = await db.billing_adjustments.count_documents(q)
    cursor = db.billing_adjustments.find(q, {"_id": 0}).sort(_sort_spec(qc, _ADJ_DEF))
    cursor = cursor.skip(max(0, (qc.page - 1) * qc.page_size)).limit(qc.page_size)
    raw = [a async for a in cursor]
    rows = [{
        "id": a.get("id"),
        "created_at": a.get("created_at"),
        "kind": a.get("kind"),
        "amount_cents": a.get("amount_cents", 0),
        "reason": a.get("reason"),
        "invoice_id": a.get("invoice_id"),
        "approved_by_id": a.get("approved_by_id"),
    } for a in raw]

    # Aggregate totals by kind
    agg = db.billing_adjustments.aggregate([
        {"$match": q},
        {"$group": {"_id": "$kind",
                    "total_cents": {"$sum": "$amount_cents"},
                    "count": {"$sum": 1}}},
    ])
    by_kind = [{"kind": r["_id"], "count": r["count"], "total_cents": int(r["total_cents"] or 0)} async for r in agg]
    total_cents = sum(r["total_cents"] for r in by_kind)
    return RunResult(rows=rows, total=total, aggregates={
        "total_cents": total_cents,
        "by_kind": {r["kind"]: r["total_cents"] for r in by_kind},
    })


_ADJ_DEF = register(ReportDefinition(
    name="billing_adjustments",
    title="Billing adjustments",
    category="Financial",
    description="Write-offs, discounts, courtesy, and contractual adjustments.",
    required_permission=("reporting", "read_financial"),
    columns=[
        Column("created_at", "Posted", "datetime"),
        Column("kind", "Kind", "enum"),
        Column("amount_cents", "Amount", "currency", align="right"),
        Column("reason", "Reason"),
        Column("invoice_id", "Invoice #"),
        Column("approved_by_id", "Approved by", hidden_by_default=True),
    ],
    default_columns=["created_at", "kind", "amount_cents", "reason", "invoice_id"],
    filters=[
        Filter("kind", "Kind", "enum",
               options=STATUS_OPTS(["writeoff", "discount", "courtesy", "contractual"])),
        Filter("from", "Posted from", "date_range"),
        Filter("to", "Posted to", "date_range"),
    ],
    sort_options=[SortOption("created_at", "Posted"),
                  SortOption("amount_cents", "Amount")],
    default_sort="created_at", default_sort_dir="desc",
    runner=_run_billing_adjustments,
))


# ---------------------------------------------------------------------------
# 24. ICD diagnoses distribution (Clinical — aggregate)
# ---------------------------------------------------------------------------

async def _run_icd_distribution(qc: QueryContext) -> RunResult:
    q = scoped_filter({}, qc.tenant)
    if q.get("__deny__"):
        return RunResult(rows=[], total=0)
    _date_range_filter(q, qc.filters, "onset_date")
    if qc.filters.get("status"):
        q["status"] = qc.filters["status"]

    db = tenant_db(qc.tenant.tenant_id)
    pipeline = [
        {"$match": q},
        {"$group": {
            "_id": {"code": "$icd10_code", "label": "$label"},
            "count": {"$sum": 1},
            "primary_count": {"$sum": {"$cond": [{"$eq": ["$is_primary", True]}, 1, 0]}},
        }},
    ]
    raw = [r async for r in db.clinical_diagnoses.aggregate(pipeline)]
    rows = [{
        "code": r["_id"].get("code"),
        "label": r["_id"].get("label"),
        "count": r["count"],
        "primary_count": r["primary_count"],
    } for r in raw]
    sort_key = resolve_sort(_ICD_DEF, qc.sort)
    rows.sort(key=lambda x: x.get(sort_key) or 0, reverse=qc.sort_dir == "desc")
    return RunResult(rows=_paged(rows, qc.page, qc.page_size), total=len(rows))


_ICD_DEF = register(ReportDefinition(
    name="icd_diagnoses_distribution",
    title="ICD-10 diagnosis distribution",
    category="Clinical",
    description="Most common ICD-10 codes captured on active/resolved chart diagnoses.",
    required_permission=("reporting", "read_clinical"),
    columns=[
        Column("code", "ICD-10"),
        Column("label", "Label"),
        Column("count", "Total", "integer", align="right"),
        Column("primary_count", "As primary", "integer", align="right"),
    ],
    default_columns=["code", "label", "count", "primary_count"],
    filters=[
        Filter("status", "Status", "enum",
               options=STATUS_OPTS(["active", "resolved"])),
        Filter("from", "Onset from", "date_range"),
        Filter("to", "Onset to", "date_range"),
    ],
    sort_options=[SortOption("count", "Total"),
                  SortOption("primary_count", "As primary")],
    default_sort="count", default_sort_dir="desc",
    runner=_run_icd_distribution,
))


# ---------------------------------------------------------------------------
# 25. CPT procedure utilisation (Financial — from billed claim lines)
# ---------------------------------------------------------------------------

async def _run_cpt_utilisation(qc: QueryContext) -> RunResult:
    q = scoped_filter({}, qc.tenant)
    if q.get("__deny__"):
        return RunResult(rows=[], total=0)
    _date_range_filter(q, qc.filters, "created_at")

    db = tenant_db(qc.tenant.tenant_id)
    pipeline = [
        {"$match": q},
        {"$group": {
            "_id": "$procedure_code",
            "count": {"$sum": 1},
            "billed_cents": {"$sum": "$billed_cents"},
            "units": {"$sum": "$units"},
        }},
    ]
    raw = [r async for r in db.claim_lines.aggregate(pipeline)]
    rows = [{
        "code": r["_id"],
        "count": r["count"],
        "units": int(r.get("units") or 0),
        "billed_cents": int(r.get("billed_cents") or 0),
    } for r in raw if r["_id"]]
    sort_key = resolve_sort(_CPT_DEF, qc.sort)
    rows.sort(key=lambda x: x.get(sort_key) or 0, reverse=qc.sort_dir == "desc")
    return RunResult(rows=_paged(rows, qc.page, qc.page_size), total=len(rows))


_CPT_DEF = register(ReportDefinition(
    name="cpt_procedure_utilisation",
    title="CPT procedure utilisation",
    category="Financial",
    description="Most billed CPT codes with total billed dollars.",
    required_permission=("reporting", "read_financial"),
    columns=[
        Column("code", "CPT"),
        Column("count", "Lines", "integer", align="right"),
        Column("units", "Units", "integer", align="right"),
        Column("billed_cents", "Billed", "currency", align="right"),
    ],
    default_columns=["code", "count", "units", "billed_cents"],
    filters=[
        Filter("from", "Created from", "date_range"),
        Filter("to", "Created to", "date_range"),
    ],
    sort_options=[SortOption("count", "Lines"),
                  SortOption("billed_cents", "Billed"),
                  SortOption("units", "Units")],
    default_sort="billed_cents", default_sort_dir="desc",
    runner=_run_cpt_utilisation,
))


# ---------------------------------------------------------------------------
# 26. Missing documentation (Clinical)
# ---------------------------------------------------------------------------

async def _run_missing_documentation(qc: QueryContext) -> RunResult:
    """Completed appointments that have no linked clinical follow-up note."""
    q = _base_filter(qc, location_scoped=True)
    if q.get("__deny__"):
        return RunResult(rows=[], total=0)
    q["status"] = "completed"
    _date_range_filter(q, qc.filters, "start_time")

    db = tenant_db(qc.tenant.tenant_id)
    # Collect completed appt ids → check against note appt_id / encounter_id
    appts = db.appointments.find(q, {"_id": 0, "id": 1, "patient_id": 1,
                                     "provider_id": 1, "start_time": 1,
                                     "encounter_id": 1})
    completed = [a async for a in appts]
    encounter_ids = [a["encounter_id"] for a in completed if a.get("encounter_id")]

    # Notes hanging off those encounters
    note_q = {"encounter_id": {"$in": encounter_ids}}
    if qc.tenant.tenant_id and not qc.tenant.is_platform_admin:
        note_q["tenant_id"] = qc.tenant.tenant_id
    have = {n["encounter_id"] async for n in db.clinical_follow_up_notes.find(
        note_q, {"_id": 0, "encounter_id": 1})}

    # Missing = completed appts whose encounter has no note
    missing = [a for a in completed if a.get("encounter_id") and a["encounter_id"] not in have]
    # Also include completed appts without any encounter at all
    missing += [a for a in completed if not a.get("encounter_id")]
    total = len(missing)

    page = missing[max(0, (qc.page - 1) * qc.page_size):qc.page * qc.page_size]
    pats = await _hydrate_patients(qc.tenant.tenant_id, [a["patient_id"] for a in page])
    provs = await _hydrate_users(qc.tenant.tenant_id, [a.get("provider_id") for a in page if a.get("provider_id")])
    rows = []
    now = datetime.now(timezone.utc)
    for a in page:
        p = pats.get(a["patient_id"], {})
        start = a.get("start_time")
        age_days = None
        if start:
            try:
                s = datetime.fromisoformat(start.replace("Z", "+00:00"))
                age_days = (now - s).days
            except ValueError:
                pass
        rows.append({
            "appointment_id": a.get("id"),
            "date_of_service": start,
            "patient_name": f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() or None,
            "provider_name": (provs.get(a.get("provider_id", "")) or {}).get("name"),
            "age_days": age_days,
            "reason": "no encounter" if not a.get("encounter_id") else "no follow-up note",
        })
    return RunResult(rows=rows, total=total)


_MISSING_DOC_DEF = register(ReportDefinition(
    name="missing_documentation",
    title="Missing documentation",
    category="Clinical",
    description="Completed appointments with no encounter record or no follow-up note attached.",
    required_permission=("reporting", "read_clinical"),
    columns=[
        Column("date_of_service", "DOS", "datetime"),
        Column("patient_name", "Patient", phi=True),
        Column("provider_name", "Provider"),
        Column("age_days", "Age (days)", "integer", align="right"),
        Column("reason", "Gap"),
    ],
    default_columns=["date_of_service", "patient_name", "provider_name", "age_days", "reason"],
    filters=[
        Filter("from", "DOS from", "date_range"),
        Filter("to", "DOS to", "date_range"),
    ],
    sort_options=[SortOption("start_time", "DOS")],
    default_sort="start_time", default_sort_dir="asc",
    contains_phi=True,
    runner=_run_missing_documentation,
))


# ---------------------------------------------------------------------------
# 27. Addendum activity (Clinical)
# ---------------------------------------------------------------------------

async def _run_addendum_activity(qc: QueryContext) -> RunResult:
    q = scoped_filter({}, qc.tenant)
    if q.get("__deny__"):
        return RunResult(rows=[], total=0)
    if qc.filters.get("status"):
        q["status"] = qc.filters["status"]
    _date_range_filter(q, qc.filters, "created_at")

    db = tenant_db(qc.tenant.tenant_id)
    total = await db.clinical_addenda.count_documents(q)
    cursor = db.clinical_addenda.find(q, {"_id": 0}).sort(_sort_spec(qc, _ADDENDA_DEF))
    cursor = cursor.skip(max(0, (qc.page - 1) * qc.page_size)).limit(qc.page_size)
    raw = [a async for a in cursor]
    authors = await _hydrate_users(qc.tenant.tenant_id, list({a["author_id"] for a in raw if a.get("author_id")}))
    rows = [{
        "created_at": a.get("created_at"),
        "status": a.get("status"),
        "parent_type": a.get("parent_type"),
        "parent_id": a.get("parent_id"),
        "author_name": (authors.get(a.get("author_id", "")) or {}).get("name"),
        "text_length": len(a.get("body") or ""),
    } for a in raw]
    return RunResult(rows=rows, total=total)


_ADDENDA_DEF = register(ReportDefinition(
    name="addendum_activity",
    title="Addendum activity",
    category="Clinical",
    description="Addenda posted to encounters, notes, and exams.",
    required_permission=("reporting", "read_clinical"),
    columns=[
        Column("created_at", "Posted", "datetime"),
        Column("status", "Status", "enum"),
        Column("author_name", "Author"),
        Column("parent_type", "Parent"),
        Column("parent_id", "Parent ID", hidden_by_default=True),
        Column("text_length", "Body length", "integer", align="right", hidden_by_default=True),
    ],
    default_columns=["created_at", "status", "author_name", "parent_type"],
    filters=[
        Filter("status", "Status", "enum",
               options=STATUS_OPTS(["draft", "signed"])),
        Filter("from", "From", "date_range"),
        Filter("to", "To", "date_range"),
    ],
    sort_options=[SortOption("created_at", "Posted")],
    default_sort="created_at", default_sort_dir="desc",
    runner=_run_addendum_activity,
))


# ---------------------------------------------------------------------------
# 28. Treatment-plan status (Clinical)
# ---------------------------------------------------------------------------

async def _run_treatment_plan_status(qc: QueryContext) -> RunResult:
    q = scoped_filter({}, qc.tenant)
    if q.get("__deny__"):
        return RunResult(rows=[], total=0)
    if qc.filters.get("plan_status"):
        q["plan_status"] = qc.filters["plan_status"]

    db = tenant_db(qc.tenant.tenant_id)
    total = await db.clinical_treatment_plans.count_documents(q)
    cursor = db.clinical_treatment_plans.find(q, {"_id": 0}).sort(_sort_spec(qc, _TP_DEF))
    cursor = cursor.skip(max(0, (qc.page - 1) * qc.page_size)).limit(qc.page_size)
    raw = [t async for t in cursor]
    pats = await _hydrate_patients(qc.tenant.tenant_id, list({t["patient_id"] for t in raw}))
    provs = await _hydrate_users(qc.tenant.tenant_id, list({t.get("provider_id") for t in raw if t.get("provider_id")}))
    rows = []
    for t in raw:
        p = pats.get(t["patient_id"], {})
        prog = t.get("progress") or {}
        rows.append({
            "patient_name": f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() or None,
            "plan_status": t.get("plan_status"),
            "provider_name": (provs.get(t.get("provider_id", "")) or {}).get("name"),
            "started_at": t.get("started_at") or t.get("created_at"),
            "visits_completed": int(prog.get("visits_completed") or 0),
            "discharged_at": t.get("discharged_at"),
        })
    return RunResult(rows=rows, total=total)


_TP_DEF = register(ReportDefinition(
    name="treatment_plan_status",
    title="Treatment plans",
    category="Clinical",
    description="Active, on-hold, completed, and discharged care plans.",
    required_permission=("reporting", "read_clinical"),
    columns=[
        Column("patient_name", "Patient", phi=True),
        Column("plan_status", "Status", "enum"),
        Column("provider_name", "Provider"),
        Column("started_at", "Started", "datetime"),
        Column("visits_completed", "Visits", "integer", align="right"),
        Column("discharged_at", "Discharged", "datetime", hidden_by_default=True),
    ],
    default_columns=["patient_name", "plan_status", "provider_name", "started_at", "visits_completed"],
    filters=[
        Filter("plan_status", "Plan status", "enum",
               options=STATUS_OPTS(["active", "on_hold", "completed", "discharged", "cancelled"])),
    ],
    sort_options=[SortOption("started_at", "Started"),
                  SortOption("visits_completed", "Visits")],
    default_sort="started_at", default_sort_dir="desc",
    contains_phi=True,
    runner=_run_treatment_plan_status,
))


# ---------------------------------------------------------------------------
# 29. Patient age cohort (Patient)
# ---------------------------------------------------------------------------

_AGE_BUCKETS = [
    (0, 12, "0-12"),
    (13, 17, "13-17"),
    (18, 34, "18-34"),
    (35, 54, "35-54"),
    (55, 74, "55-74"),
    (75, 200, "75+"),
]


async def _run_age_cohort(qc: QueryContext) -> RunResult:
    q = _base_filter(qc, location_scoped=True)
    if q.get("__deny__"):
        return RunResult(rows=[], total=0)
    q.setdefault("status", {"$ne": "deleted"})
    q["date_of_birth"] = {"$nin": [None, ""]}

    db = tenant_db(qc.tenant.tenant_id)
    today = datetime.now(timezone.utc).date()
    counts = {label: 0 for _, _, label in _AGE_BUCKETS}
    counts["unknown"] = 0
    cursor = db.patients.find(q, {"_id": 0, "date_of_birth": 1})
    async for p in cursor:
        dob = p.get("date_of_birth")
        if not dob or not isinstance(dob, str) or len(dob) < 10:
            counts["unknown"] += 1
            continue
        try:
            d = datetime.fromisoformat(dob[:10]).date()
            age = today.year - d.year - ((today.month, today.day) < (d.month, d.day))
        except ValueError:
            counts["unknown"] += 1
            continue
        for lo, hi, label in _AGE_BUCKETS:
            if lo <= age <= hi:
                counts[label] += 1
                break
    rows = [{"cohort": label, "patients": counts[label]} for _, _, label in _AGE_BUCKETS]
    if counts["unknown"]:
        rows.append({"cohort": "unknown", "patients": counts["unknown"]})
    return RunResult(rows=rows, total=len(rows),
                     aggregates={"total": sum(counts.values())})


_AGE_DEF = register(ReportDefinition(
    name="patient_age_cohort",
    title="Patient age cohorts",
    category="Patient",
    description="Active patient counts grouped into standard age cohorts.",
    required_permission=("reporting", "read"),
    columns=[
        Column("cohort", "Cohort"),
        Column("patients", "Patients", "integer", align="right"),
    ],
    default_columns=["cohort", "patients"],
    filters=[],
    sort_options=[SortOption("patients", "Patients")],
    default_sort="patients", default_sort_dir="desc",
    runner=_run_age_cohort,
))


# ---------------------------------------------------------------------------
# 30. Patient responsibility / insurance coverage summary (Patient)
# ---------------------------------------------------------------------------

async def _run_responsibility_summary(qc: QueryContext) -> RunResult:
    q = _base_filter(qc, location_scoped=True)
    if q.get("__deny__"):
        return RunResult(rows=[], total=0)
    q.setdefault("status", {"$ne": "deleted"})
    db = tenant_db(qc.tenant.tenant_id)
    pipeline = [
        {"$match": q},
        {"$group": {"_id": {"$ifNull": ["$case_details.responsibility", "unknown"]},
                    "count": {"$sum": 1}}},
    ]
    raw = [r async for r in db.patients.aggregate(pipeline)]
    rows = [{"responsibility": r["_id"], "patients": r["count"]} for r in raw]
    rows.sort(key=lambda x: x["patients"], reverse=True)
    return RunResult(rows=rows, total=len(rows),
                     aggregates={"total": sum(r["patients"] for r in rows)})


_RESP_DEF = register(ReportDefinition(
    name="patient_responsibility_summary",
    title="Patient payment responsibility",
    category="Patient",
    description="Patients grouped by responsibility mode (self-pay, insurance, mixed).",
    required_permission=("reporting", "read_financial"),
    columns=[
        Column("responsibility", "Responsibility", "enum"),
        Column("patients", "Patients", "integer", align="right"),
    ],
    default_columns=["responsibility", "patients"],
    filters=[],
    sort_options=[SortOption("patients", "Patients")],
    default_sort="patients", default_sort_dir="desc",
    runner=_run_responsibility_summary,
))


# ---------------------------------------------------------------------------
# 31. Break-glass usage (Compliance)
# ---------------------------------------------------------------------------

async def _run_break_glass(qc: QueryContext) -> RunResult:
    q: dict = {"action": {"$regex": r"^(break_glass\.|security\.break_glass)"}}
    if qc.tenant.tenant_id and not qc.tenant.is_platform_admin:
        q["tenant_id"] = qc.tenant.tenant_id
    _date_range_filter(q, qc.filters, "created_at")

    db = get_db_read()
    total = await db.audit_logs.count_documents(q)
    cursor = db.audit_logs.find(q, {"_id": 0}).sort(_sort_spec(qc, _BG_DEF))
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
        "outcome": r.get("outcome"),
    } for r in raw]
    return RunResult(rows=rows, total=total)


_BG_DEF = register(ReportDefinition(
    name="break_glass_usage",
    title="Break-glass usage",
    category="Compliance",
    description="Every break-glass privileged-access activation and attestation event.",
    required_permission=("audit_log", "read"),
    columns=[
        Column("created_at", "When", "datetime"),
        Column("action", "Action"),
        Column("actor_email", "Actor"),
        Column("actor_role", "Role"),
        Column("entity_type", "Entity"),
        Column("entity_id", "Entity ID", hidden_by_default=True),
        Column("reason", "Reason"),
        Column("outcome", "Outcome", "enum"),
    ],
    default_columns=["created_at", "action", "actor_email", "reason", "outcome"],
    filters=[
        Filter("from", "From", "date_range"),
        Filter("to", "To", "date_range"),
    ],
    sort_options=[SortOption("created_at", "When")],
    default_sort="created_at", default_sort_dir="desc",
    runner=_run_break_glass,
))


# ---------------------------------------------------------------------------
# 32. Daily appointment production (Scheduling)
# ---------------------------------------------------------------------------

async def _run_daily_production(qc: QueryContext) -> RunResult:
    q = _base_filter(qc, location_scoped=True)
    if q.get("__deny__"):
        return RunResult(rows=[], total=0)
    _date_range_filter(q, qc.filters, "start_time")

    db = tenant_db(qc.tenant.tenant_id)
    # Day-bucket using the string representation of start_time (ISO, UTC-stable).
    pipeline = [
        {"$match": q},
        {"$project": {
            "day": {"$substrBytes": [{"$ifNull": ["$start_time", ""]}, 0, 10]},
            "status": 1,
        }},
        {"$group": {
            "_id": "$day",
            "scheduled": {"$sum": {"$cond": [{"$eq": ["$status", "scheduled"]}, 1, 0]}},
            "completed": {"$sum": {"$cond": [{"$eq": ["$status", "completed"]}, 1, 0]}},
            "cancelled": {"$sum": {"$cond": [{"$eq": ["$status", "cancelled"]}, 1, 0]}},
            "no_show": {"$sum": {"$cond": [{"$eq": ["$status", "no_show"]}, 1, 0]}},
            "total": {"$sum": 1},
        }},
    ]
    raw = [r async for r in db.appointments.aggregate(pipeline) if r["_id"]]
    rows = [{
        "day": r["_id"],
        "scheduled": r["scheduled"],
        "completed": r["completed"],
        "cancelled": r["cancelled"],
        "no_show": r["no_show"],
        "total": r["total"],
    } for r in raw]
    sort_key = resolve_sort(_DAILY_DEF, qc.sort)
    rows.sort(key=lambda x: x.get(sort_key) or 0, reverse=qc.sort_dir == "desc")
    return RunResult(rows=_paged(rows, qc.page, qc.page_size), total=len(rows))


_DAILY_DEF = register(ReportDefinition(
    name="daily_appointment_production",
    title="Daily appointment production",
    category="Scheduling",
    description="Appointment counts by day and status — rolls up week-over-week volume.",
    required_permission=("reporting", "read"),
    columns=[
        Column("day", "Day", "date"),
        Column("scheduled", "Scheduled", "integer", align="right"),
        Column("completed", "Completed", "integer", align="right"),
        Column("cancelled", "Cancelled", "integer", align="right"),
        Column("no_show", "No-show", "integer", align="right"),
        Column("total", "Total", "integer", align="right"),
    ],
    default_columns=["day", "scheduled", "completed", "cancelled", "no_show", "total"],
    filters=[
        Filter("from", "From date", "date_range"),
        Filter("to", "To date", "date_range"),
    ],
    sort_options=[SortOption("day", "Day"),
                  SortOption("total", "Total"),
                  SortOption("completed", "Completed")],
    default_sort="day", default_sort_dir="desc",
    runner=_run_daily_production,
))


# ---------------------------------------------------------------------------
# 33. Appointment status summary (Scheduling)
# ---------------------------------------------------------------------------

async def _run_appt_status_summary(qc: QueryContext) -> RunResult:
    q = _base_filter(qc, location_scoped=True)
    if q.get("__deny__"):
        return RunResult(rows=[], total=0)
    _date_range_filter(q, qc.filters, "start_time")
    db = tenant_db(qc.tenant.tenant_id)
    pipeline = [
        {"$match": q},
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
    ]
    raw = [r async for r in db.appointments.aggregate(pipeline)]
    total_n = sum(r["count"] for r in raw)
    rows = [{
        "status": r["_id"] or "unknown",
        "count": r["count"],
        "share_pct": round((r["count"] / total_n) * 100, 1) if total_n else 0,
    } for r in raw]
    rows.sort(key=lambda x: x["count"], reverse=True)
    return RunResult(rows=rows, total=len(rows),
                     aggregates={"total": total_n})


_STATUS_SUMMARY_DEF = register(ReportDefinition(
    name="appointment_status_summary",
    title="Appointment status summary",
    category="Scheduling",
    description="Appointment counts and share by status for the selected period.",
    required_permission=("reporting", "read"),
    columns=[
        Column("status", "Status", "enum"),
        Column("count", "Count", "integer", align="right"),
        Column("share_pct", "Share (%)", "number", align="right"),
    ],
    default_columns=["status", "count", "share_pct"],
    filters=[
        Filter("from", "From", "date_range"),
        Filter("to", "To", "date_range"),
    ],
    sort_options=[SortOption("count", "Count")],
    default_sort="count", default_sort_dir="desc",
    runner=_run_appt_status_summary,
))
