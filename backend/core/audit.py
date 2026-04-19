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

from core.db import get_db

logger = logging.getLogger("audit")


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
