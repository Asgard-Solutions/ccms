"""Helcim integration tests — credential storage + webhook signature verification.

Live HTTP calls to api.helcim.com are mocked at the `httpx.AsyncClient`
layer so the suite runs offline.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from unittest.mock import AsyncMock, patch

import pytest
import requests
from dotenv import load_dotenv

load_dotenv("/app/frontend/.env")

BASE = (os.environ.get("REACT_APP_BACKEND_URL") or "").rstrip("/")
API = f"{BASE}/api"

ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")


def _login(email, password):
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, r.text
    return s


def _put_creds(s, **overrides):
    body = {
        "api_token": "test_helcim_api_token_abcdef1234",
        "account_id": "TEST-99999",
        "webhook_verifier_token": "dGVzdHZlcmlmaWVydG9rZW4=",  # base64("testverifiertoken")
        "test_mode": True,
        **overrides,
    }
    return s.put(f"{API}/billing/helcim/settings", json=body, timeout=10)


# ---------------------------------------------------------------------------
# Credentials lifecycle
# ---------------------------------------------------------------------------

def test_settings_unconfigured_by_default():
    s = _login(*ADMIN)
    # Clean slate — delete any prior creds.
    s.delete(f"{API}/billing/helcim/settings", timeout=10)
    r = s.get(f"{API}/billing/helcim/settings", timeout=10)
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is False
    assert body["api_token_last4"] is None


def test_put_settings_round_trip_masks_secrets():
    s = _login(*ADMIN)
    r = _put_creds(s)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["configured"] is True
    assert body["test_mode"] is True
    assert body["account_id"] == "TEST-99999"
    # Public surface should expose only last4 — never the plaintext token.
    assert body["api_token_last4"] == "1234"
    assert "test_helcim_api_token_abcdef1234" not in r.text
    # Re-read.
    r2 = s.get(f"{API}/billing/helcim/settings", timeout=10).json()
    assert r2["api_token_last4"] == "1234"
    assert r2["updated_by"] == ADMIN[0]


def test_delete_settings_clears_state():
    s = _login(*ADMIN)
    _put_creds(s)
    r = s.delete(f"{API}/billing/helcim/settings", timeout=10)
    assert r.status_code == 204
    r2 = s.get(f"{API}/billing/helcim/settings", timeout=10).json()
    assert r2["configured"] is False


def test_non_admin_cannot_access_settings():
    s = requests.Session()
    s.post(f"{API}/auth/login",
           json={"email": "doctor@ccms.app",
                 "password": "Doctor@ComplianceClinic1"},
           timeout=15)
    r = s.get(f"{API}/billing/helcim/settings", timeout=10)
    assert r.status_code in (401, 403), r.text


# ---------------------------------------------------------------------------
# Webhook signature verifier — unit tests against the helper directly.
# ---------------------------------------------------------------------------

def _sig(verifier_token: str, webhook_id: str, ts: str, body: bytes) -> str:
    signed = f"{webhook_id}.{ts}".encode() + b"." + body
    raw = hmac.new(base64.b64decode(verifier_token), signed, hashlib.sha256).digest()
    return base64.b64encode(raw).decode()


def test_webhook_signature_valid_passes():
    from services.billing.helcim.webhook_verify import verify_signature
    verifier = "dGVzdHZlcmlmaWVydG9rZW4="  # base64-encoded
    body = b'{"eventType":"cardTransaction","transactionId":"123"}'
    ts = str(int(time.time()))
    wid = "wh_abc123"
    ok, err = verify_signature(
        verifier_token=verifier, webhook_id=wid, webhook_timestamp=ts,
        webhook_signature=_sig(verifier, wid, ts, body), body=body,
    )
    assert ok and err is None


def test_webhook_signature_tamper_rejected():
    from services.billing.helcim.webhook_verify import verify_signature
    verifier = "dGVzdHZlcmlmaWVydG9rZW4="
    body = b'{"eventType":"cardTransaction"}'
    tampered = b'{"eventType":"chargeback"}'
    ts = str(int(time.time()))
    wid = "wh_abc"
    sig = _sig(verifier, wid, ts, body)
    ok, err = verify_signature(
        verifier_token=verifier, webhook_id=wid, webhook_timestamp=ts,
        webhook_signature=sig, body=tampered,
    )
    assert not ok and "mismatch" in (err or "")


def test_webhook_skew_rejected():
    from services.billing.helcim.webhook_verify import verify_signature
    verifier = "dGVzdHZlcmlmaWVydG9rZW4="
    body = b'{}'
    stale_ts = str(int(time.time()) - 600)  # 10 min old
    wid = "wh_old"
    ok, err = verify_signature(
        verifier_token=verifier, webhook_id=wid, webhook_timestamp=stale_ts,
        webhook_signature=_sig(verifier, wid, stale_ts, body), body=body,
    )
    assert not ok and "skew" in (err or "")


def test_webhook_missing_headers_rejected():
    from services.billing.helcim.webhook_verify import verify_signature
    ok, err = verify_signature(
        verifier_token="dGVzdA==", webhook_id=None,
        webhook_timestamp=None, webhook_signature=None, body=b"{}",
    )
    assert not ok and "missing" in (err or "")


# ---------------------------------------------------------------------------
# Webhook receiver end-to-end — exercises full router path.
# ---------------------------------------------------------------------------

def test_webhook_endpoint_rejects_unconfigured_tenant():
    # No verifier token saved → 404.
    r = requests.post(
        f"{API}/billing/helcim/webhook/non-existent-tenant",
        json={"eventType": "cardTransaction"},
        headers={"webhook-id": "x", "webhook-timestamp": str(int(time.time())),
                 "webhook-signature": "abc"},
        timeout=10,
    )
    assert r.status_code == 404, r.text


def test_webhook_endpoint_rejects_bad_signature():
    s = _login(*ADMIN)
    _put_creds(s)
    r = s.get(f"{API}/auth/me", timeout=5).json()
    tenant_id = r["tenant_id"]
    # Send a bogus signature.
    body = b'{"eventType":"cardTransaction","transactionId":"99"}'
    ts = str(int(time.time()))
    r = requests.post(
        f"{API}/billing/helcim/webhook/{tenant_id}",
        data=body,
        headers={"content-type": "application/json",
                 "webhook-id": "wh_bogus", "webhook-timestamp": ts,
                 "webhook-signature": "totallybogus"},
        timeout=10,
    )
    assert r.status_code == 401, r.text


def test_webhook_endpoint_accepts_valid_signature_and_dedupes():
    s = _login(*ADMIN)
    _put_creds(s)
    me = s.get(f"{API}/auth/me", timeout=5).json()
    tenant_id = me["tenant_id"]
    verifier = "dGVzdHZlcmlmaWVydG9rZW4="
    body = b'{"eventType":"cardTransaction","transactionId":"42"}'
    ts = str(int(time.time()))
    wid = f"wh_test_{ts}"
    sig = _sig(verifier, wid, ts, body)
    r = requests.post(
        f"{API}/billing/helcim/webhook/{tenant_id}",
        data=body,
        headers={"content-type": "application/json",
                 "webhook-id": wid, "webhook-timestamp": ts,
                 "webhook-signature": sig},
        timeout=10,
    )
    assert r.status_code == 200, r.text
    assert r.json().get("ok") is True
    # Idempotent retry — same webhook id.
    r2 = requests.post(
        f"{API}/billing/helcim/webhook/{tenant_id}",
        data=body,
        headers={"content-type": "application/json",
                 "webhook-id": wid, "webhook-timestamp": ts,
                 "webhook-signature": sig},
        timeout=10,
    )
    assert r2.status_code == 200
    assert r2.json().get("duplicate") is True

    # Webhook log should expose the row.
    rows = s.get(f"{API}/billing/helcim/webhook-log", timeout=10).json()
    assert any(row.get("webhook_id") == wid for row in rows)


# ---------------------------------------------------------------------------
# Test connection — uses mocked httpx response.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_test_connection_with_mocked_helcim():
    """Direct unit-test against the HelcimClient bypassing the FastAPI app."""
    from services.billing.helcim.client import HelcimClient

    class _MockResponse:
        status_code = 200
        text = "{}"
        def json(self):
            return {"connection": "ok"}

    async def _mock_request(self, method, url, *, headers=None, json=None):
        return _MockResponse()

    with patch("httpx.AsyncClient.request", new=_mock_request):
        cli = HelcimClient("fake_token")
        res = await cli.connection_test()
        assert res.ok is True
        assert res["status_code"] == 200


@pytest.mark.asyncio
async def test_purchase_with_card_token_success():
    from services.billing.helcim.client import HelcimClient

    class _MockResponse:
        status_code = 200
        text = ""
        def json(self):
            return {"transaction": {"transactionId": 555,
                                    "status": "APPROVED",
                                    "amount": 50.0}}

    async def _mock_request(self, method, url, *, headers=None, json=None):
        # Confirm idempotency-key gets sent on payment ops.
        assert "idempotency-key" in headers
        assert json["cardData"]["cardToken"] == "card_xyz"
        return _MockResponse()

    with patch("httpx.AsyncClient.request", new=_mock_request):
        cli = HelcimClient("fake_token")
        res = await cli.purchase_with_card_token(
            amount=50.0, currency="USD", card_token="card_xyz",
            customer_code="cust_1",
        )
        assert res.ok
        assert res["data"]["transaction"]["status"] == "APPROVED"


@pytest.mark.asyncio
async def test_refund_partial_amount():
    from services.billing.helcim.client import HelcimClient

    captured = {}

    class _MockResponse:
        status_code = 200
        text = ""
        def json(self):
            return {"transaction": {"transactionId": 9001, "status": "APPROVED"}}

    async def _mock_request(self, method, url, *, headers=None, json=None):
        captured["url"] = url
        captured["json"] = json
        return _MockResponse()

    with patch("httpx.AsyncClient.request", new=_mock_request):
        cli = HelcimClient("fake_token")
        res = await cli.refund(transaction_id="123456", amount=12.34, comments="partial")
        assert res.ok
        assert captured["json"]["transactionId"] == 123456  # int conversion
        assert captured["json"]["amount"] == 12.34
        assert captured["url"].endswith("/payments/refund")
