"""Helcim webhook signature verification — HMAC-SHA256 per tenant.

Helcim signs every webhook with a tenant-specific verifier token via:
    signature = HMAC_SHA256(verifier_token, "{webhook_id}.{webhook_timestamp}.{body}")

We compare in constant-time, reject stale events (> 5min skew), and
deduplicate via `webhook_id` so retries are idempotent.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("ccms.billing.helcim.webhook")

# Reject webhooks whose `webhook-timestamp` is more than this many seconds in
# the past or future. Helcim uses unix epoch seconds.
MAX_SKEW_SECONDS = 300


def _decode_verifier(token: str) -> bytes:
    """Helcim verifier tokens are base64-encoded.

    If the operator pasted a non-base64 string (rare — they should copy the
    exact value from the Helcim dashboard) we fall back to using the raw
    bytes so signature verification still works deterministically.
    """
    try:
        return base64.b64decode(token, validate=True)
    except Exception:
        return token.encode("utf-8")


def compute_signature(verifier_token: str, webhook_id: str,
                      webhook_timestamp: str, body: bytes) -> str:
    """Compute the expected base64 HMAC-SHA256 signature."""
    signed = f"{webhook_id}.{webhook_timestamp}".encode("utf-8") + b"." + body
    digest = hmac.new(_decode_verifier(verifier_token), signed, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def verify_signature(
    *, verifier_token: str,
    webhook_id: str | None,
    webhook_timestamp: str | None,
    webhook_signature: str | None,
    body: bytes,
) -> tuple[bool, str | None]:
    """Returns (ok, error_reason)."""
    if not all([webhook_id, webhook_timestamp, webhook_signature]):
        return False, "missing webhook headers"

    # Skew check.
    try:
        ts = int(webhook_timestamp)
        now = int(datetime.now(timezone.utc).timestamp())
        if abs(now - ts) > MAX_SKEW_SECONDS:
            return False, f"timestamp skew exceeds {MAX_SKEW_SECONDS}s"
    except (TypeError, ValueError):
        return False, "invalid webhook-timestamp"

    expected = compute_signature(verifier_token, webhook_id, webhook_timestamp, body)
    # Helcim may send multiple comma-separated signatures; accept any match.
    candidates = [s.strip() for s in (webhook_signature or "").split(",") if s.strip()]
    for cand in candidates:
        if hmac.compare_digest(cand, expected):
            return True, None
    return False, "signature mismatch"


def is_duplicate_window(received_at: str, *, ttl_minutes: int = 60) -> datetime:
    """Cutoff for the dedupe collection's TTL — used by the router."""
    return datetime.fromisoformat(received_at) + timedelta(minutes=ttl_minutes)
