"""
Shared helpers for Patient Service sub-routers.

Extracted from router.py so the documents + consent-PDF sub-routers can
reuse crypto + audit helpers without a circular import into the parent
`router` module.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import HTTPException, status

from core.crypto import ENC_PREFIX, decrypt_text, encrypt_text
from core.repository import PatientRepository

logger = logging.getLogger(__name__)

_patient_repo = PatientRepository()

REASON_MIN_LENGTH = 8

# Legacy top-level free-text PHI fields (stored as encrypted strings).
PATIENT_FLAT_ENCRYPTED = ["date_of_birth", "address", "emergency_contact", "notes"]

# Grouped intake sections — encrypted at rest as JSON blobs.
PATIENT_SECTION_ENCRYPTED = [
    "demographics",
    "contact",
    "admin",
    "guarantor",
    "insurance",
    "clinical_intake",
    "case_details",
    "consents",
    "address_details",
    "emergency_contact_details",
]

# Master list of encrypted-at-rest patient fields.
PATIENT_ENCRYPTED = PATIENT_FLAT_ENCRYPTED + PATIENT_SECTION_ENCRYPTED


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def encrypt_patient_value(value):
    """Encrypt one patient field. Strings go through AES-GCM; dicts/lists are
    JSON-serialized first so we can store structured intake sections as
    encrypted blobs without leaking PHI to the database."""
    if value is None or value == "":
        return value
    if isinstance(value, (dict, list)):
        return encrypt_text(json.dumps(value, default=str))
    if isinstance(value, str):
        return encrypt_text(value)
    return value


def decrypt_patient_value(value):
    if not isinstance(value, str) or not value.startswith(ENC_PREFIX):
        return value
    plaintext = decrypt_text(value)
    if isinstance(plaintext, str) and plaintext[:1] in ("{", "["):
        try:
            return json.loads(plaintext)
        except (ValueError, TypeError):
            pass
    return plaintext


def encrypt_patient_doc(doc: dict) -> dict:
    out = dict(doc)
    for key in PATIENT_ENCRYPTED:
        if key in out and out[key] is not None:
            out[key] = encrypt_patient_value(out[key])
    return out


def decrypt_patient_doc(doc: dict) -> dict:
    out = dict(doc)
    for key in PATIENT_ENCRYPTED:
        if key in out and out[key] is not None:
            out[key] = decrypt_patient_value(out[key])
    return out


def enforce_reason(reason: str | None, *, required: bool) -> str | None:
    if not required:
        return reason or None
    if not reason or len(reason.strip()) < REASON_MIN_LENGTH:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"A clinical reason of at least {REASON_MIN_LENGTH} characters is required for this access.",
        )
    return reason.strip()
