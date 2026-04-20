"""
Audit logger — persists every PHI access / mutation to MongoDB.

Each audit row maps 1:1 to a future PostgreSQL `audit_logs` row and is
immutable after write. Callers should prefer the high-level helpers
(`audit_success`, `audit_failure`, `audit_emergency`) over hand-building a doc.

We never store raw PHI in audit rows — only entity identifiers, action names,
short reason strings, and metadata safe to retain for the 7-year HIPAA window.
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import Request

from core import metrics, security_logger
from core.db import get_db

logger = logging.getLogger("audit")


_PRIVILEGED_ACTIONS = {
    "user.created", "user.disabled", "user.enabled", "user.updated",
    "user.mfa_reset", "user.mfa_policy_updated", "patient.legal_hold_updated",
}


def _component(action: str) -> str:
    if action.startswith("auth"):
        return "auth"
    if action.startswith("patient") or action.startswith("medical_record") or action.startswith("appointment"):
        return "phi"
    if action.startswith("privacy") or action.startswith("consent"):
        return "privacy"
    if action.startswith("audit_log"):
        return "audit"
    if action.startswith("user."):
        return "privileged"
    return "system"


def _emit_metrics(action: str, outcome: str, phi_accessed: bool, metadata: dict, reason: str | None) -> None:
    try:
        if outcome == "failure" and action.startswith("auth"):
            tag = (reason or (metadata or {}).get("reason") or "unspecified")
            metrics.auth_failures_total.labels(reason=str(tag)[:40]).inc()
        if phi_accessed:
            metrics.phi_access_total.labels(action=action).inc()
        if action in _PRIVILEGED_ACTIONS:
            metrics.privileged_actions_total.labels(action=action).inc()
        if action == "patient.exported":
            metrics.exports_total.labels(kind="patient").inc()
        if action == "account.self_exported":
            metrics.exports_total.labels(kind="account").inc()
        if action == "audit_log.exported":
            metrics.exports_total.labels(kind="audit_csv").inc()
        if (metadata or {}).get("emergency_access"):
            metrics.breakglass_total.inc()
        if action.startswith("privacy_request."):
            req_type = (metadata or {}).get("request_type") or "unknown"
            status = (metadata or {}).get("new_status") or action.split(".", 1)[-1]
            metrics.privacy_requests_total.labels(type=str(req_type)[:20], status=str(status)[:20]).inc()
    except Exception:
        # Never let metrics emission fail a request.
        pass


def _client_ip(request: Request | None) -> str:
    if request is None:
        return "internal"
    # Trust leftmost X-Forwarded-For when set (k8s ingress); fallback to peer.
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def log_audit(
    *,
    action: str,
    actor_id: str | None,
    actor_email: str | None = None,
    actor_role: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
    outcome: str = "success",
    request: Request | None = None,
    phi_accessed: bool = False,
) -> None:
    doc = {
        "id": str(uuid.uuid4()),
        "action": action,
        "actor_id": actor_id,
        "actor_email": actor_email,
        "actor_role": actor_role,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "reason": reason,
        "metadata": metadata or {},
        "outcome": outcome,
        "phi_accessed": phi_accessed,
        "ip": _client_ip(request),
        "user_agent": (request.headers.get("user-agent") if request else None),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        await get_db().audit_logs.insert_one(doc)
    except Exception as exc:  # noqa: BLE001
        # Never let audit failure kill a real request, but log loudly.
        logger.exception("Failed to write audit row %s: %s", action, exc)
    # Mirror the audit row to the structured security log + metrics. The DB
    # row is the system of record; this emission exists for real-time SIEM
    # alerting and monitoring.
    security_logger.event(
        action,
        outcome=outcome,
        component=_component(action),
        actor_id=actor_id,
        actor_email=actor_email,
        actor_role=actor_role,
        entity_type=entity_type,
        entity_id=entity_id,
        reason=reason,
        phi_accessed=phi_accessed,
        ip=doc["ip"],
        meta=metadata or {},
    )
    _emit_metrics(action, outcome, phi_accessed, metadata or {}, reason)


async def audit_success(user: dict, action: str, request: Request, **kwargs) -> None:
    await log_audit(
        action=action,
        actor_id=user.get("id"),
        actor_email=user.get("email"),
        actor_role=user.get("role"),
        outcome="success",
        request=request,
        **kwargs,
    )


async def audit_failure(
    *,
    action: str,
    request: Request,
    actor_email: str | None = None,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    await log_audit(
        action=action,
        actor_id=None,
        actor_email=actor_email,
        outcome="failure",
        reason=reason,
        metadata=metadata,
        request=request,
    )


async def audit_emergency(
    user: dict,
    *,
    action: str,
    entity_type: str,
    entity_id: str,
    reason: str,
    request: Request,
    metadata: dict | None = None,
) -> None:
    """Break-glass access. Always flagged with `phi_accessed=True`."""
    await log_audit(
        action=action,
        actor_id=user.get("id"),
        actor_email=user.get("email"),
        actor_role=user.get("role"),
        entity_type=entity_type,
        entity_id=entity_id,
        reason=reason,
        metadata={**(metadata or {}), "emergency_access": True},
        outcome="success",
        request=request,
        phi_accessed=True,
    )
