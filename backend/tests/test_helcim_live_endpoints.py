"""Helcim live-endpoint smoke tests against the public REACT_APP_BACKEND_URL.

Confirms behaviour of the endpoints that are NOT covered by mocked unit
tests in test_helcim_integration.py — specifically the /settings/test,
/checkout/initialize, and /webhook-log routes plus encryption-at-rest.
"""
from __future__ import annotations

import os
import asyncio

import pytest
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


def _put_creds(s):
    return s.put(
        f"{API}/billing/helcim/settings",
        json={
            "api_token": "mock_helcim_token_for_testing_abcd1234",
            "account_id": "TEST-LIVE-1",
            "webhook_verifier_token": "dGVzdHZlcmlmaWVydG9rZW4=",
            "test_mode": True,
        },
        timeout=10,
    )


# ---------------------------------------------------------------------------
# Admin enforcement (DELETE / settings / settings/test)
# ---------------------------------------------------------------------------

def test_doctor_blocked_on_put_settings():
    s = _login(*DOCTOR)
    r = s.put(f"{API}/billing/helcim/settings", json={
        "api_token": "x" * 12, "account_id": "FOO", "test_mode": False,
    }, timeout=10)
    assert r.status_code in (401, 403), r.text


def test_doctor_blocked_on_delete_settings():
    s = _login(*DOCTOR)
    r = s.delete(f"{API}/billing/helcim/settings", timeout=10)
    assert r.status_code in (401, 403), r.text


def test_doctor_blocked_on_settings_test():
    s = _login(*DOCTOR)
    r = s.post(f"{API}/billing/helcim/settings/test", timeout=10)
    assert r.status_code in (401, 403), r.text


def test_doctor_blocked_on_webhook_log():
    s = _login(*DOCTOR)
    r = s.get(f"{API}/billing/helcim/webhook-log", timeout=10)
    assert r.status_code in (401, 403), r.text


# ---------------------------------------------------------------------------
# /settings/test → expects 400 when no creds, 502 (NOT 500) with bad creds
# ---------------------------------------------------------------------------

def test_settings_test_returns_400_when_unconfigured():
    s = _login(*ADMIN)
    s.delete(f"{API}/billing/helcim/settings", timeout=10)
    r = s.post(f"{API}/billing/helcim/settings/test", timeout=15)
    assert r.status_code == 400, r.text
    assert "not configured" in r.text.lower()


def test_settings_test_returns_502_with_mock_token_no_500():
    s = _login(*ADMIN)
    _put_creds(s)
    r = s.post(f"{API}/billing/helcim/settings/test", timeout=30)
    # Helcim will reject a mock token — we want 502 (clean bubble-up), not 500.
    assert r.status_code != 500, f"Got 500 (server crash): {r.text}"
    assert r.status_code in (200, 502), r.text


# ---------------------------------------------------------------------------
# /checkout/initialize → 400 unconfigured, 502 with bad creds
# ---------------------------------------------------------------------------

def test_checkout_initialize_returns_400_when_unconfigured():
    s = _login(*ADMIN)
    s.delete(f"{API}/billing/helcim/settings", timeout=10)
    r = s.post(
        f"{API}/billing/helcim/checkout/initialize",
        json={"amount_cents": 5000, "currency": "USD"},
        timeout=15,
    )
    assert r.status_code == 400, r.text
    msg = r.json().get("detail", "").lower()
    assert "not configured" in msg or "settings" in msg


def test_checkout_initialize_returns_502_with_mock_token_no_500():
    s = _login(*ADMIN)
    _put_creds(s)
    r = s.post(
        f"{API}/billing/helcim/checkout/initialize",
        json={"amount_cents": 5000, "currency": "USD",
              "invoice_id": "INV-TEST-1", "description": "test charge"},
        timeout=30,
    )
    assert r.status_code != 500, f"Got 500 (server crash): {r.text}"
    assert r.status_code in (200, 502), r.text


# ---------------------------------------------------------------------------
# Encryption-at-rest verification — read the raw mongo doc.
# ---------------------------------------------------------------------------

def test_credentials_encrypted_at_rest():
    """Inspect the raw MongoDB doc to confirm api_token is NOT plaintext."""
    s = _login(*ADMIN)
    _put_creds(s)
    me = s.get(f"{API}/auth/me", timeout=5).json()
    tenant_id = me["tenant_id"]

    async def _check():
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        doc = await db.helcim_credentials.find_one({"tenant_id": tenant_id})
        client.close()
        return doc

    doc = asyncio.run(_check())
    assert doc is not None, "Credentials doc not found in MongoDB"
    # Plaintext api_token must NOT be persisted under any field.
    assert "api_token" not in doc or doc.get("api_token") is None
    assert "mock_helcim_token_for_testing_abcd1234" not in str(doc), (
        "Plaintext api_token found in stored document!"
    )
    # Encrypted variant must be present.
    assert doc.get("api_token_encrypted"), "api_token_encrypted missing"
    assert doc["api_token_encrypted"] != "mock_helcim_token_for_testing_abcd1234"
    # Last4 mask present + matches.
    assert doc.get("api_token_last4") == "1234"


# ---------------------------------------------------------------------------
# /webhook-log accessible to admin and returns list[dict]
# ---------------------------------------------------------------------------

def test_admin_can_read_webhook_log():
    s = _login(*ADMIN)
    _put_creds(s)
    r = s.get(f"{API}/billing/helcim/webhook-log", timeout=10)
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def test_zz_cleanup():
    s = _login(*ADMIN)
    s.delete(f"{API}/billing/helcim/settings", timeout=10)
