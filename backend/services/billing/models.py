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
    "partially_paid": {"paid", "adjusted", "void", "issued"},
    "paid": {"refunded", "adjusted", "partially_paid", "issued"},
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
    "validation_failed",  # scrubber found blocking errors
    "ready",          # passed internal scrubbing, awaiting submission
    "submitted",      # handed off to payer / clearinghouse
    "accepted",       # payer acknowledged receipt
    "pending",        # payer working the claim (after ack, pre-adjudication)
    "rejected",       # payer rejected at intake (syntax/eligibility) — fixable
    "paid",           # remit received, fully adjudicated paid
    "partially_paid",
    "denied",         # adjudicated denial (payer refuses)
    "appealed",       # denial appeal in flight
    "closed",         # terminal — no further action
]

CLAIM_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"ready", "validation_failed", "closed"},
    "validation_failed": {"draft", "ready", "closed"},
    "ready": {"submitted", "draft", "validation_failed", "closed"},
    "submitted": {"accepted", "rejected", "pending"},
    "accepted": {"paid", "partially_paid", "denied", "pending"},
    "pending": {"accepted", "paid", "partially_paid", "denied", "rejected"},
    "rejected": {"draft", "validation_failed", "ready", "closed"},
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

# Phase 2a — clearinghouse routing
#
# `clearinghouse_route` names the adapter used to transmit claims /
# pull ERAs for this payer. `"none"` keeps the current manual workflow
# (operator posts claims via paper/fax/portal). Additional adapters
# register in `services.billing.clearinghouse.routing`.
ClearinghouseRoute = Literal[
    "none",
    "change_healthcare",
    "optum",
    "availity",
    "waystar",
]
# How claims leave the system for this payer. Defaults to `portal` to
# preserve the existing manual workflow until clearinghouse enrollment
# has flipped the payer to `edi`.
ClaimSubmissionMode = Literal["edi", "portal", "paper"]
# Enrollment progress toward a clearinghouse adapter. Gate real EDI
# submission on `enrolled`.
EnrollmentStatus = Literal[
    "not_started", "in_progress", "enrolled", "suspended",
]


class PayerCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    payer_type: PayerType = "commercial"
    payer_code: str | None = Field(default=None, max_length=40)
    electronic_payer_id: str | None = Field(default=None, max_length=40)
    remit_method: RemitMethod = "era"
    notes: str | None = Field(default=None, max_length=2000)
    # Phase 2a — clearinghouse routing (all optional / safe defaults)
    clearinghouse_route: ClearinghouseRoute = "none"
    claim_submission_mode: ClaimSubmissionMode = "portal"
    enrollment_status: EnrollmentStatus = "not_started"
    trading_partner_id: str | None = Field(default=None, max_length=60)

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
    # Phase 2a — clearinghouse routing
    clearinghouse_route: ClearinghouseRoute | None = None
    claim_submission_mode: ClaimSubmissionMode | None = None
    enrollment_status: EnrollmentStatus | None = None
    trading_partner_id: str | None = Field(default=None, max_length=60)


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
    # Phase 2a — clearinghouse routing (defaults keep legacy rows valid).
    clearinghouse_route: ClearinghouseRoute = "none"
    claim_submission_mode: ClaimSubmissionMode = "portal"
    enrollment_status: EnrollmentStatus = "not_started"
    trading_partner_id: str | None = None
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Phase 5 — Providers + Service Facilities
# ---------------------------------------------------------------------------
# A clinic-side directory of the real NPI / Tax-ID / address records
# needed to populate 837P loops 2010AA (Billing provider), 2310B
# (Rendering), and 2310C (Service facility). Existing claims still
# carry free-text `billing_provider_id` / `rendering_provider_id` /
# `facility_id` FKs — when a matching row exists in these collections
# the clearinghouse payload builder resolves it to full wire shape;
# otherwise the ID is passed through as-is for backward compat.
ProviderKind = Literal[
    "billing",      # loop 2010AA (group / type-2 NPI)
    "rendering",    # loop 2310B (individual / type-1 NPI)
    "referring",    # loop 2310A
    "supervising",  # loop 2310D
]


class ProviderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: ProviderKind
    name: str = Field(min_length=1, max_length=200)
    # NPI type-1 (individual, 10 digits) or type-2 (organisation).
    npi: str = Field(min_length=10, max_length=10, pattern=r"^\d{10}$")
    # EIN or SSN; 9 digits with optional dash. Required for billing
    # providers (2010AA), optional elsewhere.
    tax_id: str | None = Field(default=None, max_length=15)
    taxonomy_code: str | None = Field(default=None, max_length=20)
    phone: str | None = Field(default=None, max_length=30)
    address: dict | None = None
    notes: str | None = Field(default=None, max_length=2000)


class ProviderUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: ProviderKind | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    npi: str | None = Field(
        default=None, min_length=10, max_length=10, pattern=r"^\d{10}$",
    )
    tax_id: str | None = Field(default=None, max_length=15)
    taxonomy_code: str | None = Field(default=None, max_length=20)
    phone: str | None = Field(default=None, max_length=30)
    address: dict | None = None
    status: Literal["active", "inactive"] | None = None
    notes: str | None = Field(default=None, max_length=2000)


class ProviderPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    tenant_id: str
    kind: ProviderKind
    name: str
    npi: str
    tax_id: str | None = None
    taxonomy_code: str | None = None
    phone: str | None = None
    address: dict | None = None
    status: Literal["active", "inactive"] = "active"
    notes: str | None = None
    created_at: str
    updated_at: str


class ServiceFacilityCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    # Type-2 NPI for the facility location (2310C). Optional — when
    # absent the billing provider's address is used on the wire.
    npi: str | None = Field(
        default=None, min_length=10, max_length=10, pattern=r"^\d{10}$",
    )
    address: dict | None = None
    phone: str | None = Field(default=None, max_length=30)
    notes: str | None = Field(default=None, max_length=2000)


class ServiceFacilityUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    npi: str | None = Field(
        default=None, min_length=10, max_length=10, pattern=r"^\d{10}$",
    )
    address: dict | None = None
    phone: str | None = Field(default=None, max_length=30)
    status: Literal["active", "inactive"] | None = None
    notes: str | None = Field(default=None, max_length=2000)


class ServiceFacilityPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    tenant_id: str
    name: str
    npi: str | None = None
    address: dict | None = None
    phone: str | None = None
    status: Literal["active", "inactive"] = "active"
    notes: str | None = None
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
    # Phase 5 — structured subscriber fields required on 837P when
    # the subscriber is not the patient (SBR loop 2000B). These are
    # optional on create to preserve the legacy flat intake shape;
    # the scrubber enforces presence when `relationship != self`.
    subscriber_dob: str | None = Field(
        default=None, pattern=r"^\d{4}-\d{2}-\d{2}$",
    )
    subscriber_gender: Literal["M", "F", "U"] | None = None
    # Loose dict to keep address parsing out of this schema. Expected
    # keys: street1 / street2 / city / state / postal_code / country.
    subscriber_address: dict | None = None
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
    # Phase 5 — structured subscriber fields (see note above).
    subscriber_dob: str | None = None
    subscriber_gender: Literal["M", "F", "U"] | None = None
    subscriber_address: dict | None = None
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

    # Legacy-data tolerance: older rows (pre-2026-Q1) used
    # `status="completed"` before the gateway-aware status machine
    # was introduced. Normalise them to `captured` on read so the
    # list endpoint doesn't 500 on historical data. Writes still go
    # through the modern Literal above.
    @field_validator("status", mode="before")
    @classmethod
    def _coerce_legacy_status(cls, value):
        if value == "completed":
            return "captured"
        return value


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


ClaimType = Literal["professional", "institutional"]


class ClaimCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patient_id: str
    payer_id: str
    policy_id: str | None = None
    location_id: str | None = None
    source_invoice_id: str | None = None
    claim_type: ClaimType = "professional"
    place_of_service: str | None = Field(default=None, max_length=2)   # CMS POS code e.g. "11"
    frequency_code: str = Field(default="1", max_length=1)             # 1 original, 7 corrected, 8 voided
    billing_provider_id: str | None = None
    rendering_provider_id: str | None = None
    facility_id: str | None = None
    authorization_number: str | None = Field(default=None, max_length=60)
    referral_number: str | None = Field(default=None, max_length=60)
    # Phase 5 — 837P foundational fields.
    # PCN: provider-assigned unique claim id sent in CLM01. Must be
    # unique per tenant. When absent, the router auto-assigns
    # `CCMS-<8char>` derived from the claim's uuid so existing clients
    # keep working.
    patient_control_number: str | None = Field(default=None, max_length=38)
    accident_date: str | None = Field(
        default=None, pattern=r"^\d{4}-\d{2}-\d{2}$",
    )
    onset_date: str | None = Field(
        default=None, pattern=r"^\d{4}-\d{2}-\d{2}$",
    )
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
    source_invoice_id: str | None = None
    claim_type: ClaimType = "professional"
    place_of_service: str | None = None
    frequency_code: str = "1"
    billing_provider_id: str | None = None
    rendering_provider_id: str | None = None
    facility_id: str | None = None
    authorization_number: str | None = None
    referral_number: str | None = None
    # Phase 5 — 837P foundational fields
    patient_control_number: str | None = None
    # Populated from remittance / 277CA events — the payer's claim
    # control number (ICN / DCN). Empty until the payer responds.
    payer_claim_control_number: str | None = None
    accident_date: str | None = None
    onset_date: str | None = None
    status: ClaimStatus
    service_date_from: str
    service_date_to: str
    billed_cents: int
    paid_cents: int = 0
    submitted_at: str | None = None
    accepted_at: str | None = None
    last_denial_code: str | None = None
    notes: str | None = None
    # Scrubber summary — most recent validation result
    validation_error_count: int = 0
    validation_warning_count: int = 0
    validation_last_run_at: str | None = None
    # Phase 4 — operational workflow fields
    assigned_to: str | None = None
    last_submission_at: str | None = None
    submission_count: int = 0
    # Phase 2b — queue enrichment. Populated by the named-queue
    # endpoint from the `claim_events` stream so operators see "last
    # activity" without drilling into the timeline. Always `None` on
    # create / detail / patch responses.
    last_event: str | None = None
    last_event_at: str | None = None
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Phase 4 — Claim submissions + outcomes
# ---------------------------------------------------------------------------
SubmissionMethod = Literal["manual_paper", "manual_portal", "batch_file"]
SubmissionOutcomeKind = Literal[
    "accepted", "rejected", "pending",
    "paid", "partially_paid", "denied",
]


class ClaimSubmissionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: SubmissionMethod
    external_reference: str | None = Field(default=None, max_length=60)
    notes: str | None = Field(default=None, max_length=2000)


class ClaimSubmissionOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: SubmissionOutcomeKind
    payer_reference: str | None = Field(default=None, max_length=60)
    denial_code: str | None = Field(default=None, max_length=20)
    paid_cents: int | None = Field(default=None, ge=0, le=10_000_000)
    notes: str | None = Field(default=None, max_length=2000)


class ClaimSubmissionPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    tenant_id: str
    claim_id: str
    method: SubmissionMethod
    external_reference: str | None = None
    submitted_at: str
    submitted_by: str
    payload_format: str          # "json" | "x12-837p-preview"
    payload_size_bytes: int
    # Outcome fields (populated once recorded)
    outcome: SubmissionOutcomeKind | None = None
    outcome_at: str | None = None
    outcome_by: str | None = None
    payer_reference: str | None = None
    denial_code: str | None = None
    paid_cents: int | None = None
    notes: str | None = None


class ClaimAssignmentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assigned_to: str | None = None


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


# ---------------------------------------------------------------------------
# Phase 5 — Remittances, AR aging, statements
# ---------------------------------------------------------------------------
class RemittanceLineInput(BaseModel):
    """One adjudicated line inside a remittance claim row."""
    model_config = ConfigDict(extra="forbid")
    claim_line_id: str | None = None
    cpt_code: str | None = Field(default=None, max_length=20)
    billed_cents: int = Field(ge=0, le=10_000_000)
    paid_cents: int = Field(default=0, ge=0, le=10_000_000)
    contractual_cents: int = Field(default=0, ge=0, le=10_000_000)
    patient_resp_cents: int = Field(default=0, ge=0, le=10_000_000)
    denied_cents: int = Field(default=0, ge=0, le=10_000_000)
    denial_code: str | None = Field(default=None, max_length=20)
    denial_category: str | None = Field(default=None, max_length=60)


class RemittanceClaimInput(BaseModel):
    """Adjudication of one claim inside a remittance."""
    model_config = ConfigDict(extra="forbid")
    claim_id: str
    payer_control_number: str | None = Field(default=None, max_length=60)
    billed_cents: int = Field(ge=0, le=10_000_000)
    paid_cents: int = Field(default=0, ge=0, le=10_000_000)
    contractual_cents: int = Field(default=0, ge=0, le=10_000_000)
    patient_resp_cents: int = Field(default=0, ge=0, le=10_000_000)
    denied_cents: int = Field(default=0, ge=0, le=10_000_000)
    denial_code: str | None = Field(default=None, max_length=20)
    lines: list[RemittanceLineInput] = Field(default_factory=list, max_length=50)


class RemittancePostRequest(BaseModel):
    """Create + post a remittance in one call."""
    model_config = ConfigDict(extra="forbid")
    payer_id: str
    received_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}")
    check_or_eft_number: str | None = Field(default=None, max_length=60)
    total_paid_cents: int = Field(ge=0, le=100_000_000)
    notes: str | None = Field(default=None, max_length=2000)
    claims: list[RemittanceClaimInput] = Field(min_length=1, max_length=100)


class RemittanceClaimPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    tenant_id: str
    remittance_id: str
    claim_id: str
    payer_control_number: str | None = None
    billed_cents: int
    paid_cents: int
    contractual_cents: int
    patient_resp_cents: int
    denied_cents: int
    denial_code: str | None = None
    created_at: str


class RemittanceLinePublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    tenant_id: str
    remittance_claim_id: str
    claim_line_id: str | None = None
    cpt_code: str | None = None
    billed_cents: int
    paid_cents: int
    contractual_cents: int
    patient_resp_cents: int
    denied_cents: int
    denial_code: str | None = None
    denial_category: str | None = None
    created_at: str


class DenialWorkItemUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: DenialWorkItemStatus | None = None
    assigned_to_id: str | None = None
    resolution_notes: str | None = Field(default=None, max_length=4000)
    denial_category: str | None = Field(default=None, max_length=60)


class StatementPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    tenant_id: str
    patient_id: str
    generated_at: str
    generated_by: str
    as_of_date: str
    total_balance_cents: int
    invoice_count: int
    body: str           # rendered plain-text statement
    invoice_breakdown: list[dict] = Field(default_factory=list)
    sent_at: str | None = None
    sent_via: str | None = None   # "email" | "mail" | "portal"
    sent_to: str | None = None    # redacted recipient for audit
    created_at: str


class AgingBucket(BaseModel):
    model_config = ConfigDict(extra="ignore")
    bucket: str         # e.g. "0-30"
    min_days: int
    max_days: int | None # null means "open-ended 120+"
    balance_cents: int
    invoice_count: int


# ---------------------------------------------------------------------------
# Phase 2a — Claim event stream
# ---------------------------------------------------------------------------
# An append-only chronological record of every transport- or state-
# significant thing that happened to a claim. Unlike the claim status
# enum (which stays minimal and portable), events let us record
# adapter-specific acknowledgments (999 / 277CA), ERA linkage,
# resubmissions, and appeal lifecycle without exploding the canonical
# status vocabulary.
#
# Writers: services/billing/events.py::emit_claim_event
# Readers: GET /api/billing/claims/{id}/events, ClaimDetail timeline.
ClaimEventType = Literal[
    "created",
    "validated",
    "submitted",
    "resubmitted",
    "ack_999_accepted",
    "ack_999_rejected",
    "ack_277ca_accepted",
    "ack_277ca_rejected",
    "outcome_recorded",
    "era_posted",
    "denied",
    "appeal_filed",
    "assigned",
    "voided",
    "closed",
]


class ClaimEventPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    tenant_id: str
    claim_id: str
    event_type: ClaimEventType
    # Optional links to transport records / remittances.
    submission_id: str | None = None
    remittance_id: str | None = None
    # Which clearinghouse adapter (if any) emitted this event.
    adapter_route: str | None = None
    # Optional CARC/denial code or payer reference surfaced on the event.
    denial_code: str | None = None
    # Free-form structured payload — intentionally untyped so adapter-
    # specific echoes (999 details, 277CA snapshots, etc.) can ride along
    # without bloating the canonical model.
    payload: dict | None = None
    # `from_status` / `to_status` are populated for status-moving events
    # so the timeline can render "draft → ready" without re-joining.
    from_status: str | None = None
    to_status: str | None = None
    occurred_at: str
    recorded_by: str | None = None
    created_at: str


# ---------------------------------------------------------------------------
# Phase 2c — Clearinghouse enrollments
# ---------------------------------------------------------------------------
# Per-tenant, per-payer record of enrollment progress with a specific
# clearinghouse. Gates real submissions: an adapter may refuse to
# transmit claims for a payer whose enrollment is not `enrolled`.
#
# Storage collection: `clearinghouse_enrollments`.
# Uniqueness:         (tenant_id, payer_id, clearinghouse)
EnrollmentState = Literal[
    "not_started", "in_progress", "enrolled", "suspended",
]


class ClearinghouseEnrollmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payer_id: str = Field(min_length=1)
    clearinghouse: ClearinghouseRoute
    status: EnrollmentState = "not_started"
    submitter_id: str | None = Field(default=None, max_length=60)
    trading_partner_id: str | None = Field(default=None, max_length=60)
    notes: str | None = Field(default=None, max_length=2000)


class ClearinghouseEnrollmentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: EnrollmentState | None = None
    submitter_id: str | None = Field(default=None, max_length=60)
    trading_partner_id: str | None = Field(default=None, max_length=60)
    notes: str | None = Field(default=None, max_length=2000)


class ClearinghouseEnrollmentPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    tenant_id: str
    payer_id: str
    clearinghouse: ClearinghouseRoute
    status: EnrollmentState
    submitter_id: str | None = None
    trading_partner_id: str | None = None
    notes: str | None = None
    created_at: str
    updated_at: str


class ClearinghouseConfigSummary(BaseModel):
    """Env-sourced, secret-free summary of one registered adapter."""
    model_config = ConfigDict(extra="ignore")
    route_id: str
    mode: str                        # "disabled" | "sandbox" | "production"
    base_url: str | None = None
    has_client_id: bool = False
    has_client_secret: bool = False
    client_id_hint: str | None = None
    env_prefix: str | None = None
    supports_edi: bool = False
    supports_era: bool = False
    supports_eligibility: bool = False
