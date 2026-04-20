"""
Billing Service — canonical domain model + status lifecycle.

This module is the PostgreSQL-ready foundation of the billing domain.
It is intentionally decoupled from any payer / clearinghouse integration
(Stripe, Change Healthcare, Availity, Waystar, …). Those adapters live
outside the canonical model and consume it through its public API.

Design principles
-----------------
1. Every entity is **tenant-scoped**. `tenant_id` is persisted on every
   row and enforced via `core.tenant_scope.scoped_filter`.
2. IDs are UUID strings (RFC-4122) everywhere — drop-in for Postgres
   `UUID` columns.
3. Monetary amounts are stored as **integer cents** (`amount_cents`).
   Never use floating-point for money.
4. All child lists (e.g. `invoice_lines`, `claim_diagnoses`, ...) live in
   **sibling collections**, not embedded documents, so the schema maps
   cleanly to normalised SQL tables.
5. Status transitions are validated against the `*_TRANSITIONS` maps
   below. Direct writes that skip the transition helper are a bug.
6. Append-only **history** entries on parent rows provide a lightweight
   audit trail at the entity level; the full audit record still lives in
   `core.audit`.

Future relational schema (summary — authoritative DDL lives in
/app/memory/PRD.md / CHANGELOG):

    payers                       (id, tenant_id, name, payer_type, payer_code,
                                  status, electronic_payer_id, remit_method, ...)
    patient_insurance_policies   (id, tenant_id, patient_id, payer_id, rank,
                                  subscriber_name, member_id, group_number,
                                  effective_date, termination_date, status, ...)
    fee_schedules                (id, tenant_id, name, effective_date, ...)
    fee_schedule_lines           (id, fee_schedule_id, code, allowed_cents, ...)
    code_catalog_items           (id, tenant_id, code_type, code, description,
                                  default_price_cents, active)
    code_modifiers               (id, tenant_id, code, description, active)

    invoices                     (id, tenant_id, location_id, patient_id,
                                  appointment_id, status, issued_at,
                                  due_date, subtotal_cents, tax_cents,
                                  adjustment_cents, total_cents, balance_cents,
                                  currency, notes, ...)
    invoice_lines                (id, invoice_id, tenant_id, service_date,
                                  code_type, code, description, quantity,
                                  unit_price_cents, total_cents,
                                  modifiers_json, provider_id)

    payments                     (id, tenant_id, location_id, patient_id,
                                  payer_id NULLABLE, method, status, amount_cents,
                                  received_at, reference, source_remittance_id,
                                  external_txn_id, ...)
    payment_allocations          (id, payment_id, tenant_id, invoice_id,
                                  invoice_line_id NULLABLE, amount_cents)
    refunds                      (id, tenant_id, payment_id, amount_cents,
                                  reason, status, processed_at, ...)
    adjustments                  (id, tenant_id, invoice_id, invoice_line_id,
                                  kind (writeoff|discount|courtesy|contractual),
                                  amount_cents, reason, approved_by_id, ...)

    claims                       (id, tenant_id, location_id, patient_id,
                                  payer_id, policy_id, status,
                                  service_date_from, service_date_to,
                                  billed_cents, submitted_at, accepted_at,
                                  paid_cents, last_denial_code, ...)
    claim_diagnoses              (id, claim_id, tenant_id, sequence, code)
    claim_lines                  (id, claim_id, tenant_id, sequence,
                                  invoice_line_id, service_date,
                                  code_type, code, units, billed_cents,
                                  diagnosis_pointers_json)
    claim_line_modifiers         (id, claim_line_id, tenant_id, sequence,
                                  modifier_code)
    remittances                  (id, tenant_id, payer_id,
                                  status, received_at,
                                  total_paid_cents, check_or_eft_number, ...)
    denial_work_items            (id, tenant_id, claim_id, claim_line_id NULLABLE,
                                  denial_code, denial_category, amount_cents,
                                  status, assigned_to_id, resolution_notes,
                                  opened_at, closed_at, ...)

Everything below is Pydantic validation + canonical status vocabularies.
The actual Mongo reads / writes live in `router.py`.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Currency / money
# ---------------------------------------------------------------------------
# Always integer cents. Keep the currency code free-form ISO-4217 uppercase;
# multi-currency support is not required in v1 but we reserve the column now.
ALLOWED_CURRENCIES = ("USD",)
DEFAULT_CURRENCY = "USD"


def _ensure_non_negative_cents(v: int, field_name: str) -> int:
    if v < 0:
        raise ValueError(f"{field_name} must be >= 0")
    return v


# ---------------------------------------------------------------------------
# Status vocabularies & transition rules (authoritative)
# ---------------------------------------------------------------------------
# Every mutation that changes `status` MUST go through `transitions.advance()`
# which consults these maps.

InvoiceStatus = Literal[
    "draft",           # created, still being built
    "issued",          # sent to patient / responsible party
    "partially_paid", # payments applied, balance > 0
    "paid",            # balance == 0
    "adjusted",        # adjustment/writeoff applied (may still have balance)
    "void",            # terminal — invoice retracted, all lines reversed
    "refunded",        # terminal — fully refunded after being paid
]

INVOICE_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"issued", "void"},
    "issued": {"partially_paid", "paid", "adjusted", "void"},
    "partially_paid": {"paid", "adjusted", "void"},
    "paid": {"refunded", "adjusted"},
    "adjusted": {"issued", "partially_paid", "paid", "void"},
    "void": set(),
    "refunded": set(),
}

PaymentStatus = Literal[
    "pending",      # recorded, not yet authorized
    "authorized",   # authorized by gateway, not captured
    "captured",     # captured, not yet settled
    "settled",      # money in the merchant account
    "refunded",     # fully refunded
    "partially_refunded",
    "failed",       # terminal — gateway declined
    "void",         # terminal — voided before capture
]

PAYMENT_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"authorized", "captured", "settled", "failed", "void"},
    "authorized": {"captured", "settled", "void", "failed"},
    "captured": {"settled", "refunded", "partially_refunded", "void"},
    "settled": {"refunded", "partially_refunded"},
    "partially_refunded": {"refunded"},
    "refunded": set(),
    "failed": set(),
    "void": set(),
}

ClaimStatus = Literal[
    "draft",          # being prepared internally
    "ready",          # passed internal scrubbing, awaiting submission
    "submitted",      # handed off to payer / clearinghouse
    "accepted",       # payer acknowledged receipt
    "rejected",       # payer rejected at intake (syntax/eligibility) — fixable
    "paid",           # remit received, fully adjudicated paid
    "partially_paid",
    "denied",         # adjudicated denial (payer refuses)
    "appealed",       # denial appeal in flight
    "closed",         # terminal — no further action
]

CLAIM_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"ready", "closed"},
    "ready": {"submitted", "draft", "closed"},
    "submitted": {"accepted", "rejected"},
    "accepted": {"paid", "partially_paid", "denied"},
    "rejected": {"draft", "ready", "closed"},  # correct & resubmit
    "partially_paid": {"paid", "denied", "appealed", "closed"},
    "paid": {"closed"},
    "denied": {"appealed", "closed"},
    "appealed": {"paid", "partially_paid", "denied", "closed"},
    "closed": set(),
}

RemittanceStatus = Literal[
    "received",       # ERA/EOB ingested, raw form
    "posted",         # allocations written to payments / invoices
    "reconciled",     # cash tie-out complete
    "disputed",       # operator flagged discrepancy
]

REMITTANCE_TRANSITIONS: dict[str, set[str]] = {
    "received": {"posted", "disputed"},
    "posted": {"reconciled", "disputed"},
    "reconciled": {"disputed"},
    "disputed": {"posted", "reconciled"},
}

DenialWorkItemStatus = Literal[
    "open",
    "in_progress",
    "resolved",
    "escalated",
    "closed",
]

DENIAL_TRANSITIONS: dict[str, set[str]] = {
    "open": {"in_progress", "closed"},
    "in_progress": {"resolved", "escalated", "closed"},
    "escalated": {"in_progress", "resolved", "closed"},
    "resolved": {"closed"},
    "closed": set(),
}

# Terminal statuses — the transition helper uses this to short-circuit.
TERMINAL_STATUSES: dict[str, set[str]] = {
    "invoice": {s for s, nxt in INVOICE_TRANSITIONS.items() if not nxt},
    "payment": {s for s, nxt in PAYMENT_TRANSITIONS.items() if not nxt},
    "claim": {s for s, nxt in CLAIM_TRANSITIONS.items() if not nxt},
    "remittance": set(),  # remittances have no truly terminal state
    "denial": {s for s, nxt in DENIAL_TRANSITIONS.items() if not nxt},
}


# ---------------------------------------------------------------------------
# Payers
# ---------------------------------------------------------------------------
PayerType = Literal["commercial", "medicare", "medicaid", "workers_comp",
                    "auto", "self_pay", "other"]
RemitMethod = Literal["era", "paper_eob", "none"]


class PayerCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    payer_type: PayerType = "commercial"
    payer_code: str | None = Field(default=None, max_length=40)
    electronic_payer_id: str | None = Field(default=None, max_length=40)
    remit_method: RemitMethod = "era"
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name cannot be blank")
        return v


class PayerUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    payer_type: PayerType | None = None
    payer_code: str | None = Field(default=None, max_length=40)
    electronic_payer_id: str | None = Field(default=None, max_length=40)
    remit_method: RemitMethod | None = None
    notes: str | None = Field(default=None, max_length=2000)
    status: Literal["active", "inactive"] | None = None


class PayerPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    tenant_id: str
    name: str
    payer_type: PayerType
    payer_code: str | None = None
    electronic_payer_id: str | None = None
    remit_method: RemitMethod
    notes: str | None = None
    status: Literal["active", "inactive"] = "active"
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Patient insurance policies
# ---------------------------------------------------------------------------
PolicyRank = Literal["primary", "secondary", "tertiary"]
RelationshipToSubscriber = Literal["self", "spouse", "child", "other"]


class PatientInsurancePolicyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patient_id: str
    payer_id: str
    rank: PolicyRank = "primary"
    subscriber_name: str = Field(min_length=1, max_length=200)
    relationship_to_subscriber: RelationshipToSubscriber = "self"
    member_id: str = Field(min_length=1, max_length=60)
    group_number: str | None = Field(default=None, max_length=60)
    effective_date: str | None = Field(
        default=None, pattern=r"^\d{4}-\d{2}-\d{2}$",
    )
    termination_date: str | None = Field(
        default=None, pattern=r"^\d{4}-\d{2}-\d{2}$",
    )
    notes: str | None = Field(default=None, max_length=2000)


class PatientInsurancePolicyPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    tenant_id: str
    patient_id: str
    payer_id: str
    rank: PolicyRank
    subscriber_name: str
    relationship_to_subscriber: RelationshipToSubscriber
    member_id: str
    group_number: str | None = None
    effective_date: str | None = None
    termination_date: str | None = None
    status: Literal["active", "inactive"] = "active"
    notes: str | None = None
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Invoices + invoice lines
# ---------------------------------------------------------------------------
CodeType = Literal["cpt", "hcpcs", "custom", "product"]


class InvoiceLineInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code_type: CodeType = "cpt"
    code: str = Field(min_length=1, max_length=20)
    description: str = Field(min_length=1, max_length=300)
    service_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    quantity: int = Field(default=1, ge=1, le=999)
    unit_price_cents: int = Field(ge=0, le=10_000_000)
    provider_id: str | None = None
    modifiers: list[str] = Field(default_factory=list, max_length=4)

    @field_validator("modifiers")
    @classmethod
    def _validate_modifiers(cls, v: list[str]) -> list[str]:
        for m in v:
            if not m or len(m) > 5:
                raise ValueError("each modifier must be 1..5 chars")
        return v


class InvoiceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patient_id: str
    location_id: str | None = None
    appointment_id: str | None = None
    currency: str = DEFAULT_CURRENCY
    due_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    notes: str | None = Field(default=None, max_length=2000)
    lines: list[InvoiceLineInput] = Field(min_length=1, max_length=200)

    @field_validator("currency")
    @classmethod
    def _currency_ok(cls, v: str) -> str:
        v = (v or "").upper()
        if v not in ALLOWED_CURRENCIES:
            raise ValueError(f"unsupported currency: {v}")
        return v


class InvoicePublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    tenant_id: str
    location_id: str | None = None
    patient_id: str
    appointment_id: str | None = None
    status: InvoiceStatus
    issued_at: str | None = None
    due_date: str | None = None
    currency: str
    subtotal_cents: int
    tax_cents: int = 0
    adjustment_cents: int = 0
    total_cents: int
    balance_cents: int
    notes: str | None = None
    created_at: str
    updated_at: str


class InvoiceLinePublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    invoice_id: str
    tenant_id: str
    sequence: int
    code_type: CodeType
    code: str
    description: str
    service_date: str
    quantity: int
    unit_price_cents: int
    total_cents: int
    modifiers: list[str] = Field(default_factory=list)
    provider_id: str | None = None


# ---------------------------------------------------------------------------
# Payments & allocations
# ---------------------------------------------------------------------------
PaymentMethod = Literal[
    "cash", "check", "card_present", "card_not_present",
    "ach", "era_posting", "hsa_fsa", "other",
]


class PaymentAllocationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    invoice_id: str
    invoice_line_id: str | None = None
    amount_cents: int = Field(ge=1, le=10_000_000)


class PaymentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patient_id: str
    location_id: str | None = None
    payer_id: str | None = None  # null means patient responsibility
    method: PaymentMethod
    amount_cents: int = Field(ge=1, le=10_000_000)
    currency: str = DEFAULT_CURRENCY
    received_at: str | None = None
    reference: str | None = Field(default=None, max_length=120)
    external_txn_id: str | None = Field(default=None, max_length=120)
    allocations: list[PaymentAllocationInput] = Field(default_factory=list, max_length=50)

    @field_validator("currency")
    @classmethod
    def _currency_ok(cls, v: str) -> str:
        v = (v or "").upper()
        if v not in ALLOWED_CURRENCIES:
            raise ValueError(f"unsupported currency: {v}")
        return v


class PaymentPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    tenant_id: str
    location_id: str | None = None
    patient_id: str
    payer_id: str | None = None
    method: PaymentMethod
    status: PaymentStatus
    amount_cents: int
    allocated_cents: int = 0
    currency: str
    received_at: str | None = None
    reference: str | None = None
    external_txn_id: str | None = None
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Refunds & adjustments
# ---------------------------------------------------------------------------
AdjustmentKind = Literal["writeoff", "discount", "courtesy", "contractual"]


class RefundCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payment_id: str
    amount_cents: int = Field(ge=1, le=10_000_000)
    reason: str = Field(min_length=1, max_length=500)


class RefundPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    tenant_id: str
    payment_id: str
    amount_cents: int
    reason: str
    status: Literal["pending", "processed", "failed"] = "pending"
    processed_at: str | None = None
    created_at: str
    updated_at: str


class AdjustmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    invoice_id: str
    invoice_line_id: str | None = None
    kind: AdjustmentKind
    amount_cents: int = Field(ge=1, le=10_000_000)
    reason: str = Field(min_length=1, max_length=500)


class AdjustmentPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    tenant_id: str
    invoice_id: str
    invoice_line_id: str | None = None
    kind: AdjustmentKind
    amount_cents: int
    reason: str
    approved_by_id: str | None = None
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Claims + child tables
# ---------------------------------------------------------------------------
class ClaimDiagnosisInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sequence: int = Field(ge=1, le=12)
    code: str = Field(min_length=1, max_length=12)   # ICD-10 e.g. M54.16


class ClaimLineInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sequence: int = Field(ge=1, le=50)
    invoice_line_id: str | None = None
    service_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    code_type: CodeType = "cpt"
    code: str = Field(min_length=1, max_length=20)
    units: int = Field(default=1, ge=1, le=999)
    billed_cents: int = Field(ge=0, le=10_000_000)
    diagnosis_pointers: list[int] = Field(default_factory=list, max_length=4)
    modifiers: list[str] = Field(default_factory=list, max_length=4)


class ClaimCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patient_id: str
    payer_id: str
    policy_id: str | None = None
    location_id: str | None = None
    service_date_from: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    service_date_to: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    diagnoses: list[ClaimDiagnosisInput] = Field(min_length=1, max_length=12)
    lines: list[ClaimLineInput] = Field(min_length=1, max_length=50)
    notes: str | None = Field(default=None, max_length=2000)


class ClaimPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    tenant_id: str
    location_id: str | None = None
    patient_id: str
    payer_id: str
    policy_id: str | None = None
    status: ClaimStatus
    service_date_from: str
    service_date_to: str
    billed_cents: int
    paid_cents: int = 0
    submitted_at: str | None = None
    accepted_at: str | None = None
    last_denial_code: str | None = None
    notes: str | None = None
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Remittances
# ---------------------------------------------------------------------------
class RemittancePublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    tenant_id: str
    payer_id: str
    status: RemittanceStatus
    received_at: str
    total_paid_cents: int
    check_or_eft_number: str | None = None
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Denial work items
# ---------------------------------------------------------------------------
class DenialWorkItemPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    tenant_id: str
    claim_id: str
    claim_line_id: str | None = None
    denial_code: str
    denial_category: str | None = None
    amount_cents: int
    status: DenialWorkItemStatus
    assigned_to_id: str | None = None
    resolution_notes: str | None = None
    opened_at: str
    closed_at: str | None = None
    created_at: str
    updated_at: str
