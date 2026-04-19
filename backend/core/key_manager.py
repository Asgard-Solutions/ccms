"""
Encryption Key Manager.

Centralises all access to symmetric encryption key material so the rest of
the app never touches `os.environ["DATA_ENCRYPTION_KEY"]` directly. This is
the seam that lets production swap in AWS KMS / Azure Key Vault / HashiCorp
Vault without changing any call sites.

Public API:
  - `is_enabled()`           -> bool — is field-level encryption active?
  - `provider()`             -> str  — one of "env"|"aws_kms"|"azure_kv"|"vault"
  - `current_version()`      -> str  — active key version label (e.g. "v1")
  - `get_key(version=None)`  -> bytes — 32-byte AES-256 key, defaults to active
  - `describe()`             -> dict — safe, **never contains secret material**

Provider selection (today):
  - If `KMS_PROVIDER` env is set to anything other than "env", load the
    provider-specific client. Only "env" is implemented in-app; the others
    raise a clear `NotImplementedError` at use time so the code path is ready
    but deploys are forced to wire the integration (keeps secrets out of the
    app repo).
  - Default: `provider == "env"`, key is loaded once from
    `DATA_ENCRYPTION_KEY` (base64-encoded 32 bytes) and cached.

Key rotation (forward compatible):
  - We tag ciphertexts with a version string so a rotated installation can
    decrypt both old and new material. Today only `v1` is emitted.
  - To rotate: add a new version under `EXTRA_KEYS` env (json mapping of
    version -> base64 key) and bump `DATA_KEY_VERSION` to match. Old
    ciphertexts still decrypt.

What this class deliberately does NOT do:
  - Perform crypto operations itself — `core/crypto.py` owns the AES-GCM
    calls.
  - Persist keys or ciphertexts anywhere.
  - Log key material. `describe()` emits only metadata and never the key.
"""
from __future__ import annotations

import base64
import json
import os
from functools import lru_cache

_ENV_PROVIDER = "env"
_SUPPORTED_PROVIDERS = {"env", "aws_kms", "azure_kv", "vault"}


def _b64_to_key(raw: str) -> bytes:
    key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    if len(key) != 32:
        raise RuntimeError(
            "Encryption key must decode to exactly 32 bytes (AES-256)."
        )
    return key


def provider() -> str:
    return os.environ.get("KMS_PROVIDER", _ENV_PROVIDER).strip().lower() or _ENV_PROVIDER


def current_version() -> str:
    return os.environ.get("DATA_KEY_VERSION", "v1").strip() or "v1"


def is_enabled() -> bool:
    """Field-level encryption is enabled if we can load at least one key."""
    try:
        _load_active_key()
        return True
    except Exception:
        return False


@lru_cache(maxsize=1)
def _load_active_key() -> bytes:
    p = provider()
    if p != _ENV_PROVIDER:
        if p not in _SUPPORTED_PROVIDERS:
            raise RuntimeError(
                f"Unknown KMS_PROVIDER '{p}'. Supported: {sorted(_SUPPORTED_PROVIDERS)}."
            )
        raise NotImplementedError(
            f"KMS provider '{p}' is declared but not wired in this deployment. "
            "Implement provider-specific fetch before starting the service."
        )
    raw = os.environ.get("DATA_ENCRYPTION_KEY")
    if not raw:
        raise RuntimeError(
            "DATA_ENCRYPTION_KEY is not set — field-level encryption cannot start. "
            "Provide a base64-encoded 32-byte key, or configure KMS_PROVIDER."
        )
    return _b64_to_key(raw)


@lru_cache(maxsize=8)
def _load_extra_key(version: str) -> bytes:
    """Extra historical keys for rotated installations.

    Format: `EXTRA_DATA_KEYS='{"v0":"<base64>"}'` — JSON mapping version->b64 key.
    Keeps past ciphertexts decryptable after rotation.
    """
    raw = os.environ.get("EXTRA_DATA_KEYS")
    if not raw:
        raise RuntimeError(f"No key material available for version '{version}'.")
    try:
        mapping = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"EXTRA_DATA_KEYS is not valid JSON: {exc}")
    if version not in mapping:
        raise RuntimeError(f"No key material available for version '{version}'.")
    return _b64_to_key(mapping[version])


def get_key(version: str | None = None) -> bytes:
    if version is None or version == current_version():
        return _load_active_key()
    return _load_extra_key(version)


def describe() -> dict:
    """Operational metadata only. Never contains key material."""
    p = provider()
    try:
        _load_active_key()
        enabled = True
        error: str | None = None
    except Exception as exc:  # pragma: no cover — diagnostic path
        enabled = False
        error = str(exc)
    try:
        extra_versions = list(json.loads(os.environ.get("EXTRA_DATA_KEYS") or "{}").keys())
    except Exception:
        extra_versions = []
    return {
        "provider": p,
        "enabled": enabled,
        "active_version": current_version(),
        "extra_versions": extra_versions,
        "error": error,
    }


def reset_cache_for_tests() -> None:
    """Test helper — reset the cached keys. Not called in production."""
    _load_active_key.cache_clear()
    _load_extra_key.cache_clear()
