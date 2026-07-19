"""Per-tenant Twilio credential storage — encrypted at rest (AES-256 via
`core.crypto.encrypt_text`). Mirrors the Helcim credentials module shape
so operators see a familiar settings UI.

Only ever persisted encrypted; plaintext is decrypted on-demand inside a
single request and discarded once the Twilio REST call completes.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from core.crypto import decrypt_text, encrypt_text
from core.tenancy import tenant_db
from services.sms import now_iso

COLLECTION = "sms_credentials"


class SmsCredentialsCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_sid: str = Field(min_length=10, max_length=64)
    auth_token: str = Field(min_length=10, max_length=128)
    # One of these must be populated. `messaging_service_sid` takes
    # precedence when both are set (enables alphanumeric sender, US
    # A2P 10DLC routing, and automatic geo-failover).
    messaging_service_sid: str | None = Field(default=None, max_length=64)
    from_number: str | None = Field(default=None, max_length=32)
    # Feature flag — when False, /sms/send returns a simulated 'logged'
    # response without calling Twilio. Flipped to True only after the
    # operator has clicked `Test connection` and we've confirmed the
    # credentials round-trip.
    enabled: bool = False


class SmsCredentialsPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    tenant_id: str
    configured: bool
    enabled: bool = False
    account_sid_last4: str | None = None
    messaging_service_sid: str | None = None
    from_number: str | None = None
    last_tested_at: str | None = None
    last_test_outcome: str | None = None
    updated_at: str | None = None
    updated_by: str | None = None


def _mask(value: str | None) -> str | None:
    if not value:
        return None
    return value[-4:] if len(value) >= 4 else "****"


async def get_credentials(tenant_id: str) -> Optional[dict]:
    db = tenant_db(tenant_id)
    return await db[COLLECTION].find_one(
        {"tenant_id": tenant_id}, {"_id": 0},
    )


async def get_decrypted_credentials(tenant_id: str) -> Optional[dict]:
    doc = await get_credentials(tenant_id)
    if not doc:
        return None
    return {
        "account_sid": decrypt_text(doc.get("account_sid_encrypted")),
        "auth_token": decrypt_text(doc.get("auth_token_encrypted")),
        "messaging_service_sid":
            decrypt_text(doc.get("messaging_service_sid_encrypted"))
            if doc.get("messaging_service_sid_encrypted") else None,
        "from_number": doc.get("from_number"),
        "enabled": doc.get("enabled", False),
    }


async def upsert_credentials(
    tenant_id: str, payload: SmsCredentialsCreate, *, actor: dict,
) -> dict:
    if not payload.messaging_service_sid and not payload.from_number:
        raise ValueError(
            "Either messaging_service_sid or from_number must be set.",
        )
    db = tenant_db(tenant_id)
    doc = {
        "tenant_id": tenant_id,
        "account_sid_encrypted": encrypt_text(payload.account_sid),
        "auth_token_encrypted": encrypt_text(payload.auth_token),
        "messaging_service_sid_encrypted":
            encrypt_text(payload.messaging_service_sid)
            if payload.messaging_service_sid else None,
        "account_sid_last4": _mask(payload.account_sid),
        "messaging_service_sid": payload.messaging_service_sid,
        "from_number": payload.from_number,
        "enabled": payload.enabled,
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


async def update_test_outcome(tenant_id: str, *, outcome: str) -> None:
    db = tenant_db(tenant_id)
    await db[COLLECTION].update_one(
        {"tenant_id": tenant_id},
        {"$set": {"last_tested_at": now_iso(), "last_test_outcome": outcome}},
    )


def to_public(doc: dict | None, tenant_id: str) -> SmsCredentialsPublic:
    if not doc:
        return SmsCredentialsPublic(tenant_id=tenant_id, configured=False)
    return SmsCredentialsPublic(
        tenant_id=tenant_id,
        configured=True,
        enabled=doc.get("enabled", False),
        account_sid_last4=doc.get("account_sid_last4"),
        messaging_service_sid=doc.get("messaging_service_sid"),
        from_number=doc.get("from_number"),
        last_tested_at=doc.get("last_tested_at"),
        last_test_outcome=doc.get("last_test_outcome"),
        updated_at=doc.get("updated_at"),
        updated_by=doc.get("updated_by"),
    )
