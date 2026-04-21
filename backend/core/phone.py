"""
U.S. phone number utilities — standard (XXX) XXX-XXXX.

Canonical storage: **10 digits, no formatting**. We deliberately keep
this US-only. International numbers / extensions / SMS delivery formats
live elsewhere and are not touched by this module.

`format_us_phone` is *permissive* on read: it only re-formats when the
input normalises to exactly 10 digits. Everything else (legacy seeds
like `+1-555-0102`, blank values, non-US numbers) echoes back
unchanged so old data renders safely without corruption.
"""
from __future__ import annotations

import re

_DIGITS_RE = re.compile(r"\D+")


def _digits_only(value: str | None) -> str:
    if value is None:
        return ""
    return _DIGITS_RE.sub("", str(value))


def normalize_us_phone(value: str | None) -> str | None:
    """Return the canonical 10-digit string, or None if the input is
    empty / blank. Raises `ValueError` for non-empty inputs that don't
    normalise cleanly to 10 digits.

    Accepts: `6155551212`, `615-555-1212`, `(615) 555-1212`,
    `615.555.1212`, `+1 615 555 1212`, `1-615-555-1212` (leading US
    country code stripped).
    """
    if value is None:
        return None
    raw = str(value).strip()
    if raw == "":
        return None
    digits = _digits_only(raw)
    # Tolerate a leading US "+1"/"1" country code.
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        raise ValueError(
            "Phone number must be a 10-digit U.S. number.",
        )
    return digits


def is_valid_us_phone(value: str | None) -> bool:
    """True iff `value` is empty OR normalises to exactly 10 digits."""
    if value is None or str(value).strip() == "":
        return True
    try:
        normalize_us_phone(value)
    except ValueError:
        return False
    return True


def format_us_phone(value: str | None) -> str:
    """Format for display.

    * Exactly 10 US digits (after normalisation)  → `(XXX) XXX-XXXX`.
    * Everything else (None, empty, non-US, 7-digit, extensions,
      garbage) → the input echoed back **unchanged** as str(value).

    This permissiveness is intentional: we render legacy/seed values
    (`+1-555-0102`) without mangling them even though they don't match
    our new canonical shape.
    """
    if value is None:
        return ""
    raw = str(value)
    if raw.strip() == "":
        return ""
    try:
        digits = normalize_us_phone(raw)
    except ValueError:
        return raw
    if digits is None:
        return raw
    return f"({digits[0:3]}) {digits[3:6]}-{digits[6:10]}"


def search_normalize_phone(value: str | None) -> str:
    """Strip formatting for search — returns just the digits the user
    typed, without enforcing length. An empty string means "no phone
    search"; the caller is responsible for deciding the minimum length
    that counts as an intentional query."""
    return _digits_only(value)
