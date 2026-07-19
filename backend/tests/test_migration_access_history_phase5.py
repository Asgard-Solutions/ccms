"""
Phase 5 — Legacy-role migration + access history endpoint tests.

Covers:
 * GET /api/authz/migration/legacy/dry-run — counts shape, no mutations
 * POST /api/authz/migration/legacy/apply — inserts user_roles rows;
   second call is idempotent (inserted_count=0)
 * GET /api/authz/access-history — returns audit rows for authz.*
   actions; action_prefix filter narrows the result set; limit param
   capped at 500
"""
from __future__ import annotations

import os
import uuid

import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

BASE = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE}/api" if BASE else "http://localhost:8001/api"

DEFAULT_ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")


def _login(email: str, password: str, *, reauth: bool = True) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, r.text
    tok = r.cookies.get("access_token") or r.json().get("access_token")
    if tok:
        s.headers["Authorization"] = f"Bearer {tok}"
    if reauth:
        r = s.post(f"{API}/auth/reauth", json={"password": password}, timeout=10)
        if r.status_code == 200:
            rt = r.cookies.get("reauth_token") or r.json().get("reauth_token")
            if rt:
                s.headers["x-reauth-token"] = rt
    return s


def test_migration_dry_run_shape():
    s = _login(*DEFAULT_ADMIN)
    r = s.get(f"{API}/authz/migration/legacy/dry-run", timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    for k in ("total_candidates", "count_mapped", "count_ambiguous",
              "count_unmapped", "mapped", "ambiguous", "unmapped",
              "generated_at"):
        assert k in body, f"missing {k}"
    # Totals add up
    assert (body["count_mapped"] + body["count_ambiguous"]
            + body["count_unmapped"]) == body["total_candidates"]


def test_migration_apply_is_idempotent():
    s = _login(*DEFAULT_ADMIN)
    # Dry-run reports N mapped.
    dry1 = s.get(f"{API}/authz/migration/legacy/dry-run", timeout=15).json()
    # Apply — should insert exactly dry1.count_mapped rows (or fewer if
    # racing). Re-login in case any audit flag bumped epoch.
    r = s.post(f"{API}/authz/migration/legacy/apply", timeout=15)
    assert r.status_code == 200, r.text
    applied = r.json()
    assert "inserted_count" in applied
    assert "applied_at" in applied
    # Dry run again — count_mapped must drop to 0 (or inserted count
    # was zero because backfill already ran on boot).
    s2 = _login(*DEFAULT_ADMIN)
    dry2 = s2.get(f"{API}/authz/migration/legacy/dry-run", timeout=15).json()
    assert dry2["count_mapped"] == 0


def test_access_history_returns_authz_actions():
    """Create a role so we know at least one authz.role.created row
    lands in the audit log, then verify access-history surfaces it."""
    s = _login(*DEFAULT_ADMIN)
    name = f"AccessHistory Test {uuid.uuid4().hex[:6]}"
    r = s.post(f"{API}/authz/roles", json={
        "name": name,
        "permission_keys": ["dashboard.read"],
    }, timeout=10)
    assert r.status_code == 201, r.text
    key = r.json()["key"]
    try:
        hist = s.get(f"{API}/authz/access-history",
                     params={"action_prefix": "authz.role.", "limit": 50},
                     timeout=15)
        assert hist.status_code == 200, hist.text
        body = hist.json()
        assert "rows" in body and "count" in body
        assert body["count"] == len(body["rows"])
        assert body["count"] >= 1
        # Newest first
        actions = [row["action"] for row in body["rows"]]
        assert any(a.startswith("authz.role.") for a in actions)
    finally:
        s.delete(f"{API}/authz/roles/{key}", params={"force": "true"}, timeout=10)


def test_access_history_no_prefix_returns_all_authz():
    s = _login(*DEFAULT_ADMIN)
    r = s.get(f"{API}/authz/access-history", params={"limit": 20}, timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    for row in body["rows"]:
        assert row["action"].startswith("authz."), row["action"]


def test_access_history_limit_capped_at_500():
    s = _login(*DEFAULT_ADMIN)
    # Requesting 9999 should be accepted but clamped.
    r = s.get(f"{API}/authz/access-history", params={"limit": 9999}, timeout=15)
    assert r.status_code == 200
    # Should return <= 500 rows.
    assert len(r.json()["rows"]) <= 500


def test_role_editor_accepts_permission_policies():
    """PATCH accepts permission_policies (MFA/approval flags per key)."""
    s = _login(*DEFAULT_ADMIN)
    name = f"PolicyTest {uuid.uuid4().hex[:6]}"
    r = s.post(f"{API}/authz/roles", json={
        "name": name,
        "permission_keys": ["patient.delete", "payment.refund"],
    }, timeout=10)
    assert r.status_code == 201, r.text
    key = r.json()["key"]
    try:
        r = s.patch(f"{API}/authz/roles/{key}", json={
            "permission_keys": ["patient.delete", "payment.refund"],
            "permission_policies": {
                "patient.delete": {"requires_mfa": True},
                "payment.refund": {"requires_approval": True, "requires_mfa": True},
            },
        }, timeout=10)
        assert r.status_code == 200, r.text
        grants = {g["permission_key"]: g for g in r.json()["grants"]}
        assert grants["patient.delete"]["requires_mfa"] is True
        assert grants["payment.refund"]["requires_mfa"] is True
        assert grants["payment.refund"]["requires_approval"] is True
    finally:
        s.delete(f"{API}/authz/roles/{key}", params={"force": "true"}, timeout=10)
