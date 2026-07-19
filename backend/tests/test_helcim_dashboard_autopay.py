"""Tests for billing-failures dashboard endpoint + statement auto-pay
hooks + treatment-plan-linked schedules."""
from __future__ import annotations

import os
import uuid
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


def _ensure_creds(s):
    s.put(f"{API}/billing/helcim/settings", json={
        "api_token": "test_helcim_api_token_abcdef1234",
        "account_id": "TEST-99999",
        "webhook_verifier_token": "dGVzdA==",
        "test_mode": True,
    }, timeout=10)


# ---------------------------------------------------------------------------
# Billing-failures endpoint
# ---------------------------------------------------------------------------

def test_billing_failures_lists_unread_billing_notifications():
    s = _login(*ADMIN)
    _ensure_creds(s)
    me = s.get(f"{API}/auth/me", timeout=5).json()
    tenant_id = me["tenant_id"]

    # Inject a synthetic billing-category notification directly.
    import asyncio
    from core.tenancy import reset_router_for_tests, tenant_db
    notif_id = f"notif-{uuid.uuid4().hex[:8]}"

    async def _seed():
        reset_router_for_tests()
        await tenant_db(tenant_id).notifications.insert_one({
            "id": notif_id, "tenant_id": tenant_id,
            "category": "billing", "severity": "warning",
            "title": "Auto-charge schedule failed: Test Plan",
            "body": "Patient X payment schedule sched-xyz failed after 3 attempts. "
                    "Last error: Insufficient funds.",
            "patient_id": "pt-x", "read": False,
            "created_at": "2026-05-02T00:00:00+00:00",
        })
    asyncio.run(_seed())

    r = s.get(f"{API}/billing/helcim/billing-failures", timeout=10)
    assert r.status_code == 200, r.text
    rows = r.json()
    found = next((x for x in rows if x["id"] == notif_id), None)
    assert found, rows
    assert found["category"] == "billing"
    assert found["read"] is False


def test_billing_failures_dismiss_marks_read():
    s = _login(*ADMIN)
    _ensure_creds(s)
    me = s.get(f"{API}/auth/me", timeout=5).json()
    tenant_id = me["tenant_id"]

    import asyncio
    from core.tenancy import reset_router_for_tests, tenant_db
    notif_id = f"notif-dismiss-{uuid.uuid4().hex[:8]}"

    async def _seed():
        reset_router_for_tests()
        await tenant_db(tenant_id).notifications.insert_one({
            "id": notif_id, "tenant_id": tenant_id,
            "category": "billing", "title": "x", "body": "x",
            "read": False, "created_at": "2026-05-02T00:00:00+00:00",
        })
    asyncio.run(_seed())

    r = s.post(f"{API}/billing/helcim/billing-failures/{notif_id}/dismiss", timeout=10)
    assert r.status_code == 200, r.text

    # After dismiss, default include_read=false should not return it.
    rows = s.get(f"{API}/billing/helcim/billing-failures", timeout=10).json()
    assert all(r["id"] != notif_id for r in rows)
    # With include_read=true it shows up again, marked read.
    rows2 = s.get(f"{API}/billing/helcim/billing-failures",
                  params={"include_read": "true"}, timeout=10).json()
    found = next((x for x in rows2 if x["id"] == notif_id), None)
    assert found and found["read"] is True


# ---------------------------------------------------------------------------
# Statement auto-pay
# ---------------------------------------------------------------------------

def test_statement_autopay_settings_round_trip():
    s = _login(*ADMIN)
    r = s.put(f"{API}/billing/helcim/statement-autopay/settings",
              json={"enabled": True, "notes": "Pilot"}, timeout=10)
    assert r.status_code == 200 and r.json()["enabled"] is True
    r2 = s.get(f"{API}/billing/helcim/statement-autopay/settings", timeout=10)
    assert r2.json()["enabled"] is True

    r3 = s.put(f"{API}/billing/helcim/statement-autopay/settings",
               json={"enabled": False}, timeout=10)
    assert r3.status_code == 200 and r3.json()["enabled"] is False


def test_patient_autopay_optin_default_false():
    s = _login(*ADMIN)
    r = s.get(f"{API}/billing/helcim/statement-autopay/patients/non-existent-pt", timeout=10)
    assert r.status_code == 200
    body = r.json()
    assert body["opted_in"] is False
    assert body["card_token_id"] is None


def test_patient_autopay_optin_persists():
    s = _login(*ADMIN)
    pid = f"pt-optin-{uuid.uuid4().hex[:6]}"
    r = s.put(f"{API}/billing/helcim/statement-autopay/patients/{pid}",
              json={"opted_in": True, "card_token_id": "card-fake"}, timeout=10)
    assert r.status_code == 200
    body = r.json()
    assert body["opted_in"] is True
    assert body["card_token_id"] == "card-fake"


@pytest.mark.asyncio
async def test_statement_autopay_helper_creates_schedule_when_eligible():
    """Direct unit test of the helper bypassing the FastAPI app."""
    from core.tenancy import reset_router_for_tests, tenant_db
    from services.billing.helcim.statement_autopay import maybe_create_statement_autopay
    from services.billing.helcim.card_vault import save_card, SavedCardCreate
    reset_router_for_tests()
    tenant_id = "_autopay_unit"
    pid = f"pt-{uuid.uuid4().hex[:6]}"

    db = tenant_db(tenant_id)
    await db.helcim_statement_autopay.insert_one({
        "tenant_id": tenant_id, "enabled": True,
    })
    await db.helcim_statement_autopay_patients.insert_one({
        "tenant_id": tenant_id, "patient_id": pid,
        "opted_in": True, "card_token_id": None,
    })
    card = await save_card(tenant_id, SavedCardCreate(
        patient_id=pid, helcim_card_token=f"tok_autopay_{uuid.uuid4().hex[:8]}",
        helcim_customer_code="cust", brand="V", last4="0001",
        is_default=True, source="manual_entry",
    ), actor={"id": "tester"})

    sched = await maybe_create_statement_autopay(
        tenant_id, patient_id=pid, statement_id="stmt-abc",
        total_cents=15000, actor={"id": "tester"},
    )
    assert sched is not None
    assert sched["kind"] == "statement_autopay"
    assert sched["total_cents"] == 15000
    assert sched["num_charges"] == 1
    assert sched["card_token_id"] == card["id"]
    assert sched["status"] == "active"


@pytest.mark.asyncio
async def test_statement_autopay_helper_skips_when_tenant_disabled():
    from core.tenancy import reset_router_for_tests, tenant_db
    from services.billing.helcim.statement_autopay import maybe_create_statement_autopay
    reset_router_for_tests()
    tenant_id = "_autopay_unit_off"
    pid = f"pt-{uuid.uuid4().hex[:6]}"
    # Tenant toggle is OFF (no row).
    db = tenant_db(tenant_id)
    await db.helcim_statement_autopay_patients.insert_one({
        "tenant_id": tenant_id, "patient_id": pid, "opted_in": True,
    })
    sched = await maybe_create_statement_autopay(
        tenant_id, patient_id=pid, statement_id="stmt-x",
        total_cents=1000, actor={"id": "tester"},
    )
    assert sched is None


@pytest.mark.asyncio
async def test_statement_autopay_helper_skips_when_no_card():
    from core.tenancy import reset_router_for_tests, tenant_db
    from services.billing.helcim.statement_autopay import maybe_create_statement_autopay
    reset_router_for_tests()
    tenant_id = "_autopay_unit_nocard"
    pid = f"pt-{uuid.uuid4().hex[:6]}"
    db = tenant_db(tenant_id)
    await db.helcim_statement_autopay.insert_one({
        "tenant_id": tenant_id, "enabled": True,
    })
    await db.helcim_statement_autopay_patients.insert_one({
        "tenant_id": tenant_id, "patient_id": pid, "opted_in": True,
    })
    # No saved card
    sched = await maybe_create_statement_autopay(
        tenant_id, patient_id=pid, statement_id="stmt-x",
        total_cents=1000, actor={"id": "tester"},
    )
    assert sched is None


# ---------------------------------------------------------------------------
# Treatment plan linkage — schedule with kind=treatment_plan
# ---------------------------------------------------------------------------

def test_create_treatment_plan_schedule_carries_treatment_plan_id():
    s = _login(*ADMIN)
    _ensure_creds(s)
    me = s.get(f"{API}/auth/me", timeout=5).json()
    pid = me["id"]
    # Save card via direct API (POST /cards).
    r = s.post(f"{API}/billing/helcim/cards", json={
        "patient_id": pid,
        "helcim_card_token": f"tok_tp_{uuid.uuid4().hex[:8]}",
        "brand": "Visa", "last4": "1212", "source": "manual_entry",
    }, timeout=10)
    card = r.json()
    r2 = s.post(f"{API}/billing/helcim/schedules", json={
        "patient_id": pid, "card_token_id": card["id"],
        "kind": "treatment_plan",
        "treatment_plan_id": "tp-xyz",
        "label": "12-visit course of care",
        "total_cents": 60000, "num_charges": 12,
        "frequency": "weekly", "start_at": "2026-06-01",
    }, timeout=10)
    assert r2.status_code == 201, r2.text
    body = r2.json()
    assert body["kind"] == "treatment_plan"
    assert body["treatment_plan_id"] == "tp-xyz"
    assert body["per_charge_cents"] == 5000
    # List filtered by patient should include it.
    rows = s.get(f"{API}/billing/helcim/schedules",
                 params={"patient_id": pid}, timeout=10).json()
    assert any(r["id"] == body["id"] for r in rows)
