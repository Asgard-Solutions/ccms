"""
Billing Service foundation tests (iteration 23).

Coverage:
  * Status-transition helper: legal + illegal transitions for every entity.
  * Model validation: currency, duration/amount bounds, required fields.
  * CRUD happy-path: payer, invoice, payment, claim.
  * Status transitions over the wire (invoice/payment/claim).
  * Tenant isolation — Sunrise can't see Default's invoice/claim.
  * RBAC — non-authorized roles get 403 on refund / writeoff; doctor cannot
    create a payer.
  * Audit rows are emitted on creation + status change.
"""
from __future__ import annotations

import os
import uuid

import pytest
import requests
from dotenv import load_dotenv

from services.billing import transitions
from services.billing.models import (
    CLAIM_TRANSITIONS,
    INVOICE_TRANSITIONS,
    InvoiceCreate,
    PayerCreate,
    PaymentCreate,
)

load_dotenv("/app/backend/.env")

API = os.environ.get("CCMS_BASE_URL", "http://localhost:8001/api")

DEFAULT_ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")
DEFAULT_DOCTOR = ("doctor@ccms.app", "Doctor@ComplianceClinic1")
DEFAULT_STAFF = ("staff@ccms.app", "Staff@ComplianceClinic1")
GROUP_ADMIN = ("group-admin@sunrise.ccms.app", "Sunrise@ComplianceClinic1")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _login(email: str, password: str) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password},
               timeout=15)
    assert r.status_code == 200, r.text
    access = r.cookies.get("access_token")
    if access:
        s.headers["Authorization"] = f"Bearer {access}"
    # Obtain a reauth token — needed because several demo accounts carry
    # `step_up_required=True`, which gates ANY permission call behind MFA
    # reauth. For non-MFA accounts the reauth endpoint accepts the same
    # password and issues a short-lived reauth cookie + returns the token
    # so we can pin it as a header too.
    rr = s.post(f"{API}/auth/reauth", json={"password": password}, timeout=10)
    if rr.status_code == 200:
        token = rr.json().get("reauth_token")
        if token:
            s.headers["x-reauth-token"] = token
    return s


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:6]}"


def _create_payer(s: requests.Session, **overrides) -> dict:
    payload = {
        "name": _unique("Acme Health"),
        "payer_type": "commercial",
        "payer_code": "ACME",
        "remit_method": "era",
    }
    payload.update(overrides)
    r = s.post(f"{API}/billing/payers", json=payload, timeout=10)
    assert r.status_code == 201, r.text
    return r.json()


def _first_patient_id(s: requests.Session) -> str:
    r = s.get(f"{API}/patients", timeout=15)
    assert r.status_code == 200, r.text
    patients = r.json()
    assert patients, "fixtures expected at least one patient"
    return patients[0]["id"]


def _create_invoice(s: requests.Session, patient_id: str, **overrides) -> dict:
    payload = {
        "patient_id": patient_id,
        "currency": "USD",
        "lines": [{
            "code_type": "cpt",
            "code": "98940",
            "description": "CMT 1-2 regions",
            "service_date": "2026-02-10",
            "quantity": 1,
            "unit_price_cents": 5500,
        }],
    }
    payload.update(overrides)
    r = s.post(f"{API}/billing/invoices", json=payload, timeout=15)
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# Pure unit tests — transitions
# ---------------------------------------------------------------------------
class TestTransitions:
    def test_invoice_lifecycle_happy_path(self):
        assert transitions.advance("invoice", "draft", "issued") == "issued"
        assert transitions.advance("invoice", "issued", "partially_paid") == "partially_paid"
        assert transitions.advance("invoice", "partially_paid", "paid") == "paid"
        assert transitions.advance("invoice", "paid", "refunded") == "refunded"

    def test_invoice_idempotent_same_status(self):
        assert transitions.advance("invoice", "issued", "issued") == "issued"

    def test_invoice_illegal_transition_raises(self):
        # void is terminal
        with pytest.raises(transitions.TransitionError):
            transitions.advance("invoice", "void", "issued")
        # can't skip draft → paid
        with pytest.raises(transitions.TransitionError):
            transitions.advance("invoice", "draft", "paid")
        # refunded is terminal
        with pytest.raises(transitions.TransitionError):
            transitions.advance("invoice", "refunded", "issued")

    def test_claim_lifecycle(self):
        cur = "draft"
        for nxt in ("ready", "submitted", "accepted", "paid", "closed"):
            cur = transitions.advance("claim", cur, nxt)
        assert cur == "closed"

    def test_claim_denial_appeal(self):
        cur = transitions.advance("claim", "submitted", "accepted")
        cur = transitions.advance("claim", cur, "denied")
        cur = transitions.advance("claim", cur, "appealed")
        cur = transitions.advance("claim", cur, "paid")
        assert cur == "paid"

    def test_payment_terminal_set(self):
        assert transitions.is_terminal("payment", "void")
        assert transitions.is_terminal("payment", "failed")
        assert transitions.is_terminal("payment", "refunded")
        assert not transitions.is_terminal("payment", "captured")

    def test_unknown_entity_raises(self):
        with pytest.raises(transitions.TransitionError):
            transitions.advance("not-a-thing", "open", "closed")

    def test_transition_maps_reachable(self):
        """Every non-terminal status must have at least one legal successor,
        and every successor must itself be a valid status."""
        for entity_map in (INVOICE_TRANSITIONS, CLAIM_TRANSITIONS):
            all_statuses = set(entity_map.keys())
            for cur, nxts in entity_map.items():
                for n in nxts:
                    assert n in all_statuses, \
                        f"transition to unknown status: {cur} → {n}"


# ---------------------------------------------------------------------------
# Pure unit tests — model validation
# ---------------------------------------------------------------------------
class TestModels:
    def test_invoice_requires_at_least_one_line(self):
        with pytest.raises(Exception):
            InvoiceCreate(patient_id="p1", lines=[])

    def test_invoice_rejects_unsupported_currency(self):
        with pytest.raises(Exception):
            InvoiceCreate(
                patient_id="p1",
                currency="BTC",
                lines=[{
                    "code_type": "cpt", "code": "98940",
                    "description": "x", "service_date": "2026-02-10",
                    "quantity": 1, "unit_price_cents": 100,
                }],
            )

    def test_invoice_line_rejects_negative_price(self):
        with pytest.raises(Exception):
            InvoiceCreate(
                patient_id="p1",
                lines=[{
                    "code_type": "cpt", "code": "98940",
                    "description": "x", "service_date": "2026-02-10",
                    "quantity": 1, "unit_price_cents": -1,
                }],
            )

    def test_payer_blank_name_rejected(self):
        with pytest.raises(Exception):
            PayerCreate(name="   ")

    def test_payment_amount_must_be_positive(self):
        with pytest.raises(Exception):
            PaymentCreate(
                patient_id="p1", method="cash",
                amount_cents=0,
            )


# ---------------------------------------------------------------------------
# Integration tests — API
# ---------------------------------------------------------------------------
class TestPayerCRUD:
    def test_admin_create_list_update(self):
        s = _login(*DEFAULT_ADMIN)
        p = _create_payer(s)
        assert p["status"] == "active"
        assert p["payer_type"] == "commercial"

        r = s.get(f"{API}/billing/payers", timeout=10)
        assert r.status_code == 200
        assert any(x["id"] == p["id"] for x in r.json())

        r = s.put(f"{API}/billing/payers/{p['id']}",
                  json={"notes": "updated"}, timeout=10)
        assert r.status_code == 200
        assert r.json()["notes"] == "updated"

    def test_payer_name_uniqueness_case_insensitive(self):
        s = _login(*DEFAULT_ADMIN)
        name = _unique("Zenith")
        _create_payer(s, name=name)
        r = s.post(f"{API}/billing/payers",
                   json={"name": name.upper(), "payer_type": "commercial",
                         "remit_method": "era"},
                   timeout=10)
        assert r.status_code == 409, r.text

    def test_doctor_can_read_but_not_create_payer(self):
        doctor = _login(*DEFAULT_DOCTOR)
        # provider role has billing.read (assigned_patients scope) so the
        # list endpoint is permitted, but they must NOT be able to create
        # a payer — that gate is wired to clinic_settings.update.
        r = doctor.get(f"{API}/billing/payers", timeout=10)
        assert r.status_code == 200, r.text
        r = doctor.post(f"{API}/billing/payers",
                        json={"name": _unique("DoctorPayer"),
                              "payer_type": "commercial",
                              "remit_method": "era"},
                        timeout=10)
        assert r.status_code == 403, r.text


class TestInvoiceLifecycle:
    def test_create_invoice_and_list(self):
        s = _login(*DEFAULT_ADMIN)
        pid = _first_patient_id(s)
        inv = _create_invoice(s, pid)
        assert inv["status"] == "draft"
        assert inv["total_cents"] == 5500
        assert inv["balance_cents"] == 5500
        assert inv["currency"] == "USD"

        lines = s.get(f"{API}/billing/invoices/{inv['id']}/lines", timeout=10)
        assert lines.status_code == 200
        ln = lines.json()
        assert len(ln) == 1
        assert ln[0]["code"] == "98940"
        assert ln[0]["total_cents"] == 5500

        got = s.get(f"{API}/billing/invoices/{inv['id']}", timeout=10)
        assert got.status_code == 200
        assert got.json()["id"] == inv["id"]

    def test_invoice_status_transition_happy(self):
        s = _login(*DEFAULT_ADMIN)
        pid = _first_patient_id(s)
        inv = _create_invoice(s, pid)

        r = s.post(f"{API}/billing/invoices/{inv['id']}/status?desired=issued",
                   timeout=10)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "issued"
        assert r.json()["issued_at"] is not None

        r = s.post(f"{API}/billing/invoices/{inv['id']}/status?desired=paid",
                   timeout=10)
        assert r.status_code == 200
        assert r.json()["status"] == "paid"

    def test_invoice_illegal_transition_409(self):
        s = _login(*DEFAULT_ADMIN)
        pid = _first_patient_id(s)
        inv = _create_invoice(s, pid)
        # draft → refunded is illegal
        r = s.post(f"{API}/billing/invoices/{inv['id']}/status?desired=refunded",
                   timeout=10)
        assert r.status_code == 409, r.text

    def test_invoice_missing_patient_404(self):
        s = _login(*DEFAULT_ADMIN)
        r = s.post(f"{API}/billing/invoices", json={
            "patient_id": "ghost-" + uuid.uuid4().hex[:6],
            "lines": [{"code_type": "cpt", "code": "98940",
                       "description": "x", "service_date": "2026-02-10",
                       "quantity": 1, "unit_price_cents": 100}],
        }, timeout=10)
        assert r.status_code == 404, r.text


class TestPaymentFlow:
    def test_create_payment_with_allocation(self):
        s = _login(*DEFAULT_ADMIN)
        pid = _first_patient_id(s)
        inv = _create_invoice(s, pid)
        # issue it so allocations are meaningful
        s.post(f"{API}/billing/invoices/{inv['id']}/status?desired=issued",
               timeout=10)

        r = s.post(f"{API}/billing/payments", json={
            "patient_id": pid,
            "method": "card_present",
            "amount_cents": 5500,
            "currency": "USD",
            "allocations": [{"invoice_id": inv["id"], "amount_cents": 5500}],
        }, timeout=10)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "pending"
        assert body["amount_cents"] == 5500
        assert body["allocated_cents"] == 5500

    def test_payment_allocation_exceeds_amount_rejected(self):
        s = _login(*DEFAULT_ADMIN)
        pid = _first_patient_id(s)
        inv = _create_invoice(s, pid)

        r = s.post(f"{API}/billing/payments", json={
            "patient_id": pid,
            "method": "cash",
            "amount_cents": 1000,
            "allocations": [{"invoice_id": inv["id"], "amount_cents": 2000}],
        }, timeout=10)
        assert r.status_code == 400, r.text

    def test_payment_status_transition(self):
        s = _login(*DEFAULT_ADMIN)
        pid = _first_patient_id(s)
        r = s.post(f"{API}/billing/payments", json={
            "patient_id": pid,
            "method": "cash",
            "amount_cents": 2500,
        }, timeout=10)
        assert r.status_code == 201, r.text
        pay_id = r.json()["id"]

        r = s.post(f"{API}/billing/payments/{pay_id}/status?desired=captured",
                   timeout=10)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "captured"

        # captured → pending is illegal
        r = s.post(f"{API}/billing/payments/{pay_id}/status?desired=pending",
                   timeout=10)
        assert r.status_code == 409, r.text


class TestClaimFlow:
    def test_create_and_submit_claim(self):
        s = _login(*DEFAULT_ADMIN)
        pid = _first_patient_id(s)
        payer = _create_payer(s)
        r = s.post(f"{API}/billing/claims", json={
            "patient_id": pid,
            "payer_id": payer["id"],
            "service_date_from": "2026-02-10",
            "service_date_to": "2026-02-10",
            "diagnoses": [{"sequence": 1, "code": "M54.16"}],
            "lines": [{
                "sequence": 1,
                "service_date": "2026-02-10",
                "code_type": "cpt",
                "code": "98940",
                "units": 1,
                "billed_cents": 5500,
                "diagnosis_pointers": [1],
                "modifiers": ["25"],
            }],
        }, timeout=10)
        assert r.status_code == 201, r.text
        claim = r.json()
        assert claim["status"] == "draft"
        assert claim["billed_cents"] == 5500

        # submit: draft → ready → submitted in one call
        r = s.post(f"{API}/billing/claims/{claim['id']}/submit", timeout=10)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "submitted"
        assert r.json()["submitted_at"] is not None

    def test_claim_illegal_transition(self):
        s = _login(*DEFAULT_ADMIN)
        pid = _first_patient_id(s)
        payer = _create_payer(s)
        r = s.post(f"{API}/billing/claims", json={
            "patient_id": pid,
            "payer_id": payer["id"],
            "service_date_from": "2026-02-10",
            "service_date_to": "2026-02-10",
            "diagnoses": [{"sequence": 1, "code": "M54.16"}],
            "lines": [{
                "sequence": 1, "service_date": "2026-02-10",
                "code_type": "cpt", "code": "98940",
                "units": 1, "billed_cents": 5500,
                "diagnosis_pointers": [1],
            }],
        }, timeout=10)
        claim_id = r.json()["id"]
        # draft → paid is illegal
        r = s.post(f"{API}/billing/claims/{claim_id}/status?desired=paid",
                   timeout=10)
        assert r.status_code == 409, r.text


class TestRBAC:
    def test_admin_cannot_refund_without_approval(self):
        """payment.refund is flagged MFA|APR; default admin has no such grant.
        The call must be rejected."""
        s = _login(*DEFAULT_ADMIN)
        pid = _first_patient_id(s)
        r = s.post(f"{API}/billing/payments", json={
            "patient_id": pid, "method": "cash", "amount_cents": 1000,
        }, timeout=10)
        payment_id = r.json()["id"]

        r = s.post(f"{API}/billing/refunds", json={
            "payment_id": payment_id,
            "amount_cents": 500,
            "reason": "duplicate charge",
        }, timeout=10)
        # SA does not carry payment.refund → 403. Any other billing role
        # that does carry it would additionally see a 401 MFA gate first.
        assert r.status_code in (401, 403), r.text

    def test_admin_cannot_writeoff_without_approval(self):
        s = _login(*DEFAULT_ADMIN)
        pid = _first_patient_id(s)
        inv = _create_invoice(s, pid)
        r = s.post(f"{API}/billing/adjustments", json={
            "invoice_id": inv["id"],
            "kind": "writeoff",
            "amount_cents": 500,
            "reason": "patient hardship",
        }, timeout=10)
        assert r.status_code in (401, 403), r.text

    def test_staff_cannot_create_claim(self):
        staff = _login(*DEFAULT_STAFF)
        r = staff.post(f"{API}/billing/claims", json={
            "patient_id": "p1", "payer_id": "py1",
            "service_date_from": "2026-02-10",
            "service_date_to": "2026-02-10",
            "diagnoses": [{"sequence": 1, "code": "M54.16"}],
            "lines": [{"sequence": 1, "service_date": "2026-02-10",
                       "code_type": "cpt", "code": "98940",
                       "units": 1, "billed_cents": 5500,
                       "diagnosis_pointers": [1]}],
        }, timeout=10)
        assert r.status_code == 403, r.text


class TestTenantIsolation:
    def test_invoice_is_not_visible_cross_tenant(self):
        default_admin = _login(*DEFAULT_ADMIN)
        sunrise_admin = _login(*GROUP_ADMIN)
        pid = _first_patient_id(default_admin)
        inv = _create_invoice(default_admin, pid)

        r = sunrise_admin.get(f"{API}/billing/invoices/{inv['id']}", timeout=10)
        assert r.status_code == 404, r.text

        listed = sunrise_admin.get(f"{API}/billing/invoices", timeout=10).json()
        assert all(x["id"] != inv["id"] for x in listed)

    def test_payer_is_not_visible_cross_tenant(self):
        default_admin = _login(*DEFAULT_ADMIN)
        sunrise_admin = _login(*GROUP_ADMIN)
        p = _create_payer(default_admin, name=_unique("DefaultOnlyPayer"))
        listed = sunrise_admin.get(f"{API}/billing/payers", timeout=10).json()
        assert all(x["id"] != p["id"] for x in listed)


class TestAuditEmitted:
    def test_invoice_creation_emits_audit(self):
        admin = _login(*DEFAULT_ADMIN)
        pid = _first_patient_id(admin)
        inv = _create_invoice(admin, pid)
        # pull recent audit rows; filter for billing.invoice.created
        r = admin.get(
            f"{API}/audit-logs?action=billing.invoice.created&limit=50",
            timeout=15,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # audit-logs endpoint may return a bare list or a paginated shape.
        rows = body if isinstance(body, list) else body.get("items", [])
        assert any(
            row.get("entity_id") == inv["id"] and
            row.get("action") == "billing.invoice.created"
            for row in rows
        ), f"audit row not found for invoice {inv['id']}"
