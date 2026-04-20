"""
Compliance readiness endpoint (admin-only).

This is an **internal readiness dashboard feed**, not a certification claim.
It aggregates real signals the application can observe about itself:

  - Environment hardening flags (TLS cookie config, CORS origin-locking,
    Redis reachability, encryption key present, JWT secret strength)
  - Audit activity summary (row counts, last PHI access, last break-glass)
  - MFA adoption across staff roles
  - Retention pipeline status (soft-deleted patients + scheduled purges)
  - Per-control status pulled from the static inventory below, annotated
    where the code can confirm the live state.

No PHI is exposed. Every field here is either a boolean flag, a count,
or a static label.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request

from core import config, key_manager
from core.db import get_db_read
from core.deps import require_role
from core.redis_client import ping as redis_ping
from core.security_headers import DEFAULT_HSTS_MAX_AGE

router = APIRouter(prefix="/compliance", tags=["compliance"])


# ----- static control catalog (sync'd with /app/memory/CONTROL_INVENTORY.md) -----
# Only the fields the UI needs; the source of truth stays in the markdown doc.

CONTROL_CATALOG: list[dict] = [
    # Access control
    {"id": "AC-1", "name": "Unique user identification (UUIDs)", "group": "Access control", "status": "implemented", "frameworks": ["SOC2-CC6.1", "ISO-A.5.16"]},
    {"id": "AC-2", "name": "Role-based access control", "group": "Access control", "status": "implemented", "frameworks": ["SOC2-CC6.1", "ISO-A.8.3"]},
    {"id": "AC-3", "name": "MFA (TOTP + backup codes)", "group": "Access control", "status": "implemented", "frameworks": ["SOC2-CC6.1", "ISO-A.8.5"]},
    {"id": "AC-4", "name": "Password policy (complexity + history + rotation)", "group": "Access control", "status": "implemented", "frameworks": ["SOC2-CC6.1", "ISO-A.5.17"]},
    {"id": "AC-5", "name": "Brute-force lockout", "group": "Access control", "status": "implemented", "frameworks": ["SOC2-CC6.1"]},
    {"id": "AC-6", "name": "Account disable / enable (preserves audit)", "group": "Access control", "status": "implemented", "frameworks": ["SOC2-CC6.3"]},
    {"id": "AC-7", "name": "Step-up reauth for destructive ops", "group": "Access control", "status": "implemented", "frameworks": ["SOC2-CC6.1"]},
    {"id": "AC-8", "name": "Session idle timeout + warning", "group": "Access control", "status": "implemented", "frameworks": ["SOC2-CC6.1", "ISO-A.8.5"]},
    {"id": "AC-9", "name": "Session cookie hardening (Secure + HttpOnly + SameSite)", "group": "Access control", "status": "implemented", "frameworks": ["SOC2-CC6.7"]},
    {"id": "AC-10", "name": "Privileged-access JIT elevation", "group": "Access control", "status": "partial", "frameworks": ["ISO-A.8.2"]},
    {"id": "AC-11", "name": "Periodic access review", "group": "Access control", "status": "missing", "frameworks": ["SOC2-CC6.2"]},

    # Auditability
    {"id": "AU-1", "name": "Structured audit log of every PHI access", "group": "Auditability", "status": "implemented", "frameworks": ["SOC2-CC7.1", "ISO-A.8.15"]},
    {"id": "AU-2", "name": "Meta-audit (audit-log viewer viewings)", "group": "Auditability", "status": "implemented", "frameworks": ["SOC2-CC7.1"]},
    {"id": "AU-3", "name": "Tamper resistance at DB layer", "group": "Auditability", "status": "partial", "frameworks": ["ISO-A.8.15"]},
    {"id": "AU-4", "name": "Audit retention (7 years) enforced", "group": "Auditability", "status": "partial", "frameworks": ["HIPAA", "SOC2"]},
    {"id": "AU-5", "name": "Break-glass reason capture + flagging", "group": "Auditability", "status": "implemented", "frameworks": ["HIPAA"]},
    {"id": "AU-6", "name": "Alerting pipeline on suspicious auth", "group": "Auditability", "status": "missing", "frameworks": ["ISO-A.8.16"]},

    # Encryption / key management
    {"id": "CR-1", "name": "PHI field-level encryption at rest (AES-256-GCM)", "group": "Encryption & keys", "status": "implemented", "frameworks": ["SOC2-C", "ISO-A.8.24"]},
    {"id": "CR-2", "name": "Password hashing (bcrypt)", "group": "Encryption & keys", "status": "implemented", "frameworks": ["SOC2-CC6.1"]},
    {"id": "CR-3", "name": "KMS-backed key management + rotation", "group": "Encryption & keys", "status": "missing", "frameworks": ["ISO-A.8.24"]},
    {"id": "CR-4", "name": "TLS 1.2+ everywhere (ingress)", "group": "Encryption & keys", "status": "out_of_app", "frameworks": ["SOC2-CC6.7"]},
    {"id": "CR-6", "name": "Formal crypto policy document", "group": "Encryption & keys", "status": "missing", "frameworks": ["ISO-A.8.24"]},

    # Data rights / privacy
    {"id": "DM-1", "name": "PHI masking by default", "group": "Data rights & privacy", "status": "implemented", "frameworks": ["SOC2-C", "ISO-A.8.11"]},
    {"id": "DM-2", "name": "Unmask requires reason + audit", "group": "Data rights & privacy", "status": "implemented", "frameworks": ["SOC2-P", "HIPAA"]},
    {"id": "DM-3", "name": "Right-to-access export", "group": "Data rights & privacy", "status": "implemented", "frameworks": ["CCPA-§1798.100", "SOC2-P"]},
    {"id": "DM-4", "name": "Right-to-correct (patient self-service)", "group": "Data rights & privacy", "status": "implemented", "frameworks": ["CCPA-§1798.106"]},
    {"id": "DM-5", "name": "Right-to-delete + retention worker", "group": "Data rights & privacy", "status": "partial", "frameworks": ["CCPA-§1798.105"]},
    {"id": "DM-6", "name": "Consent capture on registration", "group": "Data rights & privacy", "status": "missing", "frameworks": ["CCPA-§1798.100(b)", "SOC2-P"]},
    {"id": "DM-7", "name": "Privacy Notice surfaced in UI", "group": "Data rights & privacy", "status": "missing", "frameworks": ["CCPA"]},
    {"id": "DM-8", "name": "Purpose taxonomy (enum) in audit reasons", "group": "Data rights & privacy", "status": "missing", "frameworks": ["SOC2-P"]},

    # Secrets & configuration
    {"id": "SM-1", "name": "Secrets kept in .env (never committed)", "group": "Secrets & config", "status": "implemented", "frameworks": ["ISO-A.8.9"]},
    {"id": "SM-2", "name": "Config-as-code vault (Vault/SSM)", "group": "Secrets & config", "status": "out_of_app", "frameworks": ["ISO-A.8.9"]},
    {"id": "SM-3", "name": "JWT_SECRET rotation SOP", "group": "Secrets & config", "status": "missing", "frameworks": ["ISO-A.8.24"]},
    {"id": "SM-4", "name": "Separate envs (dev/stg/prod)", "group": "Secrets & config", "status": "partial", "frameworks": ["ISO-A.8.31"]},

    # Observability
    {"id": "OB-1", "name": "Health endpoint", "group": "Observability", "status": "implemented", "frameworks": ["SOC2-A"]},
    {"id": "OB-2", "name": "Prometheus metrics", "group": "Observability", "status": "implemented", "frameworks": ["SOC2-A", "ISO-A.8.15"]},
    {"id": "OB-3", "name": "Structured JSON logging", "group": "Observability", "status": "partial", "frameworks": ["SOC2-CC7.1"]},
    {"id": "OB-4", "name": "Centralised log aggregation", "group": "Observability", "status": "out_of_app", "frameworks": ["ISO-A.8.15"]},
    {"id": "OB-5", "name": "Alerting on SLO breach", "group": "Observability", "status": "missing", "frameworks": ["SOC2-A"]},

    # Vendor / dependency hygiene
    {"id": "VD-1", "name": "Dependency SCA in CI", "group": "Vendor & dependency", "status": "missing", "frameworks": ["ISO-A.8.8"]},
    {"id": "VD-2", "name": "SAST in CI", "group": "Vendor & dependency", "status": "missing", "frameworks": ["ISO-A.8.28"]},
    {"id": "VD-3", "name": "DAST / pentest pre-launch", "group": "Vendor & dependency", "status": "out_of_app", "frameworks": ["ISO-A.8.29"]},
    {"id": "VD-4", "name": "Container image scanning", "group": "Vendor & dependency", "status": "out_of_app", "frameworks": ["ISO-A.8.8"]},

    # Incident readiness
    {"id": "IR-1", "name": "Incident response runbook", "group": "Incident readiness", "status": "missing", "frameworks": ["SOC2-CC7.2", "ISO-A.5.24"]},
    {"id": "IR-2", "name": "Breach-notification timelines", "group": "Incident readiness", "status": "out_of_app", "frameworks": ["CCPA", "HIPAA"]},
    {"id": "IR-3", "name": "In-app incident reporting for staff", "group": "Incident readiness", "status": "missing", "frameworks": ["ISO-A.5.24"]},

    # Backup & evidence
    {"id": "BK-1", "name": "Encrypted backups + tested restore", "group": "Backup & evidence", "status": "out_of_app", "frameworks": ["ISO-A.8.13"]},
    {"id": "BK-2", "name": "Auditor CSV/JSON evidence export", "group": "Backup & evidence", "status": "partial", "frameworks": ["SOC2-CC7.1"]},

    # Vendor management
    {"id": "VR-1", "name": "Vendor BAAs / DPAs", "group": "Vendor & dependency", "status": "out_of_app", "frameworks": ["HIPAA", "ISO-A.5.23"]},
    {"id": "VR-2", "name": "Inventory of subprocessors", "group": "Vendor & dependency", "status": "missing", "frameworks": ["CCPA", "ISO-A.5.23"]},
]


def _env_flags() -> dict:
    """Snapshot of env-driven hardening flags. Booleans only — no secret values."""
    cfg = config.describe()
    return {
        "cors_origin_locked": cfg["cors_locked_to_frontend"],
        "frontend_url_configured": cfg["recommended"]["FRONTEND_URL"],
        "jwt_secret_strong": "JWT_SECRET" not in cfg["weak_secrets"]
        and cfg["required"]["JWT_SECRET"],
        "data_encryption_key_configured": cfg["required"]["DATA_ENCRYPTION_KEY"],
        "mfa_issuer_configured": cfg["recommended"]["MFA_ISSUER"],
        "redis_url_configured": cfg["recommended"]["REDIS_URL"],
        "mongo_read_url_distinct": bool(os.environ.get("MONGO_READ_URL"))
        and os.environ.get("MONGO_READ_URL") != os.environ.get("MONGO_URL"),
        "admin_password_configured": cfg["recommended"]["ADMIN_PASSWORD"],
    }


@router.get("/overview")
async def overview(_admin: dict = Depends(require_role("admin"))):
    """Aggregate readiness snapshot for the admin-only Compliance dashboard."""
    db = get_db_read()
    now = datetime.now(timezone.utc)
    since_24h = (now - timedelta(hours=24)).isoformat()
    since_30d = (now - timedelta(days=30)).isoformat()

    # ---- audit activity signals
    audit_total = await db.audit_logs.count_documents({})
    audit_24h = await db.audit_logs.count_documents({"created_at": {"$gte": since_24h}})
    phi_access_30d = await db.audit_logs.count_documents(
        {"phi_accessed": True, "created_at": {"$gte": since_30d}}
    )
    breakglass_30d = await db.audit_logs.count_documents(
        {"metadata.emergency_access": True, "created_at": {"$gte": since_30d}}
    )
    failed_logins_24h = await db.audit_logs.count_documents(
        {"action": {"$regex": "^auth"}, "outcome": "failure", "created_at": {"$gte": since_24h}}
    )
    last_phi = await db.audit_logs.find_one(
        {"phi_accessed": True}, {"_id": 0, "created_at": 1, "action": 1, "actor_role": 1},
        sort=[("created_at", -1)],
    )
    last_breakglass = await db.audit_logs.find_one(
        {"metadata.emergency_access": True}, {"_id": 0, "created_at": 1, "actor_role": 1},
        sort=[("created_at", -1)],
    )

    # ---- MFA adoption across privileged roles
    privileged_roles = ["admin", "doctor", "staff"]
    total_priv = await db.users.count_documents({"role": {"$in": privileged_roles}, "status": {"$ne": "disabled"}})
    mfa_priv = await db.users.count_documents(
        {"role": {"$in": privileged_roles}, "status": {"$ne": "disabled"}, "mfa_enabled": True}
    )
    mfa_adoption_ratio = round(mfa_priv / total_priv, 3) if total_priv else None

    # ---- retention pipeline
    soft_deleted = await db.patients.count_documents({"status": "deleted"})
    overdue_purge = await db.patients.count_documents(
        {"status": "deleted", "retention_until": {"$lt": now.isoformat()}}
    )

    # ---- user base
    users_active = await db.users.count_documents({"status": {"$ne": "disabled"}})
    users_disabled = await db.users.count_documents({"status": "disabled"})

    # ---- env + redis
    env = _env_flags()
    redis_alive = await redis_ping()

    # ---- aggregate control status counts
    totals = {"implemented": 0, "partial": 0, "missing": 0, "out_of_app": 0}
    for c in CONTROL_CATALOG:
        totals[c["status"]] = totals.get(c["status"], 0) + 1

    # Derived posture score — implemented + 0.5*partial out of in-app controls only.
    in_app = totals["implemented"] + totals["partial"] + totals["missing"]
    readiness_score = (
        round((totals["implemented"] + 0.5 * totals["partial"]) / in_app, 3)
        if in_app
        else None
    )

    def _iso(row):
        if not row:
            return None
        ts = row.get("created_at")
        if not ts:
            return None
        return ts.isoformat() if hasattr(ts, "isoformat") else str(ts)

    return {
        "generated_at": now.isoformat(),
        "disclaimer": (
            "Internal readiness dashboard only. This is not a certification "
            "claim. SOC 2, CCPA, and ISO 27001 posture require independent "
            "audit, policy, and operational evidence beyond this application."
        ),
        "readiness_score": readiness_score,
        "status_totals": totals,
        "environment": {
            **env,
            "redis_alive": redis_alive,
        },
        "audit_activity": {
            "total_rows": audit_total,
            "last_24h": audit_24h,
            "phi_access_30d": phi_access_30d,
            "breakglass_30d": breakglass_30d,
            "failed_logins_24h": failed_logins_24h,
            "last_phi_access_at": _iso(last_phi),
            "last_breakglass_at": _iso(last_breakglass),
        },
        "mfa": {
            "privileged_users": total_priv,
            "mfa_enabled": mfa_priv,
            "adoption_ratio": mfa_adoption_ratio,
        },
        "retention": {
            "soft_deleted_patients": soft_deleted,
            "overdue_purge_count": overdue_purge,
            "automated_purge_worker": False,  # P0.1 in backlog
        },
        "user_base": {
            "active": users_active,
            "disabled": users_disabled,
        },
        "controls": CONTROL_CATALOG,
        "framework_coverage": {
            "SOC2": "baseline-mapped",
            "CCPA": "baseline-mapped",
            "ISO27001": "baseline-mapped",
            "HIPAA": "implemented — see HIPAA_COMPLIANCE.md",
        },
        "documents": [
            "/app/memory/COMPLIANCE_BASELINE.md",
            "/app/memory/CONTROL_INVENTORY.md",
            "/app/memory/COMPLIANCE_BACKLOG.md",
            "/app/memory/HIPAA_COMPLIANCE.md",
        ],
    }



@router.get("/security-config")
async def security_config(_admin: dict = Depends(require_role("admin"))):
    """Admin-only internal view of data-protection + secure-config signals.

    This is **informational**. It never returns secret values; only lengths,
    masked prefixes, and boolean feature flags. Source of truth:
      - core/config.py      — env-var required/recommended + weak-secret
        detection
      - core/key_manager.py — encryption-key provider + active version

    Use together with `/api/compliance/overview` and the in-repo docs:
      - /app/memory/DATA_PROTECTION_AND_KEYS.md
      - /app/memory/ACCESS_CONTROL_AND_AUDIT.md
    """
    cfg = config.describe()
    redis_alive = await redis_ping()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": (
            "Informational readiness view — not a certification claim. "
            "Secrets are rendered as lengths / masked prefixes only."
        ),
        "app_env": cfg["app_env"],
        "production_ready": cfg["production_ready"],
        "required_config": cfg["required"],
        "recommended_config": cfg["recommended"],
        "missing_required": cfg["missing_required"],
        "weak_secrets": cfg["weak_secrets"],
        "secret_strength": {
            "jwt_secret_length": cfg["secret_lengths"].get("JWT_SECRET", 0),
            "data_encryption_key_length": cfg["secret_lengths"].get(
                "DATA_ENCRYPTION_KEY", 0
            ),
            "jwt_secret_masked": config.mask_secret(os.environ.get("JWT_SECRET")),
            "data_encryption_key_masked": config.mask_secret(
                os.environ.get("DATA_ENCRYPTION_KEY")
            ),
        },
        "encryption": {
            **key_manager.describe(),
            "patient_encrypted_fields": [
                "date_of_birth",
                "address",
                "emergency_contact",
                "notes",
            ],
            "medical_record_encrypted_fields": [
                "description",
                "diagnosis",
                "treatment",
            ],
        },
        "runtime": {
            "redis_alive": redis_alive,
            "cors_locked_to_frontend": cfg["cors_locked_to_frontend"],
        },
        "features": {
            "mfa_enabled_in_code": True,
            "audit_log_enabled": True,
            "privacy_workflows_enabled": True,
            "phi_masking_enabled": True,
            "legal_hold_enabled": True,
            "retention_worker_running": False,
        },
        "production_gaps": [
            gap
            for gap, present in [
                ("FRONTEND_URL missing — CORS not origin-locked", cfg["recommended"]["FRONTEND_URL"]),
                ("REDIS_URL missing — rate-limit and cache run in local-fallback mode", cfg["recommended"]["REDIS_URL"]),
                ("ADMIN_PASSWORD missing — seed will use the default, unsafe for prod", cfg["recommended"]["ADMIN_PASSWORD"]),
                ("MFA_ISSUER missing — branding in authenticator apps", cfg["recommended"]["MFA_ISSUER"]),
                ("KMS_PROVIDER not set — DATA_ENCRYPTION_KEY is env-loaded, not KMS-wrapped", key_manager.provider() != "env"),
            ]
            if not present
        ],
    }


# ---------- Monitoring hooks catalogue ----------

_SECURITY_EVENTS = [
    # component, event, when, outcome, notes
    ("auth", "auth.login", "successful login", "success",
     "carries session_started_at metadata; base rate for anomaly detection"),
    ("auth", "auth.login", "failed login", "failure",
     "reason ∈ {invalid_credentials, account_disabled, password_expired}"),
    ("auth", "auth.mfa_verified", "MFA completed", "success", ""),
    ("auth", "auth.mfa_verify", "MFA bad code", "failure", "reason=bad_code"),
    ("auth", "auth.reauth", "step-up reauth", "success|failure", ""),
    ("auth", "auth.password_reset_requested", "reset-link request", "success|failure",
     "failure with reason=unknown_email_or_disabled is anti-enumeration signal"),
    ("auth", "auth.password_reset_completed", "reset-link consumed", "success", ""),
    ("auth", "auth.password_changed", "self password change", "success",
     "metadata.other_sessions_revoked=true"),
    ("privileged", "user.created", "admin creates user", "success", ""),
    ("privileged", "user.disabled", "admin disables user", "success",
     "metadata.sessions_revoked=true"),
    ("privileged", "user.enabled", "admin re-enables user", "success", ""),
    ("privileged", "user.updated", "admin role/status patch", "success",
     "metadata.fields lists mutated fields"),
    ("privileged", "user.mfa_reset", "admin MFA recovery", "success", ""),
    ("privileged", "user.mfa_policy_updated", "admin toggles mfa_policy_required", "success", ""),
    ("phi", "patient.unmasked", "admin unmask of patient data", "success",
     "phi_accessed=true; carries reason"),
    ("phi", "patient.exported", "clinical export download", "success",
     "phi_accessed=true"),
    ("phi", "patient.emergency_access", "break-glass access", "success",
     "metadata.emergency_access=true"),
    ("privacy", "privacy_request.created", "DSAR intake", "success", ""),
    ("privacy", "privacy_request.updated", "DSAR status change", "success", ""),
    ("privacy", "privacy_request.fulfilled", "DSAR fulfilment", "success", ""),
    ("privacy", "privacy.consent_recorded", "consent accepted/withdrawn", "success", ""),
    ("audit", "audit_log.viewed", "admin reads audit log", "success", ""),
    ("audit", "audit_log.exported", "admin CSV export", "success",
     "metadata.rows_exported"),
    ("rate_limit", "rate_limit.block", "IP rate-limit triggered", "warning",
     "ALERT if sustained > 5/min from single source"),
    ("system", "system.unhandled_error", "500 response emitted", "failure",
     "carries correlation_id — match to /api/compliance/security-config logs"),
]

_METRIC_CATALOG = [
    ("ccms_auth_failures_total", "Counter", "labels: reason",
     "ALERT on >= 20 failures in 5 min per reason=invalid_credentials"),
    ("ccms_phi_access_total", "Counter", "labels: action",
     "Track weekly baseline; page on >3× baseline spike"),
    ("ccms_privileged_actions_total", "Counter", "labels: action",
     "Audit weekly; any user.disabled or user.updated outside change-window is investigate-worthy"),
    ("ccms_privacy_requests_total", "Counter", "labels: type, status",
     "Backlog signal — rising received minus fulfilled ratio should page the Privacy Officer"),
    ("ccms_breakglass_total", "Counter", "no labels",
     "ALWAYS notify Security Officer — every row deserves review"),
    ("ccms_exports_total", "Counter", "labels: kind (patient|account|audit_csv)",
     "Daily digest; bulk > 5 patient exports/day per admin is anomalous"),
    ("ccms_rate_limit_blocks_total", "Counter", "labels: source (redis|local)",
     "High-signal auth-brute detector"),
    ("ccms_secure_endpoint_errors_total", "Counter", "labels: path_prefix",
     "Page on any sustained >0/min — indicates broken deploy or attack surface"),
    ("ccms_cache_*", "Counter", "hits / misses / sets / invalidations / errors",
     "Operational only; no security threshold"),
    ("ccms_http_request_duration_seconds", "Histogram", "labels: method, path_prefix, status_class",
     "Latency SLO; surge in 5xx class points to stability incident"),
    ("ccms_redis_up", "Gauge", "0 or 1",
     "Redis down degrades rate-limit + cache → degraded but safe"),
]


@router.get("/monitoring-hooks")
async def monitoring_hooks(_admin: dict = Depends(require_role("admin"))):
    """Catalogue of security-relevant events + Prometheus metrics with
    alerting recommendations. Admin-only. Designed to be the artefact an
    auditor asks for first ('what do you log, what do you alert on?')."""
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": (
            "Application emits these signals. Real alerting, correlation, "
            "and on-call paging belong in your SIEM/Observability stack."
        ),
        "structured_logger": "python logging.getLogger('security') — JSON line per event",
        "events": [
            {
                "component": c,
                "event": e,
                "when": w,
                "outcome": o,
                "notes": n,
            }
            for (c, e, w, o, n) in _SECURITY_EVENTS
        ],
        "metrics": [
            {
                "name": n,
                "kind": k,
                "labels": lab,
                "alert_guidance": a,
            }
            for (n, k, lab, a) in _METRIC_CATALOG
        ],
        "incident_evidence_surfaces": [
            "GET /api/audit-logs (filters + date range)",
            "GET /api/audit-logs/export.csv (admin CSV export)",
            "GET /api/auth/sessions (per-user sign-in history)",
            "GET /api/compliance/overview",
            "GET /api/compliance/security-config",
            "GET /api/privacy/requests (DSAR register)",
            "GET /api/metrics (Prometheus text exposition)",
        ],
    }


# ---------- Transport / TLS posture ----------

@router.get("/transport")
async def transport_posture(request: Request, _admin: dict = Depends(require_role("admin"))):
    """Current transport posture: cookie flags, security headers emitted by
    the app, and any detected deployment warnings.

    True TLS version / cipher enforcement lives at the ingress layer — see
    `/app/memory/TLS_AND_TRANSPORT_SECURITY.md`. This endpoint only reports
    what the application can see and control from inside the process."""
    xfp = request.headers.get("x-forwarded-proto")
    effective_scheme = (xfp or request.url.scheme or "").split(",")[0].strip().lower()
    app_env = (os.environ.get("APP_ENV") or "dev").strip().lower()
    trusted_proxy = (os.environ.get("TRUSTED_PROXY_COUNT") or "").strip()
    frontend_url = (os.environ.get("FRONTEND_URL") or "").strip()

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": (
            "The application does not terminate TLS. TLS 1.3 + cipher "
            "policy + certificate lifecycle are ingress / load-balancer / "
            "reverse-proxy responsibilities. This view shows app-layer "
            "signals only."
        ),
        "app_env": app_env,
        "observed_scheme": effective_scheme,
        "scheme_source": "x-forwarded-proto" if xfp else "request.url.scheme",
        "frontend_url": frontend_url or None,
        "frontend_url_is_https": frontend_url.lower().startswith("https://") if frontend_url else None,
        "trusted_proxy_count_configured": bool(trusted_proxy),
        "cookie_flags": {
            "secure": True,
            "httponly": True,
            "samesite": "None",
            "path": "/",
            "note": "Secure=True is hardcoded in services/identity/router.py::_cookie_kwargs; browsers will reject Secure+SameSite=None over plaintext, so ingress must terminate TLS before cookies reach this app.",
        },
        "security_headers_emitted_by_app": [
            "Strict-Transport-Security (production + HTTPS only)",
            "X-Content-Type-Options: nosniff",
            "X-Frame-Options: DENY",
            "Referrer-Policy: strict-origin-when-cross-origin",
            "Permissions-Policy: geolocation=(), microphone=(), camera=(), payment=(), usb=(), accelerometer=(), gyroscope=(), magnetometer=()",
            "Content-Security-Policy (default-src 'self'; frame-ancestors 'none'; upgrade-insecure-requests; …)",
            "Cross-Origin-Opener-Policy: same-origin",
            "Cross-Origin-Resource-Policy: same-site",
        ],
        "hsts": {
            "max_age_seconds_default": DEFAULT_HSTS_MAX_AGE,
            "emitted_only_when": "APP_ENV=production AND effective scheme is https",
        },
        "transport_warnings": config.transport_warnings(),
        "reference": "/app/memory/TLS_AND_TRANSPORT_SECURITY.md",
    }

