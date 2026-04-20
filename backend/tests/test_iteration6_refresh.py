"""Iteration 6 targeted re-test: /api/auth/refresh for all 4 roles + epoch-bump 401."""
import os
import pytest
import requests

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")

CREDS = [
    ("admin",   "admin@ccms.app",   "Admin@ComplianceClinic1"),
    ("doctor",  "doctor@ccms.app",  "Doctor@ComplianceClinic1"),
    ("staff",   "staff@ccms.app",   "Staff@ComplianceClinic1"),
    ("patient", "patient@ccms.app", "Patient@ComplianceClinic1"),
]


def _login(email, password):
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": password}, timeout=20)
    assert r.status_code == 200, f"login failed for {email}: {r.status_code} {r.text}"
    body = r.json()
    assert body.get("mfa_required") in (False, None), f"unexpected mfa for {email}"
    # mfa_policy_required field should exist on user payload (regression)
    assert "mfa_policy_required" in body["user"], f"missing mfa_policy_required on {email}"
    return s


@pytest.mark.parametrize("role,email,password", CREDS)
def test_refresh_success_all_roles(role, email, password):
    s = _login(email, password)
    r = s.post(f"{BASE_URL}/api/auth/refresh", timeout=20)
    assert r.status_code == 200, f"[{role}] refresh returned {r.status_code}: {r.text}"
    assert r.json().get("message") == "Refreshed"
    # Access cookie should still be present (fresh)
    assert any(c.name == "access_token" for c in s.cookies), f"[{role}] no access_token cookie"


def test_refresh_rejected_after_epoch_bump_via_disable_enable():
    """Admin disables + re-enables a freshly created user → refresh token is now stale."""
    admin = _login("admin@ccms.app", "Admin@ComplianceClinic1")

    # Create throwaway staff user
    import uuid
    tag = uuid.uuid4().hex[:8]
    email = f"TEST_it6_refresh_{tag}@ccms.app"
    pwd = "Temp@ComplianceClinic1"
    r = admin.post(
        f"{BASE_URL}/api/auth/users",
        json={"email": email, "password": pwd, "role": "staff", "name": "Test Refresh"},
        timeout=20,
    )
    assert r.status_code in (200, 201), f"user create: {r.status_code} {r.text}"
    uid = r.json()["id"]

    # Login as that user → capture session
    user_sess = _login(email, pwd)

    # Admin disables then re-enables → bumps session_epoch
    r = admin.post(f"{BASE_URL}/api/auth/users/{uid}/disable", timeout=20)
    assert r.status_code == 200, r.text
    r = admin.post(f"{BASE_URL}/api/auth/users/{uid}/enable", timeout=20)
    assert r.status_code == 200, r.text

    # User's refresh token carries old epoch → /refresh must now 401
    r = user_sess.post(f"{BASE_URL}/api/auth/refresh", timeout=20)
    assert r.status_code == 401, f"expected 401 after epoch bump, got {r.status_code}: {r.text}"

    # Cleanup: disable the test user so we don't litter (seed users unaffected)
    admin.post(f"{BASE_URL}/api/auth/users/{uid}/disable", timeout=20)


def test_refresh_rejected_after_role_patch():
    """Admin patches target user's role → target's session_epoch bumps → refresh 401."""
    admin = _login("admin@ccms.app", "Admin@ComplianceClinic1")
    import uuid
    tag = uuid.uuid4().hex[:8]
    email = f"TEST_it6_rolepatch_{tag}@ccms.app"
    pwd = "Temp@ComplianceClinic1"
    r = admin.post(
        f"{BASE_URL}/api/auth/users",
        json={"email": email, "password": pwd, "role": "staff", "name": "Role Patch"},
        timeout=20,
    )
    assert r.status_code in (200, 201), r.text
    uid = r.json()["id"]

    user_sess = _login(email, pwd)

    # Role patch bumps session_epoch for target
    r = admin.patch(
        f"{BASE_URL}/api/auth/users/{uid}",
        json={"role": "doctor"},
        timeout=20,
    )
    # Some impls use PUT; accept 200/204 either way; if 405, skip silently
    if r.status_code == 405:
        # try PATCH via /role-change endpoint if exists; else skip
        pytest.skip("PATCH /auth/users/{id} not implemented — covered by iteration_5 suite")
    assert r.status_code in (200, 204), f"role patch: {r.status_code} {r.text}"

    r = user_sess.post(f"{BASE_URL}/api/auth/refresh", timeout=20)
    assert r.status_code == 401, f"expected 401 after role patch, got {r.status_code}: {r.text}"

    admin.post(f"{BASE_URL}/api/auth/users/{uid}/disable", timeout=20)
