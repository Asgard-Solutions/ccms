"""
Phase 9 — chiropractic specialty rules on top of the portable 837P rail.

Covers:
  * Specialty detection (CMT code present) without hardcoded specialty
    flags; non-chiro claims never see any Phase 9 rule fire.
  * CMT modifier coverage — AT/GA/GY/GZ; payer-aware severity
    (Medicare + opted-in payers = error, else warning).
  * Medicare-only AT-strict rule: CMT with GA/GY/GZ but no AT =>
    error `MEDICARE_CHIRO_AT_REQUIRED`.
  * Subluxation diagnosis presence; payer-aware severity.
  * Medicare subluxation-primary ordering =>
    error `MEDICARE_CHIRO_SUBLUXATION_NOT_PRIMARY`.
  * Medicare initial treatment date required =>
    error `MEDICARE_CHIRO_INITIAL_TX_DATE_MISSING` (accepts
    `claim.initial_treatment_date` OR `claim.onset_date`).
  * POS rule stays payer-agnostic (warning).
  * Payer-level opt-in flags (`requires_at_modifier`, etc.) elevate
    the same rules to errors for non-Medicare payers.
  * Scrubber `DEFAULT_RULES` still wires all chiropractic rules in
    (no regression in the general pipeline).
  * NO "date of last x-ray" rule exists anywhere — deliberately.
"""
from __future__ import annotations

import os
import sys

_BACKEND_DIR = "/app/backend"
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from services.billing.scrubber import (  # noqa: E402
    DEFAULT_RULES,
    ScrubberContext,
    run_rules,
)


# ---------------------------------------------------------------------------
# Fixture — minimal valid ScrubberContext
# ---------------------------------------------------------------------------
def _ctx(**overrides) -> ScrubberContext:
    """Build a ScrubberContext that passes the base rules — callers
    tweak specific fields to trigger the rule under test."""
    defaults = dict(
        claim={
            "id": "c1", "patient_id": "p1", "payer_id": "pa1",
            "policy_id": "pol1",
            "place_of_service": "11", "frequency_code": "1",
            "billing_provider_id": "1234567893",
            "rendering_provider_id": "1234567893",
            "service_date_from": "2026-04-10",
            "service_date_to": "2026-04-10",
            "billed_cents": 5500,
            # Default onset_date so Medicare init-tx rule passes unless
            # a test explicitly nulls it.
            "onset_date": "2026-04-01",
        },
        patient={
            "id": "p1", "first_name": "Jane", "last_name": "Doe",
            "date_of_birth": "1985-07-15", "gender": "female",
        },
        payer={
            "id": "pa1", "name": "Acme Health Plan",
            "payer_type": "commercial", "status": "active",
            "electronic_payer_id": "60054",
            "clearinghouse_route": "none",
            "enrollment_status": "not_started",
        },
        policy={
            "id": "pol1", "rank": "primary", "status": "active",
            "member_id": "M-1",
        },
        diagnoses=[{"sequence": 1, "code": "M99.01"}],
        lines=[{
            "id": "L1", "sequence": 1, "service_date": "2026-04-10",
            "code_type": "cpt", "code": "98940", "units": 1,
            "billed_cents": 5500, "diagnosis_pointers": [1],
        }],
        line_modifiers_by_line={
            "L1": [{"modifier_code": "AT", "sequence": 1}],
        },
    )
    for k, v in overrides.items():
        defaults[k] = v
    return ScrubberContext(**defaults)


def _codes(findings: list) -> set[str]:
    return {f["code"] for f in findings}


def _by_code(findings: list) -> dict:
    return {f["code"]: f for f in findings}


# ---------------------------------------------------------------------------
# 1. Specialty detection — non-chiro claims see no Phase 9 findings
# ---------------------------------------------------------------------------
_CHIRO_CODES = {
    "CMT_MODIFIER_MISSING",
    "CMT_SUBLUXATION_DX_MISSING",
    "CHIRO_POS_ATYPICAL",
    "MEDICARE_CHIRO_AT_REQUIRED",
    "MEDICARE_CHIRO_SUBLUXATION_NOT_PRIMARY",
    "MEDICARE_CHIRO_INITIAL_TX_DATE_MISSING",
}


def test_non_chiropractic_claim_never_triggers_phase9_rules():
    ctx = _ctx(
        lines=[{
            "id": "L1", "sequence": 1, "service_date": "2026-04-10",
            "code_type": "cpt", "code": "99213", "units": 1,   # E/M, not chiro
            "billed_cents": 5500, "diagnosis_pointers": [1],
        }],
        line_modifiers_by_line={"L1": []},
        diagnoses=[{"sequence": 1, "code": "M54.16"}],
    )
    result = run_rules(ctx, DEFAULT_RULES)
    firing = _codes(result["errors"]) | _codes(result["warnings"])
    assert not (firing & _CHIRO_CODES), (
        "Phase 9 chiro rules must stay silent on non-CMT claims; "
        f"fired: {firing & _CHIRO_CODES}"
    )


# ---------------------------------------------------------------------------
# 2. CMT modifier coverage
# ---------------------------------------------------------------------------
def test_cmt_without_any_modifier_warns_for_commercial_payer():
    ctx = _ctx(line_modifiers_by_line={"L1": []})
    result = run_rules(ctx, DEFAULT_RULES)
    warn_codes = _codes(result["warnings"])
    assert "CMT_MODIFIER_MISSING" in warn_codes
    # Commercial payer with the default opt-in off → NOT an error.
    assert "CMT_MODIFIER_MISSING" not in _codes(result["errors"])


def test_cmt_without_any_modifier_is_error_for_medicare():
    ctx = _ctx(
        payer={**_ctx().payer, "payer_type": "medicare"},
        line_modifiers_by_line={"L1": []},
    )
    result = run_rules(ctx, DEFAULT_RULES)
    err_codes = _codes(result["errors"])
    assert "CMT_MODIFIER_MISSING" in err_codes


def test_cmt_with_at_modifier_passes_modifier_rules():
    ctx = _ctx(
        payer={**_ctx().payer, "payer_type": "medicare"},
        line_modifiers_by_line={"L1": [{"modifier_code": "AT", "sequence": 1}]},
    )
    result = run_rules(ctx, DEFAULT_RULES)
    assert "CMT_MODIFIER_MISSING" not in _codes(result["errors"])
    assert "MEDICARE_CHIRO_AT_REQUIRED" not in _codes(result["errors"])


def test_medicare_cmt_with_gy_only_errors_at_required():
    ctx = _ctx(
        payer={**_ctx().payer, "payer_type": "medicare"},
        line_modifiers_by_line={"L1": [{"modifier_code": "GY", "sequence": 1}]},
    )
    result = run_rules(ctx, DEFAULT_RULES)
    err_codes = _codes(result["errors"])
    assert "MEDICARE_CHIRO_AT_REQUIRED" in err_codes
    # AT rule is distinct from the bare CMT_MODIFIER_MISSING rule —
    # GY counts as a CMT modifier so that one should NOT fire.
    assert "CMT_MODIFIER_MISSING" not in err_codes


def test_commercial_payer_with_at_opt_in_escalates_to_error():
    ctx = _ctx(
        payer={**_ctx().payer,
               "payer_type": "commercial",
               "requires_at_modifier": True},
        line_modifiers_by_line={"L1": []},
    )
    result = run_rules(ctx, DEFAULT_RULES)
    assert "CMT_MODIFIER_MISSING" in _codes(result["errors"])


# ---------------------------------------------------------------------------
# 3. Subluxation presence + primary ordering
# ---------------------------------------------------------------------------
def test_cmt_without_subluxation_warns_for_commercial():
    ctx = _ctx(diagnoses=[{"sequence": 1, "code": "M54.16"}])
    result = run_rules(ctx, DEFAULT_RULES)
    assert "CMT_SUBLUXATION_DX_MISSING" in _codes(result["warnings"])
    assert "CMT_SUBLUXATION_DX_MISSING" not in _codes(result["errors"])


def test_cmt_without_subluxation_errors_for_medicare():
    ctx = _ctx(
        payer={**_ctx().payer, "payer_type": "medicare"},
        diagnoses=[{"sequence": 1, "code": "M54.16"}],
    )
    result = run_rules(ctx, DEFAULT_RULES)
    assert "CMT_SUBLUXATION_DX_MISSING" in _codes(result["errors"])


def test_medicare_subluxation_secondary_fails_primary_ordering_rule():
    """Medicare requires M99.0x in the primary slot; a non-subluxation
    primary with subluxation in secondary must surface as an error."""
    ctx = _ctx(
        payer={**_ctx().payer, "payer_type": "medicare"},
        diagnoses=[
            {"sequence": 1, "code": "M54.16"},
            {"sequence": 2, "code": "M99.01"},
        ],
    )
    result = run_rules(ctx, DEFAULT_RULES)
    err_codes = _codes(result["errors"])
    assert "MEDICARE_CHIRO_SUBLUXATION_NOT_PRIMARY" in err_codes
    # The presence rule must NOT double-fire (subluxation is present,
    # just out of order).
    assert "CMT_SUBLUXATION_DX_MISSING" not in err_codes


def test_medicare_subluxation_primary_passes():
    ctx = _ctx(
        payer={**_ctx().payer, "payer_type": "medicare"},
        diagnoses=[
            {"sequence": 1, "code": "M99.01"},
            {"sequence": 2, "code": "M54.16"},
        ],
    )
    result = run_rules(ctx, DEFAULT_RULES)
    err_codes = _codes(result["errors"])
    assert "MEDICARE_CHIRO_SUBLUXATION_NOT_PRIMARY" not in err_codes


def test_commercial_payer_opts_in_to_subluxation_primary():
    ctx = _ctx(
        payer={**_ctx().payer,
               "payer_type": "commercial",
               "requires_subluxation_primary": True},
        diagnoses=[
            {"sequence": 1, "code": "M54.16"},
            {"sequence": 2, "code": "M99.01"},
        ],
    )
    result = run_rules(ctx, DEFAULT_RULES)
    assert "MEDICARE_CHIRO_SUBLUXATION_NOT_PRIMARY" in _codes(result["errors"])


# ---------------------------------------------------------------------------
# 4. Initial treatment date
# ---------------------------------------------------------------------------
def test_medicare_requires_initial_treatment_date_and_errors_when_missing():
    ctx = _ctx(
        payer={**_ctx().payer, "payer_type": "medicare"},
        claim={**_ctx().claim, "onset_date": None},
    )
    result = run_rules(ctx, DEFAULT_RULES)
    assert "MEDICARE_CHIRO_INITIAL_TX_DATE_MISSING" in _codes(result["errors"])


def test_medicare_accepts_explicit_initial_treatment_date():
    ctx = _ctx(
        payer={**_ctx().payer, "payer_type": "medicare"},
        claim={**_ctx().claim,
               "onset_date": None,
               "initial_treatment_date": "2026-03-01"},
    )
    result = run_rules(ctx, DEFAULT_RULES)
    assert "MEDICARE_CHIRO_INITIAL_TX_DATE_MISSING" not in _codes(result["errors"])


def test_medicare_accepts_onset_date_fallback_for_initial_treatment():
    ctx = _ctx(
        payer={**_ctx().payer, "payer_type": "medicare"},
        claim={**_ctx().claim,
               "onset_date": "2026-02-15",
               "initial_treatment_date": None},
    )
    result = run_rules(ctx, DEFAULT_RULES)
    assert "MEDICARE_CHIRO_INITIAL_TX_DATE_MISSING" not in _codes(result["errors"])


def test_commercial_does_not_require_initial_treatment_date_by_default():
    ctx = _ctx(
        claim={**_ctx().claim,
               "onset_date": None, "initial_treatment_date": None},
    )
    result = run_rules(ctx, DEFAULT_RULES)
    all_codes = _codes(result["errors"]) | _codes(result["warnings"])
    assert "MEDICARE_CHIRO_INITIAL_TX_DATE_MISSING" not in all_codes


def test_commercial_payer_opts_into_initial_treatment_date_requirement():
    ctx = _ctx(
        payer={**_ctx().payer,
               "payer_type": "commercial",
               "requires_initial_treatment_date": True},
        claim={**_ctx().claim,
               "onset_date": None, "initial_treatment_date": None},
    )
    result = run_rules(ctx, DEFAULT_RULES)
    assert "MEDICARE_CHIRO_INITIAL_TX_DATE_MISSING" in _codes(result["errors"])


# ---------------------------------------------------------------------------
# 5. POS rule — still payer-agnostic
# ---------------------------------------------------------------------------
def test_atypical_pos_warns_regardless_of_payer():
    ctx = _ctx(claim={**_ctx().claim, "place_of_service": "81"})
    result = run_rules(ctx, DEFAULT_RULES)
    assert "CHIRO_POS_ATYPICAL" in _codes(result["warnings"])
    assert "CHIRO_POS_ATYPICAL" not in _codes(result["errors"])


# ---------------------------------------------------------------------------
# 6. Isolation — scrubber.py must not reference specialty specifics
# ---------------------------------------------------------------------------
def test_general_scrubber_does_not_hardcode_chiro_cpt_codes_anymore():
    """Specialty logic should live exclusively in the specialty module —
    the general scrubber MUST NOT re-hardcode CMT code lists or the
    chiro modifier frozenset."""
    import pathlib
    src = pathlib.Path(_BACKEND_DIR, "services/billing/scrubber.py").read_text()
    # These constants moved to the specialty package; scrubber.py should
    # only carry the back-compat shim symbols, not the code tables.
    assert "_CHIROPRACTIC_CMT_CODES" not in src
    assert "_TYPICAL_CHIRO_POS" not in src
    assert "_CHIRO_CMT_MODIFIERS" not in src


def test_no_last_xray_rule_exists_anywhere():
    """CMS removed the 'date of last x-ray' requirement years ago. Our
    ruleset must NOT carry a hardcoded last-xray rule — Phase 9
    explicitly forbids it."""
    import pathlib
    for p in pathlib.Path(_BACKEND_DIR, "services/billing").rglob("*.py"):
        text = p.read_text().lower()
        # Allow literary references in docstrings/comments that contain
        # the word; but the rule itself must not be named / coded.
        assert "rule_last_xray" not in text
        assert "last_xray_required" not in text


# ---------------------------------------------------------------------------
# 7. Rule pipeline integration — Phase 9 rules are registered
# ---------------------------------------------------------------------------
def test_default_rules_registers_all_phase9_chiropractic_rules():
    names = {fn.__name__ for fn in DEFAULT_RULES}
    assert "rule_medicare_chiro_at_modifier_required" in names
    assert "rule_medicare_chiro_subluxation_primary" in names
    assert "rule_medicare_chiro_initial_treatment_date" in names
    assert "rule_chiropractic_subluxation_present" in names
    # No duplicate registration — the pipeline is a set-equivalent list.
    assert len({fn.__name__ for fn in DEFAULT_RULES}) == len(DEFAULT_RULES)


# ---------------------------------------------------------------------------
# 8. End-to-end smoke — findings bucket correctly
# ---------------------------------------------------------------------------
def test_medicare_clean_chiropractic_claim_passes_all_rules():
    ctx = _ctx(
        payer={**_ctx().payer, "payer_type": "medicare",
               "clearinghouse_route": "none"},
        diagnoses=[
            {"sequence": 1, "code": "M99.01"},
            {"sequence": 2, "code": "M54.16"},
        ],
    )
    result = run_rules(ctx, DEFAULT_RULES)
    # The only acceptable errors are ones NOT in our Phase 9 set —
    # and in this fixture there should be zero chiro errors.
    chiro_errors = _codes(result["errors"]) & _CHIRO_CODES
    assert chiro_errors == set(), (
        f"Clean Medicare claim should pass all Phase 9 chiro rules; "
        f"errors fired: {chiro_errors}"
    )


def test_medicare_worst_case_chiropractic_claim_produces_multiple_errors():
    ctx = _ctx(
        payer={**_ctx().payer, "payer_type": "medicare"},
        claim={**_ctx().claim,
               "onset_date": None, "initial_treatment_date": None,
               "place_of_service": "81"},
        diagnoses=[{"sequence": 1, "code": "M54.16"}],
        line_modifiers_by_line={"L1": []},
    )
    result = run_rules(ctx, DEFAULT_RULES)
    err_codes = _codes(result["errors"])
    assert "CMT_MODIFIER_MISSING" in err_codes
    assert "CMT_SUBLUXATION_DX_MISSING" in err_codes
    assert "MEDICARE_CHIRO_INITIAL_TX_DATE_MISSING" in err_codes
    # POS still a warning regardless.
    assert "CHIRO_POS_ATYPICAL" in _codes(result["warnings"])
