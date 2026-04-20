# CCMS — Infrastructure & Platform Security Architecture

**Last updated:** 2026-02-21

This document is the operator's playbook. It documents the production topology, the security controls baked into the code, the runbooks for every platform incident class, and reference IaC snippets.

## 1. Topology at a glance

```
                                        ┌────────────────────────────────────┐
                                        │   WAF / Edge LB (TLS 1.3, HSTS)    │
                                        └──────────────┬─────────────────────┘
                                                       │  HTTPS only, HTTP → 301
                           ┌───────────────────────────▼───────────────────────────┐
                           │            FastAPI gateway (stateless)                │
                           │  • get_tenant_context()  • require_permission()       │
                           │  • TenantScopedRepository • TenantCache • TenantJobs  │
                           │  • TenantStorage         • secrets.redact()           │
                           └──────┬──────────┬──────────┬────────────┬────────────┘
                                  │          │          │            │
                 writes + sensitive reads │  replica-ok reads    cache    object store
                          ▼               ▼                      ▼            ▼
                  ┌─────────────┐  ┌─────────────┐       ┌─────────────┐ ┌─────────────┐
                  │ MongoDB /   │  │ MongoDB     │       │ Redis /     │ │ S3 (prod)   │
                  │ PG PRIMARY  │  │ read replica│       │ Valkey (TLS)│ │ local (dev) │
                  └─────────────┘  └─────────────┘       └─────────────┘ └─────────────┘
                          ▲                                                      ▲
                          │                                                      │
                          │    async tenant-scoped jobs (export, reminders)      │
                          └─────────────────── job runner ───────────────────────┘
```

## 2. Database topology & routing

### 2.1 Classification (in code)

`core/db_routing.py` exposes `ReadPurpose` — the only sanctioned way to pick a DB:

| Purpose              | Routed to | Typical use                          |
|----------------------|-----------|--------------------------------------|
| `WRITES_ONLY`        | primary   | any write                            |
| `READ_AFTER_WRITE`   | primary   | read within ~5s of own write         |
| `REPLICA_OK`         | replica*  | list endpoints, cached reads         |
| `REPLICA_PREFERRED`  | replica*  | analytics / reports / heavy scans    |

\* falls back to primary when the replica is absent, unhealthy, or `READ_REPLICAS_ENABLED=false`.

### 2.2 Primary-only collections

`PRIMARY_ONLY_COLLECTIONS` (in `db_routing.py`) is the allow-list of collections whose reads MUST stay on primary:

- `users`, `user_roles`, `role_permissions`, `permission_scopes`, `elevation_requests`
- `login_attempts`, `password_reset_tokens`
- `audit_logs`
- `tenants`
- `jobs`, `exports`
- `consent_records`, `privacy_requests`

`safe_read(collection, purpose)` refuses any non-WRITES_ONLY purpose for these collections. That means a developer who writes `safe_read("users", ReadPurpose.REPLICA_OK)` sees a 500 in dev (and CI tests catch it), not silent replica drift.

### 2.3 Replica lag guardrails

- `probe_replica_once()` checks the replica every N seconds (wire from a background task in prod; run on-demand from `GET /api/infra/replica`).
- If the replica is down or lag > `REPLICA_MAX_LAG_SECONDS` (default 5) for 3 consecutive probes, the client is circuit-broken for 60 s — all `REPLICA_OK` reads transparently fall back to primary.
- `force_disable_replica(seconds)` is the operator lever for cutover in an incident.

### 2.4 Feature flags

- `READ_REPLICAS_ENABLED` (default `true`): master kill-switch.
- `MONGO_READ_URL`: replica URI; unset in preview, so reads go to primary.
- `REPLICA_MAX_LAG_SECONDS` (default 5).

### 2.5 Runbook — replica incident

1. **Detect**: alerts on `ccms_db_replica_lag_seconds > 10` for 2 min OR `ccms_db_replica_alive == 0`.
2. **Mitigate**: `POST /api/infra/replica/disable?seconds=900` (platform admin), or set `READ_REPLICAS_ENABLED=false` + rolling restart. All reads immediately move to primary; user-visible impact is only higher primary CPU.
3. **Investigate**: check cloud provider replica metrics (replication slot lag, IOPS, network).
4. **Recover**: once `ccms_db_replica_lag_seconds < 5` for 10 min, re-enable.
5. **Postmortem**: if lag was caused by a long-running query, add an explain plan + index; if network, raise with provider.

### 2.6 PostgreSQL migration note

`ReadPurpose` maps 1:1 to `Session(bind=primary)` vs `Session(bind=replica)`. When we promote to Postgres, only the client factory changes — every call site (routers, repositories) is already purpose-classified.

## 3. Redis / cache hardening

### 3.1 Safe API

- **Key builder** `core/tenant_cache.py::key_for(tenant_id, *segs)` — the ONLY way to build a key. `UnsafeCacheKeyError` fires on anything outside the `t:<tid>:…` / `pa:…` namespaces.
- **Categories** (`CacheCategory`) bind use-case to TTL:
  - `SESSION_AUTHZ` — 120 s default; session epoch cache, permission map
  - `REFERENCE` — 300 s default; role catalog, provider list, location list
  - `SCHEDULE_REPORT` — 300 s default; report results, appointment queries
  - `UTILITY` — 60 s default; rate-limit counters, distributed locks
- **TTL bound** — `TenantCache.set(..., ttl_seconds)` refuses ttl <= 0 or > 86400.
- **Invalidation helpers**:
  - `TenantCache.invalidate_tenant(tid)` — wipes every key for a tenant (use on permission-epoch bump, tenant setting change).
  - `TenantCache.invalidate(prefix)` — targeted; prefix must start with `t:` or `pa:`.

### 3.2 MUST-NOT-cache data

- Unmasked PHI values (address, DOB, diagnosis, treatment notes).
- JWTs, refresh tokens, reauth tickets, MFA codes, password-reset tokens.
- Export download tokens.
- Any value whose staleness could affect authorization decisions AFTER the grant has been revoked.

### 3.3 Production Redis config

```hcl
# terraform — example Valkey/ElastiCache module
resource "aws_elasticache_replication_group" "ccms" {
  replication_group_id = "ccms-${var.env}"
  description          = "CCMS cache"
  node_type            = "cache.t4g.medium"
  engine               = "valkey"
  engine_version       = "7.2"
  num_cache_clusters   = 2
  automatic_failover_enabled = true
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true       # TLS in transit
  auth_token                 = var.redis_auth_token
  subnet_group_name          = aws_elasticache_subnet_group.priv.name
  security_group_ids         = [aws_security_group.cache.id]
  snapshot_retention_limit   = 7
  maintenance_window         = "sun:05:00-sun:07:00"
  parameter_group_name       = aws_elasticache_parameter_group.ccms.name
}

resource "aws_cloudwatch_metric_alarm" "cache_cpu" {
  alarm_name          = "ccms-${var.env}-cache-cpu"
  metric_name         = "CPUUtilization"
  namespace           = "AWS/ElastiCache"
  threshold           = 75
  period              = 60
  evaluation_periods  = 5
  comparison_operator = "GreaterThanThreshold"
  alarm_actions       = [aws_sns_topic.oncall.arn]
}
```

### 3.4 Runbook — cache outage

1. **Detect**: `ccms_cache_hit_ratio < 0.5` for 5 min OR connection refused.
2. **Mitigate**: the existing `core/cache.py` **already falls back to in-process** when Redis is down (`redis_alive=False` in logs). No user-visible action is required for short outages.
3. **Investigate**: check Redis memory / eviction / network. If OOM, scale up the node type and rotate the hot tenant's keys.
4. **Recover**: restart the Redis primary or fail over to the replica.
5. **Aftercare**: run `TenantCache.invalidate_tenant(t)` for any tenant whose permissions changed during the outage window (the epoch-bump invalidation would have been dropped).

## 4. Object storage

### 4.1 Safe API (`core/storage.py`)

Every service writes through `TenantStorage` — never the raw backend:

```python
from core.storage import storage, StorageCategory

art = storage().put(ctx.tenant_id, StorageCategory.EXPORTS, csv_bytes, suffix=".csv")
# art.backend_path = "exports/<tenant_id>/<uuid>.csv"

token = storage().sign_download(art, user_id=ctx.user["id"], ttl_seconds=900)
# short-lived JWT; caller must have already verified tenant scope

# On download:
payload = storage().verify_download(token)
assert payload["tid"] == ctx.tenant_id  # route MUST check
assert payload["p"] == art.backend_path
```

Guarantees:

- **Path is always tenant-prefixed** (`<category>/<tenant_id>/<key>`); no shared folder.
- **Keys are UUIDs**, never user-controlled — no PHI in filenames.
- **Path traversal is blocked** at two layers: `sanitise_key()` rejects anything containing `..`, path separators, or control chars; `LocalStorage._resolve()` asserts the resolved path is inside the storage root.
- **Private by default** — there is no `public_read` path. Every read is gated by a signed URL with TTL ≤ 3600 s.
- **Token carries tid + path** so a token issued for tenant A's export cannot be used to fetch tenant B's file even if the attacker knows the path.

### 4.2 Categories

| Category       | Typical retention       | Bucket prefix        |
|----------------|-------------------------|----------------------|
| PERMANENT      | lifetime of the tenant  | `permanent/<tid>/`   |
| EXPORTS        | 24h (configurable)      | `exports/<tid>/`     |
| UPLOAD_STAGING | ≤ 1h (scan then promote)| `staging/<tid>/`     |
| REPORTS        | 30 days                 | `reports/<tid>/`     |

### 4.3 Production S3 config

```hcl
resource "aws_s3_bucket" "ccms_artifacts" {
  bucket = "ccms-${var.env}-artifacts"
}

resource "aws_s3_bucket_public_access_block" "pab" {
  bucket                  = aws_s3_bucket.ccms_artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "sse" {
  bucket = aws_s3_bucket.ccms_artifacts.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.ccms.arn
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "lc" {
  bucket = aws_s3_bucket.ccms_artifacts.id
  rule { id = "exports-24h"; status = "Enabled"; filter { prefix = "exports/" }; expiration { days = 2 } }
  rule { id = "staging-1h";  status = "Enabled"; filter { prefix = "staging/" };  expiration { days = 1 } }
  rule { id = "reports-30d"; status = "Enabled"; filter { prefix = "reports/" };  expiration { days = 30 } }
}
```

### 4.4 Runbook — storage incident

- **Unauthorized fetch attempt**: audit `export.download_denied` spikes ≥ 10/min for a tenant → immediately run `TenantCache.invalidate_tenant()` + revoke any active download tokens for that tenant (force session-epoch bump on the requesting user).
- **Bucket compromised**: rotate KMS key; re-issue signed URLs; restore from cross-region backup; notify compliance within 24h.

## 5. Secrets management

`core/secrets.py` centralises access:

- `get("NAME")` — soft accessor (`None` if absent).
- `require("NAME")` — hard accessor (raises `MissingSecretError`).
- `validate_startup()` — called from `server.py` lifespan hook; app refuses to serve traffic if any of `REQUIRED` is absent (`MONGO_URL`, `DB_NAME`, `JWT_SECRET`, `DATA_ENCRYPTION_KEY`).
- `redact(text)` — masks Mongo URIs, JWTs, Bearer tokens, AWS keys, Stripe keys, `password=` values, and any literal match of the configured `REQUIRED` secrets. Use in every error-handler that echoes user text back.

### 5.1 Provider swap

Today `SECRETS_PROVIDER=env`. In production, set `SECRETS_PROVIDER=aws` and wire `AwsSecretsProvider` to pull from AWS Secrets Manager or Vault:

```python
# core/secrets.py — production body for AwsSecretsProvider.get
import boto3, json, os
_sm = boto3.client("secretsmanager", region_name=os.environ["AWS_REGION"])
resp = _sm.get_secret_value(SecretId=f"ccms/{os.environ['APP_ENV']}/{name}")
return json.loads(resp["SecretString"])[name]
```

### 5.2 Rotation

- Rotate `JWT_SECRET`: stage both secrets (new + old) for a grace window; verify with either; sign with new. Force session-epoch bump at the end.
- Rotate `DATA_ENCRYPTION_KEY`: dual-write pattern (encrypt with new, decrypt with either) for a grace window, then re-encrypt-in-place via a tenant-scoped job.
- Rotate database passwords: IAM password rotation; re-deploy the gateway; rolling connection refresh.

### 5.3 Break-glass (secret compromise)

1. Revoke the compromised credential in the provider console.
2. Force a session-epoch bump tenant-wide (invalidates cached tokens).
3. Rotate `JWT_SECRET`.
4. Review `audit_logs` for `authz.platform_admin_bypass` + every action by the compromised actor.
5. Invalidate every pending export download token (rotating `JWT_SECRET` handles this implicitly).
6. Notify compliance + affected tenants per BAA.

## 6. TLS 1.3 & transport

- Edge (Kubernetes ingress / ALB) terminates TLS 1.3 with an ACME-automated cert.
- Plain HTTP at the edge is 301-redirected to HTTPS.
- `HSTS: max-age=31536000; includeSubDomains; preload` is emitted by the gateway (see iteration 11 security-headers middleware).
- Cookies: `HttpOnly; Secure; SameSite=Lax` for `access_token` / `refresh_token` / `csrf_token`.
- CSRF token is required on every mutating route (identity router enforces).
- CORS is locked to the tenant's own web origin (configured per-tenant env).
- App → DB → Redis → S3 all use encrypted transport:
  - `mongodb+srv://...&tls=true`
  - `rediss://` URL scheme + `auth_token`
  - S3 via AWS SDK (HTTPS by default)

### 6.1 mTLS for internal admin surfaces

`/api/infra/*` is reachable only from the internal cluster subnet and is locked behind a network policy. For partner-facing webhooks, terminate mTLS at the ingress and pass the verified SN/SAN into `X-MTLS-SN` so the app can authorize.

## 7. Backups & DR

### 7.1 Targets

- **DB**: RPO ≤ 5 min, RTO ≤ 30 min (point-in-time restore + hourly snapshot).
- **Object storage**: cross-region replication + 30-day versioning.
- **Redis**: 5-minute snapshots (cache loss is non-fatal; epoch-bump re-hydrates).

### 7.2 Tenant-scoped logical restore (shared-DB caveat)

Because tenants share a DB, a platform-wide PITR is the primary disaster tool. **Tenant-only restore** is a *logical* operation:

1. Stand up an isolated DB from backup (no integration wiring).
2. Run `TenantStorage.put(..., StorageCategory.PERMANENT)` + a Motor export for the tenant's row subset.
3. Replay those documents into the live DB, conflict-resolving by row id.

The same process applies to a single-tenant dedicated cluster once `TENANT_DB_MAP` routes it — *but there the restore is surgical*. This is the #1 reason enterprise tenants should be promoted to dedicated clusters.

Compensating controls until dedicated-cluster migration:

- `audit_logs` are append-only and replicated cross-region (WORM-like).
- Patients + medical_records have `status=deleted` soft-delete with `retention_until` timestamps.
- `exports` themselves are rebuild-able from primary data; lost exports are recreated by re-running `POST /api/exports`.

### 7.3 Runbooks

- **Primary DB incident** — fail over to secondary → point app at new primary via `MONGO_URL` update → rolling restart.
- **Replica lag incident** — see §2.5.
- **Cache outage** — see §3.4.
- **Secret compromise** — see §5.3.
- **Regional outage** — fail traffic via DNS to secondary region; reconnect app to the cross-region replica promoted to primary; S3 CRR covers artifacts.

## 8. Monitoring & tenant-isolation detections

Metrics already exposed at `/api/metrics` (Prometheus text format):

- `ccms_http_requests_total{path,method,status}`
- `ccms_http_request_duration_seconds_bucket{path}`
- `ccms_cache_*`
- `ccms_db_routing_stats`
- `ccms_authz_denied_total{reason}`
- `ccms_security_events_total{action}` — **includes `security.cross_tenant_attempt`** from iteration 15.
- `ccms_jobs_*` — queue depth / status counts.
- `ccms_exports_generated_total` / `ccms_exports_downloaded_total`.

### 8.1 Tenant-isolation alerts (PromQL)

```
# Cross-tenant id probe attempts — expect ~0. Any rate > 0.01/s is suspicious.
alert: CrossTenantAttempts
expr: rate(ccms_security_events_total{action="security.cross_tenant_attempt"}[5m]) > 0.01
for:  5m
labels: {severity: page}

# authz denials spike
alert: AuthzDenialSpike
expr: rate(ccms_authz_denied_total[5m]) > 1
for:  10m
labels: {severity: warn}

# platform-admin bypasses — always audited; alert on unexpected volume.
alert: UnexpectedPlatformAdminActivity
expr: rate(ccms_authz_events_total{action="authz.platform_admin_bypass"}[15m]) > 2
for:  15m
labels: {severity: page}

# replica lag
alert: ReplicaLagHigh
expr: ccms_db_replica_lag_seconds > 10
for:  2m
labels: {severity: warn}

# export denials spike (download token abuse)
alert: ExportDownloadDenials
expr: rate(ccms_exports_download_denied_total[5m]) > 0.05
for:  5m
labels: {severity: warn}
```

### 8.2 Log redaction

Every error-handler passes user-visible strings through `secrets.redact()`. Structured logs include `tenant_id`, `request_id`, `actor_user_id`, `ip` (already populated on `TenantContext`). PHI values are never logged — `core/masking.py` masks at the route boundary before they reach the log stream.

## 9. Environment separation & least privilege

| Environment | DB cluster       | Cache cluster   | S3 bucket prefix          | Secrets scope  |
|-------------|------------------|-----------------|---------------------------|----------------|
| local       | local container  | in-proc fallback| `/app/data/storage`       | `.env` file    |
| preview     | shared preview DB| in-proc fallback| `/app/data/storage`       | preview env    |
| staging     | `ccms-staging`   | `ccms-staging`  | `ccms-staging-artifacts`  | `ccms/staging/…`|
| production  | `ccms-prod`      | `ccms-prod`     | `ccms-prod-artifacts`     | `ccms/prod/…`   |

Guardrails:

- IAM roles are per-environment, per-workload (gateway, jobs runner, CI).
- CI has no long-lived keys; every deploy assumes an OIDC role scoped to the target environment.
- Staging cannot read prod secrets (AWS SCP denies cross-account `secretsmanager:*`).
- Every app container reports its `APP_ENV` at `/api/health` — integration tests refuse to run against `APP_ENV=production`.

## 10. Policy checks (CI)

- `ruff` + `eslint` (syntax).
- `trufflehog` — secret scanning on PR (prevents accidental commits).
- `terraform-compliance` policies:
  - no S3 bucket with `block_public_access != true`
  - no DB without `storage_encrypted = true`
  - no Redis without `transit_encryption_enabled = true`
  - every RDS/DynamoDB has `backup_retention_period >= 7`
  - every CloudWatch alarm bound to an SNS topic
- `checkov` for IAM policy over-permissiveness.
- GitHub Actions requires a security-review approval for changes to `core/security.py`, `core/tenancy.py`, `core/tenant_scope.py`, `core/repository.py`, `services/authz/`, `core/db_routing.py`, `core/storage.py`, `core/secrets.py`.

## 11. Iteration 17 — what this release added

- `core/db_routing.py`: `ReadPurpose`, `safe_read`, `probe_replica_once`, replica circuit-breaker, `force_disable_replica`.
- `core/storage.py`: `StorageBackend` protocol, `LocalStorage`, `TenantStorage` with tenant-prefixed paths, path-traversal guard, UUID key generation, signed-URL primitives.
- `core/secrets.py`: provider abstraction (`env`, stub `aws`), `require`, `validate_startup`, pattern-based + value-based `redact`.
- Startup refuses to serve traffic if any required secret is missing.
- `/api/infra/replica` + `/api/infra/secrets` — platform-admin diagnostics.
- `core/tenant_cache.py`: `CacheCategory` + `DEFAULT_TTL` mapping.
- 15/15 new tests (`test_iteration17_infra_backbone.py`): primary-only guardrail, replica circuit-break, tenant-storage isolation + path traversal + ttl bounds + cross-tenant token claims, secret validation + redaction (6 patterns + live value), cache category TTL bounds.
