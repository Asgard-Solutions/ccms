"""
Bulk "send outstanding statements" month-end workflow.

Endpoint: POST /api/billing/statements/send-outstanding
"""
from __future__ import annotations

import os

import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

BASE = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE}/api" if BASE else "http://localhost:8001/api"

DEFAULT_ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")
DEFAULT_DOCTOR = ("doctor@ccms.app", "Doctor@ComplianceClinic1")


def _login(email: str, password: str) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{API}/auth/login",
               json={"email": email, "password": password}, timeout=15)
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


def test_bulk_send_dry_run_returns_pending_candidates():
    s = _login(*DEFAULT_ADMIN)
    r = s.post(f"{API}/billing/statements/send-outstanding",
               json={"dry_run": True}, timeout=30)
    assert r.status_code == 200, r.text
    body = r.json()
    for key in (
        "generated", "sent_email", "queued_mail",
        "skipped_unchanged", "skipped_no_contact",
        "errors", "dry_run", "details",
    ):
        assert key in body
    assert body["dry_run"] is True
    assert body["sent_email"] == 0
    assert body["queued_mail"] == 0
    # dry-run details only populated when there are candidates
    if body["generated"]:
        sample = body["details"][0]
        for key in ("patient_id", "balance_cents", "channel"):
            assert key in sample
        assert sample["channel"] in ("email", "mail")


def test_bulk_send_is_idempotent_on_unchanged_balances():
    s = _login(*DEFAULT_ADMIN)
    # First run — generate anything outstanding that moved.
    r1 = s.post(f"{API}/billing/statements/send-outstanding",
                json={}, timeout=30)
    assert r1.status_code == 200, r1.text
    # Second run — nothing should change.
    r2 = s.post(f"{API}/billing/statements/send-outstanding",
                json={}, timeout=30)
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    assert body2["generated"] == 0
    assert body2["sent_email"] == 0
    assert body2["queued_mail"] == 0
    assert body2["errors"] == []


def test_bulk_send_rejects_doctor_role():
    s = _login(*DEFAULT_DOCTOR)
    r = s.post(f"{API}/billing/statements/send-outstanding",
               json={"dry_run": True}, timeout=10)
    # doctor is not admin|staff → 403
    assert r.status_code == 403, r.text
