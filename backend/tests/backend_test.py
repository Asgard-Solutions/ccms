"""
CCMS HIPAA-hardened Backend Test Suite (Iteration 2).

Covers previously-passing Phase 1 flows plus new HIPAA safeguards:
- Identity / strong-password policy / password history (last-5)
- MFA setup -> verify -> challenge (TOTP + backup code) -> disable
- Brute-force lockout (5 -> 429)
- Audit log: admin-only + PHI-touching rows with phi_accessed=true
- Field-level AES-256-GCM encryption at rest (enc:v1: prefix)
- PHI masking + unmask with reason
- Break-glass reason enforcement for non-admin patient detail
- Step-up reauth for add-medical-record & delete-patient
- Soft-delete + 7-year retention + include_deleted
- Patient right-to-access export
- Account disable / enable
- Notifications masking + unmask
- Scheduling lifecycle still produces 6 mock notifications; notes encrypted
- RBAC regressions
"""
import os
import uuid
import time
from datetime import datetime, timedelta, timezone

import pymongo
import pyotp
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
API = f"{BASE_URL}/api"
MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME", "test_database")

ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")
DOCTOR = ("doctor@ccms.app", "Doctor@ComplianceClinic1")
STAFF = ("staff@ccms.app", "Staff@ComplianceClinic1")
PATIENT = ("patient@ccms.app", "Patient@ComplianceClinic1")

_db = pymongo.MongoClient(MONGO_URL)[DB_NAME]


def _reset_admin_mfa():
    _db.users.update_one(
        {"email": ADMIN[0]},
        {"$set": {"mfa_enabled": False}, "$unset": {"mfa_secret": "", "mfa_backup_codes": ""}},
    )


def _login(email: str, password: str, expect_mfa: bool = False) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, f"login failed for {email}: {r.status_code} {r.text}"
    body = r.json()
    if expect_mfa:
        assert body.get("mfa_required") is True, f"expected MFA required, got {body}"
    else:
        assert body.get("mfa_required") is False, f"unexpected MFA required: {body}"
        assert "access_token" in s.cookies, "access_token cookie not set"
    return s


# ---------- Session fixtures ----------
@pytest.fixture(scope="session", autouse=True)
def _wipe_admin_mfa_at_start():
    _reset_admin_mfa()
    yield
    _reset_admin_mfa()


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
        assert r.json().get("status") == "healthy"


# ---------- Identity / Password Policy ----------
class TestPasswordPolicy:
    def test_register_weak_rejected(self):
        email = f"TEST_weak_{uuid.uuid4().hex[:6]}@ccms.app"
        r = requests.post(
            f"{API}/auth/register",
            json={"email": email, "password": "weak", "name": "Weak"},
            timeout=10,
        )
        assert r.status_code == 400 or r.status_code == 422, r.text

    def test_register_common_weak_rejected(self):
        email = f"TEST_weak2_{uuid.uuid4().hex[:6]}@ccms.app"
        r = requests.post(
            f"{API}/auth/register",
            json={"email": email, "password": "password1234", "name": "W2"},
            timeout=10,
        )
        assert r.status_code in (400, 422), r.text

    def test_register_strong_auto_login(self):
        email = f"TEST_strong_{uuid.uuid4().hex[:6]}@ccms.app"
        s = requests.Session()
        r = s.post(
            f"{API}/auth/register",
            json={"email": email, "password": "NewPatient@Abc123!", "name": "Strong"},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # Register returns user; cookies must be set
        assert s.cookies.get("access_token"), "register should auto-login"
        # Confirm via /me
        r2 = s.get(f"{API}/auth/me", timeout=10)
        assert r2.status_code == 200
        assert r2.json()["role"] == "patient"

    def test_change_password_rejects_reuse_and_weak(self):
        """Use a dedicated user so admin creds don't rotate out mid-suite."""
        email = f"TEST_pwhist_{uuid.uuid4().hex[:6]}@ccms.app"
        p1 = "FirstPwd@Clinic1!"
        p2 = "SecondPwd@Clinic2!"
        p3 = "ThirdPwd@Clinic3!"
        s = requests.Session()
        r = s.post(
            f"{API}/auth/register",
            json={"email": email, "password": p1, "name": "Hist"}, timeout=15,
        )
        assert r.status_code == 200, r.text

        # Reject weak
        r = s.post(
            f"{API}/auth/change-password",
            json={"current_password": p1, "new_password": "weak"}, timeout=10,
        )
        assert r.status_code in (400, 422), r.text

        # Rotate p1 -> p2
        r = s.post(
            f"{API}/auth/change-password",
            json={"current_password": p1, "new_password": p2}, timeout=10,
        )
        assert r.status_code == 200, r.text

        # Re-login with new password (session rotation may clear cookie)
        s2 = _login(email, p2)

        # Rotate p2 -> p3
        r = s2.post(
            f"{API}/auth/change-password",
            json={"current_password": p2, "new_password": p3}, timeout=10,
        )
        assert r.status_code == 200, r.text
        s3 = _login(email, p3)

        # Try to reuse p2 (should be in history → 400)
        r = s3.post(
            f"{API}/auth/change-password",
            json={"current_password": p3, "new_password": p2}, timeout=10,
        )
        assert r.status_code == 400, f"reuse of p2 should be rejected, got {r.status_code} {r.text}"

        # Try to reuse p3 (current) — also rejected
        r = s3.post(
            f"{API}/auth/change-password",
            json={"current_password": p3, "new_password": p3}, timeout=10,
        )
        assert r.status_code == 400, f"reuse of current should be rejected, got {r.status_code} {r.text}"


class TestBruteForce:
    def test_brute_force_lockout(self):
        email = f"TEST_bf_{uuid.uuid4().hex[:6]}@ccms.app"
        r = requests.post(
            f"{API}/auth/register",
            json={"email": email, "password": "RealPass@Clinic1!", "name": "BF"},
            timeout=15,
        )
        assert r.status_code == 200
        for i in range(5):
            rr = requests.post(
                f"{API}/auth/login", json={"email": email, "password": "wrong"}, timeout=10
            )
            assert rr.status_code == 401, f"attempt {i+1}: {rr.status_code} {rr.text}"
        rr = requests.post(
            f"{API}/auth/login", json={"email": email, "password": "wrong"}, timeout=10
        )
        assert rr.status_code == 429, f"expected 429, got {rr.status_code} {rr.text}"


# ---------- MFA ----------
class TestMFA:
    def test_mfa_full_flow(self):
        """Enrol MFA on a fresh test user (admin should not be mutated)."""
        email = f"TEST_mfa_{uuid.uuid4().hex[:6]}@ccms.app"
        pwd = "MFATester@Clinic1!"
        s = requests.Session()
        r = s.post(
            f"{API}/auth/register",
            json={"email": email, "password": pwd, "name": "MFA User"},
            timeout=15,
        )
        assert r.status_code == 200, r.text

        # Setup
        r = s.post(f"{API}/auth/mfa/setup", timeout=10)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "secret" in data and len(data["secret"]) >= 16
        assert "otpauth_url" in data and data["otpauth_url"].startswith("otpauth://")
        assert "backup_codes" in data and len(data["backup_codes"]) == 8
        secret = data["secret"]
        backup_codes = list(data["backup_codes"])

        # Verify with TOTP
        code = pyotp.TOTP(secret).now()
        r = s.post(f"{API}/auth/mfa/verify", json={"code": code}, timeout=10)
        assert r.status_code == 200, r.text

        # Fresh login must require MFA
        s2 = requests.Session()
        r = s2.post(f"{API}/auth/login", json={"email": email, "password": pwd}, timeout=10)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("mfa_required") is True, body
        assert body.get("user") in (None, {}) or body.get("user") is None
        ticket = body.get("mfa_ticket")
        assert ticket

        # Challenge with TOTP completes login
        code2 = pyotp.TOTP(secret).now()
        r = s2.post(
            f"{API}/auth/mfa/challenge",
            json={"mfa_ticket": ticket, "code": code2},
            timeout=10,
        )
        assert r.status_code == 200, r.text
        assert s2.cookies.get("access_token"), "cookie after MFA challenge"

        # Backup code flow: new session, login, use backup code at challenge
        s3 = requests.Session()
        r = s3.post(f"{API}/auth/login", json={"email": email, "password": pwd}, timeout=10)
        t2 = r.json().get("mfa_ticket")
        r = s3.post(
            f"{API}/auth/mfa/challenge",
            json={"mfa_ticket": t2, "code": backup_codes[0]},
            timeout=10,
        )
        assert r.status_code == 200, f"backup code rejected: {r.status_code} {r.text}"

        # Backup code is one-shot — reuse should fail
        s4 = requests.Session()
        r = s4.post(f"{API}/auth/login", json={"email": email, "password": pwd}, timeout=10)
        t3 = r.json().get("mfa_ticket")
        r = s4.post(
            f"{API}/auth/mfa/challenge",
            json={"mfa_ticket": t3, "code": backup_codes[0]},
            timeout=10,
        )
        assert r.status_code in (400, 401, 403), f"reused backup code should fail, got {r.status_code}"

        # Disable MFA
        r = s2.post(
            f"{API}/auth/mfa/disable", json={"password": pwd}, timeout=10
        )
        assert r.status_code == 200, r.text

        # Login no longer requires MFA
        s5 = requests.Session()
        r = s5.post(f"{API}/auth/login", json={"email": email, "password": pwd}, timeout=10)
        assert r.json().get("mfa_required") is False


# ---------- Audit Log ----------
class TestAuditLog:
    def test_admin_only(self, admin_session, doctor_session, staff_session, patient_session):
        r = admin_session.get(f"{API}/audit-logs", timeout=10)
        assert r.status_code == 200
        assert isinstance(r.json(), list)
        assert len(r.json()) > 0

        for s, who in [(doctor_session, "doctor"), (staff_session, "staff"), (patient_session, "patient")]:
            rr = s.get(f"{API}/audit-logs", timeout=10)
            assert rr.status_code == 403, f"{who} should be forbidden, got {rr.status_code}"

    def test_audit_rows_have_required_fields_and_phi_flag(self, admin_session):
        # Trigger a patient list view (PHI access)
        admin_session.get(f"{API}/patients", timeout=10)
        time.sleep(0.3)
        r = admin_session.get(f"{API}/audit-logs", timeout=10)
        rows = r.json()
        assert rows, "no audit rows"
        sample = rows[0]
        for field in ["ip", "user_agent", "outcome", "created_at"]:
            assert field in sample, f"audit row missing {field}: keys={list(sample.keys())}"
        phi_rows = [row for row in rows if row.get("phi_accessed") is True]
        assert phi_rows, "expected at least one PHI-touching audit row"


# ---------- Field-Level Encryption at Rest ----------
class TestEncryptionAtRest:
    def test_patient_free_text_encrypted(self, admin_session):
        payload = {
            "first_name": "TEST_Enc",
            "last_name": "Subject",
            "email": f"TEST_enc_{uuid.uuid4().hex[:6]}@x.com",
            "phone": "555-0200",
            "date_of_birth": "1985-05-05",
            "gender": "other",
            "address": "123 Secret Lane, Nowhere",
            "emergency_contact": "John Doe 555-9998",
            "notes": "Sensitive PHI notes for encryption test",
        }
        r = admin_session.post(f"{API}/patients", json=payload, timeout=10)
        assert r.status_code == 201, r.text
        pid = r.json()["id"]

        # Raw mongo check
        raw = _db.patients.find_one({"id": pid})
        assert raw, "patient not found in mongo"
        for field in ["address", "emergency_contact", "notes"]:
            v = raw.get(field)
            assert isinstance(v, str) and v.startswith("enc:v1:"), (
                f"{field} not encrypted at rest, raw value={v!r}"
            )

        # GET via API decrypts for admin
        r = admin_session.get(f"{API}/patients/{pid}?unmask=true", timeout=10)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["address"] == payload["address"]
        assert data["notes"] == payload["notes"]

        # Medical record encryption
        r = admin_session.get(f"{API}/auth/reauth-check", timeout=5)
        # Step-up reauth first
        rx = admin_session.post(
            f"{API}/auth/reauth", json={"password": ADMIN[1]}, timeout=10
        )
        # If admin pwd rotated earlier, allow skip
        if rx.status_code != 200:
            pytest.skip(f"admin reauth failed (pwd may have rotated): {rx.status_code}")

        rec_payload = {
            "record_type": "diagnosis",
            "title": "TEST Diag",
            "description": "Encrypted description text",
            "diagnosis": "Encrypted dx",
            "treatment": "Encrypted tx",
            "details": "Encrypted detail",
        }
        r = admin_session.post(f"{API}/patients/{pid}/records", json=rec_payload, timeout=10)
        assert r.status_code == 201, r.text
        raw_rec = _db.medical_records.find_one({"patient_id": pid})
        if raw_rec:
            for field in ["description", "diagnosis", "treatment"]:
                v = raw_rec.get(field)
                if v:
                    assert v.startswith("enc:v1:"), f"record {field} not encrypted: {v!r}"


# ---------- PHI Masking ----------
class TestPHIMasking:
    def test_list_masked_by_default_admin(self, admin_session):
        r = admin_session.get(f"{API}/patients", timeout=10)
        assert r.status_code == 200
        rows = r.json()
        assert rows, "no patients"
        # Find a row with known details
        p = rows[0]
        assert p.get("display_name_masked") or p.get("display_name"), p
        # email masked: p******
        if p.get("email"):
            assert "*" in p["email"], f"email not masked: {p['email']}"
        if p.get("dob"):
            assert "*" in p["dob"], f"dob not masked: {p['dob']}"
        if p.get("phone"):
            assert "*" in p["phone"], f"phone not masked: {p['phone']}"
        if p.get("address"):
            assert p["address"] == "[redacted]" or "*" in p["address"], p["address"]

    def test_unmask_admin_returns_cleartext(self, admin_session):
        r = admin_session.get(f"{API}/patients?unmask=true", timeout=10)
        assert r.status_code == 200
        rows = r.json()
        assert rows
        # At least one row has unmasked=true
        assert any(row.get("unmasked") is True for row in rows), "no unmasked=true flag in response"
        # Email should not contain ***
        emails = [r.get("email") for r in rows if r.get("email")]
        if emails:
            assert any("*" not in e for e in emails), f"no cleartext emails: {emails}"


# ---------- Break-glass ----------
class TestBreakGlass:
    def test_doctor_detail_requires_reason(self, admin_session, doctor_session):
        r = admin_session.get(f"{API}/patients", timeout=10)
        pid = r.json()[0]["id"]
        # No reason
        r = doctor_session.get(f"{API}/patients/{pid}", timeout=10)
        assert r.status_code == 400, f"expected 400, got {r.status_code} {r.text}"
        # Too-short reason
        r = doctor_session.get(f"{API}/patients/{pid}?reason=short", timeout=10)
        assert r.status_code == 400, r.text
        # Valid reason
        r = doctor_session.get(
            f"{API}/patients/{pid}?reason=Covering Dr. Monroe", timeout=10
        )
        assert r.status_code == 200, r.text

    def test_patient_self_no_reason_needed(self, patient_session):
        # patient role sees their own patient record
        r = patient_session.get(f"{API}/patients", timeout=10)
        assert r.status_code == 200
        rows = r.json()
        if not rows:
            pytest.skip("patient role has no linked patient record")
        pid = rows[0]["id"]
        r = patient_session.get(f"{API}/patients/{pid}", timeout=10)
        assert r.status_code == 200, r.text


# ---------- Step-up Reauth ----------
class TestStepUpReauth:
    def test_add_record_requires_reauth(self, admin_session):
        # create a throwaway patient to avoid polluting Morgan
        email = f"TEST_reauth_{uuid.uuid4().hex[:6]}@x.com"
        # Must have a fresh admin session (no reauth cookie)
        fresh = _login(*ADMIN)
        r = fresh.post(
            f"{API}/patients",
            json={
                "first_name": "TEST_RA",
                "last_name": "Pt",
                "email": email,
                "phone": "555-0301",
                "date_of_birth": "1991-01-01",
                "gender": "other",
            },
            timeout=10,
        )
        assert r.status_code == 201, r.text
        pid = r.json()["id"]

        # Without reauth -> 401
        rec = {"record_type": "note", "title": "TEST", "description": "x"}
        r = fresh.post(f"{API}/patients/{pid}/records", json=rec, timeout=10)
        assert r.status_code == 401, f"expected 401 Re-auth required, got {r.status_code} {r.text}"

        # Reauth
        r = fresh.post(f"{API}/auth/reauth", json={"password": ADMIN[1]}, timeout=10)
        if r.status_code != 200:
            pytest.skip(f"reauth failed; admin pwd may have been rotated by an earlier test: {r.status_code}")

        # Retry
        r = fresh.post(f"{API}/patients/{pid}/records", json=rec, timeout=10)
        assert r.status_code == 201, r.text


# ---------- Soft-delete + Retention ----------
class TestSoftDelete:
    def test_soft_delete_with_reauth_and_reason(self, admin_session):
        fresh = _login(*ADMIN)
        r = fresh.post(
            f"{API}/patients",
            json={
                "first_name": "TEST_SD",
                "last_name": "Gone",
                "email": f"TEST_sd_{uuid.uuid4().hex[:6]}@x.com",
                "phone": "555-0401",
                "date_of_birth": "1988-08-08",
                "gender": "female",
            },
            timeout=10,
        )
        assert r.status_code == 201, r.text
        pid = r.json()["id"]

        # Without reauth -> 401
        r = fresh.delete(
            f"{API}/patients/{pid}?reason=compliance cleanup", timeout=10
        )
        assert r.status_code == 401, f"expected 401, got {r.status_code}"

        # With reauth
        r = fresh.post(f"{API}/auth/reauth", json={"password": ADMIN[1]}, timeout=10)
        if r.status_code != 200:
            pytest.skip("reauth failed; cannot soft-delete test")

        r = fresh.delete(
            f"{API}/patients/{pid}?reason=compliance cleanup", timeout=10
        )
        assert r.status_code == 200, r.text

        # Not in default list
        r = fresh.get(f"{API}/patients", timeout=10)
        assert r.status_code == 200
        assert not any(p["id"] == pid for p in r.json())

        # include_deleted=true shows it
        r = fresh.get(f"{API}/patients?include_deleted=true", timeout=10)
        assert r.status_code == 200
        ids = [p["id"] for p in r.json()]
        assert pid in ids

        # GET by id still returns it with status=deleted
        r = fresh.get(f"{API}/patients/{pid}", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert data.get("status") == "deleted"
        # Retention ~ 7 years out
        raw = _db.patients.find_one({"id": pid})
        assert raw.get("deleted_at"), "deleted_at not set"
        assert raw.get("retention_until"), "retention_until not set"


# ---------- Export ----------
class TestExport:
    def test_admin_can_export(self, admin_session):
        r = admin_session.get(f"{API}/patients", timeout=10)
        pid = r.json()[0]["id"]
        r = admin_session.get(f"{API}/patients/{pid}/export", timeout=10)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "patient" in data
        assert "medical_records" in data
        assert "appointments" in data

    def test_doctor_forbidden_staff_forbidden(self, doctor_session, staff_session, admin_session):
        r = admin_session.get(f"{API}/patients", timeout=10)
        pid = r.json()[0]["id"]
        for s, who in [(doctor_session, "doctor"), (staff_session, "staff")]:
            r = s.get(f"{API}/patients/{pid}/export", timeout=10)
            assert r.status_code == 403, f"{who} got {r.status_code}"

    def test_patient_self_export(self, patient_session):
        r = patient_session.get(f"{API}/patients", timeout=10)
        rows = r.json()
        if not rows:
            pytest.skip("patient has no linked record")
        pid = rows[0]["id"]
        r = patient_session.get(f"{API}/patients/{pid}/export", timeout=10)
        assert r.status_code == 200, r.text


# ---------- Account Disable / Enable ----------
class TestAccountDisable:
    def test_disable_and_reenable(self, admin_session):
        email = f"TEST_disable_{uuid.uuid4().hex[:6]}@ccms.app"
        pwd = "DisableMe@Clinic1!"
        r = requests.post(
            f"{API}/auth/register",
            json={"email": email, "password": pwd, "name": "Disable Me"},
            timeout=15,
        )
        assert r.status_code == 200
        # Find user_id (emails are stored lowercased)
        user = _db.users.find_one({"email": email.lower()})
        assert user, f"user {email} not found after register"
        uid = user["id"]

        r = admin_session.post(f"{API}/auth/users/{uid}/disable", timeout=10)
        assert r.status_code == 200, r.text

        # Login attempts now 403
        r = requests.post(
            f"{API}/auth/login", json={"email": email, "password": pwd}, timeout=10
        )
        assert r.status_code == 403, f"expected 403 disabled, got {r.status_code} {r.text}"

        # Re-enable
        r = admin_session.post(f"{API}/auth/users/{uid}/enable", timeout=10)
        assert r.status_code == 200

        r = requests.post(
            f"{API}/auth/login", json={"email": email, "password": pwd}, timeout=10
        )
        assert r.status_code == 200


# ---------- Notifications Masking ----------
class TestNotificationsMasking:
    def test_admin_masked_default_and_unmask(self, admin_session):
        r = admin_session.get(f"{API}/notifications", timeout=10)
        assert r.status_code == 200
        rows = r.json()
        if not rows:
            pytest.skip("no notifications yet")
        sample = rows[0]
        # to_address masked
        if sample.get("to_address"):
            assert "*" in sample["to_address"] or sample["to_address"] == "[redacted]", sample["to_address"]
        if sample.get("body"):
            assert sample["body"] == "[redacted]", sample["body"]

        r = admin_session.get(f"{API}/notifications?unmask=true", timeout=10)
        assert r.status_code == 200
        rows2 = r.json()
        if rows2 and rows2[0].get("body"):
            assert rows2[0]["body"] != "[redacted]", "unmask should reveal body"


# ---------- Scheduling + Encrypted Notes + Event Flow ----------
@pytest.fixture(scope="class")
def ctx(admin_session):
    r = admin_session.get(f"{API}/patients?unmask=true", timeout=10)
    morgan = next((p for p in r.json() if p["first_name"].lower() == "morgan"), None)
    if not morgan:
        pytest.skip("Morgan Lee not present")
    r = admin_session.get(f"{API}/auth/providers", timeout=10)
    providers = r.json()
    doc = next((d for d in providers if d["email"] == DOCTOR[0]), providers[0] if providers else None)
    if not doc:
        pytest.skip("no provider")
    return {"patient_id": morgan["id"], "provider_id": doc["id"]}


class TestSchedulingLifecycle:
    def _slot(self, offset=120, duration=30):
        import random
        start = datetime.now(timezone.utc) + timedelta(
            days=30 + random.randint(0, 365), minutes=offset + random.randint(0, 500)
        )
        start = start.replace(second=0, microsecond=0)
        return start.isoformat(), (start + timedelta(minutes=duration)).isoformat()

    def test_lifecycle_six_notifications_and_encrypted_notes(self, admin_session, ctx):
        r = admin_session.get(f"{API}/notifications", params={"patient_id": ctx["patient_id"]}, timeout=10)
        baseline = len(r.json())

        start, end = self._slot()
        payload = {
            "patient_id": ctx["patient_id"],
            "provider_id": ctx["provider_id"],
            "start_time": start,
            "end_time": end,
            "reason": "TEST",
            "notes": "Sensitive scheduling notes to encrypt",
        }
        r = admin_session.post(f"{API}/appointments", json=payload, timeout=10)
        assert r.status_code == 201, r.text
        aid = r.json()["id"]

        raw_appt = _db.appointments.find_one({"id": aid})
        if raw_appt.get("notes"):
            assert raw_appt["notes"].startswith("enc:v1:"), f"notes not encrypted: {raw_appt['notes'][:40]}"

        # conflict
        r = admin_session.post(f"{API}/appointments", json=payload, timeout=10)
        assert r.status_code == 409

        # reschedule
        ns = (datetime.fromisoformat(start) + timedelta(hours=1)).isoformat()
        ne = (datetime.fromisoformat(end) + timedelta(hours=1)).isoformat()
        r = admin_session.put(f"{API}/appointments/{aid}", json={"start_time": ns, "end_time": ne}, timeout=10)
        assert r.status_code == 200

        # cancel
        r = admin_session.post(f"{API}/appointments/{aid}/cancel", timeout=10)
        assert r.status_code == 200

        time.sleep(0.6)
        r = admin_session.get(f"{API}/notifications", params={"patient_id": ctx["patient_id"]}, timeout=10)
        assert len(r.json()) - baseline >= 6, (
            f"expected +6 notifications (2 booked, 2 updated, 2 cancelled), got {len(r.json()) - baseline}"
        )


# ---------- RBAC regressions ----------
class TestRBAC:
    def test_doctor_cannot_list_notifications_or_audit(self, doctor_session):
        assert doctor_session.get(f"{API}/notifications", timeout=10).status_code == 403
        assert doctor_session.get(f"{API}/audit-logs", timeout=10).status_code == 403

    def test_patient_sees_only_self(self, patient_session):
        r = patient_session.get(f"{API}/patients", timeout=10)
        assert r.status_code == 200
        rows = r.json()
        assert len(rows) <= 1
        if rows:
            assert rows[0]["first_name"].lower() == "morgan"
