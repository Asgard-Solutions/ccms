"""Regression tests for /app/backend/scripts/seed_demo_pins.py.

After running the seeder, every demo account should be able to
verify the documented PIN via POST /api/auth/me/pin/verify.
"""
from __future__ import annotations

import os

import pytest
import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")
_BASE = (os.environ.get("REACT_APP_BACKEND_URL") or "http://localhost:8001").rstrip("/")
API = f"{_BASE}/api"

ACCOUNTS = [
    ("admin@ccms.app",    "Admin@ComplianceClinic1",    "100001"),
    ("doctor@ccms.app",   "Doctor@ComplianceClinic1",   "200002"),
    ("staff@ccms.app",    "Staff@ComplianceClinic1",    "300003"),
    ("patient@ccms.app",  "Patient@ComplianceClinic1",  "400004"),
]


@pytest.mark.parametrize("email,password,pin", ACCOUNTS)
def test_pin_verify_succeeds(email, password, pin):
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, r.text
    r2 = s.post(f"{API}/auth/me/pin/verify", json={"pin": pin}, timeout=15)
    assert r2.status_code == 200, f"{email}: {r2.status_code} {r2.text}"
    body = r2.json()
    # Endpoint should signal success.
    assert body.get("ok") is True or body.get("verified") is True or "verified_at" in body


@pytest.mark.parametrize("email,password,pin", ACCOUNTS)
def test_pin_verify_wrong_pin_fails(email, password, pin):
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200
    bad = "000000" if pin != "000000" else "111111"
    r2 = s.post(f"{API}/auth/me/pin/verify", json={"pin": bad}, timeout=15)
    assert r2.status_code in (400, 401, 403, 422), f"{email}: expected reject, got {r2.status_code}"


def test_pin_status_reports_set():
    s = requests.Session()
    s.post(f"{API}/auth/login", json={"email": "doctor@ccms.app", "password": "Doctor@ComplianceClinic1"}, timeout=15)
    r = s.get(f"{API}/auth/me/pin/status", timeout=10)
    assert r.status_code == 200, r.text
    body = r.json()
    # Should indicate a PIN exists.
    assert body.get("configured") is True or body.get("has_pin") is True or body.get("pin_set") is True
