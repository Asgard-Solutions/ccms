"""Helcim Customer Vault + payment-schedule auto-charge engine tests.

Covers card vault CRUD + encryption, schedule lifecycle, retry-with-
backoff failure handling, run-history, manual run-now, and the
admin scheduler-tick endpoint. Helcim API calls are mocked at the
httpx layer.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
import requests
from dotenv import load_dotenv

load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")

BASE = (os.environ.get("REACT_APP_BACKEND_URL") or "").rstrip("/")
API = f"{BASE}/api"

ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")


def _login(email, password):
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, r.text
    return s


def _ensure_helcim_creds(s):
    s.put(f"{API}/billing/helcim/settings", json={
        "api_token": "test_helcim_api_token_abcdef1234",
        "account_id": "TEST-99999",
        "webhook_verifier_token": "dGVzdHZlcmlmaWVy",
        "test_mode": True,
    }, timeout=10)


def _patient_id(s):
    me = s.get(f"{API}/auth/me", timeout=5).json()
    return me["id"]  # use the admin's user id as a stand-in patient id; we just need a stable string


def _save_card_directly(s, *, patient_id):
    return s.post(f"{API}/billing/helcim/cards", json={
        "patient_id": patient_id,
        "helcim_card_token": f"tok_{uuid.uuid4().hex[:10]}",
        "helcim_customer_code": f"cust_{uuid.uuid4().hex[:6]}",
        "brand": "Visa", "last4": "4242", "expiry": "12/29",
        "cardholder_name": "Test Patient",
        "is_default": True, "source": "manual_entry",
    }, timeout=10)


# ---------------------------------------------------------------------------
# Vault CRUD
# ---------------------------------------------------------------------------

def test_save_and_list_card():
    s = _login(*ADMIN)
    _ensure_helcim_creds(s)
    pid = _patient_id(s)
    r = _save_card_directly(s, patient_id=pid)
    assert r.status_code == 201, r.text
    card = r.json()
    assert card["last4"] == "4242"
    assert card["brand"] == "Visa"
    assert card["is_default"] is True
    # plaintext token is never in the response
    assert "tok_" not in r.text
    # List
    rows = s.get(f"{API}/billing/helcim/cards/{pid}", timeout=10).json()
    assert any(row["id"] == card["id"] for row in rows)


def test_only_one_default_card_per_patient():
    s = _login(*ADMIN)
    _ensure_helcim_creds(s)
    pid = _patient_id(s)
    _save_card_directly(s, patient_id=pid)
    # Save another with is_default=true → should unset the first.
    r = s.post(f"{API}/billing/helcim/cards", json={
        "patient_id": pid,
        "helcim_card_token": f"tok_{uuid.uuid4().hex[:10]}",
        "brand": "MC", "last4": "1111",
        "is_default": True, "source": "manual_entry",
    }, timeout=10)
    assert r.status_code == 201
    rows = s.get(f"{API}/billing/helcim/cards/{pid}", timeout=10).json()
    defaults = [r for r in rows if r["is_default"]]
    assert len(defaults) == 1
    assert defaults[0]["last4"] == "1111"


def test_delete_card_soft_deletes():
    s = _login(*ADMIN)
    _ensure_helcim_creds(s)
    pid = _patient_id(s)
    card = _save_card_directly(s, patient_id=pid).json()
    r = s.delete(f"{API}/billing/helcim/cards/{card['id']}", timeout=10)
    assert r.status_code == 204
    rows = s.get(f"{API}/billing/helcim/cards/{pid}", timeout=10).json()
    assert all(r["id"] != card["id"] for r in rows)


# ---------------------------------------------------------------------------
# Schedule lifecycle (uses pure helpers, bypassing live Helcim)
# ---------------------------------------------------------------------------

def test_create_schedule_split_amounts_correctly():
    s = _login(*ADMIN)
    _ensure_helcim_creds(s)
    pid = _patient_id(s)
    card = _save_card_directly(s, patient_id=pid).json()
    # $100 split into 3 charges → 33.33 / 33.33 / 33.34
    r = s.post(f"{API}/billing/helcim/schedules", json={
        "patient_id": pid, "card_token_id": card["id"],
        "kind": "payment_plan", "label": "3-pay test",
        "total_cents": 10_000, "num_charges": 3,
        "frequency": "monthly", "start_at": "2026-06-01",
    }, timeout=10)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["per_charge_cents"] == 3333
    assert body["last_charge_cents"] == 3334
    assert body["status"] == "active"
    assert body["charges_completed"] == 0
    assert body["next_charge_at"].startswith("2026-06-01")


def test_list_filter_and_patch_schedule():
    s = _login(*ADMIN)
    _ensure_helcim_creds(s)
    pid = _patient_id(s)
    card = _save_card_directly(s, patient_id=pid).json()
    r = s.post(f"{API}/billing/helcim/schedules", json={
        "patient_id": pid, "card_token_id": card["id"],
        "label": "filter test", "total_cents": 5000, "num_charges": 2,
        "frequency": "weekly", "start_at": "2026-07-01",
    }, timeout=10).json()
    sid = r["id"]

    # Patch label
    r2 = s.patch(f"{API}/billing/helcim/schedules/{sid}",
                 json={"label": "renamed"}, timeout=10)
    assert r2.status_code == 200 and r2.json()["label"] == "renamed"

    # Pause
    r3 = s.post(f"{API}/billing/helcim/schedules/{sid}/status",
                json={"new_status": "paused"}, timeout=10)
    assert r3.status_code == 200 and r3.json()["status"] == "paused"

    # Filter
    rows = s.get(f"{API}/billing/helcim/schedules",
                 params={"patient_id": pid, "status_filter": "paused"},
                 timeout=10).json()
    assert any(r["id"] == sid for r in rows)


# ---------------------------------------------------------------------------
# Engine — direct unit tests with mocked Helcim
# ---------------------------------------------------------------------------

def _approved_helcim(amount):
    class _R:
        status_code = 200
        text = ""
        def json(self):
            return {"transaction": {"transactionId": 12345,
                                    "status": "APPROVED",
                                    "amount": amount}}
    return _R()


def _declined_helcim():
    class _R:
        status_code = 200
        text = ""
        def json(self):
            return {"transaction": {"transactionId": 99,
                                    "status": "DECLINED",
                                    "response": "Insufficient funds"}}
    return _R()


@pytest.mark.asyncio
async def test_engine_charges_advances_and_completes():
    """Mocked end-to-end: 2-charge schedule → 2 successful charges → completed."""
    from core.tenancy import reset_router_for_tests
    reset_router_for_tests()
    from services.billing.helcim.scheduler import (
        ScheduleCreate, charge_one_schedule, create_schedule, list_runs,
    )
    from services.billing.helcim.credentials import HelcimCredentialsCreate, upsert_credentials
    tenant_id = "_unit_test_engine"

    # Fresh creds (encrypted-at-rest path).
    await upsert_credentials(tenant_id, HelcimCredentialsCreate(
        api_token="test_t_engine_xx", account_id="AID", test_mode=True,
    ), actor={"id": "tester"})

    # Save a card via direct vault call.
    from services.billing.helcim.card_vault import save_card, SavedCardCreate
    card = await save_card(tenant_id, SavedCardCreate(
        patient_id="pt-engine", helcim_card_token="tok_abc_engine",
        helcim_customer_code="cust_1",
        brand="Visa", last4="1111", is_default=True, source="manual_entry",
    ), actor={"id": "tester"})

    sched = await create_schedule(tenant_id, ScheduleCreate(
        patient_id="pt-engine", card_token_id=card["id"],
        kind="payment_plan", label="2x test",
        total_cents=2000, num_charges=2,
        frequency="weekly", start_at="2026-01-01",
    ), actor={"id": "tester"})

    async def _mock_request(self, method, url, *, headers=None, json=None):
        return _approved_helcim(json["amount"])

    with patch("httpx.AsyncClient.request", new=_mock_request):
        # First charge.
        o1 = await charge_one_schedule(tenant_id, sched)
        assert o1.outcome == "success"
        # Re-fetch and run the second.
        from core.tenancy import tenant_db
        sched2 = await tenant_db(tenant_id).payment_schedules.find_one(
            {"id": sched["id"], "tenant_id": tenant_id}, {"_id": 0},
        )
        assert sched2["charges_completed"] == 1
        assert sched2["status"] == "active"
        o2 = await charge_one_schedule(tenant_id, sched2)
        assert o2.outcome == "success"
        sched3 = await tenant_db(tenant_id).payment_schedules.find_one(
            {"id": sched["id"], "tenant_id": tenant_id}, {"_id": 0},
        )
        assert sched3["charges_completed"] == 2
        assert sched3["status"] == "completed"
        assert sched3["next_charge_at"] is None
    runs = await list_runs(tenant_id, sched["id"])
    assert len(runs) == 2
    assert all(r["outcome"] == "success" for r in runs)


@pytest.mark.asyncio
async def test_engine_retry_with_backoff_then_fail():
    """3 declines in a row → schedule.status=failed, admin notification."""
    from core.tenancy import reset_router_for_tests
    reset_router_for_tests()
    from services.billing.helcim.scheduler import (
        ScheduleCreate, charge_one_schedule, create_schedule,
        MAX_FAILED_ATTEMPTS,
    )
    from services.billing.helcim.credentials import HelcimCredentialsCreate, upsert_credentials
    from core.tenancy import tenant_db

    tenant_id = "_unit_test_retry"
    await upsert_credentials(tenant_id, HelcimCredentialsCreate(
        api_token="test_t_retry_xx", account_id="AID2", test_mode=True,
    ), actor={"id": "tester"})

    from services.billing.helcim.card_vault import save_card, SavedCardCreate
    card = await save_card(tenant_id, SavedCardCreate(
        patient_id="pt-decline", helcim_card_token="tok_dec_test",
        helcim_customer_code="cust_dec",
        brand="Visa", last4="0002", is_default=True, source="manual_entry",
    ), actor={"id": "tester"})

    sched = await create_schedule(tenant_id, ScheduleCreate(
        patient_id="pt-decline", card_token_id=card["id"],
        kind="payment_plan", label="will decline",
        total_cents=5000, num_charges=5,
        frequency="weekly", start_at="2026-01-01",
    ), actor={"id": "tester"})

    async def _mock_decline(self, method, url, *, headers=None, json=None):
        return _declined_helcim()

    with patch("httpx.AsyncClient.request", new=_mock_decline):
        for _ in range(MAX_FAILED_ATTEMPTS):
            cur = await tenant_db(tenant_id).payment_schedules.find_one(
                {"id": sched["id"], "tenant_id": tenant_id}, {"_id": 0},
            )
            if cur["status"] != "active":
                break
            await charge_one_schedule(tenant_id, cur)
    final = await tenant_db(tenant_id).payment_schedules.find_one(
        {"id": sched["id"], "tenant_id": tenant_id}, {"_id": 0},
    )
    assert final["status"] == "failed"
    assert final["consecutive_failures"] == MAX_FAILED_ATTEMPTS
    assert final["next_charge_at"] is None

    # Notification was created.
    notif = await tenant_db(tenant_id).notifications.find_one(
        {"tenant_id": tenant_id, "category": "billing"}, {"_id": 0},
    )
    assert notif is not None
    assert "failed" in notif["title"].lower()


@pytest.mark.asyncio
async def test_engine_skips_charge_when_creds_missing():
    """No credentials configured → outcome=error, schedule stays active."""
    from core.tenancy import reset_router_for_tests
    reset_router_for_tests()
    from services.billing.helcim.scheduler import (
        ScheduleCreate, charge_one_schedule, create_schedule,
    )
    from services.billing.helcim.card_vault import save_card, SavedCardCreate
    from services.billing.helcim.credentials import delete_credentials
    from core.tenancy import tenant_db

    tenant_id = "_unit_test_no_creds"
    await delete_credentials(tenant_id)

    card = await save_card(tenant_id, SavedCardCreate(
        patient_id="pt-x", helcim_card_token="tok_orphan",
        brand="V", last4="0000", source="manual_entry",
    ), actor={"id": "x"})
    sched = await create_schedule(tenant_id, ScheduleCreate(
        patient_id="pt-x", card_token_id=card["id"],
        label="orphan", total_cents=100, num_charges=1,
        frequency="weekly", start_at="2026-01-01",
    ), actor={"id": "x"})
    o = await charge_one_schedule(tenant_id, sched)
    assert o.outcome == "error"
    assert "credential" in (o.error or "").lower()
    # schedule remains active (we did not register a successful charge).
    cur = await tenant_db(tenant_id).payment_schedules.find_one(
        {"id": sched["id"], "tenant_id": tenant_id}, {"_id": 0},
    )
    assert cur["status"] == "active"


# ---------------------------------------------------------------------------
# Run-now + scheduler tick endpoints
# ---------------------------------------------------------------------------

def test_scheduler_tick_processes_due_only():
    """Verify /scheduler/tick processes only schedules whose next_charge_at <= now."""
    s = _login(*ADMIN)
    _ensure_helcim_creds(s)
    pid = _patient_id(s)
    card = _save_card_directly(s, patient_id=pid).json()

    # Past-dated schedule → should be picked up
    past = s.post(f"{API}/billing/helcim/schedules", json={
        "patient_id": pid, "card_token_id": card["id"],
        "label": "past-due", "total_cents": 1000, "num_charges": 1,
        "frequency": "monthly", "start_at": "2020-01-01",
    }, timeout=10).json()

    # Future-dated → must be skipped
    future_at = (datetime.now(timezone.utc) + timedelta(days=90)).isoformat()
    fut = s.post(f"{API}/billing/helcim/schedules", json={
        "patient_id": pid, "card_token_id": card["id"],
        "label": "future", "total_cents": 1000, "num_charges": 1,
        "frequency": "monthly", "start_at": future_at,
    }, timeout=10).json()

    r = s.post(f"{API}/billing/helcim/scheduler/tick", timeout=20)
    assert r.status_code == 200, r.text
    body = r.json()
    sched_ids = [o["schedule_id"] for o in body["outcomes"]]
    assert past["id"] in sched_ids
    assert fut["id"] not in sched_ids


def test_run_now_rejects_non_runnable_status():
    s = _login(*ADMIN)
    _ensure_helcim_creds(s)
    pid = _patient_id(s)
    card = _save_card_directly(s, patient_id=pid).json()
    r = s.post(f"{API}/billing/helcim/schedules", json={
        "patient_id": pid, "card_token_id": card["id"],
        "label": "rn test", "total_cents": 500, "num_charges": 1,
        "frequency": "weekly", "start_at": "2026-01-01",
    }, timeout=10).json()
    sid = r["id"]
    s.post(f"{API}/billing/helcim/schedules/{sid}/status",
           json={"new_status": "cancelled"}, timeout=10)
    r2 = s.post(f"{API}/billing/helcim/schedules/{sid}/run-now", timeout=10)
    assert r2.status_code == 400
    assert "status" in r2.text.lower()


def test_capture_with_save_card_persists_token():
    """End-to-end: /checkout/capture with save_card=true creates a vault row."""
    s = _login(*ADMIN)
    _ensure_helcim_creds(s)
    pid = _patient_id(s)

    me = s.get(f"{API}/auth/me", timeout=5).json()
    tenant_id = me["tenant_id"]

    # Seed a session row directly via async helper using a fresh loop.
    import asyncio
    from core.tenancy import reset_router_for_tests, tenant_db

    async def _seed():
        reset_router_for_tests()
        await tenant_db(tenant_id).helcim_sessions.insert_one({
            "id": session_id,
            "tenant_id": tenant_id,
            "checkout_token": "ct", "secret_token": "st",
            "amount_cents": 5000, "currency": "USD",
            "payment_type": "purchase", "invoice_id": None,
            "patient_id": pid, "customer_code": None,
            "created_at": "2026-05-02T00:00:00+00:00",
            "created_by": "tester", "status": "initialized",
        })

    session_id = f"sess-savecard-{uuid.uuid4().hex[:8]}"
    asyncio.run(_seed())

    r = s.post(f"{API}/billing/helcim/checkout/capture", json={
        "session_id": session_id,
        "transaction_id": "TXN_999",
        "card_token": "tok_save_test",
        "customer_code": "cust_save_test",
        "amount": 50.0, "currency": "USD",
        "response": 1, "response_message": "APPROVED",
        "raw": {"foo": "bar"},
        "save_card": True,
        "save_card_brand": "Visa",
        "save_card_last4": "9999",
        "save_card_expiry": "10/30",
        "save_card_cardholder": "Test Cardholder",
    }, timeout=10)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("saved_card_id"), body
    cards = s.get(f"{API}/billing/helcim/cards/{pid}", timeout=10).json()
    assert any(c["last4"] == "9999" for c in cards)
