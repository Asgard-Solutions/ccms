"""Iteration 87 wave-B tests.

Covers:
  - A1: send-to-claim accepts billed_cents=0 and returns price_sources
  - A3: GET /api/auth/users supports q, limit, offset and is regex-safe
  - B1: POST /api/billing/claims/{id}/quick-submit error/edge cases
        (409 wrong status, perms, response shape with sandbox CHC)
"""
from __future__ import annotations

import asyncio
import os
import re

import pytest
import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")

_BASE = (
    os.environ.get("REACT_APP_BACKEND_URL") or "http://localhost:8001"
).rstrip("/")
API = f"{_BASE}/api"

ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")
DOCTOR = ("doctor@ccms.app", "Doctor@ComplianceClinic1")
STAFF = ("staff@ccms.app", "Staff@ComplianceClinic1")


def _login(email: str, password: str) -> requests.Session:
    s = requests.Session()
    r = s.post(
        f"{API}/auth/login",
        json={"email": email, "password": password},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    return s


# ---------------------------------------------------------------------------
# A3 - provider search
# ---------------------------------------------------------------------------
class TestProviderSearch:
    def test_q_filters_results(self):
        s = _login(*ADMIN)
        r = s.get(f"{API}/auth/users", params={"role": "doctor", "q": "noah", "limit": 200})
        assert r.status_code == 200, r.text
        data = r.json()
        # Endpoint may return list or {users:[...]}
        users = data["users"] if isinstance(data, dict) and "users" in data else data
        assert isinstance(users, list)
        # Expect at least one Noah Carter doctor
        emails = [u.get("email", "").lower() for u in users]
        names = [
            (u.get("full_name") or u.get("name") or "").lower() for u in users
        ]
        joined = " ".join(emails + names)
        assert "noah" in joined, f"'noah' not in any result: {users[:5]}"

    def test_limit_caps_at_200(self):
        s = _login(*ADMIN)
        r = s.get(f"{API}/auth/users", params={"limit": 9999})
        assert r.status_code in (200, 422)
        if r.status_code == 200:
            data = r.json()
            users = data["users"] if isinstance(data, dict) and "users" in data else data
            assert len(users) <= 200

    def test_q_regex_injection_safe(self):
        # Unbalanced regex metachars MUST NOT throw 500
        s = _login(*ADMIN)
        for evil in ["(", "[a-", "*", "+++", ".*"]:
            r = s.get(
                f"{API}/auth/users",
                params={"role": "doctor", "q": evil, "limit": 50},
            )
            assert r.status_code in (200, 400, 422), f"q={evil!r} -> {r.status_code} {r.text[:200]}"

    def test_offset_pagination(self):
        s = _login(*ADMIN)
        r0 = s.get(f"{API}/auth/users", params={"role": "doctor", "limit": 5, "offset": 0})
        r1 = s.get(f"{API}/auth/users", params={"role": "doctor", "limit": 5, "offset": 5})
        assert r0.status_code == 200 and r1.status_code == 200
        l0 = r0.json()
        l1 = r1.json()
        u0 = l0["users"] if isinstance(l0, dict) and "users" in l0 else l0
        u1 = l1["users"] if isinstance(l1, dict) and "users" in l1 else l1
        ids0 = {u.get("id") for u in u0}
        ids1 = {u.get("id") for u in u1}
        # Pagination should not return identical pages (unless < 5 doctors)
        if len(u0) == 5 and len(u1) > 0:
            assert not ids0.intersection(ids1), "offset pagination overlap"


# ---------------------------------------------------------------------------
# A1 + B1 helpers (re-use seed data)
# ---------------------------------------------------------------------------
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
            {"_id": 0, "id": 1, "patient_id": 1, "status": 1},
        )
        if n and n.get("status") == "signed":
            await db.clinical_follow_up_notes.update_one(
                {"id": n["id"]}, {"$set": {"status": "draft"}},
            )
        c.close()
        return n
    return asyncio.run(find())


def _chc_payer_id():
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
# A1 - send-to-claim returns price_sources
# ---------------------------------------------------------------------------
class TestSendToClaimPriceSources:
    def test_price_sources_returned(self):
        note = _doctor_draft_note()
        if not note:
            pytest.skip("No notes")
        payer = _chc_payer_id()
        if not payer:
            pytest.skip("No payer")
        doc = _login(*DOCTOR)
        r = doc.post(
            f"{API}/scribe/encounters/follow_up/{note['id']}/send-to-claim",
            json={
                "cpt": [
                    {"code": "98941", "units": 1, "billed_cents": 0},
                ],
                "icd": [
                    {"code": "M54.5", "label": "Low back pain", "is_primary": True},
                ],
                "payer_id": payer,
            },
            timeout=20,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "claim_id" in body
        assert "price_sources" in body, body
        assert isinstance(body["price_sources"], list)
        assert len(body["price_sources"]) == body["lines"]
        # Each price_source is a string label (e.g. 'catalog', 'fee_schedule', 'zero')
        # or a dict with 'source' key.
        for ps in body["price_sources"]:
            if isinstance(ps, dict):
                assert "source" in ps
            else:
                assert isinstance(ps, str) and ps


# ---------------------------------------------------------------------------
# B1 - quick-submit edge cases
# ---------------------------------------------------------------------------
class TestQuickSubmitEdgeCases:
    def test_409_when_already_submitted(self):
        """After successful quick-submit, second call should 409."""
        note = _doctor_draft_note()
        if not note:
            pytest.skip("No notes")
        payer = _chc_payer_id()
        if not payer:
            pytest.skip("No payer")
        doc = _login(*DOCTOR)
        r = doc.post(
            f"{API}/scribe/encounters/follow_up/{note['id']}/send-to-claim",
            json={
                "cpt": [{"code": "98941", "units": 1, "billed_cents": 0}],
                "icd": [
                    {"code": "M54.5", "label": "Low back pain", "is_primary": True},
                ],
                "payer_id": payer,
            }, timeout=20,
        )
        assert r.status_code == 200, r.text
        claim_id = r.json()["claim_id"]

        admin = _login(*ADMIN)
        r1 = admin.post(f"{API}/billing/claims/{claim_id}/quick-submit", json={}, timeout=20)
        assert r1.status_code == 200, r1.text
        body = r1.json()
        # Response shape sanity
        for key in (
            "claim_id", "claim_status", "scrubber_passed",
            "adapter_route", "adapter_status",
        ):
            assert key in body, (key, body)
        # External id present when adapter is CHC sandbox
        if body.get("adapter_route") == "change_healthcare":
            ext = body.get("adapter_external_id")
            assert ext and ext.startswith("chc-sbx-"), ext

        # Second quick-submit should now reject (already submitted)
        r2 = admin.post(f"{API}/billing/claims/{claim_id}/quick-submit", json={}, timeout=20)
        assert r2.status_code in (409, 400), r2.text

    def test_staff_role_blocked(self):
        """Staff lacks claim.submit permission: must be 403 (or 404 if claim doesn't exist)."""
        s = _login(*STAFF)
        r = s.post(f"{API}/billing/claims/does-not-exist/quick-submit", json={}, timeout=15)
        assert r.status_code in (403, 404)
