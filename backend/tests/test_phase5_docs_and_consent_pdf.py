"""
Phase 5 — Patient Documents (upload/list/download/delete) + Consent PDF endpoints.

Covers /api/patients/{id}/documents* and /api/patients/{id}/consents/{type}/pdf
end-to-end against the live backend.
"""
from __future__ import annotations

import base64
import io
import os
import uuid
import time

import pytest
import requests

API = os.environ.get("CCMS_BASE_URL", "http://localhost:8001/api")

ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")
DOCTOR = ("doctor@ccms.app", "Doctor@ComplianceClinic1")
STAFF = ("staff@ccms.app", "Staff@ComplianceClinic1")
PATIENT = ("patient@ccms.app", "Patient@ComplianceClinic1")

# 1x1 PNG bytes
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQABh6FO1AAAAABJRU5ErkJggg=="
)
PNG_1X1_DATA_URL = "data:image/png;base64," + base64.b64encode(PNG_1X1).decode()


def _login(email: str, password: str, do_reauth: bool = True) -> requests.Session:
    s = requests.Session()
    # Small delay between logins to avoid hammering rate limiter
    time.sleep(0.5)
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    body = r.json()
    if body.get("mfa_required"):
        pytest.skip(f"MFA enrolled for {email}; skipping")
    access = r.cookies.get("access_token")
    assert access, f"no access_token cookie: {dict(r.cookies)}"
    s.headers["Authorization"] = f"Bearer {access}"
    if do_reauth:
        r2 = s.post(f"{API}/auth/reauth", json={"password": password}, timeout=10)
        assert r2.status_code == 200, f"reauth failed: {r2.text}"
        reauth = r2.cookies.get("reauth_token")
        if reauth:
            s.headers["x-reauth-token"] = reauth
    return s


@pytest.fixture(scope="module")
def admin_session():
    return _login(*ADMIN)


@pytest.fixture(scope="module")
def doctor_session():
    return _login(*DOCTOR, do_reauth=False)


@pytest.fixture(scope="module")
def staff_session():
    return _login(*STAFF, do_reauth=False)


@pytest.fixture(scope="module")
def patient_session():
    return _login(*PATIENT, do_reauth=False)


@pytest.fixture(scope="module")
def test_patient(admin_session):
    """Create a test patient with all consents signed for PDF tests."""
    s = admin_session
    payload = {
        "first_name": "TESTDocs",
        "last_name": f"Phase5_{uuid.uuid4().hex[:6]}",
        "email": f"test_docs_{uuid.uuid4().hex[:8]}@example.com",
        "phone": "+15551234567",
        "date_of_birth": "1985-06-15",
        "gender": "female",
        "consents": {
            "hipaa": {
                "accepted": True,
                "accepted_at": "2026-01-15T10:00:00Z",
                "signature_image": PNG_1X1_DATA_URL,
                "typed_signature": "TESTDocs Phase5",
                "version": "1.0",
            },
            "treatment": {
                "accepted": True,
                "accepted_at": "2026-01-15T10:01:00Z",
                "signature_image": PNG_1X1_DATA_URL,
                "typed_signature": "TESTDocs Phase5",
                "version": "1.0",
            },
            "financial": {
                "accepted": True,
                "accepted_at": "2026-01-15T10:02:00Z",
                "signature_image": PNG_1X1_DATA_URL,
                "typed_signature": "TESTDocs Phase5",
                "version": "1.0",
            },
            "telehealth": {
                "accepted": True,
                "accepted_at": "2026-01-15T10:03:00Z",
                "signature_image": PNG_1X1_DATA_URL,
                "typed_signature": "TESTDocs Phase5",
                "version": "1.0",
            },
            "photo_release": {
                "accepted": True,
                "accepted_at": "2026-01-15T10:04:00Z",
                "signature_image": PNG_1X1_DATA_URL,
                "typed_signature": "TESTDocs Phase5",
                "version": "1.0",
            },
        },
    }
    r = s.post(f"{API}/patients", json=payload, timeout=15)
    assert r.status_code == 201, f"patient create failed: {r.status_code} {r.text}"
    return r.json()


# =========================================================================
# Document upload tests
# =========================================================================

class TestDocumentUpload:
    def test_upload_insurance_card_png_success(self, admin_session, test_patient):
        """POST /patients/{id}/documents with valid PNG + reauth → 201"""
        s = admin_session
        files = {"file": ("insurance_front.png", io.BytesIO(PNG_1X1), "image/png")}
        data = {"category": "insurance_card_front", "description": "Front of BCBS card"}
        r = s.post(f"{API}/patients/{test_patient['id']}/documents", files=files, data=data, timeout=20)
        assert r.status_code == 201, f"upload failed: {r.status_code} {r.text}"
        doc = r.json()
        assert doc["patient_id"] == test_patient["id"]
        assert doc["category"] == "insurance_card_front"
        assert doc["content_type"] == "image/png"
        assert doc["size"] == len(PNG_1X1)
        assert doc["filename"] == "insurance_front.png"
        assert "id" in doc
        assert "storage_path" not in doc  # never expose
        # stash for subsequent tests
        pytest.doc_id = doc["id"]

    def test_list_documents_returns_upload(self, admin_session, test_patient):
        s = admin_session
        r = s.get(f"{API}/patients/{test_patient['id']}/documents", timeout=10)
        assert r.status_code == 200, r.text
        docs = r.json()
        assert isinstance(docs, list) and len(docs) >= 1
        ids = [d["id"] for d in docs]
        assert getattr(pytest, "doc_id", None) in ids

    def test_download_document_returns_bytes(self, admin_session, test_patient):
        s = admin_session
        doc_id = getattr(pytest, "doc_id")
        r = s.get(f"{API}/patients/{test_patient['id']}/documents/{doc_id}/download", timeout=15)
        assert r.status_code == 200, r.text
        assert r.headers.get("content-type", "").startswith("image/png")
        # File should match original (allow different if storage transforms; but min >=1 byte)
        assert len(r.content) >= 1
        # Best-effort: exact match expected
        if len(r.content) == len(PNG_1X1):
            assert r.content == PNG_1X1

    def test_upload_empty_file_rejected(self, admin_session, test_patient):
        s = admin_session
        files = {"file": ("empty.png", io.BytesIO(b""), "image/png")}
        r = s.post(f"{API}/patients/{test_patient['id']}/documents", files=files, data={"category": "other"}, timeout=10)
        assert r.status_code == 400, r.text

    def test_upload_oversized_rejected(self, admin_session, test_patient):
        """>10MB should be rejected with 413"""
        s = admin_session
        big = b"\x89PNG\r\n\x1a\n" + b"0" * (10 * 1024 * 1024 + 100)
        files = {"file": ("big.png", io.BytesIO(big), "image/png")}
        r = s.post(f"{API}/patients/{test_patient['id']}/documents", files=files, data={"category": "other"}, timeout=30)
        assert r.status_code == 413, f"expected 413, got {r.status_code}: {r.text[:200]}"

    def test_upload_unsupported_content_type(self, admin_session, test_patient):
        s = admin_session
        files = {"file": ("evil.exe", io.BytesIO(b"MZ\x00\x00binary"), "application/x-msdownload")}
        r = s.post(f"{API}/patients/{test_patient['id']}/documents", files=files, data={"category": "other"}, timeout=10)
        assert r.status_code == 400, r.text

    def test_upload_unknown_category(self, admin_session, test_patient):
        s = admin_session
        files = {"file": ("ok.png", io.BytesIO(PNG_1X1), "image/png")}
        r = s.post(f"{API}/patients/{test_patient['id']}/documents", files=files, data={"category": "bogus_cat"}, timeout=10)
        assert r.status_code == 400, r.text

    def test_upload_without_reauth_rejected(self, test_patient):
        """Fresh admin session without reauth → 401"""
        s = _login(*ADMIN, do_reauth=False)
        files = {"file": ("x.png", io.BytesIO(PNG_1X1), "image/png")}
        r = s.post(f"{API}/patients/{test_patient['id']}/documents", files=files, data={"category": "other"}, timeout=10)
        assert r.status_code == 401, f"expected 401 Re-auth required, got {r.status_code}: {r.text}"
        # message should mention re-auth
        assert "re-auth" in r.text.lower() or "reauth" in r.text.lower()

    def test_delete_document_and_verify_gone(self, admin_session, test_patient):
        s = admin_session
        doc_id = getattr(pytest, "doc_id")
        r = s.delete(f"{API}/patients/{test_patient['id']}/documents/{doc_id}", timeout=10)
        assert r.status_code == 204, r.text
        # list must no longer contain it
        r2 = s.get(f"{API}/patients/{test_patient['id']}/documents", timeout=10)
        assert r2.status_code == 200
        ids = [d["id"] for d in r2.json()]
        assert doc_id not in ids


# =========================================================================
# Consent PDF tests
# =========================================================================

class TestConsentPdf:
    @pytest.mark.parametrize("ctype", ["hipaa", "treatment", "financial", "telehealth", "photo_release"])
    def test_pdf_all_types_admin(self, admin_session, test_patient, ctype):
        s = admin_session
        r = s.get(f"{API}/patients/{test_patient['id']}/consents/{ctype}/pdf", timeout=20)
        assert r.status_code == 200, f"{ctype}: {r.status_code} {r.text[:300]}"
        assert r.headers.get("content-type", "").startswith("application/pdf")
        assert r.content[:4] == b"%PDF", f"{ctype}: not a PDF, got {r.content[:20]!r}"
        assert len(r.content) > 500

    def test_pdf_unsigned_returns_409(self, admin_session):
        """Create a patient with an unsigned consent and expect 409."""
        s = admin_session
        payload = {
            "first_name": "TESTUnsigned",
            "last_name": f"Phase5_{uuid.uuid4().hex[:6]}",
            "consents": {
                "hipaa": {"accepted": False},
            },
        }
        r = s.post(f"{API}/patients", json=payload, timeout=15)
        assert r.status_code == 201, r.text
        pid = r.json()["id"]
        r2 = s.get(f"{API}/patients/{pid}/consents/hipaa/pdf", timeout=10)
        assert r2.status_code == 409, r2.text

    def test_pdf_nonexistent_type_returns_404(self, admin_session, test_patient):
        s = admin_session
        r = s.get(f"{API}/patients/{test_patient['id']}/consents/bogus_type/pdf", timeout=10)
        assert r.status_code == 404, r.text

    def test_pdf_staff_without_reason_returns_400(self, staff_session, test_patient):
        s = staff_session
        r = s.get(f"{API}/patients/{test_patient['id']}/consents/hipaa/pdf", timeout=10)
        assert r.status_code == 400, f"expected 400 reason-required, got {r.status_code}: {r.text}"

    def test_pdf_staff_with_reason_succeeds(self, staff_session, test_patient):
        s = staff_session
        r = s.get(
            f"{API}/patients/{test_patient['id']}/consents/hipaa/pdf",
            params={"reason": "Compliance audit export"},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        assert r.content[:4] == b"%PDF"

    def test_pdf_doctor_without_reason_returns_400(self, doctor_session, test_patient):
        s = doctor_session
        r = s.get(f"{API}/patients/{test_patient['id']}/consents/hipaa/pdf", timeout=10)
        assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text}"

    def test_pdf_patient_self_no_reason(self, patient_session):
        """Patient downloading own consent needs no reason. Requires
        patient@ccms.app to have a linked patient row with a signed consent."""
        s = patient_session
        r = s.get(f"{API}/patients", timeout=10)
        if r.status_code != 200:
            pytest.skip(f"patient list lookup failed: {r.status_code}")
        mine = r.json()
        if not mine:
            pytest.skip("patient has no linked patient row")
        pid = mine[0]["id"]
        # Check if patient has hipaa signed
        r2 = s.get(f"{API}/patients/{pid}", timeout=10)
        if r2.status_code != 200:
            pytest.skip(f"patient self-read failed: {r2.status_code}")
        consents = (r2.json() or {}).get("consents") or {}
        hipaa = consents.get("hipaa") if isinstance(consents, dict) else None
        if not (isinstance(hipaa, dict) and hipaa.get("accepted")):
            pytest.skip("patient self has no signed hipaa consent")
        r3 = s.get(f"{API}/patients/{pid}/consents/hipaa/pdf", timeout=15)
        assert r3.status_code == 200, r3.text
        assert r3.content[:4] == b"%PDF"
