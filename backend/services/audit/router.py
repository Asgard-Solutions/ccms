"""
Audit log router — admin-only read + CSV export.

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
import csv
import io
import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from core.audit import audit_success
from core.db import get_db_read
from core.deps import require_role

router = APIRouter(prefix="/audit-logs", tags=["audit"])


def _build_query(
    actor_id: str | None,
    actor_email: str | None,
    entity_type: str | None,
    entity_id: str | None,
    action: str | None,
    outcome: str | None,
    phi_accessed: bool | None,
    date_from: str | None,
    date_to: str | None,
) -> dict:
    q: dict = {}
    if actor_id:
        q["actor_id"] = actor_id
    if actor_email:
        q["actor_email"] = {"$regex": actor_email, "$options": "i"}
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
    if date_from or date_to:
        rng: dict = {}
        if date_from:
            rng["$gte"] = date_from
        if date_to:
            rng["$lte"] = date_to
        q["created_at"] = rng
    return q


@router.get("")
async def list_audit_logs(
    request: Request,
    user: dict = Depends(require_role("admin")),
    actor_id: str | None = None,
    actor_email: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    action: str | None = None,
    outcome: str | None = None,
    phi_accessed: bool | None = None,
    date_from: str | None = Query(default=None, description="ISO-8601 UTC lower bound"),
    date_to: str | None = Query(default=None, description="ISO-8601 UTC upper bound"),
    limit: int = Query(default=100, ge=1, le=500),
):
    db = get_db_read()
    q = _build_query(
        actor_id, actor_email, entity_type, entity_id, action, outcome,
        phi_accessed, date_from, date_to,
    )
    cursor = db.audit_logs.find(q, {"_id": 0}).sort("created_at", -1).limit(limit)
    rows = [r async for r in cursor]

    await audit_success(
        user,
        "audit_log.viewed",
        request,
        metadata={"filters": q, "count": len(rows)},
    )
    return rows


@router.get("/export.csv")
async def export_audit_logs_csv(
    request: Request,
    user: dict = Depends(require_role("admin")),
    actor_id: str | None = None,
    actor_email: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    action: str | None = None,
    outcome: str | None = None,
    phi_accessed: bool | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = Query(default=5000, ge=1, le=50000),
):
    """Streaming CSV export for auditors.

    The export itself is audited. Metadata and user-agent strings are CSV-
    escaped; no raw PHI is ever written (audit rows never contain PHI values).
    """
    db = get_db_read()
    q = _build_query(
        actor_id, actor_email, entity_type, entity_id, action, outcome,
        phi_accessed, date_from, date_to,
    )
    cursor = db.audit_logs.find(q, {"_id": 0}).sort("created_at", -1).limit(limit)

    columns = [
        "created_at", "action", "outcome", "phi_accessed",
        "actor_id", "actor_email", "actor_role",
        "entity_type", "entity_id",
        "reason", "metadata", "ip", "user_agent",
    ]

    async def _iter_csv():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(columns)
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)
        count = 0
        async for r in cursor:
            meta = r.get("metadata") or {}
            writer.writerow(
                [
                    r.get("created_at", ""),
                    r.get("action", ""),
                    r.get("outcome", ""),
                    r.get("phi_accessed", False),
                    r.get("actor_id", ""),
                    r.get("actor_email", ""),
                    r.get("actor_role", ""),
                    r.get("entity_type", ""),
                    r.get("entity_id", ""),
                    r.get("reason", ""),
                    json.dumps(meta, default=str),
                    r.get("ip", ""),
                    r.get("user_agent", ""),
                ]
            )
            count += 1
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)
        # trailer — also log the export itself (fire-and-forget)
        await audit_success(
            user,
            "audit_log.exported",
            request,
            metadata={"filters": q, "rows_exported": count, "format": "csv"},
        )

    filename = "ccms_audit_export.csv"
    return StreamingResponse(
        _iter_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
