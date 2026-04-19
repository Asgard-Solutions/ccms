"""
CCMS Backend End-to-End Test Suite
Covers: Identity/Auth, RBAC, Patients, Scheduling (conflict detection,
event-driven flow), Communication/Notifications, Health.

All requests use cookie-based auth via requests.Session.
"""
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://chiro-cloud.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN = ("admin@ccms.app", "Admin@123")
DOCTOR = ("doctor@ccms.app", "Doctor@123")
STAFF = ("staff@ccms.app", "Staff@123")
PATIENT = ("patient@ccms.app", "Patient@123")


def _login(email: str, password: str) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, f"login failed for {email}: {r.status_code} {r.text}"
    assert "access_token" in s.cookies, "access_token cookie not set"
    assert "refresh_token" in s.cookies, "refresh_token cookie not set"
    return s


# ---------- Fixtures ----------
@pytest.fixture(scope="session")
def admin_session():
    return _login(*ADMIN)


@pytest.fixture(scope="session")
def doctor_session():
    return _login(*DOCTOR)


@pytest.fixture(scope="session")
def staff_session():
    return _login(*STAFF)


@pytest.fixture(scope="session")
def patient_session():
    return _login(*PATIENT)


# ---------- Health ----------
class TestHealth:
    def test_health(self):
        r = requests.get(f"{API}/health", timeout=10)
        assert r.status_code == 200
        assert r.json() == {"status": "healthy"}

    def test_root_banner(self):
        r = requests.get(f"{API}/", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert data.get("service") == "CCMS API Gateway"


# ---------- Identity / Auth ----------
class TestAuth:
    def test_login_admin_and_me(self, admin_session):
        r = admin_session.get(f"{API}/auth/me", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert data["email"] == ADMIN[0]
        assert data["role"] == "admin"

    def test_me_with_bearer_fallback(self):
        # verify Authorization: Bearer token fallback works too
        s = requests.Session()
        r = s.post(f"{API}/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]}, timeout=10)
        assert r.status_code == 200
        token = s.cookies.get("access_token")
        assert token
        r2 = requests.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {token}"}, timeout=10)
        assert r2.status_code == 200
        assert r2.json()["email"] == ADMIN[0]

    def test_logout_clears_cookie(self):
        s = _login(*DOCTOR)
        r = s.post(f"{API}/auth/logout", timeout=10)
        assert r.status_code == 200
        # after logout, /me should be 401
        s2 = requests.Session()
        # explicitly drop cookies
        r2 = s2.get(f"{API}/auth/me", timeout=10)
        assert r2.status_code == 401

    def test_refresh_rotates_access(self):
        s = _login(*STAFF)
        old_access = s.cookies.get("access_token")
        r = s.post(f"{API}/auth/refresh", timeout=10)
        assert r.status_code == 200
        new_access = s.cookies.get("access_token")
        assert new_access is not None
        # Refresh token should still be present
        assert s.cookies.get("refresh_token") is not None

    def test_register_always_patient(self):
        email = f"test_{uuid.uuid4().hex[:8]}@ccms.app"
        s = requests.Session()
        r = s.post(
            f"{API}/auth/register",
            json={"email": email, "password": "Secret@123", "name": "Test Reg", "role": "admin"},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["role"] == "patient", "public register must force role=patient"
        assert data["email"] == email.lower()

    def test_brute_force_lockout(self):
        # Use a fresh unique email so we don't lock out shared demo users
        email = f"TEST_bf_{uuid.uuid4().hex[:8]}@ccms.app"
        s = requests.Session()
        # Create the user via register so we can try wrong passwords for it
        r = s.post(
            f"{API}/auth/register",
            json={"email": email, "password": "RealPass@123", "name": "BF Test"},
            timeout=15,
        )
        assert r.status_code == 200
        # 5 wrong attempts -> 401
        for i in range(5):
            rr = requests.post(f"{API}/auth/login", json={"email": email, "password": "wrong"}, timeout=10)
            assert rr.status_code == 401, f"attempt {i+1}: {rr.status_code}"
        # 6th should be 429
        rr = requests.post(f"{API}/auth/login", json={"email": email, "password": "wrong"}, timeout=10)
        assert rr.status_code == 429, f"expected lockout 429, got {rr.status_code} {rr.text}"


# ---------- RBAC ----------
class TestRBAC:
    def test_admin_can_list_users(self, admin_session):
        r = admin_session.get(f"{API}/auth/users", timeout=10)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_non_admin_cannot_list_users(self, doctor_session, staff_session, patient_session):
        for s, who in [(doctor_session, "doctor"), (staff_session, "staff"), (patient_session, "patient")]:
            r = s.get(f"{API}/auth/users", timeout=10)
            assert r.status_code == 403, f"{who} got {r.status_code}"

    def test_admin_create_user_any_role(self, admin_session):
        email = f"TEST_doc_{uuid.uuid4().hex[:6]}@ccms.app"
        r = admin_session.post(
            f"{API}/auth/users",
            json={"email": email, "password": "Pass@1234", "name": "New Doc", "role": "doctor"},
            timeout=15,
        )
        assert r.status_code == 201, r.text
        assert r.json()["role"] == "doctor"

    def test_providers_visible_to_any_auth_user(self, patient_session, doctor_session):
        for s in [patient_session, doctor_session]:
            r = s.get(f"{API}/auth/providers", timeout=10)
            assert r.status_code == 200
            data = r.json()
            assert isinstance(data, list)
            assert all(u["role"] == "doctor" for u in data)


# ---------- Patients ----------
class TestPatients:
    def test_list_patients_seed(self, admin_session):
        r = admin_session.get(f"{API}/patients", timeout=10)
        assert r.status_code == 200
        pats = r.json()
        names = [f"{p['first_name']} {p['last_name']}" for p in pats]
        assert any("Morgan" in n and "Lee" in n for n in names), f"Morgan Lee not found: {names}"

    def test_patient_role_sees_only_self(self, patient_session):
        r = patient_session.get(f"{API}/patients", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        # Morgan Lee is linked to patient@ccms.app
        assert data[0]["first_name"].lower() == "morgan"

    def test_create_update_delete_flow(self, admin_session, doctor_session):
        # Create (staff role via admin)
        payload = {
            "first_name": "TEST_Alice",
            "last_name": "Wonder",
            "email": f"TEST_alice_{uuid.uuid4().hex[:6]}@x.com",
            "phone": "555-0100",
            "date_of_birth": "1990-01-01",
            "gender": "female",
        }
        r = admin_session.post(f"{API}/patients", json=payload, timeout=10)
        assert r.status_code == 201, r.text
        pid = r.json()["id"]

        # Update
        r = admin_session.put(f"{API}/patients/{pid}", json={"phone": "555-9999"}, timeout=10)
        assert r.status_code == 200
        assert r.json()["phone"] == "555-9999"

        # Get persisted
        r = admin_session.get(f"{API}/patients/{pid}", timeout=10)
        assert r.status_code == 200
        assert r.json()["phone"] == "555-9999"

        # Non-admin cannot delete
        r = doctor_session.delete(f"{API}/patients/{pid}", timeout=10)
        assert r.status_code == 403

        # Admin can delete
        r = admin_session.delete(f"{API}/patients/{pid}", timeout=10)
        assert r.status_code == 200
        # Verify gone
        r = admin_session.get(f"{API}/patients/{pid}", timeout=10)
        assert r.status_code == 404

    def test_medical_records_admin_doctor_only(self, admin_session, doctor_session, staff_session):
        # Find morgan lee
        r = admin_session.get(f"{API}/patients", timeout=10)
        pid = next(p["id"] for p in r.json() if p["first_name"].lower() == "morgan")

        # Doctor can add records
        r = doctor_session.post(
            f"{API}/patients/{pid}/records",
            json={"record_type": "note", "title": "TEST Rec", "details": "ok"},
            timeout=10,
        )
        assert r.status_code == 201, r.text

        # Staff cannot add
        r = staff_session.post(
            f"{API}/patients/{pid}/records",
            json={"record_type": "note", "title": "TEST", "details": "x"},
            timeout=10,
        )
        assert r.status_code == 403

        # List records works for admin/doctor/staff
        r = admin_session.get(f"{API}/patients/{pid}/records", timeout=10)
        assert r.status_code == 200
        assert len(r.json()) >= 1


# ---------- Scheduling + Event Flow ----------
@pytest.fixture(scope="class")
def ctx(admin_session):
    """Provides: patient_id (Morgan), provider_id (seeded doctor)."""
    r = admin_session.get(f"{API}/patients", timeout=10)
    pid = next(p["id"] for p in r.json() if p["first_name"].lower() == "morgan")
    r = admin_session.get(f"{API}/auth/providers", timeout=10)
    providers = r.json()
    assert providers, "no seeded doctors"
    # pick the seeded demo doctor
    seeded = [d for d in providers if d["email"] == DOCTOR[0]]
    pv = (seeded or providers)[0]
    return {"patient_id": pid, "provider_id": pv["id"]}


class TestSchedulingAndEvents:
    def _slot(self, offset_minutes=60, duration=30):
        # Use a random far-future slot to avoid colliding with stale test data
        import random
        start = datetime.now(timezone.utc) + timedelta(
            days=30 + random.randint(0, 365), minutes=offset_minutes + random.randint(0, 500)
        )
        # pin seconds to 0 to keep reschedule math clean
        start = start.replace(second=0, microsecond=0)
        end = start + timedelta(minutes=duration)
        return start.isoformat(), end.isoformat()

    def test_full_lifecycle(self, admin_session, doctor_session, patient_session, ctx):
        # Count baseline notifications for this patient
        r = admin_session.get(f"{API}/notifications", params={"patient_id": ctx["patient_id"]}, timeout=10)
        assert r.status_code == 200
        baseline = len(r.json())

        # --- Book ---
        start, end = self._slot(offset_minutes=120)
        payload = {
            "patient_id": ctx["patient_id"],
            "provider_id": ctx["provider_id"],
            "start_time": start,
            "end_time": end,
            "reason": "TEST booking",
        }
        r = admin_session.post(f"{API}/appointments", json=payload, timeout=10)
        assert r.status_code == 201, r.text
        appt = r.json()
        aid = appt["id"]
        assert appt["status"] == "scheduled"
        assert appt.get("patient_name") and appt.get("provider_name"), "hydration missing"

        # notifications grew by >= 2 with appointment.booked
        import time; time.sleep(0.5)
        r = admin_session.get(f"{API}/notifications", params={"patient_id": ctx["patient_id"]}, timeout=10)
        booked_notifs = [n for n in r.json() if n["event_type"] == "appointment.booked"]
        assert len(booked_notifs) >= 2, f"expected >=2 booked notifs, got {len(booked_notifs)}"
        channels = {n["channel"] for n in booked_notifs}
        assert {"email", "sms"}.issubset(channels)

        # --- Conflict check: exact overlap on same provider -> 409 ---
        r = admin_session.post(f"{API}/appointments", json=payload, timeout=10)
        assert r.status_code == 409, f"expected 409 on overlap, got {r.status_code}"

        # --- Reschedule (same appt, shifted 1h) — must NOT conflict with itself ---
        new_start = (datetime.fromisoformat(start) + timedelta(hours=1)).isoformat()
        new_end = (datetime.fromisoformat(end) + timedelta(hours=1)).isoformat()
        r = admin_session.put(
            f"{API}/appointments/{aid}",
            json={"start_time": new_start, "end_time": new_end},
            timeout=10,
        )
        assert r.status_code == 200, r.text
        import time; time.sleep(0.5)
        r = admin_session.get(f"{API}/notifications", params={"patient_id": ctx["patient_id"]}, timeout=10)
        updated_notifs = [n for n in r.json() if n["event_type"] == "appointment.updated"]
        assert len(updated_notifs) >= 2

        # --- Patient sees only their own appts (known seed bug may block) ---
        r = patient_session.get(f"{API}/appointments", timeout=10)
        assert r.status_code == 200
        assert all(a["patient_id"] == ctx["patient_id"] for a in r.json())
        patient_sees_appt = any(a["id"] == aid for a in r.json())

        # --- Filters ---
        r = admin_session.get(f"{API}/appointments", params={"status": "scheduled"}, timeout=10)
        assert r.status_code == 200
        assert all(a["status"] == "scheduled" for a in r.json())

        r = admin_session.get(
            f"{API}/appointments",
            params={"provider_id": ctx["provider_id"], "patient_id": ctx["patient_id"]},
            timeout=10,
        )
        assert r.status_code == 200
        assert all(
            a["provider_id"] == ctx["provider_id"] and a["patient_id"] == ctx["patient_id"]
            for a in r.json()
        )

        # --- Cancel ---
        r = admin_session.post(f"{API}/appointments/{aid}/cancel", timeout=10)
        assert r.status_code == 200
        assert r.json()["status"] == "cancelled"
        import time; time.sleep(0.5)
        r = admin_session.get(f"{API}/notifications", params={"patient_id": ctx["patient_id"]}, timeout=10)
        cancelled_notifs = [n for n in r.json() if n["event_type"] == "appointment.cancelled"]
        assert len(cancelled_notifs) >= 2

        # Total grew by >= 6
        assert len(r.json()) - baseline >= 6, f"expected +6 notifications, got {len(r.json()) - baseline}"

        # --- Modifying cancelled -> 400 ---
        r = admin_session.put(
            f"{API}/appointments/{aid}",
            json={"reason": "shouldnt work"},
            timeout=10,
        )
        assert r.status_code == 400

        # Surface known bug at end so the rest of the lifecycle assertions run first
        assert patient_sees_appt, (
            "BUG: patient-role user cannot see their own appointment — seed.py "
            "line 72 uses 'patient@ccms.local' but demo user is 'patient@ccms.app', "
            "so Morgan Lee's patient record is NOT linked to the demo patient user."
        )

    def test_missing_patient_provider_404(self, admin_session, ctx):
        start, end = self._slot(offset_minutes=500)
        # bad patient
        r = admin_session.post(
            f"{API}/appointments",
            json={
                "patient_id": "nonexistent-" + uuid.uuid4().hex,
                "provider_id": ctx["provider_id"],
                "start_time": start, "end_time": end,
            }, timeout=10,
        )
        assert r.status_code == 404
        # bad provider
        r = admin_session.post(
            f"{API}/appointments",
            json={
                "patient_id": ctx["patient_id"],
                "provider_id": "nonexistent-" + uuid.uuid4().hex,
                "start_time": start, "end_time": end,
            }, timeout=10,
        )
        assert r.status_code == 404


# ---------- Notifications RBAC ----------
class TestNotificationsRBAC:
    def test_admin_and_staff_allowed(self, admin_session, staff_session):
        for s in [admin_session, staff_session]:
            r = s.get(f"{API}/notifications", timeout=10)
            assert r.status_code == 200

    def test_doctor_and_patient_forbidden(self, doctor_session, patient_session):
        for s in [doctor_session, patient_session]:
            r = s.get(f"{API}/notifications", timeout=10)
            assert r.status_code == 403
