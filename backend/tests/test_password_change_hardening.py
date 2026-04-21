"""Backend tests for the hardened `/auth/change-password` flow.

Scope:
  * Happy path: correct current + policy-compliant new password succeeds;
    `password_changed_at` updates; response echoes `other_sessions_revoked`.
  * Session revocation: a SECOND session for the same user 401s after a
    password change on the FIRST session; first session keeps working.
  * Wrong current password → 401, audit row written (best-effort check).
  * Same password as current → 400 with "cannot reuse" message.
  * Password reuse (history of 5) → 400.
  * Policy rejections → 400 with the specific failure message.
  * Per-user failure rate-limit: 5 consecutive bad-current-password
    attempts trigger 429 on the 6th, even if the 6th has a correct
    current password (pure failure gating).
  * Logging: error responses never echo the user's password in body.
  * Audit log: a `auth.password_changed` row is written on success; a
    `auth.password_change` row (failure) is written for wrong current.

Isolation strategy:
  Each test creates its own throwaway user via the admin seed path so
  rate-limit counters don't collide with shared accounts.
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
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
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


def _new_user(role="staff"):
    """Create a fresh user via the admin `/auth/users` endpoint.

    Returns `(email, password, admin_session_used_for_creation)`. The
    password is a 16-char strong string; tests can change and re-use."""
    admin = _admin()
    unique = uuid.uuid4().hex[:10]
    email = f"pwtest_{unique}@ccms.app"
    password = f"Change@Me_{unique}!"  # meets policy
    r = admin.post(f"{API}/auth/users", json={
        "email": email,
        "password": password,
        "name": "Password Test",
        "role": role,
    }, timeout=15)
    # Fallback to /register if admin create isn't available for this env.
    if r.status_code not in (200, 201):
        r = requests.post(f"{API}/auth/register", json={
            "email": email,
            "password": password,
            "name": "Password Test",
        }, timeout=15)
        assert r.status_code in (200, 201), r.text
    return email, password


@pytest.fixture
def user():
    email, password = _new_user()
    return {"email": email, "password": password}


def _change(session, current, new):
    return session.post(f"{API}/auth/change-password", json={
        "current_password": current,
        "new_password": new,
    }, timeout=15)


# ---------------------------------------------------------------------------
# Happy path + session revocation
# ---------------------------------------------------------------------------
class TestHappyPath:
    def test_change_succeeds_and_reports_revocation(self, user):
        s = _login(user["email"], user["password"])
        new_pw = f"NewStrong@{uuid.uuid4().hex[:10]}!"
        r = _change(s, user["password"], new_pw)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("message") == "Password updated"
        assert body.get("other_sessions_revoked") is True

        # Current session keeps working (cookies were rotated).
        r = s.get(f"{API}/auth/me", timeout=10)
        assert r.status_code == 200
        assert r.json()["password_changed_at"]

        # New password works for fresh login; old password does not.
        assert _login(user["email"], new_pw).get(f"{API}/auth/me", timeout=10).status_code == 200
        r = requests.post(
            f"{API}/auth/login",
            json={"email": user["email"], "password": user["password"]},
            timeout=10,
        )
        assert r.status_code == 401

    def test_other_session_is_invalidated(self, user):
        """A second session (different cookies) is revoked when the
        first session changes the password. The acting session is
        preserved because cookies are re-issued inline."""
        s1 = _login(user["email"], user["password"])
        s2 = _login(user["email"], user["password"])
        # Sanity — both can read /auth/me
        assert s1.get(f"{API}/auth/me").status_code == 200
        assert s2.get(f"{API}/auth/me").status_code == 200

        new_pw = f"NewStrong@{uuid.uuid4().hex[:10]}!"
        r = _change(s1, user["password"], new_pw)
        assert r.status_code == 200, r.text

        # s1 still works, s2 is dead.
        assert s1.get(f"{API}/auth/me").status_code == 200
        assert s2.get(f"{API}/auth/me").status_code == 401


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------
class TestFailurePaths:
    def test_wrong_current_password_returns_401(self, user):
        s = _login(user["email"], user["password"])
        r = _change(s, "NotThePassword123!", f"Strong@{uuid.uuid4().hex[:10]}!")
        assert r.status_code == 401
        detail = r.json().get("detail") or ""
        assert "incorrect" in detail.lower()
        # Response body never echoes sensitive fields.
        assert "current_password" not in r.text.lower()
        assert "new_password" not in r.text.lower()

    def test_same_as_current_is_rejected(self, user):
        s = _login(user["email"], user["password"])
        r = _change(s, user["password"], user["password"])
        assert r.status_code == 400
        assert "reuse" in (r.json().get("detail") or "").lower()

    def test_policy_rejections(self, user):
        s = _login(user["email"], user["password"])
        # All passwords are ≥12 chars so the Pydantic min_length guard
        # (422) doesn't fire; each fails one specific policy rule
        # raised by `validate_strength`.
        cases = [
            ("alllowercase12!", "uppercase"),
            ("ALLUPPERCASE12!", "lowercase"),
            ("NoDigitsHereAAa!", "digit"),
            ("NoSymbolsHere123", "symbol"),
        ]
        for new_pw, expected_substr in cases:
            r = _change(s, user["password"], new_pw)
            assert r.status_code == 400, f"{new_pw!r} should be rejected"
            detail = (r.json().get("detail") or "").lower()
            assert expected_substr in detail, f"{new_pw!r}: {detail}"

    def test_min_length_enforced_at_schema_layer(self, user):
        """Pydantic min_length=12 returns 422 before the handler runs."""
        s = _login(user["email"], user["password"])
        r = _change(s, user["password"], "short1!Aa")
        assert r.status_code == 422

    def test_history_reuse_rejected(self, user):
        """Cycle through 2 new passwords, then try to reuse the
        original. Since reuse check covers the last 5, this fails."""
        s = _login(user["email"], user["password"])
        pw0 = user["password"]
        pw1 = f"AAA_Strong@{uuid.uuid4().hex[:6]}!"
        pw2 = f"BBB_Strong@{uuid.uuid4().hex[:6]}!"

        assert _change(s, pw0, pw1).status_code == 200
        # Re-login because password changed; session cookies rotated.
        s = _login(user["email"], pw1)
        assert _change(s, pw1, pw2).status_code == 200

        s = _login(user["email"], pw2)
        r = _change(s, pw2, pw0)   # pw0 is in history → rejected
        assert r.status_code == 400
        assert "reuse" in (r.json().get("detail") or "").lower()


# ---------------------------------------------------------------------------
# Rate limiting: per-user failure counter
# ---------------------------------------------------------------------------
class TestFailureRateLimit:
    def test_five_failures_then_429(self, user):
        """Six consecutive wrong-current-password attempts: first five
        return 401, the sixth returns 429 even if we start supplying the
        correct current password (rate gate acts at entry)."""
        s = _login(user["email"], user["password"])
        for i in range(5):
            r = _change(s, f"Wrong{i}!A123456789", f"Strong@{uuid.uuid4().hex[:6]}!")
            assert r.status_code == 401, (i, r.status_code, r.text)

        # 6th attempt — even with the CORRECT current password, the
        # gate rejects further tries until the window rolls over.
        r = _change(s, user["password"], f"FreshStrong@{uuid.uuid4().hex[:6]}!")
        assert r.status_code == 429
        assert "try again" in (r.json().get("detail") or "").lower()


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------
class TestAuditTrail:
    def test_success_writes_audit_row(self, user):
        s = _login(user["email"], user["password"])
        new_pw = f"AuditStrong@{uuid.uuid4().hex[:8]}!"
        assert _change(s, user["password"], new_pw).status_code == 200

        # Admin can read audit logs. Use actor_email filter to isolate
        # the row our test wrote, sorted newest-first by the endpoint.
        admin = _admin(reauth=True)
        r = admin.get(
            f"{API}/audit-logs",
            params={
                "action": "auth.password_changed",
                "actor_email": user["email"],
                "limit": 10,
            },
            timeout=10,
        )
        if r.status_code == 404:
            pytest.skip("audit-logs endpoint not exposed in this env")
        assert r.status_code == 200, r.text
        rows = r.json()
        assert any(
            row.get("actor_email") == user["email"]
            and row.get("action") == "auth.password_changed"
            and (row.get("outcome") in (None, "success"))
            for row in (rows if isinstance(rows, list) else rows.get("items") or [])
        ), rows

    def test_failure_writes_audit_row(self, user):
        s = _login(user["email"], user["password"])
        _change(s, "WrongCurrent1!", f"Strong@{uuid.uuid4().hex[:6]}!")

        admin = _admin(reauth=True)
        r = admin.get(
            f"{API}/audit-logs",
            params={
                "action": "auth.password_change",
                "actor_email": user["email"],
                "outcome": "failure",
                "limit": 10,
            },
            timeout=10,
        )
        if r.status_code == 404:
            pytest.skip("audit-logs endpoint not exposed in this env")
        assert r.status_code == 200, r.text
        rows = r.json() if isinstance(r.json(), list) else r.json().get("items") or []
        assert any(
            row.get("actor_email") == user["email"]
            and row.get("action") == "auth.password_change"
            and row.get("outcome") == "failure"
            for row in rows
        ), rows


# ---------------------------------------------------------------------------
# Auth requirement
# ---------------------------------------------------------------------------
def test_unauthenticated_returns_401():
    r = requests.post(f"{API}/auth/change-password", json={
        "current_password": "anything",
        "new_password": "AnotherThing123!",
    }, timeout=10)
    assert r.status_code == 401
