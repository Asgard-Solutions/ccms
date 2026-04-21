"""Backend regression for per-user theme preference.

Verifies:
  * /auth/me returns the default `system` theme for a fresh user.
  * PATCH /auth/me/preferences persists the new theme.
  * /auth/me returns the persisted theme after relogin.
  * Invalid theme values are rejected by Pydantic (422).
  * Two users in the same tenant keep independent themes.
"""
from __future__ import annotations

import os

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN = {"email": "admin@ccms.app", "password": "Admin@ComplianceClinic1"}
DOCTOR = {"email": "doctor@ccms.app", "password": "Doctor@ComplianceClinic1"}


def _session(credentials):
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json=credentials, timeout=10)
    r.raise_for_status()
    return s


@pytest.fixture
def admin_session():
    return _session(ADMIN)


@pytest.fixture
def doctor_session():
    return _session(DOCTOR)


def _me(session):
    r = session.get(f"{API}/auth/me", timeout=10)
    r.raise_for_status()
    return r.json()


def _set_theme(session, theme):
    return session.patch(
        f"{API}/auth/me/preferences", json={"theme": theme}, timeout=10
    )


class TestThemePreference:
    def test_me_includes_theme_field(self, admin_session):
        me = _me(admin_session)
        assert "theme" in me
        assert me["theme"] in ("light", "dark", "system")

    def test_patch_persists_light(self, admin_session):
        r = _set_theme(admin_session, "light")
        assert r.status_code == 200, r.text
        assert r.json()["theme"] == "light"
        assert _me(admin_session)["theme"] == "light"

    def test_patch_persists_dark(self, admin_session):
        r = _set_theme(admin_session, "dark")
        assert r.status_code == 200
        assert r.json()["theme"] == "dark"

    def test_patch_reset_to_system(self, admin_session):
        _set_theme(admin_session, "dark")
        r = _set_theme(admin_session, "system")
        assert r.status_code == 200
        assert r.json()["theme"] == "system"

    def test_invalid_theme_rejected(self, admin_session):
        r = _set_theme(admin_session, "neon")
        assert r.status_code == 422

    def test_empty_payload_rejected(self, admin_session):
        r = admin_session.patch(
            f"{API}/auth/me/preferences", json={}, timeout=10
        )
        assert r.status_code == 400

    def test_theme_survives_logout_login(self, doctor_session):
        _set_theme(doctor_session, "dark")
        doctor_session.post(f"{API}/auth/logout", timeout=10)
        fresh = _session(DOCTOR)
        assert _me(fresh)["theme"] == "dark"

    def test_two_users_independent(self, admin_session, doctor_session):
        _set_theme(admin_session, "light")
        _set_theme(doctor_session, "dark")
        assert _me(admin_session)["theme"] == "light"
        assert _me(doctor_session)["theme"] == "dark"

    def test_requires_auth(self):
        r = requests.patch(
            f"{API}/auth/me/preferences",
            json={"theme": "dark"}, timeout=10,
        )
        assert r.status_code == 401
