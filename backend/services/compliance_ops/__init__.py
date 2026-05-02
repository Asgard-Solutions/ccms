"""Compliance operations — unified data model for the compliance backbone.

Domains share a common shape so the UI + audit + evidence-bundle export code
can operate on "any compliance item" without knowing its type:

    {id, tenant_id, type, status, owner, created_at, updated_at,
     review_due_at?, history:[{at, actor, action, note}], …type-specific fields}

Statuses are enumerated per-type (controls vs risks have different lifecycles),
but *transitions are always appended to history* so every change is auditable
without a separate audit row. Evidence rows add an integrity hash so
tamper attempts are detectable.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Shared primitives
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class HistoryEntry(BaseModel):
    at: str
    actor_id: str | None = None
    actor_email: str | None = None
    action: str
    note: str | None = None


# ---------------------------------------------------------------------------
# 1. Control registry
# ---------------------------------------------------------------------------

ControlStatus = Literal["planned", "in_progress", "implemented", "needs_review",
                        "exception_approved", "retired"]

CONTROL_FAMILIES = [
    "access_control", "audit", "identity", "cryptography", "config_management",
    "incident_response", "backup", "risk_management", "vendor", "privacy",
    "operations", "training",
]


class ControlCreate(BaseModel):
    name: str = Field(min_length=3, max_length=200)
    family: str
    description: str = Field(min_length=10)
    owner_user_id: str | None = None
    framework_mappings: dict[str, list[str]] = Field(default_factory=dict,
        description='{"HIPAA":["164.312(a)(1)"], "SOC2":["CC6.1"], "ISO27001":["A.9.2"], "CCPA":["Discl"]}')
    review_cadence_days: int = 90
    evidence_sources: list[str] = Field(default_factory=list)
    linked_risk_ids: list[str] = Field(default_factory=list)
    linked_policy_ids: list[str] = Field(default_factory=list)


class ControlPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    tenant_id: str
    type: Literal["control"] = "control"
    status: ControlStatus
    name: str
    family: str
    description: str
    owner_user_id: str | None
    framework_mappings: dict[str, list[str]]
    review_cadence_days: int
    evidence_sources: list[str]
    linked_risk_ids: list[str]
    linked_policy_ids: list[str]
    created_at: str
    updated_at: str
    last_reviewed_at: str | None = None
    next_review_at: str | None = None


# ---------------------------------------------------------------------------
# 2. Evidence
# ---------------------------------------------------------------------------

EvidenceType = Literal["audit_log", "access_review", "config_snapshot",
                       "backup_test", "security_alert", "export_log",
                       "vuln_scan", "incident", "key_rotation",
                       "secret_rotation", "dr_exercise", "policy_attestation",
                       "vendor_review", "manual_upload"]


class EvidenceCreate(BaseModel):
    control_id: str | None = None
    evidence_type: EvidenceType
    source_system: str
    source_reference: str = Field(description="URL, file path, audit id, etc.")
    content_summary: str
    coverage_period_start: str
    coverage_period_end: str
    retention_days: int = 2555   # 7 years; HIPAA floor
    storage_artifact_path: str | None = None


class EvidencePublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    tenant_id: str
    type: Literal["evidence"] = "evidence"
    control_id: str | None
    evidence_type: EvidenceType
    source_system: str
    source_reference: str
    content_summary: str
    integrity_sha256: str
    coverage_period_start: str
    coverage_period_end: str
    generated_at: str
    retention_days: int
    retention_until: str
    legal_hold: bool = False
    owner_user_id: str | None
    access_restriction: Literal["internal", "audit_only"] = "internal"


# ---------------------------------------------------------------------------
# 3. Risks
# ---------------------------------------------------------------------------

RiskStatus = Literal["open", "mitigating", "mitigated", "accepted",
                     "transferred", "closed"]


class RiskCreate(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    description: str
    asset: str
    threat: str
    vulnerability: str
    likelihood: int = Field(ge=1, le=5)
    impact: int = Field(ge=1, le=5)
    owner_user_id: str | None = None
    treatment: Literal["accept", "mitigate", "transfer", "avoid"] = "mitigate"
    target_date: str | None = None
    linked_control_ids: list[str] = Field(default_factory=list)


class RiskPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    tenant_id: str
    type: Literal["risk"] = "risk"
    status: RiskStatus
    title: str
    description: str
    asset: str
    threat: str
    vulnerability: str
    likelihood: int
    impact: int
    inherent_score: int                # likelihood * impact
    residual_score: int | None = None
    treatment: str
    target_date: str | None
    owner_user_id: str | None
    linked_control_ids: list[str]
    linked_incident_ids: list[str] = []
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# 4. Policies
# ---------------------------------------------------------------------------

PolicyStatus = Literal["draft", "approved", "retired"]


class PolicyCreate(BaseModel):
    name: str
    version: str = "1.0"
    summary: str
    effective_date: str
    review_date: str
    owner_user_id: str | None = None
    linked_control_ids: list[str] = []
    linked_risk_ids: list[str] = []
    body_artifact_path: str | None = None


class PolicyPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    tenant_id: str
    type: Literal["policy"] = "policy"
    status: PolicyStatus
    name: str
    version: str
    summary: str
    effective_date: str
    review_date: str
    approved_at: str | None = None
    approved_by: str | None = None
    owner_user_id: str | None
    linked_control_ids: list[str]
    linked_risk_ids: list[str]
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# 5. Incidents
# ---------------------------------------------------------------------------

IncidentStatus = Literal["triage", "investigating", "contained", "eradicated",
                         "recovered", "closed"]


class IncidentCreate(BaseModel):
    title: str
    severity: Literal["low", "medium", "high", "critical"]
    incident_type: str = Field(description="e.g. phi_exposure, auth_breach, ransomware, availability")
    summary: str
    detected_at: str
    reported_at: str | None = None
    affected_systems: list[str] = []
    affected_tenant_ids: list[str] = []
    potential_data_categories: list[str] = []
    owner_user_id: str | None = None


class IncidentPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    tenant_id: str
    type: Literal["incident"] = "incident"
    status: IncidentStatus
    severity: str
    incident_type: str
    title: str
    summary: str
    detected_at: str
    reported_at: str | None = None
    affected_systems: list[str]
    affected_tenant_ids: list[str]
    potential_data_categories: list[str]
    owner_user_id: str | None = None
    containment_actions: list[str] = []
    eradication_actions: list[str] = []
    root_cause: str | None = None
    corrective_actions: list[str] = []
    notification_required: bool = False
    notification_sent_at: str | None = None
    closed_at: str | None = None
    linked_risk_ids: list[str] = []
    linked_alert_ids: list[str] = []
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# 6. Vendors
# ---------------------------------------------------------------------------

VendorStatus = Literal["active", "under_review", "terminated"]


class VendorCreate(BaseModel):
    name: str
    service_provided: str
    data_categories: list[str] = []
    environment: Literal["prod", "staging", "support"] = "prod"
    owner_user_id: str | None = None
    baa_required: bool = False
    baa_in_place: bool = False
    security_review_status: Literal["pending", "approved", "rejected"] = "pending"
    review_cadence_days: int = 365
    contract_end_date: str | None = None
    notes: str | None = None


class VendorPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    tenant_id: str
    type: Literal["vendor"] = "vendor"
    status: VendorStatus
    name: str
    service_provided: str
    data_categories: list[str]
    environment: str
    owner_user_id: str | None
    baa_required: bool
    baa_in_place: bool
    security_review_status: str
    review_cadence_days: int
    last_reviewed_at: str | None = None
    next_review_at: str | None = None
    contract_end_date: str | None
    notes: str | None
    linked_control_ids: list[str] = []
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# 7. Data inventory
# ---------------------------------------------------------------------------

class DataClassCreate(BaseModel):
    name: str
    owning_module: str
    is_tenant_owned: bool = True
    is_phi: bool = False
    retention_days: int = 2555
    deletion_method: Literal["soft_delete", "purge", "archive"] = "purge"
    legal_hold_applicable: bool = True
    exportable: bool = True
    storage_locations: list[str] = []
    encryption: str = "AES-256-at-rest"


class DataClassPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    tenant_id: str
    type: Literal["data_class"] = "data_class"
    name: str
    owning_module: str
    is_tenant_owned: bool
    is_phi: bool
    retention_days: int
    deletion_method: str
    legal_hold_applicable: bool
    exportable: bool
    storage_locations: list[str]
    encryption: str
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# 8. Access reviews (scheduled)
# ---------------------------------------------------------------------------

AccessReviewStatus = Literal["scheduled", "in_progress", "complete", "overdue"]


class AccessReviewCreate(BaseModel):
    name: str
    scope: Literal["tenant_admins", "platform_admins", "privileged_engineers",
                   "break_glass_events", "inactive_users", "stale_service_accounts"]
    due_at: str
    reviewer_user_id: str | None = None
    notes: str | None = None


class AccessReviewPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    tenant_id: str
    type: Literal["access_review"] = "access_review"
    status: AccessReviewStatus
    name: str
    scope: str
    due_at: str
    reviewer_user_id: str | None
    notes: str | None
    completed_at: str | None = None
    decision: str | None = None
    subject_count: int | None = None
    revocations: int | None = None
    linked_evidence_ids: list[str] = []
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Status transition action
# ---------------------------------------------------------------------------

class StatusChange(BaseModel):
    new_status: str
    note: str | None = None


class FieldPatch(BaseModel):
    """Partial update for compliance entities — the UI sends a flat dict of
    fields it wants changed; the server validates allowed keys per-type."""
    fields: dict[str, Any] = Field(default_factory=dict)
    note: str | None = None
