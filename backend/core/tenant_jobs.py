"""
Tenant-scoped background jobs.

Job model
---------
A job is `{job_id, tenant_id, job_type, payload, actor_user_id, location_id?, created_at, status}`.
The tenant_id is MANDATORY at enqueue time; the dispatcher refuses to run a
job without one (fail closed).

Handlers are registered via `@tenant_job("appointment.remind")`. Each
handler receives a `(ctx: TenantContext, payload: dict, meta: dict)` tuple:

    @tenant_job("appointment.remind")
    async def send_reminder(ctx, payload, meta):
        repo = AppointmentRepository()
        appt = await repo.find_one_by_id(payload["appointment_id"], ctx)
        ...

The dispatcher:
  - builds the background context via `TenantContext.for_background`,
  - runs the handler inside a try/except so failures are audited,
  - writes `job.started`, `job.completed`, `job.failed` audit rows.

Production deployment
---------------------
Today the dispatcher runs in-process with `asyncio.create_task`. The
payload schema + handler signature are identical to what a Celery / SQS /
RabbitMQ worker would receive, so moving to a dedicated broker later is
a matter of swapping `enqueue()` to push onto the broker and running the
same `_run()` in the worker. No business-logic change.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from core.audit import log_audit
from core.tenancy import TenantContext

logger = logging.getLogger("ccms.jobs")

JobHandler = Callable[[TenantContext, dict, dict], Awaitable[None]]

_registry: dict[str, JobHandler] = {}


class MissingJobContext(RuntimeError):
    pass


def tenant_job(job_type: str) -> Callable[[JobHandler], JobHandler]:
    def _wrap(fn: JobHandler) -> JobHandler:
        if job_type in _registry:
            logger.warning("Re-registering job handler %s", job_type)
        _registry[job_type] = fn
        return fn
    return _wrap


def _new_job_id() -> str:
    return str(uuid.uuid4())


async def enqueue(
    job_type: str,
    *,
    tenant_id: str,
    payload: dict,
    actor_user_id: str | None = None,
    location_id: str | None = None,
    run_at: datetime | None = None,
) -> str:
    """Enqueue a tenant-scoped job. Returns the job_id.

    Fails closed if tenant_id is missing. Persists a `jobs` row so dead
    jobs are visible + retryable.
    """
    if not tenant_id:
        raise MissingJobContext("enqueue() refused — tenant_id is required on every job")
    if job_type not in _registry:
        raise ValueError(f"unknown job_type {job_type!r}; handlers are: {list(_registry)}")

    from core.tenancy import tenant_db
    job_id = _new_job_id()
    now = datetime.now(timezone.utc).isoformat()
    db = tenant_db(tenant_id)
    await db.jobs.insert_one({
        "id": job_id,
        "tenant_id": tenant_id,
        "job_type": job_type,
        "payload": payload,
        "actor_user_id": actor_user_id,
        "location_id": location_id,
        "status": "queued",
        "attempts": 0,
        "max_attempts": 3,
        "created_at": now,
        "run_at": (run_at or datetime.now(timezone.utc)).isoformat(),
        "last_error": None,
    })

    await log_audit(
        action="job.enqueued",
        actor_id=actor_user_id,
        actor_email=None,
        tenant_id=tenant_id,
        entity_type="job",
        entity_id=job_id,
        metadata={"job_type": job_type, "location_id": location_id},
    )

    # Fire-and-forget execution. A real broker would pick this up.
    asyncio.create_task(_run(job_id, tenant_id, job_type, payload,
                             actor_user_id, location_id))
    return job_id


async def _run(
    job_id: str, tenant_id: str, job_type: str, payload: dict,
    actor_user_id: str | None, location_id: str | None,
) -> None:
    from core.tenancy import tenant_db
    db = tenant_db(tenant_id)
    started_at = datetime.now(timezone.utc).isoformat()
    await db.jobs.update_one(
        {"id": job_id},
        {"$set": {"status": "running", "started_at": started_at},
         "$inc": {"attempts": 1}},
    )
    await log_audit(
        action="job.started",
        actor_id=actor_user_id,
        tenant_id=tenant_id,
        entity_type="job",
        entity_id=job_id,
        metadata={"job_type": job_type},
    )

    handler = _registry.get(job_type)
    if not handler:
        await _fail(db, job_id, tenant_id, f"no_handler:{job_type}")
        return

    ctx = TenantContext.for_background(
        tenant_id=tenant_id,
        actor=f"worker:{job_type}",
    )

    try:
        await handler(ctx, payload or {}, {
            "job_id": job_id, "actor_user_id": actor_user_id,
            "location_id": location_id,
        })
    except Exception as exc:  # noqa: BLE001
        logger.exception("Job %s (%s) failed: %s", job_id, job_type, exc)
        await _fail(db, job_id, tenant_id, str(exc)[:200])
        return

    await db.jobs.update_one(
        {"id": job_id},
        {"$set": {"status": "succeeded",
                  "completed_at": datetime.now(timezone.utc).isoformat()}},
    )
    await log_audit(
        action="job.completed",
        actor_id=actor_user_id,
        tenant_id=tenant_id,
        entity_type="job",
        entity_id=job_id,
        metadata={"job_type": job_type},
    )


async def _fail(db, job_id: str, tenant_id: str, reason: str) -> None:
    await db.jobs.update_one(
        {"id": job_id},
        {"$set": {"status": "failed", "last_error": reason,
                  "failed_at": datetime.now(timezone.utc).isoformat()}},
    )
    await log_audit(
        action="job.failed",
        actor_id=None,
        tenant_id=tenant_id,
        entity_type="job",
        entity_id=job_id,
        outcome="failure",
        reason=reason,
    )


def registered_handlers() -> list[str]:
    return sorted(_registry)
