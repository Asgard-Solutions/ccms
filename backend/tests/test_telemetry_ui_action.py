"""Integration tests for the narrowly-scoped CTA telemetry endpoint.

`POST /api/telemetry/ui-action` must:
  * accept every allow-listed action_slug (204 No Content),
  * reject arbitrary action_slug values (422),
  * reject unknown section_slug / source_surface / layout_version (422),
  * reject any extra field (422 `extra_forbidden`) — the PHI-leak guard,
  * reject missing required fields (422),
  * require an authenticated session (401 without cookies).

The schema is documented in `services/telemetry/SCHEMA.md`. Any change
to the allow-list must land in this test in the same PR.
"""
from __future__ import annotations

import os

import pytest
import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")

_BASE = (os.environ.get("REACT_APP_BACKEND_URL") or "http://localhost:8001").rstrip("/")
API = f"{_BASE}/api"

ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")

ALLOWED_ACTION_SLUGS = [
    "open-encounter",
    "add-note",
    "record-outcome",
    "schedule-visit",
    "schedule-reexam",
    "review-billing-issues",
    "edit-missing-information",
]


def _login(email: str, password: str) -> requests.Session:
    s = requests.Session()
    r = s.post(
        f"{API}/auth/login",
        json={"email": email, "password": password},
        timeout=30,
    )
    assert r.status_code == 200, r.text
    return s


def _valid_payload(action_slug: str = "schedule-reexam") -> dict:
    return {
        "event_name": "clinical_care_status_action_selected",
        "section_slug": "current-care-status",
        "action_slug": action_slug,
        "source_surface": "patient-clinical",
        "layout_version": "v2",
    }


class TestUIActionHappyPath:
    def test_every_allow_listed_slug_returns_204(self):
        s = _login(*ADMIN)
        for slug in ALLOWED_ACTION_SLUGS:
            r = s.post(f"{API}/telemetry/ui-action", json=_valid_payload(slug), timeout=15)
            assert r.status_code == 204, f"{slug}: {r.status_code} {r.text}"
            assert r.content == b"", f"{slug} returned body: {r.content!r}"

    def test_layout_version_v1_is_also_accepted(self):
        s = _login(*ADMIN)
        body = _valid_payload()
        body["layout_version"] = "v1"
        r = s.post(f"{API}/telemetry/ui-action", json=body, timeout=15)
        assert r.status_code == 204, r.text


class TestUIActionRejectsUnknownVocabulary:
    def test_arbitrary_action_slug_rejected(self):
        s = _login(*ADMIN)
        body = _valid_payload()
        body["action_slug"] = "delete-all-charts"
        r = s.post(f"{API}/telemetry/ui-action", json=body, timeout=15)
        assert r.status_code == 422, r.text
        assert "action_slug" in r.text

    def test_unknown_section_slug_rejected(self):
        s = _login(*ADMIN)
        body = _valid_payload()
        body["section_slug"] = "billing-detail"
        r = s.post(f"{API}/telemetry/ui-action", json=body, timeout=15)
        assert r.status_code == 422, r.text
        assert "section_slug" in r.text

    def test_unknown_source_surface_rejected(self):
        s = _login(*ADMIN)
        body = _valid_payload()
        body["source_surface"] = "billing-workspace"
        r = s.post(f"{API}/telemetry/ui-action", json=body, timeout=15)
        assert r.status_code == 422, r.text
        assert "source_surface" in r.text

    def test_unknown_layout_version_rejected(self):
        s = _login(*ADMIN)
        body = _valid_payload()
        body["layout_version"] = "v3-beta"
        r = s.post(f"{API}/telemetry/ui-action", json=body, timeout=15)
        assert r.status_code == 422, r.text
        assert "layout_version" in r.text

    def test_unknown_event_name_rejected(self):
        s = _login(*ADMIN)
        body = _valid_payload()
        body["event_name"] = "clinical_care_status_completed"
        r = s.post(f"{API}/telemetry/ui-action", json=body, timeout=15)
        assert r.status_code == 422, r.text


class TestUIActionRejectsPhiLeakAttempts:
    """Any field not in the allow-list must be rejected with
    422 `extra_forbidden`. This is our defence-in-depth guard against a
    misbehaving client shipping PHI into the telemetry pipeline."""

    @pytest.mark.parametrize(
        "leak_field, leak_value",
        [
            ("patient_id", "b99e7285-6efa-47b4-b552-2ad2920657dc"),
            ("patient_name", "John Q. Public"),
            ("encounter_id", "some-encounter-uuid"),
            ("diagnosis_code", "M54.2"),
            ("dob", "1985-04-12"),
            ("free_text", "positive fever"),
            ("url", "/patients/abc/clinical#history"),
        ],
    )
    def test_extra_fields_are_forbidden(self, leak_field, leak_value):
        s = _login(*ADMIN)
        body = _valid_payload()
        body[leak_field] = leak_value
        r = s.post(f"{API}/telemetry/ui-action", json=body, timeout=15)
        assert r.status_code == 422, f"{leak_field}: {r.status_code} {r.text}"
        assert "extra_forbidden" in r.text, r.text
        assert leak_field in r.text


class TestUIActionRejectsIncompletePayload:
    @pytest.mark.parametrize(
        "missing_field",
        ["event_name", "section_slug", "action_slug", "source_surface", "layout_version"],
    )
    def test_missing_required_field_returns_422(self, missing_field):
        s = _login(*ADMIN)
        body = _valid_payload()
        body.pop(missing_field)
        r = s.post(f"{API}/telemetry/ui-action", json=body, timeout=15)
        assert r.status_code == 422, r.text
        assert missing_field in r.text

    def test_empty_body_returns_422(self):
        s = _login(*ADMIN)
        r = s.post(f"{API}/telemetry/ui-action", json={}, timeout=15)
        assert r.status_code == 422, r.text


class TestUIActionRequiresAuth:
    def test_anonymous_request_rejected(self):
        # No login → no session cookie.
        r = requests.post(
            f"{API}/telemetry/ui-action",
            json=_valid_payload(),
            timeout=15,
        )
        assert r.status_code == 401, r.text
