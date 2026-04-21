"""
NPI (National Provider Identifier) format + checksum validation.

Reference: CMS Publication / ISO Standard 7812-1 (Health Industry
Number card-issuer identifier prefix 80840).

An NPI is exactly 10 numeric characters. The 10th digit is a check
digit computed by running the Luhn algorithm over the implicit
``80840`` prefix plus the first 9 digits of the NPI. The prefix is not
stored on the NPI itself but CMS specifies it must be present for the
purpose of computing the check digit.

A convenient shortcut used by many validators: the ``80840`` prefix
contributes a fixed constant of 24 to the Luhn sum, regardless of the
body digits (derived from ``0 + 8 + 8 + 0 + 8`` per the Luhn doubling
rules applied to the 5 prefix characters at their fixed positions).
So validation reduces to ``Luhn(body) + 24`` and the check digit is
``(10 - total mod 10) mod 10``.

IMPORTANT: Passing validation means the NPI is *structurally* valid
only. It does **not** prove the NPI is registered with NPPES, active,
or belongs to the user supplying it. A real verification flow would
require an NPPES registry lookup; that is intentionally out of scope.
"""
from __future__ import annotations

NPI_PREFIX_LUHN_CONTRIBUTION = 24  # fixed contribution of "80840"


def _luhn_body_sum(body9: str) -> int:
    """Luhn doubling/summing pass over a 9-digit NPI body.

    The body's rightmost digit is at an ODD position in the full
    14-digit string (prefix 80840 + body[0..8]) so it gets doubled.
    """
    total = 0
    for i, ch in enumerate(body9):
        # i=0 → body[0] → position 9 from right of the 14-digit string → odd → doubled.
        # i=8 → body[8] → position 1 from right → odd → doubled.
        # Pattern: positions 1,3,5,7,9 are body[8],[6],[4],[2],[0] → even i → doubled.
        digit = ord(ch) - 48
        if i % 2 == 0:
            doubled = digit * 2
            total += doubled - 9 if doubled > 9 else doubled
        else:
            total += digit
    return total


def compute_check_digit(body9: str) -> int:
    """Return the NPI check digit for the given 9-digit body.

    Raises `ValueError` if `body9` is not exactly 9 numeric characters.
    """
    if len(body9) != 9 or not body9.isdigit():
        raise ValueError("NPI body must be exactly 9 digits.")
    total = _luhn_body_sum(body9) + NPI_PREFIX_LUHN_CONTRIBUTION
    return (10 - (total % 10)) % 10


def is_valid_npi(value: str | None) -> bool:
    """Return True iff `value` is a 10-digit NPI whose check digit
    matches the Luhn calculation described above.

    Leading/trailing whitespace is trimmed. Any other non-digit
    characters (dashes, embedded spaces, letters) cause rejection —
    **we do not silently normalise** because accepting dashes would
    hide user typos at the form boundary, and the stored value must be
    plain digits for claims downstream.
    """
    if value is None:
        return False
    v = value.strip()
    if len(v) != 10 or not v.isdigit():
        return False
    body, check = v[:9], int(v[9])
    return compute_check_digit(body) == check


class NpiValidationError(ValueError):
    """Raised by `validate_npi_or_raise` with a user-facing message."""


def validate_npi_or_raise(value: str | None) -> str:
    """Trim + validate an NPI.

    Returns the cleaned 10-digit string. Raises `NpiValidationError`
    with a **specific** message for each failure mode so the UI can
    surface targeted help text (length vs. checksum vs. junk chars).
    """
    if value is None:
        raise NpiValidationError("NPI is required.")
    v = value.strip()
    if v == "":
        raise NpiValidationError("NPI is required.")
    if not v.isdigit():
        raise NpiValidationError(
            "NPI must contain digits only (no dashes, spaces, or letters).",
        )
    if len(v) != 10:
        raise NpiValidationError("NPI must be exactly 10 digits.")
    if compute_check_digit(v[:9]) != int(v[9]):
        raise NpiValidationError(
            "NPI failed checksum validation. "
            "Double-check the number you entered.",
        )
    return v
