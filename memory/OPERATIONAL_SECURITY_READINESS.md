# CCMS Operational Security Readiness

**Last updated:** 2026-02-18 (operational security readiness phase)
**Complements:** `ACCESS_CONTROL_AND_AUDIT.md`, `DATA_PROTECTION_AND_KEYS.md`, `PRIVACY_AND_RETENTION.md`, `COMPLIANCE_BASELINE.md`.

> This document describes how the application **emits** security-relevant
> signals. The actual *detection*, *alerting*, *paging* and *incident
> response* belong in your production observability / SIEM / pager stack.
> Application code does not page on-call.

---

## 1. Structured security-event logging

### 1.1 Where events come from
- `core/security_logger.py` — the single entry point. `event(name, ...)` emits a JSON line on the `security` logger. `suspicious(name, ...)` is a WARNING-level shortcut for anomaly patterns.
- `core/audit.py::log_audit` mirrors every audit DB row to the `security` logger so the real-time channel and the durable channel are perfectly aligned.
- `core/rate_limit.py` emits `rate_limit.block` (WARNING) whenever the per-IP limiter refuses a request.
- `core/error_handlers.py` emits `system.unhandled_error` with a `correlation_id` for every 5xx the app produces.

### 1.2 Log shape

```json
{
  "event":      "auth.login",
  "outcome":    "failure",
  "component":  "auth",
  "ts":         "2026-02-18T09:30:12.123456+00:00",
  "actor_email":"alice@example.com",
  "actor_role": null,
  "entity_type":null,
  "entity_id":  null,
  "reason":     "invalid_credentials",
  "phi_accessed":false,
  "ip":         "203.0.113.10",
  "meta":       {},
  "logger":     "security",
  "level":      "INFO"
}
```

Rules:
- The `event` field is **kebab-case** and shape-stable. Treat it as an API contract; rename with care.
- `outcome` is one of `success | failure | blocked | warning`.
- **Never** add a raw password, TOTP code, token, secret or PHI value to `meta`. The module has a bannable-key scrubber as a last-line guard (`_BANNED_META_KEYS`).
- Meta may contain enums, ids, counts, booleans, trimmed reason strings.

### 1.3 Formatter
- `core/logging_setup.py::configure()` is called at startup. In `APP_ENV=production` all loggers emit JSON; in dev the root logger emits human-readable lines but the `security` logger still emits JSON so SIEM wiring can be the same in both environments.

---

## 2. Event catalogue

See `GET /api/compliance/monitoring-hooks` (admin) for the machine-readable catalogue. Summary below:

| Component | Event | Outcome | Notes |
|---|---|---|---|
| auth | `auth.registered` | success | self-register |
| auth | `auth.login` | success/failure | reason ∈ {invalid_credentials, account_disabled, password_expired} |
| auth | `auth.mfa_challenge_issued` | success | step-1 complete, step-2 needed |
| auth | `auth.mfa_verified` | success | step-2 complete |
| auth | `auth.mfa_verify` | failure | reason=bad_code |
| auth | `auth.reauth` | success/failure | step-up for destructive ops |
| auth | `auth.password_changed` | success | meta.other_sessions_revoked=true |
| auth | `auth.password_reset_requested` | success/failure | failure reason=unknown_email_or_disabled — useful anti-enumeration telemetry |
| auth | `auth.password_reset_completed` | success | meta.sessions_revoked=true |
| privileged | `user.created / disabled / enabled / updated` | success | |
| privileged | `user.mfa_reset / mfa_policy_updated` | success | |
| phi | `patient.viewed / list_viewed / unmasked / exported` | success | phi_accessed=true |
| phi | `patient.emergency_access` | success | meta.emergency_access=true |
| phi | `medical_record.created / accessed` | success | phi_accessed on reads |
| privacy | `privacy_request.created / updated / fulfilled` | success | |
| privacy | `privacy.consent_recorded` | success | policy_type + policy_version |
| privacy | `privacy.comm_preferences_updated` | success | |
| audit | `audit_log.viewed / exported` | success | meta.rows_exported on CSV export |
| rate_limit | `rate_limit.block` | warning | meta.key,source,limit,window_seconds |
| system | `system.unhandled_error` | failure | meta.correlation_id,path,error_type |

---

## 3. Metrics (Prometheus)

Scrape endpoint: `GET /api/metrics` (text exposition, unauthenticated by design for Prometheus scrapers).

| Metric | Kind | Labels | Alert guidance |
|---|---|---|---|
| `ccms_auth_failures_total` | Counter | `reason` | ≥ 20 failures / 5 min at reason=invalid_credentials → alert |
| `ccms_phi_access_total` | Counter | `action` | baseline weekly; ≥ 3× baseline spike → page |
| `ccms_privileged_actions_total` | Counter | `action` | any `user.disabled`/`user.updated` outside change window → review |
| `ccms_privacy_requests_total` | Counter | `type`, `status` | growing received − fulfilled backlog → notify Privacy Officer |
| `ccms_breakglass_total` | Counter | — | every increment reviewed by Security Officer |
| `ccms_exports_total` | Counter | `kind` ∈ {patient, account, audit_csv} | bulk > 5 patient exports/day per admin → investigate |
| `ccms_rate_limit_blocks_total` | Counter | `source` ∈ {redis, local} | high-signal brute-force / bot telemetry |
| `ccms_secure_endpoint_errors_total` | Counter | `path_prefix` | page on any sustained > 0 / min |
| `ccms_cache_*` | Counter | — | operational; not a security alert |
| `ccms_http_request_duration_seconds` | Histogram | `method,path_prefix,status_class` | latency SLO; 5xx surge = stability |
| `ccms_redis_up` | Gauge | — | fallback mode = degraded but safe |

Alerting thresholds are **recommendations**; tune to your SLOs. Commit the Prometheus rule files in your infra repo, not in this one.

---

## 4. Incident-readiness support

### 4.1 Triage surfaces

- **Audit log UI** (`/audit-log`, admin): filters (actor email, entity id, action prefix, outcome, date range), search, CSV export.
- **Audit CSV export** (`GET /api/audit-logs/export.csv`, admin): self-auditing; writes an `audit_log.exported` row with `metadata.rows_exported`.
- **Recent sign-ins** (`GET /api/auth/sessions`, self): per-user sign-in history from the audit log.
- **Readiness dashboards** (admin):
  - `/compliance` — aggregated readiness snapshot
  - `/security-config` — env + encryption + secret-strength signals
  - `/privacy` — DSAR register
  - `GET /api/compliance/monitoring-hooks` — event + metric catalogue (this file, live-generated)
- **Prometheus** (`/api/metrics`) — time-series evidence.

### 4.2 Evidence preservation

1. Upon incident, export the audit log CSV immediately filtered by the suspected window. This writes its own audit row (`audit_log.exported`), so the export itself is captured.
2. Snapshot `/api/compliance/security-config` and `/api/compliance/monitoring-hooks` for a point-in-time view of configuration + telemetry coverage.
3. If the incident involves a specific user, pull `GET /api/auth/sessions` on their behalf (admin can query audit logs with `actor_id` filter).
4. Bundle Prometheus scrape history from the observability stack — the app does not persist history.

### 4.3 Timeline reconstruction

Every security-relevant action emits **both** an audit row **and** a JSON log line with an identical `ts`. A SIEM can reconstruct a timeline by:

```
filter: logger=security AND (actor_id=<id> OR ip=<ip>) AND ts=[from..to]
sort:   ts asc
```

The matching DB query (admin):

```
GET /api/audit-logs?actor_id=<id>&date_from=<iso>&date_to=<iso>
GET /api/audit-logs?actor_email=<email>&date_from=<iso>&date_to=<iso>
```

### 4.4 Correlation ids

- 5xx responses carry `{"correlation_id": "<uuid>"}` — the full traceback is logged server-side under `system.unhandled_error` with the same id. A support ticket only needs the correlation id; the backend holds the details.

---

## 5. Safe error handling

### 5.1 What never leaves the process
- Stack traces — stripped by `core/error_handlers.py` before the response is built.
- DB-internal `_id` fields — projected out of every collection query.
- Secret material — `core/config.py::mask_secret` is the only render path; raw `DATA_ENCRYPTION_KEY` / `JWT_SECRET` are never returned.
- Raw reset / access / refresh tokens — hashed (SHA-256) at rest; cookies are `HttpOnly`; dev-only `dev_token` response field exists in the password-reset flow and must be stripped before production (env-gated follow-up).
- PHI values — `core/masking.py` enforces masked-by-default; unmask is admin-only + reason-gated + audited.

### 5.2 What the client sees on error

```json
{
  "detail":         "Internal server error. Contact support with the correlation id below.",
  "correlation_id": "7f2c5f0a-..."
}
```

Intentional `HTTPException`s are passed through with their original `detail` — routers already own those messages (e.g., "Invalid or expired reset token").

---

## 6. Dependency & package hygiene

### 6.1 Versioning philosophy
- `backend/requirements.txt` pins known-security-critical libraries (`bcrypt`, `pyjwt`, `cryptography`, `pyotp`, `fastapi`, `motor`) to a known-good revision. Transitive pins are produced via `pip freeze` as documented in the system notes.
- `frontend/package.json` pins via yarn lockfile.

### 6.2 Security-sensitive libraries (ownership pointers)

| Library | Purpose | Owner touchpoint |
|---|---|---|
| `bcrypt` | Password hashing (cost 12) | `core/security.py::hash_password` |
| `pyjwt` | Access/refresh token signing (HS256) | `core/security.py::create_access_token/decode_token` |
| `cryptography` | AES-256-GCM for PHI at rest | `core/crypto.py` |
| `pyotp` | TOTP + backup codes | `core/mfa.py` |
| `prometheus-client` | Metrics exposition | `core/metrics.py` |
| `motor` | MongoDB async driver | `core/db.py` |
| `redis` (asyncio) | rate-limit + cache | `core/redis_client.py` |
| `fastapi` | HTTP framework | `server.py` + `services/*/router.py` |

### 6.3 What we do NOT claim
- We do NOT claim a vulnerability scan has been run here. That belongs to CI and is tracked as **P0.6 — Dependency SCA** in `COMPLIANCE_BACKLOG.md`. Recommended tooling: Dependabot / Renovate + `pip-audit` + `yarn npm audit` + Snyk/Trivy at the container layer.

### 6.4 Unsafe defaults — hardening items
- Password reset `dev_token` is included in the response for development convenience — **strip in production** (trivial env check).
- Default admin seed password (`ADMIN_PASSWORD`) is present in dev; production must set this.
- `APP_ENV=dev` by default. Set `APP_ENV=production` in production to flip dev conveniences off and enable JSON root-logger formatting.

---

## 7. What external tooling is still required

| Need | Tooling suggestion |
|---|---|
| Log aggregation + search | ELK / Datadog / Loki / CloudWatch Logs |
| SIEM correlation + alerting | Splunk / Sumo / Datadog Security / Elastic SIEM |
| Metrics scraping + alerting | Prometheus + Alertmanager / Grafana Cloud |
| On-call paging | PagerDuty / Opsgenie |
| Container image scanning | Trivy / Grype / Snyk Container |
| Dependency SCA (prod gate) | Dependabot + pip-audit + Snyk |
| Secrets storage | AWS Secrets Manager / HashiCorp Vault / Azure Key Vault |
| KMS / HSM | AWS KMS / Azure Key Vault / GCP KMS |
| Backup + restore validation | Managed DB backups with quarterly restore drills |

None of the above is instantiated in this codebase. The app emits the right signals; infrastructure is responsible for the rest.

---

## 8. Test checklist (quick verification)

```bash
BASE=$REACT_APP_BACKEND_URL

# structured log events stream
tail -f /var/log/supervisor/backend.out.log | grep '"event":'

# force auth failures → bumps ccms_auth_failures_total{reason="invalid_credentials"}
for i in 1 2 3; do curl -sX POST "$BASE/api/auth/login" -H 'Content-Type: application/json' -d '{"email":"admin@ccms.app","password":"bad"}'; done
curl -s "$BASE/api/metrics" | grep ccms_auth_failures_total

# admin security-config + monitoring-hooks
curl -b admin.txt "$BASE/api/compliance/security-config" | jq '.production_gaps'
curl -b admin.txt "$BASE/api/compliance/monitoring-hooks" | jq '{events: (.events|length), metrics: (.metrics|length)}'

# confirm unhandled errors never leak stack
curl -X GET "$BASE/api/<induce-a-500>"   # response contains only {detail, correlation_id}
```
