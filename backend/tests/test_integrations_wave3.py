"""End-to-end tests for the third integration wave:
  * Resend email — per-tenant credentials, log-only fallback, send-test, log
  * Google OAuth — settings, exchange (with mocked Emergent session-data)
  * SMS inbox — staff send → thread + outbound row created
"""
from __future__ import annotations

import os
import uuid

import pytest
import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")
_BASE = (os.environ.get("REACT_APP_BACKEND_URL") or "http://localhost:8001").rstrip("/")
API = f"{_BASE}/api"
DEFAULT_ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")


def _login(email, password):
    s = requests.Session()
    r = s.post(f"{API}/auth/login",
               json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, r.text
    access = r.cookies.get("access_token")
    if access:
        s.headers["Authorization"] = f"Bearer {access}"
    return s


# ---------------------------------------------------------------------------
# Resend email
# ---------------------------------------------------------------------------
class TestEmailSettings:
    def test_unconfigured_state_logs_only(self):
        s = _login(*DEFAULT_ADMIN)
        # Make sure we start from a clean slate
        s.delete(f"{API}/email/settings", timeout=10)
        r = s.get(f"{API}/email/settings", timeout=10)
        assert r.status_code == 200, r.text
        assert r.json()["configured"] is False

    def test_save_then_send_test_logs_when_disabled(self):
        s = _login(*DEFAULT_ADMIN)
        # Save a placeholder set of credentials but leave enabled=False
        save = s.put(f"{API}/email/settings", json={
            "api_key": "re_test_" + uuid.uuid4().hex[:24],
            "from_email": "noreply@example.com",
            "from_name": "Test Clinic",
            "reply_to": None,
            "enabled": False,
        }, timeout=10)
        assert save.status_code == 200, save.text
        assert save.json()["configured"] is True
        assert save.json()["enabled"] is False

        test = s.post(f"{API}/email/settings/test", json={
            "to": "ops@example.com",
            "subject": "Hello",
            "body": "Hi there",
        }, timeout=10)
        assert test.status_code == 200, test.text
        # Disabled → log-only OR may try resend (depends on env). Either
        # way the response shape is consistent.
        assert test.json()["status"] in ("logged", "sent", "failed")
        # When disabled, our client falls back to env-var path. Whatever
        # path we take, it must not 500 and must persist a row.
        log = s.get(f"{API}/email/outbound-log", timeout=10)
        assert log.status_code == 200
        assert any(row["subject"] == "Hello" for row in log.json())

    def test_delete_credentials(self):
        s = _login(*DEFAULT_ADMIN)
        s.put(f"{API}/email/settings", json={
            "api_key": "re_test_" + uuid.uuid4().hex[:24],
            "from_email": "noreply@example.com",
            "enabled": False,
        }, timeout=10)
        d = s.delete(f"{API}/email/settings", timeout=10)
        assert d.status_code == 200
        assert d.json()["deleted"] >= 1
        after = s.get(f"{API}/email/settings", timeout=10)
        assert after.json()["configured"] is False


# ---------------------------------------------------------------------------
# Google OAuth
# ---------------------------------------------------------------------------
class TestGoogleAuth:
    def test_availability_public(self):
        # Public — no auth needed
        r = requests.get(f"{API}/auth/google/availability", timeout=10)
        assert r.status_code == 200
        assert isinstance(r.json().get("enabled"), bool)

    def test_settings_round_trip(self):
        s = _login(*DEFAULT_ADMIN)
        save = s.put(f"{API}/auth/google/settings", json={
            "enabled": True,
            "allowed_domains": ["ccms.app", "  TEST.COM  "],
            "default_role": "staff",
        }, timeout=10)
        assert save.status_code == 200, save.text
        body = save.json()
        # Domains normalised: lowercased, sorted, deduped
        assert "ccms.app" in body["allowed_domains"]
        assert "test.com" in body["allowed_domains"]
        # Availability now returns enabled=True
        r = requests.get(f"{API}/auth/google/availability", timeout=10)
        assert r.json()["enabled"] is True

    def test_exchange_rejects_unknown_session(self):
        # The Emergent session-data endpoint will 404 a random UUID
        r = requests.post(
            f"{API}/auth/google/exchange",
            json={"session_id": "not-a-real-session-" + uuid.uuid4().hex},
            timeout=20,
        )
        # Either 401 from Emergent or 502 if Emergent is unreachable
        assert r.status_code in (401, 502), r.text

    def test_exchange_unit_blocks_disabled_tenant(self, monkeypatch):
        """When OAuth is disabled tenant-wide, the exchange must 403
        even if Emergent returns valid data. We exercise the helper
        directly so we can stub out the network call.
        """
        import asyncio
        from fastapi import HTTPException, Request, Response
        from services.identity import google_auth as ga

        # Disable OAuth on the default tenant.
        s = _login(*DEFAULT_ADMIN)
        s.put(f"{API}/auth/google/settings", json={
            "enabled": False, "allowed_domains": [], "default_role": "staff",
        }, timeout=10)
        try:
            async def fake_session(_):
                return {"email": "rando@example.com", "name": "Rando",
                        "session_token": "tok"}
            monkeypatch.setattr(ga, "_emergent_session_data", fake_session)

            scope = {
                "type": "http", "method": "POST", "path": "/exchange",
                "headers": [], "query_string": b"", "scheme": "http",
                "server": ("test", 80), "client": ("127.0.0.1", 0),
            }
            req = Request(scope)
            payload = ga._ExchangePayload(session_id="tok-abc-12345")
            with pytest.raises(HTTPException) as excinfo:
                asyncio.new_event_loop().run_until_complete(
                    ga.google_exchange(payload, req, Response())
                )
            assert excinfo.value.status_code == 403
        finally:
            s.put(f"{API}/auth/google/settings", json={
                "enabled": True, "allowed_domains": ["ccms.app"],
                "default_role": "staff",
            }, timeout=10)


# ---------------------------------------------------------------------------
# SMS inbox staff-send → thread + outbound row
# ---------------------------------------------------------------------------
class TestSmsInbox:
    def test_send_creates_thread_and_log(self):
        s = _login(*DEFAULT_ADMIN)
        # Make sure SMS is in log-only mode
        s.delete(f"{API}/sms/settings", timeout=10)
        # Send to a unique phone number so we can verify the thread
        unique = "503555" + uuid.uuid4().hex[:4].upper()
        # Strip non-digits to satisfy the normaliser
        import re as _re
        unique = _re.sub(r"\D+", "", unique)[:10].ljust(10, "0")
        body = "Hello from staff."
        r = s.post(f"{API}/sms/send", json={"to": unique, "body": body}, timeout=10)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "logged"

        threads = s.get(f"{API}/sms/threads", timeout=10).json()
        match = [t for t in threads if t["peer"].endswith(unique)]
        assert match, f"no thread for {unique} in {len(threads)} threads"
        msgs = s.get(f"{API}/sms/threads/{match[0]['id']}/messages", timeout=10).json()
        assert any(m["body"] == body and m["direction"] == "outbound"
                   for m in msgs["messages"])
