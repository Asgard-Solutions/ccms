"""
Notification helpers — email (Resend), SMS (Twilio Messages),
OTP (Twilio Verify). Tests cover both no-credentials fallback and
credentialled-but-mocked real-send paths.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    """Start every test from a clean notification-env state."""
    for k in (
        "RESEND_API_KEY", "SENDER_EMAIL",
        "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN",
        "TWILIO_FROM_NUMBER", "TWILIO_VERIFY_SERVICE_SID",
    ):
        monkeypatch.delenv(k, raising=False)


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_email_log_only_when_no_credentials():
    from services.notifications import email as email_mod
    assert email_mod.is_live() is False
    res = await email_mod.send_email(
        to="user@example.com",
        subject="Hello",
        html_body="<p>hi</p>",
        event_type="test",
    )
    assert res["ok"] is True
    assert res["provider"] == "log-only"
    assert res["message_id"].startswith("log-")


@pytest.mark.asyncio
async def test_email_resend_path(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("SENDER_EMAIL", "from@example.com")
    from services.notifications import email as email_mod
    assert email_mod.is_live() is True

    fake_resend = MagicMock()
    fake_resend.Emails.send.return_value = {"id": "abc123"}
    with patch.object(email_mod, "_resend", return_value=fake_resend):
        res = await email_mod.send_email(
            to="user@example.com", subject="Hi", html_body="<p>h</p>",
            event_type="test",
        )
    assert res["ok"] is True
    assert res["provider"] == "resend"
    assert res["message_id"] == "abc123"
    fake_resend.Emails.send.assert_called_once()


@pytest.mark.asyncio
async def test_email_resend_swallows_errors(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("SENDER_EMAIL", "from@example.com")
    from services.notifications import email as email_mod

    fake_resend = MagicMock()
    fake_resend.Emails.send.side_effect = Exception("boom")
    with patch.object(email_mod, "_resend", return_value=fake_resend):
        res = await email_mod.send_email(
            to="user@example.com", subject="Hi", html_body="<p>h</p>",
        )
    assert res["ok"] is False
    assert "boom" in res["error"]


def test_redact_email():
    from services.notifications.email import _redact_email
    assert _redact_email("jane@example.com") == "j**e@example.com"
    assert _redact_email("jo@x.com") == "j***@x.com"
    assert _redact_email("") == "<invalid>"


# ---------------------------------------------------------------------------
# SMS
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sms_log_only_when_no_credentials():
    from services.notifications import sms as sms_mod
    assert sms_mod.is_live() is False
    res = await sms_mod.send_sms(to="+15551234567", body="Your code: 123456")
    assert res["ok"] is True
    assert res["provider"] == "log-only"
    assert res["message_sid"].startswith("log-")


@pytest.mark.asyncio
async def test_sms_twilio_path(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15550001111")
    from services.notifications import sms as sms_mod
    assert sms_mod.is_live() is True

    fake_client = MagicMock()
    fake_client.messages.create.return_value = MagicMock(
        sid="SM12345", status="queued",
    )
    with patch("twilio.rest.Client", return_value=fake_client):
        res = await sms_mod.send_sms(to="+15557654321", body="Hi")
    assert res["ok"] is True
    assert res["provider"] == "twilio-messages"
    assert res["message_sid"] == "SM12345"


@pytest.mark.asyncio
async def test_sms_twilio_error(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15550001111")
    from services.notifications import sms as sms_mod

    fake_client = MagicMock()
    fake_client.messages.create.side_effect = Exception("twilio-boom")
    with patch("twilio.rest.Client", return_value=fake_client):
        res = await sms_mod.send_sms(to="+15557654321", body="x")
    assert res["ok"] is False
    assert "twilio-boom" in res["error"]


def test_redact_phone():
    from services.notifications.sms import _redact_phone
    assert _redact_phone("+15557654321") == "*******4321"
    assert _redact_phone("") == "<invalid>"


# ---------------------------------------------------------------------------
# Verify (Twilio Verify API)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_verify_log_only_start_and_check_valid():
    from services.notifications import verify as verify_mod
    assert verify_mod.is_live() is False
    start = await verify_mod.start_verification(to="+15557654321")
    assert start["ok"] is True
    assert start["provider"] == "log-only"

    # Dev-mode accepts any 4–10 digit code.
    check = await verify_mod.check_code(to="+15557654321", code="123456")
    assert check["ok"] is True
    assert check["valid"] is True

    # Non-numeric → invalid.
    bad = await verify_mod.check_code(to="+15557654321", code="abcdef")
    assert bad["valid"] is False


@pytest.mark.asyncio
async def test_verify_twilio_start_path(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setenv("TWILIO_VERIFY_SERVICE_SID", "VAtest")
    from services.notifications import verify as verify_mod
    assert verify_mod.is_live() is True

    fake_client = MagicMock()
    fake_client.verify.v2.services.return_value.verifications.create.return_value = MagicMock(
        sid="VSM1", status="pending",
    )
    with patch("twilio.rest.Client", return_value=fake_client):
        res = await verify_mod.start_verification(to="+15557654321")
    assert res["ok"] is True
    assert res["provider"] == "twilio-verify"
    assert res["status"] == "pending"


@pytest.mark.asyncio
async def test_verify_twilio_check_approved(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setenv("TWILIO_VERIFY_SERVICE_SID", "VAtest")
    from services.notifications import verify as verify_mod

    fake_client = MagicMock()
    fake_client.verify.v2.services.return_value.verification_checks.create.return_value = MagicMock(
        status="approved",
    )
    with patch("twilio.rest.Client", return_value=fake_client):
        res = await verify_mod.check_code(to="+15557654321", code="999111")
    assert res["valid"] is True
    assert res["status"] == "approved"
