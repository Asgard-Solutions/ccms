"""
Object storage abstraction — tenant-safe by default.

- Backend-agnostic `StorageBackend` interface (`LocalStorage` now, `S3Storage`
  later). The route layer never talks to a driver directly.
- `TenantStorage` wraps the backend and enforces:
    * tenant-prefixed paths: `<category>/<tenant_id>/<key>`
    * path-traversal guard — rejects `..`, absolute paths, null bytes
    * key sanitisation (no PHI in filenames; UUID-named by default)
    * short-lived signed download URLs (15-min default)
    * private-by-default — no public buckets
- Categories ensure we can put permanent tenant content, temporary exports,
  and staging uploads on different buckets/prefixes in prod.

When promoting to S3 (or GCS / Azure Blob), subclass `StorageBackend`
and set the factory via `STORAGE_BACKEND=s3` + its config. Zero route
changes needed.
"""
from __future__ import annotations

import enum
import hmac
import logging
import os
import re
import secrets as _secrets
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

import jwt

logger = logging.getLogger("ccms.storage")


class StorageCategory(enum.Enum):
    """Lifecycle classes of storage. In production each could map to a
    different bucket / prefix with different retention rules."""
    PERMANENT = "permanent"       # documents kept for the tenant's lifetime
    EXPORTS = "exports"           # CSV exports — 24h TTL by default
    UPLOAD_STAGING = "staging"    # inbound uploads being scanned/processed
    REPORTS = "reports"           # generated PDFs / xlsx


_UNSAFE = re.compile(r"[\x00-\x1f<>:\"|?*\\]|\.\.")


class UnsafeStoragePathError(ValueError):
    pass


def sanitise_key(key: str) -> str:
    """Reject any user-supplied path component that could traverse or inject."""
    if not key or "/" in key or "\\" in key or _UNSAFE.search(key):
        raise UnsafeStoragePathError(f"unsafe storage key {key!r}")
    return key


@runtime_checkable
class StorageBackend(Protocol):
    """Minimal driver contract. `path` is opaque to callers — they always
    access files through signed URLs or the `open_bytes()` helper."""

    def exists(self, path: str) -> bool: ...
    def write(self, path: str, data: bytes) -> int: ...
    def read(self, path: str) -> bytes: ...
    def delete(self, path: str) -> None: ...
    def size(self, path: str) -> int: ...
    def open_stream(self, path: str): ...


class LocalStorage:
    """POSIX-backed implementation used in preview / dev."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, path: str) -> Path:
        # Guard against absolute + traversal paths even after sanitisation.
        if path.startswith("/") or ".." in Path(path).parts:
            raise UnsafeStoragePathError(path)
        full = (self.root / path).resolve()
        # Enforce that the final path stays inside root.
        try:
            full.relative_to(self.root)
        except ValueError:
            raise UnsafeStoragePathError(path)
        return full

    def exists(self, path: str) -> bool:
        return self._resolve(path).exists()

    def write(self, path: str, data: bytes) -> int:
        full = self._resolve(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(data)
        return len(data)

    def read(self, path: str) -> bytes:
        return self._resolve(path).read_bytes()

    def delete(self, path: str) -> None:
        full = self._resolve(path)
        if full.exists():
            full.unlink()

    def size(self, path: str) -> int:
        return self._resolve(path).stat().st_size

    def open_stream(self, path: str):
        return self._resolve(path).open("rb")


# ---------------------------------------------------------------------------
# TenantStorage — the only thing callers should touch.
# ---------------------------------------------------------------------------

@dataclass
class StoredArtifact:
    """Opaque handle returned by TenantStorage.put() — never exposes raw path
    to route code; every download goes through `signed_url()`."""
    tenant_id: str
    category: StorageCategory
    key: str               # UUID-ish; NEVER user-controlled
    backend_path: str      # `<category>/<tenant_id>/<key>`
    size_bytes: int


class TenantStorage:
    def __init__(self, backend: StorageBackend | None = None):
        # Default driver — in prod this becomes an S3Storage(bucket, region).
        root = os.environ.get("STORAGE_ROOT", "/app/data/storage")
        self.backend = backend or LocalStorage(root)

    def _path_for(self, tenant_id: str, category: StorageCategory, key: str) -> str:
        if not tenant_id:
            raise UnsafeStoragePathError("tenant_id is required on every storage op")
        key = sanitise_key(key)
        return f"{category.value}/{tenant_id}/{key}"

    def put(
        self, tenant_id: str, category: StorageCategory, data: bytes,
        *, suffix: str = "",
    ) -> StoredArtifact:
        key = uuid.uuid4().hex
        if suffix:
            # Only allow simple suffixes like '.csv'
            if not re.fullmatch(r"\.[a-z0-9]{1,8}", suffix):
                raise UnsafeStoragePathError(suffix)
            key = key + suffix
        path = self._path_for(tenant_id, category, key)
        size = self.backend.write(path, data)
        return StoredArtifact(tenant_id, category, key, path, size)

    def get_bytes(self, art: StoredArtifact) -> bytes:
        return self.backend.read(art.backend_path)

    def delete(self, art: StoredArtifact) -> None:
        self.backend.delete(art.backend_path)

    def exists(self, art: StoredArtifact) -> bool:
        return self.backend.exists(art.backend_path)

    def open_stream(self, art: StoredArtifact):
        return self.backend.open_stream(art.backend_path)

    # -- signed URL primitives ------------------------------------------------

    def sign_download(
        self, art: StoredArtifact, *, user_id: str, ttl_seconds: int = 900,
    ) -> str:
        """Return a short-lived JWT bound to {tenant_id, path, user_id}.
        Caller (the route) is responsible for validating the bearer's tenant
        matches `art.tenant_id` BEFORE issuing the token."""
        if ttl_seconds <= 0 or ttl_seconds > 3600:
            raise ValueError("ttl_seconds must be in (0, 3600]")
        payload = {
            "sub": user_id,
            "tid": art.tenant_id,
            "p": art.backend_path,
            "exp": int(time.time()) + ttl_seconds,
            "typ": "storage_dl",
        }
        return jwt.encode(payload, os.environ["JWT_SECRET"], algorithm="HS256")

    def verify_download(self, token: str) -> dict:
        return jwt.decode(token, os.environ["JWT_SECRET"], algorithms=["HS256"])


# Singleton used by services.
_storage_singleton: TenantStorage | None = None


def storage() -> TenantStorage:
    global _storage_singleton
    if _storage_singleton is None:
        _storage_singleton = TenantStorage()
    return _storage_singleton
