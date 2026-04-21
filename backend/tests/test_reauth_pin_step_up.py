"""Backend tests for the refactored `/auth/reauth` step-up endpoint.

The endpoint now accepts either a password OR a 6-digit PIN (exclusive)
plus an optional `reason` audit note. Success mints the same 5-minute
`reauth_token` cookie that existing `require_reauth()` gates consume —
zero behaviour change for downstream consumers.

Coverage:
  * Password path (back-compat): happy, wrong password, missing field.
  * PIN path: happy, wrong PIN (lockout counter bumps), locked user
    gets 423 even with correct PIN, no-PIN configured returns 400.
  * Exclusivity: sending both password and pin → 422.
  * Neither → 422.
  * PIN failures consumed by this endpoint also count against
    `/auth/me/pin/verify` (shared counter).
  * Reason is recorded in audit metadata.
  * Issued `reauth_token` unlocks a downstream reauth-gated endpoint
    (`POST /auth/me/pin/reset`).
"""
from __future__ import annotations

import os
import uuid

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE_URL}/api" if BASE_URL else "http://localhost:8001/api"

ADMIN = {"email": "admin@ccms.app", "password": "Admin@ComplianceClinic1"}


def _login(email, password):
    s = requests.Session()
    r = s.post(f"{API}/auth/login",
               json={"email": email, "password": password}, timeout=10)
    assert r.status_code == 200, r.text
    return s


def _new_user():
    admin = _login(ADMIN["email"], ADMIN["password"])
    unique = uuid.uuid4().hex[:8]
    email = f"reauth_{unique}@ccms.app"
    password = f"Reauth1@Strong_{unique}!"
    r = admin.post(f"{API}/auth/users", json={
        "email": email, "password": password,
        "name": "Reauth Test", "role": "staff",
    }, timeout=15)
    assert r.status_code in (200, 201), r.text
    return email, password


@pytest.fixture
def user():
    email, password = _new_user()
    return {"email": email, "password": password}


def _set_pin(session, password, pin):
    r = session.post(f"{API}/auth/me/pin", json={
        "current_password": password, "pin": pin,
    }, timeout=10)
    assert r.status_code == 201, r.text


# ---------------------------------------------------------------------------
# Password path (legacy)
# ---------------------------------------------------------------------------
class TestPasswordPath:
    def test_happy_path_password(self, user):
        s = _login(user["email"], user["password"])
        r = s.post(f"{API}/auth/reauth",
                   json={"password": user["password"]}, timeout=10)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["factor"] == "password"
        assert body.get("reauth_token")

    def test_wrong_password(self, user):
        s = _login(user["email"], user["password"])
        r = s.post(f"{API}/auth/reauth",
                   json={"password": "NopeNope1!"}, timeout=10)
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# PIN path (new)
# ---------------------------------------------------------------------------
class TestPinPath:
    def test_happy_path_pin(self, user):
        s = _login(user["email"], user["password"])
        _set_pin(s, user["password"], "135792")
        r = s.post(f"{API}/auth/reauth",
                   json={"pin": "135792"}, timeout=10)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["factor"] == "pin"
        assert body.get("reauth_token")

    def test_pin_without_configured_returns_400(self, user):
        s = _login(user["email"], user["password"])
        r = s.post(f"{API}/auth/reauth",
                   json={"pin": "135792"}, timeout=10)
        assert r.status_code == 400
        assert "no pin" in (r.json().get("detail") or "").lower()

    def test_wrong_pin_increments_counter(self, user):
        s = _login(user["email"], user["password"])
        _set_pin(s, user["password"], "111111")
        for _ in range(3):
            r = s.post(f"{API}/auth/reauth",
                       json={"pin": "000000"}, timeout=10)
            assert r.status_code == 401
        assert s.get(f"{API}/auth/me/pin/status").json()["failed_attempts"] == 3

    def test_five_wrong_locks_and_correct_pin_423(self, user):
        s = _login(user["email"], user["password"])
        _set_pin(s, user["password"], "111111")
        for i in range(4):
            r = s.post(f"{API}/auth/reauth",
                       json={"pin": "000000"}, timeout=10)
            assert r.status_code == 401, (i, r.text)
        # 5th wrong → 423
        r = s.post(f"{API}/auth/reauth",
                   json={"pin": "000000"}, timeout=10)
        assert r.status_code == 423
        # Even the correct PIN is locked out
        r = s.post(f"{API}/auth/reauth",
                   json={"pin": "111111"}, timeout=10)
        assert r.status_code == 423
        # But password fallback still works.
        r = s.post(f"{API}/auth/reauth",
                   json={"password": user["password"]}, timeout=10)
        assert r.status_code == 200

    def test_successful_pin_resets_counter(self, user):
        s = _login(user["email"], user["password"])
        _set_pin(s, user["password"], "111111")
        s.post(f"{API}/auth/reauth", json={"pin": "000000"}, timeout=10)
        s.post(f"{API}/auth/reauth", json={"pin": "000000"}, timeout=10)
        assert s.get(f"{API}/auth/me/pin/status").json()["failed_attempts"] == 2
        r = s.post(f"{API}/auth/reauth", json={"pin": "111111"}, timeout=10)
        assert r.status_code == 200
        assert s.get(f"{API}/auth/me/pin/status").json()["failed_attempts"] == 0


# ---------------------------------------------------------------------------
# Shared-counter contract: pin failures in reauth also lock /pin/verify.
# ---------------------------------------------------------------------------
def test_pin_failures_are_shared_with_verify_endpoint(user):
    s = _login(user["email"], user["password"])
    _set_pin(s, user["password"], "111111")
    # 4 wrong via /reauth
    for _ in range(4):
        s.post(f"{API}/auth/reauth", json={"pin": "000000"}, timeout=10)
    # 5th wrong via /verify triggers lockout — shared counter
    r = s.post(f"{API}/auth/me/pin/verify", json={"pin": "000000"}, timeout=10)
    assert r.status_code == 423


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
class TestPayloadValidation:
    def test_both_factors_rejected(self, user):
        s = _login(user["email"], user["password"])
        r = s.post(f"{API}/auth/reauth",
                   json={"password": user["password"], "pin": "111111"},
                   timeout=10)
        assert r.status_code == 422

    def test_neither_factor_rejected(self, user):
        s = _login(user["email"], user["password"])
        r = s.post(f"{API}/auth/reauth", json={}, timeout=10)
        assert r.status_code == 422

    def test_pin_wrong_shape_rejected(self, user):
        s = _login(user["email"], user["password"])
        for bad in ("abc123", "12345", "1234567"):
            r = s.post(f"{API}/auth/reauth", json={"pin": bad}, timeout=10)
            assert r.status_code == 422, bad


# ---------------------------------------------------------------------------
# Reason field reaches audit metadata
# ---------------------------------------------------------------------------
def test_reason_is_recorded_in_audit_metadata(user):
    s = _login(user["email"], user["password"])
    reason_text = f"break-glass: covering for Dr. X on {uuid.uuid4().hex[:6]}"
    r = s.post(f"{API}/auth/reauth", json={
        "password": user["password"], "reason": reason_text,
    }, timeout=10)
    assert r.status_code == 200

    # Admin reads audit logs (reauth required on that endpoint too).
    admin = _login(ADMIN["email"], ADMIN["password"])
    admin.post(f"{API}/auth/reauth", json={"password": ADMIN["password"]},
               timeout=10)
    tok = admin.cookies.get("reauth_token")
    if tok:
        admin.headers["x-reauth-token"] = tok
    r = admin.get(f"{API}/audit-logs", params={
        "action": "auth.reauth",
        "actor_email": user["email"],
        "outcome": "success",
        "limit": 5,
    }, timeout=10)
    if r.status_code == 404:
        pytest.skip("audit-logs endpoint not exposed in this env")
    assert r.status_code == 200, r.text
    rows = r.json() if isinstance(r.json(), list) else r.json().get("items") or []
    assert any(
        reason_text in (row.get("metadata") or {}).get("reason", "")
        for row in rows
    ), rows


# ---------------------------------------------------------------------------
# Downstream: issued token unlocks a reauth-gated endpoint
# ---------------------------------------------------------------------------
def test_pin_reauth_token_unlocks_pin_reset(user):
    """A step-up via PIN should be sufficient to call PIN-reset (which
    uses `require_reauth()`)."""
    s = _login(user["email"], user["password"])
    _set_pin(s, user["password"], "111111")
    r = s.post(f"{API}/auth/reauth", json={"pin": "111111"}, timeout=10)
    assert r.status_code == 200
    tok = s.cookies.get("reauth_token")
    if tok:
        s.headers["x-reauth-token"] = tok
    r = s.post(f"{API}/auth/me/pin/reset",
               json={"new_pin": "999999"}, timeout=10)
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Auth requirement
# ---------------------------------------------------------------------------
def test_unauthenticated_rejected():
    r = requests.post(f"{API}/auth/reauth",
                      json={"password": "anything"}, timeout=10)
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# DELETE /auth/me/pin still requires password (pin-only rejected with 400).
# ---------------------------------------------------------------------------
def test_delete_pin_rejects_pin_only_payload(user):
    s = _login(user["email"], user["password"])
    _set_pin(s, user["password"], "111111")
    r = s.delete(f"{API}/auth/me/pin",
                 json={"pin": "111111"}, timeout=10)
    assert r.status_code == 400, r.text
    assert "password" in (r.json().get("detail") or "").lower()

    # Sanity: password still works.
    r = s.delete(f"{API}/auth/me/pin",
                 json={"password": user["password"]}, timeout=10)
    assert r.status_code == 200, r.text
