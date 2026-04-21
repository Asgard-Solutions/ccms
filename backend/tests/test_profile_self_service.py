"""Backend tests for self-service profile updates (Account Settings).

Verifies:
  * GET /auth/me returns the new profile fields (nullable by default).
  * PATCH /auth/me/profile persists first_name/last_name/display_name/
    mobile_phone/work_phone/job_title/credentials_suffix/
    preferred_signature_name/time_zone.
  * Legacy `name` column stays in sync (display_name > first+last).
  * Empty-string clears a field back to null.
  * Email change without reauth is rejected 401; with reauth invalidates
    existing tokens (session epoch bump).
  * Email collision returns 409.
  * Unauthenticated PATCH returns 401.
"""
from __future__ import annotations

import os
import uuid

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE_URL}/api" if BASE_URL else "http://localhost:8001/api"

ADMIN = {"email": "admin@ccms.app", "password": "Admin@ComplianceClinic1"}
DOCTOR = {"email": "doctor@ccms.app", "password": "Doctor@ComplianceClinic1"}


def _login(credentials, *, reauth=False):
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json=credentials, timeout=10)
    r.raise_for_status()
    if reauth:
        r = s.post(
            f"{API}/auth/reauth",
            json={"password": credentials["password"]},
            timeout=10,
        )
        r.raise_for_status()
        tok = r.cookies.get("reauth_token") or r.json().get("reauth_token")
        if tok:
            s.headers["x-reauth-token"] = tok
    return s


@pytest.fixture
def admin_session():
    return _login(ADMIN)


@pytest.fixture
def doctor_session():
    return _login(DOCTOR)


def _me(session):
    r = session.get(f"{API}/auth/me", timeout=10)
    r.raise_for_status()
    return r.json()


class TestProfileSelfService:
    def test_me_includes_profile_fields(self, admin_session):
        me = _me(admin_session)
        for key in (
            "first_name", "last_name", "display_name",
            "mobile_phone", "work_phone", "job_title",
            "credentials_suffix", "preferred_signature_name", "time_zone",
        ):
            assert key in me, f"missing {key} on /auth/me"

    def test_patch_profile_persists_and_syncs_name(self, admin_session):
        r = admin_session.patch(f"{API}/auth/me/profile", json={
            "first_name": "Ada",
            "last_name": "Lovelace",
            "display_name": "Dr. Ada Lovelace, DC",
            "mobile_phone": "+1-555-0101",
            "work_phone": "+1-555-0102",
            "job_title": "Chiropractor",
            "credentials_suffix": "DC, DACBR",
            "preferred_signature_name": "A. Lovelace, DC",
            "time_zone": "America/Chicago",
        }, timeout=10)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["first_name"] == "Ada"
        assert body["last_name"] == "Lovelace"
        assert body["display_name"] == "Dr. Ada Lovelace, DC"
        # Legacy `name` tracks display_name when present
        assert body["name"] == "Dr. Ada Lovelace, DC"
        assert body["mobile_phone"] == "+1-555-0101"
        assert body["work_phone"] == "+1-555-0102"
        assert body["job_title"] == "Chiropractor"
        assert body["credentials_suffix"] == "DC, DACBR"
        assert body["preferred_signature_name"] == "A. Lovelace, DC"
        assert body["time_zone"] == "America/Chicago"
        # Survives reload
        assert _me(admin_session)["job_title"] == "Chiropractor"

    def test_name_falls_back_to_first_last_when_display_cleared(self, admin_session):
        admin_session.patch(f"{API}/auth/me/profile", json={
            "first_name": "Ada", "last_name": "Lovelace",
            "display_name": "Dr. Ada Lovelace, DC",
        }, timeout=10)
        # Clear display_name with empty string
        r = admin_session.patch(f"{API}/auth/me/profile", json={
            "display_name": "",
        }, timeout=10)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["display_name"] is None
        assert body["name"] == "Ada Lovelace"

    def test_empty_clears_field(self, admin_session):
        admin_session.patch(f"{API}/auth/me/profile", json={
            "job_title": "Admin of Things",
        }, timeout=10)
        r = admin_session.patch(f"{API}/auth/me/profile", json={
            "job_title": "",
        }, timeout=10)
        assert r.status_code == 200
        assert r.json()["job_title"] is None

    def test_patch_empty_payload_rejected(self, admin_session):
        r = admin_session.patch(f"{API}/auth/me/profile", json={}, timeout=10)
        assert r.status_code == 400

    def test_unauthenticated_patch_rejected(self):
        r = requests.patch(
            f"{API}/auth/me/profile",
            json={"first_name": "Hacker"},
            timeout=10,
        )
        assert r.status_code == 401

    def test_email_change_without_reauth_rejected(self, doctor_session):
        new_email = f"doctor+{uuid.uuid4().hex[:8]}@ccms.app"
        r = doctor_session.patch(f"{API}/auth/me/profile", json={
            "email": new_email,
        }, timeout=10)
        assert r.status_code == 401
        assert "re-auth" in (r.json().get("detail") or "").lower()

    def test_email_change_with_reauth_succeeds_and_rotates_session(self):
        # Create a brand-new user to safely rotate their email.
        unique = uuid.uuid4().hex[:10]
        new_user_email = f"profile_test_{unique}@ccms.app"
        new_user_password = f"Profile@Test{unique}!"

        # Admin can create any role; use admin flow to provision.
        admin = _login(ADMIN)
        r = admin.post(f"{API}/auth/users", json={
            "email": new_user_email,
            "password": new_user_password,
            "name": "Profile Test",
            "role": "staff",
        }, timeout=10)
        # Some tenants disable /auth/users — fall back to register endpoint
        if r.status_code not in (200, 201):
            r = requests.post(f"{API}/auth/register", json={
                "email": new_user_email,
                "password": new_user_password,
                "name": "Profile Test",
            }, timeout=10)
            assert r.status_code in (200, 201), r.text

        session = _login(
            {"email": new_user_email, "password": new_user_password},
            reauth=True,
        )
        rotated_email = f"profile_rotated_{unique}@ccms.app"
        r = session.patch(f"{API}/auth/me/profile", json={
            "email": rotated_email,
        }, timeout=10)
        assert r.status_code == 200, r.text
        assert r.json()["email"] == rotated_email

        # Session epoch bump → existing token invalidated. /auth/me must 401.
        r = session.get(f"{API}/auth/me", timeout=10)
        assert r.status_code == 401

        # Re-login with the new email succeeds.
        fresh = _login(
            {"email": rotated_email, "password": new_user_password},
        )
        assert _me(fresh)["email"] == rotated_email

    def test_email_collision_returns_409(self, doctor_session):
        # Try to change doctor's email to admin's email (with reauth).
        s = _login(DOCTOR, reauth=True)
        r = s.patch(f"{API}/auth/me/profile", json={
            "email": ADMIN["email"],
        }, timeout=10)
        assert r.status_code == 409
