"""
services/billing/specialty/chiropractic.py — chiropractic-specialty
validation rules.

Phase 9 adds a layered ruleset for chiropractic billing on top of the
standard professional (837P) rail:

  * **Detection**: every rule starts with `is_chiropractic_claim(ctx)`
    so it short-circuits harmlessly on non-chiro claims. Chiro intent
    is inferred from CPT 98940-98943 presence (no manual flag). This
    means a mixed practice can add a chiro line to any claim and the
    right rules fire automatically.
  * **Payer-aware severity**: Medicare-oriented rules elevate missing
    AT / subluxation-primary / initial-treatment-date to `error`;
    commercial payers see the same conditions as `warning`. The
    `payer_type` field on the Payer record is the single source of
    truth — no hardcoded payer names.
  * **Payer routing hooks**: the rule set consults two `payer.*` flags
    added in Phase 6/9 so clinics can opt-in to stricter behaviour for
    specific payers (e.g. a commercial BCBS plan that actually audits
    AT modifiers hard):
        - `payer.requires_at_modifier` (bool)
        - `payer.requires_subluxation_primary` (bool)
        - `payer.requires_initial_treatment_date` (bool)
    When absent or false, the rule only fires for Medicare. When
    present and true, the rule fires for that payer too (elevated
    to error).
  * **Isolation**: nothing in the general `scrubber.py` inspects
    `payer_type` for chiropractic logic — that stays here.
  * **No deprecated behaviour**: we intentionally DO NOT require the
    old "date of last x-ray" — CMS removed that mandate years ago.
"""
from __future__ import annotations

from services.billing.scrubber import ScrubberContext, ScrubberFinding

# ---------------------------------------------------------------------------
# Constants — shared with other modules via the package `__init__`
# ---------------------------------------------------------------------------
CHIROPRACTIC_CMT_CODES: frozenset[str] = frozenset({
    "98940", "98941", "98942", "98943",
})
# Modifiers that distinguish active treatment (AT) from maintenance
# (GA/GY/GZ) on CMT lines.
CHIRO_CMT_MODIFIERS: frozenset[str] = frozenset({"AT", "GA", "GY", "GZ"})
# Common chiropractic places of service. Anything outside this set
# warrants a sanity-check warning regardless of payer.
TYPICAL_CHIRO_POS: frozenset[str] = frozenset({
    "11", "12", "22", "49", "99",
})


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------
def is_chiropractic_claim(ctx: ScrubberContext) -> bool:
    """A claim counts as chiropractic when at least one line carries a
    CMT code. We intentionally do NOT use a `clinic.specialty` flag —
    a mixed-practice clinic (DC + DPT) may bill both on the same day
    and the rules should only fire when the codes actually warrant it.
    """
    return any(
        (ln.get("code") or "").strip() in CHIROPRACTIC_CMT_CODES
        for ln in (ctx.lines or [])
    )


def is_medicare_payer(ctx: ScrubberContext) -> bool:
    payer = ctx.payer or {}
    return (payer.get("payer_type") or "").strip().lower() == "medicare"


def _payer_flag(ctx: ScrubberContext, key: str) -> bool:
    """Phase 9 — read an opt-in payer requirement flag. The fields are
    carried on `billing_payers` (see `PayerCreate` / `PayerPublic`).
    Defaults to False so existing payer rows never trigger stricter
    rules until an operator explicitly opts in."""
    payer = ctx.payer or {}
    return bool(payer.get(key))


def _severity_for_medicare_rule(ctx: ScrubberContext, *, opt_in_flag: str) -> str:
    """Medicare always = error. Other payers = error only when they
    opted in via the dedicated flag, otherwise warning."""
    if is_medicare_payer(ctx) or _payer_flag(ctx, opt_in_flag):
        return "error"
    return "warning"


# ---------------------------------------------------------------------------
# Rules — CMT modifier coverage
# ---------------------------------------------------------------------------
def rule_chiropractic_cmt_modifier(ctx: ScrubberContext) -> list[ScrubberFinding]:
    """CPT 98940-98943 without an AT/GA/GY/GZ modifier is a top denial
    category. Medicare explicitly requires AT for active-care claims."""
    if not is_chiropractic_claim(ctx):
        return []
    findings: list[ScrubberFinding] = []
    for i, ln in enumerate(ctx.lines):
        code = (ln.get("code") or "").strip()
        if code not in CHIROPRACTIC_CMT_CODES:
            continue
        mods = ctx.line_modifiers_by_line.get(ln.get("id") or "", [])
        mod_codes = {(m.get("modifier_code") or "").upper() for m in mods}
        if mod_codes & CHIRO_CMT_MODIFIERS:
            continue
        severity = _severity_for_medicare_rule(
            ctx, opt_in_flag="requires_at_modifier",
        )
        findings.append(ScrubberFinding(
            code="CMT_MODIFIER_MISSING", severity=severity,
            message=(
                f"Line {ln.get('sequence')} ({code}) is a CMT code but "
                "carries none of AT/GA/GY/GZ. "
                "Medicare requires AT for active treatment; other payers "
                "commonly deny unmodified CMT as maintenance care."
            ),
            entity_path=f"lines[{i}].modifiers",
            category="chiropractic",
        ))
    return findings


def rule_medicare_chiro_at_modifier_required(
    ctx: ScrubberContext,
) -> list[ScrubberFinding]:
    """Medicare-only: for each CMT line, the AT modifier specifically
    (not just any CMT modifier) signals active treatment. A line with
    only GA/GY/GZ is effectively a maintenance / liability waiver and
    must not be billed to Medicare as a primary active-care claim."""
    if not is_chiropractic_claim(ctx):
        return []
    if not (is_medicare_payer(ctx)
            or _payer_flag(ctx, "requires_at_modifier")):
        return []
    findings: list[ScrubberFinding] = []
    for i, ln in enumerate(ctx.lines):
        code = (ln.get("code") or "").strip()
        if code not in CHIROPRACTIC_CMT_CODES:
            continue
        mods = ctx.line_modifiers_by_line.get(ln.get("id") or "", [])
        mod_codes = {(m.get("modifier_code") or "").upper() for m in mods}
        if "AT" in mod_codes:
            continue
        # GA/GY/GZ present without AT — flag with a specific message so
        # the operator knows this is a maintenance/exclusion line that
        # Medicare won't pay as primary.
        if mod_codes & {"GA", "GY", "GZ"}:
            findings.append(ScrubberFinding(
                code="MEDICARE_CHIRO_AT_REQUIRED", severity="error",
                message=(
                    f"Line {ln.get('sequence')} ({code}) is billed to "
                    "Medicare with GA/GY/GZ but no AT modifier. "
                    "Medicare only pays chiropractic manipulation when AT "
                    "(active treatment) is present."
                ),
                entity_path=f"lines[{i}].modifiers",
                category="chiropractic",
            ))
    return findings


# ---------------------------------------------------------------------------
# Rules — diagnosis ordering (subluxation primary)
# ---------------------------------------------------------------------------
_SUBLUXATION_PREFIX = "M99.0"


def _is_subluxation(code: str | None) -> bool:
    return (code or "").upper().replace(" ", "").startswith(_SUBLUXATION_PREFIX)


def rule_chiropractic_subluxation_present(
    ctx: ScrubberContext,
) -> list[ScrubberFinding]:
    """Any CMT claim should carry an M99.0x subluxation diagnosis."""
    if not is_chiropractic_claim(ctx):
        return []
    if any(_is_subluxation(d.get("code")) for d in (ctx.diagnoses or [])):
        return []
    severity = _severity_for_medicare_rule(
        ctx, opt_in_flag="requires_subluxation_primary",
    )
    return [ScrubberFinding(
        code="CMT_SUBLUXATION_DX_MISSING", severity=severity,
        message=(
            "Chiropractic manipulative treatment is billed but no "
            "subluxation diagnosis (ICD-10 M99.0x) is present. "
            "Medicare requires it; commercial payers commonly expect it."
        ),
        entity_path="diagnoses",
        category="chiropractic",
    )]


def rule_medicare_chiro_subluxation_primary(
    ctx: ScrubberContext,
) -> list[ScrubberFinding]:
    """Medicare-only: the primary diagnosis (sequence=1) MUST be the
    subluxation. Secondary ICD-10 pain codes are allowed but must not
    displace the subluxation from the primary slot."""
    if not is_chiropractic_claim(ctx):
        return []
    if not (is_medicare_payer(ctx)
            or _payer_flag(ctx, "requires_subluxation_primary")):
        return []
    diagnoses = sorted(
        list(ctx.diagnoses or []),
        key=lambda d: int(d.get("sequence") or 99),
    )
    if not diagnoses:
        return []   # rule_diagnoses_present covers the base case
    primary = diagnoses[0]
    if _is_subluxation(primary.get("code")):
        return []
    # Is a subluxation anywhere in the list but out of order?
    if any(_is_subluxation(d.get("code")) for d in diagnoses):
        return [ScrubberFinding(
            code="MEDICARE_CHIRO_SUBLUXATION_NOT_PRIMARY",
            severity="error",
            message=(
                "Medicare chiropractic claims require the M99.0x "
                "subluxation diagnosis in the primary (sequence 1) "
                "slot — it's currently ordered after a non-subluxation "
                "code."
            ),
            entity_path="diagnoses",
            category="chiropractic",
        )]
    # No subluxation anywhere — let `rule_chiropractic_subluxation_present`
    # raise it (avoids double-reporting).
    return []


# ---------------------------------------------------------------------------
# Rules — initial treatment date
# ---------------------------------------------------------------------------
def rule_medicare_chiro_initial_treatment_date(
    ctx: ScrubberContext,
) -> list[ScrubberFinding]:
    """Medicare requires an initial treatment date (X12 DTP*454) on
    chiropractic claims so CMS can track the active-treatment episode.
    We accept either an explicit `initial_treatment_date` or fall back
    to `onset_date` (same semantics for chiro single-episode claims)."""
    if not is_chiropractic_claim(ctx):
        return []
    if not (is_medicare_payer(ctx)
            or _payer_flag(ctx, "requires_initial_treatment_date")):
        return []
    claim = ctx.claim or {}
    candidate = (claim.get("initial_treatment_date")
                 or claim.get("onset_date")
                 or "").strip()
    if candidate:
        return []
    return [ScrubberFinding(
        code="MEDICARE_CHIRO_INITIAL_TX_DATE_MISSING",
        severity="error",
        message=(
            "Medicare chiropractic claims require the initial treatment "
            "date of the current condition (X12 DTP*454). Set either "
            "`initial_treatment_date` or `onset_date` on the claim."
        ),
        entity_path="onset_date",
        category="chiropractic",
    )]


# ---------------------------------------------------------------------------
# Rules — place of service (kept as a warning regardless of payer)
# ---------------------------------------------------------------------------
def rule_chiropractic_pos_typical(ctx: ScrubberContext) -> list[ScrubberFinding]:
    """Chiropractic services outside the common POS set warrant a
    review — not a block. Same behaviour across payers."""
    if not is_chiropractic_claim(ctx):
        return []
    pos = (ctx.claim.get("place_of_service") or "").strip()
    if not pos or not (pos.isdigit() and len(pos) == 2):
        return []
    if pos in TYPICAL_CHIRO_POS:
        return []
    return [ScrubberFinding(
        code="CHIRO_POS_ATYPICAL", severity="warning",
        message=(
            f"Place of service {pos} is unusual for chiropractic — "
            "verify the encounter location."
        ),
        entity_path="place_of_service",
        category="chiropractic",
    )]


# ---------------------------------------------------------------------------
# Rule manifest — the order is deliberate:
#   1. CMT modifier + AT-specific
#   2. Subluxation presence + primary ordering
#   3. Initial treatment date
#   4. POS check (least blocking, runs last)
# ---------------------------------------------------------------------------
CHIROPRACTIC_RULES = [
    rule_chiropractic_cmt_modifier,
    rule_medicare_chiro_at_modifier_required,
    rule_chiropractic_subluxation_present,
    rule_medicare_chiro_subluxation_primary,
    rule_medicare_chiro_initial_treatment_date,
    rule_chiropractic_pos_typical,
]
