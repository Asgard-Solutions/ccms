"""
Field-level encryption for PHI at rest (AES-256-GCM).

Any string free-text field that could contain PHI should be passed through
`encrypt_text` before INSERT/UPDATE and through `decrypt_text` on read.
Ciphertexts are tagged with the `ENC_PREFIX` so we can safely mix legacy
plaintext rows (e.g. from older seeds) during the rollout window — any value
without the prefix is returned as-is.

Key management:
  - Phase 1: symmetric key loaded from DATA_ENCRYPTION_KEY (base64 32 bytes)
  - Production: swap `_load_key()` for a KMS fetch (AWS KMS, Azure Key Vault,
    or HashiCorp Vault) — the public API does not change.
"""
import base64
import os
import secrets
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ENC_PREFIX = "enc:v1:"


def _load_key() -> bytes:
    raw = os.environ["DATA_ENCRYPTION_KEY"]
    key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    if len(key) != 32:
        raise RuntimeError("DATA_ENCRYPTION_KEY must decode to 32 bytes (256 bits)")
    return key


def encrypt_text(plaintext: str | None) -> str | None:
    if plaintext is None or plaintext == "":
        return plaintext
    if isinstance(plaintext, str) and plaintext.startswith(ENC_PREFIX):
        return plaintext  # already encrypted — idempotent
    aes = AESGCM(_load_key())
    nonce = secrets.token_bytes(12)
    ct = aes.encrypt(nonce, plaintext.encode("utf-8"), None)
    payload = base64.urlsafe_b64encode(nonce + ct).decode("ascii")
    return f"{ENC_PREFIX}{payload}"


def decrypt_text(value: str | None) -> str | None:
    if value is None or value == "":
        return value
    if not isinstance(value, str) or not value.startswith(ENC_PREFIX):
        return value  # legacy plaintext — return as-is
    aes = AESGCM(_load_key())
    raw = base64.urlsafe_b64decode(value[len(ENC_PREFIX):])
    nonce, ct = raw[:12], raw[12:]
    return aes.decrypt(nonce, ct, None).decode("utf-8")


def encrypt_fields(doc: dict, fields: list[str]) -> dict:
    out = dict(doc)
    for f in fields:
        if f in out and out[f] is not None:
            out[f] = encrypt_text(out[f])
    return out


def decrypt_fields(doc: dict, fields: list[str]) -> dict:
    out = dict(doc)
    for f in fields:
        if f in out and out[f] is not None:
            out[f] = decrypt_text(out[f])
    return out
