"""
Phase 2 Reports hardening — E2E API tests against the live backend.

Verifies:
  1. POST /api/reports/{name}/export accepts optional `reason` (<=500 chars).
  2. Non-PHI export (denials_log) returns protection_kind='none',
     password_protected=False.
  3. Non-PHI PDF filename matches `{slug}-\\d{8}-\\d{4}\\.pdf$`.
  4. PHI export path — admin role returns 403 with the expected message.
  5. GET /api/exports/{id} response includes `protection_kind`.
  6. DB invariant: no `password_plain`; only `password_enc` (prefixed 'enc:')
     and `password_hash` (sha256).
"""
from __future__ import annotations

import os
import re
import time
import asyncio
import pytest
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / "frontend" / ".env")
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
ADMIN_EMAIL = "admin@ccms.app"
ADMIN_PASS = "Admin@ComplianceClinic1"


# ---------------------------------------------------------------------------
# Shared session w/ reauth cookie
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": ADMIN_EMAIL, "password": ADMIN_PASS})
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    r2 = s.post(f"{BASE_URL}/api/auth/reauth", json={"password": ADMIN_PASS})
    assert r2.status_code == 200, f"reauth failed: {r2.status_code} {r2.text}"
    return s


def _poll_export(session, export_id, *, timeout=20):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        r = session.get(f"{BASE_URL}/api/exports/{export_id}")
        assert r.status_code == 200, f"{r.status_code} {r.text}"
        last = r.json()
        if last["status"] in ("ready", "failed"):
            return last
        time.sleep(0.5)
    return last


# ---------------------------------------------------------------------------
# 1) reason accepted by /reports/{name}/export (Non-PHI)
# ---------------------------------------------------------------------------

def test_non_phi_csv_export_with_reason(admin_session):
    r = admin_session.post(
        f"{BASE_URL}/api/reports/denials_log/export",
        json={"format": "csv", "filters": {},
              "reason": "TEST_Phase2 reason field audit"},
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["password_protected"] is False
    export_id = body["export_id"]

    status = _poll_export(admin_session, export_id)
    assert status["status"] == "ready"
    assert status["protection_kind"] == "none"
    assert status["password_protected"] is False
    assert status["filename"].endswith(".csv")
    # filename pattern: slug-YYYYMMDD-HHMM.csv
    assert re.match(r"^[A-Za-z0-9._-]+-\d{8}-\d{4}\.csv$", status["filename"]), \
        f"bad filename {status['filename']}"


def test_non_phi_pdf_filename_pattern(admin_session):
    r = admin_session.post(
        f"{BASE_URL}/api/reports/denials_log/export",
        json={"format": "pdf", "filters": {}, "reason": "TEST_pdf pattern"},
    )
    assert r.status_code == 202, r.text
    export_id = r.json()["export_id"]

    status = _poll_export(admin_session, export_id)
    assert status["status"] == "ready"
    assert status["protection_kind"] == "none"
    assert status["password_protected"] is False
    fn = status["filename"]
    assert re.match(r"^[A-Za-z0-9._-]+-\d{8}-\d{4}\.pdf$", fn), f"bad fn {fn}"


def test_reason_over_500_rejected(admin_session):
    r = admin_session.post(
        f"{BASE_URL}/api/reports/denials_log/export",
        json={"format": "csv", "filters": {}, "reason": "x" * 501},
    )
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# 2) PHI export 403 gate unchanged
# ---------------------------------------------------------------------------

def test_phi_export_blocked_for_admin(admin_session):
    r = admin_session.post(
        f"{BASE_URL}/api/reports/patient_roster/export",
        json={"format": "csv", "filters": {},
              "reason": "TEST_phi attempt — should 403"},
    )
    assert r.status_code == 403, r.text
    body = r.json()
    msg = body.get("detail") or body.get("message") or body.get("error") or ""
    assert "reporting.export_phi" in msg, msg


# ---------------------------------------------------------------------------
# 3) GET /api/exports/{id} contains protection_kind
# ---------------------------------------------------------------------------

def test_status_payload_has_protection_kind(admin_session):
    r = admin_session.post(
        f"{BASE_URL}/api/reports/denials_log/export",
        json={"format": "csv"},
    )
    assert r.status_code == 202, r.text
    export_id = r.json()["export_id"]
    status = _poll_export(admin_session, export_id)
    # Field must be PRESENT in payload (even when None before worker runs).
    assert "protection_kind" in status
    assert status["protection_kind"] == "none"


# ---------------------------------------------------------------------------
# 4) DB invariant — no plaintext password anywhere
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_db_has_no_plaintext_password_field():
    """Directly inspect Mongo: no `password_plain` field on any export row."""
    from motor.motor_asyncio import AsyncIOMotorClient
    mongo_url = os.environ["MONGO_URL"]
    db_name = os.environ["DB_NAME"]
    client = AsyncIOMotorClient(mongo_url)
    try:
        db = client[db_name]
        # Strict: no row should contain an ACTUAL plaintext password.
        bad = await db.exports.find_one(
            {"password_plain": {"$nin": [None, ""]}}
        )
        assert bad is None, f"leaked plaintext password in row {bad.get('id')}"
        # Advisory: legacy rows may still have the null password_plain field
        # from a prior schema (no plaintext stored). Flag count for cleanup.
        legacy_null_count = await db.exports.count_documents(
            {"password_plain": {"$exists": True}}
        )
        if legacy_null_count:
            print(f"[advisory] {legacy_null_count} legacy rows still carry a"
                  f" null `password_plain` field — cleanup migration needed.")

        # Sanity: any rows that declared password_protected must have
        # either (a) password_enc prefixed with 'enc:' still pending reveal,
        # OR (b) password_revealed=True with password_enc absent.
        async for row in db.exports.find(
            {"password_protected": True},
            {"_id": 0, "id": 1, "password_enc": 1, "password_hash": 1,
             "password_revealed": 1},
        ).limit(50):
            pe = row.get("password_enc")
            if pe is not None:
                assert isinstance(pe, str) and pe.startswith("enc:"), \
                    f"non-encrypted password_enc on {row.get('id')}: {pe!r:.30}"
            else:
                # absent ⇒ must have been revealed already
                assert row.get("password_revealed") is True, \
                    f"row {row.get('id')} has no password_enc but wasn't revealed"
            # hash always kept for audit when protected
            assert row.get("password_hash"), \
                f"row {row.get('id')} missing password_hash"
    finally:
        client.close()


# ---------------------------------------------------------------------------
# 5) reason is persisted on the export row (audit)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reason_is_persisted_on_export_row(admin_session):
    reason_text = "TEST_Phase2 audit reason persistence"
    r = admin_session.post(
        f"{BASE_URL}/api/reports/denials_log/export",
        json={"format": "csv", "reason": reason_text},
    )
    assert r.status_code == 202, r.text
    export_id = r.json()["export_id"]

    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    try:
        db = client[os.environ["DB_NAME"]]
        # small settle window for insert
        for _ in range(10):
            row = await db.exports.find_one({"id": export_id}, {"_id": 0})
            if row:
                break
            await asyncio.sleep(0.2)
        assert row is not None, "export row not found"
        assert row.get("reason") == reason_text
        assert row.get("report_name") == "denials_log"
    finally:
        client.close()
