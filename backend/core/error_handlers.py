"""
Global error handler.

Ensures sensitive endpoints do not leak:
  - stack traces to the client
  - internal identifiers (`_id`, DB names, module paths)
  - PHI captured in raised exceptions

`HTTPException`s raised intentionally by routers are passed through
untouched (FastAPI already handles these). Everything else is caught by
`handle_uncaught_exception`: a safe `500` payload is returned to the client,
the full traceback is logged server-side under the `security` logger as a
structured event with a correlation id, and the metrics counter is bumped
so dashboards light up.

The client payload carries the correlation id so a support ticket can be
matched to a specific log line without exposing any internal state.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from core import metrics, security_logger

logger = logging.getLogger("security")


def _path_prefix(path: str) -> str:
    # Cap label cardinality for metrics — keep `/api/<service>` grain only.
    parts = [p for p in path.split("/") if p]
    if not parts:
        return "/"
    if parts[0] == "api" and len(parts) > 1:
        return f"/api/{parts[1]}"
    return f"/{parts[0]}"


async def handle_uncaught_exception(request: Request, exc: Exception) -> JSONResponse:
    correlation_id = str(uuid.uuid4())
    prefix = _path_prefix(request.url.path)
    # Full traceback goes to server logs only.
    logger.exception(
        "Unhandled error on %s [cid=%s]: %s",
        request.url.path,
        correlation_id,
        exc,
    )
    security_logger.event(
        "system.unhandled_error",
        outcome="failure",
        component="system",
        correlation_id=correlation_id,
        method=request.method,
        path=request.url.path,
        error_type=type(exc).__name__,
    )
    try:
        metrics.secure_endpoint_errors_total.labels(path_prefix=prefix).inc()
    except Exception:
        pass
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error. Contact support with the correlation id below.",
            "correlation_id": correlation_id,
        },
    )


def install(app: FastAPI) -> None:
    app.add_exception_handler(Exception, handle_uncaught_exception)
