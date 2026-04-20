"""
Iteration 17 — Infrastructure backbone: DB routing, storage, secrets, classification.
"""
from __future__ import annotations

import os
import uuid

import pytest

from dotenv import load_dotenv
load_dotenv("/app/backend/.env")


# ---------------------------------------------------------------------------
# DB routing — classification guardrails
# ---------------------------------------------------------------------------

def test_primary_only_collections_refuse_replica_reads():
    from core.db_routing import ReadPurpose, PRIMARY_ONLY_COLLECTIONS, safe_read

    # Every sensitive collection refuses REPLICA_OK.
    for coll in ("users", "audit_logs", "jobs", "exports", "tenants",
                 "user_roles", "permission_scopes"):
        assert coll in PRIMARY_ONLY_COLLECTIONS
        with pytest.raises(ValueError):
            safe_read(coll, ReadPurpose.REPLICA_OK)
        with pytest.raises(ValueError):
            safe_read(coll, ReadPurpose.REPLICA_PREFERRED)

    # WRITES_ONLY and READ_AFTER_WRITE are always allowed.
    for purpose in (ReadPurpose.WRITES_ONLY, ReadPurpose.READ_AFTER_WRITE):
        safe_read("users", purpose)  # must not raise


def test_non_sensitive_collection_can_use_replica():
    from core.db_routing import ReadPurpose, safe_read
    # Patients are not in PRIMARY_ONLY — they may go to replicas.
    safe_read("patients", ReadPurpose.REPLICA_OK)
    safe_read("appointments", ReadPurpose.REPLICA_PREFERRED)


def test_replica_disable_falls_back_to_primary():
    import time
    from core.db_routing import (
        ReadPurpose, route_for, force_disable_replica,
        get_read_client, get_write_client,
    )
    # Even if a replica is configured, a forced disable routes reads to primary.
    force_disable_replica(seconds=30)
    client = route_for(ReadPurpose.REPLICA_OK)
    assert client is get_write_client()


def test_replica_health_reports_shape():
    from core.db_routing import replica_health
    h = replica_health()
    assert "has_replica" in h
    assert "enabled" in h
    assert "alive" in h
    assert "lag_seconds" in h
    assert "max_lag_seconds" in h


# ---------------------------------------------------------------------------
# Storage — tenant isolation + path-traversal guard
# ---------------------------------------------------------------------------

def test_storage_rejects_path_traversal(tmp_path):
    from core.storage import LocalStorage, UnsafeStoragePathError
    backend = LocalStorage(tmp_path)
    with pytest.raises(UnsafeStoragePathError):
        backend.write("../etc/passwd", b"x")
    with pytest.raises(UnsafeStoragePathError):
        backend.write("/etc/passwd", b"x")
    # Legit path works.
    backend.write("ok/file.txt", b"hello")
    assert backend.read("ok/file.txt") == b"hello"


def test_tenant_storage_scopes_by_tenant(tmp_path, monkeypatch):
    from core.storage import StorageCategory, TenantStorage, LocalStorage

    ts = TenantStorage(backend=LocalStorage(tmp_path))
    a = ts.put("tenant-A", StorageCategory.EXPORTS, b"hello-A", suffix=".csv")
    b = ts.put("tenant-B", StorageCategory.EXPORTS, b"hello-B", suffix=".csv")
    assert a.backend_path.startswith("exports/tenant-A/")
    assert b.backend_path.startswith("exports/tenant-B/")
    assert a.backend_path != b.backend_path
    # Each tenant's artifact still retrievable.
    assert ts.get_bytes(a) == b"hello-A"
    assert ts.get_bytes(b) == b"hello-B"


def test_tenant_storage_requires_tenant_id():
    from core.storage import TenantStorage, StorageCategory, UnsafeStoragePathError
    ts = TenantStorage()
    with pytest.raises(UnsafeStoragePathError):
        ts.put("", StorageCategory.EXPORTS, b"x")


def test_tenant_storage_rejects_bad_suffix(tmp_path):
    from core.storage import TenantStorage, StorageCategory, UnsafeStoragePathError, LocalStorage
    ts = TenantStorage(backend=LocalStorage(tmp_path))
    with pytest.raises(UnsafeStoragePathError):
        ts.put("t", StorageCategory.PERMANENT, b"x", suffix=".exe/../../evil")


def test_tenant_storage_signed_url_ttl_bounds(tmp_path):
    from core.storage import TenantStorage, StorageCategory, LocalStorage
    ts = TenantStorage(backend=LocalStorage(tmp_path))
    art = ts.put("t-1", StorageCategory.EXPORTS, b"x", suffix=".csv")
    with pytest.raises(ValueError):
        ts.sign_download(art, user_id="u", ttl_seconds=0)
    with pytest.raises(ValueError):
        ts.sign_download(art, user_id="u", ttl_seconds=7200)
    tok = ts.sign_download(art, user_id="u", ttl_seconds=600)
    payload = ts.verify_download(tok)
    assert payload["tid"] == "t-1"
    assert payload["p"] == art.backend_path


def test_tenant_storage_signed_url_cannot_be_forged_across_tenants(tmp_path):
    """Two TenantStorage instances using the same JWT secret still produce
    tokens that carry the correct tenant_id. Route-layer must re-check
    that the caller's tenant matches."""
    from core.storage import TenantStorage, StorageCategory, LocalStorage
    ts = TenantStorage(backend=LocalStorage(tmp_path))
    art = ts.put("tenant-A", StorageCategory.EXPORTS, b"x", suffix=".csv")
    tok = ts.sign_download(art, user_id="u", ttl_seconds=300)
    payload = ts.verify_download(tok)
    assert payload["tid"] == "tenant-A"  # caller must compare this to their own tenant


# ---------------------------------------------------------------------------
# Secrets — validation + redaction
# ---------------------------------------------------------------------------

def test_secrets_required_are_present():
    from core import secrets
    missing = secrets.validate_startup()
    assert missing == [], f"Missing: {missing}"


def test_secrets_require_raises_for_missing():
    from core import secrets
    with pytest.raises(secrets.MissingSecretError):
        secrets.require("CCMS_NONEXISTENT_" + uuid.uuid4().hex[:8])


def test_secrets_redaction_masks_common_formats():
    from core import secrets
    samples = [
        "connecting to mongodb+srv://user:pass@cluster.mongodb.net/db",
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc-DEF_123",
        "AWS key AKIAIOSFODNN7EXAMPLE spotted",
        "stripe sk_live_abcdef1234567890 leaked",
        "password=\"hunter2\"",
    ]
    for s in samples:
        out = secrets.redact(s)
        assert "REDACTED" in out, s


def test_secrets_redaction_masks_configured_values():
    from core import secrets
    jwt_secret = secrets.require("JWT_SECRET")
    # redact should mask the real secret if it happens to appear in a log line.
    assert jwt_secret not in secrets.redact(f"dump: {jwt_secret}")


# ---------------------------------------------------------------------------
# Cache categories
# ---------------------------------------------------------------------------

def test_cache_categories_have_bounded_ttls():
    from core.tenant_cache import CacheCategory, DEFAULT_TTL
    for c in CacheCategory:
        assert 0 < DEFAULT_TTL[c] <= 3600
