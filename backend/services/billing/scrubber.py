"""
Claim Scrubber — pluggable rule engine.

Design
------
A claim scrubber runs a sequence of **rules**. Each rule looks at the
claim document (header + diagnoses + lines + contextual data such as
payer / policy / patient) and emits zero or more `ScrubberFinding`
objects.

A finding has a severity:

* ``error``   — blocks the claim from advancing to `ready`.
* ``warning`` — does not block, but should be surfaced for reviewer
  attention (e.g. likely payer rejection).

Rules are **pure functions** of the claim context — they do not touch
the database. This makes them easy to test and reorder. Payer-specific
rules can plug in later by checking ``ctx.payer_type`` / custom flags.

Each finding carries an ``entity_path`` (e.g. ``"lines[2].diagnosis_pointers"``)
so the UI can highlight the offending field. Findings are grouped in
the response payload for display.

The scrubber is deliberately decoupled from the 837/CMS-1500 export
format — we validate the canonical model and a later adapter handles
the wire format.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal

Severity = Literal["error", "warning"]

# Phase 4 — issue categories for UI grouping. Every finding carries a
# category so the ClaimDetail panel can render "Identity / Provider /
# Codes / Dates / Totals / Routing / Chiropractic" sections instead
# of a flat list. Categories are labels only — they do not affect
# whether a finding blocks submission (that's severity's job).
FindingCategory = Literal[
    "identity",      # patient / payer / policy / subscriber
    "provider",      # NPI, tax ID, billing address, rendering
    "codes",         # diagnosis / procedure / modifiers
    "dates",         # service date range, future dates
    "totals",        # billed totals, units
    "routing",       # clearinghouse routing & enrollment
    "chiropractic",  # specialty-specific
    "other",
]


@dataclass
class ScrubberFinding:
    code: str
    severity: Severity
    message: str
    entity_path: str = ""
    # Phase 4 — grouping hint for the UI. Defaults to `other` so old
    # rules keep working without modification.
    category: FindingCategory = "other"
    # Optional payer-specific marker for later rules.
    payer_scope: str | None = None


@dataclass
class ScrubberContext:
    """Everything a rule might need to validate a claim.

    The context is built by `router.run_scrubber()`; rules receive a
    read-only view and never mutate it.
    """
    claim: dict
    diagnoses: list[dict]
    lines: list[dict]
    line_modifiers_by_line: dict[str, list[dict]]
    patient: dict | None = None
    payer: dict | None = None
    policy: dict | None = None
    # Free-form bag for future expansion (e.g. fee schedule lookups).
    extras: dict = field(default_factory=dict)


Rule = Callable[[ScrubberContext], list[ScrubberFinding]]


# ---------------------------------------------------------------------------
# Individual rules — small, focused, independently testable
# ---------------------------------------------------------------------------
def rule_has_patient(ctx: ScrubberContext) -> list[ScrubberFinding]:
    if not ctx.patient:
        return [ScrubberFinding(
            code="PATIENT_MISSING", severity="error",
            message="Patient record not found for this claim.",
            entity_path="patient_id",
            category="identity",
        )]
    return []


def rule_has_payer(ctx: ScrubberContext) -> list[ScrubberFinding]:
    if not ctx.payer:
        return [ScrubberFinding(
            code="PAYER_MISSING", severity="error",
            message="Payer record not found for this claim.",
            entity_path="payer_id",
            category="identity",
        )]
    return []


def rule_has_active_policy(ctx: ScrubberContext) -> list[ScrubberFinding]:
    """Insurance claims need an active policy tied to the same payer."""
    if not ctx.policy:
        return [ScrubberFinding(
            code="POLICY_MISSING", severity="error",
            message="No insurance policy linked to this claim.",
            entity_path="policy_id",
            category="identity",
        )]
    findings: list[ScrubberFinding] = []
    if ctx.policy.get("status") != "active":
        findings.append(ScrubberFinding(
            code="POLICY_INACTIVE", severity="error",
            message=f"Policy is {ctx.policy.get('status')}.",
            entity_path="policy_id",
            category="identity",
        ))
    if ctx.payer and ctx.policy.get("payer_id") != ctx.payer.get("id"):
        findings.append(ScrubberFinding(
            code="POLICY_PAYER_MISMATCH", severity="error",
            message="Policy payer does not match claim payer.",
            entity_path="policy_id",
            category="identity",
        ))
    if not (ctx.policy.get("member_id") or "").strip():
        findings.append(ScrubberFinding(
            code="POLICY_MEMBER_ID_MISSING", severity="error",
            message="Subscriber / member ID is required for insurance claims.",
            entity_path="policy_id",
            category="identity",
        ))
    # Subscriber name — soft signal. Many claims auto-inherit from
    # patient; warn if both are blank.
    sub_name = (ctx.policy.get("subscriber_name") or "").strip()
    if not sub_name:
        findings.append(ScrubberFinding(
            code="POLICY_SUBSCRIBER_NAME_MISSING", severity="warning",
            message="Subscriber name is blank.",
            entity_path="policy_id",
            category="identity",
        ))
    return findings


def rule_required_header_fields(ctx: ScrubberContext) -> list[ScrubberFinding]:
    findings: list[ScrubberFinding] = []
    c = ctx.claim
    if not c.get("billing_provider_id"):
        findings.append(ScrubberFinding(
            code="BILLING_PROVIDER_MISSING", severity="error",
            message="Billing provider is required.",
            entity_path="billing_provider_id",
            category="provider",
        ))
    if not c.get("rendering_provider_id"):
        findings.append(ScrubberFinding(
            code="RENDERING_PROVIDER_MISSING", severity="warning",
            message="Rendering provider is recommended (payers often require it).",
            entity_path="rendering_provider_id",
            category="provider",
        ))
    pos = c.get("place_of_service")
    if not pos:
        findings.append(ScrubberFinding(
            code="PLACE_OF_SERVICE_MISSING", severity="error",
            message="Place of service code is required.",
            entity_path="place_of_service",
            category="codes",
        ))
    elif not (pos.isdigit() and len(pos) == 2):
        findings.append(ScrubberFinding(
            code="PLACE_OF_SERVICE_FORMAT", severity="error",
            message="Place of service must be a 2-digit CMS code.",
            entity_path="place_of_service",
            category="codes",
        ))
    return findings


def rule_service_date_range(ctx: ScrubberContext) -> list[ScrubberFinding]:
    c = ctx.claim
    dfrom = c.get("service_date_from") or ""
    dto = c.get("service_date_to") or ""
    if dfrom and dto and dfrom > dto:
        return [ScrubberFinding(
            code="SERVICE_DATE_RANGE_INVALID", severity="error",
            message="service_date_from must be <= service_date_to.",
            entity_path="service_date_from",
            category="dates",
        )]
    return []


def rule_diagnoses_present(ctx: ScrubberContext) -> list[ScrubberFinding]:
    if not ctx.diagnoses:
        return [ScrubberFinding(
            code="DIAGNOSES_MISSING", severity="error",
            message="At least one diagnosis is required.",
            entity_path="diagnoses",
            category="codes",
        )]
    # Sequence uniqueness + 1..12 contiguous-ish.
    seqs = [d.get("sequence") for d in ctx.diagnoses]
    if len(seqs) != len(set(seqs)):
        return [ScrubberFinding(
            code="DIAGNOSIS_SEQUENCE_DUPLICATE", severity="error",
            message="Diagnosis sequences must be unique.",
            entity_path="diagnoses",
            category="codes",
        )]
    return []


def rule_diagnosis_code_format(ctx: ScrubberContext) -> list[ScrubberFinding]:
    findings: list[ScrubberFinding] = []
    for i, d in enumerate(ctx.diagnoses):
        code = (d.get("code") or "").strip()
        if not code:
            findings.append(ScrubberFinding(
                code="DIAGNOSIS_CODE_EMPTY", severity="error",
                message=f"Diagnosis {d.get('sequence')} has no code.",
                entity_path=f"diagnoses[{i}].code",
                category="codes",
            ))
            continue
        # Loose ICD-10 format: letter + 2-3 digits, optional `.` + 1-4 chars.
        # Full validation needs a code table; this catches obvious typos.
        if not (len(code) >= 3 and code[0].isalpha()):
            findings.append(ScrubberFinding(
                code="DIAGNOSIS_CODE_SUSPECT", severity="warning",
                message=f"Diagnosis '{code}' does not look like an ICD-10 code.",
                entity_path=f"diagnoses[{i}].code",
                category="codes",
            ))
    return findings


def rule_lines_present(ctx: ScrubberContext) -> list[ScrubberFinding]:
    if not ctx.lines:
        return [ScrubberFinding(
            code="LINES_MISSING", severity="error",
            message="At least one service line is required.",
            entity_path="lines",
            category="codes",
        )]
    seqs = [ln.get("sequence") for ln in ctx.lines]
    if len(seqs) != len(set(seqs)):
        return [ScrubberFinding(
            code="LINE_SEQUENCE_DUPLICATE", severity="error",
            message="Claim line sequences must be unique.",
            entity_path="lines",
            category="codes",
        )]
    return []


def rule_line_diagnosis_pointers(ctx: ScrubberContext) -> list[ScrubberFinding]:
    """Every diagnosis pointer on a line must reference an existing
    diagnosis sequence on the claim."""
    findings: list[ScrubberFinding] = []
    valid = {d.get("sequence") for d in ctx.diagnoses}
    for i, ln in enumerate(ctx.lines):
        ptrs = ln.get("diagnosis_pointers") or []
        if not ptrs:
            findings.append(ScrubberFinding(
                code="LINE_DX_POINTER_MISSING", severity="error",
                message=f"Line {ln.get('sequence')} has no diagnosis pointers.",
                entity_path=f"lines[{i}].diagnosis_pointers",
                category="codes",
            ))
            continue
        invalid = [p for p in ptrs if p not in valid]
        if invalid:
            findings.append(ScrubberFinding(
                code="LINE_DX_POINTER_INVALID", severity="error",
                message=(
                    f"Line {ln.get('sequence')} points to unknown diagnoses "
                    f"{invalid}."
                ),
                entity_path=f"lines[{i}].diagnosis_pointers",
                category="codes",
            ))
    return findings


def rule_line_units_and_billed(ctx: ScrubberContext) -> list[ScrubberFinding]:
    findings: list[ScrubberFinding] = []
    for i, ln in enumerate(ctx.lines):
        if int(ln.get("units", 0)) <= 0:
            findings.append(ScrubberFinding(
                code="LINE_UNITS_NONPOSITIVE", severity="error",
                message=f"Line {ln.get('sequence')} has non-positive units.",
                entity_path=f"lines[{i}].units",
                category="totals",
            ))
        if int(ln.get("billed_cents", 0)) <= 0:
            findings.append(ScrubberFinding(
                code="LINE_BILLED_ZERO", severity="error",
                message=f"Line {ln.get('sequence')} has zero billed amount.",
                entity_path=f"lines[{i}].billed_cents",
                category="totals",
            ))
        code = (ln.get("code") or "").strip()
        if not code:
            findings.append(ScrubberFinding(
                code="LINE_CODE_MISSING", severity="error",
                message=f"Line {ln.get('sequence')} has no procedure code.",
                entity_path=f"lines[{i}].code",
                category="codes",
            ))
    return findings


def rule_modifier_format(ctx: ScrubberContext) -> list[ScrubberFinding]:
    """Modifiers are 2-char CPT / HCPCS codes. Warn on anything that's
    shaped weirdly rather than block — payers differ."""
    findings: list[ScrubberFinding] = []
    for i, ln in enumerate(ctx.lines):
        mods = ctx.line_modifiers_by_line.get(ln["id"], [])
        if len(mods) > 4:
            findings.append(ScrubberFinding(
                code="LINE_MODIFIERS_TOO_MANY", severity="error",
                message=f"Line {ln.get('sequence')} has >4 modifiers.",
                entity_path=f"lines[{i}].modifiers",
                category="codes",
            ))
        for m in mods:
            mc = (m.get("modifier_code") or "").strip()
            if not mc or len(mc) > 5:
                findings.append(ScrubberFinding(
                    code="LINE_MODIFIER_FORMAT", severity="warning",
                    message=(
                        f"Line {ln.get('sequence')} modifier {mc!r} "
                        "is an unusual length."
                    ),
                    entity_path=f"lines[{i}].modifiers",
                    category="codes",
                ))
    return findings


def rule_billed_total_matches_header(ctx: ScrubberContext) -> list[ScrubberFinding]:
    expected = sum(int(ln.get("billed_cents", 0)) * int(ln.get("units", 1))
                   for ln in ctx.lines)
    header = int(ctx.claim.get("billed_cents") or 0)
    if expected != header:
        return [ScrubberFinding(
            code="BILLED_TOTAL_MISMATCH", severity="warning",
            message=(
                f"Claim header billed ({header}) does not match line sum "
                f"({expected})."
            ),
            entity_path="billed_cents",
            category="totals",
        )]
    return []


# ---------------------------------------------------------------------------
# Phase 4 — additional rules covering provider, dates, routing.
# Chiropractic-specialty rules moved to `services.billing.specialty.chiropractic`
# (Phase 9) so specialty logic stays isolated.
# ---------------------------------------------------------------------------
_VALID_FREQUENCY_CODES: frozenset[str] = frozenset({"1", "7", "8"})


def rule_patient_dob_required(ctx: ScrubberContext) -> list[ScrubberFinding]:
    """CMS-1500 / 837P both require the subscriber's / patient's date of
    birth on every insurance claim."""
    if not ctx.patient:
        return []  # already caught by rule_has_patient
    dob = (ctx.patient.get("date_of_birth") or "").strip()
    if not dob:
        return [ScrubberFinding(
            code="PATIENT_DOB_MISSING", severity="error",
            message="Patient date of birth is required for claim submission.",
            entity_path="patient_id",
            category="identity",
        )]
    return []


def rule_patient_gender_recommended(ctx: ScrubberContext) -> list[ScrubberFinding]:
    if not ctx.patient:
        return []
    gender = (ctx.patient.get("gender") or ctx.patient.get("sex") or "").strip()
    if not gender:
        return [ScrubberFinding(
            code="PATIENT_GENDER_MISSING", severity="warning",
            message=(
                "Patient gender is blank. Most payers require Box 3 / "
                "SBR03 to be populated."
            ),
            entity_path="patient_id",
            category="identity",
        )]
    return []


def rule_service_date_not_future(ctx: ScrubberContext) -> list[ScrubberFinding]:
    """Services rendered in the future are a common data-entry mistake
    (typo in year) — warn rather than block to allow pre-billing of
    same-day appointments in edge timezones."""
    from datetime import date
    c = ctx.claim
    dto = (c.get("service_date_to") or "").strip()
    if not dto:
        return []
    try:
        yyyy, mm, dd = dto.split("-")
        d = date(int(yyyy), int(mm), int(dd))
    except Exception:
        return [ScrubberFinding(
            code="SERVICE_DATE_FORMAT", severity="error",
            message=f"service_date_to {dto!r} is not ISO-8601 (YYYY-MM-DD).",
            entity_path="service_date_to",
            category="dates",
        )]
    if d > date.today():
        return [ScrubberFinding(
            code="SERVICE_DATE_FUTURE", severity="warning",
            message=(
                f"Service date {dto} is in the future — check for typos "
                "before submitting."
            ),
            entity_path="service_date_to",
            category="dates",
        )]
    return []


def rule_frequency_code_valid(ctx: ScrubberContext) -> list[ScrubberFinding]:
    freq = (ctx.claim.get("frequency_code") or "1").strip()
    if freq not in _VALID_FREQUENCY_CODES:
        return [ScrubberFinding(
            code="FREQUENCY_CODE_INVALID", severity="error",
            message=(
                f"Frequency code {freq!r} is invalid. Expected 1 "
                "(original), 7 (replacement), or 8 (void)."
            ),
            entity_path="frequency_code",
            category="codes",
        )]
    return []


def rule_provider_npi_format(ctx: ScrubberContext) -> list[ScrubberFinding]:
    """NPI must be a 10-digit numeric. We don't require the field to be
    populated here (that's `rule_required_header_fields`) — we only
    flag values that clearly aren't NPIs so submission isn't rejected
    by the clearinghouse for a silly reason."""
    findings: list[ScrubberFinding] = []
    for field_name, cat_path in (
        ("billing_provider_id", "billing_provider_id"),
        ("rendering_provider_id", "rendering_provider_id"),
    ):
        val = (ctx.claim.get(field_name) or "").strip()
        if not val:
            continue
        if val.isdigit() and len(val) == 10:
            continue
        # Tolerate alphanumeric "internal" ids (e.g. our seeded IDs
        # like "BP-TEST") with a warning rather than an error — the
        # submission adapter is expected to translate them.
        findings.append(ScrubberFinding(
            code="PROVIDER_NPI_FORMAT", severity="warning",
            message=(
                f"{field_name} {val!r} is not a 10-digit NPI; submission "
                "adapter must translate before sending."
            ),
            entity_path=cat_path,
            category="provider",
        ))
    return findings


def rule_chiropractic_cmt_modifier(ctx: ScrubberContext) -> list[ScrubberFinding]:   # noqa: E501
    """Back-compat shim — real implementation lives in
    `services.billing.specialty.chiropractic`. Retained so any existing
    import continues to work."""
    from services.billing.specialty.chiropractic import (
        rule_chiropractic_cmt_modifier as _impl,
    )
    return _impl(ctx)


def rule_chiropractic_pos_typical(ctx: ScrubberContext) -> list[ScrubberFinding]:
    from services.billing.specialty.chiropractic import (
        rule_chiropractic_pos_typical as _impl,
    )
    return _impl(ctx)


def rule_payer_routing_ready(ctx: ScrubberContext) -> list[ScrubberFinding]:
    """If the payer is configured for EDI submission, it MUST be
    enrolled with a clearinghouse before claims can be submitted."""
    p = ctx.payer
    if not p:
        return []   # rule_has_payer already covers this
    mode = (p.get("claim_submission_mode") or "").lower()
    if mode == "edi":
        enrollment = (p.get("enrollment_status") or "not_started").lower()
        route = (p.get("clearinghouse_route") or "none").lower()
        if route == "none":
            return [ScrubberFinding(
                code="PAYER_ROUTING_NONE", severity="error",
                message=(
                    "Payer is set to EDI submission but no clearinghouse "
                    "route is configured."
                ),
                entity_path="payer_id",
                category="routing",
            )]
        if enrollment != "enrolled":
            return [ScrubberFinding(
                code="PAYER_NOT_ENROLLED", severity="error",
                message=(
                    f"Payer is set to EDI but clearinghouse enrollment "
                    f"is {enrollment!r} — submission will fail."
                ),
                entity_path="payer_id",
                category="routing",
            )]
    return []


def rule_chiropractic_subluxation_suggested(
    ctx: ScrubberContext,
) -> list[ScrubberFinding]:
    """Back-compat alias — superseded by
    `specialty.chiropractic.rule_chiropractic_subluxation_present`."""
    from services.billing.specialty.chiropractic import (
        rule_chiropractic_subluxation_present as _impl,
    )
    return _impl(ctx)


# Phase 9 — chiropractic specialty rule set, owned by
# `services.billing.specialty.chiropractic`. Imported at module load
# so `DEFAULT_RULES` is deterministic; each rule self-gates on
# `is_chiropractic_claim(ctx)` so the order below is harmless for
# non-chiro claims.
from services.billing.specialty import CHIROPRACTIC_RULES   # noqa: E402


# Order matters: header-level rules run before lines so the UI shows
# "fix your header first" before drilling into line detail. Specialty
# rules run last so the header/line fundamentals settle first.
DEFAULT_RULES: list[Rule] = [
    rule_has_patient,
    rule_has_payer,
    rule_has_active_policy,
    rule_patient_dob_required,
    rule_patient_gender_recommended,
    rule_required_header_fields,
    rule_provider_npi_format,
    rule_service_date_range,
    rule_service_date_not_future,
    rule_frequency_code_valid,
    rule_payer_routing_ready,
    rule_diagnoses_present,
    rule_diagnosis_code_format,
    rule_lines_present,
    rule_line_diagnosis_pointers,
    rule_line_units_and_billed,
    rule_modifier_format,
    *CHIROPRACTIC_RULES,
    rule_billed_total_matches_header,
]


def run_rules(ctx: ScrubberContext,
              rules: list[Rule] | None = None) -> dict:
    """Execute all rules and return a grouped result dict suitable for
    JSON responses or persistence.

    Returned shape:
        {
          "errors":   [ {code, severity, message, entity_path, category}, ... ],
          "warnings": [ ... ],
          "passed":   bool,
          "by_category": {
              "identity":    {"errors": N, "warnings": M},
              "provider":    {...},
              ...
          }
        }
    """
    rules = rules if rules is not None else DEFAULT_RULES
    findings: list[ScrubberFinding] = []
    for r in rules:
        try:
            findings.extend(r(ctx))
        except Exception as exc:     # defensive — one broken rule shouldn't kill the rest
            findings.append(ScrubberFinding(
                code="RULE_INTERNAL_ERROR", severity="error",
                message=f"Rule {r.__name__} failed: {exc}",
                category="other",
            ))
    errors = [f.__dict__ for f in findings if f.severity == "error"]
    warnings = [f.__dict__ for f in findings if f.severity == "warning"]
    # Aggregate per category for the UI summary panel.
    by_category: dict[str, dict[str, int]] = {}
    for f in findings:
        bucket = by_category.setdefault(
            f.category, {"errors": 0, "warnings": 0},
        )
        bucket["errors" if f.severity == "error" else "warnings"] += 1
    return {
        "errors": errors,
        "warnings": warnings,
        "passed": not errors,
        "by_category": by_category,
    }
