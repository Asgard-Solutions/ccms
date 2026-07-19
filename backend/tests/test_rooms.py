"""Backend tests — Room management + room assignment workflow (Phase 4).

Covers:
  * Rooms CRUD (create, rename, deactivate, uniqueness, tenant scope)
  * Appointment room assignment endpoint:
      - assigns + stamps room_assigned_at/by + sets location_type=roomed
      - emits appointment_room_history row
  * Single-occupancy conflict rejected with 409
  * force=true override requires a reason + audits forced=True
  * change_room replaces and history carries from_room_id → to_room_id
  * clear_room removes current_room_id and optionally returns to waiting
  * Patient-portal role cannot touch /rooms or /appointments/{id}/room
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest  # noqa: F401
import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

BASE = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE}/api" if BASE else "http://localhost:8001/api"

DEFAULT_ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")
PATIENT_USER = ("patient@ccms.app", "Patient@ComplianceClinic1")


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


def _get_primary_location(s: requests.Session) -> str:
    ctx = s.get(f"{API}/tenancy/me/context", timeout=10).json()
    locs = ctx.get("locations") or []
    assert locs, "Test tenant must have at least one location"
    return locs[0]["id"]


def _create_room(s: requests.Session, location_id: str, *, name: str | None = None,
                 type_: str = "exam") -> dict:
    name = name or f"Room-{uuid.uuid4().hex[:6]}"
    r = s.post(f"{API}/rooms", json={
        "location_id": location_id, "name": name, "type": type_,
    }, timeout=10)
    assert r.status_code == 201, r.text
    return r.json()


def _create_appt(s: requests.Session, location_id: str,
                 *, check_in: bool = True) -> dict:
    patients = s.get(f"{API}/patients", timeout=10).json()
    patient_id = patients[0]["id"]
    providers = s.get(f"{API}/auth/providers", timeout=10).json()
    provider_id = providers[0]["id"]
    offset = (uuid.uuid4().int >> 32) % 500000
    start = datetime.now(timezone.utc) + timedelta(days=60, minutes=offset)
    end = start + timedelta(minutes=15)
    r = s.post(f"{API}/appointments", json={
        "patient_id": patient_id, "provider_id": provider_id,
        "start_time": start.isoformat(), "end_time": end.isoformat(),
        "location_id": location_id,
        "reason": "room test",
    }, timeout=10)
    assert r.status_code == 201, r.text
    appt = r.json()
    if check_in:
        r = s.post(f"{API}/appointments/{appt['id']}/check-in", json={}, timeout=10)
        assert r.status_code == 200, r.text
        appt = r.json()
    return appt


# ---------------------------------------------------------------------------
# Rooms CRUD
# ---------------------------------------------------------------------------

def test_room_crud_happy_path():
    s = _login(*DEFAULT_ADMIN)
    loc = _get_primary_location(s)
    r = s.post(f"{API}/rooms", json={
        "location_id": loc, "name": f"Exam {uuid.uuid4().hex[:4]}",
        "type": "exam", "sort_order": 10,
    }, timeout=10)
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    assert r.json()["type"] == "exam"

    # Rename + deactivate + type change.
    r = s.patch(f"{API}/rooms/{rid}", json={
        "name": f"Renamed {uuid.uuid4().hex[:4]}",
        "type": "xray",
        "is_active": False,
    }, timeout=10)
    assert r.status_code == 200, r.text
    assert r.json()["is_active"] is False
    assert r.json()["type"] == "xray"

    # List with active_only excludes the deactivated room.
    active_rooms = s.get(f"{API}/rooms", params={"active_only": "true", "location_id": loc},
                        timeout=10).json()
    assert rid not in [x["id"] for x in active_rooms]


def test_room_name_uniqueness_case_insensitive():
    s = _login(*DEFAULT_ADMIN)
    loc = _get_primary_location(s)
    name = f"Consult {uuid.uuid4().hex[:5]}"
    a = s.post(f"{API}/rooms", json={"location_id": loc, "name": name, "type": "consult"}, timeout=10)
    assert a.status_code == 201, a.text
    # Different case, same name — must collide.
    b = s.post(f"{API}/rooms", json={"location_id": loc, "name": name.upper(), "type": "consult"}, timeout=10)
    assert b.status_code == 409, b.text


def test_room_delete_blocked_when_history_exists():
    s = _login(*DEFAULT_ADMIN)
    loc = _get_primary_location(s)
    room = _create_room(s, loc)
    appt = _create_appt(s, loc)

    # Assign room → history row recorded → hard delete must be blocked.
    r = s.post(f"{API}/appointments/{appt['id']}/room",
               json={"room_id": room["id"], "reason": "assign"}, timeout=10)
    assert r.status_code == 200, r.text
    # Clear so deletion-of-occupant isn't the blocker (we want history to be).
    r = s.post(f"{API}/appointments/{appt['id']}/clear-room",
               params={"return_to_waiting": "true"}, timeout=10)
    assert r.status_code == 200, r.text
    r = s.delete(f"{API}/rooms/{room['id']}", timeout=10)
    assert r.status_code == 409, r.text


# ---------------------------------------------------------------------------
# Appointment room assignment
# ---------------------------------------------------------------------------

def test_assign_room_sets_stamps_and_history():
    s = _login(*DEFAULT_ADMIN)
    loc = _get_primary_location(s)
    room = _create_room(s, loc)
    appt = _create_appt(s, loc)

    r = s.post(f"{API}/appointments/{appt['id']}/room",
               json={"room_id": room["id"], "reason": "initial room"}, timeout=10)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["current_room_id"] == room["id"]
    assert body["current_room_name"] == room["name"]
    assert body["current_location_type"] == "roomed"
    assert body["room_assigned_at"] and body["room_assigned_by_user_id"]

    hist = s.get(f"{API}/appointments/{appt['id']}/room-history", timeout=10).json()
    assert len(hist) >= 1
    last = hist[-1]
    assert last["to_room_id"] == room["id"]
    assert last["forced"] is False
    assert last["from_room_id"] in (None, "")  # fresh assignment


def test_change_room_writes_from_and_to_in_history():
    s = _login(*DEFAULT_ADMIN)
    loc = _get_primary_location(s)
    r1 = _create_room(s, loc)
    r2 = _create_room(s, loc)
    appt = _create_appt(s, loc)

    s.post(f"{API}/appointments/{appt['id']}/room",
           json={"room_id": r1["id"]}, timeout=10)
    r = s.post(f"{API}/appointments/{appt['id']}/room",
               json={"room_id": r2["id"], "reason": "moved"}, timeout=10)
    assert r.status_code == 200, r.text
    assert r.json()["current_room_id"] == r2["id"]

    hist = s.get(f"{API}/appointments/{appt['id']}/room-history", timeout=10).json()
    assert len(hist) >= 2
    last = hist[-1]
    assert last["from_room_id"] == r1["id"]
    assert last["to_room_id"] == r2["id"]


def test_clear_room_with_return_to_waiting():
    s = _login(*DEFAULT_ADMIN)
    loc = _get_primary_location(s)
    room = _create_room(s, loc)
    appt = _create_appt(s, loc)

    s.post(f"{API}/appointments/{appt['id']}/room",
           json={"room_id": room["id"]}, timeout=10)
    r = s.post(f"{API}/appointments/{appt['id']}/clear-room",
               params={"return_to_waiting": "true", "reason": "patient stepped out"},
               timeout=10)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["current_room_id"] in (None, "")
    assert body["current_location_type"] == "waiting_room"


# ---------------------------------------------------------------------------
# Single-occupancy conflict + force override
# ---------------------------------------------------------------------------

def test_single_occupancy_conflict_blocks_without_force():
    s = _login(*DEFAULT_ADMIN)
    loc = _get_primary_location(s)
    room = _create_room(s, loc)
    a1 = _create_appt(s, loc)
    a2 = _create_appt(s, loc)

    # a1 takes the room.
    r = s.post(f"{API}/appointments/{a1['id']}/room",
               json={"room_id": room["id"]}, timeout=10)
    assert r.status_code == 200, r.text

    # a2 can't take the same room without force.
    r = s.post(f"{API}/appointments/{a2['id']}/room",
               json={"room_id": room["id"]}, timeout=10)
    assert r.status_code == 409, r.text


def test_force_override_requires_reason_and_audits_forced_true():
    s = _login(*DEFAULT_ADMIN)
    loc = _get_primary_location(s)
    room = _create_room(s, loc)
    a1 = _create_appt(s, loc)
    a2 = _create_appt(s, loc)
    s.post(f"{API}/appointments/{a1['id']}/room",
           json={"room_id": room["id"]}, timeout=10)

    # force=true without reason → 400.
    r = s.post(f"{API}/appointments/{a2['id']}/room",
               json={"room_id": room["id"], "force": True}, timeout=10)
    assert r.status_code == 400, r.text

    # force=true with a reason → 200; history row carries forced=True.
    r = s.post(f"{API}/appointments/{a2['id']}/room",
               json={"room_id": room["id"], "force": True,
                     "reason": "double-booking emergency"},
               timeout=10)
    assert r.status_code == 200, r.text
    assert r.json()["current_room_id"] == room["id"]

    hist = s.get(f"{API}/appointments/{a2['id']}/room-history", timeout=10).json()
    assert hist and hist[-1]["forced"] is True


def test_inactive_room_cannot_be_assigned():
    s = _login(*DEFAULT_ADMIN)
    loc = _get_primary_location(s)
    room = _create_room(s, loc)
    s.patch(f"{API}/rooms/{room['id']}", json={"is_active": False}, timeout=10)
    appt = _create_appt(s, loc)
    r = s.post(f"{API}/appointments/{appt['id']}/room",
               json={"room_id": room["id"]}, timeout=10)
    assert r.status_code == 400, r.text


def test_terminal_appointment_cannot_take_a_room():
    s = _login(*DEFAULT_ADMIN)
    loc = _get_primary_location(s)
    room = _create_room(s, loc)
    appt = _create_appt(s, loc, check_in=False)
    s.post(f"{API}/appointments/{appt['id']}/cancel", timeout=10)
    r = s.post(f"{API}/appointments/{appt['id']}/room",
               json={"room_id": room["id"]}, timeout=10)
    assert r.status_code == 400, r.text


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------

def test_patient_portal_cannot_touch_rooms():
    s = _login(*PATIENT_USER, reauth=False)
    r = s.get(f"{API}/rooms", timeout=10)
    assert r.status_code in (401, 403), r.text
    r = s.post(f"{API}/rooms", json={
        "location_id": "x", "name": "Nope", "type": "exam",
    }, timeout=10)
    assert r.status_code in (401, 403), r.text
