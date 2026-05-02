"""Helcim payment processing integration — per-tenant credentialed.

Each tenant stores their own Helcim API token + Account ID + webhook
verifier token in `helcim_credentials` (a tenant-scoped collection).
Tokens are encrypted at rest using `core.crypto.encrypt_text`.

Integration surface:
  * HelcimPay.js (hosted iframe checkout) — minimises PCI scope.
  * Customer Vault — saved card-on-file via `card_token`.
  * Refund API — `POST /payments/refund` with original transactionId.
  * Webhooks — HMAC-SHA256 signature verified per tenant.

All API calls route through `HelcimClient` which is constructed from a
`HelcimCredentials` row at request-time. We never cache the plaintext
API token across requests — decrypt on use, GC after.
"""
from __future__ import annotations

from datetime import datetime, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


HELCIM_API_BASE = "https://api.helcim.com/v2"
HELCIM_PAY_INIT_URL = f"{HELCIM_API_BASE}/helcim-pay/initialize"
HELCIM_PAY_SCRIPT_URL = "https://secure.helcim.app/helcim-pay/services/start.js"
