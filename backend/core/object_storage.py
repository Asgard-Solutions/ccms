"""
Emergent object-storage wrapper for CCMS.

Files stored here are PHI (insurance cards, driver's licenses, referral
letters, consent-receipt PDFs, etc.) — every access MUST be mediated by a
backend endpoint with auth + audit. Never expose the storage URL or
storage_key to the frontend.

Path convention:
    ccms/{tenant_id}/{patient_id}/{uuid}.{ext}

Source of truth for listing / soft-delete lives in MongoDB
(`patient_documents` collection). The Emergent storage bucket itself
has no delete/rename API — mark `is_deleted=True` in Mongo instead.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Tuple

import requests

logger = logging.getLogger(__name__)

STORAGE_URL = "https://integrations.emergentagent.com/objstore/api/v1/storage"
APP_NAME = "ccms"

_storage_key: str | None = None
_init_lock = threading.Lock()


class StorageUnavailable(RuntimeError):
    """Raised when EMERGENT_LLM_KEY is missing or the init handshake fails."""


def _init_storage() -> str:
    """Initialise (or return) the session-scoped storage_key. Thread-safe."""
    global _storage_key
    if _storage_key:
        return _storage_key
    with _init_lock:
        if _storage_key:
            return _storage_key
        emergent_key = os.environ.get("EMERGENT_LLM_KEY")
        if not emergent_key:
            raise StorageUnavailable("EMERGENT_LLM_KEY is not configured")
        try:
            resp = requests.post(
                f"{STORAGE_URL}/init",
                json={"emergent_key": emergent_key},
                timeout=30,
            )
            resp.raise_for_status()
            _storage_key = resp.json()["storage_key"]
            logger.info("object-storage: initialised")
        except requests.RequestException as exc:
            raise StorageUnavailable(f"storage init failed: {exc}") from exc
        return _storage_key


def put_object(path: str, data: bytes, content_type: str) -> dict:
    """Upload bytes. Returns {path, size, etag} from the storage API."""
    key = _init_storage()
    resp = requests.put(
        f"{STORAGE_URL}/objects/{path}",
        headers={"X-Storage-Key": key, "Content-Type": content_type},
        data=data,
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()


def get_object(path: str) -> Tuple[bytes, str]:
    """Download bytes. Returns (content, content_type)."""
    key = _init_storage()
    resp = requests.get(
        f"{STORAGE_URL}/objects/{path}",
        headers={"X-Storage-Key": key},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.content, resp.headers.get("Content-Type", "application/octet-stream")


def storage_path_for(tenant_id: str, patient_id: str, uuid_str: str, ext: str) -> str:
    """Canonical path — `ccms/{tenant}/{patient}/{uuid}.{ext}`."""
    safe_ext = (ext or "bin").lstrip(".").lower()[:10] or "bin"
    return f"{APP_NAME}/{tenant_id or 'default'}/{patient_id}/{uuid_str}.{safe_ext}"
