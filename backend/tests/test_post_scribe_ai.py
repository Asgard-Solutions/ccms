"""Tests for the post-scribe AI features:

  • CPT/ICD coding suggestions (`/api/scribe/.../coding-suggest`)
  • SOAP-template overrides (`/api/ai/templates`)
  • Natural-language semantic search (`/api/ai/search`)
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
    """Find a follow-up note in admin/doctor tenant; reset to draft if signed."""
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


# ---------------------------------------------------------------------------
class TestCodingSuggest:
    SAMPLE_DRAFTS = {
        "subjective": "34yo F with low back pain. NPRS down 7→4. Doing daily "
                      "McKenzie extensions and ice after long desk sessions.",
        "objective": "Lumbar ROM improving. Decreased QL muscle guarding "
                     "bilaterally. Treated with Diversified L4-L5 and right "
                     "SI joint, plus IASTM to bilateral QL for 8 minutes.",
        "assessment": "Lumbar segmental dysfunction, response to care positive.",
        "plan": "Continue 2x weekly for 2 weeks then re-assess.",
    }

    def test_doctor_role_required(self):
        s = _login(*STAFF)
        r = s.post(
            f"{API}/scribe/encounters/follow_up/x/coding-suggest",
            json={"drafts": self.SAMPLE_DRAFTS}, timeout=15,
        )
        assert r.status_code == 403

    def test_404_for_unknown_note(self):
        s = _login(*DOCTOR)
        r = s.post(
            f"{API}/scribe/encounters/follow_up/does-not-exist/coding-suggest",
            json={"drafts": self.SAMPLE_DRAFTS}, timeout=15,
        )
        assert r.status_code == 404

    def test_422_when_no_inputs(self):
        note = _doctor_draft_note()
        if not note:
            pytest.skip("No follow-up notes in doctor tenant")
        s = _login(*DOCTOR)
        r = s.post(
            f"{API}/scribe/encounters/follow_up/{note['id']}/coding-suggest",
            json={"drafts": {}}, timeout=15,
        )
        assert r.status_code == 422

    def test_happy_path_returns_cpt_and_icd(self):
        note = _doctor_draft_note()
        if not note:
            pytest.skip("No follow-up notes in doctor tenant")
        s = _login(*DOCTOR)
        r = s.post(
            f"{API}/scribe/encounters/follow_up/{note['id']}/coding-suggest",
            json={"drafts": self.SAMPLE_DRAFTS}, timeout=60,
        )
        if r.status_code != 200:
            pytest.skip(f"LLM unavailable: {r.status_code} {r.text[:200]}")
        body = r.json()
        for k in (
            "cpt_suggestions", "icd_suggestions", "documentation_warnings",
            "active_diagnoses", "model",
        ):
            assert k in body
        assert isinstance(body["cpt_suggestions"], list)
        # At least one CPT must be suggested for this rich SOAP draft.
        assert len(body["cpt_suggestions"]) >= 1
        # Each CPT must have a code, description, rationale, and confidence.
        for c in body["cpt_suggestions"]:
            assert c.get("code")
            assert c.get("description")
            assert c.get("confidence") in ("high", "medium", "low")
        # ICD list may be empty if no diagnosis terms are documented, but
        # this draft documents low back pain → at least one ICD expected.
        assert len(body["icd_suggestions"]) >= 1


# ---------------------------------------------------------------------------
class TestTemplateOverrides:
    def test_admin_only(self):
        s = _login(*DOCTOR)
        r = s.put(
            f"{API}/ai/templates",
            json={
                "scope_type": "tenant", "scope_id": None,
                "surface": "scribe_soap", "instructions": "x", "enabled": True,
            },
            timeout=10,
        )
        # Doctor can list (admin+doctor allowed) but cannot upsert.
        assert r.status_code == 403

    def test_upsert_list_delete_round_trip(self):
        s = _login(*ADMIN)
        # Upsert
        r = s.put(
            f"{API}/ai/templates",
            json={
                "scope_type": "tenant", "scope_id": None,
                "surface": "scribe_soap",
                "instructions": "Test override — please ignore",
                "enabled": True,
            },
            timeout=10,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["scope_type"] == "tenant"
        assert body["surface"] == "scribe_soap"

        # List
        r2 = s.get(f"{API}/ai/templates", timeout=10)
        assert r2.status_code == 200
        rows = r2.json().get("templates", [])
        assert any(
            row["scope_type"] == "tenant" and row["surface"] == "scribe_soap"
            for row in rows
        )

        # Delete
        me = s.get(f"{API}/auth/me", timeout=5).json()
        scope_id = me["tenant_id"]
        r3 = s.delete(
            f"{API}/ai/templates",
            params={
                "scope_type": "tenant", "scope_id": scope_id,
                "surface": "scribe_soap",
            },
            timeout=10,
        )
        assert r3.status_code == 200

    def test_delete_404_for_missing(self):
        s = _login(*ADMIN)
        r = s.delete(
            f"{API}/ai/templates",
            params={
                "scope_type": "tenant", "scope_id": "does-not-exist",
                "surface": "scribe_soap",
            },
            timeout=10,
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
class TestSemanticSearch:
    @classmethod
    def _patient_id(cls):
        """Resolve a patient that actually has clinical history. Hardcoding a
        UUID was brittle across reseeds — this discovers a viable subject in
        the live tenant DB so tests survive a fresh-Atlas seed."""
        if hasattr(cls, "_resolved_pid"):
            return cls._resolved_pid
        from motor.motor_asyncio import AsyncIOMotorClient
        from core.tenancy import reset_router_for_tests

        async def find():
            reset_router_for_tests()
            c = AsyncIOMotorClient(os.environ["MONGO_URL"])
            db = c[os.environ["DB_NAME"]]
            u = await db.users.find_one(
                {"email": "doctor@ccms.app"},
                {"_id": 0, "tenant_id": 1},
            )
            tid = u["tenant_id"]
            # Prefer a patient with at least one signed clinical artifact
            # so the snippet gather actually returns something.
            for coll in (
                "clinical_follow_up_notes",
                "clinical_initial_exams",
                "clinical_diagnoses",
            ):
                doc = await db[coll].find_one(
                    {"tenant_id": tid, "patient_id": {"$exists": True}},
                    {"_id": 0, "patient_id": 1},
                )
                if doc and doc.get("patient_id"):
                    c.close()
                    return doc["patient_id"]
            # Fallback: any patient in the tenant.
            p = await db.patients.find_one(
                {"tenant_id": tid}, {"_id": 0, "id": 1},
            )
            c.close()
            return p["id"] if p else None
        cls._resolved_pid = asyncio.run(find())
        return cls._resolved_pid

    @property
    def PATIENT_ID(self):
        return self._patient_id()

    def test_patient_role_rejected(self):
        s = _login(*PATIENT)
        r = s.post(
            f"{API}/ai/search",
            json={"patient_id": self.PATIENT_ID, "query": "low back pain"},
            timeout=15,
        )
        assert r.status_code == 403

    def test_short_query_validated(self):
        s = _login(*DOCTOR)
        r = s.post(
            f"{API}/ai/search",
            json={"patient_id": self.PATIENT_ID, "query": "x"},
            timeout=10,
        )
        assert r.status_code == 422

    def test_happy_path_returns_answer_and_results(self):
        s = _login(*DOCTOR)
        r = s.post(
            f"{API}/ai/search",
            json={
                "patient_id": self.PATIENT_ID,
                "query": "low back pain trend",
            },
            timeout=60,
        )
        if r.status_code != 200:
            pytest.skip(f"LLM unavailable: {r.status_code} {r.text[:200]}")
        body = r.json()
        assert "answer" in body
        assert isinstance(body.get("results"), list)
        # Snippet citations should reference [s#] tokens.
        if body["results"]:
            for r in body["results"]:
                assert r.get("snippet_id", "").startswith("s")
                assert 0.4 <= r["score"] <= 1.0
                assert r.get("kind")

    def test_cache_hit_on_repeat_query(self):
        s = _login(*DOCTOR)
        # Prime
        r1 = s.post(
            f"{API}/ai/search",
            json={
                "patient_id": self.PATIENT_ID,
                "query": "regression test cache hit query",
            },
            timeout=60,
        )
        if r1.status_code != 200:
            pytest.skip("LLM unavailable")
        # Repeat — should be cached
        r2 = s.post(
            f"{API}/ai/search",
            json={
                "patient_id": self.PATIENT_ID,
                "query": "regression test cache hit query",
            },
            timeout=15,
        )
        assert r2.status_code == 200
        assert r2.json().get("cached") is True
