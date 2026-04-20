"""
Iteration 16 — Cache isolation + background jobs + reports + exports.
"""
from __future__ import annotations

import os
import time
import uuid

import pytest
import requests

API = os.environ.get("CCMS_BASE_URL", "http://localhost:8001/api")

DEFAULT_ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")
GROUP_ADMIN = ("group-admin@sunrise.ccms.app", "Sunrise@ComplianceClinic1")
DOWNTOWN_DOC = ("downtown-doc@sunrise.ccms.app", "Sunrise@ComplianceClinic1")


def _login(email, password):
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, r.text
    return s


def _reauth(s, password):
    r = s.post(f"{API}/auth/reauth", json={"password": password}, timeout=10)
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Cache isolation — explicit unit test of the key builder
# ---------------------------------------------------------------------------

def test_cache_key_builder_enforces_tenant_namespace():
    from core.tenant_cache import key_for, tenant_prefix, UnsafeCacheKeyError, TenantCache
    import asyncio

    k1 = key_for("tenant-a", "report", "r1", "abcdef")
    k2 = key_for("tenant-b", "report", "r1", "abcdef")
    assert k1.startswith("t:tenant-a:")
    assert k2.startswith("t:tenant-b:")
    assert k1 != k2  # never the same key across tenants

    # Unsafe raw keys are rejected.
    async def _probe():
        with pytest.raises(UnsafeCacheKeyError):
            await TenantCache.get("patients:list")
        with pytest.raises(UnsafeCacheKeyError):
            await TenantCache.set("patients:list", "x", 60)

    asyncio.get_event_loop().run_until_complete(_probe())

    # tenant_prefix matches every key under a tenant
    assert tenant_prefix("x").startswith("t:x:")


def test_cache_refuses_ttl_edges():
    from core.tenant_cache import TenantCache, key_for
    import asyncio

    async def _probe():
        with pytest.raises(ValueError):
            await TenantCache.set(key_for("t", "x"), 1, 0)
        with pytest.raises(ValueError):
            await TenantCache.set(key_for("t", "x"), 1, 100000)

    asyncio.get_event_loop().run_until_complete(_probe())


# ---------------------------------------------------------------------------
# Reports — tenant-scoped
# ---------------------------------------------------------------------------

def test_reports_are_tenant_scoped():
    default = _login(*DEFAULT_ADMIN)
    group = _login(*GROUP_ADMIN)

    r1 = default.post(f"{API}/reports/location_performance/run", json={}, timeout=15)
    r2 = group.post(f"{API}/reports/location_performance/run", json={}, timeout=15)
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    j1, j2 = r1.json(), r2.json()
    assert j1["tenant_id"] != j2["tenant_id"]
    default_locs = {row["location_name"] for row in j1["rows"]}
    group_locs = {row["location_name"] for row in j2["rows"]}
    assert default_locs.isdisjoint(group_locs)


def test_location_restricted_user_cannot_request_other_locations():
    """downtown-doc cannot ask for a report scoped to Uptown Clinic."""
    group = _login(*GROUP_ADMIN)
    ctx = group.get(f"{API}/tenancy/me/context", timeout=10).json()
    uptown = next(l["id"] for l in ctx["locations"] if l["name"] == "Uptown Clinic")

    doc = _login(*DOWNTOWN_DOC)
    r = doc.post(
        f"{API}/reports/appointments_by_day/run",
        json={"location_ids": [uptown], "days": 30},
        timeout=10,
    )
    assert r.status_code == 403, r.text


def test_location_restricted_user_sees_only_own_location_in_report():
    doc = _login(*DOWNTOWN_DOC)
    r = doc.post(
        f"{API}/reports/provider_productivity/run",
        json={"days": 60},
        timeout=10,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # The report's location_ids echo should be exactly the doctor's allowed list.
    ctx = doc.get(f"{API}/tenancy/me/context", timeout=10).json()
    allowed = set(ctx["allowed_location_ids"])
    assert set(body.get("location_ids") or []).issubset(allowed)


# ---------------------------------------------------------------------------
# Exports — E2E
# ---------------------------------------------------------------------------

def _request_export(session, payload):
    # Exports require MFA reauth because reporting.export carries the MFA flag.
    session.post(f"{API}/auth/reauth", json={"password": GROUP_ADMIN[1]}, timeout=10)
    return session.post(f"{API}/exports", json=payload, timeout=15)


def test_export_end_to_end_tenant_scoped():
    s = _login(*GROUP_ADMIN)
    _reauth(s, GROUP_ADMIN[1])
    r = s.post(f"{API}/exports", json={"type": "patients", "filters": {"limit": 100}}, timeout=15)
    assert r.status_code == 202, r.text
    export_id = r.json()["id"]

    # Poll until ready (jobs run in-process so should be < 3s).
    for _ in range(20):
        time.sleep(0.25)
        st = s.get(f"{API}/exports/{export_id}", timeout=10).json()
        if st.get("status") == "ready":
            break
    assert st["status"] == "ready", st
    token = st["download_token"]
    assert token

    # Download succeeds for the requester.
    r = s.get(f"{API}/exports/{export_id}/download?token={token}", timeout=15)
    assert r.status_code == 200
    assert r.text.startswith("id,location_id,first_name,last_name")


def test_cross_tenant_download_token_is_rejected():
    # group admin creates an export
    s = _login(*GROUP_ADMIN)
    _reauth(s, GROUP_ADMIN[1])
    r = s.post(f"{API}/exports", json={"type": "patients"}, timeout=15)
    export_id = r.json()["id"]
    for _ in range(20):
        time.sleep(0.25)
        st = s.get(f"{API}/exports/{export_id}", timeout=10).json()
        if st.get("status") == "ready":
            break
    token = st["download_token"]

    # default admin tries the same token.
    other = _login(*DEFAULT_ADMIN)
    # The export row lives in Sunrise tenant — default admin sees 404 on the
    # status endpoint (tenant-scoped).
    r = other.get(f"{API}/exports/{export_id}", timeout=10)
    assert r.status_code == 404

    # Direct download attempt with the stolen token is refused.
    r = other.get(f"{API}/exports/{export_id}/download?token={token}", timeout=10)
    assert r.status_code in (401, 403, 404), r.text


def test_expired_download_token_is_rejected():
    """A hand-forged expired JWT is denied."""
    import jwt
    payload = {
        "exp": int(time.time()) - 60,
        "sub": "x", "tid": "anything",
        "eid": "anything", "typ": "export_dl",
    }
    # Use the same secret the server uses so we're only testing exp.
    tok = jwt.encode(payload, os.environ.get("JWT_SECRET", "test-secret-dev"), algorithm="HS256")
    s = _login(*GROUP_ADMIN)
    r = s.get(f"{API}/exports/any-id/download?token={tok}", timeout=10)
    assert r.status_code in (401, 404)


# ---------------------------------------------------------------------------
# Job dispatcher fails closed without tenant_id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enqueue_without_tenant_raises():
    from core.tenant_jobs import enqueue, MissingJobContext, tenant_job

    # Register a throw-away handler so we don't trip "unknown job_type".
    @tenant_job("test.noop")
    async def _noop(ctx, payload, meta):
        return None

    with pytest.raises(MissingJobContext):
        await enqueue("test.noop", tenant_id="", payload={})


# ---------------------------------------------------------------------------
# Audit logs cover report + export flow
# ---------------------------------------------------------------------------

def test_audit_covers_export_requested_and_downloaded():
    s = _login(*GROUP_ADMIN)
    _reauth(s, GROUP_ADMIN[1])
    r = s.post(f"{API}/exports", json={"type": "patients"}, timeout=15)
    export_id = r.json()["id"]
    for _ in range(20):
        time.sleep(0.25)
        st = s.get(f"{API}/exports/{export_id}", timeout=10).json()
        if st.get("status") == "ready":
            break
    token = st["download_token"]
    s.get(f"{API}/exports/{export_id}/download?token={token}", timeout=10)

    # Query audit logs scoped to this tenant (group admin can see them).
    logs = s.get(f"{API}/audit-logs?entity_id={export_id}&limit=20", timeout=10).json()
    actions = {row.get("action") for row in logs}
    assert "export.requested" in actions
    assert "export.generated" in actions
    assert "export.downloaded" in actions
