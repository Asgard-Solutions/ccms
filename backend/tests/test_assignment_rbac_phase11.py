"""
Phase 11 — assignment workflow, RBAC, audit, hardening.

Covers:
  * New `claim.assign` permission:
      - PATCH  /billing/claims/{id}/assignment
      - POST   /billing/claims/{id}/assign
      - POST   /billing/claims/{id}/unassign
      - PATCH (legacy) remains usable but guarded by the new permission
  * `claim.assign` granted to super_admin + org_owner + clinic_manager
    + billing_specialist (seeded via role_permissions on restart).
  * Queue `unassigned=true` filter returns only claims without an
    assignee; overrides `assigned_to` when both are sent.
  * Queue row carries assignee_id + assignee_name (existing Phase 4).
  * Assignment is idempotent: setting the same assignee twice is a
    no-op 200 with no duplicate audit row (retry-safe).
  * Validation / submission / report / follow-up endpoints all emit
    audit rows with the expected action strings.
  * Empty queue (no rows matching filter) returns structured body
    with rows=[] and total=0 — never crashes on null assignees.
  * Claim history receives `assignment_changed` entries with both
    `from_assignee` and `to_assignee`.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone, timedelta

import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

_BACKEND_DIR = "/app/backend"
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

BASE = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE}/api" if BASE else "http://localhost:8001/api"
ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")


def _login(email: str = ADMIN[0], password: str = ADMIN[1]) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{API}/auth/login",
               json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, r.text
    tok = r.cookies.get("access_token") or r.json().get("access_token")
    if tok:
        s.headers["Authorization"] = f"Bearer {tok}"
    r = s.post(f"{API}/auth/reauth",
               json={"password": password}, timeout=10)
    if r.status_code == 200:
        rt = r.cookies.get("reauth_token") or r.json().get("reauth_token")
        if rt:
            s.headers["x-reauth-token"] = rt
    return s


def _seed_claim(s) -> dict:
    payer = s.post(f"{API}/billing/payers", json={
        "name": f"P11 Payer {uuid.uuid4().hex[:6]}",
        "payer_type": "commercial", "remit_method": "era",
    }, timeout=15).json()
    patient = s.post(f"{API}/patients", json={
        "first_name": "P11", "last_name": f"As{uuid.uuid4().hex[:4]}",
        "date_of_birth": "1990-01-01",
        "email": f"p11-{uuid.uuid4().hex[:6]}@example.com",
    }, timeout=15).json()
    policy = s.post(f"{API}/billing/insurance-policies", json={
        "patient_id": patient["id"], "payer_id": payer["id"],
        "rank": "primary", "subscriber_name": "P11 Subscriber",
        "relationship_to_subscriber": "self",
        "member_id": f"M-{uuid.uuid4().hex[:6]}",
    }, timeout=15).json()
    claim = s.post(f"{API}/billing/claims", json={
        "patient_id": patient["id"], "payer_id": payer["id"],
        "policy_id": policy["id"],
        "claim_type": "professional", "place_of_service": "11",
        "frequency_code": "1",
        "billing_provider_id": "1234567893",
        "rendering_provider_id": "1234567893",
        "service_date_from": "2026-04-10",
        "service_date_to":   "2026-04-10",
        "diagnoses": [{"sequence": 1, "code": "M99.01"}],
        "lines": [{
            "sequence": 1, "service_date": "2026-04-10",
            "code_type": "cpt", "code": "98940", "units": 1,
            "billed_cents": 5500, "diagnosis_pointers": [1],
            "modifiers": ["AT"],
        }],
    }, timeout=15).json()
    return claim


def _me_id(s) -> str:
    r = s.get(f"{API}/auth/me", timeout=10)
    assert r.status_code == 200, r.text
    return r.json()["id"]


# ---------------------------------------------------------------------------
# 1. New `claim.assign` permission is seeded for the expected roles
# ---------------------------------------------------------------------------
def test_claim_assign_permission_is_seeded_for_manager_roles():
    """After startup seed, `claim.assign` must be a real permission
    row AND be granted to at least one non-wildcard role so the
    Claims Queue UI can render the assignee dropdown for the right
    users."""
    import asyncio
    from motor.motor_asyncio import AsyncIOMotorClient

    async def _check():
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        try:
            db = c[os.environ["DB_NAME"]]
            perm = await db.permissions.find_one(
                {"key": "claim.assign"}, {"_id": 0},
            )
            assert perm is not None, "claim.assign permission not seeded"
            # Grants exist for at least 2 explicit roles (not just *).
            grants = await db.role_permissions.find(
                {"permission_key": "claim.assign"}, {"_id": 0},
            ).to_list(50)
            roles = {g["role_key"] for g in grants}
            assert "billing_specialist" in roles
            assert "clinic_manager" in roles
        finally:
            c.close()

    asyncio.run(_check())


# ---------------------------------------------------------------------------
# 2. Assign / unassign round-trip via both PATCH and POST routes
# ---------------------------------------------------------------------------
def test_assign_and_unassign_round_trip_updates_claim_and_audit():
    s = _login()
    claim = _seed_claim(s)
    me = _me_id(s)

    # Default state — no assignee.
    detail = s.get(
        f"{API}/billing/claims/{claim['id']}/detail", timeout=10,
    ).json().get("claim", {})
    assert (detail.get("assigned_to") or None) is None

    # Assign via POST convenience.
    r = s.post(f"{API}/billing/claims/{claim['id']}/assign",
                json={"assigned_to": me}, timeout=15)
    assert r.status_code == 200, r.text
    assert r.json()["assigned_to"] == me

    # Unassign via the dedicated POST route.
    r = s.post(f"{API}/billing/claims/{claim['id']}/unassign",
                timeout=15)
    assert r.status_code == 200, r.text
    assert r.json()["assigned_to"] is None

    # Round-trip once more via the PATCH endpoint.
    r = s.patch(f"{API}/billing/claims/{claim['id']}/assignment",
                 json={"assigned_to": me}, timeout=15)
    assert r.status_code == 200, r.text
    assert r.json()["assigned_to"] == me

    # Verify the raw `history` array on the Mongo document — the
    # public ClaimPublic model strips it, so we go to the source of
    # truth to prove we captured all three transitions.
    import asyncio
    from motor.motor_asyncio import AsyncIOMotorClient

    async def _hist():
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        try:
            row = await c[os.environ["DB_NAME"]].claims.find_one(
                {"id": claim["id"]}, {"_id": 0, "history": 1},
            )
            return row.get("history") or []
        finally:
            c.close()

    history = asyncio.run(_hist())
    actions = [h.get("action") for h in history]
    assert actions.count("assignment_changed") >= 3


def test_assignment_is_idempotent_noop_when_same_assignee():
    s = _login()
    claim = _seed_claim(s)
    me = _me_id(s)
    s.post(f"{API}/billing/claims/{claim['id']}/assign",
           json={"assigned_to": me}, timeout=15)

    # Second assign to the same id — must 200 without creating an
    # additional history row (retry-safe).
    r = s.post(f"{API}/billing/claims/{claim['id']}/assign",
                json={"assigned_to": me}, timeout=15)
    assert r.status_code == 200, r.text

    import asyncio
    from motor.motor_asyncio import AsyncIOMotorClient

    async def _hist():
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        try:
            row = await c[os.environ["DB_NAME"]].claims.find_one(
                {"id": claim["id"]}, {"_id": 0, "history": 1},
            )
            return row.get("history") or []
        finally:
            c.close()

    history = asyncio.run(_hist())
    count = sum(1 for h in history
                if h.get("action") == "assignment_changed")
    assert count == 1, f"expected a single history entry, got {count}"


def test_cannot_assign_to_nonexistent_user():
    s = _login()
    claim = _seed_claim(s)
    r = s.post(f"{API}/billing/claims/{claim['id']}/assign",
                json={"assigned_to": f"ghost-{uuid.uuid4()}"}, timeout=15)
    assert r.status_code == 400, r.text


# ---------------------------------------------------------------------------
# 3. Queue `unassigned=true` filter + `assigned_to=<id>` filter
# ---------------------------------------------------------------------------
def test_queue_unassigned_filter_returns_only_unassigned_rows():
    s = _login()
    a = _seed_claim(s)
    b = _seed_claim(s)
    me = _me_id(s)
    s.post(f"{API}/billing/claims/{a['id']}/assign",
           json={"assigned_to": me}, timeout=15)

    # `unassigned=true` → must include `b`, exclude `a`.
    r = s.get(
        f"{API}/billing/claims/queue?tab=all&page_size=200&unassigned=true",
        timeout=15,
    )
    assert r.status_code == 200, r.text
    rows = r.json().get("rows", [])
    ids = {row["id"] for row in rows}
    assert b["id"] in ids
    assert a["id"] not in ids


def test_queue_assigned_to_filter_isolates_specific_user():
    s = _login()
    a = _seed_claim(s)
    b = _seed_claim(s)
    me = _me_id(s)
    s.post(f"{API}/billing/claims/{a['id']}/assign",
           json={"assigned_to": me}, timeout=15)

    r = s.get(
        f"{API}/billing/claims/queue?tab=all&page_size=200"
        f"&assigned_to={me}", timeout=15,
    )
    rows = r.json().get("rows", [])
    ids = {row["id"] for row in rows}
    assert a["id"] in ids
    assert b["id"] not in ids


# ---------------------------------------------------------------------------
# 4. Assignee enrichment on queue rows
# ---------------------------------------------------------------------------
def test_queue_row_carries_assignee_name_after_assignment():
    s = _login()
    claim = _seed_claim(s)
    me = _me_id(s)
    s.post(f"{API}/billing/claims/{claim['id']}/assign",
           json={"assigned_to": me}, timeout=15)

    r = s.get(
        f"{API}/billing/claims/queue?tab=all&page_size=200"
        f"&assigned_to={me}", timeout=15,
    )
    rows = r.json().get("rows", [])
    row = next((x for x in rows if x["id"] == claim["id"]), None)
    assert row is not None
    assert row["assigned_to"] == me
    # assignee_name comes from the user row via the enrichment pipeline.
    assert row.get("assignee_name"), "assignee_name must be enriched"


# ---------------------------------------------------------------------------
# 5. Audit log coverage for critical workflows
# ---------------------------------------------------------------------------
def _recent_audit_actions(s, since: str) -> set[str]:
    r = s.get(
        f"{API}/audit?since={since}&page_size=500", timeout=15,
    )
    if r.status_code != 200:
        return set()
    items = r.json().get("items") or r.json().get("entries") or r.json()
    return {(it.get("action") or "") for it in items}


def test_assignment_change_emits_dedicated_audit_action():
    import asyncio
    from motor.motor_asyncio import AsyncIOMotorClient
    s = _login()
    claim = _seed_claim(s)
    me = _me_id(s)
    t0 = datetime.now(timezone.utc).isoformat()
    s.post(f"{API}/billing/claims/{claim['id']}/assign",
           json={"assigned_to": me}, timeout=15)

    async def _find():
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        try:
            return await c[os.environ["DB_NAME"]].audit_logs.find_one(
                {"action": "billing.claim.assignment_changed",
                 "entity_id": claim["id"]},
                sort=[("occurred_at", -1)],
            )
        finally:
            c.close()
    _ = t0
    row = asyncio.run(_find())
    assert row is not None, "assignment audit row not written"
    assert row["metadata"]["to"] == me
    assert row["metadata"]["action"] == "assigned"


def test_critical_workflows_write_audit_rows():
    """The six Phase 11 critical audit events must all be writable.
    We hit each endpoint and then query Mongo for the corresponding
    action string so the coverage is provable, not just vibes."""
    import asyncio
    from motor.motor_asyncio import AsyncIOMotorClient
    s = _login()
    claim = _seed_claim(s)
    me = _me_id(s)

    # Validation
    s.post(f"{API}/billing/claims/{claim['id']}/validate", timeout=15)
    # Pre-submit gate validation (via /submissions flow)
    sub_resp = s.post(
        f"{API}/billing/claims/{claim['id']}/submissions",
        json={"method": "batch_file"}, timeout=15,
    )
    assert sub_resp.status_code == 201
    sub = sub_resp.json()
    # Inbound report
    s.post(f"{API}/billing/clearinghouse/reports/ingest", json={
        "clearinghouse": "change_healthcare",
        "report_type": "999", "status": "accepted",
        "claim_id": claim["id"], "submission_id": sub["id"],
    }, timeout=15)
    # Assignment
    s.post(f"{API}/billing/claims/{claim['id']}/assign",
           json={"assigned_to": me}, timeout=15)
    # Follow-up flag + clear
    s.post(f"{API}/billing/claims/{claim['id']}/flag-followup",
           json={"reason": "Phase 11 audit smoke"}, timeout=15)
    s.delete(f"{API}/billing/claims/{claim['id']}/flag-followup", timeout=15)

    required = {
        "billing.claim.validated",
        "billing.claim.pre_submit_validated",
        "billing.claim.submission_created",
        "billing.clearinghouse.report_ingested",
        "billing.claim.assignment_changed",
        "billing.claim.followup_flagged",
        "billing.claim.followup_cleared",
    }

    async def _collect():
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        try:
            rows = await c[os.environ["DB_NAME"]].audit_logs.find(
                {"entity_id": claim["id"]}, {"_id": 0, "action": 1},
            ).to_list(500)
            return {r["action"] for r in rows}
        finally:
            c.close()

    seen = asyncio.run(_collect())
    missing = required - seen
    assert not missing, f"Missing audit rows for: {missing}"


# ---------------------------------------------------------------------------
# 6. Hardening — empty state + defensive null handling
# ---------------------------------------------------------------------------
def test_queue_empty_filter_returns_structured_empty_body_never_crashes():
    s = _login()
    # A guaranteed-empty filter: assigned_to = a UUID that doesn't exist.
    r = s.get(
        f"{API}/billing/claims/queue?tab=all&page_size=20"
        f"&assigned_to={uuid.uuid4()}", timeout=15,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("rows") == []
    assert body.get("total") == 0
    # Tab counts still render (for sidebar badges).
    assert "tab_counts" in body


def test_queue_handles_rows_without_assigned_to_field():
    """Legacy claim rows written before the assignment feature may
    lack the `assigned_to` field entirely. The enrichment pipeline
    must not blow up — it should render `assigned_to: None` safely."""
    s = _login()
    r = s.get(
        f"{API}/billing/claims/queue?tab=all&page_size=50&unassigned=true",
        timeout=15,
    )
    assert r.status_code == 200, r.text
    rows = r.json().get("rows", [])
    # Every returned row must have the field present (possibly None).
    for row in rows:
        assert "assigned_to" in row
        assert row["assigned_to"] is None or isinstance(row["assigned_to"], str)
        # Hardening — these enrichment fields must always exist.
        for key in ("canonical_status", "canonical_status_label",
                    "aging_basis", "aging_days",
                    "followup_flag", "last_activity_at"):
            assert key in row, f"missing enrichment key: {key}"
        assert isinstance(row["followup_flag"], bool)


# ---------------------------------------------------------------------------
# 7. Hardening — unknown tab returns 404, invalid page_size returns 422
# ---------------------------------------------------------------------------
def test_unknown_tab_returns_404_not_server_error():
    s = _login()
    r = s.get(f"{API}/billing/claims/queue?tab=bogus", timeout=10)
    assert r.status_code == 404
    assert "Unknown tab" in r.text


def test_queue_validates_page_size_bounds():
    s = _login()
    # 0 is below min
    r = s.get(f"{API}/billing/claims/queue?page_size=0", timeout=10)
    assert r.status_code == 422
    # 2000 is above max
    r = s.get(f"{API}/billing/claims/queue?page_size=2000", timeout=10)
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# 8. next_action_at + aging_days coherence
# ---------------------------------------------------------------------------
def test_manual_flag_populates_next_action_at_even_when_omitted():
    s = _login()
    claim = _seed_claim(s)
    r = s.post(f"{API}/billing/claims/{claim['id']}/flag-followup",
                json={"reason": "verify benefits"}, timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    # Default: now + 3 days (we just sanity-check it's within a week).
    assert body["next_action_at"]
    nat = datetime.fromisoformat(body["next_action_at"].replace("Z", "+00:00"))
    delta = nat - datetime.now(timezone.utc)
    assert timedelta(days=2) <= delta <= timedelta(days=4)
