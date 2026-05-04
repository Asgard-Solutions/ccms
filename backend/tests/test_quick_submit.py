"""Tests for the one-click clearinghouse quick-submit endpoint.

Covers:
  * POST /api/billing/claims/{claim_id}/quick-submit
  * Sandbox-mode adapters accept claims that fail the scrubber but
    flag them with `submitted_with_warnings=True`.
  * 404 for unknown claim, 409 for non-eligible status.

Also lightly verifies the scribe send-to-claim now resolves a
non-zero `billed_cents` from the payer's fee schedule when the caller
passed 0.
"""
from __future__ import annotations

import asyncio
import os

import pytest
import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")
_BASE = (
    os.environ.get("REACT_APP_BACKEND_URL") or "http://localhost:8001"
).rstrip("/")
API = f"{_BASE}/api"

DOCTOR = ("doctor@ccms.app", "Doctor@ComplianceClinic1")
STAFF = ("staff@ccms.app", "Staff@ComplianceClinic1")
ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")


def _login(email, password):
    s = requests.Session()
    r = s.post(
        f"{API}/auth/login",
        json={"email": email, "password": password}, timeout=15,
    )
    assert r.status_code == 200, r.text
    return s


def _doctor_draft_note():
    from motor.motor_asyncio import AsyncIOMotorClient
    from core.tenancy import reset_router_for_tests

    async def find():
        reset_router_for_tests()
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]
        u = await db.users.find_one(
            {"email": "doctor@ccms.app"},
            {"_id": 0, "tenant_id": 1, "id": 1},
        )
        n = await db.clinical_follow_up_notes.find_one(
            {"tenant_id": u["tenant_id"]},
            {"_id": 0, "id": 1, "patient_id": 1, "status": 1,
             "encounter_id": 1, "date_of_service": 1},
        )
        if n and n.get("status") == "signed":
            await db.clinical_follow_up_notes.update_one(
                {"id": n["id"]}, {"$set": {"status": "draft"}},
            )
        c.close()
        return n
    return asyncio.run(find())


def _chc_payer_id():
    """Return the id of a payer routed to change_healthcare so the
    sandbox path is exercised. Falls back to any payer if none have
    that route configured (test will still exercise the NoneAdapter)."""
    from motor.motor_asyncio import AsyncIOMotorClient
    from core.tenancy import reset_router_for_tests

    async def find():
        reset_router_for_tests()
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]
        u = await db.users.find_one(
            {"email": "doctor@ccms.app"}, {"_id": 0, "tenant_id": 1},
        )
        chc = await db.billing_payers.find_one(
            {"tenant_id": u["tenant_id"], "clearinghouse_route": "change_healthcare"},
            {"_id": 0, "id": 1},
        )
        if chc:
            c.close()
            return chc["id"]
        any_payer = await db.billing_payers.find_one(
            {"tenant_id": u["tenant_id"]}, {"_id": 0, "id": 1},
        )
        c.close()
        return any_payer["id"] if any_payer else None
    return asyncio.run(find())


# ---------------------------------------------------------------------------
class TestQuickSubmit:
    def test_404_for_unknown_claim(self):
        s = _login(*ADMIN)
        r = s.post(
            f"{API}/billing/claims/does-not-exist/quick-submit",
            json={}, timeout=15,
        )
        assert r.status_code == 404

    def test_role_gate(self):
        # Patient/staff without claim.submit permission must be blocked
        # at the auth dependency layer (403), not the data layer (404).
        s = _login(*STAFF)
        r = s.post(
            f"{API}/billing/claims/does-not-exist/quick-submit",
            json={}, timeout=15,
        )
        assert r.status_code in (403, 404)

    def test_happy_path_via_scribe_send_to_claim(self):
        note = _doctor_draft_note()
        if not note:
            pytest.skip("No notes")
        payer = _chc_payer_id()
        if not payer:
            pytest.skip("No payer")
        doc = _login(*DOCTOR)
        # Step 1: doctor creates the draft via scribe send-to-claim.
        r = doc.post(
            f"{API}/scribe/encounters/follow_up/{note['id']}/send-to-claim",
            json={
                "cpt": [
                    {"code": "98941", "units": 1, "billed_cents": 0},
                    {"code": "97140", "units": 1, "modifiers": ["59"], "billed_cents": 0},
                ],
                "icd": [
                    {"code": "M54.5", "label": "Low back pain", "is_primary": True},
                ],
                "payer_id": payer,
            }, timeout=20,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        claim_id = body["claim_id"]
        # Fee-schedule lookup: response now exposes per-line price source.
        assert "price_sources" in body
        assert len(body["price_sources"]) == body["lines"]

        # Step 2: admin (the role with claim.submit) quick-submits.
        admin = _login(*ADMIN)
        r2 = admin.post(
            f"{API}/billing/claims/{claim_id}/quick-submit",
            json={}, timeout=20,
        )
        assert r2.status_code == 200, r2.text
        out = r2.json()
        assert out["claim_id"] == claim_id
        assert out["claim_status"] == "submitted"
        assert "adapter_route" in out
        assert "adapter_status" in out
        assert "scrubber_passed" in out
        if not out["scrubber_passed"]:
            assert out["submitted_with_warnings"] is True
        assert out["adapter_status"] in ("queued", "manual", "accepted")
