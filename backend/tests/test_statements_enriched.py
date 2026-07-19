"""
Enriched statement generation + multi-channel delivery + patient
self-service endpoints.
"""
from __future__ import annotations

import os

import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

BASE = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE}/api" if BASE else "http://localhost:8001/api"

DEFAULT_ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")


def _login(email: str, password: str) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, r.text
    tok = r.cookies.get("access_token") or r.json().get("access_token")
    if tok:
        s.headers["Authorization"] = f"Bearer {tok}"
    r = s.post(f"{API}/auth/reauth", json={"password": password}, timeout=10)
    if r.status_code == 200:
        rt = r.cookies.get("reauth_token") or r.json().get("reauth_token")
        if rt:
            s.headers["x-reauth-token"] = rt
    return s


def _any_patient_id(s):
    rows = s.get(f"{API}/billing/invoices?limit=1", timeout=10).json()
    assert rows, "No invoices seeded — run the billing seeder."
    return rows[0]["patient_id"]


def test_statement_body_shows_insurance_breakdown():
    s = _login(*DEFAULT_ADMIN)
    pid = _any_patient_id(s)
    stmt = s.post(f"{API}/billing/patients/{pid}/statements",
                  json={}, timeout=15).json()
    assert "invoice_breakdown" in stmt
    assert "Insurance" in stmt["body"]
    assert "Balance" in stmt["body"]
    assert "AMOUNT DUE FROM PATIENT" in stmt["body"]
    # Every breakdown row has the 4 new fields.
    for row in stmt["invoice_breakdown"]:
        assert "billed_cents" in row
        assert "insurance_paid_cents" in row
        assert "patient_paid_cents" in row
        assert "adjustments_cents" in row


def test_statement_pdf_download():
    s = _login(*DEFAULT_ADMIN)
    pid = _any_patient_id(s)
    stmt = s.post(f"{API}/billing/patients/{pid}/statements",
                  json={}, timeout=15).json()
    r = s.get(f"{API}/billing/patients/{pid}/statements/{stmt['id']}/pdf",
              timeout=15)
    assert r.status_code == 200, r.text
    assert r.headers["Content-Type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")


def test_statement_send_channel_mail_stamps_status():
    s = _login(*DEFAULT_ADMIN)
    pid = _any_patient_id(s)
    stmt = s.post(f"{API}/billing/patients/{pid}/statements",
                  json={}, timeout=15).json()
    r = s.post(f"{API}/billing/patients/{pid}/statements/{stmt['id']}/send",
               json={"channel": "mail"}, timeout=10)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["channel"] == "mail"
    assert body["provider"] == "queued-for-mail"
    # Read back — sent_via stamped.
    stmt2 = s.get(f"{API}/billing/patients/{pid}/statements/{stmt['id']}",
                  timeout=10).json()
    assert stmt2["sent_via"] == "mail"
    assert stmt2["sent_at"] is not None


def test_statement_send_channel_email_with_to_override():
    s = _login(*DEFAULT_ADMIN)
    pid = _any_patient_id(s)
    stmt = s.post(f"{API}/billing/patients/{pid}/statements",
                  json={}, timeout=15).json()
    r = s.post(f"{API}/billing/patients/{pid}/statements/{stmt['id']}/send",
               json={"channel": "email", "to": "override@example.com"},
               timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["channel"] == "email"
    assert body["to"] == "override@example.com"
    assert body["provider"] in ("resend", "log-only", "mock")


def test_statement_send_email_missing_recipient_returns_422():
    """If no patient email + no override, email channel should 422."""
    s = _login(*DEFAULT_ADMIN)
    pid = _any_patient_id(s)
    stmt = s.post(f"{API}/billing/patients/{pid}/statements",
                  json={}, timeout=15).json()
    # Patient may or may not have an email. If they do, this test is
    # inconclusive — skip by resolving to patient first.
    patient = s.get(f"{API}/patients/{pid}", timeout=10).json()
    if patient.get("email"):
        import pytest
        pytest.skip("Test patient has an email on file")
    r = s.post(f"{API}/billing/patients/{pid}/statements/{stmt['id']}/send",
               json={"channel": "email"}, timeout=10)
    assert r.status_code == 422


def test_my_statements_empty_for_admin_no_patient_record():
    """Admin users aren't patients, so /me/statements returns []."""
    s = _login(*DEFAULT_ADMIN)
    r = s.get(f"{API}/billing/me/statements", timeout=10)
    assert r.status_code == 200
    assert r.json() == []
