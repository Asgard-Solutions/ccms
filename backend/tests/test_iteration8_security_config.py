"""
Iteration 8 tests — Data protection & secure-config hardening.

Covers:
  - GET /api/compliance/security-config RBAC (admin 200, others 403, anon 401)
  - payload shape: app_env, required_config, recommended_config, secret_strength,
    encryption block, features, production_gaps, patient_encrypted_fields
  - secret_strength masking: never leaks full secret, format 'abcd…(N)'
  - DOB field-level encryption at rest (POST encrypts, DB has enc:v1:, GET unmask returns plaintext,
    list masks DOB, PUT re-encrypts, export returns plaintext)
  - /api/compliance/overview regression (admin 200, others 403, anon 401)
  - ensure_required() fail-fast semantics via subprocess import
  - Legacy plaintext DOB roundtrip via decrypt_text pass-through (core.crypto)
"""
import os
import re
import subprocess
import sys

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://clinic-phase7-ui.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")
DOCTOR = ("doctor@ccms.app", "Doctor@ComplianceClinic1")
STAFF = ("staff@ccms.app", "Staff@ComplianceClinic1")
PATIENT = ("patient@ccms.app", "Patient@ComplianceClinic1")


def _login(email, password):
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, f"login failed for {email}: {r.status_code} {r.text}"
    body = r.json()
    # Seeded users have MFA disabled → no mfa_required branch needed
    assert body.get("mfa_required") in (False, None), f"unexpected MFA prompt for {email}"
    return s


@pytest.fixture(scope="module")
def admin_session():
    return _login(*ADMIN)


@pytest.fixture(scope="module")
def doctor_session():
    return _login(*DOCTOR)


@pytest.fixture(scope="module")
def staff_session():
    return _login(*STAFF)


@pytest.fixture(scope="module")
def patient_session():
    return _login(*PATIENT)


# ---------- /api/compliance/security-config ----------

class TestSecurityConfigRBAC:
    def test_anon_is_401(self):
        r = requests.get(f"{API}/compliance/security-config", timeout=10)
        assert r.status_code == 401, r.text

    def test_patient_is_403(self, patient_session):
        r = patient_session.get(f"{API}/compliance/security-config", timeout=10)
        assert r.status_code == 403

    def test_doctor_is_403(self, doctor_session):
        r = doctor_session.get(f"{API}/compliance/security-config", timeout=10)
        assert r.status_code == 403

    def test_staff_is_403(self, staff_session):
        r = staff_session.get(f"{API}/compliance/security-config", timeout=10)
        assert r.status_code == 403

    def test_admin_200_and_payload_shape(self, admin_session):
        r = admin_session.get(f"{API}/compliance/security-config", timeout=10)
        assert r.status_code == 200, r.text
        d = r.json()
        # top-level
        for k in ("app_env", "production_ready", "required_config",
                  "recommended_config", "secret_strength", "encryption",
                  "features", "production_gaps"):
            assert k in d, f"missing key {k}"
        # required booleans all True for this pod
        for k in ("MONGO_URL", "DB_NAME", "JWT_SECRET", "DATA_ENCRYPTION_KEY"):
            assert d["required_config"].get(k) is True, f"required {k} should be True"
        # secret strength
        ss = d["secret_strength"]
        assert ss["jwt_secret_length"] >= 32
        assert ss["data_encryption_key_length"] >= 32
        # mask format: 'abcd…(NN)'
        assert re.match(r"^.{4}…\(\d+\)$", ss["jwt_secret_masked"]), ss["jwt_secret_masked"]
        assert re.match(r"^.{4}…\(\d+\)$", ss["data_encryption_key_masked"]), ss["data_encryption_key_masked"]
        # encryption block
        enc = d["encryption"]
        assert enc["provider"] == "env"
        assert enc["active_version"] == "v1"
        assert enc["enabled"] is True
        assert "date_of_birth" in enc["patient_encrypted_fields"]
        # features block present (retention worker key)
        assert "retention_worker_running" in d["features"]
        # production_gaps non-empty since KMS_PROVIDER not set
        assert isinstance(d["production_gaps"], list)
        assert any("KMS_PROVIDER" in g for g in d["production_gaps"]), d["production_gaps"]

    def test_no_full_secrets_in_response(self, admin_session):
        """The response MUST NOT contain the raw JWT_SECRET / DATA_ENCRYPTION_KEY."""
        r = admin_session.get(f"{API}/compliance/security-config", timeout=10)
        body_text = r.text
        jwt_secret = os.environ.get("JWT_SECRET")
        dek = os.environ.get("DATA_ENCRYPTION_KEY")
        # We may not have env loaded here (tests run outside backend).  If so,
        # read from backend/.env.
        if not jwt_secret or not dek:
            try:
                with open("/app/backend/.env") as f:
                    env_txt = f.read()
                if not jwt_secret:
                    m = re.search(r'JWT_SECRET="?([^"\n]+)', env_txt)
                    jwt_secret = m.group(1) if m else None
                if not dek:
                    m = re.search(r'DATA_ENCRYPTION_KEY="?([^"\n]+)', env_txt)
                    dek = m.group(1) if m else None
            except Exception:
                pass
        if jwt_secret:
            assert jwt_secret not in body_text, "raw JWT_SECRET leaked in security-config"
        if dek:
            assert dek not in body_text, "raw DATA_ENCRYPTION_KEY leaked in security-config"


# ---------- /api/compliance/overview regression ----------

class TestComplianceOverviewRegression:
    def test_anon_401(self):
        r = requests.get(f"{API}/compliance/overview", timeout=10)
        assert r.status_code == 401

    def test_patient_403(self, patient_session):
        r = patient_session.get(f"{API}/compliance/overview", timeout=10)
        assert r.status_code == 403

    def test_doctor_403(self, doctor_session):
        r = doctor_session.get(f"{API}/compliance/overview", timeout=10)
        assert r.status_code == 403

    def test_admin_200(self, admin_session):
        r = admin_session.get(f"{API}/compliance/overview", timeout=10)
        assert r.status_code == 200
        d = r.json()
        assert "environment" in d and "data_encryption_key_configured" in d["environment"]
        assert d["environment"]["data_encryption_key_configured"] is True


# ---------- DOB field-level encryption ----------

class TestDOBEncryption:
    """Verify date_of_birth is encrypted at rest + decrypts correctly."""

    def _create_patient(self, admin_session, dob="1985-06-15"):
        payload = {
            "first_name": "TEST_Iter8",
            "last_name": "DOBEncrypt",
            "email": f"test_iter8_{os.urandom(3).hex()}@example.com",
            "phone": "555-0100",
            "date_of_birth": dob,
            "address": "1 Secret Street",
            "emergency_contact": "None",
            "notes": "created by iteration 8 test",
        }
        r = admin_session.post(f"{API}/patients", json=payload, timeout=15)
        assert r.status_code == 201, r.text
        return r.json()

    def test_create_unmask_get_and_dob_roundtrip(self, admin_session):
        p = self._create_patient(admin_session, dob="1985-06-15")
        pid = p["id"]
        # admin create response returns unmasked -> dob == plaintext
        assert p.get("date_of_birth") == "1985-06-15"

        # GET with unmask=true -> plaintext
        r = admin_session.get(f"{API}/patients/{pid}?unmask=true", timeout=10)
        assert r.status_code == 200
        assert r.json().get("date_of_birth") == "1985-06-15"

        # GET default (masked) -> partially masked DOB e.g. 1985-**-**
        r2 = admin_session.get(f"{API}/patients/{pid}", timeout=10)
        assert r2.status_code == 200
        dob_masked = r2.json().get("date_of_birth")
        assert dob_masked and dob_masked != "1985-06-15", f"masked dob leaked: {dob_masked}"

        # List (default masked) includes this patient with masked DOB
        rl = admin_session.get(f"{API}/patients", timeout=15)
        assert rl.status_code == 200
        match = [x for x in rl.json() if x["id"] == pid]
        assert match, "created patient missing from list"
        assert match[0]["date_of_birth"] != "1985-06-15"

        # Export returns plaintext DOB
        rexp = admin_session.get(f"{API}/patients/{pid}/export", timeout=15)
        assert rexp.status_code == 200
        assert rexp.json()["patient"]["date_of_birth"] == "1985-06-15"

        # PUT update with a new DOB, re-encrypts + reads back correctly
        rp = admin_session.patch(
            f"{API}/patients/{pid}",
            json={"date_of_birth": "1990-01-02"},
            timeout=10,
        )
        assert rp.status_code == 200, rp.text
        assert rp.json().get("date_of_birth") == "1990-01-02"

        rexp2 = admin_session.get(f"{API}/patients/{pid}/export", timeout=10)
        assert rexp2.json()["patient"]["date_of_birth"] == "1990-01-02"

    def test_raw_db_dob_is_ciphertext(self, admin_session):
        """Direct Mongo read: the stored DOB must start with 'enc:v1:' ."""
        p = self._create_patient(admin_session, dob="1972-11-30")
        pid = p["id"]

        # Use a Python subprocess with backend env loaded
        script = f"""
import asyncio, sys
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from core.db import get_db_read
async def main():
    db = get_db_read()
    doc = await db.patients.find_one({{"id": "{pid}"}}, {{"_id": 0, "date_of_birth": 1}})
    print(doc.get("date_of_birth") if doc else "NONE")
asyncio.get_event_loop().run_until_complete(main())
"""
        proc = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, timeout=20
        )
        out = (proc.stdout or "").strip()
        assert out.startswith("enc:v1:"), f"raw DOB not ciphertext: {out!r} stderr={proc.stderr}"


# ---------- Legacy plaintext pass-through ----------

class TestLegacyPlaintextPassthrough:
    def test_decrypt_text_passes_plaintext_through(self):
        script = """
import sys
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from core.crypto import decrypt_text, encrypt_text
assert decrypt_text("1985-06-15") == "1985-06-15"
assert decrypt_text(None) is None
assert decrypt_text("") == ""
ct = encrypt_text("1972-11-30")
assert ct.startswith("enc:v1:")
assert decrypt_text(ct) == "1972-11-30"
print("OK")
"""
        proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=20)
        assert proc.stdout.strip().endswith("OK"), f"stdout={proc.stdout} stderr={proc.stderr}"


# ---------- ensure_required() fail-fast ----------

class TestEnsureRequiredFailFast:
    def test_missing_dek_raises(self):
        """Simulate missing DATA_ENCRYPTION_KEY — ensure_required() must raise."""
        script = """
import os, sys
# Start with a clean env — load .env first, then strip one required.
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
os.environ.pop("DATA_ENCRYPTION_KEY", None)
from core.config import ensure_required
try:
    ensure_required()
    print("NO_RAISE")
except RuntimeError as e:
    print("RAISED:" + str(e))
"""
        proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=20)
        out = (proc.stdout or "").strip()
        assert out.startswith("RAISED:"), f"ensure_required() did not fail-fast: {out!r} stderr={proc.stderr}"
        assert "DATA_ENCRYPTION_KEY" in out

    def test_all_present_does_not_raise(self):
        script = """
import sys
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from core.config import ensure_required, validate_required
print("MISSING:" + ",".join(validate_required()))
ensure_required()
print("OK")
"""
        proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=20)
        out = proc.stdout or ""
        assert "OK" in out, out + proc.stderr
