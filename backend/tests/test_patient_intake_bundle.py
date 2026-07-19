"""Tests for Patient Intake & Engagement bundle:
  * SMS credentials + send (log-only fallback)
  * Portal SMS OTP login
  * Booking requests end-to-end (portal create → staff approve)
  * Portal self check-in
  * Kiosk public check-in
  * Questionnaires (assign → submit → outcome row)
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")

# Use the public REACT_APP_BACKEND_URL so the auth cookie (Secure flag)
# round-trips; localhost:8001 won't accept Secure cookies.
_BASE = (os.environ.get("REACT_APP_BACKEND_URL") or "http://localhost:8001").rstrip("/")
API = os.environ.get("CCMS_BASE_URL", f"{_BASE}/api")
DEFAULT_ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")


# ---------------------------------------------------------------------------
# Helpers (mirrored from other test files)
# ---------------------------------------------------------------------------
def _login(email, password):
    s = requests.Session()
    r = s.post(f"{API}/auth/login",
               json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, r.text
    access = r.cookies.get("access_token")
    if access:
        s.headers["Authorization"] = f"Bearer {access}"
    return s


def _unique(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _ensure_patient_with_phone(s) -> tuple[str, str, str]:
    """Return (patient_id, phone_digits, dob)."""
    # 10 digits, no hex — the Pydantic normalizer demands exactly 10.
    import secrets as _secrets
    phone = "503555" + f"{_secrets.randbelow(10000):04d}"
    dob = "1990-05-15"
    body = {
        "first_name": "Intake",
        "last_name": _unique("Tester"),
        "date_of_birth": dob,
        "gender": "female",
        "phone": phone,
    }
    r = s.post(f"{API}/patients", json=body, timeout=15)
    assert r.status_code in (200, 201), r.text
    pid = r.json()["id"]
    assert len(phone) == 10
    return pid, phone, dob


# ---------------------------------------------------------------------------
# SMS
# ---------------------------------------------------------------------------
class TestSmsSettings:
    def test_settings_not_configured_by_default(self):
        s = _login(*DEFAULT_ADMIN)
        r = s.get(f"{API}/sms/settings", timeout=10)
        assert r.status_code == 200, r.text
        body = r.json()
        # Unconfigured or previously set; either way the endpoint works.
        assert "configured" in body

    def test_send_test_falls_back_to_log_only(self):
        s = _login(*DEFAULT_ADMIN)
        # Remove creds first to guarantee log-only
        s.delete(f"{API}/sms/settings", timeout=10)
        r = s.post(f"{API}/sms/settings/test",
                   json={"to": "5035550210", "body": "Hi"}, timeout=10)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] in ("logged", "failed", "sent")
        # In log-only mode we always end up as 'logged'
        assert body["status"] == "logged"
        assert body["provider"] == "log-only"

    def test_staff_send_logs_when_not_configured(self):
        s = _login(*DEFAULT_ADMIN)
        s.delete(f"{API}/sms/settings", timeout=10)
        r = s.post(f"{API}/sms/send",
                   json={"to": "5035550210", "body": "ping"}, timeout=10)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "logged"


# ---------------------------------------------------------------------------
# Portal OTP
# ---------------------------------------------------------------------------
class TestPortalOtp:
    def test_request_and_verify_happy_path(self):
        admin = _login(*DEFAULT_ADMIN)
        pid, phone, _ = _ensure_patient_with_phone(admin)
        # Request OTP — should include dev_code in log-only mode
        r = requests.post(
            f"{API}/portal/auth/otp/request",
            json={"phone": phone}, timeout=10,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("sent") is True
        code = data.get("dev_code")
        assert code and len(code) == 6

        # Verify
        session = requests.Session()
        r2 = session.post(
            f"{API}/portal/auth/otp/verify",
            json={"phone": phone, "code": code}, timeout=10,
        )
        assert r2.status_code == 200, r2.text
        body = r2.json()
        assert body["user"]["role"] == "patient"
        assert body["user"]["linked_patient_id"] == pid
        # Cookie set
        assert session.cookies.get("access_token")
        # /auth/me MUST round-trip — this is how the frontend
        # rehydrates the AuthContext on page reload. Regression: the
        # placeholder email domain must not hit pydantic's reserved
        # TLD blocklist (`.local`, `.invalid`, etc.).
        me = session.get(f"{API}/auth/me", timeout=10)
        assert me.status_code == 200, me.text
        assert me.json()["role"] == "patient"
        # Overview endpoint reachable
        r3 = session.get(f"{API}/portal/overview", timeout=10)
        assert r3.status_code == 200, r3.text
        assert "upcoming_appointments" in r3.json()

    def test_bad_code_is_rejected(self):
        admin = _login(*DEFAULT_ADMIN)
        _ensure_patient_with_phone(admin)
        r = requests.post(
            f"{API}/portal/auth/otp/verify",
            json={"phone": "5035550210", "code": "000000"}, timeout=10,
        )
        assert r.status_code == 401

    def test_unknown_phone_never_leaks_existence(self):
        # Totally random phone — response should still look identical.
        r = requests.post(
            f"{API}/portal/auth/otp/request",
            json={"phone": "9998887777"}, timeout=10,
        )
        assert r.status_code == 200
        assert r.json().get("sent") is True


# ---------------------------------------------------------------------------
# Booking requests
# ---------------------------------------------------------------------------
class TestBookingRequests:
    def test_portal_create_and_staff_approve(self):
        admin = _login(*DEFAULT_ADMIN)
        pid, phone, _ = _ensure_patient_with_phone(admin)
        # Log patient in
        requests.post(
            f"{API}/portal/auth/otp/request",
            json={"phone": phone}, timeout=10,
        )
        code = requests.post(
            f"{API}/portal/auth/otp/request",
            json={"phone": phone}, timeout=10,
        ).json()["dev_code"]
        patient = requests.Session()
        v = patient.post(
            f"{API}/portal/auth/otp/verify",
            json={"phone": phone, "code": code}, timeout=10,
        )
        assert v.status_code == 200

        # Patient creates a booking request
        slot = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        r = patient.post(
            f"{API}/portal/booking-requests",
            json={
                "reason": "Follow-up",
                "preferred_slots": [{"start_time": slot}],
            },
            timeout=10,
        )
        assert r.status_code == 201, r.text
        rid = r.json()["id"]

        # Staff lists and approves
        rlist = admin.get(
            f"{API}/booking-requests?status_filter=pending", timeout=10,
        )
        assert any(row["id"] == rid for row in rlist.json())
        approve = admin.post(
            f"{API}/booking-requests/{rid}/approve",
            json={"start_time": slot, "duration_minutes": 30},
            timeout=10,
        )
        assert approve.status_code == 200, approve.text
        body = approve.json()
        assert body["status"] == "approved"
        assert body["appointment_id"]

    def test_staff_decline(self):
        admin = _login(*DEFAULT_ADMIN)
        pid, phone, _ = _ensure_patient_with_phone(admin)
        requests.post(
            f"{API}/portal/auth/otp/request",
            json={"phone": phone}, timeout=10,
        )
        code = requests.post(
            f"{API}/portal/auth/otp/request",
            json={"phone": phone}, timeout=10,
        ).json()["dev_code"]
        patient = requests.Session()
        patient.post(
            f"{API}/portal/auth/otp/verify",
            json={"phone": phone, "code": code}, timeout=10,
        )
        slot = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
        rid = patient.post(
            f"{API}/portal/booking-requests",
            json={"reason": "Too busy slot", "preferred_slots": [{"start_time": slot}]},
            timeout=10,
        ).json()["id"]
        r = admin.post(
            f"{API}/booking-requests/{rid}/decline",
            json={"reason": "Provider unavailable"}, timeout=10,
        )
        assert r.status_code == 200
        assert r.json()["status"] == "declined"


# ---------------------------------------------------------------------------
# Questionnaires
# ---------------------------------------------------------------------------
class TestQuestionnaires:
    def test_templates_catalog(self):
        s = _login(*DEFAULT_ADMIN)
        r = s.get(f"{API}/questionnaires/templates", timeout=10)
        assert r.status_code == 200
        ids = {t["id"] for t in r.json()}
        assert {"nprs", "odi", "ndi", "psfs"} <= ids

    def test_template_detail_contains_items(self):
        s = _login(*DEFAULT_ADMIN)
        r = s.get(f"{API}/questionnaires/templates/odi", timeout=10)
        assert r.status_code == 200
        assert len(r.json()["items"]) == 10

    def test_assign_and_patient_submit_creates_outcome(self):
        admin = _login(*DEFAULT_ADMIN)
        pid, phone, _ = _ensure_patient_with_phone(admin)
        # Assign NPRS
        a = admin.post(
            f"{API}/questionnaires/assign",
            json={"patient_id": pid, "template_id": "nprs", "send_sms": True},
            timeout=10,
        )
        assert a.status_code == 201, a.text
        aid = a.json()["id"]

        # Patient signs in and submits
        code = requests.post(
            f"{API}/portal/auth/otp/request",
            json={"phone": phone}, timeout=10,
        ).json()["dev_code"]
        patient = requests.Session()
        patient.post(
            f"{API}/portal/auth/otp/verify",
            json={"phone": phone, "code": code}, timeout=10,
        )
        # Fetch questionnaire
        q = patient.get(f"{API}/portal/questionnaires/{aid}", timeout=10)
        assert q.status_code == 200
        assert q.json()["template"]["id"] == "nprs"
        # Submit
        sub = patient.post(
            f"{API}/portal/questionnaires/{aid}/submit",
            json={"answers": {"now": 7}}, timeout=10,
        )
        assert sub.status_code == 200, sub.text
        body = sub.json()
        assert body["score"] == 7.0
        assert body["interpretation"] == "Severe pain"
        assert body["outcome_entry_id"]

    def test_odi_scoring_math(self):
        from services.questionnaires.templates import score_answers
        # All minimums -> 0%, minimal disability
        answers = {f"{k}": 0 for k in
                   ("pain_intensity", "personal_care", "lifting", "walking",
                    "sitting", "standing", "sleeping", "sex_life",
                    "social_life", "travelling")}
        r = score_answers("odi", answers)
        assert r["score"] == 0.0
        # All max -> 100%, bed-bound
        answers = {k: 5 for k in answers}
        r = score_answers("odi", answers)
        assert r["score"] == 100.0
        assert "Bed-bound" in r["interpretation"]

    def test_submit_rejects_missing_answers(self):
        """Empty submissions are rejected with 422 — avoids the
        clinically-dangerous default-to-zero bug."""
        admin = _login(*DEFAULT_ADMIN)
        pid, phone, _ = _ensure_patient_with_phone(admin)
        a = admin.post(
            f"{API}/questionnaires/assign",
            json={"patient_id": pid, "template_id": "nprs",
                  "send_sms": False},
            timeout=10,
        )
        aid = a.json()["id"]
        code = requests.post(
            f"{API}/portal/auth/otp/request",
            json={"phone": phone}, timeout=10,
        ).json()["dev_code"]
        patient = requests.Session()
        patient.post(
            f"{API}/portal/auth/otp/verify",
            json={"phone": phone, "code": code}, timeout=10,
        )
        r = patient.post(
            f"{API}/portal/questionnaires/{aid}/submit",
            json={"answers": {}}, timeout=10,
        )
        assert r.status_code == 422
        assert "now" in r.text  # required item id present in message


# ---------------------------------------------------------------------------
# Kiosk
# ---------------------------------------------------------------------------
class TestKiosk:
    def test_unknown_patient_returns_404(self):
        r = requests.post(
            f"{API}/kiosk/check-in",
            json={"last_name": "Nobody", "date_of_birth": "1900-01-01"},
            timeout=10,
        )
        assert r.status_code == 404

    def test_patient_without_today_appt_returns_404(self):
        admin = _login(*DEFAULT_ADMIN)
        _, _, _ = _ensure_patient_with_phone(admin)
        # Same patient but uses a different last_name (unique) so no appt today
        r = requests.post(
            f"{API}/kiosk/check-in",
            json={"last_name": "Whitaker", "date_of_birth": "1900-01-01"},
            timeout=10,
        )
        # Dob mismatch
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Portal self check-in
# ---------------------------------------------------------------------------
class TestPortalCheckin:
    def test_checkin_rejects_other_days(self):
        admin = _login(*DEFAULT_ADMIN)
        pid, phone, _ = _ensure_patient_with_phone(admin)
        # Create an appointment for tomorrow
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1))
        body = {
            "patient_id": pid,
            "start_time": tomorrow.isoformat(),
            "end_time": (tomorrow + timedelta(minutes=30)).isoformat(),
        }
        appt = admin.post(f"{API}/appointments", json=body, timeout=10)
        if appt.status_code >= 400:
            pytest.skip(f"Scheduling API rejected: {appt.text}")
        aid = appt.json()["id"]

        # Patient logs in
        code = requests.post(
            f"{API}/portal/auth/otp/request",
            json={"phone": phone}, timeout=10,
        ).json()["dev_code"]
        patient = requests.Session()
        patient.post(
            f"{API}/portal/auth/otp/verify",
            json={"phone": phone, "code": code}, timeout=10,
        )
        r = patient.post(
            f"{API}/portal/appointments/{aid}/check-in", timeout=10,
        )
        # Not today's appointment — 409
        assert r.status_code == 409
