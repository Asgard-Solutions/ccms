"""Twilio webhook signature verification.

Twilio signs incoming webhook requests with HMAC-SHA1 of the URL + all
POST params (alphabetised + concatenated) using the tenant's auth
token. We verify this against the `X-Twilio-Signature` header.

See https://www.twilio.com/docs/usage/security#validating-requests .
"""
from __future__ import annotations

import base64
import hashlib
import hmac


def compute_signature(*, url: str, params: dict, auth_token: str) -> str:
    data = url
    for key in sorted(params.keys()):
        data += key + str(params[key] or "")
    digest = hmac.new(
        auth_token.encode("utf-8"), data.encode("utf-8"), hashlib.sha1,
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def verify_signature(
    *, url: str, params: dict, signature_header: str | None,
    auth_token: str,
) -> bool:
    if not signature_header or not auth_token:
        return False
    expected = compute_signature(
        url=url, params=params, auth_token=auth_token,
    )
    return hmac.compare_digest(expected, signature_header)
