"""
services/billing/specialty/ — specialty-specific validation rules.

Specialty rules keep disease/payer-specific logic out of the general
claim engine. Each sub-module (e.g. `chiropractic`) exposes its rule
set as a plain list of callables compatible with `scrubber.Rule`, so
the default pipeline can stitch them in without needing a resolver.

Design tenets
-------------
  * Specialty rules **do not** assume their applicability — each rule
    inspects the claim context (lines / codes / payer_type) before
    emitting findings. A commercial-dental claim passing through the
    scrubber should never fire a Medicare-chiro rule.
  * Severity is payer-aware inside each rule: Medicare-oriented chiro
    rules elevate common patterns to `error`; non-Medicare payers get
    a `warning` for the same condition. This keeps a single rule
    definition instead of branching the whole pipeline.
  * The modules export their rule list so tests / feature-flag
    pipelines can assemble custom rule sets without re-importing every
    individual callable.
"""
from services.billing.specialty.chiropractic import (
    CHIROPRACTIC_RULES,
    CHIROPRACTIC_CMT_CODES,
    CHIRO_CMT_MODIFIERS,
    TYPICAL_CHIRO_POS,
    is_chiropractic_claim,
    is_medicare_payer,
)

__all__ = [
    "CHIROPRACTIC_RULES",
    "CHIROPRACTIC_CMT_CODES",
    "CHIRO_CMT_MODIFIERS",
    "TYPICAL_CHIRO_POS",
    "is_chiropractic_claim",
    "is_medicare_payer",
]
