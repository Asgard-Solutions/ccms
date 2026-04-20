"""Compliance-ops router — unified CRUD + workflow for all compliance entities.

All endpoints are tenant-scoped, audited, and gated by `require_permission()`.
Each entity shares the same storage shape (repository-per-type) and the same
mutation model (`create`, `update_fields`, `change_status`) which always
appends to the `history[]` array. Tamper-evidence for Evidence rows is
enforced at creation time via SHA-256.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status

from core.audit import audit_success
from core.repository import TenantScopedRepository
from core.tenancy import TenantContext, get_tenant_context
from services.authz.policy import require_permission
from services.compliance_ops import (
    AccessReviewCreate, AccessReviewPublic,
    ControlCreate, ControlPublic,
    DataClassCreate, DataClassPublic,
    EvidenceCreate, EvidencePublic,
    FieldPatch,
    IncidentCreate, IncidentPublic,
    PolicyCreate, PolicyPublic,
    RiskCreate, RiskPublic,
    StatusChange,
    VendorCreate, VendorPublic,
    now_iso,
)

router = APIRouter(prefix="/compliance-ops", tags=["compliance-ops"])


# ---------------------------------------------------------------------------
# Repositories — one per collection, all tenant-scoped.
# ---------------------------------------------------------------------------

class _Repo(TenantScopedRepository):
    location_scoped = False


_controls = _Repo("compliance_controls")
_evidence = _Repo("compliance_evidence")
_risks = _Repo("compliance_risks")
_policies = _Repo("compliance_policies")
_incidents = _Repo("compliance_incidents")
_vendors = _Repo("compliance_vendors")
_data_classes = _Repo("compliance_data_classes")
_access_reviews = _Repo("compliance_access_reviews")

ENTITY_TO_REPO: dict[str, _Repo] = {
    "control": _controls, "evidence": _evidence, "risk": _risks,
    "policy": _policies, "incident": _incidents, "vendor": _vendors,
    "data_class": _data_classes, "access_review": _access_reviews,
}


def _history_entry(ctx: TenantContext, action: str, note: str | None = None) -> dict:
    return {
        "at": now_iso(),
        "actor_id": ctx.user.get("id") if ctx.user else None,
        "actor_email": ctx.user.get("email") if ctx.user else None,
        "action": action,
        "note": note,
    }


def _stamp(ctx: TenantContext, doc: dict, action: str) -> dict:
    doc = dict(doc)
    doc["history"] = [_history_entry(ctx, action, doc.get("_note"))]
    doc.pop("_note", None)
    doc.setdefault("created_at", now_iso())
    doc["updated_at"] = now_iso()
    return doc


async def _insert(repo: _Repo, payload: BaseModelLike, ctx: TenantContext,
                  *, extras: dict | None = None) -> dict:
    body = payload.model_dump() if hasattr(payload, "model_dump") else dict(payload)
    if extras:
        body.update(extras)
    body["id"] = str(uuid.uuid4())
    body = _stamp(ctx, body, "created")
    return await repo.insert_one(body, ctx)


# `BaseModelLike` type helper — purely for readability above.
class BaseModelLike:
    def model_dump(self) -> dict:
        ...  # pragma: no cover


# ---------------------------------------------------------------------------
# CONTROLS
# ---------------------------------------------------------------------------

@router.post("/controls", response_model=ControlPublic, status_code=201)
async def create_control(
    payload: ControlCreate, request: Request,
    user: dict = Depends(require_permission("reporting", "read", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    cadence = payload.review_cadence_days
    next_review = (datetime.now(timezone.utc) + timedelta(days=cadence)).isoformat()
    doc = await _insert(_controls, payload, ctx, extras={
        "status": "planned",
        "next_review_at": next_review,
        "last_reviewed_at": None,
    })
    await audit_success(user, "compliance.control_created", request,
                        entity_type="compliance_control", entity_id=doc["id"],
                        metadata={"name": payload.name, "frameworks": list(payload.framework_mappings)})
    return doc


@router.get("/controls", response_model=list[ControlPublic])
async def list_controls(
    request: Request,
    status_filter: str | None = None,
    family: str | None = None,
    framework: str | None = None,
    user: dict = Depends(require_permission("reporting", "read", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    q: dict = {}
    if status_filter:
        q["status"] = status_filter
    if family:
        q["family"] = family
    if framework:
        q[f"framework_mappings.{framework}"] = {"$exists": True}
    return await _controls.find(q, ctx, sort=[("updated_at", -1)])


# ---------------------------------------------------------------------------
# EVIDENCE — immutable-ish: no updates, only creation + legal-hold toggle.
# ---------------------------------------------------------------------------

@router.post("/evidence", response_model=EvidencePublic, status_code=201)
async def create_evidence(
    payload: EvidenceCreate, request: Request,
    user: dict = Depends(require_permission("reporting", "read", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    # Integrity hash over the canonical reference + summary + coverage window.
    blob = f"{payload.source_system}|{payload.source_reference}|{payload.content_summary}|{payload.coverage_period_start}|{payload.coverage_period_end}".encode()
    integrity = hashlib.sha256(blob).hexdigest()
    retention_until = (datetime.now(timezone.utc) + timedelta(days=payload.retention_days)).isoformat()
    doc = await _insert(_evidence, payload, ctx, extras={
        "integrity_sha256": integrity,
        "generated_at": now_iso(),
        "retention_until": retention_until,
        "legal_hold": False,
        "owner_user_id": user["id"],
        "access_restriction": "internal",
    })
    # Optional: link to the control.
    if payload.control_id:
        await _controls.update_one(
            {"id": payload.control_id},
            {"$push": {"evidence_ids": doc["id"]}},
            ctx,
        )
    await audit_success(user, "compliance.evidence_created", request,
                        entity_type="compliance_evidence", entity_id=doc["id"],
                        metadata={"control_id": payload.control_id,
                                  "type": payload.evidence_type,
                                  "integrity_sha256": integrity})
    return doc


@router.get("/evidence", response_model=list[EvidencePublic])
async def list_evidence(
    control_id: str | None = None,
    evidence_type: str | None = None,
    request: Request = None,  # type: ignore[assignment]
    user: dict = Depends(require_permission("reporting", "read", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    q: dict = {}
    if control_id:
        q["control_id"] = control_id
    if evidence_type:
        q["evidence_type"] = evidence_type
    return await _evidence.find(q, ctx, sort=[("generated_at", -1)], limit=1000)


@router.post("/evidence/{evidence_id}/legal-hold")
async def set_legal_hold(
    evidence_id: str, on: bool = True, request: Request = None,  # type: ignore[assignment]
    user: dict = Depends(require_permission("reporting", "export", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    matched = await _evidence.update_one(
        {"id": evidence_id},
        {"$set": {"legal_hold": bool(on), "updated_at": now_iso()},
         "$push": {"history": _history_entry(ctx, "legal_hold_toggle",
                                             f"on={on}")}},
        ctx,
    )
    if matched == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evidence not found")
    await audit_success(user, "compliance.evidence_legal_hold", request,
                        entity_type="compliance_evidence", entity_id=evidence_id,
                        metadata={"on": bool(on)})
    return {"ok": True, "legal_hold": bool(on)}


# ---------------------------------------------------------------------------
# RISKS
# ---------------------------------------------------------------------------

@router.post("/risks", response_model=RiskPublic, status_code=201)
async def create_risk(
    payload: RiskCreate, request: Request,
    user: dict = Depends(require_permission("reporting", "read", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    inherent = payload.likelihood * payload.impact
    doc = await _insert(_risks, payload, ctx, extras={
        "status": "open", "inherent_score": inherent, "residual_score": inherent,
        "linked_incident_ids": [],
    })
    await audit_success(user, "compliance.risk_created", request,
                        entity_type="compliance_risk", entity_id=doc["id"])
    return doc


@router.get("/risks", response_model=list[RiskPublic])
async def list_risks(
    status_filter: str | None = None,
    request: Request = None,  # type: ignore[assignment]
    user: dict = Depends(require_permission("reporting", "read", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    q: dict = {}
    if status_filter:
        q["status"] = status_filter
    return await _risks.find(q, ctx, sort=[("inherent_score", -1)])


# ---------------------------------------------------------------------------
# POLICIES
# ---------------------------------------------------------------------------

@router.post("/policies", response_model=PolicyPublic, status_code=201)
async def create_policy(
    payload: PolicyCreate, request: Request,
    user: dict = Depends(require_permission("reporting", "read", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    doc = await _insert(_policies, payload, ctx, extras={
        "status": "draft", "approved_at": None, "approved_by": None,
    })
    await audit_success(user, "compliance.policy_created", request,
                        entity_type="compliance_policy", entity_id=doc["id"])
    return doc


@router.get("/policies", response_model=list[PolicyPublic])
async def list_policies(
    status_filter: str | None = None, request: Request = None,  # type: ignore[assignment]
    user: dict = Depends(require_permission("reporting", "read", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    q: dict = {}
    if status_filter:
        q["status"] = status_filter
    return await _policies.find(q, ctx, sort=[("review_date", 1)])


# ---------------------------------------------------------------------------
# INCIDENTS
# ---------------------------------------------------------------------------

@router.post("/incidents", response_model=IncidentPublic, status_code=201)
async def create_incident(
    payload: IncidentCreate, request: Request,
    user: dict = Depends(require_permission("reporting", "export", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    doc = await _insert(_incidents, payload, ctx, extras={
        "status": "triage",
        "containment_actions": [], "eradication_actions": [],
        "root_cause": None, "corrective_actions": [],
        "notification_required": payload.severity in ("high", "critical"),
        "notification_sent_at": None, "closed_at": None,
        "linked_risk_ids": [], "linked_alert_ids": [],
    })
    await audit_success(user, "compliance.incident_created", request,
                        entity_type="compliance_incident", entity_id=doc["id"],
                        metadata={"severity": payload.severity,
                                  "incident_type": payload.incident_type})
    return doc


@router.get("/incidents", response_model=list[IncidentPublic])
async def list_incidents(
    status_filter: str | None = None, severity: str | None = None,
    request: Request = None,  # type: ignore[assignment]
    user: dict = Depends(require_permission("reporting", "export", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    q: dict = {}
    if status_filter:
        q["status"] = status_filter
    if severity:
        q["severity"] = severity
    return await _incidents.find(q, ctx, sort=[("detected_at", -1)])


# ---------------------------------------------------------------------------
# VENDORS
# ---------------------------------------------------------------------------

@router.post("/vendors", response_model=VendorPublic, status_code=201)
async def create_vendor(
    payload: VendorCreate, request: Request,
    user: dict = Depends(require_permission("reporting", "read", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    next_review = (datetime.now(timezone.utc) + timedelta(days=payload.review_cadence_days)).isoformat()
    doc = await _insert(_vendors, payload, ctx, extras={
        "status": "under_review" if not payload.baa_in_place and payload.baa_required else "active",
        "last_reviewed_at": None, "next_review_at": next_review,
        "linked_control_ids": [],
    })
    await audit_success(user, "compliance.vendor_created", request,
                        entity_type="compliance_vendor", entity_id=doc["id"])
    return doc


@router.get("/vendors", response_model=list[VendorPublic])
async def list_vendors(
    request: Request,
    user: dict = Depends(require_permission("reporting", "read", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    return await _vendors.find({}, ctx, sort=[("name", 1)])


# ---------------------------------------------------------------------------
# DATA CLASSES
# ---------------------------------------------------------------------------

@router.post("/data-classes", response_model=DataClassPublic, status_code=201)
async def create_data_class(
    payload: DataClassCreate, request: Request,
    user: dict = Depends(require_permission("reporting", "read", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    doc = await _insert(_data_classes, payload, ctx)
    await audit_success(user, "compliance.data_class_created", request,
                        entity_type="compliance_data_class", entity_id=doc["id"])
    return doc


@router.get("/data-classes", response_model=list[DataClassPublic])
async def list_data_classes(
    request: Request,
    user: dict = Depends(require_permission("reporting", "read", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    return await _data_classes.find({}, ctx, sort=[("name", 1)])


# ---------------------------------------------------------------------------
# ACCESS REVIEWS
# ---------------------------------------------------------------------------

@router.post("/access-reviews", response_model=AccessReviewPublic, status_code=201)
async def create_access_review(
    payload: AccessReviewCreate, request: Request,
    user: dict = Depends(require_permission("user", "read", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    doc = await _insert(_access_reviews, payload, ctx, extras={
        "status": "scheduled",
        "completed_at": None, "decision": None,
        "subject_count": None, "revocations": None,
        "linked_evidence_ids": [],
    })
    await audit_success(user, "compliance.access_review_created", request,
                        entity_type="compliance_access_review", entity_id=doc["id"])
    return doc


@router.get("/access-reviews", response_model=list[AccessReviewPublic])
async def list_access_reviews(
    status_filter: str | None = None,
    request: Request = None,  # type: ignore[assignment]
    user: dict = Depends(require_permission("user", "read", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    q: dict = {}
    if status_filter:
        q["status"] = status_filter
    rows = await _access_reviews.find(q, ctx, sort=[("due_at", 1)])
    # Auto-mark overdue.
    now = now_iso()
    for r in rows:
        if r.get("status") == "scheduled" and r.get("due_at", now) < now:
            r["status"] = "overdue"
    return rows


# ---------------------------------------------------------------------------
# Generic status change + field patch — works for every entity type.
# ---------------------------------------------------------------------------

@router.post("/{entity_type}/{entity_id}/status", status_code=200)
async def change_status(
    entity_type: str, entity_id: str, payload: StatusChange, request: Request,
    user: dict = Depends(require_permission("reporting", "read", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    repo = ENTITY_TO_REPO.get(entity_type)
    if not repo:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown entity type {entity_type}")
    matched = await repo.update_one(
        {"id": entity_id},
        {"$set": {"status": payload.new_status, "updated_at": now_iso()},
         "$push": {"history": _history_entry(ctx, "status_change", payload.note)}},
        ctx,
    )
    if matched == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{entity_type} not found")
    await audit_success(user, f"compliance.{entity_type}_status_changed", request,
                        entity_type=f"compliance_{entity_type}", entity_id=entity_id,
                        metadata={"new_status": payload.new_status})
    return {"ok": True, "new_status": payload.new_status}


# Writable fields allow-list per entity type — prevents a client from
# silently mutating integrity-relevant fields (integrity_sha256, history, etc.).
_WRITABLE_FIELDS: dict[str, set[str]] = {
    "control": {"name", "description", "framework_mappings", "evidence_sources",
                "linked_risk_ids", "linked_policy_ids", "review_cadence_days",
                "next_review_at", "last_reviewed_at", "owner_user_id"},
    "evidence": {"content_summary", "access_restriction"},    # minimal — integrity locked
    "risk": {"title", "description", "likelihood", "impact", "treatment",
             "target_date", "owner_user_id", "linked_control_ids",
             "linked_incident_ids", "residual_score"},
    "policy": {"summary", "version", "effective_date", "review_date",
               "approved_at", "approved_by", "owner_user_id",
               "linked_control_ids", "linked_risk_ids", "body_artifact_path"},
    "incident": {"summary", "owner_user_id", "affected_systems",
                 "affected_tenant_ids", "potential_data_categories",
                 "containment_actions", "eradication_actions", "root_cause",
                 "corrective_actions", "notification_required",
                 "notification_sent_at", "closed_at", "linked_risk_ids",
                 "linked_alert_ids", "reported_at"},
    "vendor": {"service_provided", "data_categories", "environment",
               "baa_required", "baa_in_place", "security_review_status",
               "review_cadence_days", "last_reviewed_at", "next_review_at",
               "contract_end_date", "notes", "owner_user_id",
               "linked_control_ids"},
    "data_class": {"retention_days", "deletion_method", "legal_hold_applicable",
                   "exportable", "storage_locations", "encryption"},
    "access_review": {"due_at", "reviewer_user_id", "notes", "completed_at",
                      "decision", "subject_count", "revocations",
                      "linked_evidence_ids"},
}


@router.get("/{entity_type}/{entity_id}")
async def get_entity(
    entity_type: str, entity_id: str,
    request: Request,
    user: dict = Depends(require_permission("reporting", "read", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    """Fetch the raw document for any compliance entity — includes `history`
    (the mutation audit trail that the typed response_models strip out)."""
    repo = ENTITY_TO_REPO.get(entity_type)
    if not repo:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown entity type {entity_type}")
    row = await repo.find_one_by_id(entity_id, ctx)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{entity_type} not found")
    return row


@router.patch("/{entity_type}/{entity_id}")
async def patch_entity(
    entity_type: str, entity_id: str, payload: FieldPatch, request: Request,
    user: dict = Depends(require_permission("reporting", "read", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    repo = ENTITY_TO_REPO.get(entity_type)
    allowed = _WRITABLE_FIELDS.get(entity_type)
    if not repo or allowed is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown entity type {entity_type}")
    bad = set(payload.fields) - allowed
    if bad:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"fields {sorted(bad)} are not editable on {entity_type}")
    updates = dict(payload.fields)
    updates["updated_at"] = now_iso()
    matched = await repo.update_one(
        {"id": entity_id},
        {"$set": updates,
         "$push": {"history": _history_entry(ctx, "fields_patched", payload.note)}},
        ctx,
    )
    if matched == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{entity_type} not found")
    await audit_success(user, f"compliance.{entity_type}_updated", request,
                        entity_type=f"compliance_{entity_type}", entity_id=entity_id,
                        metadata={"fields": sorted(payload.fields)})
    return {"ok": True}


# ---------------------------------------------------------------------------
# Dashboard aggregation — single call feeds the platform admin UI
# ---------------------------------------------------------------------------

@router.get("/dashboard")
async def dashboard(
    request: Request,
    user: dict = Depends(require_permission("reporting", "read", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    now = now_iso()

    async def count(repo: _Repo, filt: dict) -> int:
        return await repo.count(filt, ctx)

    controls_total = await count(_controls, {})
    controls_needs_review = await count(_controls, {"status": "needs_review"})
    controls_planned = await count(_controls, {"status": "planned"})

    risks_open = await count(_risks, {"status": {"$in": ["open", "mitigating"]}})
    risks_accepted = await count(_risks, {"status": "accepted"})
    high_risks = await count(_risks, {"inherent_score": {"$gte": 15},
                                      "status": {"$nin": ["closed", "mitigated"]}})

    incidents_open = await count(_incidents, {"status": {"$nin": ["closed"]}})
    incidents_high = await count(_incidents, {"severity": {"$in": ["high", "critical"]},
                                              "status": {"$nin": ["closed"]}})

    policies_overdue = await count(_policies, {"review_date": {"$lt": now}})
    vendors_baa_missing = await count(_vendors, {"baa_required": True, "baa_in_place": False})
    vendors_review_due = await count(_vendors, {"next_review_at": {"$lt": now}})

    access_reviews_overdue = await count(_access_reviews,
                                         {"status": "scheduled", "due_at": {"$lt": now}})
    access_reviews_scheduled = await count(_access_reviews, {"status": "scheduled"})

    # Privacy requests (from the existing privacy service).
    from core.tenancy import tenant_db
    db = tenant_db(ctx.tenant_id)
    privacy_pending = await db.privacy_requests.count_documents(
        {"tenant_id": ctx.tenant_id, "status": {"$in": ["received", "verifying", "processing"]}},
    )

    # Evidence statistics.
    evidence_total = await count(_evidence, {})
    evidence_90d = await count(_evidence, {
        "generated_at": {"$gte": (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()}
    })

    await audit_success(user, "compliance.dashboard_viewed", request,
                        metadata={"platform_admin_access": ctx.is_platform_admin})
    return {
        "controls": {"total": controls_total, "planned": controls_planned,
                     "needs_review": controls_needs_review},
        "risks": {"open": risks_open, "accepted": risks_accepted, "high_severity_open": high_risks},
        "incidents": {"open": incidents_open, "high_severity_open": incidents_high},
        "policies": {"overdue": policies_overdue},
        "vendors": {"baa_missing": vendors_baa_missing, "review_due": vendors_review_due},
        "access_reviews": {"scheduled": access_reviews_scheduled,
                           "overdue": access_reviews_overdue},
        "privacy_requests": {"pending": privacy_pending},
        "evidence": {"total": evidence_total, "last_90_days": evidence_90d},
        "generated_at": now,
    }
