"""Contract tests for Phase 2 Wave A grouped endpoints.

Guardrails proven here:

  * `schema_version` is present and pinned to "1.0".
  * Every group carries `source_ids` with the authoritative record ids.
  * Grouping uses `appointment_id` / `encounter_id` linkage — never
    timestamps alone.
  * Orphan records (appointment without encounter, note without
    encounter, etc.) surface as their own groups — never dropped.
  * The endpoints DO NOT mutate source records: `updated_at` on
    appointments / encounters / notes is identical before and after
    the read.
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
DEMO_PATIENT = "0601bbe4-251e-435d-8727-30ce68d1c8ee"


def _login():
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]}, timeout=30)
    assert r.status_code == 200, r.text
    return s


class TestGroupedEncountersContract:
    def test_schema_version_present(self):
        s = _login()
        r = s.get(f"{API}/patients/{DEMO_PATIENT}/clinical/encounters/grouped", timeout=30)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["schema_version"] == "1.0"
        assert isinstance(body["groups"], list)

    def test_every_group_has_source_ids(self):
        s = _login()
        r = s.get(f"{API}/patients/{DEMO_PATIENT}/clinical/encounters/grouped", timeout=30)
        body = r.json()
        for g in body["groups"]:
            assert "source_ids" in g
            sids = g["source_ids"]
            assert set(sids.keys()) >= {"appointment_id", "encounter_id", "note_ids", "billing_readiness_id"}
            # At least ONE authoritative id must be present — grouping is
            # never keyed on timestamps alone.
            has_any = bool(sids["appointment_id"] or sids["encounter_id"] or sids["note_ids"])
            assert has_any, f"group without any source id: {g}"

    def test_status_dimensions_are_present(self):
        s = _login()
        r = s.get(f"{API}/patients/{DEMO_PATIENT}/clinical/encounters/grouped", timeout=30)
        body = r.json()
        for g in body["groups"]:
            st = g["status"]
            for dim in ("workflow", "documentation", "clinical_response", "billing"):
                assert dim in st, f"{dim} missing from status"

    def test_source_records_are_not_mutated_by_read(self):
        """Fetch grouped, then fetch the underlying appointments +
        encounters + notes and assert their `updated_at` field is not
        modified by the grouped read."""
        s = _login()
        appts_before = s.get(f"{API}/patients/{DEMO_PATIENT}/appointments", timeout=15).json()
        encs_before = s.get(f"{API}/patients/{DEMO_PATIENT}/clinical/encounters", timeout=15).json()
        notes_before = s.get(f"{API}/patients/{DEMO_PATIENT}/clinical/notes", timeout=15).json()

        r = s.get(f"{API}/patients/{DEMO_PATIENT}/clinical/encounters/grouped", timeout=30)
        assert r.status_code == 200

        appts_after = s.get(f"{API}/patients/{DEMO_PATIENT}/appointments", timeout=15).json()
        encs_after = s.get(f"{API}/patients/{DEMO_PATIENT}/clinical/encounters", timeout=15).json()
        notes_after = s.get(f"{API}/patients/{DEMO_PATIENT}/clinical/notes", timeout=15).json()

        # No source row's `updated_at` changed between before and after.
        def _map(rows):
            return {r["id"]: r.get("updated_at") for r in (rows if isinstance(rows, list) else rows.get("items", []))}

        assert _map(appts_before) == _map(appts_after), "appointment updated_at changed"
        assert _map(encs_before) == _map(encs_after), "encounter updated_at changed"
        assert _map(notes_before) == _map(notes_after), "note updated_at changed"

    def test_source_records_are_not_omitted(self):
        """Every appointment id and every encounter id must resolve into
        at least one group's source_ids."""
        s = _login()
        appts = s.get(f"{API}/patients/{DEMO_PATIENT}/appointments", timeout=15).json()
        appt_rows = appts if isinstance(appts, list) else appts.get("items", [])
        encs = s.get(f"{API}/patients/{DEMO_PATIENT}/clinical/encounters", timeout=15).json()

        r = s.get(f"{API}/patients/{DEMO_PATIENT}/clinical/encounters/grouped", timeout=30)
        body = r.json()
        appt_ids_in_groups = {g["source_ids"]["appointment_id"] for g in body["groups"] if g["source_ids"]["appointment_id"]}
        enc_ids_in_groups = {g["source_ids"]["encounter_id"] for g in body["groups"] if g["source_ids"]["encounter_id"]}

        for a in appt_rows:
            assert a["id"] in appt_ids_in_groups, f"appointment {a['id']} dropped"
        for e in encs:
            assert e["id"] in enc_ids_in_groups, f"encounter {e['id']} dropped"


class TestGroupedTimelineContract:
    def test_schema_version_and_events_list(self):
        s = _login()
        r = s.get(f"{API}/patients/{DEMO_PATIENT}/clinical/timeline/grouped", timeout=30)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["schema_version"] == "1.0"
        assert isinstance(body["events"], list)

    def test_kind_filter_narrows_results(self):
        s = _login()
        all_r = s.get(f"{API}/patients/{DEMO_PATIENT}/clinical/timeline/grouped", timeout=30).json()
        vis_r = s.get(f"{API}/patients/{DEMO_PATIENT}/clinical/timeline/grouped?kinds=visit", timeout=30).json()
        assert all(e["kind"] == "visit" for e in vis_r["events"])
        assert len(vis_r["events"]) <= len(all_r["events"])

    def test_every_event_carries_source_ids(self):
        s = _login()
        body = s.get(f"{API}/patients/{DEMO_PATIENT}/clinical/timeline/grouped", timeout=30).json()
        for e in body["events"]:
            assert isinstance(e.get("source_ids"), dict) and any(e["source_ids"].values()), e

    def test_requires_auth(self):
        r = requests.get(f"{API}/patients/{DEMO_PATIENT}/clinical/timeline/grouped", timeout=15)
        assert r.status_code == 401
