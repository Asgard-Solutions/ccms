"""Tenant-scoped export service — CSV generator with signed download URLs.

Flow
----
1. Client POSTs `/api/exports` with `{type, filters}`.
2. We persist a row in `exports` collection with status=pending + random
   `download_token_hash` (never stored raw).
3. A tenant-scoped background job generates the file at
   `/app/data/exports/<tenant_id>/<export_id>.csv` and flips status to
   `ready`.
4. Client polls `/api/exports/{id}`; when ready, it receives a signed
   download URL valid for 15 minutes.
5. `/api/exports/{id}/download?token=...` verifies the signed token
   (reuses `JWT_SECRET`), re-checks permissions, streams the file, and
   audits the download.
6. `cleanup_expired_exports()` runs on startup + admin endpoint to purge
   old files.

Access controls
---------------
- Generation requires `export.create`
- Status lookup / download requires the export to belong to the caller's
  tenant (cross-tenant returns 404)
- Download links are tenant-scoped, time-bound, and audit every fetch
- Files live under a tenant-prefixed path; never a shared directory
- No raw PHI in filenames or URLs; filename = `{export_id}.csv`
"""
from __future__ import annotations

import csv
import io
import logging
import os
import secrets
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Awaitable

import jwt
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from core.audit import audit_failure, audit_success, log_audit
from core.crypto import decrypt_fields, decrypt_text, encrypt_text
from core.tenancy import TenantContext, get_tenant_context, tenant_db
from core.tenant_jobs import enqueue, tenant_job
from services.authz.policy import require_permission

import hashlib as _hashlib

logger = logging.getLogger("ccms.exports")

EXPORT_DIR = Path(os.environ.get("EXPORT_DIR", "/app/data/exports"))
EXPORT_TTL_HOURS = int(os.environ.get("EXPORT_TTL_HOURS", "24"))
DOWNLOAD_TOKEN_TTL_MINUTES = 15

router = APIRouter(prefix="/exports", tags=["exports"])


# ---------------------------------------------------------------------------
# Permission-aware CSV builders
# ---------------------------------------------------------------------------

async def _export_patients(ctx: TenantContext, filters: dict, *, include_phi: bool) -> tuple[list[str], list[list[Any]]]:
    """Basic patient roster. PHI fields are included only when `include_phi`
    is True — decided at request-time by the route, not in the worker."""
    columns = ["id", "location_id", "first_name", "last_name", "status", "created_at"]
    if include_phi:
        columns += ["date_of_birth", "email", "phone"]

    q: dict = {"tenant_id": ctx.tenant_id, "status": {"$ne": "deleted"}}
    if filters.get("location_ids"):
        q["location_id"] = {"$in": filters["location_ids"]}

    db = tenant_db(ctx.tenant_id)
    rows: list[list[Any]] = []
    async for p in db.patients.find(q, {"_id": 0}).limit(int(filters.get("limit", 5000))):
        if include_phi:
            p = decrypt_fields(p, ["date_of_birth"])
        row = [
            p.get("id"), p.get("location_id"), p.get("first_name"),
            p.get("last_name"), p.get("status"), p.get("created_at"),
        ]
        if include_phi:
            row += [p.get("date_of_birth"), p.get("email"), p.get("phone")]
        rows.append(row)
    return columns, rows


async def _export_appointments(ctx: TenantContext, filters: dict, *, include_phi: bool = False) -> tuple[list[str], list[list[Any]]]:
    columns = ["id", "location_id", "patient_id", "provider_id",
               "start_time", "end_time", "status", "reason"]
    q: dict = {"tenant_id": ctx.tenant_id}
    if filters.get("location_ids"):
        q["location_id"] = {"$in": filters["location_ids"]}
    if filters.get("from"):
        q["start_time"] = {"$gte": filters["from"]}
    db = tenant_db(ctx.tenant_id)
    rows: list[list[Any]] = []
    async for a in db.appointments.find(q, {"_id": 0}).limit(int(filters.get("limit", 10000))):
        rows.append([
            a.get("id"), a.get("location_id"), a.get("patient_id"),
            a.get("provider_id"), a.get("start_time"), a.get("end_time"),
            a.get("status"), a.get("reason"),
        ])
    return columns, rows


EXPORT_BUILDERS: dict[str, Callable[[TenantContext, dict], Awaitable[tuple[list[str], list[list[Any]]]]]] = {
    "patients": _export_patients,
    "appointments": _export_appointments,
}


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def _tenant_export_dir(tenant_id: str) -> Path:
    d = EXPORT_DIR / tenant_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _export_path(tenant_id: str, export_id: str) -> Path:
    return _tenant_export_dir(tenant_id) / f"{export_id}.csv"


def _sign_download_token(export_id: str, tenant_id: str, user_id: str) -> str:
    payload = {
        "exp": datetime.now(timezone.utc) + timedelta(minutes=DOWNLOAD_TOKEN_TTL_MINUTES),
        "sub": user_id,
        "tid": tenant_id,
        "eid": export_id,
        "typ": "export_dl",
    }
    return jwt.encode(payload, os.environ["JWT_SECRET"], algorithm="HS256")


def _verify_download_token(token: str) -> dict:
    return jwt.decode(token, os.environ["JWT_SECRET"], algorithms=["HS256"])


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@router.post("", status_code=202)
async def create_export(
    body: dict = Body(...),
    request: Request = None,  # type: ignore[assignment]
    user: dict = Depends(require_permission("reporting", "export", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    export_type = body.get("type")
    if export_type not in EXPORT_BUILDERS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"unknown export type; valid: {list(EXPORT_BUILDERS)}")
    filters = body.get("filters") or {}
    # Validate requested locations.
    if filters.get("location_ids") and not (ctx.is_platform_admin or ctx.tenant_scope_all):
        allowed = set(ctx.allowed_location_ids)
        for loc in filters["location_ids"]:
            if loc not in allowed:
                await audit_failure(
                    action="export.denied", request=request,
                    actor_email=user.get("email"),
                    reason="location_not_assigned",
                    metadata={"type": export_type, "location_id": loc,
                              "tenant_id": ctx.tenant_id},
                )
                raise HTTPException(status.HTTP_403_FORBIDDEN,
                                    f"location {loc} not assigned to you")

    export_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    db = tenant_db(ctx.tenant_id)
    # Stash the requester's PHI privilege at request time. The job runs in a
    # background context (no user role), so we need to carry forward whatever
    # PHI inclusion the real actor is entitled to.
    include_phi = ctx.is_platform_admin or user.get("role") in ("admin", "super_admin")
    await db.exports.insert_one({
        "id": export_id,
        "tenant_id": ctx.tenant_id,
        "type": export_type,
        "filters": filters,
        "actor_user_id": user["id"],
        "actor_role": user.get("role"),
        "include_phi": include_phi,
        "status": "pending",
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=EXPORT_TTL_HOURS)).isoformat(),
        "path": str(_export_path(ctx.tenant_id, export_id)),
        "size_bytes": None,
        "rows": None,
    })

    await audit_success(
        user, "export.requested", request,
        entity_type="export", entity_id=export_id,
        metadata={"type": export_type, "filters": filters},
    )

    await enqueue(
        "export.generate",
        tenant_id=ctx.tenant_id,
        payload={"export_id": export_id},
        actor_user_id=user["id"],
    )
    return {"id": export_id, "status": "pending"}


@router.get("/{export_id}")
async def get_export(
    export_id: str,
    request: Request,
    user: dict = Depends(require_permission("reporting", "read", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = tenant_db(ctx.tenant_id)
    row = await db.exports.find_one(
        {"id": export_id, "tenant_id": ctx.tenant_id},
        {"_id": 0},
    )
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Export not found")

    # Issue a signed download token if ready.
    token = None
    if row["status"] == "ready":
        token = _sign_download_token(export_id, ctx.tenant_id, user["id"])

    # One-time password surfacing: if this export used zip+password or
    # native-PDF password protection, reveal the password exactly once
    # (on the first successful poll by the requester). On reveal we
    # decrypt the at-rest ciphertext, hand it back, then wipe the
    # ciphertext entirely so it cannot be replayed from the DB.
    one_time_password: str | None = None
    if (
        row.get("password_protected")
        and row["status"] == "ready"
        and not row.get("password_revealed")
        and row.get("actor_user_id") == user["id"]
        and row.get("password_enc") is not None
    ):
        one_time_password = decrypt_text(row["password_enc"])
        await db.exports.update_one(
            {"id": export_id},
            {"$set": {"password_revealed": True,
                      "password_revealed_at": datetime.now(timezone.utc).isoformat()},
             "$unset": {"password_enc": ""}},
        )
        await log_audit(
            action="export.password_revealed",
            actor_id=user["id"],
            actor_email=user.get("email"),
            actor_role=user.get("role"),
            tenant_id=ctx.tenant_id,
            entity_type="export", entity_id=export_id,
            metadata={"type": row.get("type"),
                      "report_name": row.get("report_name"),
                      "protection_kind": row.get("protection_kind")},
        )

    return {
        "id": row["id"],
        "status": row["status"],
        "type": row["type"],
        "report_name": row.get("report_name"),
        "format": row.get("format"),
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "size_bytes": row.get("size_bytes"),
        "rows": row.get("rows"),
        "error": row.get("error"),
        "download_token": token,
        "password_protected": bool(row.get("password_protected")),
        "password_revealed": bool(row.get("password_revealed")),
        "protection_kind": row.get("protection_kind"),
        "one_time_password": one_time_password,
        "filename": row.get("filename"),
    }


@router.get("/{export_id}/download")
async def download_export(
    export_id: str,
    request: Request,
    token: str = Query(...),
    ctx: TenantContext = Depends(get_tenant_context),
):
    try:
        payload = _verify_download_token(token)
    except jwt.PyJWTError:
        await audit_failure(
            action="export.download_denied", request=request,
            reason="invalid_or_expired_token",
            metadata={"export_id": export_id, "tenant_id": ctx.tenant_id},
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")

    if payload.get("typ") != "export_dl" or payload.get("eid") != export_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token mismatch")

    # Tenant claim in token MUST match caller's tenant context — this is
    # the last-line defence against a stolen token being replayed by a
    # user in a different tenant.
    if payload.get("tid") != ctx.tenant_id and not ctx.is_platform_admin:
        await audit_failure(
            action="export.download_denied", request=request,
            actor_email=ctx.user.get("email"),
            reason="cross_tenant_token",
            metadata={"export_id": export_id,
                      "token_tid": payload.get("tid"),
                      "caller_tid": ctx.tenant_id},
        )
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cross-tenant token")

    db = tenant_db(ctx.tenant_id)
    row = await db.exports.find_one(
        {"id": export_id, "tenant_id": ctx.tenant_id, "status": "ready"},
        {"_id": 0},
    )
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Export not available")

    path = Path(row["path"])
    if not path.exists():
        raise HTTPException(status.HTTP_410_GONE, "Export file has been purged")

    await audit_success(
        ctx.user, "export.downloaded", request,
        entity_type="export", entity_id=export_id,
        metadata={"type": row["type"], "size_bytes": row.get("size_bytes"),
                  "report_name": row.get("report_name"),
                  "format": row.get("format")},
    )

    def _stream():
        with path.open("rb") as fh:
            while True:
                chunk = fh.read(65536)
                if not chunk:
                    break
                yield chunk

    filename = row.get("filename") or f"{row['type']}-{export_id}.csv"
    media_type = row.get("mime") or "text/csv"
    return StreamingResponse(
        _stream(),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Job handler + cleanup
# ---------------------------------------------------------------------------

@tenant_job("export.generate")
async def _generate(ctx: TenantContext, payload: dict, meta: dict) -> None:
    export_id = payload["export_id"]
    db = tenant_db(ctx.tenant_id)
    row = await db.exports.find_one(
        {"id": export_id, "tenant_id": ctx.tenant_id}, {"_id": 0},
    )
    if not row:
        raise RuntimeError("export row disappeared")
    builder = EXPORT_BUILDERS.get(row["type"])
    if not builder:
        await db.exports.update_one(
            {"id": export_id},
            {"$set": {"status": "failed", "error": f"unknown type {row['type']}"}},
        )
        return
    columns, rows = await builder(ctx, row.get("filters") or {},
                                  include_phi=bool(row.get("include_phi")))
    path = Path(row["path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    for r in rows:
        writer.writerow(r)
    path.write_text(buf.getvalue())

    await db.exports.update_one(
        {"id": export_id},
        {"$set": {
            "status": "ready",
            "rows": len(rows),
            "size_bytes": path.stat().st_size,
            "ready_at": datetime.now(timezone.utc).isoformat(),
        }},
    )
    await log_audit(
        action="export.generated",
        actor_id=meta.get("actor_user_id"),
        tenant_id=ctx.tenant_id,
        entity_type="export",
        entity_id=export_id,
        metadata={"type": row["type"], "rows": len(rows),
                  "size_bytes": path.stat().st_size},
    )


async def cleanup_expired_exports() -> int:
    """Called on startup and exposed as admin endpoint.
    Deletes files + marks rows `expired` for exports whose expires_at has passed.
    Returns the number of files removed."""
    from core.db import get_db_write
    db = get_db_write()
    now = datetime.now(timezone.utc).isoformat()
    removed = 0
    async for row in db.exports.find(
        {"expires_at": {"$lt": now}, "status": {"$in": ["ready", "pending"]}},
        {"_id": 0, "id": 1, "path": 1, "tenant_id": 1},
    ):
        path = Path(row.get("path") or "")
        if path.exists():
            try:
                path.unlink()
                removed += 1
            except OSError:
                logger.warning("Could not remove export %s", path)
        await db.exports.update_one(
            {"id": row["id"]},
            {"$set": {"status": "expired", "expired_at": now}},
        )
        await log_audit(
            action="export.expired",
            actor_id=None,
            tenant_id=row.get("tenant_id"),
            entity_type="export",
            entity_id=row["id"],
        )
    # Also delete empty tenant directories.
    if EXPORT_DIR.exists():
        for sub in EXPORT_DIR.iterdir():
            if sub.is_dir() and not any(sub.iterdir()):
                try:
                    sub.rmdir()
                except OSError:
                    pass
    return removed


@router.post("/cleanup")
async def cleanup_endpoint(
    user: dict = Depends(require_permission("reporting", "export", audit_allow=False)),
):
    removed = await cleanup_expired_exports()
    return {"removed": removed}


# ---------------------------------------------------------------------------
# Report exports — reuses the same `exports` row schema, different handler
# ---------------------------------------------------------------------------

async def create_report_export(
    ctx: TenantContext,
    user: dict,
    *,
    report_name: str,
    fmt: str,
    filters: dict,
    sort: str | None,
    sort_dir: str,
    columns: list[str] | None,
    reason: str | None = None,
) -> tuple[str, str | None]:
    """Persist a pending report-export row and return (export_id, password).

    The password is generated server-side when the report is flagged
    `contains_phi=True` and is stored in plaintext ONLY until the user's
    first successful polling response — `get_export` returns it once and
    then wipes the plaintext copy and flips `password_revealed=True`.

    The hash of the password is always kept for audit integrity.
    """
    from services.reports import get_definition
    from services.reports.export_writer import generate_password

    definition = get_definition(report_name)
    if not definition:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown report {report_name}")

    export_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    password_plain = generate_password() if definition.contains_phi else None
    password_hash = (
        _hashlib.sha256(password_plain.encode()).hexdigest() if password_plain else None
    )
    # Encrypt the plaintext at rest so the DB never holds it in the clear.
    # It is decrypted only twice: once by the worker to drive encryption,
    # once on the requester's first status poll to reveal it — then wiped.
    password_encrypted = encrypt_text(password_plain) if password_plain else None

    db = tenant_db(ctx.tenant_id)
    await db.exports.insert_one({
        "id": export_id,
        "tenant_id": ctx.tenant_id,
        "type": "report",
        "report_name": report_name,
        "format": fmt,
        "filters": filters,
        "sort": sort, "sort_dir": sort_dir,
        "columns": columns,
        "actor_user_id": user["id"],
        "actor_role": user.get("role"),
        "actor_email": user.get("email"),
        "reason": reason,
        "status": "pending",
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=EXPORT_TTL_HOURS)).isoformat(),
        "path": None,
        "size_bytes": None,
        "rows": None,
        "password_protected": bool(password_plain),
        "password_hash": password_hash,
        "password_enc": password_encrypted,  # AES-GCM via core.crypto
        "password_revealed": False,
        "protection_kind": None,  # set by worker: pdf_native | aes_zip | none
        "filename": None,
        "mime": None,
    })
    return export_id, password_plain


@tenant_job("export.generate_report")
async def _generate_report(ctx: TenantContext, payload: dict, meta: dict) -> None:
    """Worker for the report export pipeline.

    Flow:
      1. Load the pending export row.
      2. Resolve the report definition + columns.
      3. Execute the runner with `page_size=50_000` (hard cap).
      4. Hand rows + columns off to `export_writer.build_export`.
      5. Flip status→ready with size + rows + path + filename + mime.
    """
    from services.reports import get_definition, resolve_columns, resolve_sort
    from services.reports.definitions import QueryContext
    from services.reports.export_writer import build_export

    export_id = payload["export_id"]
    db = tenant_db(ctx.tenant_id)
    row = await db.exports.find_one(
        {"id": export_id, "tenant_id": ctx.tenant_id}, {"_id": 0},
    )
    if not row:
        raise RuntimeError("export row disappeared")

    definition = get_definition(row["report_name"])
    if not definition:
        await db.exports.update_one(
            {"id": export_id},
            {"$set": {"status": "failed",
                      "error": f"Unknown report {row['report_name']}"}},
        )
        return

    qc = QueryContext(
        tenant=ctx,
        filters=row.get("filters") or {},
        sort=resolve_sort(definition, row.get("sort")),
        sort_dir=row.get("sort_dir") or definition.default_sort_dir,
        page=1, page_size=50_000,  # hard ceiling for export
        selected_columns=row.get("columns"),
    )
    result = await definition.runner(qc)
    cols = resolve_columns(definition, row.get("columns"))

    # Decrypt the one-time password only for the encryption step.
    password_plain = decrypt_text(row.get("password_enc")) if row.get("password_enc") else None

    artifact = build_export(
        dest_dir=_tenant_export_dir(ctx.tenant_id),
        export_id=export_id,
        title=definition.title,
        columns=cols,
        rows=result.rows,
        fmt=row["format"],
        password=password_plain,  # None when not PHI
    )
    # Drop the decrypted password reference immediately.
    password_plain = None  # noqa: F841

    await db.exports.update_one(
        {"id": export_id},
        {"$set": {
            "status": "ready",
            "rows": len(result.rows),
            "size_bytes": artifact.size_bytes,
            "path": str(artifact.path),
            "mime": artifact.mime,
            "filename": artifact.filename,
            "protection_kind": artifact.protection_kind,
            "ready_at": datetime.now(timezone.utc).isoformat(),
        }},
    )
    await log_audit(
        action="report.export_generated",
        actor_id=meta.get("actor_user_id"),
        tenant_id=ctx.tenant_id,
        entity_type="report_export",
        entity_id=export_id,
        metadata={
            "report": row["report_name"],
            "format": row["format"],
            "password_protected": bool(row.get("password_protected")),
            "protection_kind": artifact.protection_kind,
            "rows": len(result.rows),
            "size_bytes": artifact.size_bytes,
        },
    )
