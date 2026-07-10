"""Contract tests for Phase 3 Slice 2 timeline filter surface.

Guarantees under test:
  * Backward compatibility — `?kinds=` legacy alias still works and the
    unfiltered response still ships as schema_version 1.0.
  * Any filter application bumps schema_version to 1.1 and adds
    `filter_meta` with `applied` / `ignored_*` sub-objects.
  * Unknown slugs (kinds, sources, date_window) are dropped, not 400s —
    they surface in `filter_meta.ignored_slugs` so the UI can prompt
    for stale-preset repair.
  * Permission-aware provider filter: any provider id the caller can't
    see is dropped and echoed in `ignored_provider_ids`.
  * Episode filter drops ids that don't belong to this patient.
  * `q` param is bounded to 80 chars and does NOT leak into any saved
    preset (durable prefs enforce this separately).
"""
from __future__ import annotations

import os

import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")

_BASE = (os.environ.get("REACT_APP_BACKEND_URL") or "http://localhost:8001").rstrip("/")
API = f"{_BASE}/api"

ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")
DOCTOR = ("doctor@ccms.app", "Doctor@ComplianceClinic1")

# Isabella Cho — MVA episode with rich timeline.
ISABELLA = "d41b48bc-13d7-45b1-baae-4ccf8aa253f9"


def _login(email: str, password: str) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=30)
    assert r.status_code == 200, r.text
    return s


class TestBackwardCompatibility:
    def test_no_params_returns_v10_no_filter_meta(self):
        s = _login(*DOCTOR)
        r = s.get(f"{API}/patients/{ISABELLA}/clinical/timeline/grouped", timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["schema_version"] == "1.0"
        assert "events" in body
        assert "filter_meta" not in body

    def test_legacy_kinds_alias_still_works(self):
        s = _login(*DOCTOR)
        r = s.get(
            f"{API}/patients/{ISABELLA}/clinical/timeline/grouped",
            params={"kinds": "visit"}, timeout=15,
        )
        assert r.status_code == 200
        body = r.json()
        # Applying a filter — response now 1.1
        assert body["schema_version"] == "1.1"
        assert set(body["filter_meta"]["applied"]["event_kinds"]) == {"visit"}
        assert all(e["kind"] == "visit" for e in body["events"])


class TestFilterSurface:
    def test_event_kinds_filter(self):
        s = _login(*DOCTOR)
        r = s.get(
            f"{API}/patients/{ISABELLA}/clinical/timeline/grouped",
            params={"event_kinds": "outcome_entry,clinical_media"}, timeout=15,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["schema_version"] == "1.1"
        for e in body["events"]:
            assert e["kind"] in {"outcome_entry", "clinical_media"}
        assert set(body["filter_meta"]["applied"]["event_kinds"]) == {
            "outcome_entry", "clinical_media",
        }

    def test_unknown_kind_ignored_not_errored(self):
        s = _login(*DOCTOR)
        r = s.get(
            f"{API}/patients/{ISABELLA}/clinical/timeline/grouped",
            params={"event_kinds": "visit,pizza"}, timeout=15,
        )
        assert r.status_code == 200
        assert "pizza" in r.json()["filter_meta"]["ignored_slugs"]

    def test_unknown_date_window_ignored(self):
        s = _login(*DOCTOR)
        r = s.get(
            f"{API}/patients/{ISABELLA}/clinical/timeline/grouped",
            params={"date_window": "since_dawn_of_time"}, timeout=15,
        )
        assert r.status_code == 200
        body = r.json()
        assert "since_dawn_of_time" in body["filter_meta"]["ignored_slugs"]
        assert body["filter_meta"]["applied"]["date_window"] is None

    def test_bogus_provider_id_flows_into_ignored(self):
        s = _login(*DOCTOR)
        r = s.get(
            f"{API}/patients/{ISABELLA}/clinical/timeline/grouped",
            params={"provider_ids": "not-a-real-provider-uuid"}, timeout=15,
        )
        assert r.status_code == 200
        body = r.json()
        assert "not-a-real-provider-uuid" in body["filter_meta"]["ignored_provider_ids"]
        assert body["filter_meta"]["applied"]["provider_ids"] == []

    def test_episode_id_from_another_patient_ignored(self):
        s = _login(*DOCTOR)
        # Fetch Ethan's episode to pass into Isabella's timeline query.
        r = s.get(
            f"{API}/patients/eaa6ad75-2389-4e67-8530-98170ad21af2/clinical/episodes",
            timeout=15,
        )
        assert r.status_code == 200
        other_eps = r.json()
        if not other_eps:
            return
        stolen = other_eps[0]["id"]
        r = s.get(
            f"{API}/patients/{ISABELLA}/clinical/timeline/grouped",
            params={"episode_ids": stolen}, timeout=15,
        )
        assert r.status_code == 200
        body = r.json()
        assert stolen in body["filter_meta"]["ignored_episode_ids"]

    def test_date_window_last_30d_narrows_results(self):
        s = _login(*DOCTOR)
        r_all = s.get(
            f"{API}/patients/{ISABELLA}/clinical/timeline/grouped",
            timeout=15,
        ).json()
        r_30 = s.get(
            f"{API}/patients/{ISABELLA}/clinical/timeline/grouped",
            params={"date_window": "last_30d"}, timeout=15,
        ).json()
        assert r_30["schema_version"] == "1.1"
        assert len(r_30["events"]) <= len(r_all["events"])
        assert r_30["filter_meta"]["applied"]["date_window"] == "last_30d"
        assert r_30["filter_meta"]["applied"]["date_from"] is not None

    def test_q_param_max_length_enforced(self):
        s = _login(*DOCTOR)
        r = s.get(
            f"{API}/patients/{ISABELLA}/clinical/timeline/grouped",
            params={"q": "x" * 200}, timeout=15,
        )
        assert r.status_code == 422, r.text
