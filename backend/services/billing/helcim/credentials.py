"""Helcim credential storage — encrypted per-tenant API token + Account ID.

The plaintext token is **never persisted**. We mask it for display
(`hc_****1234`) and decrypt only when a request needs to make a Helcim API
call. Decryption happens inside `get_credentials()` and the plaintext
strings are dropped as soon as the HTTP call completes.
"""
from __future__ import annotations

import logging
import secrets
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from core.crypto import decrypt_text, encrypt_text
from core.tenancy import tenant_db

logger = logging.getLogger("ccms.billing.helcim.credentials")

COLLECTION = "helcim_credentials"


class HelcimCredentialsCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_token: str = Field(min_length=10, max_length=500)
    account_id: str = Field(min_length=1, max_length=64)
    webhook_verifier_token: str | None = Field(default=None, max_length=500)
    test_mode: bool = False


class HelcimCredentialsPublic(BaseModel):
    """Safe-to-display view — never includes plaintext tokens.

    `api_token_last4` and `webhook_verifier_token_last4` show enough for
    the operator to confirm they entered the right value without
    exposing the secret. Status badges (`configured`, `tested_at`, etc.)
    drive the settings UI.
    """
    model_config = ConfigDict(extra="ignore")
    tenant_id: str
    configured: bool
    test_mode: bool = False
    account_id: str | None = None
    api_token_last4: str | None = None
    webhook_verifier_token_last4: str | None = None
    last_tested_at: str | None = None
    last_test_outcome: str | None = None
    updated_at: str | None = None
    updated_by: str | None = None


def _mask(value: str | None) -> str | None:
    if not value:
        return None
    return value[-4:] if len(value) >= 4 else "****"


async def get_credentials(tenant_id: str) -> Optional[dict]:
    """Return the raw stored doc (still-encrypted)."""
    db = tenant_db(tenant_id)
    return await db[COLLECTION].find_one(
        {"tenant_id": tenant_id}, {"_id": 0},
    )


async def get_decrypted_credentials(tenant_id: str) -> Optional[dict]:
    """Return a transient dict with plaintext values for an API call.

    Caller should not persist or log the result — use immediately
    inside an HTTP request and discard.
    """
    doc = await get_credentials(tenant_id)
    if not doc:
        return None
    return {
        "api_token": decrypt_text(doc.get("api_token_encrypted")),
        "account_id": decrypt_text(doc.get("account_id_encrypted")),
        "webhook_verifier_token": decrypt_text(doc.get("webhook_verifier_token_encrypted")),
        "test_mode": doc.get("test_mode", False),
    }


async def upsert_credentials(
    tenant_id: str, payload: HelcimCredentialsCreate, *, actor: dict,
) -> dict:
    db = tenant_db(tenant_id)
    from services.billing.helcim import now_iso
    doc = {
        "tenant_id": tenant_id,
        "api_token_encrypted": encrypt_text(payload.api_token),
        "account_id_encrypted": encrypt_text(payload.account_id),
        "webhook_verifier_token_encrypted":
            encrypt_text(payload.webhook_verifier_token) if payload.webhook_verifier_token else None,
        "api_token_last4": _mask(payload.api_token),
        "webhook_verifier_token_last4": _mask(payload.webhook_verifier_token),
        "account_id_display": payload.account_id,
        "test_mode": payload.test_mode,
        "updated_at": now_iso(),
        "updated_by": actor.get("email") or actor.get("id"),
    }
    await db[COLLECTION].update_one(
        {"tenant_id": tenant_id}, {"$set": doc}, upsert=True,
    )
    return doc


async def delete_credentials(tenant_id: str) -> int:
    db = tenant_db(tenant_id)
    res = await db[COLLECTION].delete_one({"tenant_id": tenant_id})
    return res.deleted_count


async def update_test_outcome(
    tenant_id: str, *, outcome: str,
) -> None:
    """Persist the result of a `Test connection` button click."""
    from services.billing.helcim import now_iso
    db = tenant_db(tenant_id)
    await db[COLLECTION].update_one(
        {"tenant_id": tenant_id},
        {"$set": {"last_tested_at": now_iso(), "last_test_outcome": outcome}},
    )


def to_public(doc: dict | None, tenant_id: str) -> HelcimCredentialsPublic:
    if not doc:
        return HelcimCredentialsPublic(tenant_id=tenant_id, configured=False)
    return HelcimCredentialsPublic(
        tenant_id=tenant_id,
        configured=True,
        test_mode=doc.get("test_mode", False),
        account_id=doc.get("account_id_display"),
        api_token_last4=doc.get("api_token_last4"),
        webhook_verifier_token_last4=doc.get("webhook_verifier_token_last4"),
        last_tested_at=doc.get("last_tested_at"),
        last_test_outcome=doc.get("last_test_outcome"),
        updated_at=doc.get("updated_at"),
        updated_by=doc.get("updated_by"),
    )


def random_idempotency_key() -> str:
    """RFC4122-style nonce for `idempotency-key` header on payment ops."""
    return secrets.token_hex(16)
