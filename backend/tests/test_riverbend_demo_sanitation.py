"""
Backend verification for the Riverbend demo sanitation pass.
Read-only checks (plus a single round-trip PATCH that re-saves the same payload).

Coverage:
  - GET /api/clinic-profiles
  - GET /api/appointment-types
  - GET /api/rooms (requires reauth)
  - GET /api/tenancy/me/context
  - GET /api/auth/me (admin & doctor)
  - GET /api/patients (admin, unmask=true)
  - PATCH /api/patient/{id} round-trip for Hannah Whitaker + Marcus Reid
"""

import os
import copy
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://demo-cleanup.preview.emergentagent.com").rstrip("/")

ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")
DOCTOR = ("doctor@ccms.app", "Doctor@ComplianceClinic1")

RIVERBEND_TENANT_ID = "a5b12dcb-88da-4121-b0b0-1fafac646b7e"
RIVERBEND_LOCATION_ID = "775775d1-7955-4553-b506-2bc89f877e1f"

EXPECTED_APPT_TYPES = [
    ("Chiropractic Adjustment", 15),
    ("Follow-up Visit", 30),
    ("New Patient Exam", 60),
    ("Re-Exam", 30),
    ("Therapy / Modality", 20),
    ("Auto Injury / PIP Evaluation", 45),
    ("Workers' Comp Evaluation", 45),
    ("Maintenance / Wellness Visit", 15),
    ("Pediatric Visit", 15),
]

EXPECTED_ROOMS = {"Exam 1", "Exam 2", "Adjustment 1", "Adjustment 2", "Consult Room", "X-Ray Suite", "Therapy Bay"}

EXPECTED_PATIENTS = {
    "Hannah Whitaker", "Marcus Reid", "Isabella Cho", "Derrick Stone",
    "Aria Johnson", "Claire Morgan", "Jaxon Morgan", "Ethan Parker",
}


def _login(session: requests.Session, email: str, password: str) -> dict:
    resp = session.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": password}, timeout=20)
    assert resp.status_code == 200, f"Login failed for {email}: {resp.status_code} {resp.text}"
    body = resp.json()
    assert body.get("mfa_required") is False, f"MFA required unexpectedly for {email}"
    return body["user"]


def _reauth(session: requests.Session, password: str):
    """Trigger /api/auth/reauth to set the reauth cookie required by /api/rooms."""
    resp = session.post(f"{BASE_URL}/api/auth/reauth", json={"password": password}, timeout=20)
    assert resp.status_code == 200, f"reauth failed: {resp.status_code} {resp.text}"
    return resp.json()


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    _login(s, *ADMIN)
    return s


@pytest.fixture(scope="module")
def admin_session_reauth(admin_session):
    _reauth(admin_session, ADMIN[1])
    return admin_session


@pytest.fixture(scope="module")
def doctor_session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    _login(s, *DOCTOR)
    return s


# --------- Clinic Profile ---------
class TestClinicProfile:
    def test_single_riverbend_profile(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/clinic-profiles", timeout=20)
        assert r.status_code == 200, r.text
        body = r.json()
        # endpoint may return list or dict with items
        items = body if isinstance(body, list) else body.get("items") or body.get("profiles") or []
        # filter to current tenant if multi-tenant fields present
        rb = [p for p in items if p.get("tenant_id") in (None, RIVERBEND_TENANT_ID)]
        assert len(rb) == 1, f"Expected exactly 1 Riverbend clinic profile, got {len(rb)}: {[p.get('name') for p in rb]}"
        p = rb[0]
        assert p["name"] == "Riverbend Chiropractic & Wellness", p
        # API exposes address as address_line1 (line2 = "Suite 210")
        assert p.get("address_line1") == "1840 NW Riverside Dr", p
        assert p["city"] == "Portland"
        assert p["state"] == "OR"
        assert p["postal_code"] == "97209"
        assert p.get("primary_phone"), "primary_phone missing"
        assert p["email"] == "hello@riverbend-chiro.app"
        assert p["website"] == "https://riverbend-chiro.app"
        assert p["timezone"] == "America/Los_Angeles"

        hours = p.get("hours") or []
        assert len(hours) == 7, f"Expected 7 hours entries, got {len(hours)}"
        by_day = {h["day_of_week"]: h for h in hours}
        # Mon..Fri have two intervals with 12:00-13:00 lunch gap
        for d in range(0, 5):
            entry = by_day.get(d)
            assert entry is not None, f"Missing day {d}"
            ints = entry.get("intervals") or []
            assert len(ints) == 2, f"Day {d} should have 2 intervals, got {ints}"
            # lunch gap 12:00 - 13:00
            ends = sorted(i.get("close_time") or i.get("end_time") for i in ints)
            starts = sorted(i.get("open_time") or i.get("start_time") for i in ints)
            assert "12:00" in ends, f"Day {d} missing 12:00 lunch start, intervals={ints}"
            assert "13:00" in starts, f"Day {d} missing 13:00 lunch end, intervals={ints}"
        sat = by_day.get(5)
        assert sat is not None and len(sat.get("intervals", [])) == 1, f"Sat must have 1 interval, got {sat}"
        sat_iv = sat["intervals"][0]
        assert (sat_iv.get("open_time") or sat_iv.get("start_time")) == "09:00"
        assert (sat_iv.get("close_time") or sat_iv.get("end_time")) == "13:00"
        sun = by_day.get(6)
        assert sun is not None and sun.get("is_closed") is True, f"Sun must be closed, got {sun}"


# --------- Appointment Types ---------
class TestAppointmentTypes:
    def test_appointment_types_riverbend(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/appointment-types", timeout=20)
        assert r.status_code == 200, r.text
        body = r.json()
        items = body if isinstance(body, list) else body.get("items") or []
        # Sort by sort_order to match expected ordering
        items_sorted = sorted(items, key=lambda x: x.get("sort_order", 0))
        names = [i["name"] for i in items_sorted]

        # No junk
        for n in names:
            assert not n.startswith("Dup-"), f"Junk Dup- name found: {n}"
            assert not n.startswith("Filterable-"), f"Junk Filterable- name found: {n}"
            assert not n.startswith("DefaultOnly-"), f"Junk DefaultOnly- name found: {n}"

        assert len(items_sorted) == 9, f"Expected exactly 9 appt types, got {len(items_sorted)}: {names}"

        for (exp_name, exp_dur), got in zip(EXPECTED_APPT_TYPES, items_sorted):
            assert got["name"] == exp_name, f"Order/name mismatch: got {got['name']} expected {exp_name}"
            dur = got.get("default_duration_minutes") or got.get("duration_minutes")
            assert dur == exp_dur, f"{exp_name} duration {dur} != {exp_dur}"
            assert got.get("is_active") is True, f"{exp_name} not active"
            assert (got.get("description") or "").strip(), f"{exp_name} missing description"


# --------- Rooms (requires reauth) ---------
class TestRooms:
    def test_rooms_exact_seven(self, admin_session_reauth):
        r = admin_session_reauth.get(f"{BASE_URL}/api/rooms", timeout=20)
        assert r.status_code == 200, f"GET /api/rooms failed: {r.status_code} {r.text[:300]}"
        body = r.json()
        items = body if isinstance(body, list) else body.get("items") or body.get("rooms") or []
        # Filter to riverbend location
        rb = [x for x in items if x.get("location_id") in (None, RIVERBEND_LOCATION_ID)]
        names = sorted(x["name"] for x in rb)
        assert len(rb) == 7, f"Expected 7 rooms on Riverbend Downtown, got {len(rb)}: {names}"
        assert set(names) == EXPECTED_ROOMS, f"Room names mismatch: {set(names)} vs {EXPECTED_ROOMS}"
        types = {x.get("room_type") or x.get("type") for x in rb}
        for required in ("exam", "consult", "xray", "therapy"):
            assert required in types, f"Room type '{required}' missing. Types found: {types}"
        for x in rb:
            assert x.get("is_active") is True, f"Room {x.get('name')} not active"


# --------- Tenancy context (locations) ---------
class TestTenancyContext:
    def test_single_riverbend_location(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/tenancy/me/context", timeout=20)
        assert r.status_code == 200, r.text
        body = r.json()
        locs = body.get("locations") or []
        # restrict to current tenant
        rb_locs = [l for l in locs if l.get("tenant_id") in (None, RIVERBEND_TENANT_ID)]
        assert len(rb_locs) == 1, f"Expected exactly 1 location, got {len(rb_locs)}: {[l.get('name') for l in rb_locs]}"
        loc = rb_locs[0]
        assert loc["name"] == "Riverbend — Downtown", f"Bad location name: {loc.get('name')}"
        assert loc.get("code") == "RB-DT", f"Bad code: {loc.get('code')}"
        bad_names = {"Main Clinic", "Main Office", "HQ"}
        all_names = {l.get("name") for l in locs}
        assert not (bad_names & all_names), f"Legacy location leftovers found: {bad_names & all_names}"


# --------- Auth/me ---------
class TestAuthMe:
    def test_admin_profile(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/auth/me", timeout=20)
        assert r.status_code == 200, r.text
        u = r.json()
        assert u["first_name"] == "Ava", u
        assert u["last_name"] == "Bennett", u
        assert u["display_name"] == "Ava Bennett", u
        assert u["job_title"] == "Clinic Administrator", u
        assert u["credentials_suffix"] == "", f"credentials_suffix should be empty, got '{u.get('credentials_suffix')}'"
        assert u["preferred_signature_name"] == "A. Bennett", u
        assert u["time_zone"] == "America/Los_Angeles", u
        # Negative checks
        assert u.get("name") != "Ada Lovelace"
        assert "DACBR" not in (u.get("credentials_suffix") or "")

    def test_doctor_profile(self, doctor_session):
        r = doctor_session.get(f"{BASE_URL}/api/auth/me", timeout=20)
        assert r.status_code == 200, r.text
        u = r.json()
        assert u["first_name"] == "Noah", u
        assert u["last_name"] == "Carter", u
        assert u["display_name"] == "Dr. Noah Carter, DC", u
        assert u["credentials_suffix"] == "DC, CCSP", u
        assert u["job_title"] == "Lead Chiropractor", u


# --------- Patients ---------
@pytest.fixture(scope="module")
def patients_list(admin_session_reauth):
    r = admin_session_reauth.get(f"{BASE_URL}/api/patients", params={"unmask": "true"}, timeout=30)
    assert r.status_code == 200, f"GET /api/patients failed: {r.status_code} {r.text[:300]}"
    body = r.json()
    items = body if isinstance(body, list) else body.get("items") or body.get("patients") or []
    return items


class TestPatients:
    def test_eight_personas_present(self, patients_list):
        names = set()
        for p in patients_list:
            full = (p.get("name") or f"{p.get('first_name','')} {p.get('last_name','')}".strip())
            names.add(full)
        missing = EXPECTED_PATIENTS - names
        assert not missing, f"Missing expected patient personas: {missing}. Got: {sorted(names)}"
        # also assert the count is exactly 8 in default tenant
        # restrict count to those matching expected set since tenant may include others (unlikely after sanitation)
        rb_personas = [p for p in patients_list if (p.get("name") or f"{p.get('first_name','')} {p.get('last_name','')}".strip()) in EXPECTED_PATIENTS]
        assert len(rb_personas) == 8, f"Expected 8 Riverbend personas, got {len(rb_personas)}"
        # Strict total: Riverbend tenant must have exactly 8 patients
        assert len(patients_list) == 8, f"Expected exactly 8 patients in Riverbend tenant, got {len(patients_list)}: {sorted(names)}"

    def test_every_patient_has_sex(self, patients_list):
        missing_sex = []
        for p in patients_list:
            demo = p.get("demographics") or {}
            sex_at_birth = demo.get("sex_at_birth")
            gender = demo.get("gender")
            full = p.get("name") or f"{p.get('first_name','')} {p.get('last_name','')}".strip()
            if sex_at_birth not in ("female", "male"):
                missing_sex.append((full, "sex_at_birth", sex_at_birth))
            if not gender:
                missing_sex.append((full, "gender", gender))
        assert not missing_sex, f"Patients missing sex/gender: {missing_sex}"


# --------- PATCH round-trip ---------
class TestPatientPatchRoundTrip:
    @pytest.mark.parametrize("persona", ["Hannah Whitaker", "Marcus Reid"])
    def test_patch_round_trip(self, admin_session_reauth, patients_list, persona):
        target = None
        for p in patients_list:
            full = p.get("name") or f"{p.get('first_name','')} {p.get('last_name','')}".strip()
            if full == persona:
                target = p
                break
        assert target, f"{persona} not in patient list"
        pid = target["id"]

        # GET full record (some endpoints return more fields than list)
        rget = admin_session_reauth.get(
            f"{BASE_URL}/api/patients/{pid}",
            params={"unmask": "true", "reason": "round-trip sanitation test"},
            timeout=20,
        )
        assert rget.status_code == 200, f"GET patient {pid} failed: {rget.status_code} {rget.text[:300]}"
        full_record = rget.json()

        # Build payload mimicking the wizard (structured fields preserved).
        payload_keys = [
            "first_name", "last_name", "middle_name", "preferred_name",
            "email", "phone", "date_of_birth",
            "demographics", "address_details", "emergency_contact_details", "contact",
            "insurance", "address", "emergency_contact",
        ]
        payload = {}
        for k in payload_keys:
            if k in full_record and full_record[k] is not None:
                payload[k] = copy.deepcopy(full_record[k])

        # Try PATCH /api/patients/{id}; fall back to /api/patient/{id}
        urls = [
            f"{BASE_URL}/api/patients/{pid}",
            f"{BASE_URL}/api/patient/{pid}",
        ]
        last_resp = None
        for url in urls:
            r = admin_session_reauth.patch(url, json=payload, timeout=30)
            last_resp = r
            if r.status_code != 404:
                break
        # Some backends use PUT for updates
        if last_resp is None or last_resp.status_code in (404, 405):
            for url in urls:
                r = admin_session_reauth.put(url, json=payload, timeout=30)
                last_resp = r
                if r.status_code != 404:
                    break

        assert last_resp is not None
        assert last_resp.status_code in (200, 204), (
            f"Round-trip save failed for {persona} ({pid}): "
            f"{last_resp.status_code} {last_resp.text[:600]}"
        )
        # If body returned, verify name didn't get nuked
        if last_resp.status_code == 200 and last_resp.text:
            try:
                back = last_resp.json()
                assert back.get("first_name") == target.get("first_name") or back.get("name") == persona
            except ValueError:
                pass


# --------- Claims: display vs. storage separation ---------
UUID_RE = __import__("re").compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def _first_claim_id(admin_session) -> str:
    """Return the first Riverbend-demo claim id. Filter by
    `patient_control_number` starting with 'RB-' so pytest-created
    claims from other suites don't poison this test (they use random
    or absent control numbers)."""
    r = admin_session.get(
        f"{BASE_URL}/api/billing/claims?limit=50", timeout=20,
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    assert isinstance(rows, list) and rows, f"No claims seeded: {rows}"
    demo_rows = [
        c for c in rows
        if (c.get("patient_control_number") or "").startswith("RB-")
    ]
    assert demo_rows, (
        "No demo-seeded claims found — re-run "
        "`python /app/backend/scripts/reseed_demo_clinic.py`"
    )
    return demo_rows[0]["id"]


class TestClaimDisplayVsStorage:
    """Regression guards against UUIDs / internal IDs leaking onto
    user-facing claim screens and outbound 837P payloads. """

    def test_detail_refs_resolve_all_foreign_keys(self, admin_session):
        claim_id = _first_claim_id(admin_session)
        r = admin_session.get(
            f"{BASE_URL}/api/billing/claims/{claim_id}/detail", timeout=20,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "refs" in body, "detail endpoint must include `refs` block"
        refs = body["refs"]
        # Every claim row points at a seeded patient + payer + providers.
        for key in ("patient", "payer", "billing_provider",
                    "rendering_provider", "facility", "location"):
            assert refs.get(key), f"refs.{key} must resolve: {refs.get(key)}"
            # Every resolved entity has a human-readable `name`.
            assert refs[key].get("name"), (
                f"refs.{key}.name must be a readable string, "
                f"got {refs[key]}"
            )
            # And the `name` must NOT itself be a UUID (the #1 foot-gun —
            # a fallback accidentally placing the id into the name slot).
            assert not UUID_RE.match(refs[key]["name"]), (
                f"refs.{key}.name must not be a UUID: {refs[key]}"
            )

    def test_billing_provider_has_real_npi(self, admin_session):
        claim_id = _first_claim_id(admin_session)
        r = admin_session.get(
            f"{BASE_URL}/api/billing/claims/{claim_id}/detail", timeout=20,
        )
        assert r.status_code == 200, r.text
        bp = r.json()["refs"]["billing_provider"]
        rp = r.json()["refs"]["rendering_provider"]
        # NPI is a 10-digit number — never a UUID slice, never empty.
        assert bp["npi"] and bp["npi"].isdigit() and len(bp["npi"]) == 10, bp
        assert rp["npi"] and rp["npi"].isdigit() and len(rp["npi"]) == 10, rp

    def test_claims_queue_rows_use_readable_names(self, admin_session):
        r = admin_session.get(
            f"{BASE_URL}/api/billing/claims/queue?tab=all&page=1&page_size=20",
            timeout=20,
        )
        assert r.status_code == 200, r.text
        rows = r.json().get("rows", [])
        assert rows, "queue should have seeded rows"
        for row in rows:
            # Every row must expose a readable patient_name + payer_name.
            assert row.get("patient_name"), (
                f"queue row missing patient_name: {row}"
            )
            assert not UUID_RE.match(row["patient_name"]), row
            if row.get("payer_name"):
                assert not UUID_RE.match(row["payer_name"]), row
            # When assigned, the enriched assignee_name must be readable.
            if row.get("assigned_to"):
                assert row.get("assignee_name"), (
                    f"assigned claim without assignee_name: {row}"
                )
                assert not UUID_RE.match(row["assignee_name"]), row

    def test_assignable_users_endpoint(self, admin_session):
        r = admin_session.get(
            f"{BASE_URL}/api/billing/claims/assignable-users", timeout=20,
        )
        assert r.status_code == 200, r.text
        rows = r.json()
        assert isinstance(rows, list) and rows, rows
        for u in rows:
            assert u.get("id"), u
            assert u.get("name") and not UUID_RE.match(u["name"]), u
            assert u.get("role") in (
                "admin", "doctor", "staff", "billing_specialist",
            ), u

    def test_x12_builder_refuses_uuid_as_npi(self):
        """The x12 wire builder must refuse to emit a UUID as the
        billing-provider NPI. This is the last-line-of-defence against
        a raw `claim.billing_provider_id` leaking onto the wire."""
        import sys
        sys.path.insert(0, "/app/backend")
        from services.billing.clearinghouse.x12_837p import build_x12_837p_wire

        claim = {"id": "c1", "billing_provider_id": "29da6566-a752-4c36-b524-3291dd698cb9"}
        # No billing_provider provided — must refuse rather than silently
        # using the UUID.
        with pytest.raises(ValueError, match="billing_provider is required"):
            build_x12_837p_wire(
                claim=claim, diagnoses=[], lines=[],
                patient=None, payer=None, policy=None,
            )
        # Billing provider provided but NPI is a UUID — must refuse.
        bad_bp = {"name": "X", "npi": "29da6566-a752-4c36-b524-3291dd698cb9"}
        with pytest.raises(ValueError, match="10-digit NPI"):
            build_x12_837p_wire(
                claim=claim, diagnoses=[], lines=[],
                patient=None, payer=None, policy=None,
                billing_provider=bad_bp,
            )


# --------- Charts: every persona with visits has intake + clinical ---
CHART_BEARING_PERSONAS = [
    ("Hannah", "Whitaker"), ("Marcus", "Reid"), ("Isabella", "Cho"),
    ("Derrick", "Stone"),   ("Aria",   "Johnson"), ("Claire", "Morgan"),
    ("Jaxon",  "Morgan"),   ("Ethan",  "Parker"),
]


class TestClinicalChartCompleteness:
    """Every Riverbend persona that has an appointment on the schedule
    must have a fully populated chart (episode + diagnoses + treatment
    plan + clinical_history + at least one intake form). Otherwise the
    Clinical + Intake tabs render empty states that contradict the
    visit history and break the demo narrative."""

    def _patient_ids(self, patients_list):
        ids: dict[tuple[str, str], str] = {}
        for p in patients_list:
            key = (p.get("first_name"), p.get("last_name"))
            if key in set(CHART_BEARING_PERSONAS):
                ids[key] = p["id"]
        return ids

    def test_every_persona_has_episode(self, admin_session_reauth, patients_list):
        pid_by_name = self._patient_ids(patients_list)
        missing = []
        for name_key, pid in pid_by_name.items():
            r = admin_session_reauth.get(
                f"{BASE_URL}/api/patients/{pid}/clinical/episodes",
                timeout=20,
            )
            assert r.status_code == 200, r.text
            if not r.json():
                missing.append(f"{name_key[0]} {name_key[1]}")
        assert not missing, (
            f"These personas have no clinical episode — Clinical tab "
            f"will show empty state: {missing}"
        )

    def test_every_persona_has_treatment_plan(self, admin_session_reauth, patients_list):
        pid_by_name = self._patient_ids(patients_list)
        missing = []
        for name_key, pid in pid_by_name.items():
            r = admin_session_reauth.get(
                f"{BASE_URL}/api/patients/{pid}/clinical/treatment-plans",
                timeout=20,
            )
            assert r.status_code == 200, r.text
            if not r.json():
                missing.append(f"{name_key[0]} {name_key[1]}")
        assert not missing, (
            f"Expected a treatment plan for each chart-bearing persona; "
            f"missing: {missing}"
        )

    def test_every_persona_has_diagnoses(self, admin_session_reauth, patients_list):
        pid_by_name = self._patient_ids(patients_list)
        missing = []
        for name_key, pid in pid_by_name.items():
            r = admin_session_reauth.get(
                f"{BASE_URL}/api/patients/{pid}/clinical/diagnoses",
                timeout=20,
            )
            assert r.status_code == 200, r.text
            if not r.json():
                missing.append(f"{name_key[0]} {name_key[1]}")
        assert not missing, (
            f"Every persona should have ICD-10 diagnosis rows; missing: "
            f"{missing}"
        )

    def test_every_persona_has_history_snapshot(self, admin_session_reauth, patients_list):
        pid_by_name = self._patient_ids(patients_list)
        missing = []
        for name_key, pid in pid_by_name.items():
            r = admin_session_reauth.get(
                f"{BASE_URL}/api/patients/{pid}/clinical/history",
                timeout=20,
            )
            # 404 or empty body = no history snapshot yet.
            if r.status_code == 404:
                missing.append(f"{name_key[0]} {name_key[1]}")
                continue
            assert r.status_code == 200, r.text
            body = r.json()
            if not body or not body.get("chief_complaint"):
                missing.append(f"{name_key[0]} {name_key[1]}")
        assert not missing, (
            f"Every persona should have a clinical_history snapshot "
            f"(with chief_complaint populated) — missing: {missing}"
        )

    def test_every_persona_has_intake_form(self, admin_session_reauth, patients_list):
        pid_by_name = self._patient_ids(patients_list)
        missing = []
        for name_key, pid in pid_by_name.items():
            r = admin_session_reauth.get(
                f"{BASE_URL}/api/patients/{pid}/intake-forms",
                timeout=20,
            )
            assert r.status_code == 200, r.text
            if not r.json():
                missing.append(f"{name_key[0]} {name_key[1]}")
        assert not missing, (
            f"Every persona should have a completed intake form; "
            f"missing: {missing}"
        )
