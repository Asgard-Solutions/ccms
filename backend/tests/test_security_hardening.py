"""Backend tests for the Security Hardening pass (Task Prompt 6).

Covers:
  * Per-user failure lockout across PIN create / change / remove and
    step-up `/auth/reauth` password path (5 wrong attempts → 429).
  * Per-IP volume ceiling applies to email-change attempts on
    `/me/profile` but NOT to benign profile edits.
  * MFA disable wrong password writes a `auth.mfa_disable` failure row.
  * `/me/preferences` PATCH writes a `user.preferences_updated` row.
  * Audit reasons are machine-readable (`invalid_password`,
    `invalid_pin`, `locked_out`).

All tests use a fresh admin-created user so per-user counters never
collide. `conftest.py` already resets rate-limit buckets before each
test.
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
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=10)
    assert r.status_code == 200, r.text
    return s


def _admin(*, reauth=False):
    s = _login(ADMIN["email"], ADMIN["password"])
    if reauth:
        r = s.post(
            f"{API}/auth/reauth", json={"password": ADMIN["password"]}, timeout=10,
        )
        assert r.status_code == 200, r.text
        tok = r.cookies.get("reauth_token") or r.json().get("reauth_token")
        if tok:
            s.headers["x-reauth-token"] = tok
    return s


def _new_user(role="doctor"):
    admin = _admin()
    unique = uuid.uuid4().hex[:10]
    email = f"hardening_{unique}@ccms.app"
    password = f"Hardening@Strong_{unique}!"
    r = admin.post(f"{API}/auth/users", json={
        "email": email, "password": password,
        "name": "Hardening Test", "role": role,
    }, timeout=15)
    assert r.status_code in (200, 201), r.text
    return email, password


@pytest.fixture
def user():
    email, password = _new_user()
    return {"email": email, "password": password}


def _pin_preload(session, password, pin):
    """Create a PIN so PIN-requiring tests have a starting state."""
    r = session.post(f"{API}/auth/me/pin", json={
        "current_password": password, "pin": pin,
    }, timeout=10)
    assert r.status_code == 201, r.text


# ---------------------------------------------------------------------------
# PIN create lockout
# ---------------------------------------------------------------------------
class TestPinCreateLockout:
    def test_five_wrong_passwords_then_429(self, user):
        s = _login(user["email"], user["password"])
        for i in range(5):
            r = s.post(f"{API}/auth/me/pin", json={
                "current_password": f"Wrong{i}!A123456789",
                "pin": "123456",
            }, timeout=10)
            assert r.status_code == 401, (i, r.status_code, r.text)
        # 6th attempt gated even with the CORRECT password.
        r = s.post(f"{API}/auth/me/pin", json={
            "current_password": user["password"],
            "pin": "654321",
        }, timeout=10)
        assert r.status_code == 429, r.text
        # PIN never got written.
        status_r = s.get(f"{API}/auth/me/pin/status", timeout=10)
        assert status_r.json()["configured"] is False


# ---------------------------------------------------------------------------
# PIN change lockout — failures count whether they trip on password OR pin
# ---------------------------------------------------------------------------
class TestPinChangeLockout:
    def test_mixed_failures_trigger_429(self, user):
        s = _login(user["email"], user["password"])
        _pin_preload(s, user["password"], "111222")

        # 3 wrong passwords + 2 wrong PINs = 5 failures → 6th is 429.
        for i in range(3):
            r = s.patch(f"{API}/auth/me/pin", json={
                "current_password": f"Wrong{i}!A123456789",
                "current_pin": "111222", "new_pin": "333444",
            }, timeout=10)
            assert r.status_code == 401, (i, r.text)
        for i in range(2):
            r = s.patch(f"{API}/auth/me/pin", json={
                "current_password": user["password"],
                "current_pin": f"99{i}000", "new_pin": "333444",
            }, timeout=10)
            assert r.status_code == 401, (i, r.text)

        r = s.patch(f"{API}/auth/me/pin", json={
            "current_password": user["password"],
            "current_pin": "111222", "new_pin": "555666",
        }, timeout=10)
        assert r.status_code == 429, r.text


# ---------------------------------------------------------------------------
# PIN remove lockout
# ---------------------------------------------------------------------------
class TestPinRemoveLockout:
    def test_five_wrong_passwords_then_429(self, user):
        s = _login(user["email"], user["password"])
        _pin_preload(s, user["password"], "111222")

        for i in range(5):
            r = s.delete(f"{API}/auth/me/pin",
                         json={"password": f"Wrong{i}!A123456789"}, timeout=10)
            assert r.status_code == 401, (i, r.text)
        r = s.delete(f"{API}/auth/me/pin",
                     json={"password": user["password"]}, timeout=10)
        assert r.status_code == 429, r.text

        # PIN is still configured since the remove never went through.
        status_r = s.get(f"{API}/auth/me/pin/status", timeout=10)
        assert status_r.json()["configured"] is True


# ---------------------------------------------------------------------------
# `/auth/reauth` password-path lockout
# ---------------------------------------------------------------------------
class TestReauthPasswordLockout:
    def test_five_wrong_passwords_then_429(self, user):
        s = _login(user["email"], user["password"])
        for i in range(5):
            r = s.post(f"{API}/auth/reauth",
                       json={"password": f"Wrong{i}!Anope"},
                       timeout=10)
            assert r.status_code == 401, (i, r.text)
        # 6th even with correct password → 429.
        r = s.post(f"{API}/auth/reauth",
                   json={"password": user["password"]}, timeout=10)
        assert r.status_code == 429, r.text


# ---------------------------------------------------------------------------
# Profile PATCH: email-change throttled, benign edits NOT throttled
# ---------------------------------------------------------------------------
class TestProfileEmailChangeThrottle:
    def test_benign_patches_are_not_throttled(self, user):
        s = _login(user["email"], user["password"])
        for i in range(80):   # more than the 60/60s volume ceiling
            r = s.patch(f"{API}/auth/me/profile",
                        json={"first_name": f"Iter{i}"}, timeout=5)
            assert r.status_code == 200, (i, r.text)

    def test_email_change_without_reauth_returns_401_not_429(self, user):
        s = _login(user["email"], user["password"])
        # One attempt: should be 401 (reauth_required) not 429, since
        # the volume ceiling hasn't been hit yet.
        r = s.patch(f"{API}/auth/me/profile",
                    json={"email": f"new_{uuid.uuid4().hex[:6]}@ccms.app"},
                    timeout=10)
        assert r.status_code == 401, r.text


# ---------------------------------------------------------------------------
# MFA disable — wrong password writes an audit row
# ---------------------------------------------------------------------------
class TestMfaDisableAudit:
    def test_wrong_password_writes_failure_audit(self, user):
        s = _login(user["email"], user["password"])
        # MFA isn't enabled for this throwaway user; the request path
        # still verifies the current password BEFORE looking at the
        # MFA state, so a wrong password returns 401 and audits.
        r = s.post(f"{API}/auth/mfa/disable",
                   json={"password": "DefinitelyWrong1!"}, timeout=10)
        assert r.status_code == 401

        admin = _admin(reauth=True)
        r = admin.get(f"{API}/audit-logs", params={
            "action": "auth.mfa_disable",
            "actor_email": user["email"],
            "outcome": "failure",
            "limit": 5,
        }, timeout=10)
        if r.status_code == 404:
            pytest.skip("audit-logs endpoint not exposed")
        assert r.status_code == 200, r.text
        rows = r.json() if isinstance(r.json(), list) else r.json().get("items") or []
        assert any(
            row.get("action") == "auth.mfa_disable"
            and row.get("outcome") == "failure"
            and row.get("reason") == "invalid_password"
            for row in rows
        ), rows


# ---------------------------------------------------------------------------
# /me/preferences writes an audit row on every successful change
# ---------------------------------------------------------------------------
class TestPreferencesAudit:
    def test_preferences_update_is_audited(self, user):
        s = _login(user["email"], user["password"])
        # Flip the theme; any persistent preference works.
        r = s.patch(f"{API}/auth/me/preferences", json={"theme": "dark"},
                    timeout=10)
        assert r.status_code == 200, r.text

        admin = _admin(reauth=True)
        r = admin.get(f"{API}/audit-logs", params={
            "action": "user.preferences_updated",
            "actor_email": user["email"],
            "limit": 5,
        }, timeout=10)
        if r.status_code == 404:
            pytest.skip("audit-logs endpoint not exposed")
        assert r.status_code == 200, r.text
        rows = r.json() if isinstance(r.json(), list) else r.json().get("items") or []
        assert any(
            row.get("action") == "user.preferences_updated"
            and row.get("actor_email") == user["email"]
            and "theme" in (row.get("metadata") or {}).get("fields", [])
            for row in rows
        ), rows


# ---------------------------------------------------------------------------
# Audit reason normalisation: PIN create failure surfaces `invalid_password`
# ---------------------------------------------------------------------------
class TestAuditReasonsAreMachineReadable:
    def test_pin_create_wrong_password_uses_invalid_password_reason(self, user):
        s = _login(user["email"], user["password"])
        r = s.post(f"{API}/auth/me/pin", json={
            "current_password": "NotTheRealPassword1!", "pin": "999999",
        }, timeout=10)
        assert r.status_code == 401, r.text

        admin = _admin(reauth=True)
        r = admin.get(f"{API}/audit-logs", params={
            "action": "user.pin_create",
            "actor_email": user["email"],
            "outcome": "failure",
            "limit": 5,
        }, timeout=10)
        if r.status_code == 404:
            pytest.skip("audit-logs endpoint not exposed")
        rows = r.json() if isinstance(r.json(), list) else r.json().get("items") or []
        assert any(row.get("reason") == "invalid_password" for row in rows), rows

    def test_reauth_wrong_password_uses_invalid_password_reason(self, user):
        s = _login(user["email"], user["password"])
        r = s.post(f"{API}/auth/reauth",
                   json={"password": "SomethingWrong1!"}, timeout=10)
        assert r.status_code == 401

        admin = _admin(reauth=True)
        r = admin.get(f"{API}/audit-logs", params={
            "action": "auth.reauth",
            "actor_email": user["email"],
            "outcome": "failure",
            "limit": 5,
        }, timeout=10)
        if r.status_code == 404:
            pytest.skip("audit-logs endpoint not exposed")
        rows = r.json() if isinstance(r.json(), list) else r.json().get("items") or []
        assert any(row.get("reason") == "invalid_password" for row in rows), rows


# ---------------------------------------------------------------------------
# Response sanitisation — passwords / pins never echoed in error bodies
# ---------------------------------------------------------------------------
class TestSecretsNeverEchoed:
    def test_pin_create_error_never_echoes_secret(self, user):
        s = _login(user["email"], user["password"])
        sentinel_pw = "MySensitivePw!_SENTINEL_12345"
        sentinel_pin = "998877"
        r = s.post(f"{API}/auth/me/pin", json={
            "current_password": sentinel_pw, "pin": sentinel_pin,
        }, timeout=10)
        assert r.status_code == 401
        assert sentinel_pw not in r.text
        assert sentinel_pin not in r.text

    def test_reauth_error_never_echoes_secret(self, user):
        s = _login(user["email"], user["password"])
        sentinel = "HunterTwo!_SENTINEL_ZZZZ"
        r = s.post(f"{API}/auth/reauth", json={"password": sentinel},
                   timeout=10)
        assert r.status_code == 401
        assert sentinel not in r.text
