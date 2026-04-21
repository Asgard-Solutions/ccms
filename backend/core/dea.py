"""
DEA (Drug Enforcement Administration) registration number validation.

Structure (9 characters):
  * 1st char  — registrant-type letter (A,B,C,D,E,F,G,H,J,K,L,M,P,R,S,
    T,U,X — subset of A-Z excluding I,N,O,Q,V,W,Y,Z). This app uses the
    DEA-published practitioner and institutional set.
  * 2nd char  — any uppercase letter A-Z. Conventionally the first
    letter of the registrant's last name, but DEA allows approved
    exceptions (marriage, legal name changes, institutional DEAs,
    etc.) so we don't enforce a last-name match — a caller who wants
    the soft-warn can use `matches_last_name_initial()`.
  * chars 3-8 — 6 digits (the numeric body).
  * char 9    — single check digit.

Checksum (DEA spec):
  1. odd  = digit[1] + digit[3] + digit[5]   (1-indexed within the 6-digit block)
  2. even = (digit[2] + digit[4] + digit[6]) * 2
  3. total = odd + even
  4. check digit = total % 10

Example: ``AB1234563``
  odd = 1 + 3 + 5 = 9
  even = (2 + 4 + 6) * 2 = 24
  total = 33
  check digit = 33 % 10 = 3  ✓

IMPORTANT: Passing this validator means the DEA number is
*structurally* plausible. It does NOT prove the number is real,
active, assigned, or in good standing. A real authoritative check
requires a federal-registry integration (out of scope for this file —
see the NPPES docstring in `core/npi.py` for the parallel caveat).
"""
from __future__ import annotations

# DEA-assigned registrant-type codes as published by the Diversion
# Control Division. Any letter NOT in this set is a structural
# red-flag.
VALID_REGISTRANT_CODES = frozenset("ABCDEFGHJKLMPRSTUX")


def compute_dea_check_digit(body6: str) -> int:
    """Return the expected check digit (0-9) for a 6-digit DEA body.

    Raises `ValueError` if `body6` is not exactly 6 numeric characters.
    """
    if len(body6) != 6 or not body6.isdigit():
        raise ValueError("DEA numeric body must be exactly 6 digits.")
    odd = int(body6[0]) + int(body6[2]) + int(body6[4])
    even = (int(body6[1]) + int(body6[3]) + int(body6[5])) * 2
    return (odd + even) % 10


def is_valid_dea(value: str | None) -> bool:
    """Return True iff `value` is a structurally-valid DEA number.

    Leading/trailing whitespace is trimmed and letters are upper-cased
    before validation — this mirrors how the UI normalises the input.
    """
    if value is None:
        return False
    v = value.strip().upper()
    if len(v) != 9:
        return False
    if v[0] not in VALID_REGISTRANT_CODES:
        return False
    if not v[1].isalpha():
        return False
    if not v[2:].isdigit():
        return False
    body, check = v[2:8], int(v[8])
    return compute_dea_check_digit(body) == check


class DeaValidationError(ValueError):
    """Raised by `validate_dea_or_raise` with a specific, user-facing
    message so the UI can surface targeted help text."""


def validate_dea_or_raise(value: str | None) -> str:
    """Trim, upper-case, and validate a DEA number.

    Returns the normalised 9-character string. Raises `DeaValidationError`
    with a specific message per failure mode.
    """
    if value is None:
        raise DeaValidationError("DEA number is required.")
    v = value.strip().upper()
    if v == "":
        raise DeaValidationError("DEA number is required.")
    if len(v) != 9:
        raise DeaValidationError("DEA number must be exactly 9 characters.")
    if not v[0].isalpha() or not v[1].isalpha():
        raise DeaValidationError(
            "DEA number must begin with two letters followed by 7 digits.",
        )
    if v[0] not in VALID_REGISTRANT_CODES:
        raise DeaValidationError(
            f"'{v[0]}' is not a recognised DEA registrant-type code.",
        )
    if not v[2:].isdigit():
        raise DeaValidationError(
            "DEA number positions 3-9 must all be digits.",
        )
    if compute_dea_check_digit(v[2:8]) != int(v[8]):
        raise DeaValidationError(
            "DEA number failed checksum validation. "
            "Double-check the number you entered.",
        )
    return v


def matches_last_name_initial(dea: str, last_name: str | None) -> bool:
    """Soft heuristic — returns True if the DEA's 2nd character matches
    the first letter of `last_name`.

    Never call this as a *required* check; DEA explicitly allows
    exceptions. Use it to drive an optional "looks unusual" warning."""
    if not dea or not last_name:
        return False
    cleaned = dea.strip().upper()
    first = last_name.strip()[:1].upper() if last_name else ""
    return len(cleaned) >= 2 and first != "" and cleaned[1] == first
