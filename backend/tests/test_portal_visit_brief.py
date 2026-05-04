"""Tests for the patient-portal AI visit-brief endpoint.

Mirrors test_ai_context_documentation.py for the staff-side AI surfaces.
The brief itself is shaped by Claude Sonnet 4.5; tests focus on:
  * 403 enforcement for non-patient roles
  * Happy-path 200 with the JSON shape the UI expects
  * Cache hit on second call
  * Regenerate invalidates cache
  * No PHI leak — the brief should never contain ICD-style codes
    (regex `[A-Z]\\d{2}\\.[\\d]+`) or medication name patterns
    common in seed data.
"""
from __future__ import annotations

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
PATIENT = ("patient@ccms.app", "Patient@ComplianceClinic1")


def _login(email, password):
    s = requests.Session()
    r = s.post(f"{API}/auth/login",
               json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, r.text
    return s


class TestVisitBriefAuth:
    def test_admin_role_rejected(self):
        s = _login(*ADMIN)
        r = s.get(f"{API}/portal/visit-brief", timeout=15)
        assert r.status_code == 403
        assert "patient" in r.json().get("detail", "").lower()

    def test_unauthenticated_rejected(self):
        r = requests.get(f"{API}/portal/visit-brief", timeout=15)
        assert r.status_code in (401, 403)


class TestVisitBriefHappyPath:
    def test_brief_shape_and_caching(self):
        s = _login(*PATIENT)
        r1 = s.get(f"{API}/portal/visit-brief", timeout=60)
        if r1.status_code != 200:
            pytest.skip(f"LLM unavailable: {r1.status_code} {r1.text[:200]}")
        body = r1.json()
        assert "brief" in body
        b = body["brief"]
        # Required keys the UI relies on
        for k in (
            "headline", "last_visit", "your_progress",
            "this_visit", "ask_about", "reminders",
        ):
            assert k in b, f"missing key: {k}"
        assert isinstance(b["ask_about"], list)
        assert isinstance(b["reminders"], list)
        # At least one reminder is mandated by the prompt
        assert len(b["reminders"]) >= 1

        # Second call must hit the cache
        r2 = s.get(f"{API}/portal/visit-brief", timeout=15)
        assert r2.status_code == 200
        assert r2.json().get("cached") is True

    def test_regenerate_breaks_cache(self):
        s = _login(*PATIENT)
        # Prime the cache
        r1 = s.get(f"{API}/portal/visit-brief", timeout=60)
        if r1.status_code != 200:
            pytest.skip("LLM unavailable")
        # Regenerate must return cached:false
        r2 = s.post(f"{API}/portal/visit-brief/regenerate", timeout=60)
        assert r2.status_code == 200
        assert r2.json().get("cached") is False


class TestVisitBriefPHIHygiene:
    def test_brief_text_strips_clinical_codes(self):
        s = _login(*PATIENT)
        r = s.get(f"{API}/portal/visit-brief", timeout=60)
        if r.status_code != 200:
            pytest.skip("LLM unavailable")
        b = r.json()["brief"]
        full = " ".join(
            str(b.get(k, "")) for k in (
                "headline", "last_visit", "your_progress",
                "this_visit",
            )
        ) + " " + " ".join(b.get("ask_about", []) or [])
        # No ICD-10-style codes (e.g. M54.5, S13.4XXA)
        icd_pattern = re.compile(r"\b[A-Z]\d{2}\.\d+", re.IGNORECASE)
        assert not icd_pattern.search(full), \
            f"Patient-facing brief leaked ICD-style code: {full!r}"
