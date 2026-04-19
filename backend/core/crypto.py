"""
Field-level encryption for PHI at rest (AES-256-GCM).

Any string free-text field that could contain PHI should be passed through
`encrypt_text` before INSERT/UPDATE and through `decrypt_text` on read.
Ciphertexts are tagged with the `ENC_PREFIX` so we can safely mix legacy
plaintext rows (e.g. from older seeds) during the rollout window — any value
without the prefix is returned as-is.

Key management:
  - All key material comes from `core.key_manager`. Never read
    `DATA_ENCRYPTION_KEY` directly from here or from any caller.
  - Today the key is loaded from the env var `DATA_ENCRYPTION_KEY` (phase 1).
  - Production should set `KMS_PROVIDER=aws_kms|azure_kv|vault` and wire a
    provider-specific fetch in `core/key_manager.py`. Call sites do not
    change.

Forward rotation:
  - Each ciphertext embeds the key version that encrypted it (`enc:vN:...`).
  - On read we ask the key manager for the correct version.
  - On write we use the currently-active version.
"""
import base64
import secrets
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from core import key_manager

ENC_PREFIX = "enc:"


def _format(version: str, nonce: bytes, ct: bytes) -> str:
    payload = base64.urlsafe_b64encode(nonce + ct).decode("ascii")
    return f"{ENC_PREFIX}{version}:{payload}"


def _parse(value: str) -> tuple[str, bytes, bytes]:
    # Accept both `enc:v1:<b64>` (current) and the historical `enc:v1:<b64>`
    # form — they happen to be identical.
    rest = value[len(ENC_PREFIX):]
    version, b64 = rest.split(":", 1)
    raw = base64.urlsafe_b64decode(b64)
    return version, raw[:12], raw[12:]


def encrypt_text(plaintext: str | None) -> str | None:
    if plaintext is None or plaintext == "":
        return plaintext
    if isinstance(plaintext, str) and plaintext.startswith(ENC_PREFIX):
        return plaintext  # already encrypted — idempotent
    version = key_manager.current_version()
    aes = AESGCM(key_manager.get_key(version))
    nonce = secrets.token_bytes(12)
    ct = aes.encrypt(nonce, plaintext.encode("utf-8"), None)
    return _format(version, nonce, ct)


def decrypt_text(value: str | None) -> str | None:
    if value is None or value == "":
        return value
    if not isinstance(value, str) or not value.startswith(ENC_PREFIX):
        return value  # legacy plaintext — return as-is
    version, nonce, ct = _parse(value)
    aes = AESGCM(key_manager.get_key(version))
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
