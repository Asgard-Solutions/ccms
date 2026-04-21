"""Backend tests for the self-service 6-digit Security PIN flow.

Endpoints covered:
  * GET    /auth/me/pin/status
  * POST   /auth/me/pin              (create — requires password)
  * PATCH  /auth/me/pin               (change — requires password + current PIN)
  * POST   /auth/me/pin/reset         (forgot-PIN — requires reauth token)
  * DELETE /auth/me/pin               (remove — requires password)
  * POST   /auth/me/pin/verify        (verify with retry lockout)
  * GET    /auth/me                   (includes `pin_configured` bit)

Isolation: each test builds its own throwaway user so retry counters,
lockouts, and history don't leak between tests.
"""
from __future__ import annotations

import os
import uuid

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE_URL}/api" if BASE_URL else "http://localhost:8001/api"

ADMIN = {"email": "admin@ccms.app", "password": "Admin@ComplianceClinic1"}


def _login(email, password, *, reauth=False):
    s = requests.Session()
    r = s.post(f"{API}/auth/login",
               json={"email": email, "password": password}, timeout=10)
    assert r.status_code == 200, r.text
    if reauth:
        r = s.post(f"{API}/auth/reauth", json={"password": password}, timeout=10)
        assert r.status_code == 200, r.text
        tok = r.cookies.get("reauth_token") or r.json().get("reauth_token")
        if tok:
            s.headers["x-reauth-token"] = tok
    return s


def _new_user():
    """Create a throwaway staff user; returns (email, password)."""
    admin = _login(ADMIN["email"], ADMIN["password"])
    unique = uuid.uuid4().hex[:8]
    email = f"pin_{unique}@ccms.app"
    password = f"Pin1@Strong_{unique}!"
    r = admin.post(f"{API}/auth/users", json={
        "email": email, "password": password,
        "name": "Pin Test", "role": "staff",
    }, timeout=15)
    assert r.status_code in (200, 201), r.text
    return email, password


@pytest.fixture
def user():
    email, password = _new_user()
    return {"email": email, "password": password}


# ---------------------------------------------------------------------------
# Status / creation
# ---------------------------------------------------------------------------
class TestStatusAndCreate:
    def test_default_status_not_configured(self, user):
        s = _login(user["email"], user["password"])
        r = s.get(f"{API}/auth/me/pin/status", timeout=10)
        assert r.status_code == 200
        body = r.json()
        assert body["configured"] is False
        assert body["failed_attempts"] == 0
        assert body["created_at"] is None

    def test_auth_me_exposes_pin_configured(self, user):
        s = _login(user["email"], user["password"])
        assert s.get(f"{API}/auth/me").json()["pin_configured"] is False

    def test_create_pin_happy_path(self, user):
        s = _login(user["email"], user["password"])
        r = s.post(f"{API}/auth/me/pin", json={
            "current_password": user["password"], "pin": "135792",
        }, timeout=10)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["configured"] is True
        assert body["created_at"]
        assert body["failed_attempts"] == 0
        # `pin_configured` flows into /auth/me
        assert s.get(f"{API}/auth/me").json()["pin_configured"] is True

    def test_create_rejects_non_digit(self, user):
        s = _login(user["email"], user["password"])
        r = s.post(f"{API}/auth/me/pin", json={
            "current_password": user["password"], "pin": "abc123",
        }, timeout=10)
        assert r.status_code == 422

    def test_create_rejects_wrong_length(self, user):
        s = _login(user["email"], user["password"])
        r = s.post(f"{API}/auth/me/pin", json={
            "current_password": user["password"], "pin": "12345",
        }, timeout=10)
        assert r.status_code == 422

    def test_create_rejects_wrong_password(self, user):
        s = _login(user["email"], user["password"])
        r = s.post(f"{API}/auth/me/pin", json={
            "current_password": "WrongPassword123!", "pin": "999999",
        }, timeout=10)
        assert r.status_code == 401

    def test_create_twice_returns_409(self, user):
        s = _login(user["email"], user["password"])
        s.post(f"{API}/auth/me/pin", json={
            "current_password": user["password"], "pin": "111111",
        }, timeout=10)
        r = s.post(f"{API}/auth/me/pin", json={
            "current_password": user["password"], "pin": "222222",
        }, timeout=10)
        assert r.status_code == 409


# ---------------------------------------------------------------------------
# Change
# ---------------------------------------------------------------------------
class TestChange:
    def _setup(self, user, pin):
        s = _login(user["email"], user["password"])
        s.post(f"{API}/auth/me/pin", json={
            "current_password": user["password"], "pin": pin,
        }, timeout=10)
        return s

    def test_change_happy_path(self, user):
        s = self._setup(user, "111111")
        r = s.patch(f"{API}/auth/me/pin", json={
            "current_password": user["password"],
            "current_pin": "111111",
            "new_pin": "222222",
        }, timeout=10)
        assert r.status_code == 200, r.text
        # Old PIN now fails verification; new one succeeds.
        assert s.post(f"{API}/auth/me/pin/verify",
                      json={"pin": "111111"}).status_code == 401
        assert s.post(f"{API}/auth/me/pin/verify",
                      json={"pin": "222222"}).status_code == 200

    def test_change_wrong_password(self, user):
        s = self._setup(user, "111111")
        r = s.patch(f"{API}/auth/me/pin", json={
            "current_password": "wrong",
            "current_pin": "111111",
            "new_pin": "222222",
        }, timeout=10)
        assert r.status_code == 401

    def test_change_wrong_current_pin(self, user):
        s = self._setup(user, "111111")
        r = s.patch(f"{API}/auth/me/pin", json={
            "current_password": user["password"],
            "current_pin": "000000",
            "new_pin": "222222",
        }, timeout=10)
        assert r.status_code == 401

    def test_change_same_as_current_rejected(self, user):
        s = self._setup(user, "111111")
        r = s.patch(f"{API}/auth/me/pin", json={
            "current_password": user["password"],
            "current_pin": "111111",
            "new_pin": "111111",
        }, timeout=10)
        assert r.status_code == 400

    def test_change_when_none_configured_returns_404(self, user):
        s = _login(user["email"], user["password"])
        r = s.patch(f"{API}/auth/me/pin", json={
            "current_password": user["password"],
            "current_pin": "111111",
            "new_pin": "222222",
        }, timeout=10)
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Reset (forgot-PIN) — requires reauth token
# ---------------------------------------------------------------------------
class TestReset:
    def test_reset_without_reauth_rejected(self, user):
        s = _login(user["email"], user["password"])  # no reauth
        s.post(f"{API}/auth/me/pin", json={
            "current_password": user["password"], "pin": "111111",
        }, timeout=10)
        r = s.post(f"{API}/auth/me/pin/reset", json={"new_pin": "333333"}, timeout=10)
        assert r.status_code == 401
        assert "re-auth" in (r.json().get("detail") or "").lower()

    def test_reset_with_reauth_succeeds(self, user):
        s = _login(user["email"], user["password"], reauth=True)
        s.post(f"{API}/auth/me/pin", json={
            "current_password": user["password"], "pin": "111111",
        }, timeout=10)
        r = s.post(f"{API}/auth/me/pin/reset", json={"new_pin": "333333"}, timeout=10)
        assert r.status_code == 200, r.text
        # New PIN verifies; old one doesn't.
        assert s.post(f"{API}/auth/me/pin/verify", json={"pin": "111111"}).status_code == 401
        assert s.post(f"{API}/auth/me/pin/verify", json={"pin": "333333"}).status_code == 200

    def test_reset_when_not_configured_creates_new_pin(self, user):
        s = _login(user["email"], user["password"], reauth=True)
        r = s.post(f"{API}/auth/me/pin/reset", json={"new_pin": "555555"}, timeout=10)
        assert r.status_code == 200
        assert r.json()["configured"] is True


# ---------------------------------------------------------------------------
# Verify + lockout
# ---------------------------------------------------------------------------
class TestVerifyAndLockout:
    def test_verify_happy_path(self, user):
        s = _login(user["email"], user["password"])
        s.post(f"{API}/auth/me/pin", json={
            "current_password": user["password"], "pin": "424242",
        }, timeout=10)
        r = s.post(f"{API}/auth/me/pin/verify", json={"pin": "424242"}, timeout=10)
        assert r.status_code == 200
        assert r.json()["verified"] is True

    def test_verify_wrong_pin_increments_counter(self, user):
        s = _login(user["email"], user["password"])
        s.post(f"{API}/auth/me/pin", json={
            "current_password": user["password"], "pin": "424242",
        }, timeout=10)
        for _ in range(3):
            r = s.post(f"{API}/auth/me/pin/verify", json={"pin": "000000"}, timeout=10)
            assert r.status_code == 401
        status = s.get(f"{API}/auth/me/pin/status").json()
        assert status["failed_attempts"] == 3

    def test_five_wrong_locks_pin(self, user):
        s = _login(user["email"], user["password"])
        s.post(f"{API}/auth/me/pin", json={
            "current_password": user["password"], "pin": "424242",
        }, timeout=10)
        for i in range(4):
            r = s.post(f"{API}/auth/me/pin/verify", json={"pin": "000000"}, timeout=10)
            assert r.status_code == 401, (i, r.text)
        # 5th wrong attempt locks
        r = s.post(f"{API}/auth/me/pin/verify", json={"pin": "000000"}, timeout=10)
        assert r.status_code == 423
        # Even the correct PIN is blocked while locked
        r = s.post(f"{API}/auth/me/pin/verify", json={"pin": "424242"}, timeout=10)
        assert r.status_code == 423
        assert s.get(f"{API}/auth/me/pin/status").json()["locked_until"]

    def test_successful_verify_clears_counter(self, user):
        s = _login(user["email"], user["password"])
        s.post(f"{API}/auth/me/pin", json={
            "current_password": user["password"], "pin": "424242",
        }, timeout=10)
        s.post(f"{API}/auth/me/pin/verify", json={"pin": "000000"}, timeout=10)
        s.post(f"{API}/auth/me/pin/verify", json={"pin": "000000"}, timeout=10)
        assert s.get(f"{API}/auth/me/pin/status").json()["failed_attempts"] == 2
        s.post(f"{API}/auth/me/pin/verify", json={"pin": "424242"}, timeout=10)
        assert s.get(f"{API}/auth/me/pin/status").json()["failed_attempts"] == 0

    def test_reset_clears_lockout(self, user):
        s = _login(user["email"], user["password"], reauth=True)
        s.post(f"{API}/auth/me/pin", json={
            "current_password": user["password"], "pin": "424242",
        }, timeout=10)
        # Lock it
        for _ in range(5):
            s.post(f"{API}/auth/me/pin/verify", json={"pin": "000000"}, timeout=10)
        assert s.get(f"{API}/auth/me/pin/status").json()["locked_until"]
        # Reset clears the lock
        r = s.post(f"{API}/auth/me/pin/reset", json={"new_pin": "999999"}, timeout=10)
        assert r.status_code == 200
        assert s.get(f"{API}/auth/me/pin/status").json()["locked_until"] is None


# ---------------------------------------------------------------------------
# Remove
# ---------------------------------------------------------------------------
class TestRemove:
    def test_remove_happy_path(self, user):
        s = _login(user["email"], user["password"])
        s.post(f"{API}/auth/me/pin", json={
            "current_password": user["password"], "pin": "424242",
        }, timeout=10)
        r = s.delete(f"{API}/auth/me/pin",
                     json={"password": user["password"]}, timeout=10)
        assert r.status_code == 200
        assert r.json()["configured"] is False
        assert s.get(f"{API}/auth/me").json()["pin_configured"] is False

    def test_remove_wrong_password(self, user):
        s = _login(user["email"], user["password"])
        s.post(f"{API}/auth/me/pin", json={
            "current_password": user["password"], "pin": "424242",
        }, timeout=10)
        r = s.delete(f"{API}/auth/me/pin",
                     json={"password": "wrong"}, timeout=10)
        assert r.status_code == 401

    def test_remove_when_not_configured_returns_404(self, user):
        s = _login(user["email"], user["password"])
        r = s.delete(f"{API}/auth/me/pin",
                     json={"password": user["password"]}, timeout=10)
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Leak checks — PIN should never appear in any response body.
# ---------------------------------------------------------------------------
def test_pin_never_echoed_in_responses(user):
    s = _login(user["email"], user["password"], reauth=True)
    pin = "654321"
    pin2 = "246813"

    bodies = [
        s.post(f"{API}/auth/me/pin", json={
            "current_password": user["password"], "pin": pin,
        }).text,
        s.get(f"{API}/auth/me/pin/status").text,
        s.get(f"{API}/auth/me").text,
        s.patch(f"{API}/auth/me/pin", json={
            "current_password": user["password"],
            "current_pin": pin, "new_pin": pin2,
        }).text,
        s.post(f"{API}/auth/me/pin/verify", json={"pin": pin2}).text,
        s.post(f"{API}/auth/me/pin/reset", json={"new_pin": "333333"}).text,
        s.delete(f"{API}/auth/me/pin",
                 json={"password": user["password"]}).text,
    ]
    joined = " ".join(bodies)
    for secret in (pin, pin2, "333333"):
        assert secret not in joined, f"PIN leaked: {secret}"
    # Also assert the bcrypt hash prefix is never surfaced
    assert "pin_hash" not in joined
    assert "$2b$" not in joined


# ---------------------------------------------------------------------------
# Auth required
# ---------------------------------------------------------------------------
def test_pin_endpoints_require_auth():
    endpoints = [
        ("GET", "/auth/me/pin/status", None),
        ("POST", "/auth/me/pin", {"current_password": "x", "pin": "123456"}),
        ("PATCH", "/auth/me/pin", {
            "current_password": "x", "current_pin": "123456", "new_pin": "654321",
        }),
        ("POST", "/auth/me/pin/reset", {"new_pin": "123456"}),
        ("DELETE", "/auth/me/pin", {"password": "x"}),
        ("POST", "/auth/me/pin/verify", {"pin": "123456"}),
    ]
    for method, path, body in endpoints:
        r = requests.request(method, f"{API}{path}", json=body, timeout=10)
        assert r.status_code == 401, f"{method} {path} → {r.status_code}"
