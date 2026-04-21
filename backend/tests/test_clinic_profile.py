"""
Clinic Profile (iteration 21) — CRUD, tenant scoping, permissioning, audits.

Exercises:
  * create / read / list / update / delete
  * validation: HH:MM format, interval ordering, overlap detection,
    day-of-week coverage, invalid timezone
  * tenant isolation — Sunrise can't see Default, Default can't see Sunrise
  * RBAC — doctor & staff can read; doctor cannot mutate
  * audit rows emitted for each mutation
"""
from __future__ import annotations

import os
import uuid

import pytest
import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

API = os.environ.get("CCMS_BASE_URL", "http://localhost:8001/api")

DEFAULT_ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")
GROUP_ADMIN = ("group-admin@sunrise.ccms.app", "Sunrise@ComplianceClinic1")
DOWNTOWN_DOC = ("downtown-doc@sunrise.ccms.app", "Sunrise@ComplianceClinic1")
EASTSIDE_STAFF = ("eastside-staff@sunrise.ccms.app", "Sunrise@ComplianceClinic1")


def _login(email: str, password: str) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{API}/auth/login",
               json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, r.text
    access = r.cookies.get("access_token")
    if access:
        s.headers["Authorization"] = f"Bearer {access}"
    return s


def _first_location_id(s: requests.Session) -> tuple[str, str]:
    """Return (tenant_id, first_location_id) for the caller."""
    ctx = s.get(f"{API}/tenancy/me/context", timeout=10).json()
    tid = ctx["tenant"]["id"]
    locs = ctx.get("locations") or []
    if not locs:
        locs = s.get(f"{API}/tenancy/tenants/{tid}/locations",
                     timeout=10).json()
    assert locs, "no locations visible"
    return tid, locs[0]["id"]


def _valid_hours() -> list[dict]:
    return [
        {"day_of_week": i,
         "is_closed": (i >= 5),
         "intervals": [] if i >= 5
                     else [{"open_time": "09:00", "close_time": "17:00"}]}
        for i in range(7)
    ]


def _cleanup(s: requests.Session, location_id: str) -> None:
    r = s.get(f"{API}/clinic-profiles/{location_id}", timeout=5)
    if r.status_code == 200:
        s.delete(f"{API}/clinic-profiles/{location_id}", timeout=5)


# ---------------------------------------------------------------------------
# Happy path — create / read / list / update / delete
# ---------------------------------------------------------------------------
def test_clinic_profile_crud_happy_path():
    admin = _login(*GROUP_ADMIN)
    tid, loc_id = _first_location_id(admin)
    _cleanup(admin, loc_id)

    # Create
    payload = {
        "location_id": loc_id,
        "name": "Sunrise Downtown",
        "address_line1": "1 Market St",
        "city": "Portland",
        "state": "OR",
        "postal_code": "97201",
        "primary_phone": "+1 503-555-0100",
        "secondary_phone": "+1 503-555-0101",
        "email": "hello@sunrise.ccms.app",
        "website": "https://sunrise.example",
        "timezone": "America/Los_Angeles",
        "hours": _valid_hours(),
        "notes": "Flagship clinic",
    }
    r = admin.post(f"{API}/clinic-profiles", json=payload, timeout=10)
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["tenant_id"] == tid
    assert created["location_id"] == loc_id
    assert created["name"] == "Sunrise Downtown"
    assert len(created["hours"]) == 7
    assert created["hours"][0]["day_of_week"] == 0
    assert created["hours"][5]["is_closed"] is True

    # Read by location id
    r = admin.get(f"{API}/clinic-profiles/{loc_id}", timeout=10)
    assert r.status_code == 200
    assert r.json()["id"] == created["id"]

    # Read by profile id
    r = admin.get(f"{API}/clinic-profiles/{created['id']}", timeout=10)
    assert r.status_code == 200
    assert r.json()["location_id"] == loc_id

    # List
    r = admin.get(f"{API}/clinic-profiles", timeout=10)
    assert r.status_code == 200
    ids = [p["id"] for p in r.json()]
    assert created["id"] in ids

    # Update
    new_hours = _valid_hours()
    # Add a lunch-break split for Monday (two intervals)
    new_hours[0] = {
        "day_of_week": 0, "is_closed": False,
        "intervals": [
            {"open_time": "09:00", "close_time": "12:00"},
            {"open_time": "13:00", "close_time": "17:00"},
        ],
    }
    r = admin.patch(f"{API}/clinic-profiles/{loc_id}",
                  json={"primary_phone": "+1 503-555-9999", "hours": new_hours},
                  timeout=10)
    assert r.status_code == 200, r.text
    updated = r.json()
    assert updated["primary_phone"] == "+1 503-555-9999"
    mon = next(h for h in updated["hours"] if h["day_of_week"] == 0)
    assert len(mon["intervals"]) == 2

    # Delete
    r = admin.delete(f"{API}/clinic-profiles/{loc_id}", timeout=10)
    assert r.status_code == 204
    r = admin.get(f"{API}/clinic-profiles/{loc_id}", timeout=10)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Validation — bad HH:MM, overlapping intervals, missing day, bad tz
# ---------------------------------------------------------------------------
def test_bad_hours_rejected():
    admin = _login(*GROUP_ADMIN)
    _tid, loc_id = _first_location_id(admin)
    _cleanup(admin, loc_id)

    base = {
        "location_id": loc_id,
        "name": "x", "timezone": "America/Los_Angeles",
    }

    # Invalid HH:MM format
    bad = _valid_hours()
    bad[0]["intervals"][0]["open_time"] = "9:00"  # missing leading zero
    r = admin.post(f"{API}/clinic-profiles", json={**base, "hours": bad}, timeout=10)
    assert r.status_code == 422, r.text

    # close <= open
    bad = _valid_hours()
    bad[0]["intervals"][0] = {"open_time": "17:00", "close_time": "09:00"}
    r = admin.post(f"{API}/clinic-profiles", json={**base, "hours": bad}, timeout=10)
    assert r.status_code == 422

    # overlapping intervals on same day
    bad = _valid_hours()
    bad[0]["intervals"] = [
        {"open_time": "09:00", "close_time": "13:00"},
        {"open_time": "12:30", "close_time": "17:00"},  # overlap
    ]
    r = admin.post(f"{API}/clinic-profiles", json={**base, "hours": bad}, timeout=10)
    assert r.status_code == 422

    # missing Sunday (day_of_week 6)
    bad = _valid_hours()[:6]
    r = admin.post(f"{API}/clinic-profiles", json={**base, "hours": bad}, timeout=10)
    assert r.status_code == 422

    # invalid timezone
    bad = _valid_hours()
    r = admin.post(f"{API}/clinic-profiles",
                   json={**base, "timezone": "Not/A_Zone", "hours": bad},
                   timeout=10)
    assert r.status_code == 422

    # is_closed with intervals is inconsistent
    bad = _valid_hours()
    bad[5] = {"day_of_week": 5, "is_closed": True,
              "intervals": [{"open_time": "09:00", "close_time": "17:00"}]}
    r = admin.post(f"{API}/clinic-profiles", json={**base, "hours": bad}, timeout=10)
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Uniqueness — one profile per (tenant, location)
# ---------------------------------------------------------------------------
def test_conflict_when_profile_already_exists():
    admin = _login(*GROUP_ADMIN)
    _tid, loc_id = _first_location_id(admin)
    _cleanup(admin, loc_id)

    payload = {"location_id": loc_id, "name": "A",
               "timezone": "America/Los_Angeles", "hours": _valid_hours()}
    r = admin.post(f"{API}/clinic-profiles", json=payload, timeout=10)
    assert r.status_code == 201
    try:
        r = admin.post(f"{API}/clinic-profiles", json=payload, timeout=10)
        assert r.status_code == 409, r.text
    finally:
        _cleanup(admin, loc_id)


# ---------------------------------------------------------------------------
# RBAC — doctor & staff can read, but only admin can mutate
# ---------------------------------------------------------------------------
def test_doctor_and_staff_read_but_not_write():
    admin = _login(*GROUP_ADMIN)
    _tid, loc_id = _first_location_id(admin)
    _cleanup(admin, loc_id)

    r = admin.post(f"{API}/clinic-profiles",
                   json={"location_id": loc_id, "name": "rbac-test",
                         "timezone": "America/Los_Angeles",
                         "hours": _valid_hours()},
                   timeout=10)
    assert r.status_code == 201

    try:
        doc = _login(*DOWNTOWN_DOC)
        # Doctor can read
        r = doc.get(f"{API}/clinic-profiles/{loc_id}", timeout=10)
        assert r.status_code == 200
        # Doctor cannot update
        r = doc.patch(f"{API}/clinic-profiles/{loc_id}",
                    json={"name": "hacked"}, timeout=10)
        assert r.status_code == 403
        # Doctor cannot delete
        r = doc.delete(f"{API}/clinic-profiles/{loc_id}", timeout=10)
        assert r.status_code == 403

        staff = _login(*EASTSIDE_STAFF)
        # Eastside staff is scoped to Eastside location only — they must NOT
        # see the Downtown profile we just created. Expected: 404 for the
        # specific id, and the list endpoint omits it.
        r = staff.get(f"{API}/clinic-profiles/{loc_id}", timeout=10)
        assert r.status_code == 404
        r = staff.get(f"{API}/clinic-profiles", timeout=10)
        assert r.status_code == 200
        assert not any(p["location_id"] == loc_id for p in r.json())
    finally:
        _cleanup(admin, loc_id)


# ---------------------------------------------------------------------------
# Tenant isolation — Sunrise can't see Default's profile and vice versa
# ---------------------------------------------------------------------------
def test_tenant_isolation():
    sunrise = _login(*GROUP_ADMIN)
    default_admin = _login(*DEFAULT_ADMIN)

    _stid, sunrise_loc = _first_location_id(sunrise)
    _dtid, default_loc = _first_location_id(default_admin)

    _cleanup(sunrise, sunrise_loc)
    _cleanup(default_admin, default_loc)

    # Create in Sunrise
    r = sunrise.post(f"{API}/clinic-profiles",
                     json={"location_id": sunrise_loc, "name": "Sunrise X",
                           "timezone": "America/Los_Angeles",
                           "hours": _valid_hours()},
                     timeout=10)
    assert r.status_code == 201, r.text
    sunrise_pid = r.json()["id"]

    try:
        # Default admin can't hit the Sunrise profile by id OR by location_id
        r = default_admin.get(f"{API}/clinic-profiles/{sunrise_pid}", timeout=10)
        assert r.status_code == 404
        r = default_admin.get(f"{API}/clinic-profiles/{sunrise_loc}", timeout=10)
        assert r.status_code == 404

        # Default admin can't create a profile for a Sunrise location either
        r = default_admin.post(f"{API}/clinic-profiles",
                               json={"location_id": sunrise_loc,
                                     "name": "cross-tenant attempt",
                                     "timezone": "America/Los_Angeles",
                                     "hours": _valid_hours()},
                               timeout=10)
        assert r.status_code == 404  # location masked as absent
    finally:
        _cleanup(sunrise, sunrise_loc)
        _cleanup(default_admin, default_loc)


# ---------------------------------------------------------------------------
# Audit rows written for each mutation
# ---------------------------------------------------------------------------
def test_mutations_emit_audit_rows():
    admin = _login(*DEFAULT_ADMIN)
    _tid, loc_id = _first_location_id(admin)
    _cleanup(admin, loc_id)

    r = admin.post(f"{API}/clinic-profiles",
                   json={"location_id": loc_id, "name": "audit-test",
                         "timezone": "America/Los_Angeles",
                         "hours": _valid_hours()},
                   timeout=10)
    assert r.status_code == 201
    pid = r.json()["id"]

    admin.patch(f"{API}/clinic-profiles/{loc_id}",
              json={"primary_phone": "+15551234567"}, timeout=10)
    admin.delete(f"{API}/clinic-profiles/{loc_id}", timeout=10)

    # /audit-logs may require reauth cookie/header.
    r = admin.post(f"{API}/auth/reauth",
                   json={"password": DEFAULT_ADMIN[1]}, timeout=10)
    assert r.status_code == 200, r.text
    reauth = r.cookies.get("reauth_token")
    if reauth:
        admin.headers["x-reauth-token"] = reauth

    r = admin.get(f"{API}/audit-logs",
                  params={"entity_id": pid, "limit": 50}, timeout=10)
    assert r.status_code == 200, r.text
    actions = {row["action"] for row in r.json()}
    assert "clinic_profile.created" in actions
    assert "clinic_profile.updated" in actions
    assert "clinic_profile.deleted" in actions
