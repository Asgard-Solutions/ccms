"""Iter-73 extras: RBAC + 502-not-500 on the new vault & scheduler routes."""
from __future__ import annotations

import os
import uuid
import requests
from dotenv import load_dotenv

load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")

BASE = (os.environ.get("REACT_APP_BACKEND_URL") or "").rstrip("/")
API = f"{BASE}/api"

ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")
DOCTOR = ("doctor@ccms.app", "Doctor@ComplianceClinic1")


def _login(email, password):
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, r.text
    return s


def _ensure_creds(s):
    s.put(f"{API}/billing/helcim/settings", json={
        "api_token": "test_helcim_api_token_extra_1",
        "account_id": "TEST-99999",
        "webhook_verifier_token": "dGVzdHZlcmlmaWVy",
        "test_mode": True,
    }, timeout=10)


# -- RBAC ------------------------------------------------------------------

def test_doctor_blocked_on_list_cards():
    a = _login(*ADMIN)
    pid = a.get(f"{API}/auth/me").json()["id"]
    d = _login(*DOCTOR)
    r = d.get(f"{API}/billing/helcim/cards/{pid}", timeout=10)
    assert r.status_code in (401, 403), r.text


def test_doctor_blocked_on_save_card():
    d = _login(*DOCTOR)
    r = d.post(f"{API}/billing/helcim/cards", json={
        "patient_id": "p1", "helcim_card_token": "tok_x",
        "brand": "V", "last4": "0000", "source": "manual_entry",
    }, timeout=10)
    assert r.status_code in (401, 403), r.text


def test_doctor_blocked_on_schedule_create():
    d = _login(*DOCTOR)
    r = d.post(f"{API}/billing/helcim/schedules", json={
        "patient_id": "p1", "card_token_id": "fake",
        "label": "x", "total_cents": 1000, "num_charges": 2,
        "frequency": "weekly", "start_at": "2026-06-01",
    }, timeout=10)
    assert r.status_code in (401, 403), r.text


def test_doctor_blocked_on_scheduler_tick():
    """tick is admin-only via require_role."""
    d = _login(*DOCTOR)
    r = d.post(f"{API}/billing/helcim/scheduler/tick", timeout=10)
    assert r.status_code in (401, 403), r.text


# -- 502-not-500 against real Helcim with mock token -----------------------

def test_run_now_returns_clean_error_not_500():
    s = _login(*ADMIN)
    _ensure_creds(s)
    pid = s.get(f"{API}/auth/me").json()["id"]
    card = s.post(f"{API}/billing/helcim/cards", json={
        "patient_id": pid,
        "helcim_card_token": f"tok_{uuid.uuid4().hex[:8]}",
        "brand": "Visa", "last4": "4242",
        "is_default": True, "source": "manual_entry",
    }, timeout=10).json()
    sched = s.post(f"{API}/billing/helcim/schedules", json={
        "patient_id": pid, "card_token_id": card["id"],
        "label": "rn-error", "total_cents": 500, "num_charges": 1,
        "frequency": "weekly", "start_at": "2020-01-01",
    }, timeout=10).json()
    r = s.post(f"{API}/billing/helcim/schedules/{sched['id']}/run-now", timeout=20)
    # Helcim auth fails with our test token → outcome=error from engine,
    # endpoint must return 200 with outcome=error (not 500)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["outcome"] in ("error", "declined")
    assert body.get("error") or body["outcome"] == "declined"


def test_scheduler_tick_returns_clean_outcomes_not_500():
    s = _login(*ADMIN)
    _ensure_creds(s)
    r = s.post(f"{API}/billing/helcim/scheduler/tick", timeout=30)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "processed" in body and "outcomes" in body


# -- Scheduler math sanity -------------------------------------------------

def test_schedule_split_99_cents_3_charges():
    """99c / 3 = 33 / 33 / 33 (no remainder)"""
    s = _login(*ADMIN)
    _ensure_creds(s)
    pid = s.get(f"{API}/auth/me").json()["id"]
    card = s.post(f"{API}/billing/helcim/cards", json={
        "patient_id": pid,
        "helcim_card_token": f"tok_{uuid.uuid4().hex[:8]}",
        "brand": "V", "last4": "0001",
        "source": "manual_entry",
    }, timeout=10).json()
    r = s.post(f"{API}/billing/helcim/schedules", json={
        "patient_id": pid, "card_token_id": card["id"],
        "label": "99c-3", "total_cents": 99, "num_charges": 3,
        "frequency": "weekly", "start_at": "2026-06-01",
    }, timeout=10).json()
    assert r["per_charge_cents"] == 33
    assert r["last_charge_cents"] == 33
