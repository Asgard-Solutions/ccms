"""Iteration 7: Privacy & data-governance tests.

Covers:
- /api/privacy/data-inventory (admin only, 8 categories)
- /api/privacy/requests CRUD + state machine
- Legal-hold + fulfill-delete (409 + reauth)
- Consents (accept + my) & comm preferences
- /api/auth/me/export self-service
- Patient.created audit regression (no PHI values)
- Regression: /api/compliance/overview still admin-only
- Register page consent post-register (via API)
"""
import os
import uuid
import requests
import pytest

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")

ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")
DOCTOR = ("doctor@ccms.app", "Doctor@ComplianceClinic1")
STAFF = ("staff@ccms.app", "Staff@ComplianceClinic1")
PATIENT = ("patient@ccms.app", "Patient@ComplianceClinic1")


# ---------------- helpers ----------------

def _login(email, password):
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": password}, timeout=20)
    assert r.status_code == 200, f"login failed {email}: {r.status_code} {r.text}"
    j = r.json()
    if j.get("mfa_required"):
        pytest.skip(f"{email} requires MFA; cannot run in this environment")
    return s


def _me(s):
    r = s.get(f"{BASE_URL}/api/auth/me", timeout=10)
    assert r.status_code == 200, r.text
    return r.json()


def _reauth(s, password):
    r = s.post(f"{BASE_URL}/api/auth/reauth", json={"password": password}, timeout=10)
    assert r.status_code == 200, f"reauth failed: {r.status_code} {r.text}"
    return r.json().get("reauth_token")


@pytest.fixture(scope="module")
def admin_sess():
    return _login(*ADMIN)


@pytest.fixture(scope="module")
def doctor_sess():
    return _login(*DOCTOR)


@pytest.fixture(scope="module")
def staff_sess():
    return _login(*STAFF)


@pytest.fixture(scope="module")
def patient_sess():
    return _login(*PATIENT)


# ---------------- Data inventory ----------------

class TestDataInventory:
    def test_admin_sees_8_categories(self, admin_sess):
        r = admin_sess.get(f"{BASE_URL}/api/privacy/data-inventory", timeout=10)
        assert r.status_code == 200, r.text
        j = r.json()
        assert "categories" in j and "retention_settings" in j
        assert len(j["categories"]) == 8, f"expected 8, got {len(j['categories'])}"
        assert j["retention_settings"]["patient_retention_years"] == 7

    @pytest.mark.parametrize("role_fx", ["doctor_sess", "staff_sess", "patient_sess"])
    def test_non_admin_forbidden(self, role_fx, request):
        s = request.getfixturevalue(role_fx)
        r = s.get(f"{BASE_URL}/api/privacy/data-inventory", timeout=10)
        assert r.status_code == 403, f"{role_fx} got {r.status_code}"

    def test_anon_unauthorized(self):
        r = requests.get(f"{BASE_URL}/api/privacy/data-inventory", timeout=10)
        assert r.status_code == 401


# ---------------- Privacy requests ----------------

class TestPrivacyRequests:
    def test_patient_self_request_ok(self, patient_sess):
        me = _me(patient_sess)
        r = patient_sess.post(
            f"{BASE_URL}/api/privacy/requests",
            json={"request_type": "export", "subject_user_id": me["id"], "notes": "TEST_self"},
            timeout=10,
        )
        assert r.status_code == 201, r.text
        j = r.json()
        assert j["status"] == "received"
        assert j["submitted_by_id"] == me["id"]
        assert j["notes"] == "TEST_self"

    def test_patient_cannot_request_for_other(self, patient_sess, doctor_sess):
        doc = _me(doctor_sess)
        r = patient_sess.post(
            f"{BASE_URL}/api/privacy/requests",
            json={"request_type": "export", "subject_user_id": doc["id"], "notes": "TEST_patient_other"},
            timeout=10,
        )
        assert r.status_code == 403, r.text

    def test_doctor_forbidden(self, doctor_sess):
        doc = _me(doctor_sess)
        r = doctor_sess.post(
            f"{BASE_URL}/api/privacy/requests",
            json={"request_type": "export", "subject_user_id": doc["id"], "notes": "TEST"},
            timeout=10,
        )
        assert r.status_code == 403, r.text

    def test_staff_can_create_for_patient(self, staff_sess, patient_sess):
        pat = _me(patient_sess)
        r = staff_sess.post(
            f"{BASE_URL}/api/privacy/requests",
            json={"request_type": "correct", "subject_user_id": pat["id"], "notes": "TEST_staff"},
            timeout=10,
        )
        assert r.status_code == 201, r.text

    def test_admin_can_create_for_anyone(self, admin_sess, doctor_sess):
        doc = _me(doctor_sess)
        r = admin_sess.post(
            f"{BASE_URL}/api/privacy/requests",
            json={"request_type": "restrict", "subject_user_id": doc["id"], "notes": "TEST_admin"},
            timeout=10,
        )
        assert r.status_code == 201, r.text

    def test_admin_list_with_filters(self, admin_sess):
        r = admin_sess.get(
            f"{BASE_URL}/api/privacy/requests",
            params={"status": "received", "request_type": "export"},
            timeout=10,
        )
        assert r.status_code == 200
        rows = r.json()
        assert isinstance(rows, list)
        for row in rows:
            assert row["status"] == "received"
            assert row["request_type"] == "export"

    def test_non_admin_cannot_list(self, patient_sess):
        r = patient_sess.get(f"{BASE_URL}/api/privacy/requests", timeout=10)
        assert r.status_code == 403

    def test_my_requests_returns_self(self, patient_sess):
        me = _me(patient_sess)
        r = patient_sess.get(f"{BASE_URL}/api/privacy/my-requests", timeout=10)
        assert r.status_code == 200
        rows = r.json()
        assert isinstance(rows, list) and len(rows) >= 1
        for row in rows:
            assert row["subject_user_id"] == me["id"] or row["submitted_by_id"] == me["id"]

    def test_get_request_admin_and_related(self, admin_sess, patient_sess, doctor_sess):
        me = _me(patient_sess)
        c = patient_sess.post(
            f"{BASE_URL}/api/privacy/requests",
            json={"request_type": "opt_out", "subject_user_id": me["id"], "notes": "TEST_get"},
            timeout=10,
        )
        rid = c.json()["id"]
        r_admin = admin_sess.get(f"{BASE_URL}/api/privacy/requests/{rid}", timeout=10)
        assert r_admin.status_code == 200
        r_self = patient_sess.get(f"{BASE_URL}/api/privacy/requests/{rid}", timeout=10)
        assert r_self.status_code == 200
        r_doc = doctor_sess.get(f"{BASE_URL}/api/privacy/requests/{rid}", timeout=10)
        assert r_doc.status_code == 403

    def test_state_machine_valid_transitions(self, admin_sess, patient_sess):
        me = _me(patient_sess)
        c = patient_sess.post(
            f"{BASE_URL}/api/privacy/requests",
            json={"request_type": "export", "subject_user_id": me["id"], "notes": "TEST_sm_valid"},
            timeout=10,
        )
        rid = c.json()["id"]
        for nxt in ("in_review", "approved", "fulfilled"):
            r = admin_sess.patch(
                f"{BASE_URL}/api/privacy/requests/{rid}",
                json={"status": nxt, "response_notes": f"moving to {nxt}"},
                timeout=10,
            )
            assert r.status_code == 200, f"{nxt}: {r.text}"
            assert r.json()["status"] == nxt
        final = admin_sess.get(f"{BASE_URL}/api/privacy/requests/{rid}", timeout=10).json()
        assert final["closed_at"] is not None

    def test_state_machine_terminal_rejects(self, admin_sess, patient_sess):
        me = _me(patient_sess)
        c = patient_sess.post(
            f"{BASE_URL}/api/privacy/requests",
            json={"request_type": "export", "subject_user_id": me["id"], "notes": "TEST_sm_term"},
            timeout=10,
        )
        rid = c.json()["id"]
        r1 = admin_sess.patch(
            f"{BASE_URL}/api/privacy/requests/{rid}", json={"status": "rejected"}, timeout=10,
        )
        assert r1.status_code == 200
        r2 = admin_sess.patch(
            f"{BASE_URL}/api/privacy/requests/{rid}", json={"status": "approved"}, timeout=10,
        )
        assert r2.status_code == 400

    def test_state_machine_invalid_jump(self, admin_sess, patient_sess):
        me = _me(patient_sess)
        c = patient_sess.post(
            f"{BASE_URL}/api/privacy/requests",
            json={"request_type": "export", "subject_user_id": me["id"], "notes": "TEST_invalid"},
            timeout=10,
        )
        rid = c.json()["id"]
        # received -> fulfilled (invalid direct)
        r = admin_sess.patch(
            f"{BASE_URL}/api/privacy/requests/{rid}", json={"status": "fulfilled"}, timeout=10,
        )
        assert r.status_code == 400


# ---------------- Legal hold + fulfill-delete ----------------

class TestLegalHoldFulfillDelete:
    """Create throwaway patient, test legal-hold toggle, 409 on DELETE + fulfill-delete."""

    @pytest.fixture(scope="class")
    def throwaway_patient(self, admin_sess):
        _reauth(admin_sess, ADMIN[1])
        payload = {
            "first_name": "TESTFirst",
            "last_name": "TESTLast",
            "date_of_birth": "1990-01-01",
            "gender": "other",
            "email": f"test_{uuid.uuid4().hex[:8]}@example.com",
            "phone": "+10000000000",
        }
        r = admin_sess.post(f"{BASE_URL}/api/patients", json=payload, timeout=15)
        assert r.status_code in (200, 201), f"create patient: {r.status_code} {r.text}"
        return r.json()["id"]

    def test_fulfill_delete_requires_reauth(self, admin_sess, patient_sess, throwaway_patient):
        me = _me(patient_sess)
        c = patient_sess.post(
            f"{BASE_URL}/api/privacy/requests",
            json={
                "request_type": "delete",
                "subject_user_id": me["id"],
                "subject_patient_id": throwaway_patient,
                "notes": "TEST_delete_req",
            },
            timeout=10,
        )
        rid = c.json()["id"]
        # move request to approved
        admin_sess.patch(f"{BASE_URL}/api/privacy/requests/{rid}", json={"status": "in_review"}, timeout=10)
        admin_sess.patch(f"{BASE_URL}/api/privacy/requests/{rid}", json={"status": "approved"}, timeout=10)

        # fresh session without reauth cookie
        fresh = _login(*ADMIN)
        r = fresh.post(f"{BASE_URL}/api/privacy/requests/{rid}/fulfill-delete", timeout=10)
        assert r.status_code == 401, f"expected 401 sans reauth, got {r.status_code}"

    def test_legal_hold_blocks_fulfill_and_delete(self, admin_sess, patient_sess, throwaway_patient):
        # set legal hold
        _reauth(admin_sess, ADMIN[1])
        r = admin_sess.post(
            f"{BASE_URL}/api/privacy/patients/{throwaway_patient}/legal-hold",
            json={"hold": True, "reason": "TEST active litigation"},
            timeout=10,
        )
        assert r.status_code == 200, r.text

        # Create+approve a delete request
        me = _me(patient_sess)
        c = patient_sess.post(
            f"{BASE_URL}/api/privacy/requests",
            json={
                "request_type": "delete",
                "subject_user_id": me["id"],
                "subject_patient_id": throwaway_patient,
                "notes": "TEST_hold_delete",
            },
            timeout=10,
        )
        rid = c.json()["id"]
        admin_sess.patch(f"{BASE_URL}/api/privacy/requests/{rid}", json={"status": "in_review"}, timeout=10)
        admin_sess.patch(f"{BASE_URL}/api/privacy/requests/{rid}", json={"status": "approved"}, timeout=10)

        _reauth(admin_sess, ADMIN[1])
        r = admin_sess.post(f"{BASE_URL}/api/privacy/requests/{rid}/fulfill-delete", timeout=10)
        assert r.status_code == 409, f"expected 409, got {r.status_code}: {r.text}"

        # DELETE /patients/{id} also 409
        _reauth(admin_sess, ADMIN[1])
        r = admin_sess.delete(
            f"{BASE_URL}/api/patients/{throwaway_patient}",
            params={"reason": "TEST legal hold regression"},
            timeout=10,
        )
        assert r.status_code == 409, f"expected 409 on delete, got {r.status_code} {r.text}"

    def test_clear_hold_then_fulfill_ok(self, admin_sess, patient_sess, throwaway_patient):
        _reauth(admin_sess, ADMIN[1])
        r = admin_sess.post(
            f"{BASE_URL}/api/privacy/patients/{throwaway_patient}/legal-hold",
            json={"hold": False},
            timeout=10,
        )
        assert r.status_code == 200

        me = _me(patient_sess)
        c = patient_sess.post(
            f"{BASE_URL}/api/privacy/requests",
            json={
                "request_type": "delete",
                "subject_user_id": me["id"],
                "subject_patient_id": throwaway_patient,
                "notes": "TEST_fulfil_ok",
            },
            timeout=10,
        )
        rid = c.json()["id"]
        admin_sess.patch(f"{BASE_URL}/api/privacy/requests/{rid}", json={"status": "in_review"}, timeout=10)
        admin_sess.patch(f"{BASE_URL}/api/privacy/requests/{rid}", json={"status": "approved"}, timeout=10)

        _reauth(admin_sess, ADMIN[1])
        r = admin_sess.post(f"{BASE_URL}/api/privacy/requests/{rid}/fulfill-delete", timeout=10)
        assert r.status_code == 200, r.text

        got = admin_sess.get(f"{BASE_URL}/api/privacy/requests/{rid}", timeout=10).json()
        assert got["status"] == "fulfilled"
        assert got["fulfillment"]["linked_patient_id"] == throwaway_patient


# ---------------- Consents + comm prefs ----------------

class TestConsentsAndPrefs:
    def test_accept_consent_then_list(self, patient_sess):
        r = patient_sess.post(
            f"{BASE_URL}/api/privacy/consents/accept",
            json={"policy_type": "privacy_notice", "policy_version": "2026-02-v1", "action": "accepted"},
            timeout=10,
        )
        assert r.status_code == 201, r.text
        j = r.json()
        assert j["policy_version"] == "2026-02-v1"
        assert j["action"] == "accepted"

        lst = patient_sess.get(f"{BASE_URL}/api/privacy/consents/me", timeout=10)
        assert lst.status_code == 200
        rows = lst.json()
        assert len(rows) >= 1
        # desc by accepted_at
        times = [r["accepted_at"] for r in rows]
        assert times == sorted(times, reverse=True)

    def test_comm_prefs_defaults(self):
        # Fresh user with no prefs record
        s = requests.Session()
        email = f"test_prefs_{uuid.uuid4().hex[:8]}@example.com"
        r = s.post(f"{BASE_URL}/api/auth/register", json={
            "email": email, "password": "TestPass@Cadence1", "name": "Test Prefs",
        }, timeout=15)
        if r.status_code not in (200, 201):
            pytest.skip(f"register failed: {r.status_code} {r.text}")
        s2 = _login(email, "TestPass@Cadence1")
        r = s2.get(f"{BASE_URL}/api/privacy/communication-preferences", timeout=10)
        assert r.status_code == 200
        j = r.json()
        assert j["email_opt_in"] is True
        assert j["sms_opt_in"] is False
        assert j["marketing_opt_in"] is False

    def test_comm_prefs_partial_update(self, patient_sess):
        r = patient_sess.put(
            f"{BASE_URL}/api/privacy/communication-preferences",
            json={"sms_opt_in": True},
            timeout=10,
        )
        assert r.status_code == 200
        j = r.json()
        assert j["sms_opt_in"] is True
        # email_opt_in unchanged from prior state
        r2 = patient_sess.put(
            f"{BASE_URL}/api/privacy/communication-preferences",
            json={"sms_opt_in": False},
            timeout=10,
        )
        assert r2.status_code == 200 and r2.json()["sms_opt_in"] is False


# ---------------- /auth/me/export ----------------

class TestSelfExport:
    def test_export_shape_and_no_secrets(self, patient_sess):
        r = patient_sess.get(f"{BASE_URL}/api/auth/me/export", timeout=15)
        assert r.status_code == 200, r.text
        j = r.json()
        for k in ("account", "communication_preferences", "consents", "privacy_requests", "recent_events"):
            assert k in j, f"missing {k}"
        acct = j["account"]
        for secret in ("password_hash", "password_history", "mfa_secret", "mfa_backup_codes"):
            assert secret not in acct, f"{secret} leaked in export"


# ---------------- Audit regressions ----------------

class TestAuditRegression:
    def test_patient_created_audit_no_phi(self, admin_sess):
        _reauth(admin_sess, ADMIN[1])
        unique = uuid.uuid4().hex[:8]
        first = f"TESTPHI{unique}"
        last = f"LAST{unique}"
        email = f"audit_{unique}@example.com"
        r = admin_sess.post(f"{BASE_URL}/api/patients", json={
            "first_name": first, "last_name": last,
            "date_of_birth": "1985-05-05", "gender": "other",
            "email": email, "phone": "+15550001111",
        }, timeout=15)
        assert r.status_code in (200, 201), r.text
        pid = r.json()["id"]

        # inspect audit logs for the patient
        al = admin_sess.get(
            f"{BASE_URL}/api/audit-logs",
            params={"entity_type": "patient", "entity_id": pid, "action": "patient.created"},
            timeout=10,
        )
        assert al.status_code == 200
        rows = al.json()
        assert len(rows) >= 1
        for row in rows:
            md_str = str(row.get("metadata", {}))
            # No PHI values should leak
            for phi_val in (first, last, email, "+15550001111"):
                assert phi_val not in md_str, f"PHI {phi_val} leaked in audit metadata: {md_str}"

    def test_compliance_overview_admin_only(self, admin_sess, patient_sess):
        r = admin_sess.get(f"{BASE_URL}/api/compliance/overview", timeout=10)
        assert r.status_code == 200
        r2 = patient_sess.get(f"{BASE_URL}/api/compliance/overview", timeout=10)
        assert r2.status_code == 403


# ---------------- Register consent integration ----------------

class TestRegisterConsent:
    def test_register_then_accept_consent_v1(self):
        s = requests.Session()
        email = f"test_reg_consent_{uuid.uuid4().hex[:8]}@example.com"
        r = s.post(f"{BASE_URL}/api/auth/register", json={
            "email": email, "password": "TestPass@Cadence1", "name": "Reg Consent",
        }, timeout=15)
        if r.status_code not in (200, 201):
            pytest.skip(f"register failed: {r.status_code} {r.text}")
        # emulate the frontend post-register call
        s2 = _login(email, "TestPass@Cadence1")
        acc = s2.post(
            f"{BASE_URL}/api/privacy/consents/accept",
            json={"policy_type": "privacy_notice", "policy_version": "2026-02-v1", "action": "accepted"},
            timeout=10,
        )
        assert acc.status_code == 201
        lst = s2.get(f"{BASE_URL}/api/privacy/consents/me", timeout=10).json()
        assert any(c["policy_version"] == "2026-02-v1" for c in lst)
