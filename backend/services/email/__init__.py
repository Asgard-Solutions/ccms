"""Per-tenant Resend email credentials — encrypted at rest. Mirrors the
SMS (Twilio) module shape so the admin Settings UI feels familiar.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from core.crypto import decrypt_text, encrypt_text
from core.tenancy import tenant_db
from datetime import datetime, timezone


COLLECTION = "email_credentials"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EmailCredentialsCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: str = Field(min_length=10, max_length=128)
    from_email: EmailStr
    from_name: str | None = Field(default=None, max_length=120)
    reply_to: EmailStr | None = None
    enabled: bool = False


class EmailCredentialsPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    tenant_id: str
    configured: bool
    enabled: bool = False
    api_key_last4: str | None = None
    from_email: str | None = None
    from_name: str | None = None
    reply_to: str | None = None
    last_tested_at: str | None = None
    last_test_outcome: str | None = None
    updated_at: str | None = None
    updated_by: str | None = None


def _mask(value: str | None) -> str | None:
    if not value:
        return None
    return value[-4:] if len(value) >= 4 else "****"


async def get_credentials(tenant_id: str) -> Optional[dict]:
    return await tenant_db(tenant_id)[COLLECTION].find_one(
        {"tenant_id": tenant_id}, {"_id": 0},
    )


async def get_decrypted_credentials(tenant_id: str) -> Optional[dict]:
    doc = await get_credentials(tenant_id)
    if not doc:
        return None
    return {
        "api_key": decrypt_text(doc.get("api_key_encrypted")),
        "from_email": doc.get("from_email"),
        "from_name": doc.get("from_name"),
        "reply_to": doc.get("reply_to"),
        "enabled": doc.get("enabled", False),
    }


async def upsert_credentials(
    tenant_id: str, payload: EmailCredentialsCreate, *, actor: dict,
) -> dict:
    doc = {
        "tenant_id": tenant_id,
        "api_key_encrypted": encrypt_text(payload.api_key),
        "api_key_last4": _mask(payload.api_key),
        "from_email": payload.from_email,
        "from_name": payload.from_name,
        "reply_to": payload.reply_to,
        "enabled": payload.enabled,
        "updated_at": _now_iso(),
        "updated_by": actor.get("email") or actor.get("id"),
    }
    await tenant_db(tenant_id)[COLLECTION].update_one(
        {"tenant_id": tenant_id}, {"$set": doc}, upsert=True,
    )
    return doc


async def delete_credentials(tenant_id: str) -> int:
    res = await tenant_db(tenant_id)[COLLECTION].delete_one(
        {"tenant_id": tenant_id},
    )
    return res.deleted_count


async def update_test_outcome(tenant_id: str, *, outcome: str) -> None:
    await tenant_db(tenant_id)[COLLECTION].update_one(
        {"tenant_id": tenant_id},
        {"$set": {"last_tested_at": _now_iso(),
                  "last_test_outcome": outcome}},
    )


def to_public(doc: dict | None, tenant_id: str) -> EmailCredentialsPublic:
    if not doc:
        return EmailCredentialsPublic(tenant_id=tenant_id, configured=False)
    return EmailCredentialsPublic(
        tenant_id=tenant_id,
        configured=True,
        enabled=doc.get("enabled", False),
        api_key_last4=doc.get("api_key_last4"),
        from_email=doc.get("from_email"),
        from_name=doc.get("from_name"),
        reply_to=doc.get("reply_to"),
        last_tested_at=doc.get("last_tested_at"),
        last_test_outcome=doc.get("last_test_outcome"),
        updated_at=doc.get("updated_at"),
        updated_by=doc.get("updated_by"),
    )
