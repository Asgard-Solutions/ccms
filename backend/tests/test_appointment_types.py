"""
Appointment Types (iteration 22) — CRUD, tenant scoping, permissioning, audits.

Exercises:
  * create / read / list / update / soft-delete / reactivate
  * validation: duration bounds, blank name rejection, case-insensitive
    uniqueness per tenant
  * tenant isolation — Sunrise can't see Default's types
  * RBAC — doctor & staff can list; only admin can mutate
  * active_only filter returns only is_active=true rows
"""
from __future__ import annotations

import os
import uuid

import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

API = os.environ.get("CCMS_BASE_URL", "http://localhost:8001/api")

DEFAULT_ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")
DEFAULT_DOCTOR = ("doctor@ccms.app", "Doctor@ComplianceClinic1")
DEFAULT_STAFF = ("staff@ccms.app", "Staff@ComplianceClinic1")
GROUP_ADMIN = ("group-admin@sunrise.ccms.app", "Sunrise@ComplianceClinic1")


def _login(email: str, password: str) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{API}/auth/login",
               json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, r.text
    access = r.cookies.get("access_token")
    if access:
        s.headers["Authorization"] = f"Bearer {access}"
    return s


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:6]}"


def _cleanup(s: requests.Session, ids: list[str]) -> None:
    for tid in ids:
        try:
            s.delete(f"{API}/appointment-types/{tid}", timeout=5)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# CRUD happy-path
# ---------------------------------------------------------------------------
def test_admin_crud_lifecycle():
    s = _login(*DEFAULT_ADMIN)
    created_ids: list[str] = []
    try:
        name = _unique_name("Initial")
        r = s.post(f"{API}/appointment-types", json={
            "name": name,
            "default_duration_minutes": 60,
            "description": "First visit",
        }, timeout=10)
        assert r.status_code == 201, r.text
        body = r.json()
        created_ids.append(body["id"])
        assert body["name"] == name
        assert body["default_duration_minutes"] == 60
        assert body["is_active"] is True

        # GET list sees it
        r = s.get(f"{API}/appointment-types", timeout=10)
        assert r.status_code == 200
        assert any(t["id"] == body["id"] for t in r.json())

        # PUT update duration
        r = s.patch(f"{API}/appointment-types/{body['id']}",
                  json={"default_duration_minutes": 45}, timeout=10)
        assert r.status_code == 200, r.text
        assert r.json()["default_duration_minutes"] == 45

        # DELETE deactivates
        r = s.delete(f"{API}/appointment-types/{body['id']}", timeout=10)
        assert r.status_code == 204, r.text
        r = s.get(f"{API}/appointment-types?active_only=true", timeout=10)
        assert all(t["id"] != body["id"] for t in r.json())

        # Reactivate
        r = s.post(f"{API}/appointment-types/{body['id']}/reactivate", timeout=10)
        assert r.status_code == 200
        assert r.json()["is_active"] is True
    finally:
        _cleanup(s, created_ids)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def test_duration_bounds_rejected():
    s = _login(*DEFAULT_ADMIN)
    for bad in (0, 3, 500, -10):
        r = s.post(f"{API}/appointment-types", json={
            "name": _unique_name("Bad"),
            "default_duration_minutes": bad,
        }, timeout=10)
        assert r.status_code == 422, (bad, r.text)


def test_blank_name_rejected():
    s = _login(*DEFAULT_ADMIN)
    r = s.post(f"{API}/appointment-types", json={
        "name": "   ",
        "default_duration_minutes": 30,
    }, timeout=10)
    assert r.status_code == 422


def test_case_insensitive_name_uniqueness():
    s = _login(*DEFAULT_ADMIN)
    name = _unique_name("Dup")
    created_ids: list[str] = []
    try:
        r = s.post(f"{API}/appointment-types", json={
            "name": name,
            "default_duration_minutes": 30,
        }, timeout=10)
        assert r.status_code == 201
        created_ids.append(r.json()["id"])
        # Same name, different case → 409
        r = s.post(f"{API}/appointment-types", json={
            "name": name.upper(),
            "default_duration_minutes": 30,
        }, timeout=10)
        assert r.status_code == 409, r.text
    finally:
        _cleanup(s, created_ids)


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------
def test_doctor_staff_can_list_but_not_mutate():
    admin = _login(*DEFAULT_ADMIN)
    created_ids: list[str] = []
    try:
        r = admin.post(f"{API}/appointment-types", json={
            "name": _unique_name("ReadOnly"),
            "default_duration_minutes": 30,
        }, timeout=10)
        assert r.status_code == 201
        tid = r.json()["id"]
        created_ids.append(tid)

        for creds in (DEFAULT_DOCTOR, DEFAULT_STAFF):
            s = _login(*creds)
            # List allowed
            lr = s.get(f"{API}/appointment-types", timeout=10)
            assert lr.status_code == 200, creds
            # Create forbidden
            cr = s.post(f"{API}/appointment-types", json={
                "name": _unique_name("ShouldFail"),
                "default_duration_minutes": 30,
            }, timeout=10)
            assert cr.status_code == 403, (creds, cr.status_code)
            # Update forbidden
            ur = s.patch(f"{API}/appointment-types/{tid}",
                       json={"default_duration_minutes": 45}, timeout=10)
            assert ur.status_code == 403, (creds, ur.status_code)
            # Delete forbidden
            dr = s.delete(f"{API}/appointment-types/{tid}", timeout=10)
            assert dr.status_code == 403, (creds, dr.status_code)
    finally:
        _cleanup(admin, created_ids)


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------
def test_tenant_isolation():
    default_admin = _login(*DEFAULT_ADMIN)
    sunrise_admin = _login(*GROUP_ADMIN)
    created_ids: list[str] = []
    try:
        r = default_admin.post(f"{API}/appointment-types", json={
            "name": _unique_name("DefaultOnly"),
            "default_duration_minutes": 30,
        }, timeout=10)
        assert r.status_code == 201
        tid = r.json()["id"]
        created_ids.append(tid)

        # Sunrise admin must NOT see it
        sr_list = sunrise_admin.get(f"{API}/appointment-types", timeout=10).json()
        assert all(t["id"] != tid for t in sr_list)

        # Sunrise admin cannot update it (404 — not leaking existence).
        upd = sunrise_admin.patch(f"{API}/appointment-types/{tid}",
                                 json={"default_duration_minutes": 60}, timeout=10)
        assert upd.status_code == 404, upd.text
    finally:
        _cleanup(default_admin, created_ids)


# ---------------------------------------------------------------------------
# active_only filter
# ---------------------------------------------------------------------------
def test_active_only_filter():
    s = _login(*DEFAULT_ADMIN)
    created_ids: list[str] = []
    try:
        r = s.post(f"{API}/appointment-types", json={
            "name": _unique_name("Filterable"),
            "default_duration_minutes": 20,
        }, timeout=10)
        assert r.status_code == 201
        tid = r.json()["id"]
        created_ids.append(tid)
        s.delete(f"{API}/appointment-types/{tid}", timeout=10)

        all_rows = s.get(f"{API}/appointment-types", timeout=10).json()
        active_rows = s.get(f"{API}/appointment-types?active_only=true", timeout=10).json()
        assert any(t["id"] == tid for t in all_rows)
        assert all(t["id"] != tid for t in active_rows)
    finally:
        _cleanup(s, created_ids)
