"""
Audit log router — admin-only read endpoint.

Future relational schema:
  audit_logs (
    id             UUID PRIMARY KEY,
    action         VARCHAR(80) NOT NULL,
    actor_id       UUID,
    actor_email    VARCHAR(255),
    actor_role     VARCHAR(20),
    entity_type    VARCHAR(40),
    entity_id      VARCHAR(80),
    reason         TEXT,
    metadata       JSONB NOT NULL DEFAULT '{}',
    outcome        VARCHAR(16) NOT NULL,
    phi_accessed   BOOLEAN NOT NULL DEFAULT FALSE,
    ip             VARCHAR(64),
    user_agent     VARCHAR(400),
    created_at     TIMESTAMPTZ NOT NULL
  );
  CREATE INDEX ON audit_logs (created_at DESC);
  CREATE INDEX ON audit_logs (actor_id);
  CREATE INDEX ON audit_logs (entity_type, entity_id);
"""
from fastapi import APIRouter, Depends, Query, Request

from core.audit import audit_success
from core.db import get_db_read
from core.deps import require_role

router = APIRouter(prefix="/audit-logs", tags=["audit"])


@router.get("")
async def list_audit_logs(
    request: Request,
    user: dict = Depends(require_role("admin")),
    actor_id: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    action: str | None = None,
    outcome: str | None = None,
    phi_accessed: bool | None = None,
    limit: int = Query(default=100, ge=1, le=500),
):
    db = get_db_read()
    q: dict = {}
    if actor_id:
        q["actor_id"] = actor_id
    if entity_type:
        q["entity_type"] = entity_type
    if entity_id:
        q["entity_id"] = entity_id
    if action:
        q["action"] = {"$regex": f"^{action}"}
    if outcome:
        q["outcome"] = outcome
    if phi_accessed is not None:
        q["phi_accessed"] = phi_accessed
    cursor = db.audit_logs.find(q, {"_id": 0}).sort("created_at", -1).limit(limit)
    rows = [r async for r in cursor]

    # Audit the fact that someone read the audit log (meta-audit).
    await audit_success(
        user,
        "audit_log.viewed",
        request,
        metadata={"filters": {k: v for k, v in q.items() if v is not None}, "count": len(rows)},
    )
    return rows
