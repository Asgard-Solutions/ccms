"""
services/billing/denial_categories.py — taxonomy for denial codes.

Maps ANSI CARC (Claim Adjustment Reason Codes) + some common payer
custom codes to a small set of operational categories used for queue
grouping. Unknown codes fall through to `"other"`.

Operators can override `denial_category` manually via the denial work
item PUT endpoint — this module only *seeds* the mapping at posting
time.
"""
from __future__ import annotations

# Supported categories (stable contract with the UI).
DENIAL_CATEGORIES: list[str] = [
    "coding",         # CPT / ICD / modifier / bundling
    "eligibility",    # coverage / subscriber / plan
    "authorization",  # pre-auth / referral
    "timely_filing",  # filed past payer window
    "duplicate",      # already processed
    "other",          # unmapped / miscellaneous
]

DENIAL_CATEGORY_LABELS: dict[str, str] = {
    "coding": "Coding / bundling",
    "eligibility": "Eligibility",
    "authorization": "Authorization",
    "timely_filing": "Timely filing",
    "duplicate": "Duplicate",
    "other": "Other / unmapped",
}

# Code prefixes (canonical `CO-nn`) mapped to categories.
# See https://x12.org/codes/claim-adjustment-reason-codes for source.
CODE_TO_CATEGORY: dict[str, str] = {
    # ---- Coding / bundling / medical necessity
    "CO-4": "coding",
    "CO-8": "coding",
    "CO-11": "coding",
    "CO-16": "coding",        # "missing information"
    "CO-50": "coding",        # medical necessity
    "CO-96": "coding",
    "CO-97": "coding",        # bundled
    "CO-151": "coding",
    "CO-236": "coding",
    "CO-B15": "coding",

    # ---- Eligibility / coverage
    "CO-26": "eligibility",
    "CO-27": "eligibility",
    "CO-31": "eligibility",
    "CO-32": "eligibility",
    "CO-33": "eligibility",
    "CO-177": "eligibility",  # invalid subscriber id
    "CO-204": "eligibility",  # not covered under plan

    # ---- Authorization / referrals
    "CO-15": "authorization", # invalid auth number
    "CO-197": "authorization",
    "CO-198": "authorization",
    "CO-272": "authorization",

    # ---- Timely filing
    "CO-29": "timely_filing",

    # ---- Duplicate
    "CO-18": "duplicate",
}


def normalize_code(code: str | None) -> str:
    """Uppercase + strip + ensure CO-XX form when possible."""
    if not code:
        return ""
    c = str(code).strip().upper()
    # Accept "CO97" or "97" as "CO-97"
    if c.isdigit():
        c = f"CO-{c}"
    elif c.startswith("CO") and not c.startswith("CO-"):
        c = f"CO-{c[2:]}"
    return c


def derive_category(code: str | None) -> str:
    """Return a category for the given denial code; never raises."""
    key = normalize_code(code)
    return CODE_TO_CATEGORY.get(key, "other")
