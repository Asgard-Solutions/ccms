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


@dataclass
class ScrubberFinding:
    code: str
    severity: Severity
    message: str
    entity_path: str = ""
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
        )]
    return []


def rule_has_payer(ctx: ScrubberContext) -> list[ScrubberFinding]:
    if not ctx.payer:
        return [ScrubberFinding(
            code="PAYER_MISSING", severity="error",
            message="Payer record not found for this claim.",
            entity_path="payer_id",
        )]
    return []


def rule_has_active_policy(ctx: ScrubberContext) -> list[ScrubberFinding]:
    """Insurance claims need an active policy tied to the same payer."""
    if not ctx.policy:
        return [ScrubberFinding(
            code="POLICY_MISSING", severity="error",
            message="No insurance policy linked to this claim.",
            entity_path="policy_id",
        )]
    findings: list[ScrubberFinding] = []
    if ctx.policy.get("status") != "active":
        findings.append(ScrubberFinding(
            code="POLICY_INACTIVE", severity="error",
            message=f"Policy is {ctx.policy.get('status')}.",
            entity_path="policy_id",
        ))
    if ctx.payer and ctx.policy.get("payer_id") != ctx.payer.get("id"):
        findings.append(ScrubberFinding(
            code="POLICY_PAYER_MISMATCH", severity="error",
            message="Policy payer does not match claim payer.",
            entity_path="policy_id",
        ))
    if not (ctx.policy.get("member_id") or "").strip():
        findings.append(ScrubberFinding(
            code="POLICY_MEMBER_ID_MISSING", severity="error",
            message="Subscriber / member ID is required for insurance claims.",
            entity_path="policy_id",
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
        ))
    if not c.get("rendering_provider_id"):
        findings.append(ScrubberFinding(
            code="RENDERING_PROVIDER_MISSING", severity="warning",
            message="Rendering provider is recommended (payers often require it).",
            entity_path="rendering_provider_id",
        ))
    pos = c.get("place_of_service")
    if not pos:
        findings.append(ScrubberFinding(
            code="PLACE_OF_SERVICE_MISSING", severity="error",
            message="Place of service code is required.",
            entity_path="place_of_service",
        ))
    elif not (pos.isdigit() and len(pos) == 2):
        findings.append(ScrubberFinding(
            code="PLACE_OF_SERVICE_FORMAT", severity="error",
            message="Place of service must be a 2-digit CMS code.",
            entity_path="place_of_service",
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
        )]
    return []


def rule_diagnoses_present(ctx: ScrubberContext) -> list[ScrubberFinding]:
    if not ctx.diagnoses:
        return [ScrubberFinding(
            code="DIAGNOSES_MISSING", severity="error",
            message="At least one diagnosis is required.",
            entity_path="diagnoses",
        )]
    # Sequence uniqueness + 1..12 contiguous-ish.
    seqs = [d.get("sequence") for d in ctx.diagnoses]
    if len(seqs) != len(set(seqs)):
        return [ScrubberFinding(
            code="DIAGNOSIS_SEQUENCE_DUPLICATE", severity="error",
            message="Diagnosis sequences must be unique.",
            entity_path="diagnoses",
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
            ))
            continue
        # Loose ICD-10 format: letter + 2-3 digits, optional `.` + 1-4 chars.
        # Full validation needs a code table; this catches obvious typos.
        if not (len(code) >= 3 and code[0].isalpha()):
            findings.append(ScrubberFinding(
                code="DIAGNOSIS_CODE_SUSPECT", severity="warning",
                message=f"Diagnosis '{code}' does not look like an ICD-10 code.",
                entity_path=f"diagnoses[{i}].code",
            ))
    return findings


def rule_lines_present(ctx: ScrubberContext) -> list[ScrubberFinding]:
    if not ctx.lines:
        return [ScrubberFinding(
            code="LINES_MISSING", severity="error",
            message="At least one service line is required.",
            entity_path="lines",
        )]
    seqs = [ln.get("sequence") for ln in ctx.lines]
    if len(seqs) != len(set(seqs)):
        return [ScrubberFinding(
            code="LINE_SEQUENCE_DUPLICATE", severity="error",
            message="Claim line sequences must be unique.",
            entity_path="lines",
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
            ))
        if int(ln.get("billed_cents", 0)) <= 0:
            findings.append(ScrubberFinding(
                code="LINE_BILLED_ZERO", severity="error",
                message=f"Line {ln.get('sequence')} has zero billed amount.",
                entity_path=f"lines[{i}].billed_cents",
            ))
        code = (ln.get("code") or "").strip()
        if not code:
            findings.append(ScrubberFinding(
                code="LINE_CODE_MISSING", severity="error",
                message=f"Line {ln.get('sequence')} has no procedure code.",
                entity_path=f"lines[{i}].code",
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
        )]
    return []


# Order matters: header-level rules run before lines so the UI shows
# "fix your header first" before drilling into line detail.
DEFAULT_RULES: list[Rule] = [
    rule_has_patient,
    rule_has_payer,
    rule_has_active_policy,
    rule_required_header_fields,
    rule_service_date_range,
    rule_diagnoses_present,
    rule_diagnosis_code_format,
    rule_lines_present,
    rule_line_diagnosis_pointers,
    rule_line_units_and_billed,
    rule_modifier_format,
    rule_billed_total_matches_header,
]


def run_rules(ctx: ScrubberContext,
              rules: list[Rule] | None = None) -> dict:
    """Execute all rules and return a grouped result dict suitable for
    JSON responses or persistence."""
    rules = rules if rules is not None else DEFAULT_RULES
    findings: list[ScrubberFinding] = []
    for r in rules:
        try:
            findings.extend(r(ctx))
        except Exception as exc:     # defensive — one broken rule shouldn't kill the rest
            findings.append(ScrubberFinding(
                code="RULE_INTERNAL_ERROR", severity="error",
                message=f"Rule {r.__name__} failed: {exc}",
            ))
    errors = [f.__dict__ for f in findings if f.severity == "error"]
    warnings = [f.__dict__ for f in findings if f.severity == "warning"]
    return {
        "errors": errors,
        "warnings": warnings,
        "passed": not errors,
    }
