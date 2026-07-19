"""Per-patient Helcim Customer Vault — saved card-on-file storage.

We store the Helcim `cardToken` + `customerCode` encrypted at rest and
expose only the brand/last4/expiry for display. Never persist the PAN.
"""
from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from core.crypto import decrypt_text, encrypt_text
from core.tenancy import tenant_db
from services.billing.helcim import now_iso

logger = logging.getLogger("ccms.billing.helcim.vault")

COLLECTION = "patient_card_tokens"


class SavedCardCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patient_id: str
    helcim_card_token: str = Field(min_length=4, max_length=200)
    helcim_customer_code: str | None = None
    brand: str | None = Field(default=None, max_length=32)
    last4: str | None = Field(default=None, max_length=4)
    expiry: str | None = Field(default=None, max_length=7)  # "MM/YY" or "MM/YYYY"
    cardholder_name: str | None = None
    is_default: bool = False
    source: str = "helcim_pay"  # "helcim_pay" | "manual_entry"


class SavedCardPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    patient_id: str
    brand: str | None
    last4: str | None
    expiry: str | None
    cardholder_name: str | None
    is_default: bool
    source: str
    created_at: str
    last_used_at: str | None = None
    last_use_outcome: str | None = None
    helcim_customer_code: str | None = None  # safe to expose — not a secret


def to_public(doc: dict) -> SavedCardPublic:
    return SavedCardPublic(
        id=doc["id"], patient_id=doc["patient_id"],
        brand=doc.get("brand"), last4=doc.get("last4"),
        expiry=doc.get("expiry"),
        cardholder_name=doc.get("cardholder_name"),
        is_default=doc.get("is_default", False),
        source=doc.get("source", "helcim_pay"),
        created_at=doc.get("created_at", now_iso()),
        last_used_at=doc.get("last_used_at"),
        last_use_outcome=doc.get("last_use_outcome"),
        helcim_customer_code=doc.get("helcim_customer_code_display"),
    )


async def list_for_patient(tenant_id: str, patient_id: str) -> list[dict]:
    db = tenant_db(tenant_id)
    return await db[COLLECTION].find(
        {"tenant_id": tenant_id, "patient_id": patient_id, "deleted_at": None},
        {"_id": 0, "helcim_card_token_encrypted": 0,
         "helcim_customer_code_encrypted": 0},
    ).sort("created_at", -1).to_list(length=100)


async def save_card(tenant_id: str, payload: SavedCardCreate, *, actor: dict) -> dict:
    import uuid
    db = tenant_db(tenant_id)
    # If is_default → unset previous default for this patient.
    if payload.is_default:
        await db[COLLECTION].update_many(
            {"tenant_id": tenant_id, "patient_id": payload.patient_id,
             "is_default": True},
            {"$set": {"is_default": False}},
        )
    doc = {
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "patient_id": payload.patient_id,
        "helcim_card_token_encrypted": encrypt_text(payload.helcim_card_token),
        "helcim_customer_code_encrypted":
            encrypt_text(payload.helcim_customer_code) if payload.helcim_customer_code else None,
        "helcim_customer_code_display": payload.helcim_customer_code,
        "brand": payload.brand,
        "last4": payload.last4,
        "expiry": payload.expiry,
        "cardholder_name": payload.cardholder_name,
        "is_default": payload.is_default,
        "source": payload.source,
        "created_at": now_iso(),
        "created_by": actor.get("email") or actor.get("id"),
        "deleted_at": None,
        "last_used_at": None,
        "last_use_outcome": None,
    }
    await db[COLLECTION].insert_one(doc)
    doc.pop("_id", None)
    return doc


async def delete_card(tenant_id: str, token_id: str) -> int:
    db = tenant_db(tenant_id)
    res = await db[COLLECTION].update_one(
        {"id": token_id, "tenant_id": tenant_id, "deleted_at": None},
        {"$set": {"deleted_at": now_iso()}},
    )
    return res.modified_count


async def get_decrypted(tenant_id: str, token_id: str) -> Optional[dict]:
    """Return token + customer_code in plaintext for an immediate charge."""
    db = tenant_db(tenant_id)
    row = await db[COLLECTION].find_one(
        {"id": token_id, "tenant_id": tenant_id, "deleted_at": None},
        {"_id": 0},
    )
    if not row:
        return None
    return {
        "id": row["id"],
        "patient_id": row["patient_id"],
        "card_token": decrypt_text(row.get("helcim_card_token_encrypted")),
        "customer_code": (
            decrypt_text(row["helcim_customer_code_encrypted"])
            if row.get("helcim_customer_code_encrypted") else None
        ),
        "brand": row.get("brand"), "last4": row.get("last4"),
        "expiry": row.get("expiry"),
    }


async def record_use(tenant_id: str, token_id: str, *, outcome: str) -> None:
    db = tenant_db(tenant_id)
    await db[COLLECTION].update_one(
        {"id": token_id, "tenant_id": tenant_id},
        {"$set": {"last_used_at": now_iso(), "last_use_outcome": outcome}},
    )
