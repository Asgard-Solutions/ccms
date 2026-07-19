"""Tests for the third-wave AI features:
  • Send-to-claim (one-click claim creation from accepted CPT/ICD)
  • NL scheduling reschedule + cancel intents
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

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
PATIENT = ("patient@ccms.app", "Patient@ComplianceClinic1")


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
            {
                "_id": 0, "id": 1, "patient_id": 1, "status": 1,
                "encounter_id": 1, "date_of_service": 1,
            },
        )
        if n and n.get("status") == "signed":
            await db.clinical_follow_up_notes.update_one(
                {"id": n["id"]}, {"$set": {"status": "draft"}},
            )
        c.close()
        return n
    return asyncio.run(find())


def _payer_id():
    from motor.motor_asyncio import AsyncIOMotorClient
    from core.tenancy import reset_router_for_tests

    async def find():
        reset_router_for_tests()
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]
        u = await db.users.find_one(
            {"email": "doctor@ccms.app"}, {"_id": 0, "tenant_id": 1},
        )
        p = await db.billing_payers.find_one(
            {"tenant_id": u["tenant_id"]}, {"_id": 0, "id": 1},
        )
        c.close()
        return p["id"] if p else None
    return asyncio.run(find())


# ---------------------------------------------------------------------------
class TestSendToClaim:
    def test_doctor_role_required(self):
        s = _login(*STAFF)
        r = s.post(
            f"{API}/scribe/encounters/follow_up/x/send-to-claim",
            json={
                "cpt": [{"code": "98941"}], "icd": [{"code": "M54.5"}],
                "payer_id": "x",
            }, timeout=15,
        )
        assert r.status_code == 403

    def test_404_for_unknown_note(self):
        s = _login(*DOCTOR)
        payer = _payer_id()
        if not payer:
            pytest.skip("No payer in tenant")
        r = s.post(
            f"{API}/scribe/encounters/follow_up/does-not-exist/send-to-claim",
            json={
                "cpt": [{"code": "98941"}], "icd": [{"code": "M54.5"}],
                "payer_id": payer,
            }, timeout=15,
        )
        assert r.status_code == 404

    def test_422_when_no_codes(self):
        note = _doctor_draft_note()
        if not note:
            pytest.skip("No notes")
        payer = _payer_id()
        if not payer:
            pytest.skip("No payer")
        s = _login(*DOCTOR)
        r = s.post(
            f"{API}/scribe/encounters/follow_up/{note['id']}/send-to-claim",
            json={"cpt": [], "icd": [], "payer_id": payer}, timeout=15,
        )
        assert r.status_code == 422

    def test_404_for_unknown_payer(self):
        note = _doctor_draft_note()
        if not note:
            pytest.skip("No notes")
        s = _login(*DOCTOR)
        r = s.post(
            f"{API}/scribe/encounters/follow_up/{note['id']}/send-to-claim",
            json={
                "cpt": [{"code": "98941", "billed_cents": 7500}],
                "icd": [{"code": "M54.5", "is_primary": True}],
                "payer_id": "does-not-exist",
            }, timeout=15,
        )
        assert r.status_code == 404

    def test_happy_path_creates_claim(self):
        note = _doctor_draft_note()
        if not note:
            pytest.skip("No notes")
        payer = _payer_id()
        if not payer:
            pytest.skip("No payer")
        s = _login(*DOCTOR)
        r = s.post(
            f"{API}/scribe/encounters/follow_up/{note['id']}/send-to-claim",
            json={
                "cpt": [
                    {"code": "98941", "units": 1, "billed_cents": 7500},
                    {"code": "97140", "units": 1, "modifiers": ["59"], "billed_cents": 3500},
                ],
                "icd": [
                    {"code": "M54.5", "label": "Low back pain", "is_primary": True},
                    {"code": "M99.03", "label": "Lumbar segmental dysfunction"},
                ],
                "payer_id": payer,
            }, timeout=20,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "draft"
        assert body["lines"] >= 2
        assert body["diagnoses"] >= 2
        assert body["billed_cents"] == 7500 + 3500
        assert body.get("claim_id")

        # Verify it appears via the canonical claim-detail endpoint.
        d = s.get(
            f"{API}/billing/claims/{body['claim_id']}/detail", timeout=10,
        )
        assert d.status_code == 200
        detail = d.json()
        assert detail["claim"]["status"] == "draft"
        codes = sorted(ln["code"] for ln in detail.get("lines", []))
        assert "97140" in codes and "98941" in codes


# ---------------------------------------------------------------------------
def _new_appointment_for_test(s):
    """Helper: create a fresh appointment we can then reschedule/cancel."""
    # Find tenant patient + provider via auth/me
    me = s.get(f"{API}/auth/me", timeout=10).json()
    from motor.motor_asyncio import AsyncIOMotorClient
    from core.tenancy import reset_router_for_tests

    async def find():
        reset_router_for_tests()
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]
        p = await db.patients.find_one(
            {"tenant_id": me["tenant_id"]}, {"_id": 0, "id": 1},
        )
        u = await db.users.find_one(
            {"tenant_id": me["tenant_id"], "role": "doctor"},
            {"_id": 0, "id": 1},
        )
        c.close()
        return p, u
    p, u = asyncio.run(find())
    if not p or not u:
        return None
    start = (datetime.now(timezone.utc) + timedelta(days=7)).replace(
        hour=15, minute=0, second=0, microsecond=0,
    )
    end = start + timedelta(minutes=30)
    r = s.post(
        f"{API}/scheduling/nl/create",
        json={
            "patient_id": p["id"], "provider_id": u["id"],
            "start_iso": start.isoformat(),
            "duration_minutes": 30,
        }, timeout=15,
    )
    if r.status_code not in (200, 201):
        return None
    return r.json()


class TestNLReschedule:
    def test_404_for_unknown_appointment(self):
        s = _login(*DOCTOR)
        r = s.post(
            f"{API}/scheduling/nl/reschedule",
            json={
                "appointment_id": "does-not-exist",
                "start_iso": "2030-01-01T10:00:00",
                "duration_minutes": 30,
            }, timeout=15,
        )
        assert r.status_code == 404

    def test_422_for_bad_iso(self):
        s = _login(*DOCTOR)
        appt = _new_appointment_for_test(s)
        if not appt:
            pytest.skip("Couldn't seed appointment")
        try:
            r = s.post(
                f"{API}/scheduling/nl/reschedule",
                json={
                    "appointment_id": appt["id"],
                    "start_iso": "not-an-iso",
                }, timeout=10,
            )
            assert r.status_code == 422
        finally:
            # Clean up the seeded appointment.
            s.post(
                f"{API}/scheduling/nl/cancel",
                json={"appointment_id": appt["id"]}, timeout=10,
            )

    def test_happy_path_reschedules(self):
        s = _login(*DOCTOR)
        appt = _new_appointment_for_test(s)
        if not appt:
            pytest.skip("Couldn't seed an appointment")
        new_start = (datetime.now(timezone.utc) + timedelta(days=8)).replace(
            hour=10, minute=0, second=0, microsecond=0,
        )
        r = s.post(
            f"{API}/scheduling/nl/reschedule",
            json={
                "appointment_id": appt["id"],
                "start_iso": new_start.isoformat(),
                "duration_minutes": 45,
            }, timeout=15,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["id"] == appt["id"]


class TestNLCancel:
    def test_404_for_unknown_appointment(self):
        s = _login(*DOCTOR)
        r = s.post(
            f"{API}/scheduling/nl/cancel",
            json={"appointment_id": "does-not-exist"}, timeout=15,
        )
        assert r.status_code == 404

    def test_patient_role_rejected(self):
        s = _login(*PATIENT)
        r = s.post(
            f"{API}/scheduling/nl/cancel",
            json={"appointment_id": "x"}, timeout=10,
        )
        assert r.status_code == 403

    def test_happy_path_cancels(self):
        s = _login(*DOCTOR)
        appt = _new_appointment_for_test(s)
        if not appt:
            pytest.skip("Couldn't seed an appointment")
        r = s.post(
            f"{API}/scheduling/nl/cancel",
            json={"appointment_id": appt["id"], "cancel_reason": "test"},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        # Verify status flipped to cancelled.
        d = s.get(f"{API}/appointments/{appt['id']}", timeout=10)
        assert d.status_code == 200
        assert d.json().get("status") == "cancelled"


class TestNLParseTargetAppointment:
    def test_parse_includes_target_appointment_for_reschedule(self):
        s = _login(*DOCTOR)
        appt = _new_appointment_for_test(s)
        if not appt:
            pytest.skip("Couldn't seed appointment")
        # Look up patient name so we can build a realistic NL request.
        from motor.motor_asyncio import AsyncIOMotorClient
        from core.tenancy import reset_router_for_tests

        async def find_name():
            reset_router_for_tests()
            c = AsyncIOMotorClient(os.environ["MONGO_URL"])
            db = c[os.environ["DB_NAME"]]
            p = await db.patients.find_one(
                {"id": appt["patient_id"]}, {"_id": 0, "first_name": 1, "last_name": 1},
            )
            c.close()
            return p
        p = asyncio.run(find_name())
        nl = (
            f"Reschedule {p.get('first_name', '')} {p.get('last_name', '')}'s "
            f"upcoming appointment to next Friday at 11am"
        )
        r = s.post(
            f"{API}/scheduling/nl/parse",
            json={"text": nl, "timezone": "America/New_York"},
            timeout=60,
        )
        if r.status_code != 200:
            pytest.skip(f"LLM unavailable: {r.status_code}")
        body = r.json()
        assert body.get("intent") in ("reschedule", "create"), body
        if body.get("intent") == "reschedule":
            assert "target_appointment_id" in body
        # Cleanup the seeded appointment so subsequent runs aren't noisy.
        s.post(
            f"{API}/scheduling/nl/cancel",
            json={"appointment_id": appt["id"]}, timeout=10,
        )
