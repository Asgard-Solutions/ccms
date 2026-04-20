"""Compliance-ops seed — representative sample rows for both demo tenants."""
from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("ccms.compliance_ops.seed")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _add_days(n: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=n)).isoformat()


def _history(action: str) -> list:
    return [{"at": _now(), "actor_id": "seed", "actor_email": "seed",
             "action": action, "note": None}]


CONTROLS: list[dict] = [
    {
        "name": "Unique user IDs + MFA for admin roles",
        "family": "access_control",
        "description": "Every admin/doctor/platform user has a unique identity and MFA is required before PHI access.",
        "status": "implemented",
        "framework_mappings": {
            "HIPAA": ["164.312(a)(2)(i)", "164.312(d)"],
            "SOC2": ["CC6.1", "CC6.2"],
            "ISO27001": ["A.9.2", "A.9.4"],
        },
        "review_cadence_days": 90,
        "evidence_sources": ["users collection", "audit_logs action=auth.mfa_*"],
    },
    {
        "name": "Audit log completeness for PHI access",
        "family": "audit",
        "description": "Every PHI access (read, export, emergency) is audited with actor, tenant, IP, user-agent.",
        "status": "implemented",
        "framework_mappings": {
            "HIPAA": ["164.312(b)"],
            "SOC2": ["CC4.1", "CC7.2"],
            "ISO27001": ["A.12.4"],
        },
        "review_cadence_days": 30,
        "evidence_sources": ["audit_logs phi_accessed=true"],
    },
    {
        "name": "Row-level tenant isolation",
        "family": "access_control",
        "description": "All tenant data carries tenant_id; scoped_filter + TenantScopedRepository enforce isolation at the query layer.",
        "status": "implemented",
        "framework_mappings": {
            "SOC2": ["CC6.1", "CC6.6"],
            "ISO27001": ["A.9.4", "A.13.2"],
            "HIPAA": ["164.312(a)(1)"],
        },
        "review_cadence_days": 90,
        "evidence_sources": ["core/tenant_scope.py", "audit_logs action=security.cross_tenant_attempt"],
    },
    {
        "name": "Encryption at rest + in transit",
        "family": "cryptography",
        "description": "AES-256 at rest via DATA_ENCRYPTION_KEY; TLS 1.3 at the edge; Mongo/Redis/S3 use TLS transport.",
        "status": "implemented",
        "framework_mappings": {
            "HIPAA": ["164.312(a)(2)(iv)", "164.312(e)(1)"],
            "SOC2": ["CC6.7"],
            "ISO27001": ["A.10.1", "A.13.1"],
        },
        "review_cadence_days": 180,
        "evidence_sources": ["infra tls config", "secrets DATA_ENCRYPTION_KEY"],
    },
    {
        "name": "Automated backups + restore tests",
        "family": "backup",
        "description": "Daily full + 5-min point-in-time DB backups; cross-region S3 replication; quarterly restore drill.",
        "status": "planned",
        "framework_mappings": {
            "HIPAA": ["164.308(a)(7)"],
            "SOC2": ["A1.2", "A1.3"],
            "ISO27001": ["A.12.3", "A.17"],
        },
        "review_cadence_days": 90,
        "evidence_sources": ["DR runbook §7", "future restore-test evidence"],
    },
    {
        "name": "Incident response plan + tabletop drills",
        "family": "incident_response",
        "description": "Documented IR plan; tabletop drills twice a year; 72-hour tenant notification SLA for confirmed breaches.",
        "status": "in_progress",
        "framework_mappings": {
            "HIPAA": ["164.308(a)(6)"],
            "SOC2": ["CC7.3", "CC7.4"],
            "ISO27001": ["A.16.1"],
        },
        "review_cadence_days": 180,
        "evidence_sources": ["incidents collection", "IR runbook"],
    },
    {
        "name": "Privacy request handling (CCPA/CPRA)",
        "family": "privacy",
        "description": "Access/delete/correct request intake, verification, and 45-day fulfillment with tenant-safe execution.",
        "status": "implemented",
        "framework_mappings": {
            "CCPA": ["1798.100", "1798.105", "1798.106", "1798.110"],
            "SOC2": ["P3.1"],
            "ISO27001": ["A.18.1"],
        },
        "review_cadence_days": 180,
        "evidence_sources": ["privacy_requests collection"],
    },
]


RISKS: list[dict] = [
    {
        "title": "Cross-tenant data leakage via cache key collision",
        "description": "Risk that a cache key without tenant_id namespace returns another tenant's data.",
        "asset": "Redis cache",
        "threat": "developer misuse",
        "vulnerability": "ad-hoc cache keys",
        "likelihood": 2, "impact": 5,
        "treatment": "mitigate",
        "status": "mitigated",
        "target_date": _add_days(30),
    },
    {
        "title": "Backup/restore process not validated under load",
        "description": "Unknown RTO under full-dataset restore — tabletop says 30 min but not tested.",
        "asset": "MongoDB",
        "threat": "regional outage",
        "vulnerability": "unrehearsed restore",
        "likelihood": 3, "impact": 4,
        "treatment": "mitigate",
        "status": "open",
        "target_date": _add_days(60),
    },
    {
        "title": "Stale privileged accounts from ex-employees",
        "description": "No automated detection of dormant privileged users (> 90 days inactive).",
        "asset": "Identity & Access",
        "threat": "insider / credential theft",
        "vulnerability": "manual deprovisioning",
        "likelihood": 3, "impact": 4,
        "treatment": "mitigate",
        "status": "open",
        "target_date": _add_days(45),
    },
]


POLICIES: list[dict] = [
    {
        "name": "Information Security Policy",
        "version": "1.0", "status": "approved",
        "summary": "Principles: least privilege, defense-in-depth, continuous monitoring.",
        "effective_date": _add_days(-120), "review_date": _add_days(60),
    },
    {
        "name": "HIPAA Privacy & Breach Notification Policy",
        "version": "1.1", "status": "approved",
        "summary": "PHI handling, disclosure accounting, 60-day breach notification.",
        "effective_date": _add_days(-90), "review_date": _add_days(-10),  # OVERDUE
    },
    {
        "name": "Incident Response Plan",
        "version": "1.0", "status": "approved",
        "summary": "On-call rotation, severity ladder, tabletop schedule.",
        "effective_date": _add_days(-60), "review_date": _add_days(180),
    },
]


INCIDENTS: list[dict] = [
    {
        "title": "Replica lag spike on 2026-02-15",
        "severity": "medium", "incident_type": "availability",
        "summary": "Replica lag exceeded 30s for 12 minutes during billing export surge.",
        "detected_at": _add_days(-6), "reported_at": _add_days(-6),
        "status": "closed",
        "affected_systems": ["read replica"], "affected_tenant_ids": [],
        "potential_data_categories": [],
        "containment_actions": ["force_disable_replica(900)"],
        "eradication_actions": ["tuned report aggregation"],
        "root_cause": "unbounded $group on appointments without date filter",
        "corrective_actions": ["date bounds enforced in report runner"],
        "closed_at": _add_days(-5),
    },
]


VENDORS: list[dict] = [
    {
        "name": "AWS", "service_provided": "Infrastructure (EC2, RDS, S3, KMS)",
        "data_categories": ["PHI at rest", "audit logs"],
        "environment": "prod",
        "baa_required": True, "baa_in_place": True,
        "security_review_status": "approved",
        "review_cadence_days": 365,
    },
    {
        "name": "Twilio", "service_provided": "SMS appointment reminders",
        "data_categories": ["phone number", "appointment time"],
        "environment": "prod",
        "baa_required": True, "baa_in_place": False,   # flag on dashboard
        "security_review_status": "pending",
        "review_cadence_days": 365,
    },
]


DATA_CLASSES: list[dict] = [
    {"name": "Patient demographics", "owning_module": "patient",
     "is_tenant_owned": True, "is_phi": True, "retention_days": 2555,
     "deletion_method": "soft_delete", "legal_hold_applicable": True,
     "exportable": True, "storage_locations": ["mongo:patients"]},
    {"name": "Clinical notes", "owning_module": "patient",
     "is_tenant_owned": True, "is_phi": True, "retention_days": 2555,
     "deletion_method": "soft_delete", "legal_hold_applicable": True,
     "exportable": True, "storage_locations": ["mongo:medical_records"]},
    {"name": "Audit logs", "owning_module": "audit",
     "is_tenant_owned": True, "is_phi": False, "retention_days": 2555,
     "deletion_method": "archive", "legal_hold_applicable": True,
     "exportable": True, "storage_locations": ["mongo:audit_logs", "cold storage"]},
    {"name": "Exports", "owning_module": "exports",
     "is_tenant_owned": True, "is_phi": True, "retention_days": 1,
     "deletion_method": "purge", "legal_hold_applicable": False,
     "exportable": False, "storage_locations": ["fs:/app/data/exports"]},
]


ACCESS_REVIEWS: list[dict] = [
    {"name": "Q1 2026 platform admin review", "scope": "platform_admins",
     "status": "scheduled", "due_at": _add_days(-7)},           # OVERDUE
    {"name": "Q1 2026 tenant admin review", "scope": "tenant_admins",
     "status": "scheduled", "due_at": _add_days(20)},
]


async def seed_compliance_ops() -> None:
    """Idempotent — seeds for each existing tenant (default + sunrise)."""
    from core.db import get_db_write
    db = get_db_write()
    tenants = [t async for t in db.tenants.find({}, {"_id": 0, "id": 1, "slug": 1})]
    for t in tenants:
        tid = t["id"]

        # Helper: seed a collection idempotently keyed by (tenant_id, name).
        async def _seed(collection: str, rows: list[dict], key_field: str = "name",
                        defaults: dict | None = None):
            for row in rows:
                existing = await db[collection].find_one(
                    {"tenant_id": tid, key_field: row[key_field]},
                    {"_id": 0, "id": 1},
                )
                if existing:
                    continue
                doc = {
                    "id": str(uuid.uuid4()),
                    "tenant_id": tid,
                    "history": _history("seeded"),
                    "created_at": _now(), "updated_at": _now(),
                    **(defaults or {}),
                    **row,
                }
                await db[collection].insert_one(doc)

        await _seed("compliance_controls", CONTROLS,
                    defaults={"status": "planned", "evidence_sources": [],
                              "linked_risk_ids": [], "linked_policy_ids": [],
                              "owner_user_id": None, "last_reviewed_at": None,
                              "next_review_at": _add_days(90)})
        # Risks add inherent_score
        for r in RISKS:
            r["inherent_score"] = r["likelihood"] * r["impact"]
            r["residual_score"] = r["inherent_score"]
            r.setdefault("linked_control_ids", [])
            r.setdefault("linked_incident_ids", [])
            r.setdefault("owner_user_id", None)
        await _seed("compliance_risks", RISKS, key_field="title")
        await _seed("compliance_policies", POLICIES,
                    defaults={"owner_user_id": None, "linked_control_ids": [],
                              "linked_risk_ids": [], "approved_at": _now(),
                              "approved_by": "seed"})
        await _seed("compliance_incidents", INCIDENTS, key_field="title")
        await _seed("compliance_vendors", VENDORS,
                    defaults={"status": "active", "next_review_at": _add_days(365),
                              "last_reviewed_at": None, "contract_end_date": None,
                              "notes": None, "owner_user_id": None,
                              "linked_control_ids": []})
        await _seed("compliance_data_classes", DATA_CLASSES,
                    defaults={"encryption": "AES-256-at-rest"})
        await _seed("compliance_access_reviews", ACCESS_REVIEWS,
                    defaults={"reviewer_user_id": None, "notes": None,
                              "completed_at": None, "decision": None,
                              "subject_count": None, "revocations": None,
                              "linked_evidence_ids": []})

        # Seed one evidence item tied to the audit-log control.
        control = await db.compliance_controls.find_one(
            {"tenant_id": tid, "name": "Audit log completeness for PHI access"},
            {"_id": 0, "id": 1},
        )
        if control:
            existing = await db.compliance_evidence.find_one(
                {"tenant_id": tid, "control_id": control["id"]}, {"_id": 0, "id": 1},
            )
            if not existing:
                coverage_start = _add_days(-30)
                coverage_end = _now()
                blob = f"audit_logs|last_30d|seed|{coverage_start}|{coverage_end}".encode()
                await db.compliance_evidence.insert_one({
                    "id": str(uuid.uuid4()),
                    "tenant_id": tid,
                    "control_id": control["id"],
                    "evidence_type": "audit_log",
                    "source_system": "ccms.audit",
                    "source_reference": "audit_logs last 30d",
                    "content_summary": "Monthly audit log snapshot with PHI access rows.",
                    "integrity_sha256": hashlib.sha256(blob).hexdigest(),
                    "generated_at": _now(),
                    "coverage_period_start": coverage_start,
                    "coverage_period_end": coverage_end,
                    "retention_days": 2555,
                    "retention_until": _add_days(2555),
                    "legal_hold": False,
                    "owner_user_id": "seed",
                    "access_restriction": "internal",
                    "history": _history("seeded"),
                    "created_at": _now(), "updated_at": _now(),
                })
    logger.info("compliance_ops.seed complete for %d tenants", len(tenants))
