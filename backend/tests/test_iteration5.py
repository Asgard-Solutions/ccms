"""
CCMS Iteration 5 — Security Hardening tests.

Covers: session epoch revocation, password reset, admin MFA reset/require,
/auth/sessions, audit date-range + actor filters + CSV export, and PHI leak
spot-checks. Does NOT mutate seed creds — all mutations happen on TEST_* users.
"""
import csv
import io
import os
import time
import uuid
from datetime import datetime, timezone

import pymongo
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
STRONG_PW = "StrongNewPass@Clinic9!"


def _login(email, pw):
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": pw}, timeout=15)
    assert r.status_code == 200, f"login {email}: {r.status_code} {r.text}"
    return s, r.json()


@pytest.fixture(scope="module")
def admin_session():
    s, _ = _login(*ADMIN)
    return s


@pytest.fixture
def throwaway_user(admin_session):
    """Create a fresh temp user via admin for epoch/disable/reset tests."""
    email = f"TEST_it5_{uuid.uuid4().hex[:8]}@ccms.app"
    pw = "TempPass@Clinic1!"
    r = admin_session.post(
        f"{API}/auth/users",
        json={"email": email, "password": pw, "name": "TestIt5", "role": "staff"},
        timeout=15,
    )
    assert r.status_code == 201, r.text
    return {"id": r.json()["id"], "email": email, "password": pw}


# ---------- Login shape ----------
class TestLoginShape:
    def test_login_returns_new_fields_all_roles(self):
        for creds in [ADMIN, DOCTOR, STAFF, PATIENT]:
            s, body = _login(*creds)
            # mfa_required may gate MFA users; either way user object (when
            # present) must include mfa_policy_required
            if body.get("user") is not None:
                assert "mfa_policy_required" in body["user"], body["user"]


# ---------- Session epoch revocation ----------
class TestSessionEpoch:
    def test_disable_revokes_sessions(self, admin_session, throwaway_user):
        # User logs in, captures cookie
        u_sess, _ = _login(throwaway_user["email"], throwaway_user["password"])
        r = u_sess.get(f"{API}/auth/me", timeout=10)
        assert r.status_code == 200

        # Admin disables → epoch bumped → old cookie 401
        r = admin_session.post(f"{API}/auth/users/{throwaway_user['id']}/disable", timeout=10)
        assert r.status_code == 200
        r = u_sess.get(f"{API}/auth/me", timeout=10)
        assert r.status_code in (401, 403), f"old cookie must be rejected, got {r.status_code}"

        # Admin re-enables → SAME old cookie still rejected (epoch >= disable epoch)
        r = admin_session.post(f"{API}/auth/users/{throwaway_user['id']}/enable", timeout=10)
        assert r.status_code == 200
        r = u_sess.get(f"{API}/auth/me", timeout=10)
        assert r.status_code in (401, 403), "old cookie must remain invalid after re-enable"

        # Fresh login after enable must work
        u_sess2, _ = _login(throwaway_user["email"], throwaway_user["password"])
        assert u_sess2.get(f"{API}/auth/me", timeout=10).status_code == 200

    def test_patch_role_bumps_target_epoch_not_admin(self, admin_session, throwaway_user):
        u_sess, _ = _login(throwaway_user["email"], throwaway_user["password"])
        # Admin patches target's role
        r = admin_session.patch(
            f"{API}/auth/users/{throwaway_user['id']}", json={"role": "doctor"}, timeout=10
        )
        assert r.status_code == 200, r.text
        # Target's old cookie dies
        assert u_sess.get(f"{API}/auth/me", timeout=10).status_code == 401
        # Admin's own cookie stays alive
        assert admin_session.get(f"{API}/auth/me", timeout=10).status_code == 200

    def test_change_password_keeps_current_session_kills_old_token(self, throwaway_user):
        u_sess, _ = _login(throwaway_user["email"], throwaway_user["password"])
        old_access = u_sess.cookies.get("access_token")
        assert old_access

        r = u_sess.post(
            f"{API}/auth/change-password",
            json={"current_password": throwaway_user["password"], "new_password": STRONG_PW},
            timeout=10,
        )
        assert r.status_code == 200, r.text
        # Current session still works (cookies refreshed)
        assert u_sess.get(f"{API}/auth/me", timeout=10).status_code == 200
        # Old captured access token rejected
        r2 = requests.get(f"{API}/auth/me", cookies={"access_token": old_access}, timeout=10)
        assert r2.status_code == 401, f"old access token must die, got {r2.status_code}"

    def test_refresh_dies_after_epoch_bump(self, admin_session, throwaway_user):
        u_sess, _ = _login(throwaway_user["email"], throwaway_user["password"])
        # Refresh works pre-bump
        r = u_sess.post(f"{API}/auth/refresh", timeout=10)
        assert r.status_code == 200, r.text
        # Admin disables → refresh token invalid
        admin_session.post(f"{API}/auth/users/{throwaway_user['id']}/disable", timeout=10)
        r = u_sess.post(f"{API}/auth/refresh", timeout=10)
        assert r.status_code == 401, f"refresh must die after disable, got {r.status_code}"


# ---------- Password reset ----------
class TestPasswordReset:
    def test_unknown_email_still_200_no_enum(self):
        r = requests.post(
            f"{API}/auth/password-reset/request",
            json={"email": f"nobody_{uuid.uuid4().hex}@example.com"}, timeout=10,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("dev_token") in (None, ""), "dev_token must be None for unknown email"
        assert "message" in body

    def test_known_email_returns_dev_token_and_confirm_flow(self, throwaway_user):
        # Request
        r = requests.post(
            f"{API}/auth/password-reset/request",
            json={"email": throwaway_user["email"]}, timeout=10,
        )
        assert r.status_code == 200, r.text
        token = r.json().get("dev_token")
        assert token, "dev_token required for known email"

        # Weak password -> 400
        r = requests.post(
            f"{API}/auth/password-reset/confirm",
            json={"token": token, "new_password": "weak"}, timeout=10,
        )
        assert r.status_code in (400, 422), r.text

        # Strong password -> 200
        new_pw = f"Reset@Clinic{uuid.uuid4().hex[:6]}!"
        r = requests.post(
            f"{API}/auth/password-reset/confirm",
            json={"token": token, "new_password": new_pw}, timeout=10,
        )
        assert r.status_code == 200, r.text

        # Single-use: second use fails
        r = requests.post(
            f"{API}/auth/password-reset/confirm",
            json={"token": token, "new_password": "AnotherStrong@Clinic9!"}, timeout=10,
        )
        assert r.status_code == 400, f"token must be single-use, got {r.status_code}"

        # Login with new pw works
        s, _ = _login(throwaway_user["email"], new_pw)
        assert s.get(f"{API}/auth/me", timeout=10).status_code == 200

    def test_confirm_rejects_reused_password_history(self, throwaway_user):
        # First reset to pw A
        r = requests.post(
            f"{API}/auth/password-reset/request", json={"email": throwaway_user["email"]}, timeout=10,
        )
        tok1 = r.json()["dev_token"]
        pw_a = throwaway_user["password"]  # original -- should be in history
        r = requests.post(
            f"{API}/auth/password-reset/confirm",
            json={"token": tok1, "new_password": pw_a}, timeout=10,
        )
        # Should reject reuse since pw_a was the initial password (in history)
        assert r.status_code == 400, f"history reuse should 400, got {r.status_code} {r.text}"


# ---------- Admin MFA controls ----------
class TestAdminMFA:
    def test_admin_mfa_reset_rbac(self, admin_session, throwaway_user):
        # doctor forbidden
        doc_s, _ = _login(*DOCTOR)
        r = doc_s.post(f"{API}/auth/users/{throwaway_user['id']}/mfa/reset", timeout=10)
        assert r.status_code == 403

        # admin OK (user has no mfa → still succeeds and bumps epoch)
        r = admin_session.post(f"{API}/auth/users/{throwaway_user['id']}/mfa/reset", timeout=10)
        assert r.status_code == 200, r.text

    def test_admin_mfa_require_toggle(self, admin_session, throwaway_user):
        r = admin_session.post(
            f"{API}/auth/users/{throwaway_user['id']}/mfa/require",
            params={"required": "true"}, timeout=10,
        )
        assert r.status_code == 200, r.text
        user = _db.users.find_one({"id": throwaway_user["id"]})
        assert user.get("mfa_policy_required") is True

        # doctor forbidden
        doc_s, _ = _login(*DOCTOR)
        r = doc_s.post(
            f"{API}/auth/users/{throwaway_user['id']}/mfa/require",
            params={"required": "false"}, timeout=10,
        )
        assert r.status_code == 403


# ---------- /auth/sessions ----------
class TestSessions:
    def test_sessions_returns_current_and_events(self, admin_session):
        r = admin_session.get(f"{API}/auth/sessions", timeout=10)
        assert r.status_code == 200, r.text
        body = r.json()
        assert "current_session" in body
        assert "events" in body and isinstance(body["events"], list)
        # At least admin's own login should be there
        assert len(body["events"]) > 0


# ---------- Audit filters + CSV export ----------
class TestAuditFilters:
    def test_date_from_to_and_actor_email_filters(self, admin_session):
        today = datetime.now(timezone.utc).date().isoformat()
        r = admin_session.get(
            f"{API}/audit-logs",
            params={"date_from": today, "actor_email": "admin@ccms.app", "limit": 20},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        rows = r.json()
        assert isinstance(rows, list)
        for row in rows:
            if row.get("actor_email"):
                assert "admin@ccms.app" in row["actor_email"].lower()

    def test_csv_export_admin_only_and_shape(self, admin_session):
        r = admin_session.get(
            f"{API}/audit-logs/export.csv",
            params={"limit": 50}, timeout=30, stream=True,
        )
        assert r.status_code == 200, r.text
        assert "text/csv" in r.headers.get("content-type", "")
        content = r.content.decode("utf-8", errors="replace")
        reader = csv.reader(io.StringIO(content))
        rows = list(reader)
        assert len(rows) >= 1, "no header row"
        header = rows[0]
        for col in ["created_at", "action", "outcome", "actor_email", "metadata"]:
            assert col in header, f"missing column {col}: {header}"

    def test_csv_export_writes_exported_meta_row(self, admin_session):
        admin_session.get(f"{API}/audit-logs/export.csv", params={"limit": 5}, timeout=30).content
        time.sleep(0.5)
        r = admin_session.get(
            f"{API}/audit-logs", params={"action": "audit_log.exported", "limit": 5}, timeout=10,
        )
        assert r.status_code == 200
        assert any(row.get("action") == "audit_log.exported" for row in r.json()), \
            "audit_log.exported meta-row must be written"

    def test_csv_export_forbidden_for_non_admin(self):
        for creds in [DOCTOR, STAFF, PATIENT]:
            s, _ = _login(*creds)
            r = s.get(f"{API}/audit-logs/export.csv", timeout=10)
            assert r.status_code == 403, f"{creds[0]} got {r.status_code}"


# ---------- PHI leak spot-check ----------
class TestNoPhiInAudit:
    def test_patient_create_audit_metadata_has_no_phi(self, admin_session):
        first = f"TEST_phi_{uuid.uuid4().hex[:6]}"
        r = admin_session.post(
            f"{API}/patients",
            json={
                "first_name": first, "last_name": "Leak",
                "email": f"TEST_phi_{uuid.uuid4().hex[:6]}@x.com",
                "phone": "555-0199", "date_of_birth": "1990-01-01", "gender": "other",
                "address": "12 PHI Street", "emergency_contact": "Emergency Bob 555-1",
            }, timeout=10,
        )
        assert r.status_code == 201, r.text
        pid = r.json()["id"]
        time.sleep(0.3)
        # Find the matching audit row
        rows = list(_db.audit_logs.find(
            {"entity_type": "patient", "entity_id": pid, "action": {"$regex": "^patient"}}
        ).limit(5))
        assert rows, "no patient audit rows found"
        banned = [first, "12 PHI Street", "Emergency Bob", "555-0199"]
        for row in rows:
            meta = row.get("metadata") or {}
            dump = str(meta)
            for needle in banned:
                assert needle not in dump, f"PHI {needle!r} leaked into metadata: {meta}"


# ---------- Regression — prior HIPAA + compliance flows ----------
class TestRegressions:
    def test_compliance_overview_rbac(self, admin_session):
        assert admin_session.get(f"{API}/compliance/overview", timeout=10).status_code == 200
        doc_s, _ = _login(*DOCTOR)
        assert doc_s.get(f"{API}/compliance/overview", timeout=10).status_code == 403
        assert requests.get(f"{API}/compliance/overview", timeout=10).status_code == 401

    def test_patients_list_masked_default(self, admin_session):
        rows = admin_session.get(f"{API}/patients", timeout=10).json()
        assert rows
        # at least one masked field
        assert any("*" in (p.get("email") or "") or "*" in (p.get("phone") or "")
                   or p.get("address") == "[redacted]" for p in rows)

    def test_audit_logs_still_admin_only(self):
        for creds in [DOCTOR, STAFF, PATIENT]:
            s, _ = _login(*creds)
            assert s.get(f"{API}/audit-logs", timeout=10).status_code == 403

    def test_break_glass_reason_required_for_doctor(self, admin_session):
        pid = admin_session.get(f"{API}/patients", timeout=10).json()[0]["id"]
        doc_s, _ = _login(*DOCTOR)
        assert doc_s.get(f"{API}/patients/{pid}", timeout=10).status_code == 400
        assert doc_s.get(
            f"{API}/patients/{pid}?reason=Covering clinician", timeout=10,
        ).status_code == 200
